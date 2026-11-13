import { useLayoutEffect, useRef, useState } from "react";

/** Scroll-follow contract for message threads:
 *  - new content auto-scrolls only while the user is within 80px of the bottom;
 *  - reading upward keeps position and surfaces a "new content" affordance;
 *  - switching `viewKey` (e.g. Run) saves and restores each view's scrollTop. */
export function useScrollFollow(viewKey: string) {
  const ref = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, number>());
  const prevKey = useRef(viewKey);
  const atBottom = useRef(true);
  const [hasNew, setHasNew] = useState(false);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight <= 80;
    if (atBottom.current) setHasNew(false);
  };

  const toBottom = () => {
    const el = ref.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
    setHasNew(false);
    atBottom.current = true;
  };

  // restore the target view's position whenever the key changes
  useLayoutEffect(() => {
    if (prevKey.current === viewKey) return;
    positions.current.set(prevKey.current, ref.current?.scrollTop ?? 0);
    prevKey.current = viewKey;
    const el = ref.current;
    if (!el) return;
    const saved = positions.current.get(viewKey);
    el.scrollTop = saved ?? el.scrollHeight;
    atBottom.current = saved === undefined;
    if (saved === undefined) setHasNew(false);
  }, [viewKey]);

  /** Call whenever rendered content may have grown. */
  const onContentGrow = () => {
    const el = ref.current;
    if (!el) return;
    if (atBottom.current) el.scrollTo({ top: el.scrollHeight });
    else setHasNew(true);
  };

  return { ref, hasNew, onScroll, toBottom, onContentGrow };
}
