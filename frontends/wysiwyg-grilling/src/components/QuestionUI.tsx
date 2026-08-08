import React, { useEffect, useState } from "react";

/** Shared UI for the two in-place question kinds (spec v0.1 §10 addendum):
 * pins (open questions) and mapping tripwires. Both get a goto affordance
 * (reveal the anchored element on canvas) and a detail modal (the what,
 * the why, concrete examples). Tripwires are answerable in place — choice
 * buttons when the entry carries `choices`, free text always. */

export function gotoRefOf(item: any, kind: "pin" | "tripwire") {
  if (kind === "pin")
    return item.artifact && item.element
      ? { aid: item.artifact, el: item.element }
      : null;
  const [aid, el] = String(item.changed || "").split("#");
  return aid && el ? { aid, el } : null;
}

export function TripwireCard({
  t, disabled, onAnswer, onGoto, onOpen, compact,
}: {
  t: any; disabled?: boolean;
  onAnswer: (answer: string) => void;
  onGoto: (() => void) | null;
  onOpen: () => void;
  compact?: boolean;
}) {
  const [text, setText] = useState("");
  const answered = t.status === "answered";
  return (
    <div
      className={`pin-card tripwire ${compact ? "compact" : ""}`}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("button,input")) return;
        onOpen();
      }}
      title="click for the full story"
    >
      <div className="q">
        ⚠ {t.question ||
          `${String(t.changed).replace("#", " › ")} diverged from ${String(t.sibling).replace("#", " › ")} — intentional?`}
      </div>
      <div className="anchor">
        {String(t.changed).replace("#", " › ")} · save {t.save}
      </div>
      {answered ? (
        <div className="a">↳ {t.answer}</div>
      ) : (
        <>
          {(t.choices || []).length > 0 && (
            <div className="choice-row">
              {(t.choices || []).map((c: string) => (
                <button key={c} disabled={disabled}
                  onClick={() => onAnswer(c)}>{c}</button>
              ))}
            </div>
          )}
          <div className="answer-row">
            <input
              placeholder="Or answer in your own words…"
              value={text}
              disabled={disabled}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && text.trim()) onAnswer(text.trim());
              }}
            />
            <button disabled={!text.trim() || disabled}
              onClick={() => onAnswer(text.trim())}>
              Answer
            </button>
          </div>
        </>
      )}
      <div className="card-foot">
        {onGoto && (
          <button className="linkish"
            onClick={onGoto} title="reveal this element on the canvas">
            ⌖ show on canvas
          </button>
        )}
        <button className="linkish" onClick={onOpen}>ⓘ details</button>
      </div>
    </div>
  );
}

export function QuestionModal({
  kind, data, onClose, onGoto, onAnswer,
}: {
  kind: "pin" | "tripwire"; data: any;
  onClose: () => void; onGoto: (() => void) | null;
  onAnswer?: ((answer: string) => void) | null;
}) {
  const [text, setText] = useState("");
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  const isPin = kind === "pin";
  const title = isPin ? data.question : (data.question ||
    `${String(data.changed).replace("#", " › ")} diverged — intentional?`);
  const anchorLine = isPin
    ? data.artifact && `anchored to ${data.artifact} › ${data.element}`
    : `between ${String(data.changed).replace("#", " › ")} and ${String(data.sibling).replace("#", " › ")}`;
  const what = isPin
    ? `Asked by the agent${data.asked_at_revn != null ? ` at save ${data.asked_at_revn}` : ""}${data.round ? `, round ${data.round}` : ""}. Answer here, on the canvas, or in chat — no channel is required.`
    : `Fired at save ${data.save}: one side of a declared mapping changed and its sibling didn't. Nothing syncs without your yes.`;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-kind">{isPin ? "❓ open question" : "⚠ mapping tripwire"}</span>
          <button className="modal-close" onClick={onClose} title="close (Esc)">✕</button>
        </div>
        <h2>{title}</h2>
        {anchorLine && <div className="anchor">{anchorLine}</div>}
        <div className="modal-section">
          <h4>What this is</h4>
          <p>{what}</p>
        </div>
        {data.detail && (
          <div className="modal-section">
            <h4>Why it matters</h4>
            {String(data.detail).split(/\n\n+/).map((para: string, i: number) => (
              <p key={i}>{para.split("\n").map((ln: string, j: number) => (
                <React.Fragment key={j}>{j > 0 && <br />}{ln}</React.Fragment>
              ))}</p>
            ))}
          </div>
        )}
        {(data.examples || []).length > 0 && (
          <div className="modal-section">
            <h4>Examples</h4>
            <ul>
              {data.examples.map((x: string, i: number) => <li key={i}>{x}</li>)}
            </ul>
          </div>
        )}
        {data.answer && (
          <div className="modal-section">
            <h4>Your answer</h4>
            <p>↳ {data.answer}</p>
          </div>
        )}
        {onAnswer && (
          <div className="modal-section">
            <h4>Answer in place</h4>
            {(data.choices || []).length > 0 && (
              <div className="choice-row">
                {(data.choices || []).map((c: string) => (
                  <button key={c} onClick={() => onAnswer(c)}>{c}</button>
                ))}
              </div>
            )}
            <div className="answer-row">
              <input
                autoFocus
                placeholder="Answer in your own words…"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && text.trim()) onAnswer(text.trim());
                }}
              />
              <button disabled={!text.trim()}
                onClick={() => onAnswer(text.trim())}>Answer</button>
            </div>
          </div>
        )}
        <div className="modal-foot">
          {onGoto && (
            <button onClick={() => { onGoto(); onClose(); }}>
              ⌖ show on canvas
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
