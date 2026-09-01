/**
 * The 🐞 bug-hunter — one click captures a defect where the user sees it.
 *
 * SELECTION FIRST, RING SECOND. Ruled by the user on 2026-08-31 after
 * three shapes were built on the real canvas page (`DECISION-ladybug.md`
 * on `prototype-ladybug`): clicking the ladybug opens the description
 * dialog IMMEDIATELY, already naming whatever was selected, and the ring
 * is offered beside it as `◯ circle it instead`. It cashes in the deleted
 * spike's own finding — "if the user has something selected when hitting
 * the ladybug, the bundle names it, and that is often the whole bug
 * report" — because a ring-first flow taxes every report with a drawing
 * step to capture something the selection usually already states.
 *
 * The ring stays because it is the only way to point at a problem that is
 * not an element: clipped text, two boxes overlapping, a gap. It is never
 * the entry gate, and it is never an element in the artifact scene — the
 * points live here, are posted as coordinates, and the server burns them
 * into its own rendering. A ring drawn into the scene would land in the
 * dirty buffer and be saved as if the user had drawn it.
 *
 * NO PIXELS LEAVE THIS COMPONENT. The 2026-08-13 spike measured a privacy
 * browser (LibreWolf) poisoning `toDataURL` AND `exportToBlob` identically
 * and silently, so every client pixel path returns noise by design. The
 * server renders the drawing from the scene it already holds. What this
 * component sends is the half a server cannot know: the description, the
 * selection, the camera, the open panels, and where the ring went.
 *
 * EVERY STYLE HERE IS INLINE, deliberately. The spike's first catch was
 * its own white-on-white Submit button: its injected CSS lost the
 * specificity war with its own container rule, and this overlay sits over
 * Excalidraw, which ships broad element selectors of its own. An inline
 * style beats every selector short of `!important`, so the dialog cannot
 * be restyled out of legibility by anything the host page grows later.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { apiPost } from "../api";

/** One element the report is about, as the client knows it. */
export type BugTarget = {
  /** Element id — the server re-derives type and bbox from this. */
  id: string;
  /** Element type as the tab sees it; the server's own reading wins. */
  type: string;
  /** Human-readable name: bound label, own text, or the id. */
  label: string;
};

/** Props for {@link BugHunter}. */
export type BugHunterProps = {
  /** Whether the dialog is up. The ladybug button owns this. */
  open: boolean;
  /** Close without filing. Must leave nothing behind. */
  onClose: () => void;
  /** Ref to the live Excalidraw API. */
  apiRef: { current: any };
  /** Ref to the latest `appState` Excalidraw handed `onChange`. */
  appStateRef: { current: any };
  /** Ref to the canvas camera (`scrollX`/`scrollY`/`zoom`). */
  cameraRef: { current: { scrollX: number; scrollY: number; zoom: number } };
  /** Ref to the canvas wrapper, for the scene↔viewport transform origin. */
  wrapRef: { current: HTMLDivElement | null };
  /** Artifact currently on the canvas, or null. */
  artifact: string | null;
  /** Concept that artifact belongs to, or null. */
  concept: string | null;
  /** Which panels/modes are open — a browser-level issue reads differently. */
  panels: Record<string, unknown>;
  /** The `web_bundle` this tab booted with; identifies a stale tab. */
  webBundle: string | null;
  /** Transient message sink — the guidance toast goes here. */
  toast: (text: string) => void;
};

const RING_STROKE = "#cf222e";

const dialogStyle: React.CSSProperties = {
  position: "fixed", zIndex: 10001, right: 24, top: 96, width: 460,
  background: "#ffffff", border: "1px solid #d0d7de", borderRadius: 10,
  boxShadow: "0 18px 50px rgba(0,0,0,.28)", padding: 16,
  font: "13px/1.5 system-ui, sans-serif", color: "#24292f",
  boxSizing: "border-box",
};
const btnStyle: React.CSSProperties = {
  padding: "6px 12px", borderRadius: 6, border: "1px solid #d0d7de",
  background: "#f6f8fa", color: "#24292f", cursor: "pointer",
  font: "13px system-ui", opacity: 1,
};
const primaryStyle: React.CSSProperties = {
  ...btnStyle, background: "#cf222e", borderColor: "#a40e26",
  color: "#ffffff",
};
const areaStyle: React.CSSProperties = {
  width: "100%", boxSizing: "border-box", padding: 8, borderRadius: 6,
  border: "1px solid #d0d7de", font: "13px system-ui", color: "#24292f",
  background: "#ffffff", resize: "vertical",
};

/**
 * Read an element's human-readable name.
 *
 * Prefers the bound label over the container's own text, because a
 * wireframe box's meaning lives in the label attached to it — reporting
 * on `rect-7` when the user selected "Second sign-off" throws away the
 * one thing that made the selection worth capturing.
 *
 * @param el - The selected element.
 * @param all - Every live element, for the bound-label lookup.
 * @returns A short single-line name, falling back to the element id.
 */
function labelOf(el: any, all: any[]): string {
  const bound = all.find((e) => e.containerId === el.id && e.type === "text");
  const text = String(bound?.text ?? el?.text ?? "").replace(/\s+/g, " ").trim();
  return text ? text.slice(0, 48) : String(el?.id ?? "");
}

/**
 * The 🐞 dialog and its optional focus ring.
 *
 * @param props - See {@link BugHunterProps}.
 * @returns The dialog, the ring overlay, or nothing when closed.
 */
export function BugHunter({
  open, onClose, apiRef, appStateRef, cameraRef, wrapRef,
  artifact, concept, panels, webBundle, toast,
}: BugHunterProps) {
  const [targets, setTargets] = useState<BugTarget[]>([]);
  const [desc, setDesc] = useState("");
  const [ringMode, setRingMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Scene and viewport coordinates are both kept, and each is converted
  // AS THE POINT IS DRAWN rather than at submit: converting later would
  // read a camera the user may have moved, and the two arrays would then
  // disagree about the same stroke with nothing saying which was right.
  const sceneRef = useRef<number[][]>([]);
  const [viewPts, setViewPts] = useState<number[][]>([]);
  const drawing = useRef(false);

  /** Read the current selection into reportable targets. */
  const readSelection = useCallback((): BugTarget[] => {
    const api = apiRef.current;
    if (!api) return [];
    const sel = appStateRef.current?.selectedElementIds || {};
    const ids = new Set(Object.keys(sel).filter((k) => sel[k]));
    const all = (api.getSceneElements?.() || []).filter((e: any) => !e.isDeleted);
    return all
      .filter((e: any) => ids.has(e.id))
      .map((e: any) => ({ id: e.id, type: e.type, label: labelOf(e, all) }));
  }, [apiRef, appStateRef]);

  // The selection is captured ON OPEN and then held. It is not re-read
  // while the dialog is up: clicking into the textarea or the ring
  // overlay can clear Excalidraw's selection, and a report that quietly
  // lost the thing it named between opening and filing is worse than one
  // that never named it.
  useEffect(() => {
    if (!open) return;
    setTargets(readSelection());
    setDesc("");
    setError(null);
    setRingMode(false);
    setViewPts([]);
    sceneRef.current = [];
  }, [open, readSelection]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Escape backs out of the RING first and the dialog second. One
      // Escape that threw away a half-written description because the
      // user was leaving ring mode is a data-loss bug in a tool whose
      // whole job is not losing what the user saw.
      if (ringMode) { setRingMode(false); setViewPts([]); sceneRef.current = []; }
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, ringMode, onClose]);

  if (!open) return null;

  /**
   * Convert a viewport point to scene coordinates.
   *
   * The inverse of the app's own `screenPt`, plus the wrapper's offset:
   * the overlay is `position: fixed` so its pointer events carry
   * viewport coordinates, while the canvas origin sits below the header
   * and whatever banners are up. Verified against the one surviving
   * 2026-08-21 bundle, whose `ring_viewport[0]` of `[717, 468]` maps to
   * its recorded `ring_scene[0]` of `[413.66, 314.62]` under
   * `offsetTop: 46`, `zoom: 1.3`, `scroll: (137.88, 10)`.
   *
   * @param clientX - Pointer X in viewport pixels.
   * @param clientY - Pointer Y in viewport pixels.
   * @returns The `[x, y]` scene point the server will draw at.
   */
  const toScene = (clientX: number, clientY: number): number[] => {
    const cam = cameraRef.current || { scrollX: 0, scrollY: 0, zoom: 1 };
    const r = wrapRef.current?.getBoundingClientRect();
    const zoom = cam.zoom || 1;
    return [
      (clientX - (r?.left ?? 0)) / zoom - cam.scrollX,
      (clientY - (r?.top ?? 0)) / zoom - cam.scrollY,
    ];
  };

  /**
   * Bound a stroke by its own points.
   *
   * @param pts - The stroke's `[x, y]` pairs, in either space.
   * @returns The bounding box, or null when a stroke of fewer than two
   *   points was handed in — the server derives its own from the scene
   *   coordinates either way, so this one is only the record of what the
   *   client believed.
   */
  const bboxOf = (pts: number[][]) => {
    if (pts.length < 2) return null;
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    return {
      x: Math.min(...xs), y: Math.min(...ys),
      w: Math.max(...xs) - Math.min(...xs),
      h: Math.max(...ys) - Math.min(...ys),
    };
  };

  const enterRing = () => {
    setRingMode(true);
    setViewPts([]);
    sceneRef.current = [];
    // Requirement 6 of the spike's eleven: the toast NAMES THE WAY OUT.
    // A modal overlay with a crosshair and no stated exit is a trap, and
    // the first thing a user does in one is reload the page — losing the
    // description they had already written.
    toast("🐞 Drag a circle around the problem area · Esc goes back to the description");
  };

  const submit = async () => {
    if (!desc.trim() || busy) return;
    setBusy(true);
    setError(null);
    const cam = cameraRef.current || { scrollX: 0, scrollY: 0, zoom: 1 };
    const r = wrapRef.current?.getBoundingClientRect();
    try {
      const resp = await apiPost("/api/bugreport", {
        description: desc,
        artifact, concept,
        selection: targets,
        ring: sceneRef.current.length >= 2 ? {
          points_scene: sceneRef.current,
          points_viewport: viewPts,
          bbox_viewport: bboxOf(viewPts),
        } : null,
        viewport: {
          innerWidth: window.innerWidth, innerHeight: window.innerHeight,
          devicePixelRatio: window.devicePixelRatio ?? 1,
        },
        scene_view: {
          scrollX: cam.scrollX, scrollY: cam.scrollY, zoom: cam.zoom,
          offsetLeft: Math.round(r?.left ?? 0),
          offsetTop: Math.round(r?.top ?? 0),
        },
        panels,
        url: window.location.href,
        user_agent: navigator.userAgent,
        client_bundle: webBundle,
        captured_at: new Date().toISOString(),
      });
      onClose();
      // The capture's own verdict is said OUT LOUD to the person who
      // filed it. A bundle that quietly recorded `snapshot_suspect` and
      // told nobody is the silence-reads-as-health failure this feature
      // exists to catch, reproduced inside the catcher.
      toast(resp.snapshot_suspect
        ? `🐞 Filed report #${resp.id} — but the capture looks incomplete; the description is carrying it`
        : `🐞 Filed report #${resp.id} — the agent is told`);
    } catch (e: any) {
      // The dialog STAYS OPEN and keeps the text. Losing a written
      // report to a failed POST is the one failure this component must
      // not have: the user cannot re-observe what they just saw.
      setError(e?.message || "the report could not be filed");
      setBusy(false);
    }
  };

  if (ringMode) {
    return (
      <div
        data-testid="bug-ring-overlay"
        onPointerDown={(e) => {
          drawing.current = true;
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          sceneRef.current = [toScene(e.clientX, e.clientY)];
          setViewPts([[e.clientX, e.clientY]]);
        }}
        onPointerMove={(e) => {
          if (!drawing.current) return;
          sceneRef.current = [...sceneRef.current, toScene(e.clientX, e.clientY)];
          setViewPts((p) => [...p, [e.clientX, e.clientY]]);
        }}
        onPointerUp={() => {
          if (!drawing.current) return;
          drawing.current = false;
          setRingMode(false);
        }}
        style={{
          position: "fixed", inset: 0, zIndex: 10000, cursor: "crosshair",
          background: "rgba(15,17,21,.30)", touchAction: "none",
        }}
      >
        <svg style={{
          position: "absolute", inset: 0, width: "100%", height: "100%",
          pointerEvents: "none",
        }}>
          {viewPts.length > 1 && (
            <polyline points={viewPts.map((p) => p.join(",")).join(" ")}
              fill="none" stroke={RING_STROKE} strokeWidth={3}
              strokeLinecap="round" strokeLinejoin="round" />
          )}
        </svg>
        <div style={{
          position: "absolute", top: 22, left: "50%",
          transform: "translateX(-50%)", background: "#24292f",
          color: "#ffffff", padding: "9px 16px", borderRadius: 20,
          font: "13px system-ui", boxShadow: "0 6px 20px rgba(0,0,0,.3)",
        }}>
          🐞 Drag a circle around the problem area
          <span style={{ opacity: 0.7, marginLeft: 10 }}>
            or{" "}
            <button type="button" data-testid="bug-ring-skip"
              onClick={() => { setRingMode(false); setViewPts([]); sceneRef.current = []; }}
              style={{
                background: "none", border: "none", padding: 0,
                color: "#ffffff", textDecoration: "underline",
                cursor: "pointer", font: "13px system-ui",
              }}>skip it</button>
            {" "}· Esc goes back to the description
          </span>
        </div>
      </div>
    );
  }

  const named = targets.map((t) => t.label || t.id).join(", ");
  return (
    <div style={dialogStyle} data-testid="bug-dialog" role="dialog"
      aria-label="Report a problem">
      <div style={{ fontWeight: 600, marginBottom: 8 }}>🐞 Report a problem</div>
      <div data-testid="bug-targets" style={{
        margin: "0 0 10px", padding: "7px 9px", borderRadius: 6,
        fontSize: 12, color: "#24292f",
        background: targets.length ? "#eef6ff" : "#f6f8fa",
        border: `1px solid ${targets.length ? "#b6d9ff" : "#e3e6ea"}`,
      }}>
        {targets.length ? (
          <>
            <b>Reporting on {targets.length === 1 ? "" : `${targets.length} things`}
              {targets.length === 1 ? "" : ": "}</b>
            {targets.length === 1 ? <b>{named}</b> : named}
            <span style={{ opacity: 0.65 }}> — captured automatically</span>
          </>
        ) : (
          <>
            <b>Nothing selected.</b>
            <span style={{ opacity: 0.75 }}>
              {" "}This reports the whole view. Cancel, click the thing that is
              wrong, and press 🐞 again if you meant something specific.
            </span>
          </>
        )}
      </div>
      <textarea data-testid="bug-desc" style={areaStyle} rows={5} autoFocus
        value={desc} onChange={(e) => setDesc(e.target.value)}
        placeholder="What's wrong? What did you expect instead?" />
      <div style={{ marginTop: 8 }}>
        <button type="button" data-testid="bug-ring" style={btnStyle}
          onClick={enterRing}>
          {sceneRef.current.length >= 2 ? "✓ ring drawn — redraw" : "◯ circle it instead"}
        </button>
        {sceneRef.current.length >= 2 && (
          <span style={{ marginLeft: 8, fontSize: 12, opacity: 0.7 }}>
            the ring is an overlay — it is never added to your drawing
          </span>
        )}
      </div>
      {/* Requirement 8 of the eleven, stated where the user is writing
          rather than in a doc they will not read: the capture is the
          drawing plus app state, NOT a screenshot of the browser. A
          report about the rail, a menu, or a font has to say so in
          words, because nothing in the bundle will show it. */}
      <div style={{ marginTop: 10, fontSize: 11, lineHeight: 1.45, opacity: 0.7 }}>
        Captured with this: the drawing (redrawn by the server, not a
        screenshot), what you have selected, the camera, and the session
        state. It is <b>not</b> a picture of the browser — if the problem
        is in a panel, a menu or the text around the canvas, describe it
        above.
      </div>
      {error && (
        <div data-testid="bug-error" style={{
          marginTop: 10, padding: "7px 9px", borderRadius: 6, fontSize: 12,
          background: "#fff1f0", border: "1px solid #f3b3ad", color: "#7d1a12",
        }}>
          {error} — your text is still here; try again.
        </div>
      )}
      <div style={{
        display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end",
      }}>
        <button type="button" data-testid="bug-cancel" style={btnStyle}
          onClick={onClose}>Cancel</button>
        <button type="button" data-testid="bug-submit"
          style={{
            ...primaryStyle,
            opacity: desc.trim() && !busy ? 1 : 0.5,
            cursor: desc.trim() && !busy ? "pointer" : "not-allowed",
          }}
          disabled={!desc.trim() || busy} onClick={submit}>
          {busy ? "Filing…" : "File it"}
        </button>
      </div>
    </div>
  );
}
