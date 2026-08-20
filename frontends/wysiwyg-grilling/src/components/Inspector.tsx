import React from "react";
import { isComposedPiece } from "../composed";

/** Element interactivity inspector (v0.3): before this, the only user
 * levers were Excalidraw's native link dialog (with the undiscoverable
 * artifact:<id> syntax) and lock — document/links_to/kind/role were
 * agent-op-only. All edits go into the live scene buffer, so the next
 * Save narrates them like any other user edit. */

/**
 * Kind options for the current artifact type.
 *
 * The vocabulary is the SERVER'S — `kind_vocabulary` off `/api/state`,
 * built from the same constants the composers and lints switch on. The
 * hand-written list that used to live here is exactly the shape of
 * staleness `composed.ts` records next door: thirteen wireframe kinds,
 * no `body`, no `fold`, and every artifact that wasn't a wireframe or a
 * domain — sequences included — handed the flow vocabulary. A selector
 * missing a kind does not merely fail to offer it: the `<select>` finds
 * no matching option, falls back to `— none —`, and tells the user their
 * fold line has no kind while one touch of the control writes
 * `kind: null` over it.
 * @param artifactType The artifact's type ("wireframe" | "domain" | …).
 * @param vocabulary Server-supplied `{artifactType: kinds[]}`.
 * @returns The kind names offered in the selector, in reference order.
 */
function kindsFor(artifactType: string,
  vocabulary: Record<string, string[]>): string[] {
  // extended types (er, class, dfd, …) have no vocabulary of their own
  // and have always been offered the flow arm's
  return vocabulary[artifactType] || vocabulary.flow || [];
}

/**
 * Rail section shown while exactly one editable element is selected.
 * @param root0 The component's props.
 * @param root0.el The live element being inspected.
 * @param root0.artifactType Its artifact's type ("wireframe" | …).
 * @param root0.kindVocabulary Server-supplied `{artifactType: kinds[]}`.
 * @param root0.artifacts Every artifact, `{id: {name}}`, for links-to.
 * @param root0.docs Attachable project_knowledge-relative md paths.
 * @param root0.disabled True while viewing history (time travel).
 * @param root0.onPatch Element fields / customData; a null value in
 * `cdPatch` deletes the key.
 * @param root0.onPin The SAME action the canvas tack runs — routed
 * through one handler so the two surfaces cannot disagree.
 * @param root0.onEditTooltip Opens the anchored tooltip editor.
 * @returns The inspector section, or null for composed/label pieces.
 */
export function Inspector({ el, artifactType, kindVocabulary, artifacts,
  docs, disabled, onPatch, onPin, onEditTooltip }: {
  el: any; artifactType: string;
  kindVocabulary: Record<string, string[]>;
  artifacts: Record<string, any>; docs: string[]; disabled: boolean;
  onPatch: (patch: any, cdPatch?: any) => void;
  onPin: (pinned: boolean) => void;
  onEditTooltip: () => void;
}) {
  const cd = el?.customData || {};
  // composed pieces (attribute rows, glyphs, X-box strokes, labels) are
  // the server's — the owner element is the thing to edit. The rule is
  // shared with the pin surfaces rather than restated: the enumerated
  // `_of` list that used to live here was missing `body_of`, and so was
  // its twin next door.
  if (!el || isComposedPiece(el)) return null;
  const linkTarget = String(el.link || "").startsWith("artifact:")
    ? String(el.link).slice("artifact:".length) : "";
  const isPinOrAnno = cd.role === "pin";
  // A `<select>` whose value matches no option renders as the FIRST
  // option, so any kind the vocabulary doesn't hold would read back as
  // `— none —` and be overwritten by the next touch. The vocabulary can
  // no longer go stale, but an artifact hand-authored or imported with
  // an off-vocabulary kind still can, and the control must not lie about
  // it: carry the stored value as its own option, marked for what it is.
  const kinds = kindsFor(artifactType, kindVocabulary);
  const storedKind = typeof cd.kind === "string" ? cd.kind : "";
  const unknownKind = !!storedKind && !kinds.includes(storedKind);
  return (
    <div className="rail-section inspector">
      <h3>Element <span className="count">{el.id}</span></h3>
      {disabled && <div className="insp-note">read-only while viewing history</div>}
      <fieldset disabled={disabled}>
        <label className="insp-row">
          <span>links to</span>
          <select value={linkTarget}
            onChange={(e) => onPatch({
              link: e.target.value ? `artifact:${e.target.value}` : null,
            })}>
            <option value="">— none —</option>
            {Object.entries(artifacts).map(([aid, a]: any) => (
              <option key={aid} value={aid}>{a.name || aid}</option>
            ))}
          </select>
        </label>
        <label className="insp-row">
          <span>document</span>
          <select value={cd.document || ""}
            onChange={(e) => onPatch({}, { document: e.target.value || null })}>
            <option value="">— none —</option>
            {docs.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        {!isPinOrAnno && (
          <label className="insp-row">
            <span>kind</span>
            <select value={storedKind}
              onChange={(e) => onPatch({}, { kind: e.target.value || null })}>
              <option value="">— none —</option>
              {unknownKind && (
                <option value={storedKind}>
                  {storedKind} — not in this artifact&apos;s vocabulary
                </option>
              )}
              {kinds.map((k) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>
          </label>
        )}
        {!isPinOrAnno && (
          <label className="insp-row">
            <span>role</span>
            <select value={cd.role || "node"}
              onChange={(e) => onPatch({}, { role: e.target.value })}>
              <option value="node">node</option>
              <option value="annotation">annotation</option>
              <option value="decoration">decoration</option>
            </select>
          </label>
        )}
        {/* the same switch as the canvas tack, on purpose. Both write
            Excalidraw's native `locked` and nothing else, so the two
            surfaces cannot get out of step — the checkbox reflects a pin
            made by the tack, the menu, the agent, or a third-party file.
            The old wording ("settled structure") described the agent's
            reason for locking something, which now reads as a different
            feature from the one the tack offers. */}
        <label className="insp-row check">
          <input type="checkbox" checked={!!el.locked}
            onChange={(e) => onPin(e.target.checked)} />
          <span>📌 pinned to canvas (not draggable)</span>
        </label>
        {!!el.locked && (
          <div className="insp-note">a pin stops drags and resizes — not deletes</div>
        )}
        <div className="insp-row">
          <button className="insp-btn" onClick={onEditTooltip}>
            🛈 {cd.tooltip ? "edit tooltip…" : "add tooltip…"}
          </button>
        </div>
        <div className="insp-note">edits land in your next Save — the agent narrates them</div>
      </fieldset>
    </div>
  );
}
