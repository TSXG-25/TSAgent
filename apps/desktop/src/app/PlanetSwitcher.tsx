import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { SCENES, PLANET_GLYPH, type SceneDef } from "./scenes";
import { t } from "./i18n";



/** §3.1 planet switcher — single topbar control, no star-map round trip.
 *  ⌘0 opens Sun, ⌘1–9 the planets in scene order. Capability keywords
 *  (name/workspace/desc/mvp) are searchable; recent visits are remembered
 *  locally. The star map and the existing shortcuts stay. */

const RECENT_KEY = "tsagent:recent-scenes";
const loadRecent = (): string[] => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as string[]; } catch { return []; } };

const hay = (s: SceneDef) => `${s.id} ${s.name} ${s.workspace} ${s.desc} ${s.mvp}`.toLowerCase();

export default function PlanetSwitcher({ scene }: { scene: SceneDef | null }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const ref = useRef<HTMLDivElement>(null);
  const [recent, setRecent] = useState<string[]>(loadRecent);

  // record visits — recents are a local navigation aid, not a backend fact
  useEffect(() => {
    if (!scene) return;
    setRecent((r) => {
      const next = [scene.id, ...r.filter((x) => x !== scene.id)].slice(0, 4);
      try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* quota */ }
      return next;
    });
  }, [scene?.id]);

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

  const ql = q.trim().toLowerCase();
  const matched = SCENES.filter((s) => !ql || hay(s).includes(ql));
  const recentScenes = recent
    .map((id) => matched.find((s) => s.id === id))
    .filter((s): s is SceneDef => s !== undefined);
  const rest = matched.filter((s) => !recentScenes.includes(s));

  const item = (s: SceneDef, i?: number) => (
    <Link className={`ps-item ${scene?.id === s.id ? "on" : ""}`} key={s.id} to={`/w/${s.id}`} onClick={() => { setOpen(false); setQ(""); }}>
      <span className="ps-glyph" style={{ color: s.accent }}>{PLANET_GLYPH[s.id]}</span>
      <strong>{s.workspace}</strong>
      <span className="ps-sub">{s.name}</span>
      {i !== undefined && <code>⌘{i === 0 ? 0 : i}</code>}
    </Link>
  );

  return (
    <div className="planet-switcher" ref={ref}>
      <button className="ps-current" onClick={() => setOpen((v) => !v)} type="button">
        <span className="ps-glyph" style={{ color: scene?.accent ?? "var(--text-main)" }}>{scene ? PLANET_GLYPH[scene.id] : "◎"}</span>
        <span className="ps-name">{scene ? scene.workspace : t("ps.starmap")}</span>
        <span className="ps-caret">▾</span>
      </button>
      {open && (
        <div className="ps-menu">
          <input autoFocus className="ps-search" onChange={(e) => setQ(e.target.value)} placeholder={t("ps.search")} value={q} />
          <Link className={`ps-item ${!scene ? "on" : ""}`} to="/" onClick={() => { setOpen(false); setQ(""); }}>
            <span className="ps-glyph">◎</span><strong>{t("ps.starmap")}</strong><code>⌘·</code>
          </Link>
          {recentScenes.length > 0 && !ql && <span className="ps-group">{t("ps.recent")}</span>}
          {recentScenes.map((s) => item(s))}
          <span className="ps-group">{t("ps.all")}</span>
          {rest.map((s) => item(s, SCENES.indexOf(s)))}
          {ql && matched.length === 0 && <span className="ps-group">{t("sun.no-matching")}</span>}
        </div>
      )}
    </div>
  );
}
