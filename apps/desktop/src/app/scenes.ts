import type { PlanetKey } from "../components/solar/orbits";

/** Scene registry — the single source of truth for the Sun + 9-planet map.
 *  Semantics follow TSAgent_Implementation_Plan_v1.0 §1.1 (scene id, workspace
 *  name, first-wave capability) and §8.1 (accent). `mode` is live (wired to
 *  real run data) or demo (visual skeleton, capability not installed). */
export type SceneId = "sun" | PlanetKey;

export interface SceneDef {
  id: SceneId;
  /** PlanetKey for planets, "sun" for the core entry. */
  planet: PlanetKey | "sun";
  num: string;
  name: string;
  workspace: string;
  accent: string;
  desc: string;
  /** First-wave capability statement from §1.1. */
  mvp: string;
  mode: "live" | "demo";
}

export const SCENES: SceneDef[] = [
  { id: "sun", planet: "sun", num: "00", name: "Sun", workspace: "Omni", accent: "#F6B94A",
    desc: "Cross-planet orchestration — projects, sessions and the task tree.",
    mvp: "Task tree · chat · artifacts · delegation · approvals", mode: "live" },
  { id: "mercury", planet: "mercury", num: "01", name: "Mercury", workspace: "Navigator", accent: "#6FD3FF",
    desc: "Local search navigator — browser takeover not installed (explicit state).",
    mvp: "Local search · result panes · takeover: not installed", mode: "live" },
  { id: "venus", planet: "venus", num: "02", name: "Venus", workspace: "Muse", accent: "#D778FF",
    desc: "Document/work preview with style-aware generation.",
    mvp: "md/txt/svg preview · style.md + presets ride generation", mode: "live" },
  { id: "earth", planet: "earth", num: "03", name: "Earth", workspace: "Organic Intelligence Lab", accent: "#5FE0B1",
    desc: "Brain-like network, parameters and experiments.",
    mvp: "Bio-proxy params · human verdicts · eval records", mode: "live" },
  { id: "mars", planet: "mars", num: "04", name: "Mars", workspace: "Forge", accent: "#FF6A4D",
    desc: "Engineering workstation — workspace files + delegated tasks.",
    mvp: "Workspace files · text read/write · delegated runs", mode: "live" },
  { id: "jupiter", planet: "jupiter", num: "05", name: "Jupiter", workspace: "Archive", accent: "#E6B96B",
    desc: "File browsing + knowledge starmap + memory and history.",
    mvp: "File/artifact retrieval · memory/history replay", mode: "live" },
  { id: "saturn", planet: "saturn", num: "06", name: "Saturn", workspace: "Chronos", accent: "#F2D06B",
    desc: "Automated task board — schedules, triggers and retries.",
    mvp: "Timed triggers · task status · pause · retry · logs", mode: "live" },
  { id: "uranus", planet: "uranus", num: "07", name: "Uranus", workspace: "Laboratory", accent: "#57F0E3",
    desc: "Workflow / skill builder.",
    mvp: "Node graph · save · delegated run · profiles/skills", mode: "live" },
  { id: "neptune", planet: "neptune", num: "08", name: "Neptune", workspace: "Media Studio", accent: "#4DA3FF",
    desc: "Media canvas + asset panel.",
    mvp: "SVG preview · media index · delegated generation", mode: "live" },
  { id: "pluto", planet: "pluto", num: "09", name: "Pluto", workspace: "Genesis", accent: "#9B7BFF",
    desc: "Unknown-matter exploration with human judgement.",
    mvp: "Motifs · candidates · evidence · human judgement", mode: "live" },
];

export const PLANET_SCENES = SCENES.filter((s) => s.planet !== "sun");

export function resolveScene(id: string | undefined): SceneDef | undefined {
  return SCENES.find((s) => s.id === id);
}

export const PLANET_GLYPH: Record<string, string> = {
  sun: "☉", mercury: "☿", venus: "♀", earth: "⊕", mars: "♂",
  jupiter: "♃", saturn: "♄", uranus: "⛢", neptune: "♆", pluto: "♇",
};

/** T1 reveal origin — planet's screen coords at moment of insertion. */
export const lastJump = { x: 0, y: 0, accent: "", at: 0 };
export function setLastJump(v: { x: number; y: number; accent: string }) {
  lastJump.x = v.x; lastJump.y = v.y; lastJump.accent = v.accent; lastJump.at = Date.now();
}

/** T4 delegation jump marker — the target workspace pulses once on arrival. */
export const delegationJump: { scene: string; accent: string; at: number } | null = { scene: "", accent: "", at: 0 };
export function setDelegationJump(scene: string, accent: string) {
  delegationJump!.scene = scene; delegationJump!.accent = accent; delegationJump!.at = Date.now();
}
