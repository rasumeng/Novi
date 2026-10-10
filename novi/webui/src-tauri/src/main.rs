#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod tray;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri::window::Color;
use tauri_plugin_global_shortcut::ShortcutState;

use backend::launcher::{BackendConfig, BackendLauncher};

struct AppState {
    launcher: Arc<BackendLauncher>,
}

// Toggles the main window's visibility. Shared by the tray icon, tray menu,
// and this global shortcut so all three behave identically.
const SHOW_HIDE_SHORTCUT: &str = "CmdOrCtrl+Shift+Space";

fn repo_root() -> PathBuf {
    if let Ok(root) = std::env::var("NOVI_REPO_ROOT") {
        return PathBuf::from(root);
    }

    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from(".."))
}

fn ensure_workspace(candidates: impl IntoIterator<Item = PathBuf>, fallback: PathBuf) -> PathBuf {
    for candidate in candidates {
        if std::fs::create_dir_all(&candidate).is_ok() {
            return candidate;
        }
    }
    fallback
}

fn working_dir(app_handle: &tauri::AppHandle, dev: bool) -> PathBuf {
    if dev {
        return repo_root();
    }

    // The compile-time repository path only exists on the build machine. Use
    // a writable, user-visible workspace for the packaged backend instead.
    let paths = app_handle.path();
    let candidates = [
        paths.document_dir().ok().map(|path| path.join("Novi")),
        paths.app_data_dir().ok().map(|path| path.join("workspace")),
        paths.home_dir().ok().map(|path| path.join("Novi")),
        Some(std::env::temp_dir().join("Novi")),
        paths.resource_dir().ok(),
        std::env::current_dir().ok(),
    ];
    ensure_workspace(candidates.into_iter().flatten(), PathBuf::from("."))
}

fn main() {
    let dev = cfg!(debug_assertions);

    let port = std::env::var("NOVI_BACKEND_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8765);

    let global_shortcut_plugin = tauri_plugin_global_shortcut::Builder::new()
        .with_shortcut(SHOW_HIDE_SHORTCUT)
        .expect("invalid global shortcut definition")
        .with_handler(|app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                if let Some(window) = app.get_webview_window("main") {
                    tray::toggle_visibility(&window);
                }
            }
        })
        .build();

    let app = tauri::Builder::default()
        // Must be the first plugin registered. On a second launch this callback
        // fires in the already-running instance instead of a new process starting;
        // we just bring the existing window forward instead of spawning a second
        // backend on the same port.
        .plugin(tauri_plugin_single_instance::init(|app_handle, _args, _cwd| {
            if let Some(window) = app_handle.get_webview_window("main") {
                tray::focus_and_show(&window);
            }
        }))
        .plugin(global_shortcut_plugin)
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .setup(move |app_handle| {
            let resource_dir = app_handle.path().resource_dir().ok();
            let backend_name = if cfg!(windows) { "novi-backend.exe" } else { "novi-backend" };
            let bundled_backend = resource_dir.map(|path| path.join("resources").join(backend_name));
            let launcher = Arc::new(BackendLauncher::new(BackendConfig {
                working_dir: working_dir(&app_handle.handle(), dev),
                host: "127.0.0.1".into(),
                port,
                // First launch may need to download the default embedding model.
                start_timeout: Duration::from_secs(600),
                bundled_backend,
                development_mode: dev,
            }));
            app_handle.manage(AppState { launcher });
            let state = app_handle.state::<AppState>();

// Load the React app immediately — boot screen is now handled by the web UI
            let initial_url = if dev {
                "http://localhost:5173".to_string()
            } else {
                format!("http://127.0.0.1:{}", port)
            };

            let app_url: url::Url = initial_url.parse().unwrap();
            let window = WebviewWindowBuilder::new(app_handle, "main", WebviewUrl::External(app_url.clone()))
                .title("Novi — AI Agent")
                .inner_size(1280.0, 860.0)
                .min_inner_size(960.0, 640.0)
                // Prevent WebView2's white default from showing between documents.
                .background_color(Color(19, 20, 24, 255))
                .decorations(false)
                // Required to let the frontend use plain HTML5 drag-and-drop
                // (real File objects) instead of Tauri's own drag-drop event,
                // which only hands back file paths.
                .disable_drag_drop_handler()
                .build()?;

            tray::setup(&app_handle.handle().clone())?;

            state.launcher.start()?;

            // In dev, the backend is already running on the port. In prod, the launcher
            // starts the backend. The web UI connects via WebSocket and shows real progress.
            if !dev {
                let launcher = state.launcher.clone();
                let window_for_thread = window.clone();

                std::thread::spawn(move || {
                    if let Err(e) = launcher.wait_until_ready() {
                        eprintln!("[novi-desktop] backend did not become ready: {e}");
                        launcher.stop();
                        // The web UI will handle error display via WebSocket connection failure
                    } else if let Err(e) = window_for_thread.navigate(app_url) {
                        eprintln!("[novi-desktop] failed to load the ready backend: {e}");
                    }
                });
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let state = app_handle.state::<AppState>();
            state.launcher.stop();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::ensure_workspace;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_root() -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "novi-workspace-test-{}-{nonce}",
            std::process::id()
        ))
    }

    #[test]
    fn creates_the_first_available_workspace_candidate() {
        let root = temp_root();
        let documents_workspace = root.join("Documents").join("Novi");
        let app_data_workspace = root.join("AppData").join("workspace");

        let selected = ensure_workspace(
            [documents_workspace.clone(), app_data_workspace],
            root.join("fallback"),
        );

        assert_eq!(selected, documents_workspace);
        assert!(selected.is_dir());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn skips_a_workspace_candidate_that_cannot_be_a_directory() {
        let root = temp_root();
        std::fs::create_dir_all(&root).unwrap();
        let blocker = root.join("file");
        std::fs::write(&blocker, "not a directory").unwrap();
        let app_data_workspace = root.join("AppData").join("workspace");

        let selected = ensure_workspace(
            [blocker.join("Novi"), app_data_workspace.clone()],
            root.join("fallback"),
        );

        assert_eq!(selected, app_data_workspace);
        assert!(selected.is_dir());
        std::fs::remove_dir_all(root).unwrap();
    }
}
