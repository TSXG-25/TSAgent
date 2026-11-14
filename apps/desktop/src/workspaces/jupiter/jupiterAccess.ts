import type { FactRecordSummary, JupiterArtifactSummary } from "../../types/service";

/** Jupiter — the inspect rail shows the record's real fields, not a fixed
 *  permission bar.  Artifacts report existence/verification; facts report
 *  scope/source/revision. */
export type AccessRow = { label: string; value: string };

export function accessRows(item: FactRecordSummary | JupiterArtifactSummary): AccessRow[] {
  if ("artifactId" in item) {
    return [
      { label: "exists", value: item.exists ? "✓" : "✗" },
      { label: "verified", value: item.verified ? "✓" : "—" },
      { label: "type", value: item.artifactType },
      { label: "run", value: item.runId },
    ];
  }
  return [
    { label: "scope", value: item.scope },
    { label: "source", value: item.sourceKind },
    { label: "revision", value: String(item.revision) },
    { label: "evidence", value: item.evidenceId || "—" },
  ];
}

/** Preview request gate — each request mints a monotonically increasing
 *  token; a response commits only while its token is current. Selection
 *  changes and newer requests invalidate in-flight ones, so a late A₁
 *  response can't overwrite the A₂-or-newer result (object identity can't
 *  tell same-artifact requests apart). */
export function createPreviewGate() {
  let current = 0;
  return {
    next(): number { return ++current; },
    isCurrent(token: number): boolean { return token === current; },
    invalidate(): void { current += 1; },
  };
}
