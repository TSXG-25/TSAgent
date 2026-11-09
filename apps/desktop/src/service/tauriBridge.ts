import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { TauriSidecarBridge, TauriSidecarProcess } from "./tauriSidecarTransport";
import { clientError } from "./localTransport";

/**
 * Host bridge adapter: inside the Tauri shell, exposes
 * `window.__TSAGENT_SIDECAR_BRIDGE__` which spawns the Python stdio sidecar
 * (`python -m agent.service.local_sidecar`). VITE_TSAGENT_SIDECAR_CWD must be
 * the repo root so the module resolves.
 */
const SIDECAR_CMD = import.meta.env.VITE_TSAGENT_SIDECAR_CMD ?? "python3";
const SIDECAR_ARGS = (import.meta.env.VITE_TSAGENT_SIDECAR_ARGS ?? "-u -m agent.service.local_sidecar").split(" ");
const SIDECAR_CWD = import.meta.env.VITE_TSAGENT_SIDECAR_CWD;

if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
  window.__TSAGENT_SIDECAR_BRIDGE__ = {
    async spawn(): Promise<TauriSidecarProcess> {
      if (!SIDECAR_CWD) {
        throw clientError(
          "INVALID_REQUEST",
          "Tauri sidecar requires VITE_TSAGENT_SIDECAR_CWD set to the repo root " +
          "(so `-m agent.service.local_sidecar` resolves); point " +
          "VITE_TSAGENT_SIDECAR_CMD at the venv python if `python3` lacks deps.",
          false,
        );
      }
      const id = await invoke<number>("sidecar_spawn", {
        program: SIDECAR_CMD,
        args: SIDECAR_ARGS,
        cwd: SIDECAR_CWD,
      });
      const stdoutListeners = new Set<(line: string) => void>();
      const exitListeners = new Set<(error?: unknown) => void>();
      await listen<string>(`sidecar://${id}/stdout`, (event) => {
        for (const listener of stdoutListeners) listener(event.payload);
      });
      await listen(`sidecar://${id}/exit`, (event) => {
        for (const listener of exitListeners) listener(event.payload);
      });
      return {
        writeLine: (line) => invoke("sidecar_write", { id, line }),
        onStdoutLine: (listener) => {
          stdoutListeners.add(listener);
          return () => stdoutListeners.delete(listener);
        },
        onExit: (listener) => {
          exitListeners.add(listener);
          return () => exitListeners.delete(listener);
        },
        close: () => invoke("sidecar_close", { id }),
      };
    },
  };
}

/** Native pickers — Finder dialog inside Tauri, DOM fallback in dev browser. */
export interface PickedFolder { name: string; path: string; files: File[] }

let sidecarPicker: (() => Promise<{ path: string | null }>) | null = null;
export function setSidecarPicker(fn: (() => Promise<{ path: string | null }>) | null): void { sidecarPicker = fn; }

export async function pickFolder(): Promise<PickedFolder | null> {
  if ("__TAURI__" in window || (window as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const path = await open({ directory: true, multiple: false, title: "选择文件夹" }) as string | null;
    return path ? { name: path.split("/").filter(Boolean).pop() ?? path, path, files: [] } : null;
  }
  // Dev browser: real Finder dialog via the local sidecar (osascript).
  if (sidecarPicker) {
    const picked = await sidecarPicker();
    if (picked?.path) return { name: picked.path.split("/").filter(Boolean).pop() ?? picked.path, path: picked.path, files: [] };
    return null;
  }
  // Last-resort: webkitdirectory (name only, no absolute path).
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.setAttribute("webkitdirectory", "");
    input.onchange = () => {
      const files = Array.from(input.files ?? []);
      const rel = (files[0] as { webkitRelativePath?: string } | undefined)?.webkitRelativePath ?? "";
      const name = rel ? rel.split("/")[0] : (files[0]?.name ?? "");
      resolve(files.length ? { name, path: name, files } : null);
    };
    input.click();
  });
}
