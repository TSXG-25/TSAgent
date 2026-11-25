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
  const dragging = useRef(false);
  const composing = useRef(false);
  const imeRef = useRef<HTMLInputElement>(null);

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
          <button type="button" className={board.selected.has(summary.viewId) ? "nav-taken" : ""}
            title={t("mer.bind-window")}
            onClick={(e) => { e.stopPropagation(); board.toggleSelected(summary.viewId); }}>⌗</button>
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
              // the editable host takes focus so IME/keyboard reach it —
              // a canvas cannot carry a composition.
              imeRef.current?.focus();
              dragging.current = true;
              channelRef.current.down(p.x, p.y);
            }
          }}
          onMouseMove={(e) => {
            if (!dragging.current) return;
            const p = point(e);
            if (p && channelRef.current) channelRef.current.move(p.x, p.y);
          }}
          onMouseUp={(e) => {
            if (!dragging.current) return;
            dragging.current = false;
            const p = point(e);
            if (p && channelRef.current) channelRef.current.up(p.x, p.y);
          }}
          onMouseLeave={() => { dragging.current = false; }}
          onWheel={(e) => {
            const p = point(e as unknown as React.MouseEvent);
            if (p && channelRef.current) {
              channelRef.current.scroll(p.x, p.y, e.deltaX, e.deltaY);
            }
          }}
        />
        <input
          ref={imeRef}
          className="ime-host"
          aria-label={summary.title || summary.viewId}
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={(e) => {
            // IME commit — the full composed string lands at once; the
            // intermediate keydowns were suppressed while composing.
            composing.current = false;
            channelRef.current?.text(e.data);
            e.currentTarget.value = "";
          }}
          onInput={(e) => {
            // Text that landed without a keydown path (IME variants,
            // autocorrect) — forward the host's value and clear it.
            if ((e.nativeEvent as InputEvent).isComposing || composing.current) return;
            const v = e.currentTarget.value;
            if (v) channelRef.current?.text(v);
            e.currentTarget.value = "";
          }}
          onPaste={(e) => {
            const text = e.clipboardData.getData("text");
            if (text) channelRef.current?.text(text);
            e.preventDefault();
          }}
          onCopy={(e) => e.preventDefault()}
          onCut={(e) => e.preventDefault()}
          onKeyDown={(e) => {
            const channel = channelRef.current;
            if (!channel || composing.current || e.nativeEvent.isComposing) return;
            const mods = [
              ...(e.metaKey ? ["meta"] : []),
              ...(e.ctrlKey ? ["ctrl"] : []),
              ...(e.altKey ? ["alt"] : []),
              ...(e.shiftKey ? ["shift"] : []),
            ];
            const chord = e.metaKey || e.ctrlKey || e.altKey || e.key.length > 1;
            if (chord) {
              // Copy/cut/select-all reach the page as real key chords —
              // the page's own clipboard/selection logic handles them.
              channel.key(e.key.length === 1 ? e.key.toUpperCase() : e.key, mods);
            } else {
              // Text-producing key (Shift alone included): the DOM key
              // already carries the shifted glyph — send it as text so
              // insertText types "A", not a bare keyDown that types "".
              channel.text(e.key);
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
