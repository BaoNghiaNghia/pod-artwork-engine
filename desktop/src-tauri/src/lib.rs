use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

struct EngineProcess(Mutex<Option<Child>>);

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
        command
            .arg("serve")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

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
        .manage(EngineProcess(Mutex::new(None)))
        .setup(|app| {
            match spawn_engine() {
                Ok(child) => {
                    let state = app.state::<EngineProcess>();
                    *state.0.lock().expect("engine process lock poisoned") = Some(child);
                }
                Err(error) => {
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
