import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { SCENES, PLANET_GLYPH, type SceneDef } from "./scenes";



/** §3.1 planet switcher — single topbar control, no star-map round trip.
 *  ⌘0 opens Sun, ⌘1–9 the planets in scene order. */
export default function PlanetSwitcher({ scene }: { scene: SceneDef | null }) {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && /^[0-9]$/.test(e.key)) {
        const idx = e.key === "0" ? 0 : Number(e.key);
        const target = idx === 0 ? SCENES[0] : SCENES[idx];
        if (target) { e.preventDefault(); nav(`/w/${target.id}`); }
      }
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("mousedown", onClick); };
  }, [nav]);

  return (
    <div className="planet-switcher" ref={ref}>
      <button className="ps-current" onClick={() => setOpen((v) => !v)} type="button">
        <span className="ps-glyph" style={{ color: scene?.accent ?? "var(--text-main)" }}>{scene ? PLANET_GLYPH[scene.id] : "◎"}</span>
        <span className="ps-name">{scene ? scene.workspace : "星图"}</span>
        <span className="ps-caret">▾</span>
      </button>
      {open && (
        <div className="ps-menu">
          <Link className={`ps-item ${!scene ? "on" : ""}`} to="/" onClick={() => setOpen(false)}>
            <span className="ps-glyph">◎</span><strong>星图</strong><code>⌘·</code>
          </Link>
          {SCENES.map((s, i) => (
            <Link className={`ps-item ${scene?.id === s.id ? "on" : ""}`} key={s.id} to={`/w/${s.id}`} onClick={() => setOpen(false)}>
              <span className="ps-glyph" style={{ color: s.accent }}>{PLANET_GLYPH[s.id]}</span>
              <strong>{s.workspace}</strong>
              <span className="ps-sub">{s.name}</span>
              <code>⌘{i === 0 ? 0 : i}</code>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
