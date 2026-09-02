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
  kind, data, onClose, onGoto, onAnswer, onPromote, onDiscardProposal, debt,
}: {
  kind: "pin" | "tripwire"; data: any;
  onClose: () => void; onGoto: (() => void) | null;
  onAnswer?: ((answer: string) => void) | null;
  /**
   * Accept a waiting agent draft — the argument is the wording being
   * accepted, blank for "as written". The agent cannot reach this.
   */
  onPromote?: ((answer: string) => void) | null;
  /** Throw the waiting draft away and leave the question open. */
  onDiscardProposal?: (() => void) | null;
  debt?: any;
}) {
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<string | null>(null);
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
  // Age, not just origin (v0.8/B1): "asked at round 1" made the USER
  // subtract to learn what the agent was told outright ("age 5r"). And
  // a user-authored pin is not "asked by the agent".
  const asker = data.direction === "user" ? "Asked by you"
    : "Asked by the agent";
  const age = isPin && debt && debt.age_rounds > 0
    ? ` Open for ${debt.age_rounds} round${debt.age_rounds === 1 ? "" : "s"}` +
      (debt.target_edits > 0
        ? `; its target has been edited ${debt.target_edits}× since.` : ".")
    : "";
  const what = isPin
    ? `${asker}${data.asked_at_revn != null ? ` at save ${data.asked_at_revn}` : ""}${data.round ? `, round ${data.round}` : ""}.${age} Answer here, on the canvas, or in chat — no channel is required.`
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
        {/* A draft the agent left, and the three things the user can do
            with it. It sits ABOVE the answer box for the reason the rail
            orders it the same way: accepting a draft and writing an
            answer are two different acts and must not read as one. */}
        {onAnswer && data.proposed_answer && (
          <div className="modal-section proposal">
            <h4>The agent drafted an answer for you</h4>
            {draft === null ? (
              <p className="proposal-text">“{data.proposed_answer}”</p>
            ) : (
              <textarea className="proposal-edit" autoFocus value={draft}
                onChange={(e) => setDraft(e.target.value)} />
            )}
            <p className="dim">This is the agent's wording, not your
              answer. Nothing is recorded as yours until you accept it.</p>
            {onPromote && (
              <div className="proposal-row">
                <button disabled={draft !== null && !draft.trim()}
                  onClick={() => onPromote(draft === null ? "" : draft.trim())}>
                  {draft === null ? "Accept as written" : "Accept my wording"}
                </button>
                {draft === null
                  ? <button className="linkish"
                      onClick={() => setDraft(data.proposed_answer)}>
                      ✎ edit first
                    </button>
                  : <button className="linkish"
                      onClick={() => setDraft(null)}>cancel edit</button>}
                {onDiscardProposal && (
                  <button className="linkish dismiss"
                    title="throw the draft away — the question stays open"
                    onClick={onDiscardProposal}>✕ discard draft</button>
                )}
              </div>
            )}
          </div>
        )}
        {data.answer && (
          <div className="modal-section">
            <h4>{data.answer_source === "agent-proposal"
              ? "Your answer, from the agent's draft" : "Your answer"}</h4>
            <p>↳ {data.answer}</p>
            {data.answer_source === "agent-proposal" && (
              <p className="dim">{data.answer === data.proposed_answer
                ? "You accepted the agent's wording as written — the ruling is yours, the sentence was its."
                : `You rewrote the agent's draft before accepting it. It had offered “${data.proposed_answer}”.`}</p>
            )}
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
