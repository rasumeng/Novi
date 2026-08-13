#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod splash;
mod tray;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::ShortcutState;

use backend::launcher::{BackendConfig, BackendLauncher};

struct AppState {
    launcher: Arc<BackendLauncher>,
}

// Toggles the main window's visibility. Shared by the tray icon, tray menu,
// and this global shortcut so all three behave identically.
const SHOW_HIDE_SHORTCUT: &str = "CmdOrCtrl+Shift+Space";

fn repo_root() -> PathBuf {
    if let Ok(root) = std::env::var("COZMO_REPO_ROOT") {
        return PathBuf::from(root);
    }

    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from(".."))
}

fn main() {
    let dev = cfg!(debug_assertions);

    let root = repo_root();
    let port = std::env::var("COZMO_BACKEND_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8765);

    let backend = BackendLauncher::new(BackendConfig {
        repo_root: root,
        host: "127.0.0.1".into(),
        port,
        start_timeout: Duration::from_secs(60),
    });

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
        .manage(AppState {
            launcher: Arc::new(backend),
        })
        .setup(move |app_handle| {
            let state = app_handle.state::<AppState>();

            // Show the window immediately with a startup screen instead of leaving
            // the user staring at nothing for up to 60s while the backend boots.
            let loading_url = splash::loading_url()
                .ok_or_else(|| "failed to prepare the startup screen".to_string())?;

            let window = WebviewWindowBuilder::new(app_handle, "main", WebviewUrl::External(loading_url))
                .title("Cozmo — AI Agent")
                .inner_size(1280.0, 860.0)
                .min_inner_size(960.0, 640.0)
                .decorations(false)
                // Required to let the frontend use plain HTML5 drag-and-drop
                // (real File objects) instead of Tauri's own drag-drop event,
                // which only hands back file paths.
                .disable_drag_drop_handler()
                .build()?;

            // Close-to-tray: closing the window hides it instead of quitting.
            // The tray menu's "Quit Cozmo" (or the OS killing the process) is
            // the only way out, same as most tray-resident desktop apps.
            

            tray::setup(&app_handle.handle().clone())?;

            state.launcher.start()?;

            let launcher = state.launcher.clone();
            let window_for_thread = window.clone();

            std::thread::spawn(move || {
                match launcher.wait_until_ready() {
                    Ok(()) => {
                        let url = if dev {
                            "http://localhost:5173".to_string()
                        } else {
                            format!("http://{}:{}", launcher.host(), launcher.port())
                        };
                        match tauri::Url::parse(&url) {
                            Ok(parsed) => {
                                if let Err(e) = window_for_thread.navigate(parsed) {
                                    eprintln!("[cozmo-desktop] failed to load app: {e}");
                                }
                            }
                            Err(e) => {
                                eprintln!("[cozmo-desktop] invalid frontend url '{url}': {e}");
                                if let Some(err_url) =
                                    splash::error_url(&format!("Internal error: invalid app URL.\n\n{e}"))
                                {
                                    let _ = window_for_thread.navigate(err_url);
                                }
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("[cozmo-desktop] backend did not become ready: {e}");
                        launcher.stop();
                        if let Some(err_url) = splash::error_url(&format!(
                            "The Cozmo backend didn't respond in time.\n\n{e}"
                        )) {
                            let _ = window_for_thread.navigate(err_url);
                        }
                    }
                }
            });

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
