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

export function buildEvalRequest(input: {
  tenantId: string;
  sessionId: string;
  requestId: string;
  runId: string;
  verdict: EvaluationVerdict;
  vals: ParamValues;
  notes: string;
}): RecordEvaluationRequest {
  return {
    tenantId: input.tenantId,
    sessionId: input.sessionId,
    requestId: input.requestId,
    name: `bio-sim-${Date.now().toString(36)}`,
    runId: input.runId,
    baselineRef: "baseline:defaults",
    candidateRef: evalCandidateRef(input.vals),
    verdict: input.verdict,
    notes: input.notes || `params: ${JSON.stringify(input.vals)}`,
  };
}
