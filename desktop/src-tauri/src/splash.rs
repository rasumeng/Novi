// Static startup/error screens shown in the main window before the Python
// backend is reachable. These are plain files written to the OS temp
// directory and loaded as file:// URLs — no build-time bundling, no JS
// dependency on the app itself, so they render even if the backend never
// comes up.

use std::path::PathBuf;

use tauri::Url;

const STYLE: &str = r#"
html,body{margin:0;height:100%;background:#131418;color:#ececf0;
  font-family:Inter,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;}
.wrap{display:flex;flex-direction:column;align-items:center;gap:14px;max-width:440px;text-align:center;padding:24px;}
.spinner{width:30px;height:30px;border-radius:50%;border:3px solid rgba(122,110,224,0.25);
  border-top-color:#7A6EE0;animation:spin 0.9s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
h1{font-size:15px;font-weight:600;margin:0;}
p{font-size:13px;color:#8f919b;margin:0;line-height:1.55;white-space:pre-line;}
.err h1{color:#B04A5A;}
"#;

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn write_temp(name: &str, html: &str) -> Option<Url> {
    let path: PathBuf = std::env::temp_dir().join(name);
    std::fs::write(&path, html).ok()?;
    Url::from_file_path(&path).ok()
}

pub fn loading_url() -> Option<Url> {
    let html = format!(
        "<!doctype html><html><head><meta charset='utf-8'><style>{STYLE}</style></head>\
         <body><div class='wrap'><div class='spinner'></div>\
         <h1>Starting Cozmo…</h1>\
         <p>Warming up the local brain. This can take a little longer on first launch.</p>\
         </div></body></html>"
    );
    write_temp("cozmo-desktop-loading.html", &html)
}

pub fn error_url(message: &str) -> Option<Url> {
    let escaped = html_escape(message);
    let html = format!(
        "<!doctype html><html><head><meta charset='utf-8'><style>{STYLE}</style></head>\
         <body><div class='wrap err'><h1>Cozmo couldn't start</h1>\
         <p>{escaped}</p>\
         <p>Close this window and try again. If it keeps happening, make sure no other \
         Cozmo instance or process is already using the same port.</p>\
         </div></body></html>"
    );
    write_temp("cozmo-desktop-error.html", &html)
}
