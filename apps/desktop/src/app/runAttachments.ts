/** Files a composer attached to a run it submitted — the run snapshot does
 *  not echo context refs back, so the thread shows what this client sent. */
const byRun = new Map<string, string[]>();

export function rememberRunAttachments(runId: string, files: string[]): void {
  byRun.set(runId, files);
}

export function runAttachments(runId: string): string[] {
  return byRun.get(runId) ?? [];
}
