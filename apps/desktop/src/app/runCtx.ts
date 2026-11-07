import type { FormEvent } from "react";
import type { DesktopAgentServiceClient } from "../types/service";
import type { RunView, WorkflowView } from "../types";
import type { RunController } from "../features/runs/runController";

/** Shared run context assembled once in App and handed to the selector and
 *  every workspace. Owns no state itself — App is the source of truth. */
export interface RunCtx {
  /** UI-selected session — composer sends target this scope. */
  sessionId?: string;
  /** Session-bound workspace root — runs scoped to this dir. */
  workspacePath?: string;
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
  openRun: (runId: string) => void;
  openNewTask: () => void;
  refreshActiveRun: () => void;
  cancelActiveRun: () => void;
  resumeActiveRun: () => void;
  canCancel: boolean;
  canResume: boolean;
  isCancelling: boolean;
  isResuming: boolean;
}
