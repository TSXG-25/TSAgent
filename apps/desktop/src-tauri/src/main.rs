#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
};
use tauri::{AppHandle, Emitter, State};

/// Registry of live sidecar processes: id → (stdin handle, child).
/// Line-delimited JSON-RPC over stdio; stdout is fanned out to the
/// frontend via per-process events (`sidecar://{id}/stdout`, `/exit`).
struct Procs(Mutex<HashMap<u64, (ChildStdin, Child)>>);
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

#[tauri::command]
fn sidecar_spawn(
    app: AppHandle,
    procs: State<Procs>,
    program: String,
    args: Vec<String>,
    cwd: Option<String>,
) -> Result<u64, String> {
    let mut cmd = Command::new(program);
    cmd.args(&args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "sidecar stdout unavailable".to_string())?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "sidecar stdin unavailable".to_string())?;
    let id = NEXT_ID.fetch_add(1, Ordering::SeqCst);
    let out_event = format!("sidecar://{id}/stdout");
    let exit_event = format!("sidecar://{id}/exit");
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(l) => {
                    let _ = app.emit(&out_event, l);
                }
                Err(_) => break,
            }
        }
        let _ = app.emit(&exit_event, ());
    });
    procs
        .0
        .lock()
        .map_err(|_| "registry lock".to_string())?
        .insert(id, (stdin, child));
    Ok(id)
}

#[tauri::command]
fn sidecar_write(procs: State<Procs>, id: u64, line: String) -> Result<(), String> {
    let mut map = procs.0.lock().map_err(|_| "registry lock".to_string())?;
    let entry = map.get_mut(&id).ok_or("unknown sidecar id")?;
    writeln!(entry.0, "{line}").map_err(|e| e.to_string())?;
    entry.0.flush().map_err(|e| e.to_string())
}

#[tauri::command]
fn sidecar_close(procs: State<Procs>, id: u64) -> Result<(), String> {
    let removed = procs
        .0
        .lock()
        .map_err(|_| "registry lock".to_string())?
        .remove(&id);
    if let Some((_, mut child)) = removed {
        let _ = child.kill();
        let _ = child.wait();
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Procs(Mutex::new(HashMap::new())))
        .invoke_handler(tauri::generate_handler![
            sidecar_spawn,
            sidecar_write,
            sidecar_close
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
