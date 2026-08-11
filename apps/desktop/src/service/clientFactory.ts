import type { DesktopAgentServiceClient } from "../types/service";
import { MockAgentServiceClient } from "./mockAgentService";

/** Composition root for the browser MVP. Swap this implementation after C-4. */
export function createAgentServiceClient(): DesktopAgentServiceClient {
  return new MockAgentServiceClient();
}
