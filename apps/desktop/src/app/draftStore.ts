import { useCallback, useEffect, useState } from "react";

/** App-held input drafts keyed by tenant / session / target workspace. A
 *  GlobalChat composer and a workspace composer aimed at the same target
 *  share one draft. Text and attachment references persist in sessionStorage
 *  and are only cleared after the request is accepted. */
export interface InputDraft {
  text: string;
  files: string[];
}

const EMPTY: InputDraft = { text: "", files: [] };
const listeners = new Map<string, Set<() => void>>();

export function draftKey(tenantId: string, sessionId: string, target: string): string {
  return `${tenantId}${sessionId}${target}`;
}

const storeKey = (key: string) => `tsagent:draft:${key}`;

export function loadDraft(key: string): InputDraft {
  try {
    const raw = sessionStorage.getItem(storeKey(key));
    if (raw) {
      const p = JSON.parse(raw) as Partial<InputDraft>;
      return { text: String(p.text ?? ""), files: Array.isArray(p.files) ? p.files.map(String) : [] };
    }
  } catch { /* storage unavailable */ }
  return { ...EMPTY };
}

export function saveDraft(key: string, draft: InputDraft): void {
  try {
    if (!draft.text && draft.files.length === 0) sessionStorage.removeItem(storeKey(key));
    else sessionStorage.setItem(storeKey(key), JSON.stringify(draft));
  } catch { /* quota */ }
  listeners.get(key)?.forEach((fn) => fn());
}

export function clearDraft(key: string): void {
  saveDraft(key, EMPTY);
}

/** Clear only when the stored draft still equals what was submitted — text
 *  typed into the same draft while the request was in flight is user content
 *  and must survive the request completing. */
export function clearDraftIfUnchanged(key: string, submitted: InputDraft): void {
  const cur = loadDraft(key);
  if (cur.text === submitted.text
    && cur.files.length === submitted.files.length
    && cur.files.every((f, i) => f === submitted.files[i])) {
    clearDraft(key);
  }
}

/** Reactive draft binding — every mounted composer sharing `key` stays in
 *  sync because writes notify all listeners. */
export function useDraft(key: string): [InputDraft, (d: InputDraft) => void] {
  const [draft, setDraftState] = useState<InputDraft>(() => loadDraft(key));
  useEffect(() => {
    setDraftState(loadDraft(key));
    let set = listeners.get(key);
    if (!set) { set = new Set(); listeners.set(key, set); }
    const fn = () => setDraftState(loadDraft(key));
    set.add(fn);
    return () => { set?.delete(fn); };
  }, [key]);
  const setDraft = useCallback((d: InputDraft) => {
    saveDraft(key, d);
    setDraftState(d);
  }, [key]);
  return [draft, setDraft];
}
