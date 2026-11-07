import { useEffect, useRef } from "react";

export default function Cursor() {
  const dotRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const dot = dotRef.current;
    const ring = ringRef.current;
    if (!dot || !ring) return;

    let targetX = window.innerWidth / 2;
    let targetY = window.innerHeight / 2;
    let ringX = targetX;
    let ringY = targetY;
    let raf = 0;

    const onMove = (event: PointerEvent) => {
      targetX = event.clientX;
      targetY = event.clientY;
      dot.style.transform = `translate(${targetX}px, ${targetY}px)`;
    };

    const onOver = (event: PointerEvent) => {
      const target = event.target as Element | null;
      const text = target?.closest?.("input, textarea, [contenteditable]");
      const hot = !text && target?.closest?.("button, a, select, [role='tab'], [role='button']");
      ring.dataset.state = text ? "text" : hot ? "hot" : "";
      dot.dataset.state = text ? "text" : "";
    };

    const loop = () => {
      ringX += (targetX - ringX) * 0.16;
      ringY += (targetY - ringY) * 0.16;
      ring.style.transform = `translate(${ringX}px, ${ringY}px)`;
      raf = requestAnimationFrame(loop);
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerover", onOver, { passive: true });
    raf = requestAnimationFrame(loop);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerover", onOver);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <>
      <div className="cursor-dot" ref={dotRef} aria-hidden="true" />
      <div className="cursor-ring" ref={ringRef} aria-hidden="true">
        <span className="cr-frame"><i /><i /><i /><i /></span>
      </div>
    </>
  );
}
