import type { FormEvent, KeyboardEvent } from "react";

/** Shared state for every "create Run" composer; App owns the single draft. */
export interface RunDraft {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

/** Enter submits the enclosing form; Shift+Enter and IME composition insert text. */
export function submitOnEnter(event: KeyboardEvent<HTMLTextAreaElement>): void {
  if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
  event.preventDefault();
  event.currentTarget.form?.requestSubmit();
}
