import { useEffect, useRef, useState, type FormEvent } from "react";
import { t } from "../../app/i18n";
import type { WindowSummary } from "../../types/service";
import type { DisplayChannel, MercuryWindowBoard } from "./windows";

/** One live browser pane: chrome strip (title/url/controls) over a
 *  canvas fed by the display channel; clicks, scrolls and keys go back
 *  up the same connection.  The canvas maps rendered pixels to the
 *  frame's device pixels via meta.deviceWidth/deviceHeight. */
export default function BrowserPane({
  board,
  summary,
  active,
  onSelect,
  onExpand,
  expanded,
}: {
  board: MercuryWindowBoard;
  summary: WindowSummary;
  active: boolean;
  onSelect: () => void;
  onExpand: () => void;
  expanded: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const channelRef = useRef<DisplayChannel | null>(null);
  const metaRef = useRef<Record<string, unknown>>({});
  const [channelState, setChannelState] = useState<"connecting" | "live" | "dead">("connecting");
  const [addr, setAddr] = useState("");

  useEffect(() => {
    setAddr(summary.url);
  }, [summary.url]);

  // Attach the channel once per window — the channel survives summary
  // refreshes; it dies only with the window or a channel failure.
  useEffect(() => {
    let dead = false;
    setChannelState("connecting");
    void board.channelFor(summary.viewId).then((channel) => {
      if (dead) return;
      if (!channel) {
        setChannelState("dead");
        return;
      }
      channelRef.current = channel;
      channel.onFrame = (bmp, meta) => {
        metaRef.current = meta;
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx2d = canvas.getContext("2d");
        if (!ctx2d) return;
        canvas.width = bmp.width;
        canvas.height = bmp.height;
        ctx2d.drawImage(bmp, 0, 0);
      };
      channel.onClose = () => setChannelState("dead");
      setChannelState("live");
      // Report the painted size so the browser viewport matches what
      // the user sees — new frames arrive at the new device size.
      const box = canvasRef.current?.getBoundingClientRect();
      if (box && box.width > 0 && box.height > 0) {
        channel.viewport(Math.round(box.width), Math.round(box.height));
      }
    });
    return () => {
      dead = true;
      channelRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary.viewId, board]);

  const point = (e: React.MouseEvent) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const dw = Number(metaRef.current.deviceWidth) || canvas.width;
    const dh = Number(metaRef.current.deviceHeight) || canvas.height;
    return {
      x: ((e.clientX - rect.left) / rect.width) * dw,
      y: ((e.clientY - rect.top) / rect.height) * dh,
    };
  };

  const submitAddr = (e: FormEvent) => {
    e.preventDefault();
    const url = addr.trim();
    if (!url) return;
    void board.navigate(summary.viewId, "navigate", url);
  };

  return (
    <section
      className={`nav-view live${active ? " active" : ""}${summary.error ? " error" : ""}`}
      onClick={onSelect}
      onKeyDown={onSelect}
      role="group"
      aria-label={summary.title || summary.viewId}
    >
      <header>
        <span className="nav-win-title" title={summary.viewId}>
          {summary.loading ? "… " : ""}
          {summary.title || summary.viewId.slice(0, 8)}
        </span>
        <span className="nav-win-ctl">
          <button type="button" disabled={!summary.canBack} title="back"
            onClick={(e) => { e.stopPropagation(); void board.navigate(summary.viewId, "back"); }}>‹</button>
          <button type="button" disabled={!summary.canForward} title="forward"
            onClick={(e) => { e.stopPropagation(); void board.navigate(summary.viewId, "forward"); }}>›</button>
          <button type="button" title="reload"
            onClick={(e) => { e.stopPropagation(); void board.navigate(summary.viewId, "reload"); }}>⟳</button>
          <button type="button" className={summary.controller === "user" ? "nav-taken" : ""}
            title={summary.controller === "user" ? t("mer.hand-back") : t("mer.take-over")}
            onClick={(e) => {
              e.stopPropagation();
              void (summary.controller === "user"
                ? board.release(summary.viewId)
                : board.claim(summary.viewId));
            }}>{summary.controller === "user" ? "◉" : "○"}</button>
          <button type="button" title={expanded ? t("mer.restore") : t("mer.expand")}
            onClick={(e) => { e.stopPropagation(); onExpand(); }}>{expanded ? "⊡" : "⊞"}</button>
          <button type="button" className="nav-win-close" title="close"
            onClick={(e) => { e.stopPropagation(); void board.close(summary.viewId); }}>×</button>
        </span>
      </header>
      <form className="nav-addr" onSubmit={submitAddr} onClick={(e) => e.stopPropagation()}>
        <input value={addr} onChange={(e) => setAddr(e.target.value)}
          placeholder="https://…" spellCheck={false} />
      </form>
      <div className="nav-view-body canvas" onClick={(e) => e.stopPropagation()}>
        {summary.error && <div className="nav-win-error">{t("mer.window-error")}</div>}
        {channelState === "dead" && (
          <div className="empty-state"><p>{t("mer.channel-dead")}</p></div>
        )}
        <canvas
          ref={canvasRef}
          tabIndex={0}
          onMouseDown={(e) => {
            const p = point(e);
            if (p && channelRef.current) {
              canvasRef.current?.focus();
              channelRef.current.click(p.x, p.y);
            }
          }}
          onWheel={(e) => {
            const p = point(e as unknown as React.MouseEvent);
            if (p && channelRef.current) {
              channelRef.current.scroll(p.x, p.y, e.deltaX, e.deltaY);
            }
          }}
          onKeyDown={(e) => {
            const channel = channelRef.current;
            if (!channel) return;
            if (e.key.length === 1 && !e.metaKey && !e.ctrlKey) {
              channel.text(e.key);
            } else if (e.key.length > 1) {
              channel.key(e.key);
            }
            e.preventDefault();
          }}
        />
      </div>
      <footer className="nav-win-foot">
        <code>{summary.url || "about:blank"}</code>
        <span>{summary.controller === "user" ? t("mer.you-drive") : `agent`}</span>
      </footer>
    </section>
  );
}
