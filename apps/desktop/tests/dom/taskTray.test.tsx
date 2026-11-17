// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import TaskTray from "../../src/app/TaskTray";
import { MockAgentServiceClient } from "../../src/service/mockAgentService";
import type { RunCtx } from "../../src/app/runCtx";

afterEach(cleanup);

function ctxFor(service: MockAgentServiceClient, sessionId: string): RunCtx {
  return { service, sessionId, openRun: () => {} } as Partial<RunCtx> as RunCtx;
}

test("the tray lists tasks of the session it is currently scoped to", async () => {
  const service = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s1" });
  const listTasks = vi.spyOn(service, "listTasks");
  const { rerender } = render(<TaskTray ctx={ctxFor(service, "s1")} />);
  await waitFor(() => expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({ sessionId: "s1" })));

  rerender(<TaskTray ctx={ctxFor(service, "s2")} />);
  await waitFor(() => expect(listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ sessionId: "s2" })));
});
