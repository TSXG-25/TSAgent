/** Venus — style.md preferences and preset selection ride the actual
 *  composition request (AgentComposer contextPrefix), not page-local state. */
export const VENUS_PRESETS = [
  { id: "brutalist", name: "Brutalist", swatch: "#4a4f57" },
  { id: "editorial", name: "Editorial", swatch: "#d1b57c" },
  { id: "tech-doc", name: "Tech Doc", swatch: "#3a6ae0" },
  { id: "minimal", name: "Minimal", swatch: "#8a8f98" },
] as const;

export function styleContext(style: string, presetId: string | null): string {
  const preset = VENUS_PRESETS.find((p) => p.id === presetId)?.name;
  const parts: string[] = [];
  if (preset) parts.push(`[style preset: ${preset}]`);
  const trimmed = style.trim();
  if (trimmed) parts.push(`[style.md]\n${trimmed}`);
  return parts.join("\n");
}
