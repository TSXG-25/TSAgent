import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { t } from "../../app/i18n";
import { usePoll } from "../../app/usePoll";
import type { RunCtx } from "../../app/runCtx";
import type { ParticipantSummary, SessionSummary } from "../../types/service";
import type { RunView } from "../../types";
import { fetchSessionSnapshot, RefreshGate } from "./sessionSnapshot";

/** Sun/Omni session data: the tenant-wide session list, its participants,
 *  and the run the main column is viewing. Owns the 8s list poll, the
 *  per-session run hydration, and the blocked-run view poll — the rail and
 *  thread only render what this returns. */
export function useSunSessions(ctx: RunCtx) {
  const { service, sessionId, runs, selectSession } = ctx;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [participants, setParticipants] = useState<ParticipantSummary[]>([]);
  const pickedSessionRef = useRef(false);

  const sessionIdRef = useRef(sessionId);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);
  const runsRef = useRef(runs);
  useEffect(() => { runsRef.current = runs; }, [runs]);
  const refreshGate = useRef(new RefreshGate());
  // StrictMode 会 setup→cleanup→setup：gate 在每次 setup 新建，cleanup 只
  // 关闭当次实例 — 第二次 setup 不复用已永久关闭的 gate。须先于下方
  // refresh 轮询声明，保证首个 refresh 拿到当前实例。
  useEffect(() => {
    const gate = new RefreshGate();
    refreshGate.current = gate;
    return () => gate.close();
  }, []);

  const refresh = useCallback(async () => {
    const sid = sessionId;
    const gate = refreshGate.current;
    const seq = gate.begin();
    try {
      const snap = await fetchSessionSnapshot(service, sid);
      // 会话已切换、有更新的刷新或实例已被 cleanup 替换 — 统一提交。
      if (refreshGate.current !== gate || !gate.isCurrent(seq) || sessionIdRef.current !== snap.sessionId) return;
      setSessions(snap.sessions);
      if (!pickedSessionRef.current && snap.sessions.length > 0 && !snap.sessions.some((x) => x.sessionId === sid)) {
        const latest = snap.sessions.slice().sort((a, b) => (b.latestUpdatedAt ?? "").localeCompare(a.latestUpdatedAt ?? ""))[0];
        selectSession(latest.sessionId);
      }
      pickedSessionRef.current = true; setParticipants(snap.participants);
      snap.sessions.slice(0, 20).forEach((x) => {
        if (x.latestRunId && !runsRef.current.find((r) => r.runId === x.latestRunId)) {
          void ctx.controller.refreshRun(x.latestRunId, x.sessionId).catch(() => {});
        }
      });
    } catch { /* non-fatal */ }
  }, [service, sessionId]);
  usePoll(() => void refresh(), 8000, [refresh]);
  // hydrate ALL runs in the selected session — the thread renders every
  // run's messages, so each one needs its conversation/events loaded.
  useEffect(() => {
    let dead = false;
    void (async () => {
      try {
        const snaps = await service.listRuns({ sessionId });
        if (dead) return;
        for (const snap of snaps.filter((x) => x.sessionId === sessionId)) {
          if (!runs.find((r) => r.runId === snap.runId)) {
            void ctx.controller.refreshRun(snap.runId, sessionId).catch(() => {});
          }
        }
      } catch { /* offline */ }
    })();
    return () => { dead = true; };
  }, [sessionId]);

  const newSession = useCallback(async () => {
    const s = await service.createSession({
      sessionId: `session-${Date.now().toString(36)}`, });
    await refresh(); selectSession(s.sessionId);
    return s;
  }, [service, refresh, selectSession]);

  // session → latest run request as display title (real data)
  const titleOf = useCallback((sid: string, latestRunId?: string) => {
    const rs = runs.filter((x) => x.sessionId === sid);
    const first = rs.slice().sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""))[0];
    const r = first ?? rs.find((x) => x.runId === latestRunId);
    return r?.request?.slice(0, 28) ?? t("sun.session-fallback", { id: sid.slice(-6) });
  }, [runs]);

  const viewRun = useMemo((): RunView | undefined => {
    const s = sessions.find((x) => x.sessionId === sessionId);
    if (ctx.activeRun?.sessionId === sessionId) return ctx.activeRun;
    return runs.find((r) => r.runId === s?.latestRunId) ?? runs.filter((r) => r.sessionId === sessionId).slice(-1)[0];
  }, [sessionId, sessions, runs, ctx.activeRun]);
  // Active/pending runs are long-polled by the controller. A run parked on
  // an approval is not, so the viewed one refreshes here until it moves on
  // (refresh errors surface through the controller's error banner).
  usePoll(
    () => void ctx.controller.refreshRun(viewRun!.runId, sessionId).catch(() => {}),
    viewRun?.status === "blocked" ? 3000 : null,
    [viewRun?.runId, viewRun?.status, sessionId, ctx.controller],
  );

  return { sessions, participants, refresh, newSession, titleOf, viewRun };
}
