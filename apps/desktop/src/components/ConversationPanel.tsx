import type { RunView } from "../types";
import { Icon } from "./Icon";
import { submitOnEnter, type RunDraft } from "./runDraft";

export function ConversationPanel({ run, draft }: { run: RunView; draft: RunDraft }) {
  const resumeCompleted = run.resume?.outcome === "completed";
  return (
    <section className="conversation-panel">
      <div className="panel-heading"><div><span className="panel-eyebrow">THREAD</span><h2>Conversation</h2></div><span className="panel-count">{run.conversation.length} messages</span></div>
      <div className="conversation-scroll">
        <div className="thread-start"><span className="thread-start-icon"><Icon name="layers" size={14} /></span><span>Run created in <code>{run.sessionId}</code></span></div>
        {run.conversation.map((message) => (
          <article className={`message ${message.role}`} key={message.messageId}>
            <div className="message-heading"><span className="message-role">{message.role === "user" ? "You" : message.role === "assistant" ? "TSAgent" : "System"}</span><time>{message.at}</time></div>
            <div className="message-content">{message.content}</div>
          </article>
        ))}
        {run.output && (
          <article aria-label="Durable RunOutput" className="message assistant durable-output">
            <div className="message-heading"><span className="message-role">TSAgent · RunOutput</span><time>{run.output.createdAt}</time></div>
            <div className="message-content">{run.output.text}</div>
            <div className="run-output-meta"><span>revision {run.output.revision}</span><span>{run.output.artifactIds.length} artifacts</span><span>{run.output.evidenceIds.length} evidence items</span></div>
          </article>
        )}
        {!run.output && run.failure && (
          <article aria-label="Run failure" className="message system run-failure">
            <div className="message-heading"><span className="message-role">{run.status.toUpperCase()}</span><time>{run.updatedAt}</time></div>
            <div className="message-content">{run.failure.message}</div>
            <div className="run-output-meta"><span>{run.failure.code}</span><span>{run.failure.retryable ? "retryable" : "terminal"}</span></div>
          </article>
        )}
        {run.resume && (
          <div className={`checkpoint-message ${resumeCompleted ? "complete" : ""}`}>
            <div className="checkpoint-message-icon"><Icon name={resumeCompleted ? "check" : "box"} size={14} /></div>
            <div>
              <strong>{resumeCompleted ? "Resume completed" : "Recovery checkpoint persisted"}</strong>
              <span>{resumeCompleted ? "authoritative resume result committed" : "run is eligible for service-managed recovery"}</span>
            </div>
            <em>{resumeCompleted ? "DONE" : "RESUMABLE"}</em>
          </div>
        )}
      </div>

      <form className="task-composer" onSubmit={draft.onSubmit}>
        <textarea onChange={(event) => draft.onChange(event.target.value)} onKeyDown={submitOnEnter} placeholder="Ask TSAgent to work on a task…" rows={2} value={draft.value} />
        <div className="composer-footer">
          <span><Icon name="spark" size={12} /> Creates an isolated RunContext</span>
          <div><kbd>↵</kbd><button className="composer-send" disabled={!draft.value.trim()} type="submit"><span>Create Run</span><Icon name="send" size={13} /></button></div>
        </div>
      </form>
    </section>
  );
}
