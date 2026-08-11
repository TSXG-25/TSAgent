/// <reference types="vite/client" />

import type { TauriSidecarBridge } from "./service/tauriSidecarTransport";

declare global {
  interface Window {
    /** Injected by the future Tauri host; absent in ordinary browser builds. */
    __TSAGENT_SIDECAR_BRIDGE__?: TauriSidecarBridge;
  }
}
