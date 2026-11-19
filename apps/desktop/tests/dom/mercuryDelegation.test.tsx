// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AgentComposer from "../../src/app/AgentComposer";
import { WorkspaceTasks } from "../../src/app/WorkspaceTasks";
import { getWorkspaceRunId, selectWorkspaceRun } from "../../src/app/workspaceSelection";
import { MockAgentServiceClient } from "../../src/service/mockAgentService";
import type { RunCtx } from "../../src/app/runCtx";
import type { RunView } from "../../src/types";
import type { HandoffSummary } from "../../src/types/service";

afterEach(cleanup);

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}

function ctxFor(service: MockAgentServiceClient, sessionId: string, extra: Partial<RunCtx> = {}): RunCtx {
  return {
    service,
    sessionId,
    runs: [],
    activeRun: null,
    selectSession: vi.fn(),
    selectSessionFor: vi.fn(),
    selectRun: vi.fn(),
    openRun: vi.fn(),
    reportError: vi.fn(),
    controller: {
      startRun: vi.fn().mockResolvedValue({ runId: "run-child" }),
      selectRun: vi.fn(),
      refreshRun: vi.fn().mockResolvedValue(undefined),
      loadTenantCatalog: vi.fn().mockResolvedValue(undefined),
      pauseRun: vi.fn(),
      cancelRun: vi.fn(),
    },
    ...extra,
  } as Partial<RunCtx> as RunCtx;
}

const runView = (over: Partial<RunView>): RunView => ({
  runId: "run-1",
  status: "completed",
  request: "research brief",
  activeWorkflowId: "",
  createdAt: "2024-01-01T00:00:00Z",
  updatedAt: "2024-01-01T00:00:00Z",
  duration: "",
  sessionId: "s-mer",
  originPlanet: "mercury",
  workflows: [],
  artifacts: [],
  events: [],
  conversation: [],
  ...over,
} as RunView);

const handoff = (over: Partial<HandoffSummary>): HandoffSummary => ({
  handoffId: "handoff-1",
  sourceRunId: "",
  sourceTaskId: "task-parent",
  targetTaskId: "task-child",
  targetRunId: "run-1",
  targetPlanet: "mars",
  contextRefs: [],
  approvalState: "not_required",
  createdAt: "2024-01-01T00:00:00Z",
  ...over,
});

function renderComposer(ctx: RunCtx) {
  return render(
    <MemoryRouter initialEntries={["/w/mercury"]}>
      <AgentComposer ctx={ctx} planet="mercury" />
      <Routes>
        <Route path="/w/mars" element={<div data-testid="mars-view" />} />
      </Routes>
    </MemoryRouter>,
  );
}

function send(text: string) {
  const area = screen.getByRole("textbox") as HTMLTextAreaElement;
  fireEvent.change(area, { target: { value: text } });
  fireEvent.submit(area.closest("form")!);
}

describe("Mercury @planet delegation", () => {
  test("@mars walks the chain under a fresh mercury parent task", async () => {
    const service = new MockAgentServiceClient();
    const delegate = vi.spyOn(service, "delegateRun");
    const createTask = vi.spyOn(service, "createTask");
    const ctx = ctxFor(service, "s-mer");
    renderComposer(ctx);
    send("@mars implement the fix");

    await waitFor(() => expect(delegate).toHaveBeenCalled());
    expect(createTask).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "s-mer", originPlanet: "mercury" }),
    );
    const arg = delegate.mock.calls[0][0];
    expect(arg.sessionId).toBe("s-mer");
    expect(arg.targetPlanet).toBe("mars");
    expect(arg.requestText).toBe("implement the fix");
    expect(arg.parentRunId ?? "").toBe("");
    expect(arg.parentTaskId).toBeTruthy();
    await waitFor(() =>
      expect(ctx.controller.startRun).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: "s-mer",
          originPlanet: "mars",
          metadata: expect.objectContaining({ delegation_depth: 1 }),
        }),
      ),
    );
    // Target rail selection records the child under this session.
    await waitFor(() =>
      expect(getWorkspaceRunId(service.identity.tenantId, "s-mer", "mars")).toBe("run-child"),
    );
  });

  test("the selected mercury run is the continue anchor", async () => {
    const service = new MockAgentServiceClient();
    const delegate = vi.spyOn(service, "delegateRun");
    const createTask = vi.spyOn(service, "createTask");
    const research = runView({ runId: "run-research", sessionId: "s-mer-anchor" });
    selectWorkspaceRun(service.identity.tenantId, "s-mer-anchor", "mercury", "run-research");
    const ctx = ctxFor(service, "s-mer-anchor", { runs: [research] });
    renderComposer(ctx);
    send("@mars implement what the research says");

    await waitFor(() => expect(delegate).toHaveBeenCalled());
    expect(createTask).not.toHaveBeenCalledWith(
      expect.objectContaining({ originPlanet: "mercury" }),
    );
    expect(delegate).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "s-mer-anchor", parentRunId: "run-research" }),
    );
  });

  test("a same-planet send stays a flat mercury task", async () => {
    const service = new MockAgentServiceClient();
    const delegate = vi.spyOn(service, "delegateRun");
    const ctx = ctxFor(service, "s-mer-flat");
    renderComposer(ctx);
    send("summarize these sources");

    await waitFor(() =>
      expect(ctx.controller.startRun).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: "s-mer-flat", originPlanet: "mercury" }),
      ),
    );
    expect(delegate).not.toHaveBeenCalled();
  });

  test("delegated-out section shows the child and enters Mars on its session", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listHandoffs").mockResolvedValue([handoff({})]);
    const child = runView({
      runId: "run-1",
      sessionId: "s-mer-out",
      originPlanet: "mars",
      output: { text: "implemented and tested", createdAt: "t" } as RunView["output"],
    });
    const ctx = ctxFor(service, "s-mer-out", { runs: [child] });
    render(
      <MemoryRouter initialEntries={["/w/mercury"]}>
        <WorkspaceTasks ctx={ctx} planet="mercury" />
        <Routes>
          <Route path="/w/mars" element={<div data-testid="mars-view" />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("chat-delegated")).toBeTruthy(),
    );
    const card = screen.getByTestId("chat-delegated");
    expect(card.textContent).toContain("implemented and tested");

    fireEvent.click([...card.querySelectorAll(".ven-link")].pop()!);
    expect(ctx.selectSessionFor).toHaveBeenCalledWith("mars", "s-mer-out");
    expect(ctx.selectRun).toHaveBeenCalledWith("run-1");
    expect(getWorkspaceRunId(service.identity.tenantId, "s-mer-out", "mars")).toBe("run-1");
    await waitFor(() => expect(screen.getByTestId("mars-view")).toBeTruthy());
  });
});
