import { useEffect, useRef, type DependencyList } from "react";

/** Run `fn` immediately and every `ms` while mounted — `null` disables the
 *  poll. The poll restarts (and `fn` fires again at once) whenever `ms` or
 *  `deps` change, so e.g. a session switch refetches without waiting for the
 *  next tick. Interval ticks always invoke the latest `fn`, never a stale
 *  closure. */
export function usePoll(fn: () => void, ms: number | null, deps: DependencyList = []): void {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; });
  useEffect(() => {
    if (ms === null) return;
    ref.current();
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `fn` is read via ref; callers list what restarts the poll
  }, [ms, ...deps]);
}
