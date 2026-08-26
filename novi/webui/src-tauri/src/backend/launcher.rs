use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Clone)]
pub struct BackendConfig {
    pub repo_root: PathBuf,
    pub host: String,
    pub port: u16,
    pub start_timeout: Duration,
}

#[derive(Clone)]
enum BackendCommand {
    Python { python: PathBuf },
    Sidecar { executable: PathBuf },
}

pub struct BackendLauncher {
    config: BackendConfig,
    command: BackendCommand,
    child: Arc<Mutex<Option<Child>>>,
    stopping: Arc<AtomicBool>,
    logs: Arc<Mutex<VecDeque<String>>>,
    log_limit: usize,
}

fn resolve_python(repo_root: &Path) -> PathBuf {
    if let Ok(p) = std::env::var("NOVI_PYTHON") {
        return PathBuf::from(p);
    }
    let venv_names: [&str; 2] = ["venv", ".venv"];
    if cfg!(windows) {
        for name in venv_names {
            let p = repo_root.join(name).join("Scripts").join("python.exe");
            if p.exists() {
                return p;
            }
        }
        PathBuf::from("python")
    } else {
        for name in venv_names {
            let p = repo_root.join(name).join("bin").join("python");
            if p.exists() {
                return p;
            }
        }
        PathBuf::from("python3")
    }
}

impl BackendLauncher {
    pub fn new(config: BackendConfig) -> Self {
        let command = if let Ok(exe) = std::env::var("NOVI_BACKEND_BIN") {
            BackendCommand::Sidecar { executable: PathBuf::from(exe) }
        } else {
            BackendCommand::Python { python: resolve_python(&config.repo_root) }
        };
        Self {
            config,
            command,
            child: Arc::new(Mutex::new(None)),
            stopping: Arc::new(AtomicBool::new(false)),
            logs: Arc::new(Mutex::new(VecDeque::new())),
            log_limit: 200,
        }
    }

    pub fn host(&self) -> &str {
        &self.config.host
    }

    pub fn port(&self) -> u16 {
        self.config.port
    }

    fn command_parts(&self) -> (PathBuf, Vec<String>, PathBuf) {
        match &self.command {
            BackendCommand::Python { python } => (
                python.clone(),
                vec![
                    "-m".into(),
                    "novi".into(),
                    "webui".into(),
                    "--host".into(),
                    self.config.host.clone(),
                    "--port".into(),
                    self.config.port.to_string(),
                ],
                self.config.repo_root.clone(),
            ),
            BackendCommand::Sidecar { executable } => (
                executable.clone(),
                vec![
                    "--host".into(),
                    self.config.host.clone(),
                    "--port".into(),
                    self.config.port.to_string(),
                ],
                self.config.repo_root.clone(),
            ),
        }
    }

    pub fn start(&self) -> Result<(), String> {
        self.stopping.store(false, Ordering::SeqCst);
        let (program, args, cwd) = self.command_parts();

        let mut cmd = Command::new(&program);
        cmd.args(&args)
            .current_dir(&cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("failed to spawn backend '{}': {}", program.display(), e))?;

        if let Some(out) = child.stdout.take() {
            self.spawn_log_reader(out);
        }
        if let Some(err) = child.stderr.take() {
            self.spawn_log_reader(err);
        }

        let pid = child.id();
        *self.child.lock().unwrap() = Some(child);
        println!(
            "[novi-desktop] backend spawned: {} {} (pid {pid})",
            program.display(),
            args.join(" ")
        );
        Ok(())
    }

    fn spawn_log_reader(&self, pipe: impl Read + Send + 'static) {
        let logs = self.logs.clone();
        let limit = self.log_limit;
        std::thread::spawn(move || {
            let reader = BufReader::new(pipe);
            for line in reader.lines() {
                match line {
                    Ok(l) => {
                        eprintln!("[novi-backend] {l}");
                        let mut q = logs.lock().unwrap();
                        if q.len() >= limit {
                            q.pop_front();
                        }
                        q.push_back(l);
                    }
                    Err(_) => break,
                }
            }
        });
    }

    pub fn is_running(&self) -> bool {
        self.child
            .lock()
            .unwrap()
            .as_mut()
            .map_or(false, |c| match c.try_wait() {
                Ok(Some(_)) => false,
                Ok(None) => true,
                Err(_) => false,
            })
    }

    pub fn is_ready(&self) -> bool {
        let addr: SocketAddr = format!("{}:{}", self.config.host, self.config.port)
            .parse()
            .unwrap();
        match TcpStream::connect_timeout(&addr, Duration::from_millis(700)) {
            Ok(mut stream) => {
                let req = format!(
                    "GET /api/config HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n"
                );
                if stream.write_all(req.as_bytes()).is_err() {
                    return false;
                }
                let mut buf = [0u8; 256];
                match stream.read(&mut buf) {
                    Ok(n) => {
                        let head = String::from_utf8_lossy(&buf[..n]);
                        head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
                    }
                    Err(_) => false,
                }
            }
            Err(_) => false,
        }
    }

    pub fn recent_logs(&self) -> Vec<String> {
        self.logs
            .lock()
            .unwrap()
            .iter()
            .rev()
            .take(8)
            .cloned()
            .collect()
    }

    pub fn wait_until_ready(&self) -> Result<(), String> {
        let deadline = Instant::now() + self.config.start_timeout;
        let poll = Duration::from_millis(400);
        let mut last_log = String::new();
        while Instant::now() < deadline {
            if !self.is_running() {
                let logs = self.recent_logs().join(" | ");
                return Err(format!(
                    "backend exited early (last logs: {})",
                    if logs.is_empty() { "<none>" } else { &logs }
                ));
            }
            if self.is_ready() {
                return Ok(());
            }
            if let Some(l) = self.recent_logs().pop() {
                last_log = l;
            }
            std::thread::sleep(poll);
        }
        Err(format!(
            "timed out after {}s on {}:{} (last log: {})",
            self.config.start_timeout.as_secs(),
            self.config.host,
            self.config.port,
            if last_log.is_empty() { "<none>" } else { &last_log }
        ))
    }

    pub fn stop(&self) {
        if self.stopping.swap(true, Ordering::SeqCst) {
            return;
        }
        let child = self.child.lock().unwrap().take();
        let pid = child.as_ref().map(|c| c.id());
        if let Some(_child) = child {
            #[cfg(target_os = "windows")]
            {
                let _ = Command::new("taskkill")
                    .args(["/PID", &pid.unwrap().to_string(), "/T", "/F"])
                    .status();
            }
            #[cfg(not(target_os = "windows"))]
            {
                let mut c = _child;
                let _ = c.kill();
                let _ = c.wait();
            }
            println!("[novi-desktop] backend stopped (pid {})", pid.unwrap());
        }
    }
}