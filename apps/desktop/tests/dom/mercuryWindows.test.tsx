// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import MercuryWorkspace from "../../src/workspaces/mercury/MercuryWorkspace";
import { MercuryWindowBoard } from "../../src/workspaces/mercury/windows";
import { MockAgentServiceClient } from "../../src/service/mockAgentService";
import type { RunCtx } from "../../src/app/runCtx";
import type { WindowSummary } from "../../src/types/service";

afterEach(cleanup);

function win(over: Partial<WindowSummary> = {}): WindowSummary {
  return {
    viewId: "view-1", title: "Alpha", url: "https://a.example/",
    loading: false, error: false, canBack: false, canForward: false,
    controller: "user", runId: "", owner: "workbench", docGeneration: 1,
    ...over,
  };
}

function ctxFor(service: MockAgentServiceClient, sessionId: string): RunCtx {
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
      startRun: vi.fn(),
      selectRun: vi.fn(),
      refreshRun: vi.fn().mockResolvedValue(undefined),
      loadTenantCatalog: vi.fn().mockResolvedValue(undefined),
      pauseRun: vi.fn(),
      cancelRun: vi.fn(),
    },
  } as unknown as RunCtx;
}

function mount(ctx: RunCtx) {
  return render(
    <MemoryRouter initialEntries={["/w/mercury"]}>
      <MercuryWorkspace ctx={ctx} />
    </MemoryRouter>,
  );
}

describe("MercuryWindowBoard", () => {
  test("window ops forward the session scope verbatim", async () => {
    const service = new MockAgentServiceClient();
    const listed = vi.spyOn(service, "listWindows").mockResolvedValue([] as never);
    const board = new MercuryWindowBoard(service);
    board.bind("sess-a");
    await board.refresh();
    expect(listed).toHaveBeenCalledWith({ sessionId: "sess-a" });
  });

  test("boundIds returns a snapshot the send owns", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listWindows")
      .mockResolvedValueOnce([win({ viewId: "v-a" }), win({ viewId: "v-b" })] as never)
      .mockResolvedValueOnce([win({ viewId: "v-a" })] as never);
    const board = new MercuryWindowBoard(service);
    board.bind("s");
    await board.refresh();
    board.activeViewId = "v-b";
    const snapshot = board.boundIds();
    await board.refresh(); // v-b goes away
    // the snapshot the send captured is unaffected by the refresh
    expect(snapshot).toEqual(["v-b"]);
    expect(board.boundIds()).toEqual(["v-a"]);
  });

  test("session switch clears the previous session's windows", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listWindows")
      .mockResolvedValueOnce([win({ viewId: "v-a" })] as never)
      .mockResolvedValueOnce([] as never);
    const board = new MercuryWindowBoard(service);
    board.bind("sess-a");
    await board.refresh();
    expect(board.windows.map((w) => w.summary.viewId)).toEqual(["v-a"]);
    board.bind("sess-b");
    expect(board.windows).toEqual([]);
    expect(board.activeViewId).toBeNull();
    await board.refresh();
    expect(board.windows).toEqual([]);
  });

  test("a stale listWindows response from an earlier session is dropped", async () => {
    const service = new MockAgentServiceClient();
    let resolveFirst!: (w: WindowSummary[]) => void;
    const first = new Promise<WindowSummary[]>((r) => { resolveFirst = r; });
    const spy = vi.spyOn(service, "listWindows")
      .mockReturnValueOnce(first as never)
      .mockResolvedValueOnce([] as never);
    const board = new MercuryWindowBoard(service);
    board.bind("sess-a");
    const pending = board.refresh();
    board.bind("sess-b");
    resolveFirst([win({ viewId: "v-stale" })]);
    await pending;
    await board.refresh();
    expect(board.windows).toEqual([]);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("MercuryWorkspace live panes", () => {
  test("renders one pane per service window with real title/url", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listWindows").mockResolvedValue([
      win({ viewId: "v-a", title: "Alpha", url: "https://a.example/" }),
      win({ viewId: "v-b", title: "Beta", url: "https://b.example/" }),
    ] as never);
    mount(ctxFor(service, "s-1"));
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    expect(screen.getByText("Beta")).toBeTruthy();
    expect(document.querySelectorAll(".nav-view.live").length).toBe(2);
    expect(document.querySelector(".nav-grid.count-2")).toBeTruthy();
  });

  test("no windows and no live support show the honest states", async () => {
    const service = new MockAgentServiceClient();
    // mock client has no windows — listWindows returns [] truthfully
    mount(ctxFor(service, "s-1"));
    await waitFor(() =>
      expect(document.querySelector(".nav-grid.live")).toBeTruthy());
    expect(document.querySelectorAll(".nav-view.live").length).toBe(0);
  });
});

describe("session switch + binding", () => {
  test("switching sessions re-binds and shows the new session's windows", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listWindows")
      .mockResolvedValueOnce([win({ viewId: "v-a", title: "Alpha" })] as never)
      .mockResolvedValueOnce([win({ viewId: "v-b", title: "Beta" })] as never);
    const ctx = ctxFor(service, "s-1");
    const { rerender } = mount(ctx);
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    rerender(
      <MemoryRouter initialEntries={["/w/mercury"]}>
        <MercuryWorkspace ctx={{ ...ctx, sessionId: "s-2" }} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("Beta")).toBeTruthy());
    expect(screen.queryByText("Alpha")).toBeNull();
  });

  test("boundIds returns the active window unless some are selected", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "listWindows").mockResolvedValue([
      win({ viewId: "v-a" }), win({ viewId: "v-b" }), win({ viewId: "v-c" }),
    ] as never);
    const board = new MercuryWindowBoard(service);
    board.bind("s");
    await board.refresh();
    board.activeViewId = "v-b";
    // no explicit selection → the focused window, never the whole set
    expect(board.boundIds()).toEqual(["v-b"]);
    board.toggleSelected("v-a");
    board.toggleSelected("v-c");
    expect(board.boundIds()).toEqual(["v-a", "v-c"]);
    board.toggleSelected("v-a");
    board.toggleSelected("v-c");
    expect(board.boundIds()).toEqual(["v-b"]);
  });
});
