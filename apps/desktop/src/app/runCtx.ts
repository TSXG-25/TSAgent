import type { FormEvent } from "react";
import type { DesktopAgentServiceClient } from "../types/service";
import type { RunView, WorkflowView } from "../types";
import type { RunController } from "../features/runs/runController";

/** Shared run context assembled once in App and handed to the selector and
 *  every workspace. Owns no state itself — App is the source of truth. */
export interface RunCtx {
  /** Current scope's session — for a workspace ctx this is that planet's own
   *  session; for the base ctx it is Sun/Omni's. Composer sends target it. */
  sessionId: string;
  /** Session-bound workspace root — runs scoped to this dir. */
  workspacePath?: string;
  /** True only when the resolved root is a user-bound workspace (auto
   *  roots are app-managed). Lets a workspace embed its own dir controls. */
  workspaceUserBound?: boolean;
  /** Re-resolve the live scope's workspace after a bind/unbind action. */
  onWorkspaceBound?: () => void;
  /** Switch the current scope's session (claims it for this workstation). */
  selectSession: (sessionId: string) => void;
  /** Ensure a planet's scope exists — creates its session on first use. */
  ensurePlanet: (planet: string) => Promise<string>;
  /** Read a planet's current session ("" if not yet created). */
  sessionForPlanet: (planet: string) => string;
  /** Switch a specific planet's session without leaving the current view. */
  selectSessionFor: (planet: string, sessionId: string) => void;
  service: DesktopAgentServiceClient;
  runtimeLabel: string;
  controller: RunController;
  runs: RunView[];
  activeRun: RunView | null;
  workflow: WorkflowView | undefined;
  requestDraft: string;
  setRequestDraft: (value: string) => void;
  createRun: (event?: FormEvent<HTMLFormElement>) => void;
  selectRun: (runId: string) => void;
  /** Loads the run, switches to its owning session, selects it, navigates to
   *  the Sun thread — the single entry point for "go to this run". */
  openRun: (runId: string, sessionId?: string) => void;
  openNewTask: () => void;
  /** Surface a failed service call in the app-wide error banner. */
  reportError: (error: unknown) => void;
  refreshActiveRun: () => void;
  cancelActiveRun: () => void;
  resumeActiveRun: () => void;
  canCancel: boolean;
  canResume: boolean;
  isCancelling: boolean;
  isResuming: boolean;
}
