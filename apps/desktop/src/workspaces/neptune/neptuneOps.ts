/** Neptune — media ops.  Only SVG previews are real today; other formats
 *  show an explicit placeholder.  Generation ops are delegations bound to
 *  the selected artifact's producing run — a request, not a built-in
 *  capability (the uninstalled-capability state stays visible). */
export function mediaDelegationRequest(path: string, producingRunId: string): string {
  return `将 ${path}（来自 run ${producingRunId}）渲染为 MP4 视频并保存到 output/`;
}
