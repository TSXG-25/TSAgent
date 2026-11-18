// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkspaceRail } from "../../src/app/WorkspaceRail";
import { WorkspaceThread } from "../../src/app/WorkspaceThread";
import { MockAgentServiceClient } from "../../src/service/mockAgentService";
import type { RunCtx } from "../../src/app/runCtx";
import type { RunView } from "../../src/types";

afterEach(cleanup);

function ctxFor(service: MockAgentServiceClient, sessionId: string, extra: Partial<RunCtx> = {}): RunCtx {
  return {
    service,
    sessionId,
    runs: [],
    selectSession: vi.fn(),
    reportError: vi.fn(),
    controller: {
      startRun: vi.fn().mockResolvedValue({ runId: "run-new" }),
      selectRun: vi.fn(),
      refreshRun: vi.fn().mockResolvedValue(undefined),
      loadTenantCatalog: vi.fn(),
      pauseRun: vi.fn(),
      cancelRun: vi.fn(),
    },
    ...extra,
  } as Partial<RunCtx> as RunCtx;
}

function typeSend(text: string) {
  const area = screen.getByRole("textbox") as HTMLTextAreaElement;
  fireEvent.change(area, { target: { value: text } });
  fireEvent.submit(area.closest("form")!);
  return area;
}

const runView = (over: Partial<RunView>): RunView => ({
  runId: "run-1",
  status: "completed",
  request: "fix subtract",
  activeWorkflowId: "",
  createdAt: "2024-01-01T00:00:00Z",
  updatedAt: "2024-01-01T00:00:00Z",
  duration: "",
  sessionId: "s-mars-1",
  originPlanet: "mars",
  workflows: [],
  artifacts: [],
  events: [],
  conversation: [],
  ...over,
} as RunView);

const openHome = async () => {
  fireEvent.click(document.querySelector(".rail-home-btn")!);
  await waitFor(() => expect(document.querySelector(".rail-home")).toBeTruthy());
};

describe("WorkspaceRail DOM", () => {
  test("home send files a new record session and lands on its thread", async () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-mars-old" });
    const selectSession = vi.fn();
    const startRun = vi.fn().mockResolvedValue({ runId: "run-new" });
    // The real host re-renders with the adopted session — selectSession
    // drives the sessionId the rail sees, which drives the view.
    function Host() {
      const [sessionId, setSessionId] = useState("s-mars-old");
      const ctx = ctxFor(service, sessionId, {
        selectSession: (id: string) => { selectSession(id); setSessionId(id); },
        controller: {
          startRun, selectRun: vi.fn(),
          refreshRun: vi.fn().mockResolvedValue(undefined),
          loadTenantCatalog: vi.fn(), pauseRun: vi.fn(), cancelRun: vi.fn(),
        } as never,
      });
      return <WorkspaceRail ctx={ctx} planet="mars" />;
    }
    render(<Host />);
    await openHome();

    typeSend("fix the parser");
    await waitFor(() => expect(selectSession).toHaveBeenCalled());

    const adopted = selectSession.mock.calls[0][0] as string;
    expect(adopted).not.toBe("s-mars-old");
    expect(startRun.mock.calls[0][0])
      .toMatchObject({ sessionId: adopted, originPlanet: "mars" });
    // thread view: the record's own surface, not home
    await waitFor(() => expect(document.querySelector(".rail-thread")).toBeTruthy());
    expect(document.querySelector(".rail-home")).toBeNull();
  });

  test("failed send keeps the draft, the current session and home view", async () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-mars-old" });
    const ctx = ctxFor(service, "s-mars-old");
    (ctx.controller.startRun as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("sidecar down"));
    render(<WorkspaceRail ctx={ctx} planet="mars" />);
    await openHome();

    const area = typeSend("解释下代码");
    await waitFor(() => expect(screen.getByText(/提交失败|Submit failed/)).toBeTruthy());

    expect(area.value).toBe("解释下代码");
    expect(ctx.selectSession).not.toHaveBeenCalled();
    expect(document.querySelector(".rail-home")).toBeTruthy();
  });

  test("clicking a recent record selects it and opens the thread explicitly", async () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-mars-1" });
    await service.createSession({ sessionId: "s-mars-1", planet: "mars", requestText: "" } as never);
    await service.createSession({ sessionId: "s-mars-2", planet: "mars", requestText: "" } as never);
    const ctx = ctxFor(service, "s-mars-1");
    render(<WorkspaceRail ctx={ctx} planet="mars" />);
    await openHome();

    fireEvent.click(screen.getByText("s-mars-2"));
    expect(ctx.selectSession).toHaveBeenCalledWith("s-mars-2");
    await waitFor(() => expect(document.querySelector(".rail-thread")).toBeTruthy());
  });

  test("a mars send restores the global run selection and writes only the mars slot", async () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-mars-1" });
    const selectRun = vi.fn();
    const ctx = ctxFor(service, "s-mars-1", {
      activeRun: runView({ runId: "run-omni-current", sessionId: "s-mars-1" }),
      controller: {
        startRun: vi.fn().mockResolvedValue({ runId: "run-mars-task" }),
        selectRun,
        refreshRun: vi.fn().mockResolvedValue(undefined),
        loadTenantCatalog: vi.fn(), pauseRun: vi.fn(), cancelRun: vi.fn(),
      } as never,
    });
    const { getWorkspaceRunId } = await import("../../src/app/workspaceSelection");
    render(<WorkspaceRail ctx={ctx} planet="mars" />);

    typeSend("mars task");
    await waitFor(() => expect(ctx.controller.startRun).toHaveBeenCalled());
    // Omni's selected run is restored after startRun hijacked it.
    await waitFor(() => expect(selectRun).toHaveBeenCalledWith("run-omni-current"));
    // The per-workstation selection slot is mars-keyed only.
    expect(getWorkspaceRunId("t", "s-mars-1", "mars")).toBe("run-mars-task");
    expect(getWorkspaceRunId("t", "s-mars-1", "sun")).toBe("");
    expect(getWorkspaceRunId("t", "s-mars-1", "venus")).toBe("");
  });

  test("late admission resolves cleanly — input clears only after the send lands", async () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-mars-1" });
    const ctx = ctxFor(service, "s-mars-1");
    let resolveRun: (v: { runId: string }) => void = () => {};
    (ctx.controller.startRun as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise((r) => { resolveRun = r; }),
    );
    render(<WorkspaceRail ctx={ctx} planet="mars" />);

    const area = typeSend("slow send");
    // in flight: the draft is untouched and no selection has moved yet
    await waitFor(() => expect(ctx.controller.startRun).toHaveBeenCalled());
    expect(area.value).toBe("slow send");

    resolveRun({ runId: "run-late" });
    await waitFor(() => expect(area.value).toBe(""));
  });
});

describe("WorkspaceThread evidence DOM", () => {
  const evidenceRun = (observations: unknown[], changes: unknown) =>
    runView({
      events: [{
        eventId: "ev-done", type: "run", title: "", description: "", at: "2024-01-01T00:01:00Z",
        payload: { continuation: { observations, workspace_changes: changes } },
      }] as never,
    });

  test("renders attributed file changes and the unified diff", () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-mars-1" });
    const ctx = ctxFor(service, "s-mars-1", {
      runs: [evidenceRun([], {
        git: true,
        added: ["new.py"],
        modified: ["calc.py", "README.md"],
        deleted: [],
        agent_files: ["calc.py", "new.py"],
        ambiguous_files: ["README.md"],
        user_dirty_preserved: ["draft.txt"],
        diff: { "calc.py": "@@ -1 +1 @@\n-return a + b\n+return a - b" },
        diff_truncated: false,
      })],
    });
    render(<WorkspaceThread ctx={ctx} planet="mars" />);

    expect(screen.getByText(/本轮改动|Changes/)).toBeTruthy();
    expect(screen.getAllByText("calc.py").length).toBeGreaterThan(0);
    expect(screen.getByText("new.py")).toBeTruthy();
    expect(screen.getByText(/无法归因|unattributable/)).toBeTruthy();
    expect(screen.getByText(/保留任务前修改|pre-existing edits preserved/)).toBeTruthy();
    expect(document.querySelector(".ev-diff pre")?.textContent).toContain("return a - b");
  });

  test("verification shows failed and passing commands as failed and passing", () => {
    const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s-mars-1" });
    const ctx = ctxFor(service, "s-mars-1", {
      runs: [evidenceRun([
        { tool: "shell", input: { cmd: "python -m pytest -q" }, result: { ok: false, content: "1 failed" } },
        { tool: "shell", input: { cmd: "python -m pytest -q" }, result: { ok: true, content: "3 passed" } },
      ], undefined)],
    });
    render(<WorkspaceThread ctx={ctx} planet="mars" />);

    expect(screen.getByText(/验证记录|Verification/)).toBeTruthy();
    expect(document.querySelectorAll(".ev-tag.fail").length).toBe(1);
    expect(document.querySelectorAll(".ev-tag.ok").length).toBe(1);
    expect(document.querySelector(".ev-out")?.textContent).toContain("1 failed");
  });
});
