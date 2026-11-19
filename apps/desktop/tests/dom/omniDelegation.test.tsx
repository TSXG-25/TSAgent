// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import GlobalChat from "../../src/app/GlobalChat";
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
    vi.spyOn(service, "listHandoffs").mockResolvedValue([handoff({})]);
    const child = runView({
      runId: "run-1",
      sessionId: "s-omni",
      originPlanet: "mars",
      output: { text: "fixed and tested", createdAt: "t" } as RunView["output"],
    });
    const ctx = ctxFor(service, "s-omni", { runs: [child] });
    renderChat(ctx);

    await waitFor(() =>
      expect(screen.getByTestId("chat-delegated")).toBeTruthy(),
    );
    const card = screen.getByTestId("chat-delegated");
    expect(card.querySelector(".mc-delegated-head > span")!.textContent).toBe("@mars");
    expect(card.textContent).toContain("fixed and tested");

    // Enter-Mars locates the owning session, the run, and lands on Mars —
    // Mars's rail filter + bound project follow the session scope.
    fireEvent.click([...card.querySelectorAll(".ven-link")].pop()!);
    expect(ctx.selectSessionFor).toHaveBeenCalledWith("mars", "s-omni");
    expect(ctx.selectRun).toHaveBeenCalledWith("run-1");
    await waitFor(() => expect(screen.getByTestId("mars-view")).toBeTruthy());

    // The request link still selects the run inline (Omni view).
    fireEvent.click(card.querySelector(".ven-link")!);
    expect(ctx.selectRun).toHaveBeenCalledWith("run-1");
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
});
