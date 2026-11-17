import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";
import type { RunView, StageStatus, WorkflowView } from "../types";
import type { IconName } from "../app/ui";
import { t } from "../app/i18n";
import { SCENES, PLANET_SCENES, setLastJump, type SceneDef, type SceneId } from "../app/scenes";
import SolarSystem, { solarSceneInstance } from "./solar/SolarSystem";
import { SoundFX } from "./solar/sound";
import { PLANETS, type PlanetKey } from "./solar/orbits";

type NodeState = "idle" | "active" | "done" | "failed";

const RESTORE_KEY = "tsa.selector";

interface SelectorRestore {
  locked: SceneId | null;
  navMode: boolean;
}

function restoreSelector(): SelectorRestore {
  try {
    const raw = sessionStorage.getItem(RESTORE_KEY);
    if (!raw) return { locked: null, navMode: false };
    const parsed = JSON.parse(raw) as SelectorRestore;
    return { locked: parsed.locked && SCENES.some((s) => s.id === parsed.locked) ? parsed.locked : null, navMode: !!parsed.navMode };
  } catch {
    return { locked: null, navMode: false };
  }
}

function stageStateOf(status: StageStatus | undefined): NodeState {
  if (status === "running") return "active";
  if (status === "completed" || status === "verified") return "done";
  if (status === "failed") return "failed";
  return "idle";
}

function stageFor(workflow: WorkflowView | undefined, ...keys: string[]) {
  return workflow?.stages.find((stage) =>
    keys.some((key) => `${stage.stageId} ${stage.name}`.toLowerCase().includes(key)),
  );
}

const stateLabel: Record<NodeState, string> = { idle: "READY", active: "RUNNING", done: "DONE", failed: "FAILED" };

const TIME_PRESETS = [
  { value: 1, label: "1 d/s" },
  { value: 7, label: "1 wk/s" },
  { value: 30, label: "1 mo/s" },
  { value: 365, label: "1 yr/s" },
];

export default function MissionControl({
  activeRun,
  workflow,
  runs,
  runtimeLabel,
  requestDraft,
  setRequestDraft,
  createRun,
  openRun,
  canCancel,
  canResume,
  isCancelling,
  isResuming,
  onCancel,
  onResume,
  onRefresh,
  Icon,
}: {
  activeRun: RunView | null;
  workflow: WorkflowView | undefined;
  runs: RunView[];
  runtimeLabel: string;
  requestDraft: string;
  setRequestDraft: (value: string) => void;
  createRun: (event?: FormEvent<HTMLFormElement>) => void;
  openRun: (runId: string) => void;
  canCancel: boolean;
  canResume: boolean;
  isCancelling: boolean;
  isResuming: boolean;
  onCancel: () => void;
  onResume: () => void;
  onRefresh: () => void;
  Icon: (props: { name: IconName; size?: number; className?: string }) => ReactElement;
}) {
  const navigate = useNavigate();
  const restored = useRef<SelectorRestore | null>(null);
  if (!restored.current) restored.current = restoreSelector();
  const [hovered, setHovered] = useState<SceneId | null>(null);
  const [locked, setLocked] = useState<SceneId | null>(restored.current.locked);
  const [navMode, setNavMode] = useState(restored.current.navMode);
  const [boot, setBoot] = useState(() => !sessionStorage.getItem("tsa.booted") && !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const skipBoot = useCallback(() => { sessionStorage.setItem("tsa.booted", "1"); setBoot(false); }, []);
  useEffect(() => { sessionStorage.setItem(RESTORE_KEY, JSON.stringify({ locked, navMode })); }, [locked, navMode]);
  const dateRef = useRef<HTMLElement>(null);
  const [timeScale, setTimeScale] = useState(() => {
    const saved = Number(localStorage.getItem("tsa.sim.speed"));
    return saved >= 0.1 && saved <= 2000 ? saved : 7;
  });
  const [paused, setPaused] = useState(false);
  useEffect(() => { localStorage.setItem("tsa.sim.speed", String(timeScale)); }, [timeScale]);
  const sound = useRef(new SoundFX());
  const [soundOn, setSoundOn] = useState(sound.current.enabled);


  const runIsLive = activeRun?.status === "active" || activeRun?.status === "cancelling";
  const activeRuns = runs.filter((run) => run.status === "active").length;
  const checkpointCount = runs.filter((run) => run.resume).length;

  const pipeline: { label: string; state: NodeState }[] = [
    { label: "PLANNER", state: stageStateOf(stageFor(workflow, "analy", "plan")?.status) },
    { label: "EXECUTOR", state: stageStateOf(stageFor(workflow, "implement", "exec", "build")?.status) },
    { label: "VERIFIER", state: activeRun?.verifier.status === "verified" ? "done" : activeRun?.verifier.status === "failed" ? "failed" : runIsLive ? "active" : "idle" },
  ];

  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const enterScene = (scene: SceneDef) => {
    const sc = solarSceneInstance();
    const planetKey = scene.planet;
    if (sc && planetKey !== "sun" && !reducedMotion()) {
      sound.current.insert();
      let fired = false;
      const go = (p?: { x: number; y: number }) => {
        if (fired) return; fired = true;
        if (p) setLastJump({ x: p.x, y: p.y, accent: scene.accent });
        void navigate(`/w/${scene.id}`);
      };
      sc.insertion(planetKey, go);
      window.setTimeout(() => go(), 600); // failsafe: never trap the click
    } else {
      void navigate(`/w/${scene.id}`);
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (locked) setLocked(null);
        else if (navMode) setNavMode(false);
        return;
      }
      if (event.key === "Enter" && locked) {
        const scene = SCENES.find((s) => s.id === locked);
        if (scene) enterScene(scene);
        return;
      }
      if (/^[0-9]$/.test(event.key) && navMode) {
        const target = SCENES[Number(event.key)];
        if (target) { event.preventDefault(); setLocked(target.id); }
        return;
      }
      if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && (locked || navMode)) {
        const idx = locked ? PLANET_SCENES.findIndex((x) => x.id === locked) : -1;
        const dir = event.key === "ArrowRight" ? 1 : -1;
        setLocked(PLANET_SCENES[(idx + dir + PLANET_SCENES.length) % PLANET_SCENES.length].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [locked, navMode]);

  const displayed = hovered ?? locked;
  const active = displayed ? SCENES.find((s) => s.id === displayed) ?? null : null;
  const lockedScene = locked ? SCENES.find((s) => s.id === locked) ?? null : null;
  const solarSectors = PLANET_SCENES.map((scene) => {
    const def = PLANETS.find((p) => p.key === scene.planet)!;
    return { key: scene.id, planet: scene.planet as PlanetKey, num: scene.num, label: scene.workspace, planetName: scene.name, accent: scene.accent, au: def.a, period: def.period };
  });
  const onSimTick = (days: number) => {
    if (dateRef.current) dateRef.current.textContent = new Date(Date.UTC(2000, 0, 1, 12) + days * 86400000).toISOString().slice(0, 10);
  };

  return (
    <div className="mc-scroll" data-active={displayed ?? undefined}
          style={{ "--scene-accent": active?.accent } as React.CSSProperties}>
      <div className="mc-main">
        <section
          className={`solar-zone ${locked ? "focus" : ""} ${navMode ? "nav" : ""}`}
          data-active={displayed ?? undefined}
          style={{ "--scene-accent": active?.accent } as React.CSSProperties}
          aria-label="Solar system function selector"
          onMouseLeave={() => setHovered(null)}
        >
          <SolarSystem
            sectors={solarSectors}
            hovered={displayed}
            nav={navMode}
            selected={locked}
            timeScale={timeScale}
            paused={paused}
            onHover={(key) => { if (key && key !== hovered) sound.current.hover(); setHovered(key); }}
            onSelect={(key) => { sound.current.select(); setLocked(locked === key ? null : key); }}
            onSun={() => {
              if (!navMode) { sound.current.lock(); setNavMode(true); return; }
              sound.current.select();
              setLocked(locked === "sun" ? null : "sun");
            }}
            onSunDbl={() => { sound.current.unlock(); setLocked(null); setNavMode(false); }}
            onEmpty={() => { if (locked) setLocked(null); }}
            onTick={onSimTick}
          />

          <div className="solar-hud tr">
            {lockedScene ? (
              <button className="hud-chip action" onClick={() => setLocked(null)} type="button">
                <span>{navMode ? "Back to starmap" : "Back to space"}</span>
              </button>
            ) : navMode ? (
              <button className="hud-chip action" onClick={() => setNavMode(false)} type="button">
                <span>Back to space</span>
              </button>
            ) : (
              <button className="hud-chip action" onClick={() => setNavMode(true)} title="切换到星图视图 — 平面选行星" type="button">
                <span>星图视图</span>
              </button>
            )}
            <button className={`hud-chip sound ${soundOn ? "on" : ""}`} onClick={() => setSoundOn(sound.current.toggle())} title="Toggle UI sound" type="button">
              {soundOn ? "♪" : "·"}
            </button>
          </div>

          <div className="time-dock" role="group" aria-label="Simulation time controls">
            <button className="td-btn" onClick={() => setPaused((value) => !value)} disabled={!!locked || navMode} type="button" aria-label={paused ? "Resume time" : "Pause time"}>
              {paused ? "▶" : "❚❚"}
            </button>
            <div className="td-presets">
              {TIME_PRESETS.map((preset) => (
                <button key={preset.value} className={timeScale === preset.value ? "on" : ""} onClick={() => setTimeScale(preset.value)} type="button">{preset.label}</button>
              ))}
            </div>
            <input
              className="td-slider"
              type="range"
              min={-1}
              max={3.3}
              step={0.05}
              value={Math.log10(timeScale)}
              onChange={(event) => setTimeScale(Math.round(10 ** Number(event.target.value) * 100) / 100)}
              aria-label="Time speed"
            />
            <code className="td-date" ref={dateRef}>—</code>
            {(locked || navMode) && <span className="td-frozen">Frozen</span>}
          </div>

          {!locked && !navMode && <p className="solar-hint">Click the core for starmap · drag to orbit · scroll to zoom</p>}
          {!locked && navMode && <p className="solar-hint">Pick a planet — Esc to return to space</p>}
          {locked && <p className="solar-hint">Enter — open workspace</p>}
          {active && (
            <div className="solar-intel">
              <div className="intel-body" key={active.id}>
                <h2 className="intel-title">{active.workspace}</h2>
                <p className="intel-sub">{active.name}</p>
                <p className="intel-desc">{active.desc}</p>
                <button className="holo-open" onClick={() => enterScene(active)} type="button">
                  <span>Open workspace</span>
                </button>
              </div>
            </div>
          )}

        <form className="console-composer floating" onSubmit={createRun}>
                    <textarea onChange={(event) => setRequestDraft(event.target.value)} placeholder={t("app.ask-placeholder")} rows={2} value={requestDraft} />
          <button className="mc-send" disabled={!requestDraft.trim()} title={t("app.create-run")} type="submit"><Icon name="send" size={14} /></button>
        </form>

        </section>
      </div>


    </div>
  );
}
