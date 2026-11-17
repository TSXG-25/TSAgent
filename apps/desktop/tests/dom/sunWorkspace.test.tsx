// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import SessionRail from "../../src/workspaces/sun/SessionRail";
import RunThread from "../../src/workspaces/sun/RunThread";
import ApprovalCards from "../../src/workspaces/sun/ApprovalCards";
import { MockAgentServiceClient } from "../../src/service/mockAgentService";
import { rememberRunAttachments } from "../../src/app/runAttachments";
import type { RunCtx } from "../../src/app/runCtx";
import type { RunView } from "../../src/types";
import type { ApprovalSummary, SessionSummary } from "../../src/types/service";

afterEach(cleanup);

// jsdom has no layout engine — scroll targets are no-ops there.
if (!("scrollTo" in Element.prototype)) {
  Object.assign(Element.prototype, { scrollTo: () => {} });
}

function ctxFor(service: MockAgentServiceClient, sessionId: string, extra: Partial<RunCtx> = {}): RunCtx {
  return {
    service,
    sessionId,
    runs: [],
    selectSession: vi.fn(),
    reportError: vi.fn(),
    controller: { refreshRun: vi.fn().mockResolvedValue(undefined), loadTenantCatalog: vi.fn() },
    ...extra,
  } as Partial<RunCtx> as RunCtx;
}

const session = (sessionId: string, planet?: string): SessionSummary => ({
  tenantId: "t",
  sessionId,
  userId: "u",
  createdAt: "2024-01-01T00:00:00Z",
  runCount: 1,
  latestRunId: "",
  latestStatus: "completed",
  latestUpdatedAt: "2024-01-01T00:00:00Z",
  planet,
});

function railProps(service: MockAgentServiceClient, sessions: SessionSummary[]) {
  return {
    ctx: ctxFor(service, "s-omni"),
    sessions,
    titleOf: (sid: string) => `title-${sid}`,
    wsList: [],
    onNewSession: vi.fn().mockResolvedValue(session("s-new")),
    onSetFolder: vi.fn(),
    onSessionsChanged: vi.fn(),
    openPrompt: vi.fn(),
    openConfirm: vi.fn(),
    onEditWorkspace: vi.fn(),
    onRemoveWorkspace: vi.fn(),
  };
}

describe("SessionRail", () => {
  test("omni view lists unclaimed sessions and hides workstation-owned ones until opted in", async () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-omni" });
    const props = railProps(service, [session("s-omni"), session("s-mars", "mars")]);
    render(<SessionRail {...props} />);

    expect(screen.getByText("title-s-omni")).toBeTruthy();
    expect(screen.queryByText("title-s-mars")).toBeNull();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "all" } });
    await waitFor(() => expect(screen.getByText("title-s-mars")).toBeTruthy());
  });

  test("clicking a session row selects it", () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-omni" });
    const props = railProps(service, [session("s-omni")]);
    render(<SessionRail {...props} />);

    fireEvent.click(screen.getByText("title-s-omni"));
    expect(props.ctx.selectSession).toHaveBeenCalledWith("s-omni");
  });

  test("deleting a session goes through the confirm callback, not directly", () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-omni" });
    const props = railProps(service, [session("s-omni")]);
    const { container } = render(<SessionRail {...props} />);

    fireEvent.click(container.querySelector(".omni-sess-del")!);
    expect(props.openConfirm).toHaveBeenCalledTimes(1);
    const [, onOk] = props.openConfirm.mock.calls[0] as unknown as [string, () => void];
    const deleteSession = vi.spyOn(service, "deleteSession");
    onOk();
    expect(deleteSession).toHaveBeenCalledWith(expect.objectContaining({ sessionId: "s-omni" }));
  });
});

const runView = (over: Partial<RunView>): RunView => ({
  runId: "run-1",
  status: "completed",
  request: "fix the parser",
  activeWorkflowId: "",
  createdAt: "2024-01-01T00:00:00Z",
  updatedAt: "2024-01-01T00:00:00Z",
  duration: "",
  sessionId: "s-omni",
  workflows: [],
  artifacts: [],
  events: [],
  conversation: [],
  ...over,
} as RunView);

describe("RunThread", () => {
  test("renders the run request and attachments the composer sent", () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-omni" });
    rememberRunAttachments("run-1", ["/tmp/spec.py"]);
    const ctx = ctxFor(service, "s-omni", { runs: [runView({})] });
    render(<RunThread approvals={[]} ctx={ctx} curPhase="" viewRun={ctx.runs[0]} wfSteps={[]} />);

    expect(screen.getByText("fix the parser")).toBeTruthy();
    expect(screen.getByText("📎 spec.py")).toBeTruthy();
  });
});

describe("ApprovalCards", () => {
  test("approve decides through the service and clears the card error", async () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-omni" });
    const decideApproval = vi.spyOn(service, "decideApproval");
    const approval: ApprovalSummary = {
      approvalId: "appr-1", runId: "run-1", action: "file.write", scope: "workspace:/out",
      params: { path: "out/x.md" }, riskLevel: "R1", status: "PENDING",
      decidedBy: "", decidedAt: "", createdAt: "2024-01-01T00:00:00Z",
    };
    const ctx = ctxFor(service, "s-omni");
    const { container } = render(<ApprovalCards approvals={[approval]} ctx={ctx} />);

    fireEvent.click(container.querySelector(".btn.primary")!);
    await waitFor(() => expect(decideApproval).toHaveBeenCalledWith(
      expect.objectContaining({ approvalId: "appr-1", decision: "approve" }),
    ));
  });
});
