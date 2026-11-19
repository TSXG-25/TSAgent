// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import MercuryWorkspace from "../../src/workspaces/mercury/MercuryWorkspace";
import { MockAgentServiceClient } from "../../src/service/mockAgentService";
import type { RunCtx } from "../../src/app/runCtx";

afterEach(cleanup);

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
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
  } as Partial<RunCtx> as RunCtx;
}

function mount(ctx: RunCtx) {
  return render(
    <MemoryRouter initialEntries={["/w/mercury"]}>
      <MercuryWorkspace ctx={ctx} />
    </MemoryRouter>,
  );
}

async function runSearch(q: string) {
  const input = document.querySelector(".nav-bar input") as HTMLInputElement;
  fireEvent.change(input, { target: { value: q } });
  fireEvent.submit(input.closest("form")!);
}

describe("Mercury search → panes", () => {
  test("sources project into panes; an empty snippet falls back to content", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "webSearch").mockResolvedValue({
      provider: "deepseek",
      sources: [
        { title: "Alpha Source", url: "https://a.example/x", snippet: "" },
        { title: "Beta Source", url: "https://b.example/y", snippet: "beta snippet" },
      ],
      content: "synthesized answer body",
    } as never);
    mount(ctxFor(service, "s-mer"));
    await runSearch("query one");

    await waitFor(() =>
      expect(screen.getAllByText("Alpha Source").length).toBeGreaterThan(0),
    );
    // snippet "" is falsy — pane 0 body falls back to the pane's content.
    const paneBody = document.querySelector(".nav-view .nav-view-body p");
    expect(paneBody?.textContent).toBe("synthesized answer body");
    expect(document.querySelector(".nav-view a")?.textContent).toBe("https://a.example/x");

    // Only one pane exists by default — add-view maps the next source in.
    fireEvent.click(document.querySelector(".nav-vctl .btn")!);
    await waitFor(() => expect(screen.getByText("beta snippet")).toBeTruthy());
  });

  test("a content-only response still surfaces text instead of a bare empty state", async () => {
    const service = new MockAgentServiceClient();
    vi.spyOn(service, "webSearch").mockResolvedValue({
      provider: "duckduckgo",
      sources: [],
      content: "raw duckduckgo prose result",
    } as never);
    mount(ctxFor(service, "s-mer"));
    await runSearch("query two");

    await waitFor(() =>
      expect(screen.getByText("raw duckduckgo prose result")).toBeTruthy(),
    );
  });

  test("a stale response from an earlier session cannot write the new session's panes", async () => {
    const service = new MockAgentServiceClient();
    let resolveA: ((v: unknown) => void) | null = null;
    const spy = vi.spyOn(service, "webSearch").mockImplementation(
      (req: { query: string }) =>
        new Promise((resolve) => {
          if (req.query === "old-q") resolveA = resolve;
          else resolve({ provider: "p", sources: [{ title: "New Src", url: "https://new", snippet: "s" }], content: "" } as never);
        }) as never,
    );
    const ctx = ctxFor(service, "sess-a");
    const view = mount(ctx);
    await runSearch("old-q");

    // Switch session before A resolves, then run a search in B.
    view.rerender(
      <MemoryRouter initialEntries={["/w/mercury"]}>
        <MercuryWorkspace ctx={ctxFor(service, "sess-b")} />
      </MemoryRouter>,
    );
    await runSearch("new-q");
    await waitFor(() =>
      expect(screen.getAllByText("New Src").length).toBeGreaterThan(0),
    );

    resolveA?.({ provider: "p", sources: [{ title: "Stale Src", url: "https://stale", snippet: "s" }], content: "" });
    await waitFor(() => expect(screen.queryByText("Stale Src")).toBeNull());
    expect(spy).toHaveBeenCalled();
  });
});
