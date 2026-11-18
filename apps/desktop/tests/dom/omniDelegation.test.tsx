// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
    selectRun: vi.fn(),
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
    <MemoryRouter>
      <GlobalChat ctx={ctx} embedded instant />
    </MemoryRouter>,
  );
}

describe("Omni @planet delegation", () => {
  test("send to @mars walks the delegation chain, not a flat run", async () => {
    const service = new MockAgentServiceClient();
    const delegate = vi.spyOn(service, "delegateRun");
    const createTask = vi.spyOn(service, "createTask");
    const ctx = ctxFor(service, "s-omni");
    renderChat(ctx);

    fireEvent.change(document.querySelector(".chat-target")!, {
      target: { value: "mars" },
    });
    const area = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(area, { target: { value: "fix the bug" } });
    fireEvent.submit(area.closest("form")!);

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

  test("delegated card shows status, summary and the Mars link", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listHandoffs").mockResolvedValue([handoff({})]);
    const child = runView({
      runId: "run-1",
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
    expect(card.querySelector(".ven-link")!).toBeTruthy();
    expect(card.textContent).toMatch(/进入|Enter/);
    fireEvent.click(card.querySelector(".ven-link")!);
    expect(ctx.selectRun).toHaveBeenCalledWith("run-1");
  });
});
