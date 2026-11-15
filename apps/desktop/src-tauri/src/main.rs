#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc,
        Mutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager, State};

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
    cwd: String,
) -> Result<u64, String> {
    let mut cmd = Command::new(program);
    cmd.args(&args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .current_dir(cwd);
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
    procs
        .0
        .lock()
        .map_err(|_| "registry lock".to_string())?
        .insert(id, (stdin, child));
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
        // EOF means the child died — drop the registry entry and reap it so
        // the process cannot linger as a zombie. sidecar_close may have
        // already removed and waited it; then there is nothing to reap.
        if let Ok(mut map) = app.state::<Procs>().0.lock() {
            if let Some((_, mut child)) = map.remove(&id) {
                let _ = child.wait();
            }
        }
        let _ = app.emit(&exit_event, ());
    });
    Ok(id)
}

#[tauri::command]
fn sidecar_write(procs: State<Procs>, id: u64, line: String) -> Result<(), String> {
    let mut map = procs.0.lock().map_err(|_| "registry lock".to_string())?;
    let entry = map.get_mut(&id).ok_or("unknown sidecar id")?;
    // The line arrives with a trailing newline already; write raw so the
    // sidecar never sees a phantom empty request after each real one.
    if line.ends_with('\n') {
        entry.0.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
    } else {
        writeln!(entry.0, "{line}").map_err(|e| e.to_string())?;
    }
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

/// Managed code-server IDE instance (FE-04B) — the host owns the whole
/// lifecycle: one isolated standalone instance bound to a workspace
/// folder, restarted on rebind, killed on request or app exit. The brew
/// service on :8080 is NOT this instance.
struct IdeProc {
    child: Child,
    port: u16,
    workspace: String,
}

struct Ide(Mutex<Option<IdeProc>>);

#[derive(serde::Serialize)]
struct IdeInfo {
    url: String,
    port: u16,
    workspace: String,
}

fn code_server_program() -> String {
    if let Ok(p) = std::env::var("TSAGENT_CODE_SERVER") {
        return p;
    }
    // Homebrew binaries are not on a packaged .app's launchd PATH.
    for candidate in ["/opt/homebrew/bin/code-server", "/usr/local/bin/code-server"] {
        if std::path::Path::new(candidate).exists() {
            return candidate.to_string();
        }
    }
    "code-server".to_string()
}

/// "HTTP server listening on http://127.0.0.1:<port>/" — the readiness
/// signal the FE-04A probe established for standalone instances.
fn parse_listen_port(line: &str) -> Option<u16> {
    let marker = "HTTP server listening on http://127.0.0.1:";
    let start = line.find(marker)? + marker.len();
    let digits: String = line[start..]
        .chars()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    digits.parse().ok()
}

fn ide_info(proc: &IdeProc) -> IdeInfo {
    IdeInfo {
        url: format!("http://127.0.0.1:{}", proc.port),
        port: proc.port,
        workspace: proc.workspace.clone(),
    }
}

#[tauri::command]
fn ide_start(app: AppHandle, ide: State<Ide>, workspace: String) -> Result<IdeInfo, String> {
    let mut guard = ide.0.lock().map_err(|_| "ide lock".to_string())?;
    if let Some(mut proc) = guard.take() {
        let alive = proc.child.try_wait().map(|s| s.is_none()).unwrap_or(false);
        if alive && proc.workspace == workspace {
            let info = ide_info(&proc);
            *guard = Some(proc);
            return Ok(info);
        }
        let _ = proc.child.kill();
        let _ = proc.child.wait();
    }
    // Isolated user data + extensions — the hosted instance must not share
    // state with the user's brew service or another install.
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("code-server");
    let ext_dir = data_dir.join("extensions");
    std::fs::create_dir_all(&ext_dir).map_err(|e| e.to_string())?;
    // The hosted instance is app-managed, so its bound workspace is
    // always trusted — seed user settings to skip the Restricted Mode
    // prompt instead of letting it degrade the IDE.
    seed_code_server_settings(&data_dir.join("User"))?;
    let mut child = Command::new(code_server_program())
        .args([
            "--bind-addr",
            "127.0.0.1:0",
            "--auth",
            "none",
            "--user-data-dir",
            data_dir.to_str().ok_or("app data dir not utf-8")?,
            "--extensions-dir",
            ext_dir.to_str().ok_or("extensions dir not utf-8")?,
            &workspace,
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| e.to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "ide stdout unavailable".to_string())?;
    let (tx, rx) = mpsc::channel();
    // Drain stdout for the life of the process — the port line is sent
    // once, everything after is discarded but must still be consumed.
    std::thread::spawn(move || {
        let mut sent = false;
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(l) => {
                    if !sent {
                        if let Some(port) = parse_listen_port(&l) {
                            let _ = tx.send(port);
                            sent = true;
                        }
                    }
                }
                Err(_) => break,
            }
        }
    });
    let port = match rx.recv_timeout(Duration::from_secs(15)) {
        Ok(port) => port,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err("code-server did not report a listen port within 15s".to_string());
        }
    };
    let proc = IdeProc {
        child,
        port,
        workspace,
    };
    let info = ide_info(&proc);
    *guard = Some(proc);
    Ok(info)
}

/// Write `security.workspace.trust.enabled=false` into the isolated
/// user-data dir's settings.json, preserving any existing keys — the
/// hosted instance opens its bound workspace without Restricted Mode.
fn seed_code_server_settings(user_dir: &std::path::Path) -> Result<(), String> {
    std::fs::create_dir_all(user_dir).map_err(|e| e.to_string())?;
    let path = user_dir.join("settings.json");
    let mut settings: serde_json::Value = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| serde_json::json!({}));
    let obj = settings.as_object_mut().ok_or("settings.json not an object")?;
    obj.insert(
        "security.workspace.trust.enabled".to_string(),
        serde_json::json!(false),
    );
    std::fs::write(&path, settings.to_string()).map_err(|e| e.to_string())
}

#[tauri::command]
fn ide_status(ide: State<Ide>) -> Result<Option<IdeInfo>, String> {
    let mut guard = ide.0.lock().map_err(|_| "ide lock".to_string())?;
    if let Some(mut proc) = guard.take() {
        let alive = proc.child.try_wait().map(|s| s.is_none()).unwrap_or(false);
        if alive {
            let info = ide_info(&proc);
            *guard = Some(proc);
            return Ok(Some(info));
        }
        let _ = proc.child.wait();
    }
    Ok(None)
}

#[tauri::command]
fn ide_stop(ide: State<Ide>) -> Result<(), String> {
    let mut guard = ide.0.lock().map_err(|_| "ide lock".to_string())?;
    if let Some(mut proc) = guard.take() {
        let _ = proc.child.kill();
        let _ = proc.child.wait();
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Procs(Mutex::new(HashMap::new())))
        .manage(Ide(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            sidecar_spawn,
            sidecar_write,
            sidecar_close,
            ide_start,
            ide_status,
            ide_stop
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                // Kill and reap every registered sidecar so app exit never
                // orphans a python process.
                if let Ok(mut map) = app.state::<Procs>().0.lock() {
                    for (_, (_, mut child)) in map.drain() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
                // Same ownership for the hosted IDE instance.
                if let Ok(mut guard) = app.state::<Ide>().0.lock() {
                    if let Some(mut proc) = guard.take() {
                        let _ = proc.child.kill();
                        let _ = proc.child.wait();
                    }
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::{parse_listen_port, seed_code_server_settings};

    #[test]
    fn parses_the_standalone_listen_line() {
        // The FE-04A probe's real stdout shape.
        assert_eq!(
            parse_listen_port(
                "[2026-11-15T11:54:21.484Z] info  HTTP server listening on http://127.0.0.1:58840/"
            ),
            Some(58840)
        );
        assert_eq!(
            parse_listen_port("info    - Authentication is disabled"),
            None
        );
        // Other bind forms must not leak a bogus port.
        assert_eq!(
            parse_listen_port("info  HTTP server listening on http://0.0.0.0:8080/"),
            None
        );
    }

    #[test]
    fn seeds_workspace_trust_and_preserves_existing_settings() {
        let dir = std::env::temp_dir().join(format!("tsagent-ide-test-{}", std::process::id()));
        let user_dir = dir.join("User");
        std::fs::create_dir_all(&user_dir).unwrap();
        std::fs::write(
            user_dir.join("settings.json"),
            r#"{"workbench.colorTheme": "Default Dark+"}"#,
        )
        .unwrap();
        seed_code_server_settings(&user_dir).unwrap();
        let parsed: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(user_dir.join("settings.json")).unwrap())
                .unwrap();
        assert_eq!(parsed["security.workspace.trust.enabled"], false);
        assert_eq!(parsed["workbench.colorTheme"], "Default Dark+");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
