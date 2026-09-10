// Static startup/error screens shown in the main window before the Python
// backend is reachable. These are plain files written to the OS temp
// directory and loaded as file:// URLs — no build-time bundling, no JS
// dependency on the app itself, so they render even if the backend never
// comes up.

use std::path::PathBuf;

use tauri::Url;

const STYLE: &str = r#"
:root{color-scheme:dark}html,body{margin:0;height:100%;background:#131418;color:#f3f2f7;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.boot{min-height:100%;box-sizing:border-box;display:grid;place-items:center;padding:32px;background:radial-gradient(circle at 50% 42%,#25223d 0,#17181f 39%,#111216 100%)}.card{width:min(100%,440px);padding:32px;box-sizing:border-box;border:1px solid rgba(255,255,255,.09);border-radius:20px;background:rgba(25,26,33,.84);box-shadow:0 22px 64px rgba(0,0,0,.32);text-align:left}.brand{display:flex;align-items:center;gap:10px;margin-bottom:30px;color:#bcb7ff;font-size:11px;font-weight:700;letter-spacing:.13em}.mark{display:grid;place-items:center;width:24px;height:24px;border-radius:8px;background:#7a6ee0;color:#fff;font-size:16px;letter-spacing:0}.heading{display:flex;align-items:center;gap:13px}.spinner{width:26px;height:26px;box-sizing:border-box;flex:none;border:3px solid rgba(142,130,255,.24);border-top-color:#a99fff;border-radius:50%;animation:spin .85s linear infinite}h1{margin:0;font-size:22px;line-height:1.2;letter-spacing:-.025em}#status{min-height:22px;margin:10px 0 25px;color:#b4b5be;font-size:14px;line-height:1.55}.progress{height:4px;overflow:hidden;border-radius:99px;background:#30313a}.progress:after{content:"";display:block;width:42%;height:100%;border-radius:inherit;background:linear-gradient(90deg,#7368dd,#b3aaff,#7368dd);animation:move 1.65s ease-in-out infinite}.steps{display:grid;gap:13px;margin:25px 0 0;padding:0;list-style:none}.steps li{display:flex;align-items:center;gap:10px;color:#777983;font-size:13px;transition:color .28s ease}.dot{display:block;width:7px;height:7px;border-radius:50%;background:#4a4b54;transition:background .28s ease,box-shadow .28s ease}.steps li.active{color:#dcdaeb}.steps li.active .dot{background:#9c91ff;box-shadow:0 0 0 4px rgba(156,145,255,.13)}.steps li.done{color:#a6a7b0}.steps li.done .dot{background:#7a6ee0}.foot{margin:26px 0 0;color:#777983;font-size:12px;line-height:1.5}.err h1{color:#e98796}.err p{color:#b4b5be;white-space:pre-line;line-height:1.55}.err .card{border-color:rgba(224,88,109,.25)}@keyframes spin{to{transform:rotate(360deg)}}@keyframes move{0%{transform:translateX(-105%)}55%,100%{transform:translateX(250%)}}
"#;

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn write_temp(name: &str, html: &str) -> Option<Url> {
    let path: PathBuf = std::env::temp_dir().join(name);
    std::fs::write(&path, html).ok()?;
    Url::from_file_path(&path).ok()
}

pub fn boot_url() -> Option<Url> {
    let html = format!(
        "<!doctype html><html><head><meta charset='utf-8'><style>{STYLE}</style></head>\
         <body><main class='boot'><section class='card' aria-live='polite'><div class='brand'><span class='mark'>✦</span>NOVI DESKTOP</div><div class='heading'><span class='spinner'></span><h1>Starting Novi</h1></div><p id='status'>Starting Novi…</p><div class='progress'></div><p class='foot'>Everything is running locally on your device.</p></section></main></body></html>"
    );
    write_temp("novi-desktop-boot.html", &html)
}

pub fn error_url(message: &str) -> Option<Url> {
    let escaped = html_escape(message);
    let html = format!(
        "<!doctype html><html><head><meta charset='utf-8'><style>{STYLE}</style></head>\
         <body><main class='boot'><section class='card err'><h1>Novi couldn't start</h1>\
         <p>{escaped}</p>\
         <p class='foot'>Close this window and try again. If it keeps happening, make sure no other \
         Novi instance or process is already using the same port.</p>\
         </section></main></body></html>"
    );
    write_temp("novi-desktop-error.html", &html)
}
