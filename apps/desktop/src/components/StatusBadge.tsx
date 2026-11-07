import type { RunStatus } from "../types";

export const statusLabels: Record<RunStatus, string> = {
  pending: "PENDING",
  active: "ACTIVE",
  cancelling: "CANCELLING",
  cancelled: "CANCELLED",
  timed_out: "TIMED OUT",
  completed: "COMPLETED",
  failed: "FAILED",
  blocked: "BLOCKED",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`status-badge ${status}`}>{statusLabels[status]}</span>;
}
