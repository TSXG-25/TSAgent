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
