import { useEffect, useRef, useState } from "react";
import { SolarScene } from "./SolarScene";
import { daysSinceJ2000, type PlanetKey } from "./orbits";
import type { SceneId } from "../../app/scenes";

export interface SolarSector {
  key: SceneId;
  planet: PlanetKey;
  num: string;
  label: string;
  planetName: string;
  accent: string;
  au: number;
  period: number;
}

export default function SolarSystem({
  sectors,
  hovered,
  selected,
  nav,
  timeScale,
  paused,
  onHover,
  onSelect,
  onSun,
  onSunDbl,
  onEmpty,
  onTick,
}: {
  sectors: SolarSector[];
  hovered: SceneId | null;
  selected: SceneId | null;
  nav: boolean;
  timeScale: number;
  paused: boolean;
  onHover: (key: SceneId | null) => void;
  onSelect: (key: SceneId) => void;
  onSun: () => void;
  onSunDbl: () => void;
  onEmpty: () => void;
  onTick: (simDays: number) => void;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SolarScene | null>(null);
  const [failed, setFailed] = useState(false);
  const labelRefs = useRef(new Map<SceneId, HTMLButtonElement>());
  const ringRef = useRef<SVGSVGElement>(null);
  const ringTarget = useRef<SceneId | null>(null);
  ringTarget.current = selected ?? hovered;
  const ringSector = useRef<SceneId | null>(null);
  const sectorsRef = useRef(sectors);
  sectorsRef.current = sectors;
  const cbRef = useRef({ onHover, onSelect, onSun, onSunDbl, onEmpty, onTick });
  cbRef.current = { onHover, onSelect, onSun, onSunDbl, onEmpty, onTick };
  // two hover sources — DOM labels and the 3D raycast — merged here, label wins
  const hoverRef = useRef<{ label: SceneId | null; scene: SceneId | null }>({ label: null, scene: null });
  const emitHover = () => cbRef.current.onHover(hoverRef.current.label ?? hoverRef.current.scene);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const byPlanet = new Map(sectorsRef.current.map((s) => [s.planet, s.key]));
    let scene: SolarScene;
    try {
      scene = new SolarScene(
      mount,
      {
        onHover: (p) => { hoverRef.current.scene = p ? (byPlanet.get(p) ?? null) : null; emitHover(); },
        onSelect: (p) => { const k = byPlanet.get(p); if (k) cbRef.current.onSelect(k); },
        onSun: () => cbRef.current.onSun(),
        onSunDbl: () => cbRef.current.onSunDbl(),
        onEmpty: () => cbRef.current.onEmpty(),
        onTick: (d) => cbRef.current.onTick(d),
      },
      Object.fromEntries(sectorsRef.current.map((s) => [s.planet, parseInt(s.accent.slice(1), 16)])),
    );
    } catch {
      setFailed(true);
      return;
    }
    sceneRef.current = scene;
    _liveScene = scene;

    const resize = () => scene.resize(mount.clientWidth, mount.clientHeight);
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(mount);

    let raf = 0;
    const place = () => {
      const c = scene.center();
      const placed: { x: number; y: number }[] = [];
      for (const s of sectorsRef.current) {
        const el = labelRefs.current.get(s.key);
        if (!el) continue;
        const p = scene.project(s.planet);
        const wave = scene.waveRadius();
        el.classList.toggle('scan-locked', wave === Infinity || wave >= scene.orbitRadiusOf(s.planet));
        const lineupMode = scene.inLineup;
        let dx = lineupMode ? 0 : p.x - c.x;
        let dy = lineupMode ? 1 : p.y - c.y;
        const len = Math.hypot(dx, dy) || 1;
        dx /= len; dy /= len;
        let lx = p.x + dx * (p.r + 14);
        let ly = p.y + dy * (p.r + 14);
        // radial first, then tangent fan so inner planets don't stack
        const tx = -dy, ty = dx;
        let resolved = false;
        for (let guard = 0; guard < 14 && !resolved; guard++) {
          resolved = !placed.some((q) => Math.abs(q.x - lx) < 110 && Math.abs(q.y - ly) < 34);
          if (!resolved) { lx += dx * 12; ly += dy * 12; }
        }
        for (let guard = 0, side = 1; guard < 10 && !resolved; guard++) {
          lx += tx * 26 * side; ly += ty * 26 * side; side = -side;
          resolved = !placed.some((q) => Math.abs(q.x - lx) < 110 && Math.abs(q.y - ly) < 34);
        }
        lx = Math.min(Math.max(lx, 80), mount.clientWidth - 80);
        ly = Math.min(Math.max(ly, 34), mount.clientHeight - 34);
        placed.push({ x: lx, y: ly });
        el.style.transform = `translate(${lx.toFixed(1)}px, ${ly.toFixed(1)}px) translate(-50%, -50%)`;
        el.style.visibility = p.visible ? "visible" : "hidden";
      }
      mount.classList.toggle("locked", scene.waveRadius() === Infinity);
      const ring = ringRef.current;
      if (ring) {
        const tk = ringTarget.current;
        const sector = tk ? sectorsRef.current.find((x) => x.key === tk) : null;
        if (sector) {
          ringSector.current = tk;
          const p = scene.project(sector.planet);
          const d = p.r * 2 + 20;
          if (!Number.isFinite(d) || !Number.isFinite(p.x) || !Number.isFinite(p.y)) {
            ring.style.opacity = "0";
            raf = requestAnimationFrame(place);
            return;
          }
          ring.setAttribute("width", `${d}`);
          ring.setAttribute("height", `${d}`);
          ring.setAttribute("viewBox", `0 0 ${d} ${d}`);
          (ring.firstElementChild as SVGCircleElement | null)?.setAttribute("r", `${d / 2 - 3}`);
          ring.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px) translate(-50%, -50%)`;
          ring.style.opacity = "1";
        } else {
          ring.style.opacity = "0";
        }
      }
      raf = requestAnimationFrame(place);
    };
    raf = requestAnimationFrame(place);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      scene.dispose();
      sceneRef.current = null;
      _liveScene = null;
    };
  }, []);

  useEffect(() => {
    const bySector = new Map(sectors.map((s) => [s.key, s.planet]));
    sceneRef.current?.setHovered(hovered ? (bySector.get(hovered) ?? null) : null);
  }, [hovered, sectors]);

  useEffect(() => {
    const bySector = new Map(sectors.map((s) => [s.key, s.planet]));
    sceneRef.current?.setSelected(selected === "sun" ? ("sun" as PlanetKey) : selected ? (bySector.get(selected) ?? null) : null);
  }, [selected, sectors]);

  useEffect(() => { sceneRef.current?.setNav(nav); }, [nav]);
  useEffect(() => { sceneRef.current?.setTimeScale(timeScale); }, [timeScale]);
  useEffect(() => { sceneRef.current?.setPaused(paused); }, [paused]);

  if (failed) {
    return (
      <div className="solar-fallback">
        <p>3D unavailable — pick a module:</p>
        <div className="sf-grid">
          {sectors.map((s) => (
            <button key={s.key} className={`solar-label ${selected === s.key ? "sel" : ""}`} style={{ "--sl-accent": s.accent } as React.CSSProperties} onClick={() => onSelect(s.key)} type="button">
              <span className="sl-row"><strong>{s.label}</strong></span>
              <span className="sl-planet">{s.planetName}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="solar-mount" ref={mountRef}>
      <div className="lock-title">TEMPORAL LOCK ESTABLISHED</div>
      <svg className="target-ring" ref={ringRef} aria-hidden="true">
        <circle cx="50%" cy="50%" fill="none" />
      </svg>
      {sectors.map((s) => (
        <button
          key={s.key}
          ref={(el) => { if (el) labelRefs.current.set(s.key, el); }}
          className={`solar-label ${hovered === s.key ? "hot" : ""} ${selected === s.key ? "sel" : ""}`} style={{ "--sl-accent": s.accent } as React.CSSProperties}
          onClick={() => onSelect(s.key)}
          onMouseEnter={() => { hoverRef.current.label = s.key; emitHover(); }}
          onMouseLeave={() => { hoverRef.current.label = null; emitHover(); }}
          type="button"
        >
          <span className="sl-row"><strong>{s.label}</strong></span>
          <span className="sl-planet">{s.planetName}</span>
          <span className="sl-data">{s.au.toFixed(2)} AU · {s.period >= 365 ? `${(s.period / 365.25).toFixed(1)} yr` : `${s.period.toFixed(0)} d`}</span>

        </button>
      ))}
    </div>
  );
}

export { daysSinceJ2000 };

let _liveScene: SolarScene | null = null;
export function solarSceneInstance(): SolarScene | null { return _liveScene; }
