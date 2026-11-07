import { useEffect, useRef, useState, type ReactNode } from "react";
import { delegationJump, lastJump } from "../app/scenes";
import type { SceneDef } from "../app/scenes";
import type { RunCtx } from "../app/runCtx";
import "./tokens.css";

/** Shared workspace shell: mecha header (cut corners, accent breathing
 *  background) + per-scene main canvas. §7 of plan — every workspace uses it,
 *  layouts inside differ per planet. When ctx is supplied, a composer bar
 *  lets the operator dispatch a task bound to this planet's scene. */
export function WorkspaceFrame({ scene, ctx, children }: { scene: SceneDef; ctx?: RunCtx; children: ReactNode }) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [delegateHit, setDelegateHit] = useState<string | null>(() =>
    delegationJump && delegationJump.scene === scene.id && Date.now() - delegationJump.at < 3000
      ? delegationJump.accent : null);
  useEffect(() => {
    if (!delegateHit) return;
    const t = setTimeout(() => setDelegateHit(null), 900);
    return () => clearTimeout(t);
  }, [delegateHit]);
  const [reveal, setReveal] = useState<{ x: number; y: number; accent: string } | null>(() =>
    lastJump && Date.now() - lastJump.at < 3000
      ? { x: lastJump.x, y: lastJump.y, accent: lastJump.accent }
      : null);
  useEffect(() => {
    if (!reveal) return;
    const t = setTimeout(() => setReveal(null), 620);
    return () => clearTimeout(t);
  }, [reveal]);
  return (
    <div className={`ws-root ${delegateHit ? "delegate-arrive" : ""}`} data-planet={scene.id} style={{ "--planet-accent": scene.accent, "--delegate-accent": delegateHit ?? scene.accent } as React.CSSProperties}>
      <div className="ws-canvas" ref={canvasRef}>{children}</div>
      {reveal && <div className="ws-reveal" style={{ "--planet-accent": reveal.accent, "--rx": `${reveal.x}px`, "--ry": `${reveal.y}px` } as React.CSSProperties} />}
    </div>
  );
}

/** Cut-corner mecha panel. */
export function MechaPanel({ title, aside, children, className = "", scroll = false }: {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  scroll?: boolean;
}) {
  return (
    <section className={`mp ${scroll ? "scroll" : ""} ${className}`}>
      {(title || aside) && (
        <header className="mp-head">
          <h2>{title}</h2>
          {aside}
        </header>
      )}
      <div className="mp-body">{children}</div>
    </section>
  );
}

/** Honest empty state for capabilities that are not installed yet (§10.2). */
export function CapNotInstalled({ what }: { what: string }) {
  return (
    <div className="cap-empty">
      <span className="cap-dot" />
      <div><strong>{what}</strong><small>Capability not installed — no adapter registered for this planet yet.</small></div>
    </div>
  );
}
