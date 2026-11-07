import { Icon } from "./Icon";
import { submitOnEnter, type RunDraft } from "./runDraft";

interface CreateRunModalProps {
  runtimeLabel: string;
  draft: RunDraft;
  onClose: () => void;
}

export function CreateRunModal({ runtimeLabel, draft, onClose }: CreateRunModalProps) {
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div aria-labelledby="create-run-title" aria-modal="true" className="create-modal" onKeyDown={(event) => event.key === "Escape" && onClose()} onMouseDown={(event) => event.stopPropagation()} role="dialog">
        <div className="modal-heading">
          <div><span className="panel-eyebrow">NEW TASK</span><h2 id="create-run-title">Create an isolated Run</h2></div>
          <button aria-label="Close" className="modal-close" onClick={onClose} type="button"><Icon name="x" size={16} /></button>
        </div>
        <p>{runtimeLabel} 将通过稳定的 AgentService Contract 创建 Run；local 模式不可用时不会回退到 Mock。</p>
        <form onSubmit={draft.onSubmit}>
          <label htmlFor="new-run-request">Task</label>
          <textarea autoFocus id="new-run-request" onChange={(event) => draft.onChange(event.target.value)} onKeyDown={submitOnEnter} placeholder="例如：生成一个 Python 程序，计算 7 的平方并保存" rows={5} value={draft.value} />
          <div className="modal-footer">
            <span>Enter 创建 · Shift+Enter 换行 · Esc 关闭</span>
            <button className="composer-send" disabled={!draft.value.trim()} type="submit"><span>Create Run</span><Icon name="arrow-up-right" size={13} /></button>
          </div>
        </form>
      </div>
    </div>
  );
}
