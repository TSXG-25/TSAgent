import assert from "node:assert/strict";
import { PLANETS, daysSinceJ2000, displayOrbitRadius, heliocentric, orbitPath, scenePosition, solveKepler } from "../src/components/solar/orbits";

const norm = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

// 1. Kepler solver residual across eccentricities
for (const e of [0, 0.2, 0.9]) {
  for (let k = 0; k < 97; k++) {
    const M = (k / 97) * Math.PI * 2;
    const E = solveKepler(M, e);
    assert.ok(Math.abs(norm(E - e * Math.sin(E) - M)) < 1e-9, `kepler residual e=${e} M=${M}`);
  }
}

// 2. Position is periodic with the sidereal period
for (const p of PLANETS) {
  const t0 = heliocentric(p, 1234.5);
  const t1 = heliocentric(p, 1234.5 + p.period);
  const dist = Math.hypot(t0.x - t1.x, t0.y - t1.y, t0.z - t1.z);
  assert.ok(dist < p.a * 1e-6, `${p.name} not periodic`);
}

// 3. Apsides distances
for (const p of PLANETS) {
  const M0 = -(p.meanLongitude0 - p.perihelion) * (p.period / 360); // days back to perihelion
  const peri = heliocentric(p, M0);
  const dPeri = Math.hypot(peri.x, peri.y, peri.z);
  assert.ok(Math.abs(dPeri - p.a * (1 - p.e)) < 1e-6, `${p.name} perihelion`);
  const api = heliocentric(p, M0 + p.period / 2);
  const dApi = Math.hypot(api.x, api.y, api.z);
  assert.ok(Math.abs(dApi - p.a * (1 + p.e)) < 1e-6, `${p.name} aphelion`);
}

// 4. Earth near perihelion 2000-01-03 ≈ 0.9833 AU
{
  const earth = PLANETS.find((p) => p.key === "earth")!;
  const d = heliocentric(earth, daysSinceJ2000(Date.UTC(2000, 0, 3)));
  const dist = Math.hypot(d.x, d.y, d.z);
  assert.ok(Math.abs(dist - 0.9833) < 0.002, `earth perihelion dist ${dist}`);
}

// 5. Display orbits: ordered, strictly separated, dense
for (let k = 0; k < PLANETS.length - 1; k++) {
  const gap = displayOrbitRadius(PLANETS[k + 1]) - displayOrbitRadius(PLANETS[k]);
  assert.ok(gap >= 2.5, `${PLANETS[k].name}/${PLANETS[k + 1].name} gap ${gap.toFixed(2)}`);
}
for (const p of PLANETS) {
  const pos = scenePosition(p, 1000);
  const r = Math.hypot(pos.x, pos.y, pos.z);
  assert.ok(r <= displayOrbitRadius(p) + 0.01, `${p.name} exceeds its display orbit`);
}

// 6. orbitPath returns a closed loop with sane bounds
for (const p of PLANETS) {
  const path = orbitPath(p, 64);
  assert.equal(path.length, 65 * 3);
  const rMax = displayOrbitRadius(p) * (1 + 1e-5);
  for (let k = 0; k < 65; k++) {
    const r = Math.hypot(path[k * 3], path[k * 3 + 1], path[k * 3 + 2]);
    assert.ok(r <= rMax, `${p.name} path out of bounds`);
  }
  assert.equal(path[0], path[64 * 3]);
  const sp = scenePosition(p, 0);
  assert.ok(Number.isFinite(sp.x + sp.y + sp.z));
}

console.log("orbits: all checks passed");
