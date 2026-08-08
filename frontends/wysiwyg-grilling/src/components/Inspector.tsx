import React from "react";

/** Element interactivity inspector (v0.3): before this, the only user
 * levers were Excalidraw's native link dialog (with the undiscoverable
 * artifact:<id> syntax) and lock — document/links_to/kind/role were
 * agent-op-only. All edits go into the live scene buffer, so the next
 * Save narrates them like any other user edit. */

const WIREFRAME_KINDS = ["block", "button", "nav", "input", "image",
  "kpi", "checkbox", "toggle", "slider", "priority", "help",
  "sticky-bar", "feedback"];
const FLOW_KINDS = ["source", "transform", "agent", "control", "sink",
  "store"];
const DOMAIN_KINDS = ["entity"];

/**
 * Kind options for the current artifact type.
 * @param artifactType The artifact's type ("wireframe" | "domain" | …).
 * @returns The kind names offered in the selector.
 */
function kindsFor(artifactType: string): string[] {
  if (artifactType === "wireframe") return WIREFRAME_KINDS;
  if (artifactType === "domain") return DOMAIN_KINDS;
  return FLOW_KINDS;
}

/**
 * Rail section shown while exactly one editable element is selected.
 * @param root0 Props: `el` (the live element), `artifactType`,
 * `artifacts` ({id: {name}}), `docs` (project_knowledge-relative md
 * paths), `disabled` (time travel), `onPatch(patch, cdPatch)` (element
 * fields / customData; null value in cdPatch deletes the key),
 * `onEditTooltip` (opens the anchored editor).
 * @returns The inspector section, or null for composed/label pieces.
 */
export function Inspector({ el, artifactType, artifacts, docs, disabled,
  onPatch, onEditTooltip }: {
  el: any; artifactType: string;
  artifacts: Record<string, any>; docs: string[]; disabled: boolean;
  onPatch: (patch: any, cdPatch?: any) => void;
  onEditTooltip: () => void;
}) {
  const cd = el?.customData || {};
  // composed pieces (attribute rows, glyphs, X-box strokes, labels) are
  // the server's — the owner element is the thing to edit
  const composed = cd.role === "label" || cd.role === "decoration" ||
    cd.attr_of || cd.x_of || cd.value_of || cd.box_of || cd.chk_of ||
    cd.thumb_of || cd.track_of;
  if (!el || composed) return null;
  const linkTarget = String(el.link || "").startsWith("artifact:")
    ? String(el.link).slice("artifact:".length) : "";
  const isPinOrAnno = cd.role === "pin";
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
            <select value={cd.kind || ""}
              onChange={(e) => onPatch({}, { kind: e.target.value || null })}>
              <option value="">— none —</option>
              {kindsFor(artifactType).map((k) => (
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
        <label className="insp-row check">
          <input type="checkbox" checked={!!el.locked}
            onChange={(e) => onPatch({ locked: e.target.checked })} />
          <span>locked (settled structure — not draggable)</span>
        </label>
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
