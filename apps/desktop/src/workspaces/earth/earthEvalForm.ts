import { HUMAN_EVALUATION_VERDICTS, type EvaluationVerdict, type RecordEvaluationRequest } from "../../types/service";

/** Earth — bio-proxy slider definitions and the evaluation-record form.
 *  The sliders are *run input*: the composer request embeds the snapshot so
 *  the simulation actually sees them; the same snapshot is the candidate
 *  identity on the eval record.  Verdicts are the human vocabulary —
 *  execution verdicts (pass/fail/neutral) belong to Saturn's internal path. */
export const EARTH_PARAMS = [
  { id: "thyroid", label: "Thyroid hormones", min: 0, max: 100, def: 50 },
  { id: "cortisol", label: "Cortisol", min: 0, max: 100, def: 42 },
  { id: "insulin", label: "Insulin", min: 0, max: 100, def: 58 },
  { id: "leptin", label: "Leptin", min: 0, max: 100, def: 50 },
  { id: "dopamine", label: "Dopamine", min: 0, max: 100, def: 61 },
] as const;

export const EARTH_VERDICTS = HUMAN_EVALUATION_VERDICTS;

export type ParamValues = Record<string, number>;

export function defaultParamValues(): ParamValues {
  return Object.fromEntries(EARTH_PARAMS.map((p) => [p.id, p.def]));
}

export function paramSnapshot(vals: ParamValues): string {
  return EARTH_PARAMS.map((p) => `${p.id}=${vals[p.id]}`).join(",");
}

/** Prepended to the composer's submitted request — the simulation run
 *  receives the slider snapshot as part of its input. */
export function composerContext(vals: ParamValues): string {
  return `[bio-proxy params: ${paramSnapshot(vals)}]`;
}

export function evalCandidateRef(vals: ParamValues): string {
  return `candidate:${paramSnapshot(vals)}`;
}

/** The params a run was actually submitted with, recovered from its own
 *  request text — the composer embeds `composerContext` as the first line.
 *  A run not launched through this composer carries no prefix, so its
 *  params are unknown and must not be fabricated from live slider state. */
export function parseRunParams(request: string): ParamValues | null {
  const m = request.match(/^\[bio-proxy params: ([^\]]+)\]/m);
  if (!m) return null;
  const out: ParamValues = {};
  for (const pair of m[1].split(",")) {
    const [k, v] = pair.split("=");
    const n = Number(v);
    if (!k || !Number.isFinite(n)) return null;
    out[k] = n;
  }
  return out;
}

export function buildEvalRequest(input: {
  tenantId: string;
  sessionId: string;
  requestId: string;
  runId: string;
  verdict: EvaluationVerdict;
  vals: ParamValues | null;
  notes: string;
}): RecordEvaluationRequest {
  const snapshot = input.vals ? `params: ${JSON.stringify(input.vals)}` : "";
  return {
    tenantId: input.tenantId,
    sessionId: input.sessionId,
    requestId: input.requestId,
    name: `bio-sim-${Date.now().toString(36)}`,
    runId: input.runId,
    baselineRef: "baseline:defaults",
    candidateRef: input.vals
      ? evalCandidateRef(input.vals)
      : `candidate:${input.runId || "unknown"}`,
    verdict: input.verdict,
    notes: [snapshot, input.notes].filter(Boolean).join(" — "),
  };
}
