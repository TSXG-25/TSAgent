/** Wire contract between a workspace's send-time context and the thread
 *  renderer: the marker is a stable machine tag (never localized), so a
 *  record stored under any locale renders the same way everywhere. */
export const IDE_FILE_RE = /^\[IDE file\] (\S+)\n/;

export const ideFilePrefix = (path: string): string => `[IDE file] ${path}\n`;

/** Splits a stored request into its IDE-file context (if any) and the
 *  text the user actually typed — the record keeps the full payload but
 *  the bubble shows a context chip, not the raw marker line. */
export function splitIdeContext(request: string): { file: string; text: string } {
  const m = request.match(IDE_FILE_RE);
  return m ? { file: m[1], text: request.slice(m[0].length) } : { file: "", text: request };
}
