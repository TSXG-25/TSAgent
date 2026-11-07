import { SCENES, PLANET_SCENES, resolveScene } from "../src/app/scenes";
import { PLANETS } from "../src/components/solar/orbits";

const expected: Record<string, { workspace: string; accent: string }> = {
  sun: { workspace: "Omni", accent: "#F6B94A" },
  mercury: { workspace: "Navigator", accent: "#6FD3FF" },
  venus: { workspace: "Muse", accent: "#D778FF" },
  earth: { workspace: "Organic Intelligence Lab", accent: "#5FE0B1" },
  mars: { workspace: "Forge", accent: "#FF6A4D" },
  jupiter: { workspace: "Archive", accent: "#E6B96B" },
  saturn: { workspace: "Chronos", accent: "#F2D06B" },
  uranus: { workspace: "Laboratory", accent: "#57F0E3" },
  neptune: { workspace: "Media Studio", accent: "#4DA3FF" },
  pluto: { workspace: "Genesis", accent: "#9B7BFF" },
};

function check(cond: boolean, msg: string) {
  if (!cond) throw new Error(`scenes: ${msg}`);
}

check(SCENES.length === 10, `expected 10 scenes, got ${SCENES.length}`);
check(new Set(SCENES.map((s) => s.id)).size === 10, "scene ids must be unique");
check(new Set(SCENES.map((s) => s.planet)).size === 10, "planet bindings must be unique");
check(PLANET_SCENES.length === 9, "exactly 9 planet scenes");

const planetKeys = new Set(PLANETS.map((p) => p.key));
for (const s of PLANET_SCENES) check(planetKeys.has(s.planet as never), `scene ${s.id} binds unknown planet ${s.planet}`);
for (const p of PLANETS) check(PLANET_SCENES.some((s) => s.planet === p.key), `planet ${p.key} has no scene`);

for (const [id, want] of Object.entries(expected)) {
  const s = resolveScene(id);
  check(!!s, `resolveScene("${id}") missing`);
  check(s!.workspace === want.workspace, `${id}.workspace = ${s!.workspace}, want ${want.workspace}`);
  check(s!.accent === want.accent, `${id}.accent = ${s!.accent}, want ${want.accent}`);
  check(s!.mvp.length > 0, `${id}.mvp empty`);
}
check(resolveScene("bogus") === undefined, "unknown id must resolve to undefined");
check(SCENES.find((s) => s.id === "sun")!.mode === "live", "sun must be live");
check(SCENES.find((s) => s.id === "jupiter")!.mode === "live", "jupiter must be live");

console.log("scenes: all checks passed");
