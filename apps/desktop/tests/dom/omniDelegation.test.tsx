// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import GlobalChat from "../../src/app/GlobalChat";
import { getWorkspaceRunId } from "../../src/app/workspaceSelection";
import { MockAgentServiceClient } from "../../src/service/mockAgentService";
import type { RunCtx } from "../../src/app/runCtx";
import type { RunView } from "../../src/types";
import type { HandoffSummary } from "../../src/types/service";

afterEach(cleanup);

// jsdom lacks Element.scrollTo — useScrollFollow calls it on content grow.
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
      loadTenantCatalog: vi.fn(),
      pauseRun: vi.fn(),
      cancelRun: vi.fn(),
    },
    ...extra,
  } as Partial<RunCtx> as RunCtx;
}

const runView = (over: Partial<RunView>): RunView => ({
  runId: "run-1",
  status: "completed",
  request: "fix subtract",
  activeWorkflowId: "",
  createdAt: "2024-01-01T00:00:00Z",
  updatedAt: "2024-01-01T00:00:00Z",
  duration: "",
  sessionId: "s-omni",
  originPlanet: "mars",
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

function renderChat(ctx: RunCtx) {
  return render(
    <MemoryRouter initialEntries={["/w/sun"]}>
      <GlobalChat ctx={ctx} embedded instant />
      <Routes>
        <Route path="/w/mars" element={<div data-testid="mars-view" />} />
      </Routes>
    </MemoryRouter>,
  );
}

function send(text: string, planet = "mars") {
  fireEvent.change(document.querySelector(".chat-target")!, {
    target: { value: planet },
  });
  const area = screen.getByRole("textbox") as HTMLTextAreaElement;
  fireEvent.change(area, { target: { value: text } });
  fireEvent.submit(area.closest("form")!);
}

describe("Omni @planet delegation", () => {
  test("send to @mars walks the delegation chain, not a flat run", async () => {
    const service = new MockAgentServiceClient();
    const delegate = vi.spyOn(service, "delegateRun");
    const createTask = vi.spyOn(service, "createTask");
    const ctx = ctxFor(service, "s-omni");
    renderChat(ctx);
    send("fix the bug");

    await waitFor(() => expect(delegate).toHaveBeenCalled());
    // No active run → the Omni record is a fresh parent Task.
    expect(createTask).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "s-omni", originPlanet: "sun" }),
    );
    expect(delegate).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: "s-omni",
        targetPlanet: "mars",
        requestText: "fix the bug",
      }),
    );
    const arg = delegate.mock.calls[0][0];
    expect(arg.parentRunId ?? "").toBe("");
    expect(arg.parentTaskId).toBeTruthy();
    // Child run admitted under the provisioned task in the Omni session.
    await waitFor(() =>
      expect(ctx.controller.startRun).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: "s-omni",
          taskId: expect.any(String),
          originPlanet: "mars",
          metadata: expect.objectContaining({ delegation_depth: 1 }),
        }),
      ),
    );
    // The child run is selected so progress shows in the Omni thread.
    await waitFor(() => expect(ctx.selectRun).toHaveBeenCalledWith("run-child"));
  });

  test("a selected run from another session never anchors the delegation", async () => {
    const service = new MockAgentServiceClient();
    const delegate = vi.spyOn(service, "delegateRun");
    const createTask = vi.spyOn(service, "createTask");
    // The global activeRun still points at a run from session A while the
    // composer sits in session B — the delegation must stay on B.
    const foreign = runView({ runId: "run-a", sessionId: "s-omni-A" });
    const ctx = ctxFor(service, "s-omni-B", { activeRun: foreign });
    renderChat(ctx);
    send("fix the bug");

    await waitFor(() => expect(delegate).toHaveBeenCalled());
    expect(createTask).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "s-omni-B", originPlanet: "sun" }),
    );
    const arg = delegate.mock.calls[0][0];
    expect(arg.sessionId).toBe("s-omni-B");
    expect(arg.parentRunId ?? "").toBe("");
    expect(arg.parentTaskId).toBeTruthy();
    await waitFor(() =>
      expect(ctx.controller.startRun).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: "s-omni-B" }),
      ),
    );
  });

  test("a selected run in this session is the explicit continue-anchor", async () => {
    const service = new MockAgentServiceClient();
    const delegate = vi.spyOn(service, "delegateRun");
    const createTask = vi.spyOn(service, "createTask");
    const mine = runView({ runId: "run-mine", sessionId: "s-omni" });
    const ctx = ctxFor(service, "s-omni", { activeRun: mine });
    renderChat(ctx);
    send("keep going");

    await waitFor(() => expect(delegate).toHaveBeenCalled());
    // No Omni parent task — the only createTask call is the child's own
    // provisioning inside delegateRun.
    expect(createTask).not.toHaveBeenCalledWith(
      expect.objectContaining({ originPlanet: "sun" }),
    );
    expect(delegate).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: "s-omni",
        parentRunId: "run-mine",
      }),
    );
  });

  test("delegated card shows status, summary and enters Mars on the owning session", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listHandoffs").mockResolvedValue([
      handoff({ handoffId: "h-new", targetRunId: "run-new" }),
      handoff({ handoffId: "h-old", targetRunId: "run-old" }),
    ]);
    // ctx.runs is newest-first — without an explicit workspace selection
    // Mars's current task would fall back to run-new regardless of which
    // card was clicked.
    const runNew = runView({ runId: "run-new", sessionId: "s-delegated", request: "newer task" });
    const runOld = runView({
      runId: "run-old",
      sessionId: "s-delegated",
      request: "older task",
      output: { text: "fixed and tested", createdAt: "t" } as RunView["output"],
    });
    const ctx = ctxFor(service, "s-delegated", { runs: [runNew, runOld] });
    renderChat(ctx);

    await waitFor(() =>
      expect(screen.getByTestId("chat-delegated")).toBeTruthy(),
    );
    const cards = document.querySelectorAll(".mc-delegated");
    expect(cards).toHaveLength(2);
    const oldCard = cards[1];
    expect(oldCard.textContent).toContain("fixed and tested");

    // Enter-Mars on the OLDER child: session scope, global selection, and
    // the per-workstation selection record must all point at it — Mars's
    // status/controls read that record, not the global run.
    fireEvent.click([...oldCard.querySelectorAll(".ven-link")].pop()!);
    expect(ctx.selectSessionFor).toHaveBeenCalledWith("mars", "s-delegated");
    expect(ctx.selectRun).toHaveBeenCalledWith("run-old");
    expect(getWorkspaceRunId(service.identity.tenantId, "s-delegated", "mars")).toBe("run-old");
    await waitFor(() => expect(screen.getByTestId("mars-view")).toBeTruthy());
  });

  test("a late handoffs response from the previous session is dropped", async () => {
    const service = new MockAgentServiceClient();
    let resolveA: (v: HandoffSummary[]) => void = () => {};
    const spy = vi.spyOn(service, "listHandoffs")
      .mockImplementationOnce(() => new Promise<HandoffSummary[]>((res) => { resolveA = res; }))
      .mockResolvedValue([]);
    const runA = runView({ runId: "run-1", sessionId: "s-A" });
    const ctxA = ctxFor(service, "s-A", { runs: [runA] });
    const { rerender } = renderChat(ctxA);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    // Switch sessions before A's response lands — B's poll fires at once.
    const ctxB = ctxFor(service, "s-B", { runs: [runA] });
    rerender(
      <MemoryRouter initialEntries={["/w/sun"]}>
        <GlobalChat ctx={ctxB} embedded instant />
        <Routes>
          <Route path="/w/mars" element={<div data-testid="mars-view" />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));

    // A's stale snapshot resolves late — it must not paint on B's surface
    // even though run-1 is still resolvable in the cross-scope catalog.
    resolveA([handoff({})]);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("chat-delegated")).toBeNull();
  });

  test("A→B→A: a stale response for the same session cannot overwrite newer cards", async () => {
    const service = new MockAgentServiceClient();
    let resolveA1: (v: HandoffSummary[]) => void = () => {};
    let resolveA2: (v: HandoffSummary[]) => void = () => {};
    const spy = vi.spyOn(service, "listHandoffs")
      .mockImplementationOnce(() => new Promise<HandoffSummary[]>((res) => { resolveA1 = res; }))
      .mockResolvedValueOnce([])
      .mockImplementationOnce(() => new Promise<HandoffSummary[]>((res) => { resolveA2 = res; }))
      .mockResolvedValue([]);
    const runOld = runView({ runId: "run-old", sessionId: "s-A", request: "stale task" });
    const runNew = runView({ runId: "run-new", sessionId: "s-A", request: "fresh task" });
    const mount = (ctx: RunCtx) => (
      <MemoryRouter initialEntries={["/w/sun"]}>
        <GlobalChat ctx={ctx} embedded instant />
        <Routes>
          <Route path="/w/mars" element={<div data-testid="mars-view" />} />
        </Routes>
      </MemoryRouter>
    );
    const ctxA = ctxFor(service, "s-A", { runs: [runNew, runOld] });
    const { rerender } = render(mount(ctxA));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1)); // A₁ in flight

    // A→B→A: B's poll fires and resolves; A₂ is then the newest request.
    rerender(mount(ctxFor(service, "s-B", { runs: [runNew, runOld] })));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    rerender(mount(ctxA));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(3));

    // Newest first: fresh cards land.
    resolveA2([handoff({ targetRunId: "run-new" })]);
    await waitFor(() => expect(screen.getByText(/fresh task/)).toBeTruthy());

    // Stale A₁ arrives last — it shares the session string but loses the
    // sequence race, so the fresh cards survive.
    resolveA1([handoff({ targetRunId: "run-old" })]);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText(/fresh task/)).toBeTruthy();
    expect(screen.queryByText(/stale task/)).toBeNull();
  });
});
