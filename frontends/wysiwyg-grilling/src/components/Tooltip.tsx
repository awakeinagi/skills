import React, { useState } from "react";
import { renderMd } from "./DocReader";

/** Element tooltips (v0.3): hover-only markdown detail stored in
 * customData.tooltip. The hover card and the right-click editor live
 * here; hit-testing and the context menu shell live in App (they need
 * the Excalidraw api + camera refs). */

/**
 * The hover card: rendered markdown at a fixed wrap position.
 * @param root0 Props: `x`/`y` (wrap px), `text` (markdown).
 * @returns The tooltip card.
 */
export function TooltipCard({ x, y, text }: { x: number; y: number; text: string }) {
  return (
    <div className="tip-card" style={{ left: x, top: y }}>
      {renderMd(text)}
    </div>
  );
}

/**
 * Markdown editor for a tooltip: free-text box with Save / Preview /
 * Discard changes (render inside AnchoredPopover).
 * @param root0 Props: `initial` (current markdown, "" when adding),
 * `onSave(text)` (empty string = remove), `onClose`.
 * @returns The editor body.
 */
export function TooltipEditor({ initial, onSave, onClose }: {
  initial: string; onSave: (text: string) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [preview, setPreview] = useState(false);
  return (
    <div className="tip-editor">
      <div className="aq-head">
        <span className="aq-kind">🛈 tooltip (markdown)</span>
        <button className="modal-close" onClick={onClose} title="discard changes">✕</button>
      </div>
      {preview ? (
        <div className="tip-preview">
          {draft.trim() ? renderMd(draft) : <em>(empty — Save removes the tooltip)</em>}
        </div>
      ) : (
        <textarea
          autoFocus
          rows={6}
          placeholder={"Shown on hover. Markdown works:\n**bold**, *italic*, `code`, lists, tables."}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      )}
      <div className="actions">
        <button className="ghost" onClick={() => setPreview((p) => !p)}>
          {preview ? "✎ edit" : "👁 preview"}
        </button>
        <button className="ghost" onClick={onClose}>Discard changes</button>
        <button className="primary" onClick={() => { onSave(draft.trim()); onClose(); }}>
          Save
        </button>
      </div>
    </div>
  );
}
