import type { WorkspaceFileContent, WorkspaceFileRequest } from "../../types/service";

export interface DocumentIdentity {
  tenantId: string;
  sessionId: string;
}

export interface DocumentSessionService {
  readWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent>;
  writeWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent>;
}

export interface ForgeDocument {
  /** Resolved workspace root the read came from — the Service verifies it
   *  on every save, so a same-session rebind cannot redirect the draft. */
  workspaceRoot: string;
  path: string;
  text: string;
  digest: string;
  redacted: boolean;
}

/** One bound document: path + workspace identity + read baseline + draft.
 *  Async reads are sequence-gated so a late response for a superseded
 *  selection can never overwrite the current document; saves are bound to
 *  the workspace the read came from and to the baseline digest. */
export class ForgeDocumentSession {
  doc: ForgeDocument | null = null;
  content = "";
  dirty = false;
  saving = false;
  private readSeq = 0;
  private editSeq = 0;
  private requestSeq = 0;

  constructor(
    private readonly service: DocumentSessionService,
    private readonly identity: () => DocumentIdentity,
    private readonly onChange: () => void = () => {},
  ) {}

  private req() {
    return { ...this.identity(), requestId: `forge-${++this.requestSeq}` };
  }

  /** Returns false when an unsaved draft blocks the switch — the caller
   *  may confirm and retry with `discardDraft`. */
  async open(path: string, opts: { discardDraft?: boolean } = {}): Promise<boolean> {
    if (this.dirty && !opts.discardDraft) return false;
    const seq = ++this.readSeq;
    let c: WorkspaceFileContent;
    try {
      c = await this.service.readWorkspaceFile({ ...this.req(), path });
    } catch (error) {
      if (seq === this.readSeq) throw error;
      return true;
    }
    if (seq !== this.readSeq) return true;
    this.doc = {
      workspaceRoot: c.workspaceRoot,
      path: c.path,
      text: c.text,
      digest: c.digest,
      redacted: c.redacted,
    };
    this.content = c.text;
    this.dirty = false;
    this.onChange();
    return true;
  }

  edit(next: string): void {
    this.content = next;
    this.editSeq += 1;
    this.dirty = true;
    this.onChange();
  }

  async save(): Promise<void> {
    const doc = this.doc;
    if (!doc || doc.redacted) return;
    this.saving = true;
    const sent = this.editSeq;
    try {
      const saved = await this.service.writeWorkspaceFile({
        ...this.req(),
        path: doc.path,
        content: this.content,
        baseDigest: doc.digest,
        workspaceRoot: doc.workspaceRoot,
      });
      if (this.doc === doc) {
        // Keep typing during save: advance the baseline to the written
        // bytes, but stay dirty when the draft moved past what was sent.
        doc.digest = saved.digest;
        this.dirty = this.editSeq !== sent;
      }
    } finally {
      this.saving = false;
      this.onChange();
    }
  }
}
