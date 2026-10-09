use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

struct EngineProcess(Mutex<Option<Child>>);

fn engine_log_path() -> Option<PathBuf> {
    let data_root = std::env::var_os("POD_ARTWORK_DATA")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("LOCALAPPDATA")
                .map(|root| PathBuf::from(root).join("PODArtworkTool"))
        })?;
    Some(data_root.join("logs").join("desktop-engine.log"))
}

fn engine_log_file() -> std::io::Result<File> {
    let path = engine_log_path().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "engine log directory unavailable")
    })?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    if path.metadata().map(|meta| meta.len()).unwrap_or_default() > 5 * 1024 * 1024 {
        let backup = path.with_extension("log.1");
        let _ = fs::remove_file(&backup);
        let _ = fs::rename(&path, &backup);
    }
    OpenOptions::new().append(true).create(true).open(path)
}

fn record_engine_failure(message: &str) {
    if let Ok(mut log) = engine_log_file() {
        let _ = writeln!(log, "Desktop engine startup: {message}");
    }
}

fn spawn_engine() -> std::io::Result<Child> {
    #[cfg(debug_assertions)]
    {
        let mut command = Command::new("python");
        command
            .args(["-m", "pod_artwork_engine", "serve"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }

        return command.spawn();
    }

    #[cfg(not(debug_assertions))]
    {
        let executable = std::env::current_exe()?;
        let engine_path = executable
            .parent()
            .unwrap_or_else(|| std::path::Path::new("."))
            .join("pod-artwork-engine.exe");

        let mut command = Command::new(engine_path);
        command.arg("serve").stdin(Stdio::null()).stdout(Stdio::null());
        let stderr = engine_log_file()
            .map(Stdio::from)
            .unwrap_or_else(|_| Stdio::null());
        command.stderr(stderr);

        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }

        command.spawn()
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineProcess(Mutex::new(None)))
        .setup(|app| {
            match spawn_engine() {
                Ok(child) => {
                    let state = app.state::<EngineProcess>();
                    *state.0.lock().expect("engine process lock poisoned") = Some(child);
                }
                Err(error) => {
                    record_engine_failure(&format!("Failed to start POD engine: {error}"));
                    eprintln!("Failed to start POD engine: {error}");
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building POD Artwork Tool");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            let child = {
                let state = app_handle.state::<EngineProcess>();
                let mut guard = state.0.lock().expect("engine process lock poisoned");
                guard.take()
            };
            if let Some(mut child) = child {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
