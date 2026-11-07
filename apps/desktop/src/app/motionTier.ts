/** Three-tier effects system (transitions-and-style.md): full / standard /
 *  minimal. reduced-motion forces minimal; SolarScene auto-degrades on low fps. */
export type MotionTier = "full" | "standard" | "minimal";

let tier: MotionTier = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "minimal" : "full";
const listeners = new Set<(t: MotionTier) => void>();

export function motionTier(): MotionTier { return tier; }

export function setMotionTier(next: MotionTier) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) next = "minimal";
  if (next === tier) return;
  tier = next;
  document.documentElement.dataset.fx = next;
  listeners.forEach((l) => l(next));
}

export function onMotionTier(l: (t: MotionTier) => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

document.documentElement.dataset.fx = tier;
