import React, { useEffect, useRef, useState } from "react";

/** Anchored popovers (v0.3): cards that open NEXT TO the element they are
 * about — a pin's ❓, a tripwire's ?, the tooltip editor — instead of a
 * centered modal that loses the spatial context. Positioned in canvas-wrap
 * coordinates from scene coords × camera, so they track pan/zoom. */

/**
 * Scene-rect → wrap-pixel anchor for a popover.
 * @param el The anchor element's scene bbox.
 * @param camera Live camera (scrollX/scrollY/zoom).
 * @param wrapW Canvas-wrap width in px, for right-edge clamping.
 * @returns Absolute left/top for the popover inside .canvas-wrap.
 */
export function anchorStyle(
  el: { x: number; y: number; width?: number; height?: number },
  camera: { scrollX: number; scrollY: number; zoom: number },
  wrapW: number,
): React.CSSProperties {
  const z = camera.zoom || 1;
  const left = (el.x + (el.width || 0) + 12 + camera.scrollX) * z;
  const top = (el.y + camera.scrollY) * z;
  return {
    left: Math.max(8, Math.min(left, Math.max(wrapW - 300, 8))),
    top: Math.max(8, top),
  };
}

/**
 * Generic anchored card: closes on Escape (capture — Excalidraw must not
 * eat it) and on click outside.
 * @param root0 Props: `style` (absolute position within .canvas-wrap),
 * `onClose`, `children`.
 * @returns The positioned card.
 */
export function AnchoredPopover({ style, onClose, children }: {
  style: React.CSSProperties; onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        onClose();
      }
    };
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown, true);
    };
  }, [onClose]);
  return (
    <div className="anchored-pop" style={style} ref={ref}
         onClick={(e) => e.stopPropagation()}>
      {children}
    </div>
  );
}

/**
 * Anchored question card for a pin or tripwire: question, choice buttons
 * (tripwires), free-text answer, "full story" escape hatch into the
 * centered QuestionModal.
 * @param root0 Props: `kind` ("pin" | "tripwire"), `data` (the registry
 * record), `onAnswer(text)` (null = read-only), `onFullStory`, `onClose`.
 * @returns The question card body (render inside AnchoredPopover).
 */
export function AnchoredQuestion({ kind, data, onAnswer, onFullStory, onClose }: {
  kind: "pin" | "tripwire"; data: any;
  onAnswer: ((text: string) => void) | null;
  onFullStory: () => void; onClose: () => void;
}) {
  const [text, setText] = useState("");
  const send = (v: string) => {
    if (!v.trim() || !onAnswer) return;
    onAnswer(v.trim());
    onClose();
  };
  const who = data?.direction === "user" || data?.author === "user"
    ? "yours" : "agent";
  return (
    <div className="anchored-q">
      <div className="aq-head">
        <span className="aq-kind">{kind === "pin" ? "❓ pinned question" : "⚠ mapping tripwire"}</span>
        <span className={`aq-who ${who === "yours" ? "user" : "agent"}`}>{who}</span>
        <button className="modal-close" onClick={onClose}>✕</button>
      </div>
      <div className="aq-question">{data?.question}</div>
      {onAnswer && (data?.choices || []).length > 0 && (
        <div className="aq-choices">
          {(data.choices as string[]).map((c) => (
            <button key={c} onClick={() => send(c)}>{c}</button>
          ))}
        </div>
      )}
      {onAnswer ? (
        <div className="aq-answer">
          <input
            autoFocus
            placeholder="Answer in your own words…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") send(text); }}
          />
          <button disabled={!text.trim()} onClick={() => send(text)}>Answer</button>
        </div>
      ) : (
        <div className="aq-settled">{data?.answer ? `answered: ${data.answer}` : "read-only"}</div>
      )}
      <button className="aq-more" onClick={onFullStory}>ⓘ full story →</button>
    </div>
  );
}
