// Real Keplerian orbital mechanics for the mission-control solar system.
// Pure math — no three.js, so it can be unit-tested in Node.
// Elements are J2000 mean elements (degrees / AU / days).

export type PlanetKey =
  | "mercury" | "venus" | "earth" | "mars"
  | "jupiter" | "saturn" | "uranus" | "neptune" | "pluto";

export type PlanetKind = "rocky" | "earth" | "gas" | "ice";

export interface PlanetDef {
  key: PlanetKey;
  name: string;
  /** semi-major axis, AU */
  a: number;
  /** eccentricity */
  e: number;
  /** inclination, deg */
  i: number;
  /** longitude of ascending node, deg */
  node: number;
  /** longitude of perihelion, deg */
  perihelion: number;
  /** mean longitude at J2000, deg */
  meanLongitude0: number;
  /** sidereal orbital period, days */
  period: number;
  /** axial tilt, deg */
  axialTilt: number;
  /** sidereal rotation period, days (negative = retrograde) */
  rotation: number;
  /** display radius in scene units (compressed, not physical) */
  radius: number;
  kind: PlanetKind;
  ringed?: boolean;
}

export const PLANETS: PlanetDef[] = [
  { key: "mercury", name: "Mercury", a: 0.38710, e: 0.20563, i: 7.005, node: 48.33, perihelion: 77.46, meanLongitude0: 252.25, period: 87.969, axialTilt: 0.03, rotation: 58.65, radius: 1.0, kind: "rocky" },
  { key: "venus", name: "Venus", a: 0.72333, e: 0.00677, i: 3.39, node: 76.68, perihelion: 131.60, meanLongitude0: 181.98, period: 224.701, axialTilt: 177.4, rotation: -243.0, radius: 1.15, kind: "rocky" },
  { key: "earth", name: "Earth", a: 1.0, e: 0.01671, i: 0, node: 0, perihelion: 102.94, meanLongitude0: 100.46, period: 365.256, axialTilt: 23.44, rotation: 0.997, radius: 1.2, kind: "earth" },
  { key: "mars", name: "Mars", a: 1.52368, e: 0.0934, i: 1.85, node: 49.56, perihelion: 336.04, meanLongitude0: 355.43, period: 686.98, axialTilt: 25.19, rotation: 1.026, radius: 1.0, kind: "rocky" },
  { key: "jupiter", name: "Jupiter", a: 5.2026, e: 0.04849, i: 1.3, node: 100.46, perihelion: 14.33, meanLongitude0: 34.4, period: 4332.59, axialTilt: 3.13, rotation: 0.414, radius: 4.4, kind: "gas" },
  { key: "saturn", name: "Saturn", a: 9.55491, e: 0.05551, i: 2.49, node: 113.67, perihelion: 93.06, meanLongitude0: 49.94, period: 10759.22, axialTilt: 26.73, rotation: 0.444, radius: 3.8, kind: "gas", ringed: true },
  { key: "uranus", name: "Uranus", a: 19.21845, e: 0.0463, i: 0.77, node: 74.01, perihelion: 173.01, meanLongitude0: 313.23, period: 30685.4, axialTilt: 97.77, rotation: -0.718, radius: 2.15, kind: "ice" },
  { key: "neptune", name: "Neptune", a: 30.11039, e: 0.00899, i: 1.77, node: 131.78, perihelion: 48.12, meanLongitude0: 304.88, period: 60189, axialTilt: 28.32, rotation: 0.671, radius: 2.1, kind: "ice" },
  { key: "pluto", name: "Pluto", a: 39.482, e: 0.2488, i: 17.16, node: 110.3, perihelion: 113.76, meanLongitude0: 238.92, period: 90560, axialTilt: 119.6, rotation: -6.387, radius: 0.6, kind: "ice" },
];

const DEG = Math.PI / 180;

/** Display orbit radius in scene units. Real √a flavor + index spacing so
 *  neighbors never collide and the whole system stays dense and in frame. */
export function displayOrbitRadius(planet: PlanetDef): number {
  return 8 + 9 * Math.sqrt(planet.a) + PLANETS.indexOf(planet) * 1.2;
}

/** Radius of the outermost displayed orbit — used to fit the camera. */
export const OUTER_ORBIT = displayOrbitRadius(PLANETS[PLANETS.length - 1]);

/** Real inclinations make orbit planes cross on screen; flatten for the hologram look. */
const INCL_FLATTEN = 0.35;

export function daysSinceJ2000(date: number | Date): number {
  const ms = typeof date === "number" ? date : date.getTime();
  return (ms - Date.UTC(2000, 0, 1, 12, 0, 0)) / 86400000;
}

/** Newton solve of Kepler's equation M = E - e·sinE. */
export function solveKepler(meanAnomaly: number, e: number): number {
  let E = e < 0.8 ? meanAnomaly : Math.PI;
  for (let k = 0; k < 32; k++) {
    const f = E - e * Math.sin(E) - meanAnomaly;
    E -= f / (1 - e * Math.cos(E));
    if (Math.abs(f) < 1e-10) break;
  }
  return E;
}

/** Heliocentric position in AU at `days` since J2000 (ecliptic frame). */
export function heliocentric(planet: PlanetDef, days: number): { x: number; y: number; z: number } {
  const M = ((planet.meanLongitude0 - planet.perihelion + (360 * days) / planet.period) % 360) * DEG;
  const E = solveKepler(M, planet.e);
  const xOrb = planet.a * (Math.cos(E) - planet.e);
  const yOrb = planet.a * Math.sqrt(1 - planet.e * planet.e) * Math.sin(E);
  const w = (planet.perihelion - planet.node) * DEG;
  const N = planet.node * DEG;
  const i = Math.min(planet.i, 6) * DEG; // clamp: keep the hologram coplanar (Pluto's real 17° otherwise skews the map)
  const cw = Math.cos(w);
  const sw = Math.sin(w);
  const cN = Math.cos(N);
  const sN = Math.sin(N);
  const ci = Math.cos(i);
  const si = Math.sin(i);
  return {
    x: (cN * cw - sN * sw * ci) * xOrb + (-cN * sw - sN * cw * ci) * yOrb,
    y: (sN * cw + cN * sw * ci) * xOrb + (-sN * sw + cN * cw * ci) * yOrb,
    z: sw * si * xOrb + cw * si * yOrb,
  };
}

/** Scene-space position (x, y-up, z) at a given sim day.
 *  Direction comes from the real Keplerian position (inclination tilt and
 *  angular speed variation preserved); radius uses the collision-free
 *  circular display orbit. */
export function scenePosition(planet: PlanetDef, days: number): { x: number; y: number; z: number } {
  const p = heliocentric(planet, days);
  const R = displayOrbitRadius(planet);
  const len = Math.hypot(p.x, p.y, p.z) || 1;
  return { x: (p.x / len) * R, y: (p.z / len) * R * INCL_FLATTEN, z: (-p.y / len) * R };
}

/** Sampled circular orbit path in scene units, for orbit lines. */
export function orbitPath(planet: PlanetDef, segments = 256): Float32Array {
  const R = displayOrbitRadius(planet);
  const out = new Float32Array((segments + 1) * 3);
  const w = (planet.perihelion - planet.node) * DEG;
  const N = planet.node * DEG;
  const i = Math.min(planet.i, 6) * DEG; // clamp: keep the hologram coplanar (Pluto's real 17° otherwise skews the map)
  const cw = Math.cos(w);
  const sw = Math.sin(w);
  const cN = Math.cos(N);
  const sN = Math.sin(N);
  const ci = Math.cos(i);
  const si = Math.sin(i);
  for (let sIdx = 0; sIdx <= segments; sIdx++) {
    const E = (sIdx / segments) * Math.PI * 2;
    const xOrb = R * Math.cos(E);
    const yOrb = R * Math.sin(E);
    const x = (cN * cw - sN * sw * ci) * xOrb + (-cN * sw - sN * cw * ci) * yOrb;
    const y = (sN * cw + cN * sw * ci) * xOrb + (-sN * sw + cN * cw * ci) * yOrb;
    const z = sw * si * xOrb + cw * si * yOrb;
    out[sIdx * 3] = x;
    out[sIdx * 3 + 1] = z * INCL_FLATTEN;
    out[sIdx * 3 + 2] = -y;
  }
  return out;
}
