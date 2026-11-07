import { useState, type PointerEvent as ReactPointerEvent } from "react";

export default function EdgeResizer({
  side,
  value,
  onChange,
  min = 200,
  max = 560,
}: {
  /** which panel edge this handle resizes: "left" = panel on the left (handle at its right edge) */
  side: "left" | "right";
  value: number;
  onChange: (width: number) => void;
  min?: number;
  max?: number;
}) {
  const [dragging, setDragging] = useState(false);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = value;
    setDragging(true);
    document.body.style.userSelect = "none";
    const onMove = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      const next = startWidth + (side === "left" ? delta : -delta);
      onChange(Math.min(max, Math.max(min, next)));
    };
    const onUp = () => {
      setDragging(false);
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", onMove);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  };

  return (
    <div
      className={`edge-resizer ${dragging ? "dragging" : ""}`}
      data-side={side}
      onPointerDown={onPointerDown}
      onDoubleClick={() => onChange(side === "left" ? 290 : 368)}
      title="Drag to resize · double-click to reset"
    />
  );
}
