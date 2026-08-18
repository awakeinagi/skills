import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Excalidraw, exportToBlob, restoreElements, CaptureUpdateAction,
} from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { apiGet, apiPost, fingerprint, replayChanges } from "./api";
import { Rail } from "./components/Rail";
import { HistoryGraph } from "./components/HistoryGraph";
import { SceneThumb } from "./components/Thumb";
import { QuestionModal, gotoRefOf } from "./components/QuestionUI";
import { DocReader } from "./components/DocReader";
import { AnchoredPopover, AnchoredQuestion, anchorStyle } from "./components/AnchoredPopover";
import { TooltipCard, TooltipEditor } from "./components/Tooltip";
import { Inspector } from "./components/Inspector";
import { libraryItems, TEMPLATES, templateElements } from "./library";

const POLL_MS = 2500;

let toastSeq = 0;

/** Describe a held revision's ops in a few words.
 *
 * "Apply now" used to be a blind click: the banner named the agent's
 * note but never what the revision would actually do to the drawing.
 *
 * @param ops - The queued batch's op list, as sent by the server.
 * @returns A short phrase like "adds 3, rewires 1", or "no drawing ops".
 */
function opSummary(ops: unknown): string {
  const list = Array.isArray(ops) ? ops : [];
  const verbs: Record<string, string> = {
    add: "adds", mod: "changes", del: "deletes", reorder: "reorders",
    pin: "asks", resolve_pin: "closes", registry: "records",
  };
  const counts = new Map<string, number>();
  for (const o of list) {
    const kind = verbs[String((o as { op?: string })?.op ?? "")] ?? "other";
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  if (!counts.size) return "no drawing ops";
  return [...counts].map(([verb, n]) => `${verb} ${n}`).join(", ");
}

/** One element of the onion skin. Deliberately schematic: the point is
 * "something used to be here, this big" — a faithful re-render would
 * compete with the live drawing instead of annotating it. */
function ghostShape(e: any) {
  const w = e.width || 0, h = e.height || 0;
  if (e.type === "ellipse")
    return <ellipse key={e.id} cx={e.x + w / 2} cy={e.y + h / 2} rx={w / 2} ry={h / 2} />;
  if (e.type === "diamond") {
    const cx = e.x + w / 2, cy = e.y + h / 2;
    return <polygon key={e.id}
      points={`${cx},${e.y} ${e.x + w},${cy} ${cx},${e.y + h} ${e.x},${cy}`} />;
  }
  if ((e.type === "arrow" || e.type === "line" || e.type === "freedraw") &&
      e.points?.length >= 2)
    return <polyline key={e.id}
      points={e.points.map((p: number[]) => `${e.x + p[0]},${e.y + p[1]}`).join(" ")} />;
  if (e.type === "text")
    return <text key={e.id} x={e.x} y={e.y + (e.fontSize || 14) * 0.95}
      fontSize={e.fontSize || 14}>{String(e.text || "").split("\n")[0]}</text>;
  return <rect key={e.id} x={e.x} y={e.y} width={w} height={h} />;
}

/** Restore stored elements the way the EDITOR does.
 *
 * `refreshDimensions` is what re-wraps container-bound text — and it only
 * runs inside restore's `repairBindings` block, so the two must travel
 * together. The server stores label text unwrapped on purpose
 * (`fit_label_in` sizes the box for the wrapped line count and sets
 * `autoResize: false` so the client wraps inside it), which means anything
 * that renders stored elements WITHOUT these options draws one long line.
 *
 * The agent's own `snapshot` did exactly that, so the PNG it took of its
 * work disagreed with the canvas the user was reading, and with the
 * server's SVG, which wraps by a completely separate path. A cold
 * observer handed those PNGs reported broken text in 4 of 6 artifacts,
 * none of it in the drawings (v0.6 assessment r3-4).
 */
const restoreForRender = (els: any[]) =>
  restoreElements(els || [], null, {
    refreshDimensions: true, repairBindings: true,
  } as any);

/** Clear air between an imported diagram and the drawing already there. */
const IMPORT_GAP = 120;

/** How far one imported node may grow to save a chopped word, in px.
 *
 * Mermaid's own default node separation, so a repair can spend the gap
 * mermaid left between two nodes and never more than it — the widening
 * cannot walk one box into its neighbour. Where a word still does not fit
 * inside that, the label is left as the converter made it: the picture is
 * no worse, and a node overlapping another is not a better answer.
 */
const IMPORT_WIDEN_CAP = 50;

/** The font the import asks mermaid for, and the floor a repair may take
 * one chopped label down to. Shrinking is the second lever and it is only
 * ever spent on the labels that are still broken after the box has grown
 * as far as it may: text a size or two down reads; text with a word cut in
 * half does not, at any size. */
const IMPORT_FONT_SIZE = 20;
const IMPORT_FONT_FLOOR = 12;

/** Did the converter break a WORD to make this label fit its box?
 *
 * Excalidraw re-measures a bound label in its own font and wraps it to the
 * container's width, and where a single word is wider than that width it
 * breaks the word itself. Mermaid sized the box in a different font, so
 * `b{different?}` arrived as a 143px diamond holding `differe\nnt?` — the
 * CLI path fed the same text produces a clean label (v0.9 WP4, `r5-10`).
 *
 * Joining the lines with spaces reconstructs `originalText` exactly when
 * every break is at a space, and cannot when one is not, which is the whole
 * test — no font metrics, no threshold.
 */
const choppedAWord = (e: any) =>
  e.type === "text" && e.containerId &&
  typeof e.text === "string" && e.text.includes("\n") &&
  e.text.split("\n").join(" ") !== (e.originalText || e.text);

/**
 * Registration promise for mermaid's ELK layouts — created once, awaited by
 * every conversion. `null` until the first conversion asks for it.
 */
let elkLayoutRegistration: Promise<void> | null = null;

/** Make `layout: elk` mean something, once per page load.
 *
 * Mermaid ships a registry with exactly three layouts — `dagre`, `swimlane`
 * and `cose-bilkent` — and `getRegisteredLayoutAlgorithm` FAILS OPEN: an
 * unregistered name falls back to dagre behind a `log.warn` that mermaid's
 * default log level swallows. So before this, a diagram asking for
 * `layout: elk` got dagre's positions with no error and no note, which is
 * indistinguishable from being honoured. ELK is a separate package; loading
 * it and registering its loaders is what turns that silent fallback into a
 * real choice.
 *
 * Deliberately lazy and deliberately shared. Lazy because ELK is ~460KB
 * gzipped and most sessions never write a mermaid diagram at all — it stays
 * out of the initial bundle and loads on the first conversion. Shared
 * because both conversion paths (the agent's seed servicing and the user's
 * paste) go through the same mermaid module instance, so registering twice
 * would be wasted work rather than a second registry.
 *
 * A failure here is swallowed on purpose: ELK is an enhancement, and a
 * diagram that would have converted under dagre must still convert if the
 * chunk fails to load. The promise is left resolved rather than reset so a
 * broken load costs one attempt, not one per conversion.
 * @returns A promise that settles once ELK is registered, or has failed to.
 */
const ensureElkLayouts = (): Promise<void> => {
  if (!elkLayoutRegistration) {
    elkLayoutRegistration = (async () => {
      const [mermaidMod, elkMod] = await Promise.all([
        import("mermaid"),
        import("@mermaid-js/layout-elk"),
      ]);
      mermaidMod.default.registerLayoutLoaders(elkMod.default);
    })().catch((e: unknown) => {
      // eslint-disable-next-line no-console
      console.warn("ELK layouts unavailable; mermaid stays on dagre", e);
    });
  }
  return elkLayoutRegistration;
};

/** Convert mermaid skeletons, repairing any box that chopped a word.
 *
 * Convert, ask which labels came back with a word cut in half, adjust only
 * those skeletons, convert again — so nothing here has to know how wide a
 * glyph is; the converter's own wrapper answers that on the next pass. Each
 * broken label gets the box widened once (by `IMPORT_WIDEN_CAP`, no more,
 * so the growth stays inside the gap mermaid left) and then, if it is still
 * chopped, a smaller font one step at a time down to `IMPORT_FONT_FLOOR`.
 * Width first because a bigger box is what the user would have drawn; font
 * second because it costs no geometry at all and cannot collide.
 *
 * The adjustments are keyed on the LABEL TEXT, not the skeleton id: the
 * converter is free to mint its own ids, and a repair that silently matches
 * nothing is worse than no repair because it looks like one. Two nodes
 * carrying the same label are adjusted together, which is right — the same
 * text in the same size box breaks the same way.
 *
 * The loop is bounded by the font floor and returns the last conversion
 * either way: where a word fits in no box mermaid's gaps can afford, the
 * widest box at the smallest font is still the least bad picture.
 * @param skeletons - Skeletons from `parseMermaidToExcalidraw`.
 * @param convert - `convertToExcalidrawElements`.
 * @returns Converted elements.
 */
const widenChoppedContainers = (skeletons: any[], convert: any): any[] => {
  const base = JSON.stringify(skeletons);
  const grown = new Set<string>();
  const shrunk = new Map<string, number>();
  const build = () => convert(JSON.parse(base).map((s: any) => {
    const text = s.label?.text;
    if (text === undefined) return s;
    const out = { ...s, label: { ...s.label } };
    if (grown.has(text) && out.width) out.width += IMPORT_WIDEN_CAP;
    const size = shrunk.get(text);
    if (size) out.label.fontSize = size;
    return out;
  }));
  let converted = build();
  const steps = 1 + Math.ceil(
    (IMPORT_FONT_SIZE - IMPORT_FONT_FLOOR) / 2);
  for (let pass = 0; pass < steps; pass++) {
    const bad: string[] = converted.filter(choppedAWord)
      .map((e: any) => e.originalText as string);
    if (!bad.length) break;
    for (const text of bad) {
      if (!grown.has(text)) { grown.add(text); continue; }
      shrunk.set(text, Math.max(
        IMPORT_FONT_FLOOR, (shrunk.get(text) ?? IMPORT_FONT_SIZE) - 2));
    }
    converted = build();
  }
  return converted;
};

/** Where a marker sits so it hugs a shape's bottom-right EDGE.
 *
 * A rectangle fills its bounding box; a diamond and an ellipse do not, so
 * the box corner is empty canvas for them and a marker anchored there
 * floats clear and reads as a stray mark. v0.6 fixed this for the tooltip
 * dot and nobody grepped for the shape, so it was still live in the pin
 * seeder AND in the tripwire mark below (v0.6 assessment r3-1).
 *
 * Mirrors `marker_anchor` in canvas.py — stored geometry and canvas overlay
 * cannot share code, so they share this rule.
 */
const markerAnchor = (e: any, dx = 0, dy = 0, corner: "br" | "tr" = "br") => {
  const w = e.width || 0, h = e.height || 0;
  const inset = e.type === "diamond" ? 0.5
    : e.type === "ellipse" ? 1 - Math.SQRT1_2 : 0;
  return {
    x: e.x + w - (w * inset) / 2 + dx,
    y: corner === "tr" ? e.y + (h * inset) / 2 + dy : e.y + h - (h * inset) / 2 + dy,
  };
};

/** A rectangle in scene coordinates. */
type Rect = { x: number; y: number; w: number; h: number };

/** A point in scene coordinates. */
type Pt = { x: number; y: number };

/** The parts of a scene element that decide where an insert may land. */
type SceneEl = {
  id?: string; type?: string; x: number; y: number;
  width?: number; height?: number; points?: number[][];
  isDeleted?: boolean; customData?: { role?: string } | null;
};

/** Scene px kept between a fresh insert and whatever it lands beside. */
const DROP_GAP = 24;

/** How many obstacles one direction of the nudge search will step past. */
const DROP_HOPS = 12;

/** The sticky note's box — the label is inset 8px inside it. */
const NOTE_W = 180, NOTE_H = 90;

/**
 * A live element's occupied box.
 *
 * Arrows, lines and freedraw are bounded by their POINTS, which run from
 * `x`/`y` and may go LEFT of or ABOVE it. This used to union the point hull
 * with the stored box — `Math.max(e.width || 0, ...xs)` — which maximises a
 * stored MAGNITUDE against a point COORDINATE. They coincide for a rightward
 * arrow and diverge for every leftward or upward one, where the stored width
 * wins and the box grows a phantom half on the side the arrow never reaches:
 * 63 of the frozen corpus's 194 point-strung elements overhung their own ink
 * by more than `LANE_TOL`, the worst by 600px — a box exactly twice its
 * stroke (`r-held-vets`, w 1200 against 600px of ink). `dropClear` reads
 * these as obstacles, so an insert was pushed out of canvas that is clear
 * (curator batch 30 item 5, measured; v0.9 TASK-ELBOX).
 *
 * `canvas.py`'s `ink_extent` answers the same question on the other side of
 * the wire and this mirrors it deliberately, down to taking the hull raw
 * rather than unioned with the element's own origin — *"what a polyline
 * paints is its points, and its stored box is a summary of them that this
 * loop has never been entitled to widen by"*. ONE DIVERGENCE, stated: a
 * point-strung element carrying NO points falls back to its stored box here
 * where `ink_extent` reads a zero-size box at `x`/`y`. Excalidraw never
 * writes one, and for a CLEARANCE test the stored box is the safe direction
 * to be wrong in, where a 0x0 obstacle is not.
 * @param e Any live scene element.
 * @returns The element's occupied rectangle in scene coords.
 */
const elBox = (e: SceneEl): Rect => {
  const pts: number[][] = Array.isArray(e.points) ? e.points : [];
  if (!["arrow", "line", "freedraw"].includes(e.type || "") || !pts.length)
    return { x: e.x, y: e.y, w: e.width || 0, h: e.height || 0 };
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys);
  return { x: e.x + x0, y: e.y + y0,
           w: Math.max(...xs) - x0, h: Math.max(...ys) - y0 };
};

/** What the app knows about where the canvas is looking. */
type Camera = { scrollX: number; scrollY: number; zoom: number };

/**
 * Scene point → pixels inside `.canvas-wrap`.
 *
 * The one place this arithmetic lives. It was open-coded at four sites
 * (leader lines, the onion skin's transform, the tripwire mark, the
 * tooltip dot) before the tack badge needed a fifth, and an overlay whose
 * position is re-derived by hand is an overlay that drifts from the
 * drawing it annotates the next time the camera model changes.
 * @param p A point in scene coordinates.
 * @param cam The live camera.
 * @returns Absolute `left`/`top` for an element positioned in `.canvas-wrap`.
 */
const screenPt = (p: Pt, cam: Camera) => ({
  left: (p.x + cam.scrollX) * cam.zoom,
  top: (p.y + cam.scrollY) * cam.zoom,
});

/**
 * Scene rect → pixel rect inside `.canvas-wrap`.
 *
 * Sizes scale with zoom and offsets do not, which is exactly the trap a
 * hand-rolled conversion falls into: `screenPt` alone cannot place a
 * marker on a box's far corner without the caller multiplying the extent
 * by the zoom itself.
 * @param r A rectangle in scene coordinates.
 * @param cam The live camera.
 * @returns The same rectangle in `.canvas-wrap` pixels.
 */
const screenBox = (r: Rect, cam: Camera) => ({
  ...screenPt({ x: r.x, y: r.y }, cam),
  width: r.w * cam.zoom, height: r.h * cam.zoom,
});

/**
 * The smallest scene rect containing every element given.
 *
 * Bounds come from `elBox`, so a point-strung element contributes the hull
 * of its POINTS and never its stored width — the same rule that keeps a
 * leftward arrow from claiming canvas it never paints.
 * @param els The elements to bound.
 * @returns The union rectangle, or null when the list is empty.
 */
const unionBox = (els: SceneEl[]): Rect | null => {
  if (!els.length) return null;
  const bs = els.map(elBox);
  const x = Math.min(...bs.map((b) => b.x));
  const y = Math.min(...bs.map((b) => b.y));
  return { x, y,
           w: Math.max(...bs.map((b) => b.x + b.w)) - x,
           h: Math.max(...bs.map((b) => b.y + b.h)) - y };
};

/** Where the "always show pin icon" preference persists. */
const PIN_ALWAYS_KEY = "wysiwyg-pin-always";

/** What the pin rules need to read off an element. */
type PinEl = Omit<SceneEl, "customData"> & {
  locked?: boolean; containerId?: string | null;
  customData?: Record<string, unknown> | null };

/**
 * Is this element one "Pin ALL" should target?
 *
 * Only OWNERS. A bound label, the ❓ glyph, a composed attribute row and a
 * checkbox's tick do not have positions of their own — they are placed
 * relative to the thing they belong to, so pinning one is pinning the same
 * fact twice and leaves a pin the user cannot see the point of. On a real
 * corpus that is 469 of 976 elements, half the drawing, badged for nothing.
 * They follow their host, which "Pin ALL" does pin.
 *
 * A deliberate pin ON a label by hand is still honoured — this predicate
 * scopes the bulk gesture, not the flag.
 * @param e A live scene element.
 * @returns True when "Pin ALL" should pin it.
 */
const pinnable = (e: PinEl) => {
  if (e.isDeleted) return false;
  const cd: Record<string, unknown> = e.customData || {};
  if (["label", "decoration", "pin"].includes(String(cd.role || ""))) return false;
  return !e.containerId && !hostIdOf(e);
};

/**
 * The element a composed piece belongs to, if it is one.
 *
 * The server names the relationship with a `<thing>_of` key — `box_of`,
 * `chk_of`, `thumb_of`, `value_of`, `attr_of`, `x_of`, `track_of`,
 * `body_of` — so the SUFFIX is the rule rather than a list that goes
 * stale. It already had: an enumerated list here missed `body_of`, and a
 * click on a body block (whose rectangle is transparent, so Excalidraw's
 * fill-aware hit test reaches the wavy lines instead) offered to pin five
 * decoration strokes and nothing else.
 * @param e A live scene element.
 * @returns The host element's id, or null when it is not a piece of one.
 */
const hostIdOf = (e: PinEl): string | null => {
  if (e.containerId) return String(e.containerId);
  const cd: Record<string, unknown> = e.customData || {};
  const key = Object.keys(cd).find((k) => k.endsWith("_of") && cd[k]);
  return key ? String(cd[key]) : null;
};

/**
 * A selection resolved to the elements a pin means anything on.
 *
 * A selection is rarely the thing the user thinks they clicked. Clicking
 * one KPI tile selects two elements (the tile and its value text); a
 * click on a body block selects five wavy decoration strokes and not the
 * transparent rectangle they belong to; a click anywhere in a grouped
 * checkbox selects the box, the tick and the host. Pin those literally
 * and the menu offers "Pin 5 elements" for one gesture and the drawing
 * grows five badges where the user asked for one.
 *
 * So every selected element resolves to its OWNER, deduped. Only if
 * nothing resolves — a selection of pieces whose hosts are gone — does
 * this hand back what it was given, because refusing to pin anything at
 * all is the worse answer.
 * @param sel The currently selected live elements.
 * @param scene Every live element, for looking hosts up.
 * @returns The elements the pin action should act on.
 */
const pinTargets = <T extends PinEl>(sel: T[], scene: T[]): T[] => {
  const out = new Map<string, T>();
  for (const e of sel) {
    const host = hostIdOf(e);
    const owner = host ? scene.find((x) => x.id === host) : e;
    const pick = owner && pinnable(owner) ? owner : pinnable(e) ? e : null;
    if (pick?.id) out.set(pick.id, pick);
  }
  return out.size ? [...out.values()] : sel;
};

/**
 * Where a `w`×`h` insert should land, given where the user is looking.
 *
 * `want` is the viewport centre, and the viewport centre is over a node
 * exactly when the user has zoomed in on one to comment about it — the
 * moment they reach for 🗒, ＋ insert or a template. Dropping there buries
 * the thing they are talking about (the r5-13 shape, arriving by their own
 * hand), so the insert slides out from under whatever it covers.
 *
 * A drop that is ALREADY clear is returned untouched: on empty canvas the
 * centre of the view is the right answer and relocating it would be the
 * opposite defect. Otherwise each of the four directions is stepped past
 * the content in its way, and the winner is the shortest trip that is also
 * still on screen — falling back to the shortest trip, then (if the search
 * is boxed in) to the r5-10 rule the ⌗ import already uses: right of
 * everything drawn, top-aligned with it.
 * @param want Desired top-left in scene coords (the viewport centre).
 * @param w Width of the insert.
 * @param h Height of the insert.
 * @param boxes Occupied boxes the insert must clear.
 * @param view The visible scene rectangle — preferred, not required.
 * @returns The top-left the insert should take.
 */
const clearSpot = (want: Pt, w: number, h: number,
                   boxes: Rect[], view?: Rect): Pt => {
  const over = (x: number, y: number) => boxes.filter(
    (b) => x < b.x + b.w && b.x < x + w && y < b.y + b.h && b.y < y + h);
  if (!over(want.x, want.y).length) return { x: want.x, y: want.y };
  const cands: { x: number; y: number; d: number; seen: boolean }[] = [];
  for (const [dx, dy] of [[1, 0], [0, 1], [-1, 0], [0, -1]]) {
    let x = want.x, y = want.y, hit = over(x, y);
    for (let hop = 0; hop < DROP_HOPS && hit.length; hop++) {
      if (dx > 0) x = Math.max(...hit.map((b) => b.x + b.w)) + DROP_GAP;
      else if (dx < 0) x = Math.min(...hit.map((b) => b.x)) - DROP_GAP - w;
      else if (dy > 0) y = Math.max(...hit.map((b) => b.y + b.h)) + DROP_GAP;
      else y = Math.min(...hit.map((b) => b.y)) - DROP_GAP - h;
      hit = over(x, y);
    }
    if (hit.length) continue;
    cands.push({ x, y, d: Math.abs(x - want.x) + Math.abs(y - want.y),
      seen: !!view && x >= view.x && y >= view.y &&
            x + w <= view.x + view.w && y + h <= view.y + view.h });
  }
  if (!cands.length) {
    return { x: Math.max(...boxes.map((b) => b.x + b.w)) + DROP_GAP,
             y: Math.min(...boxes.map((b) => b.y)) };
  }
  cands.sort((a, b) => Number(b.seen) - Number(a.seen) || a.d - b.d);
  return { x: cands[0].x, y: cands[0].y };
};

/**
 * Where a ❓ glyph sits: hugging its target, never in a neighbour.
 *
 * Mirrors `pin_spot` in canvas.py, the same way `markerAnchor` mirrors
 * `marker_anchor` — the agent's pin seeder learned this in r4b-3 and the
 * user's own ❓ button never did: a constant top-right offset is
 * layout-density-blind, so on a tight wireframe grid the glyph missed its
 * target entirely and sat inside the NEXT panel, reading as a question
 * about that one. On flows, where there are hundreds of px of air, nothing
 * moves.
 *
 * A FRAME IS NOT A NEIGHBOUR — the same rule `dropClear` states in its own
 * docstring below, which this used to contradict from twenty lines away. A
 * frame is the screen an element is drawn INSIDE, so on any wireframe the
 * hug spot always overlapped it, `buried` was always true, and the glyph was
 * always pulled out of clear air onto the tile it asks about: the r5-13
 * shape arriving through the function written to prevent its sibling. 54 of
 * the corpus's 291 pin-eligible elements (18.6%) fell back for this reason
 * alone, every one with a clear hug spot, and none of them for a frame that
 * did NOT contain them — so the type goes rather than a containment test
 * (curator batch 31, driving this; v0.9 TASK-ELBOX).
 * @param target The element the question is about.
 * @param live The scene, as collision candidates.
 * @param size Glyph bbox edge in px.
 * @returns The glyph's top-left in scene coords.
 */
const pinSpot = (target: SceneEl, live: SceneEl[], size = 26): Pt => {
  const p = markerAnchor(target, 8, -8, "tr");
  const foreign = live.filter((e) =>
    e.id !== target.id && !e.isDeleted &&
    ["rectangle", "diamond", "ellipse"].includes(e.type || "") &&
    !["label", "pin", "decoration", "annotation"]
      .includes(e.customData?.role || ""));
  const buried = foreign.some((e) => {
    const b = elBox(e);
    return p.x < b.x + b.w && b.x < p.x + size &&
           p.y < b.y + b.h && b.y < p.y + size;
  });
  return buried
    ? { x: target.x + (target.width || 0) - size - 2, y: target.y + 2 }
    : p;
};


export default function App() {
  const [state, setState] = useState<any>(null);
  const [currentArtifact, setCurrentArtifact] = useState<string | null>(null);
  const [dirtyMap, setDirtyMap] = useState<Record<string, boolean>>({});
  const [viewingRevn, setViewingRevn] = useState<number | null>(null);
  const [viewScenes, setViewScenes] = useState<Record<string, any[]>>({});
  const [toasts, setToasts] = useState<{ id: number; text: string }[]>([]);
  const [staleInfo, setStaleInfo] = useState<any>(null);
  const [forkPrompt, setForkPrompt] = useState<{ open: boolean; name: string } | null>(null);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [suggestText, setSuggestText] = useState("");
  const [dropOpen, setDropOpen] = useState(false);
  const [dismissedPending, setDismissedPending] = useState<number[]>([]);
  const [showAllPending, setShowAllPending] = useState(false);
  const [seenPins, setSeenPins] = useState<string[]>([]);
  const [lastSave, setLastSave] = useState<any>(null);
  const [narrOpen, setNarrOpen] = useState(false);
  const [camera, setCamera] = useState({ scrollX: 0, scrollY: 0, zoom: 1 });
  const [detailItem, setDetailItem] = useState<{ kind: "pin" | "tripwire"; data: any } | null>(null);
  const [docView, setDocView] = useState<{ path: string; content: string } | null>(null);
  // v0.3 — anchored popovers, tooltips, inspector, revert
  const [anchored, setAnchored] = useState<{
    kind: "pin" | "tripwire"; data: any;
    el: { x: number; y: number; width?: number; height?: number };
  } | null>(null);
  // v0.8 — a drawn control is operable (r4/B2: clicking a checkbox
  // opened Excalidraw's stroke panel; the user could not express
  // "uncheck" at all — the flagship gesture of the capability demo)
  const [ctl, setCtl] = useState<{
    hostId: string; kind: string; checked: boolean;
    el: { x: number; y: number; width?: number; height?: number };
  } | null>(null);
  const [tooltipEdit, setTooltipEdit] = useState<{
    elId: string; initial: string;
    el: { x: number; y: number; width?: number; height?: number };
  } | null>(null);
  const [hoverTip, setHoverTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [revertPrompt, setRevertPrompt] = useState(false);
  const [selElId, setSelElId] = useState<string | null>(null);
  // v0.9 "Pin to Canvas" — a pinned element is Excalidraw's own `locked`,
  // and Excalidraw draws a locked element no differently at all: no
  // badge, no border, no handles. The tack is the whole affordance, and
  // because a locked element cannot be click-selected it is also the only
  // way back out that does not go through a menu.
  const [selIds, setSelIds] = useState<string[]>([]);
  const [hoverPinId, setHoverPinId] = useState<string | null>(null);
  const [pinAlways, setPinAlways] = useState<boolean>(() =>
    typeof localStorage !== "undefined" &&
    localStorage.getItem(PIN_ALWAYS_KEY) === "1");
  // Element GEOMETRY has no React presence: `selEl` and friends re-derive
  // from `dirtyMap`/`state`, which move once per save and never during a
  // move, so an overlay keyed off them freezes while the drawing under it
  // travels. This ticks on every real element mutation instead.
  const [geomTick, setGeomTick] = useState(0);
  const [docsList, setDocsList] = useState<string[]>([]);
  const [insertOpen, setInsertOpen] = useState(false);
  const [mermaidOpen, setMermaidOpen] = useState(false);
  const [mermaidText, setMermaidText] = useState("");
  const [mermaidBusy, setMermaidBusy] = useState(false);
  const [walkIdx, setWalkIdx] = useState<number | null>(null);
  // the onion skin: an old revision kept as a DOM overlay only. It never
  // enters the Excalidraw scene, so it can never reach a save buffer.
  const [ghost, setGhost] = useState<{ revn: number; scenes: Record<string, any[]> } | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    (typeof localStorage !== "undefined" && localStorage.getItem("wysiwyg-theme") === "dark")
      ? "dark" : "light");

  const apiRef = useRef<any>(null);
  const stateRef = useRef<any>(null);
  const viewScenesRef = useRef<Record<string, any[]>>({});
  const firstStateRef = useRef(true);
  const loadedRevnRef = useRef<number>(0);
  const buffersRef = useRef<Record<string, any[]>>({});
  const baselineFpRef = useRef<Record<string, string>>({});
  const loadedHashRef = useRef<Record<string, string>>({});
  const appStateRef = useRef<any>({});
  const currentRef = useRef<string | null>(null);
  const dirtyRef = useRef<Record<string, boolean>>({});
  const viewingRef = useRef<number | null>(null);
  const changeTimer = useRef<any>(null);
  const servicingShot = useRef<Set<number>>(new Set());
  const servicingMermaid = useRef<Set<number>>(new Set());
  const prevSelRef = useRef<string>("");
  const suppressPinOpenRef = useRef<number>(0);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const cameraRef = useRef({ scrollX: 0, scrollY: 0, zoom: 1 });
  const hoverTimerRef = useRef<any>(null);
  // the pinned set, mirrored for the pointer-move handler: without it,
  // tracking hover would run a full-scene hit test at pointer rate on
  // every artifact, pinned or not
  const pinnedIdsRef = useRef<Set<string>>(new Set());
  const pinAlwaysRef = useRef(false);

  currentRef.current = currentArtifact;
  dirtyRef.current = dirtyMap;
  viewingRef.current = viewingRevn;
  cameraRef.current = camera;
  pinAlwaysRef.current = pinAlways;

  const toast = useCallback((text: string) => {
    const id = ++toastSeq;
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const anyDirty = Object.values(dirtyMap).some(Boolean);

  useEffect(() => {
    (window as any).__wysiwyg = {
      dirtyMap, currentArtifact,
      baseline: baselineFpRef.current,
      buffers: buffersRef.current,
      state,
    };
  });


  /* ---------------- scene loading ---------------- */
  const loadScene = useCallback((els: any[], viewMode: boolean) => {
    if (!apiRef.current) return [];
    const restored = restoreForRender(els);
    apiRef.current.updateScene({
      elements: restored,
      captureUpdate: CaptureUpdateAction.NEVER,
    });
    if (!viewMode) apiRef.current.scrollToContent(restored, { fitToViewport: true, viewportZoomFactor: 0.85 });
    return restored;
  }, []);

  /** Re-hydrate Excalidraw's file store from the artifact's persisted
   * images. Without this an imported image round-trips as an empty box:
   * the element survives the save, its bytes live in `files`, and the
   * canvas has no idea where to find them. */
  const addStoredFiles = useCallback((aid: string, st?: any) => {
    const api = apiRef.current;
    const s = st || stateRef.current;
    if (!api?.addFiles) return;
    const stored = s?.artifacts?.[aid]?.files;
    if (!stored) return;
    const vals = Object.values<any>(stored).filter((f) => f && f.id && f.dataURL);
    if (vals.length) api.addFiles(vals as any);
  }, []);

  const showArtifact = useCallback(
    (aid: string, st?: any, forceReload = false) => {
      const s = st || stateRef.current;
      if (!s) return;
      // stash the outgoing buffer
      const prev = currentRef.current;
      if (prev && dirtyRef.current[prev] && apiRef.current)
        buffersRef.current[prev] = apiRef.current.getSceneElements();
      setCurrentArtifact(aid);
      currentRef.current = aid;
      addStoredFiles(aid, s);
      if (viewingRef.current != null) {
        loadScene(viewScenesRef.current[aid] || [], true);
        return;
      }
      if (!Object.values(dirtyRef.current).some(Boolean))
        loadedRevnRef.current = s.head_revn ?? loadedRevnRef.current;
      const committed = s.artifacts?.[aid]?.elements || [];
      if (dirtyRef.current[aid] && buffersRef.current[aid] && !forceReload) {
        loadScene(buffersRef.current[aid], false);
      } else {
        loadedHashRef.current[aid] = JSON.stringify(committed);
        const restored = loadScene(committed, false);
        baselineFpRef.current[aid] = fingerprint(restored);
        pendingBaselineRef.current = aid;
        setDirtyMap((d) => ({ ...d, [aid]: false }));
      }
    },
    [loadScene, addStoredFiles]
  );

  /* ---------------- pin-only gap merge ---------------- */
  const mergePinOnlyGap = useCallback(async (s: any) => {
    const from = loadedRevnRef.current;
    const gap: any[] = [];
    for (const sv of s.saves || [])
      if (sv.revn > from && sv.revn <= s.head_revn) gap.push(sv);
    gap.sort((a, b) => a.revn - b.revn);
    const records: any[] = [];
    for (const sv of gap) {
      try {
        records.push((await apiGet(`/api/save-record/${sv.revn}`)).record);
      } catch {
        return; // can't read the gap — let the base_revn check handle it
      }
    }
    const pinOnly = records.every((r) =>
      Object.values(r.artifacts || {}).every((part: any) =>
        (part.changes || []).every(
          (c: any) =>
            (c.op === "add" || c.op === "del")
              ? (c.element?.customData?.role === "pin")
              : c.op === "mod"
                ? (c.attrs || []).every((a: any) => a.derived)
                : false
        )
      )
    );
    if (!pinOnly) return; // genuine stale tab — leave for the 409 path
    for (const r of records) {
      for (const [aid, part] of Object.entries<any>(r.artifacts || {})) {
        if (buffersRef.current[aid])
          buffersRef.current[aid] = replayChanges(buffersRef.current[aid], part.changes || []);
        if (aid === currentRef.current && apiRef.current) {
          const replayed = replayChanges(
            apiRef.current.getSceneElements().filter((e: any) => !e.isDeleted),
            part.changes || []
          );
          loadScene(replayed, false);
          buffersRef.current[aid] = replayed;
        }
      }
    }
    loadedRevnRef.current = s.head_revn;
  }, [loadScene]);

  /* ---------------- polling ---------------- */
  const refresh = useCallback(async () => {
    try {
      const s = await apiGet("/api/state");
      if (firstStateRef.current) {
        // pins that were already open before this tab loaded don't deserve a
        // "new question" banner — the rail shows them
        firstStateRef.current = false;
        setSeenPins((s.pins || []).filter((p: any) => p.status === "open").map((p: any) => p.id));
      }
      stateRef.current = s;
      setState(s);
      const aids = Object.keys(s.artifacts || {});
      let cur = currentRef.current;
      if (!cur || (!s.artifacts?.[cur] && aids.length)) {
        cur = aids[0] || null;
        if (cur) showArtifact(cur, s);
        else setCurrentArtifact(null);
      } else if (cur && s.artifacts?.[cur] && viewingRef.current == null &&
                 s.checkout_revn == null) {
        const anyDirtyNow = Object.values(dirtyRef.current).some(Boolean);
        if (!anyDirtyNow) {
          loadedRevnRef.current = s.head_revn;
          // reload if the committed scene changed under a clean canvas
          const committed = JSON.stringify(s.artifacts[cur].elements);
          if (!dirtyRef.current[cur] && committed !== loadedHashRef.current[cur]) {
            loadedHashRef.current[cur] = committed;
            addStoredFiles(cur, s);
            const restored = loadScene(s.artifacts[cur].elements, false);
            baselineFpRef.current[cur] = fingerprint(restored);
            pendingBaselineRef.current = cur;
          }
        } else if (s.head_revn > loadedRevnRef.current) {
          // head advanced under unsaved edits. Pin-only revisions commit
          // without holding — merge those into the dirty buffers so the
          // eventual Save diffs cleanly. Anything else is a genuine stale
          // tab: leave it; the Save's base_revn check catches it gently.
          await mergePinOnlyGap(s);
        }
      }
      return s;
    } catch {
      return null;
    }
  }, [showArtifact, loadScene, mergePinOnlyGap, addStoredFiles]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
    // mount-only: refresh is ref-based and stable in practice
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // heartbeat: the server needs to know whether the canvas is dirty to decide
  // auto-apply vs pending-banner
  useEffect(() => {
    const send = () => apiPost("/api/heartbeat", { dirty: Object.values(dirtyRef.current).some(Boolean) }).catch(() => {});
    send();
    const t = setInterval(send, 3000);
    return () => clearInterval(t);
  }, [anyDirty]);

  /* ---------------- Excalidraw change tracking ---------------- */
  const verSumRef = useRef<number>(-1);
  const pendingBaselineRef = useRef<string | null>(null);

  const onChange = useCallback((els: readonly any[], appState: any) => {
    appStateRef.current = appState;
    setCamera((c) =>
      c.scrollX !== appState.scrollX || c.scrollY !== appState.scrollY || c.zoom !== appState.zoom.value
        ? { scrollX: appState.scrollX, scrollY: appState.scrollY, zoom: appState.zoom.value }
        : c
    );
    if (viewingRef.current != null) return;
    const aid = currentRef.current;
    if (!aid) return;
    // pin ❓ glyph click → open its detail card. The glyph is a plain
    // Excalidraw text element, so pointer selection is the only signal —
    // before this, clicking the glyph did nothing (capability assessment).
    const selIds = Object.keys(appState.selectedElementIds || {}).filter(
      (k) => appState.selectedElementIds[k]);
    const selKey = selIds.join(",");
    if (selKey !== prevSelRef.current) {
      prevSelRef.current = selKey;
      setSelElId(selIds.length === 1 ? selIds[0] : null);
      // the WHOLE selection, not just the singleton: `selElId` is null
      // for a multi-select, so every selection-driven surface was blind
      // to two-or-more until the tack needed a union box
      setSelIds(selIds);
      if (selIds.length === 1 && Date.now() > suppressPinOpenRef.current) {
        const sel = els.find((e) => e.id === selIds[0]);
        if (sel?.customData?.role === "pin") {
          const pin = stateRef.current?.pins?.find((p: any) => p.id === sel.id);
          if (pin && pin.status !== "resolved")
            // anchored next to the ❓ (v0.3) — the centered modal lost
            // the spatial context the pin exists to give
            setAnchored({ kind: "pin", data: pin,
              el: { x: sel.x, y: sel.y, width: sel.width, height: sel.height } });
        } else if (sel?.customData?.document) {
          // report reader: a document-backed element opens readable in
          // place, never download-to-read (demo parity)
          apiGet(`/api/doc/${encodeURI(sel.customData.document)}`)
            .then((r) => setDocView({ path: r.path, content: r.content }))
            .catch((e) => toast(e.message));
        }
      }
      // control affordance (v0.8): selecting a checkbox/toggle host, a
      // composed part, or the whole GROUP (a click on a grouped element
      // selects all its members) offers the state flip
      const hostIds = new Set<string>();
      for (const id of selIds) {
        const e = els.find((el2) => el2.id === id && !el2.isDeleted);
        const cd2: any = e?.customData || {};
        if (["checkbox", "toggle"].includes(cd2.kind)) hostIds.add(e!.id);
        else if (cd2.box_of || cd2.chk_of || cd2.thumb_of)
          hostIds.add(cd2.box_of || cd2.chk_of || cd2.thumb_of);
        else if (e) hostIds.add("__not_a_control__");
      }
      const onlyHost = hostIds.size === 1 &&
        !hostIds.has("__not_a_control__")
        ? [...hostIds][0] : null;
      const host = onlyHost ? els.find((e) =>
        e.id === onlyHost && !e.isDeleted &&
        ["checkbox", "toggle"].includes((e.customData || {}).kind))
        : null;
      if (host && selIds.length > 0) {
        setCtl({
          hostId: host.id, kind: host.customData.kind,
          checked: !!host.customData.checked,
          el: { x: host.x, y: host.y, width: host.width,
                height: host.height },
        });
      } else {
        setCtl(null);
      }
    }
    // Excalidraw calls onChange continuously; element versions only move on
    // real mutations, so gate the (heavier) fingerprint work on them.
    let v = els.length * 7919;
    for (const e of els) v += (e.version || 0) + (e.isDeleted ? 13 : 0);
    if (v === verSumRef.current && pendingBaselineRef.current !== aid) return;
    verSumRef.current = v;
    // past the gate a real mutation happened — a move, a resize, a pin.
    // Canvas overlays derive their position from element geometry, so
    // this is the only signal that tells them to re-read it.
    setGeomTick((t) => t + 1);
    const live = els.filter((e) => !e.isDeleted).map((e) => e);
    const fp = fingerprint(live);
    if (pendingBaselineRef.current === aid) {
      // first onChange after a scene load: whatever the canvas settled on IS
      // the clean state
      pendingBaselineRef.current = null;
      baselineFpRef.current[aid] = fp;
      setDirtyMap((d) => (d[aid] === false ? d : { ...d, [aid]: false }));
      return;
    }
    buffersRef.current[aid] = live as any[];
    const isDirty = fp !== baselineFpRef.current[aid];
    setDirtyMap((d) => (d[aid] === isDirty ? d : { ...d, [aid]: isDirty }));
  }, []);

  /* ---------------- Save ---------------- */
  const doSave = useCallback(
    async (forkName?: string) => {
      if (!state) return;
      const scenes: Record<string, any[]> = {};
      for (const [aid, dirty] of Object.entries(dirtyRef.current))
        if (dirty && buffersRef.current[aid]) scenes[aid] = buffersRef.current[aid];
      const cur = currentRef.current;
      if (cur && apiRef.current && dirtyRef.current[cur])
        scenes[cur] = apiRef.current.getSceneElements().filter((e: any) => !e.isDeleted);
      if (!Object.keys(scenes).length) return;
      if (state.checkout_revn != null && !forkName) {
        setForkPrompt({ open: true, name: `alt-${String((state.revn || 0) + 1).padStart(4, "0")}` });
        return;
      }
      const selection = Object.keys(appStateRef.current?.selectedElementIds || {})
        .filter((k) => appStateRef.current.selectedElementIds[k])
        .map((k) => `${cur}#${k}`);
      // ship the bytes behind any image element in the artifacts we're
      // saving. Excalidraw keeps files in one flat store keyed by fileId;
      // the server keeps them per artifact, so split by what each scene
      // actually references. The server MERGES what arrives, so sending a
      // subset is safe — it never drops files we didn't mention.
      const allFiles: Record<string, any> =
        (apiRef.current?.getFiles ? apiRef.current.getFiles() : null) || {};
      const files: Record<string, Record<string, any>> = {};
      for (const [aid, els] of Object.entries(scenes)) {
        const mine: Record<string, any> = {};
        for (const e of els as any[])
          if (e.fileId && allFiles[e.fileId]) mine[e.fileId] = allFiles[e.fileId];
        if (Object.keys(mine).length) files[aid] = mine;
      }
      try {
        const base = state.checkout_revn != null ? state.checkout_revn
          : (loadedRevnRef.current || state.head_revn);
        const r = await apiPost("/api/save", {
          base_revn: base, scenes, selection, fork_name: forkName || undefined,
          files: Object.keys(files).length ? files : undefined,
        });
        setDirtyMap({});
        buffersRef.current = {};
        setLastSave(r);
        setNarrOpen(true);
        const s = await refresh();
        if (s && cur) showArtifact(cur, s, true);
        toast(`Saved ✓ #${r.revn} (${r.short_id})${r.branch !== "main" ? ` on ⎇ ${r.branch}` : ""}`);
      } catch (e: any) {
        if (e.status === 409) setStaleInfo(e.payload);
        else toast(`Save failed: ${e.message}`);
      }
    },
    [state, refresh, showArtifact, toast]
  );

  /* ---------------- view mode (time travel) ---------------- */
  const viewCommit = useCallback(
    async (revn: number) => {
      if (Object.values(dirtyRef.current).some(Boolean)) {
        toast("Unsaved edits block time-travel — Save (or undo) first. Nothing is ever lost.");
        return;
      }
      const aids = Object.keys(state?.artifacts || {});
      const scenes: Record<string, any[]> = {};
      await Promise.all(
        aids.map(async (aid) => {
          try {
            const r = await apiGet(`/api/artifact/${aid}?at=${revn}`);
            scenes[aid] = r.elements || [];
          } catch {
            scenes[aid] = [];
          }
        })
      );
      setViewScenes(scenes);
      viewScenesRef.current = scenes;
      setViewingRevn(revn);
      viewingRef.current = revn;
      const cur = currentRef.current || aids[0];
      if (cur) loadScene(scenes[cur] || [], true);
    },
    [state, loadScene, toast]
  );

  const backToLive = useCallback(async () => {
    setViewingRevn(null);
    viewingRef.current = null;
    setViewScenes({});
    viewScenesRef.current = {};
    const s = await refresh();
    const cur = currentRef.current;
    if (cur && s) showArtifact(cur, s, true);
  }, [refresh, showArtifact]);

  const workFromHere = useCallback(async () => {
    const revn = viewingRef.current;
    if (revn == null) return;
    try {
      await apiPost("/api/checkout", { revn });
      const scenes = viewScenes;
      setViewingRevn(null);
      viewingRef.current = null;
      const s = await refresh();
      const cur = currentRef.current;
      if (cur && s) {
        // load the checked-out state as the editable baseline
        const els = scenes[cur] || [];
        loadedHashRef.current[cur] = JSON.stringify(els);
        const restored = loadScene(els, false);
        baselineFpRef.current[cur] = fingerprint(restored);
        pendingBaselineRef.current = cur;
        setDirtyMap((d) => ({ ...d, [cur]: false }));
      }
      toast(`Working from save #${revn} — your first Save here will fork a branch.`);
    } catch (e: any) {
      toast(e.message);
    }
  }, [viewScenes, refresh, loadScene, toast]);

  /* ---------------- pending revisions ---------------- */
  const resolvePending = useCallback(
    async (id: number, action: "apply_now" | "after_save" | "discard") => {
      try {
        const r = await apiPost("/api/pending/resolve", { id, action });
        if (action === "discard") {
          setDismissedPending((d) => [...d, id]);
          toast("Revision discarded.");
          refresh();
          return;
        }
        if (action === "apply_now") {
          const cur = currentRef.current;
          if (cur && dirtyRef.current[cur] && r.changes?.[cur] && apiRef.current) {
            // op replay onto the unsaved canvas — user work is inviolable
            const replayed = replayChanges(
              apiRef.current.getSceneElements().filter((e: any) => !e.isDeleted),
              r.changes[cur]
            );
            buffersRef.current[cur] = replayed;
            loadScene(replayed, false);
          }
          // say WHERE it landed when that is not where you are looking:
          // the filmstrip is concept-scoped, so an artifact applied into
          // another concept never joins the strip you can see (r3-3)
          const landed = r.artifact || cur;
          const st = stateRef.current;
          const owning = (st?.concepts || []).find(
            (c: any) => (c.views || []).includes(cur));
          const visible: string[] = owning
            ? owning.views : Object.keys(st?.artifacts || {});
          toast(landed && !visible.includes(landed)
            ? `Applied agent revision #${r.revn} — ${st?.artifacts?.[landed]?.name || landed}, in another concept`
            : `Applied agent revision #${r.revn}`);
        } else toast("Revision will land after your next Save.");
        refresh();
      } catch (e: any) {
        toast(e.message);
      }
    },
    [refresh, loadScene, toast]
  );

  /* ---------------- screenshot servicing ---------------- */
  useEffect(() => {
    const reqs = state?.screenshot_requests || [];
    for (const req of reqs) {
      if (servicingShot.current.has(req.id)) continue;
      servicingShot.current.add(req.id);
      const aid = req.artifact || currentRef.current;
      const els =
        (viewingRef.current != null ? viewScenes[aid!] : null) ||
        buffersRef.current[aid!] ||
        state?.artifacts?.[aid!]?.elements ||
        (apiRef.current ? apiRef.current.getSceneElements() : []);
      (async () => {
        try {
          const blob = await exportToBlob({
            elements: restoreForRender(els) as any,
            appState: {
              viewBackgroundColor: "#faf8f2", exportWithDarkMode: false,
              frameRendering: { enabled: true, name: true, outline: true, clip: false },
            },
            // the user's own export passes getFiles(); this passed null,
            // so an agent snapshot of an artifact holding an image
            // placeholder rendered it as an empty box (v0.7 WP5)
            files: apiRef.current?.getFiles ? apiRef.current.getFiles() : null,
            mimeType: "image/png",
            exportPadding: 40,
          } as any);
          const dataUrl: string = await new Promise((res) => {
            const fr = new FileReader();
            fr.onload = () => res(fr.result as string);
            fr.readAsDataURL(blob);
          });
          // what this tab knows and the server can only re-derive: how
          // many bytes left here, and off what canvas. The server checks
          // the file it ends up holding against both (canvas.py,
          // `validate_png`). The byte count is the load-bearing one — a
          // payload truncated in transit keeps a valid PNG header and a
          // plausible density, so nothing on that side can see it
          // otherwise. Best effort throughout: a browser without
          // createImageBitmap posts the count alone, and the server
          // takes a report with fields missing (or with none at all,
          // which is what every bundle older than this one sends).
          let framed: { png_w: number; png_h: number } | null = null;
          try {
            const bmp = await createImageBitmap(blob);
            framed = { png_w: bmp.width, png_h: bmp.height };
            bmp.close();
          } catch { /* older browser — the byte count still ships */ }
          await apiPost("/api/screenshot/complete", {
            id: req.id, data_url: dataUrl, bytes: blob.size,
            artifact: aid, ...framed,
          });
        } catch (e: any) {
          console.error("screenshot failed", e);
        }
      })();
    }
  }, [state, viewScenes]);

  /* ---------------- mermaid conversion servicing (WP9) ----------------
   * The stdlib server can't run the mermaid converter, so the agent's
   * `canvas.py mermaid` posts the text here and this tab (or a headless
   * one the CLI launches on the same URL) converts it to element
   * SKELETONS and posts them back. Skeletons, not full elements: the
   * CLI maps them to ops so the seed flows through apply — lints,
   * budgets, registry, narration — like any agent revision. */
  useEffect(() => {
    const reqs = state?.mermaid_requests || [];
    for (const req of reqs) {
      if (servicingMermaid.current.has(req.id)) continue;
      servicingMermaid.current.add(req.id);
      (async () => {
        try {
          const { parseMermaidToExcalidraw } = await import("@excalidraw/mermaid-to-excalidraw");
          await ensureElkLayouts();
          const { elements } = await parseMermaidToExcalidraw(req.definition, {
            themeVariables: { fontSize: "20px" },
          });
          await apiPost("/api/mermaid/complete", { id: req.id, elements });
        } catch (e: any) {
          // a syntax error must reach the CLI as an ERROR, not a timeout
          await apiPost("/api/mermaid/complete", {
            id: req.id, error: String(e?.message || e),
          }).catch(() => undefined);
        }
      })();
    }
  }, [state]);

  /* ---------------- derived ---------------- */
  const artifacts = state?.artifacts || {};
  const artifactIds = Object.keys(artifacts);
  const concepts = state?.concepts || [];
  const cadence = state?.config?.canvas_updates || "per-round";
  const pending = (state?.pending || []).filter((p: any) => !dismissedPending.includes(p.id));
  const openTripwires = (state?.tripwires || []).filter((t: any) => t.status === "open");
  const freshPins = (state?.pins || []).filter(
    (p: any) => p.status === "open" && !seenPins.includes(p.id)
  );

  const currentConceptViews = useMemo(() => {
    const c = concepts.find((c: any) => (c.views || []).includes(currentArtifact));
    const views = c ? c.views.filter((v: string) => artifacts[v]) : artifactIds;
    return views;
  }, [concepts, currentArtifact, artifactIds.join(",")]);

  // v0.8 (r3-3/B7): the strip shows the current concept's views FIRST,
  // then every other artifact after a divider — a 5-artifact project
  // used to show one thumbnail, and an artifact applied into another
  // concept was unreachable except through the dropdown.
  const stripViews = useMemo(() => {
    const here = new Set(currentConceptViews);
    const rest = artifactIds.filter((a) => !here.has(a));
    return { here: currentConceptViews, rest };
  }, [currentConceptViews, artifactIds.join(",")]);

  /** The concept the filmstrip is showing, if it is showing one.
   *
   * The strip is concept-scoped on purpose — grouping views by the thing
   * they are views OF is the skill's central idea — but it never said so,
   * so "what exists here" silently meant "what exists in this concept"
   * and an artifact applied from the banner while you were looking
   * elsewhere never appeared (v0.6 assessment r3-3). */
  const currentConceptName = useMemo(() => {
    const c = concepts.find((c: any) => (c.views || []).includes(currentArtifact));
    return c ? c.name : null;
  }, [concepts, currentArtifact]);

  const groupedArtifacts = useMemo(() => {
    const groups: { name: string; ids: string[] }[] = [];
    const claimed = new Set<string>();
    for (const c of concepts) {
      const ids = (c.views || []).filter((v: string) => artifacts[v]);
      if (ids.length) {
        groups.push({ name: c.name, ids });
        ids.forEach((i: string) => claimed.add(i));
      }
    }
    const solo = artifactIds.filter((a) => !claimed.has(a));
    if (solo.length) groups.push({ name: "Solo artifacts", ids: solo });
    return groups;
  }, [concepts, artifactIds.join(",")]);

  /* ---------------- goto: reveal an anchored element on canvas -------- */
  const revealElement = useCallback((elId: string) => {
    const api = apiRef.current;
    if (!api) return;
    const el = api.getSceneElements().find((e: any) => e.id === elId);
    if (!el) { toast("That element is no longer on the canvas."); return; }
    try {
      // programmatic selection must not re-open the pin card the user is
      // already reading ("show on canvas" would boomerang)
      suppressPinOpenRef.current = Date.now() + 1200;
      api.scrollToContent([el], { fitToViewport: false, animate: true });
      api.updateScene({ appState: { selectedElementIds: { [el.id]: true } } });
    } catch {
      /* selection API differences are cosmetic — scroll is the point */
    }
  }, []);

  const gotoElement = useCallback((ref: { aid: string; el: string }) => {
    if (!ref) return;
    if (ref.aid !== currentRef.current) {
      showArtifact(ref.aid);
      // let the scene land before revealing
      setTimeout(() => revealElement(ref.el), 350);
    } else {
      revealElement(ref.el);
    }
  }, [revealElement, showArtifact]);

  const answerTripwire = useCallback((id: string, answer: string) => {
    apiPost("/api/tripwires/answer", { id, answer })
      .then(() => { toast("Answer sent — the agent picks it up on its next move."); refresh(); })
      .catch((e) => toast(e.message));
  }, [refresh]);

  /* annotation → target leader lines (layout.md promises them; the
     renderer never drew them) */
  const leaderLines = useMemo(() => {
    if (!currentArtifact) return [];
    const els = (viewingRevn != null
      ? viewScenes[currentArtifact]
      : buffersRef.current[currentArtifact] ||
        artifacts[currentArtifact]?.elements) || [];
    const ix: Record<string, any> = {};
    for (const e of els) ix[e.id] = e;
    const out: { a: any; b: any }[] = [];
    for (const e of els) {
      const tgt = e.customData?.annotates && ix[e.customData.annotates];
      if (e.customData?.role === "annotation" && tgt)
        out.push({ a: e, b: tgt });
    }
    return out;
  }, [currentArtifact, state, viewingRevn, viewScenes]);

  const tripTagsForCurrent = useMemo(
    () =>
      openTripwires
        .map((t: any) => {
          const [aid, elId] = (t.changed || "").split("#");
          if (aid !== currentArtifact) return null;
          const els = viewingRevn != null ? viewScenes[aid] : buffersRef.current[aid] || artifacts[aid]?.elements;
          const el = (els || []).find((e: any) => e.id === elId);
          return el ? { el, t } : null;
        })
        .filter(Boolean),
    [openTripwires, currentArtifact, state, viewingRevn, viewScenes]
  );

  const copyContextUpdate = useCallback(() => {
    const s = lastSave;
    const parts = [
      "WYSIWYG Grilling context update:",
      s ? `please review save #${s.revn} (${s.short_id}) on branch ${s.branch}.` : `project is at revn ${state?.revn} on branch ${state?.head}.`,
      currentArtifact ? `Current artifact: ${artifacts[currentArtifact]?.name || currentArtifact}.` : "",
      openTripwires.length ? `${openTripwires.length} open mapping tripwire(s).` : "",
      "Read project_knowledge/ and narrate.",
    ].filter(Boolean);
    navigator.clipboard?.writeText(parts.join(" "));
    toast("Context update copied — paste it to the agent in chat.");
  }, [lastSave, state, currentArtifact, openTripwires.length, toast]);

  /* Esc closes whichever lightweight modal is open — before this, the
     suggest-view scrim could only be closed via Cancel and silently
     swallowed every click including Save (capability assessment) */
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (suggestOpen) { setSuggestOpen(false); setSuggestText(""); }
      else if (forkPrompt?.open) setForkPrompt(null);
      else if (revertPrompt) setRevertPrompt(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [suggestOpen, forkPrompt, revertPrompt]);

  /* ---------------- user-authored elements (Phase 5) ---------------- */
  const insertElements = useCallback((newEls: any[]) => {
    const api = apiRef.current;
    if (!api) return;
    const live = api.getSceneElements().filter((e: any) => !e.isDeleted);
    const restored = restoreForRender([...live, ...newEls]);
    api.updateScene({ elements: restored });
  }, []);

  const sceneCenter = useCallback(() => {
    const a = appStateRef.current || {};
    const z = camera.zoom || 1;
    return {
      x: (a.width || 800) / 2 / z - camera.scrollX,
      y: (a.height || 600) / 2 / z - camera.scrollY,
    };
  }, [camera]);

  /** The scene rectangle the camera is currently showing. */
  const viewRect = useCallback((): Rect => {
    const a = appStateRef.current || {};
    const z = camera.zoom || 1;
    return { x: -camera.scrollX, y: -camera.scrollY,
             w: (a.width || 800) / z, h: (a.height || 600) / z };
  }, [camera]);

  /**
   * Top-left for a `w`×`h` insert: the centre of the view, nudged clear.
   *
   * Frames are containers you draw things INSIDE, so they are not obstacles
   * for the things — landing within one is the point, landing on a node, a
   * label or another note is the harm. A frame insert is the exception and
   * passes `avoidFrames`: two screens stacked on each other is not a
   * container relationship, it is a mess.
   * @param w Width of the insert.
   * @param h Height of the insert.
   * @param avoidFrames Treat frames as obstacles too (frame inserts).
   * @returns The top-left the insert should take, in scene coords.
   */
  const dropClear = useCallback((w: number, h: number, avoidFrames = false) => {
    const c = sceneCenter();
    const boxes = (apiRef.current?.getSceneElements() || [])
      .filter((e: SceneEl) => !e.isDeleted &&
              (avoidFrames || e.type !== "frame"))
      .map(elBox);
    return clearSpot({ x: c.x - w / 2, y: c.y - h / 2 }, w, h,
                     boxes, viewRect());
  }, [sceneCenter, viewRect]);

  /**
   * Bring the view to a fresh insert, but only if it landed off screen.
   *
   * The nudge is what makes this necessary: zoomed in on one tile there may
   * be no clear spot in view at all, and an insert the user cannot see is
   * indistinguishable from one that never happened. Scrolling when it IS in
   * view would be the gratuitous half of the same defect, so this does
   * nothing in that case — and it keeps the zoom either way.
   *
   * The scroll is written straight into `appState` rather than through
   * `scrollToContent`, which takes REAL scene elements: handed the raw
   * skeletons an insert is made of, it drove Excalidraw into a render
   * loop (React #185) and the insert never landed at all.
   * @param els The elements just inserted.
   */
  const revealInsert = useCallback((els: SceneEl[]) => {
    const api = apiRef.current;
    if (!api || !els.length) return;
    const v = viewRect();
    const bs = els.map(elBox);
    const x0 = Math.min(...bs.map((b) => b.x)), y0 = Math.min(...bs.map((b) => b.y));
    const x1 = Math.max(...bs.map((b) => b.x + b.w));
    const y1 = Math.max(...bs.map((b) => b.y + b.h));
    if (x0 >= v.x && y0 >= v.y && x1 <= v.x + v.w && y1 <= v.y + v.h) return;
    const z = camera.zoom || 1;
    const a = appStateRef.current || {};
    api.updateScene({ appState: {
      scrollX: (a.width || 800) / 2 / z - (x0 + x1) / 2,
      scrollY: (a.height || 600) / 2 / z - (y0 + y1) / 2 } });
  }, [camera, viewRect]);

  const addStickyNote = useCallback(() => {
    const text = window.prompt("Sticky note (yours — the agent reads it as a requirement):");
    if (!text) return;
    const { x, y } = dropClear(NOTE_W, NOTE_H);
    const id = `note-user-${Date.now().toString(36)}`;
    const els = [
      {
        id, type: "rectangle", x, y, width: NOTE_W, height: NOTE_H,
        backgroundColor: "#fbf3c9", strokeColor: "#c9b961",
        fillStyle: "solid", strokeWidth: 1, roughness: 1, opacity: 100,
        angle: 0, roundness: null, groupIds: [], frameId: null,
        boundElements: [{ id: `${id}-label`, type: "text" }],
        customData: { role: "annotation", author: "user" },
      },
      {
        id: `${id}-label`, type: "text", x: x + 8, y: y + 8,
        width: NOTE_W - 16, height: NOTE_H - 16, text, originalText: text,
        fontSize: 14, fontFamily: 6, textAlign: "left", verticalAlign: "top",
        lineHeight: 1.25, containerId: id, autoResize: false,
        strokeColor: "#4a4433", customData: { role: "label" },
      },
    ];
    insertElements(els);
    revealInsert(els);
    toast("Note added — Save to record it.");
  }, [dropClear, insertElements, revealInsert, toast]);

  const askUserPin = useCallback(() => {
    const api = apiRef.current;
    if (!api) return;
    const selIds = Object.keys(appStateRef.current?.selectedElementIds || {})
      .filter((k) => appStateRef.current.selectedElementIds[k]);
    const target = api.getSceneElements().find(
      (e: any) => selIds.includes(e.id) && !e.isDeleted);
    if (!target) {
      toast("Select an element first — questions anchor to elements (cross-cutting questions go in chat).");
      return;
    }
    const q = window.prompt("Your question about this element (the agent answers on its move):");
    if (!q) return;
    const id = `pin-user-${Date.now().toString(36)}`;
    // anchored, not centred: a pin is ABOUT one element, so it hugs that
    // element's top-right rather than landing where the view happens to
    // be — `pinSpot` is the part that keeps the hug out of a neighbour.
    const spot = pinSpot(target, api.getSceneElements());
    insertElements([{
      id, type: "text", x: spot.x, y: spot.y, width: 26, height: 26, text: "❓",
      originalText: "❓", fontSize: 20, fontFamily: 6,
      textAlign: "center", verticalAlign: "top", lineHeight: 1.25,
      containerId: null, autoResize: true, strokeColor: "#5b9dff",
      strokeWidth: 1, roughness: 1, opacity: 100, angle: 0,
      roundness: null, groupIds: [], frameId: null, boundElements: [],
      backgroundColor: "transparent", fillStyle: "solid",
      customData: { role: "pin", question: q, target: target.id,
                    status: "open", author: "user", direction: "user" },
    }]);
    toast("Question pinned — Save to send it to the agent.");
  }, [insertElements, toast]);

  const dismissPin = useCallback((p: any) => {
    // the user's "not worth explaining": delete the ❓ element; the next
    // Save records the deletion and the server marks the pin dismissed
    if (p.artifact && p.artifact !== currentRef.current) {
      showArtifact(p.artifact);
      toast("Switched to the pin's artifact — dismiss it there.");
      return;
    }
    const api = apiRef.current;
    if (!api) return;
    const live = api.getSceneElements().filter(
      (e: any) => !e.isDeleted && e.id !== p.id);
    api.updateScene({ elements: live });
    toast("Pin dismissed — Save to record it (reads as “not worth explaining”).");
  }, [showArtifact, toast]);

  /* ---------------- v0.3: tooltips, inspector, revert ---------------- */

  /** Client coords → scene coords via the live camera. */
  const sceneAt = useCallback((clientX: number, clientY: number) => {
    const wrap = wrapRef.current;
    if (!wrap) return null;
    const r = wrap.getBoundingClientRect();
    const cam = cameraRef.current;
    const z = cam.zoom || 1;
    return {
      sx: (clientX - r.left) / z - cam.scrollX,
      sy: (clientY - r.top) / z - cam.scrollY,
      wx: clientX - r.left, wy: clientY - r.top,
    };
  }, []);

  /** Smallest non-label element whose bbox contains the scene point. */
  const hitAtClient = useCallback((clientX: number, clientY: number) => {
    const api = apiRef.current;
    const p = sceneAt(clientX, clientY);
    if (!api || !p) return null;
    let best: any = null;
    let bestArea = Infinity;
    for (const e of api.getSceneElements()) {
      if (e.isDeleted || e.type === "frame") continue;
      if (e.customData?.role === "label") continue;
      const w = e.width || 0, h = e.height || 0;
      if (p.sx >= e.x && p.sx <= e.x + w && p.sy >= e.y && p.sy <= e.y + h) {
        const area = (w * h) || 1;
        if (area < bestArea) { best = e; bestArea = area; }
      }
    }
    return best;
  }, [sceneAt]);

  /** Patch a live element (fields + customData; null cd value deletes the
   * key). Version bump makes the change reach the dirty fingerprint, so
   * it lands in the next Save and narrates like any user edit. */
  const patchElement = useCallback((elId: string, patch: any, cdPatch?: any) => {
    const api = apiRef.current;
    if (!api) return;
    const els = api.getSceneElements().map((e: any) => {
      if (e.id !== elId || e.isDeleted) return e;
      const cd: any = { ...(e.customData || {}) };
      for (const [k, v] of Object.entries(cdPatch || {})) {
        if (v === null) delete cd[k];
        else cd[k] = v;
      }
      return { ...e, ...patch, customData: cd, version: (e.version || 0) + 1 };
    });
    api.updateScene({ elements: restoreForRender(els) as any });
  }, []);

  /** Pin (or unpin) a set of elements in ONE scene update.
   *
   * A pin is Excalidraw's native `locked` AND NOTHING ELSE. That flag buys
   * the whole guarantee for free — no drag, no resize, no rubber-band, no
   * Ctrl+A, no transform handles — and `api.ts`'s dirty fingerprint already
   * hashes it, so a pin reaches Save with no new plumbing.
   *
   * No companion `customData` key, deliberately (ruled 2026-08-18 with the
   * server half). One flag, one truth: the server's guard is the single
   * predicate `bool(el["locked"])`, so anything else that sets it — the
   * Inspector checkbox, Excalidraw's own Ctrl+Shift+L, a third-party import,
   * the elements already locked in the corpus — is protected and badged on
   * exactly the same terms as this. A second key would also write a second
   * significant-attr change per element and narrate every pin twice.
   *
   * One `updateScene` for the whole set, deliberately. `doSave` posts whole
   * scenes, so N pins are one POST, one revision, one narration line — the
   * client must never fan a bulk gesture out into per-element writes.
   * @param ids The element ids to change.
   * @param pinned True to pin, false to unpin.
   */
  const setPinned = useCallback((ids: string[], pinned: boolean) => {
    const api = apiRef.current;
    if (!api || !ids.length) return;
    const want = new Set(ids);
    let n = 0;
    const els = api.getSceneElements().map((e: any) => {
      if (!want.has(e.id) || e.isDeleted || !!e.locked === pinned) return e;
      n++;
      return { ...e, locked: pinned, version: (e.version || 0) + 1 };
    });
    if (!n) return;
    // NOT through `restoreForRender`, unlike `patchElement` and
    // `flipControl`. Those two change geometry or add elements, so they
    // need the re-measure; this flips one boolean on elements the canvas
    // already restored. Sending them back through restore re-sorts the
    // scene and re-measures bound text, and a bulk pin came back narrating
    // "resized cb-chk … 1× resized, 5× reordered" — five phantom facts on
    // a save whose entire content was the pins (measured, 2026-08-18).
    api.updateScene({ elements: els });
    const what = n === 1 ? "this element" : `${n} elements`;
    toast(pinned
      ? `📌 Pinned ${what} — Save to record it. A pin stops drags, not deletes.`
      : `Unpinned ${what} — Save to record it.`);
  }, [toast]);

  /** Persist "always show pin icon" — a preference about the drawing's
   * chrome, so it rides localStorage next to the theme rather than the
   * artifact, and survives a reload. */
  useEffect(() => {
    try { localStorage.setItem(PIN_ALWAYS_KEY, pinAlways ? "1" : "0"); }
    catch { /* private mode */ }
  }, [pinAlways]);

  // e2e hook (v0.8): scene→screen through the app's own camera, so
  // browser tests click elements instead of guessing the fit transform.
  // Read-only; invisible to any session participant.
  useEffect(() => {
    (window as any).__sceneToScreen = (x: number, y: number) => {
      const a = appStateRef.current;
      if (!a) return null;
      const z = a.zoom?.value ?? 1;
      return { x: (x + a.scrollX) * z + (a.offsetLeft || 0),
               y: (y + a.scrollY) * z + (a.offsetTop || 0) };
    };
  }, []);

  /** Flip a drawn checkbox/toggle (v0.8). State is the truth
   * (customData.checked); the glyph mirror below is immediate visual
   * feedback — the server re-derives parts at commit either way, so the
   * verb and the picture cannot disagree past the next Save. */
  const flipControl = useCallback((hostId: string) => {
    const api = apiRef.current;
    if (!api) return;
    const scene = api.getSceneElements();
    const host: any = scene.find((e: any) => e.id === hostId && !e.isDeleted);
    if (!host) return;
    const kind = (host.customData || {}).kind;
    const now = !(host.customData || {}).checked;
    const cy = host.y + (host.height || 28) / 2;
    let els = scene.map((e: any) => {
      if (e.id === hostId)
        return { ...e, version: (e.version || 0) + 1,
                 customData: { ...(e.customData || {}), checked: now } };
      if (kind === "toggle" &&
          (e.customData || {}).thumb_of === hostId)
        // mirrors _toggle_thumb_x with TOGGLE_PILL_W = 36
        return { ...e, x: host.x + (now ? 8 + 36 - 12 - 2 : 10),
                 version: (e.version || 0) + 1 };
      return e;
    });
    if (kind === "checkbox") {
      const chkId = `${hostId}-chk`;
      const has = els.some((e: any) => e.id === chkId && !e.isDeleted);
      if (now && !has) {
        els = [...els, {
          id: chkId, type: "line", x: host.x + 11, y: cy - 1,
          width: 10, height: 8, points: [[0, 0], [4, 4], [10, -6]],
          strokeColor: "#1e1e1e", strokeWidth: 2, roughness: 1,
          opacity: 100, angle: 0, roundness: null,
          groupIds: [`${hostId}-grp`], frameId: host.frameId || null,
          boundElements: [], backgroundColor: "transparent",
          fillStyle: "solid", lastCommittedPoint: null,
          startBinding: null, endBinding: null,
          startArrowhead: null, endArrowhead: null,
          customData: { role: "decoration", chk_of: hostId,
                        author: "user" },
        }];
      } else if (!now && has) {
        els = els.filter((e: any) => e.id !== chkId);
      }
    }
    api.updateScene({ elements: restoreForRender(els) as any });
    setCtl((c) => (c && c.hostId === hostId ? { ...c, checked: now } : c));
    toast(now ? "Checked — Save to record it." :
      "Unchecked — Save to record it.");
  }, [toast]);

  const setElementTooltip = useCallback((elId: string, text: string) => {
    patchElement(elId, {}, { tooltip: text || null });
    toast(text ? "Tooltip set — Save to record it."
      : "Tooltip removed — Save to record it.");
  }, [patchElement, toast]);

  const openTooltipEditor = useCallback((el: any) => {
    setTooltipEdit({
      elId: el.id, initial: el.customData?.tooltip || "",
      el: { x: el.x, y: el.y, width: el.width, height: el.height },
    });
  }, []);

  /* right-click on an element: Excalidraw's own menu opens untouched
     (copy, delete, z-order…) and the tooltip actions are APPENDED to it.
     There is no public menu-extension API, so we inject styled items
     into the rendered menu — replacing the native menu was a v0.3
     regression (user report). */
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const closeNativeMenu = () => {
      // Excalidraw closes its menu on Escape / outside pointerdown
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    };
    const h = (e: MouseEvent) => {
      if (viewingRef.current != null) return;
      const hit = hitAtClient(e.clientX, e.clientY);
      let tries = 0;
      const inject = () => {
        const menu = wrap.querySelector<HTMLElement>("ul.context-menu") ||
          document.querySelector<HTMLElement>("ul.context-menu");
        if (!menu) {
          if (++tries < 10) setTimeout(inject, 30);
          return;
        }
        if (menu.querySelector(".wg-tooltip-item, .wg-pin-item")) return;
        // collapse the bulkiest native groups into hover flyouts (user
        // request: the flat menu ate too much vertical space). Moving
        // the original <li> nodes keeps their React handlers and
        // shortcut hints intact.
        const labelOf = (li: Element) =>
          li.querySelector(".context-menu-item__label")?.textContent || "";
        const collapse = (test: (l: string) => boolean, title: string) => {
          const lis = Array.from(menu.children).filter(
            (li) => li.tagName === "LI" && test(labelOf(li)));
          if (lis.length < 2) return;
          const parent = document.createElement("li");
          parent.className = "wg-submenu-parent";
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "context-menu-item";
          const span = document.createElement("span");
          span.className = "context-menu-item__label";
          span.textContent = title;
          const arrow = document.createElement("span");
          arrow.className = "context-menu-item__shortcut";
          arrow.textContent = "▸";
          btn.append(span, arrow);
          btn.addEventListener("click", (ev) => {
            ev.stopPropagation();
            ev.preventDefault();
          });
          const sub = document.createElement("ul");
          sub.className = "context-menu wg-submenu";
          // position:fixed escapes the popover's overflow clipping (a
          // left/right flyout otherwise gets cut at the menu's box);
          // :hover still reaches it because it stays a DOM child
          parent.addEventListener("mouseenter", () => {
            const r = parent.getBoundingClientRect();
            const fitsRight = r.right + 200 <= window.innerWidth;
            sub.style.left = (fitsRight ? r.right : r.left - 180) + "px";
            sub.style.top = Math.max(8, Math.min(
              r.top - 8, window.innerHeight - 40 * lis.length - 24)) + "px";
          });
          parent.append(btn, sub);
          menu.insertBefore(parent, lis[0]);
          lis.forEach((li) => sub.appendChild(li));
        };
        if (!menu.querySelector(".wg-submenu-parent")) {
          collapse((l) => l.startsWith("Copy to clipboard as"), "Copy as…");
          collapse((l) => ["Send backward", "Bring forward",
                           "Send to back", "Bring to front"].includes(l),
                   "Arrange");
        }
        const api = apiRef.current;
        const scene: any[] = api
          ? api.getSceneElements().filter((x: any) => !x.isDeleted) : [];
        // WHICH elements the menu is about. Right-click has already made
        // the selection — Excalidraw's own hit test runs first, and it is
        // fill-aware where `hitAtClient` is a bbox test, so the two
        // disagree on any transparent-background shape. Trust the
        // selection; fall back to our hit only when there is none (the
        // menu opened on something Excalidraw declined to select).
        const selMap = api?.getAppState()?.selectedElementIds || {};
        const selNow = Object.keys(selMap).filter((k) => selMap[k]);
        const ids = (selNow.length ? selNow : hit ? [hit.id] : [])
          .filter((id) => scene.some((x) => x.id === id));
        const targets = pinTargets(
          scene.filter((x) => ids.includes(x.id)), scene);
        const targetIds = targets.map((x) => x.id);
        const mk = (label: string, fn: () => void, cls = "wg-tooltip-item") => {
          const li = document.createElement("li");
          li.className = cls;
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "context-menu-item";
          const span = document.createElement("span");
          span.className = "context-menu-item__label";
          span.textContent = label;
          btn.appendChild(span);
          btn.addEventListener("click", (ev) => {
            ev.stopPropagation();
            closeNativeMenu();
            fn();
          });
          li.appendChild(btn);
          return li;
        };
        /** One flyout holding the pin action and its preference — the
         * user asked for a group, not two loose items, and it reuses the
         * same DOM shape `collapse` builds for the native groups. */
        const mkGroup = (title: string,
          items: Array<{ label: string; fn: () => void }>) => {
          const parent = document.createElement("li");
          parent.className = "wg-submenu-parent wg-pin-item";
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "context-menu-item";
          const span = document.createElement("span");
          span.className = "context-menu-item__label";
          span.textContent = title;
          const arrow = document.createElement("span");
          arrow.className = "context-menu-item__shortcut";
          arrow.textContent = "▸";
          btn.append(span, arrow);
          btn.addEventListener("click", (ev) => {
            ev.stopPropagation();
            ev.preventDefault();
          });
          const sub = document.createElement("ul");
          sub.className = "context-menu wg-submenu";
          parent.addEventListener("mouseenter", () => {
            const r = parent.getBoundingClientRect();
            const fitsRight = r.right + 200 <= window.innerWidth;
            sub.style.left = (fitsRight ? r.right : r.left - 180) + "px";
            sub.style.top = Math.max(8, Math.min(
              r.top - 8, window.innerHeight - 40 * items.length - 24)) + "px";
          });
          for (const it of items)
            sub.appendChild(mk(it.label, it.fn, "wg-pin-item"));
          parent.append(btn, sub);
          menu.appendChild(parent);
        };
        const sep = document.createElement("li");
        sep.className = "context-menu-item-separator wg-tooltip-item";
        menu.appendChild(sep);
        if (targets.length) {
          const allPinned = targets.every((x) => x.locked);
          const what = targets.length === 1
            ? "this element" : `${targets.length} elements`;
          mkGroup("📌 Pin to Canvas", [
            { label: allPinned ? `Unpin ${what}` : `Pin ${what} to canvas`,
              fn: () => setPinned(targetIds, !allPinned) },
            { label: `${pinAlwaysRef.current ? "☑" : "☐"} Always show pin icon`,
              fn: () => setPinAlways((v) => !v) },
          ]);
        } else {
          // the empty-canvas arm. Scope is exactly what is drawn NOW:
          // anything added later arrives unpinned, which is the only
          // reading of "all" that does not quietly become a mode.
          const all = scene.filter(pinnable);
          if (all.length) {
            const allPinned = all.every((x) => x.locked);
            const allIds = all.map((x) => x.id);
            menu.appendChild(mk(
              allPinned ? "📌 Unpin ALL Elements"
                : "📌 Pin ALL Elements to Canvas",
              () => setPinned(allIds, !allPinned), "wg-pin-item wg-pin-all"));
          }
        }
        if (hit) {
          const live = targets.find((x: any) => x.id === hit.id) ||
            scene.find((x: any) => x.id === hit.id) || hit;
          const has = !!live.customData?.tooltip;
          menu.appendChild(mk(has ? "✎ Edit tooltip…" : "🛈 Add tooltip…",
            () => openTooltipEditor(live)));
          if (has) menu.appendChild(
            mk("Remove tooltip", () => setElementTooltip(live.id, "")));
        }
      };
      setTimeout(inject, 0);
    };
    wrap.addEventListener("contextmenu", h);
    return () => wrap.removeEventListener("contextmenu", h);
  }, [hitAtClient, openTooltipEditor, setElementTooltip, setPinned]);

  /* hover → tooltip card after a beat; any movement re-arms */
  const onWrapPointerMove = useCallback((e: React.PointerEvent) => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    if (hoverTip) setHoverTip(null);
    if (e.buttons !== 0 || walkIdx != null) return;
    const { clientX, clientY } = e;
    // the tack appears on hover unless the preference pins it on. The hit
    // test is skipped outright while nothing on this artifact is pinned,
    // so an unpinned drawing pays nothing at pointer rate; `hitAtClient`
    // does not filter `locked`, which is why hovering a pinned element
    // still finds it when nothing else in the app can select it.
    const pinnedNow = pinnedIdsRef.current;
    const over = pinnedNow.size ? hitAtClient(clientX, clientY) : null;
    const pinId = over && pinnedNow.has(over.id) ? over.id : null;
    setHoverPinId((h) => (h === pinId ? h : pinId));
    hoverTimerRef.current = setTimeout(() => {
      const el = hitAtClient(clientX, clientY);
      const text = el?.customData?.tooltip;
      const p = sceneAt(clientX, clientY);
      if (text && p) setHoverTip({ x: p.wx + 14, y: p.wy + 12, text });
    }, 450);
  }, [hitAtClient, sceneAt, hoverTip, walkIdx]);

  const onWrapPointerLeave = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHoverTip(null);
    setHoverPinId(null);
  }, []);

  /** Revert all: drop every unsaved buffer and restore the currently
   * selected history node (live head, or the checkout point). */
  const revertAll = useCallback(async () => {
    setRevertPrompt(false);
    buffersRef.current = {};
    setDirtyMap({});
    const cur = currentRef.current;
    const co = stateRef.current?.checkout_revn;
    if (co != null && cur) {
      try {
        const r = await apiGet(`/api/artifact/${cur}?at=${co}`);
        const els = r.elements || [];
        loadedHashRef.current[cur] = JSON.stringify(els);
        const restored = loadScene(els, false);
        baselineFpRef.current[cur] = fingerprint(restored);
        pendingBaselineRef.current = cur;
        toast(`Restored to save #${co} — nothing was recorded.`);
      } catch (e: any) {
        toast(e.message);
      }
    } else {
      const s = await refresh();
      if (cur && s) showArtifact(cur, s, true);
      toast(`Restored to save #${stateRef.current?.head_revn ?? "?"} — nothing was recorded.`);
    }
  }, [refresh, showArtifact, loadScene, toast]);

  /* attachable documents for the inspector */
  useEffect(() => {
    apiGet("/api/docs").then((r) => setDocsList(r.docs || [])).catch(() => {});
  }, [state?.revn]);

  /* tooltip presence dots — discoverability for hover-only content */
  const tooltipDots = useMemo(() => {
    if (!currentArtifact) return [];
    const els = (viewingRevn != null
      ? viewScenes[currentArtifact]
      : buffersRef.current[currentArtifact] ||
        artifacts[currentArtifact]?.elements) || [];
    return els.filter((e: any) => !e.isDeleted && e.customData?.tooltip);
  }, [currentArtifact, state, viewingRevn, viewScenes, dirtyMap]);

  const selEl = useMemo(() => {
    if (!selElId || !apiRef.current) return null;
    return apiRef.current.getSceneElements()
      .find((e: any) => e.id === selElId && !e.isDeleted) || null;
  }, [selElId, dirtyMap, state, camera]);

  /** Every pinned element on the artifact in front of us. */
  const pinnedEls = useMemo(() => {
    if (viewingRevn != null || !apiRef.current) return [];
    return apiRef.current.getSceneElements()
      .filter((e: any) => !e.isDeleted && e.locked);
  }, [geomTick, viewingRevn, currentArtifact, dirtyMap, state]);
  pinnedIdsRef.current = new Set(pinnedEls.map((e: any) => e.id));

  /** The live selection, reduced to what a pin acts on — the same rule
   * the context menu applies, so the tack and the menu can never be
   * talking about different elements. */
  const selOwners = useMemo(() => {
    if (!selIds.length || !apiRef.current) return [];
    const live = apiRef.current.getSceneElements()
      .filter((e: any) => !e.isDeleted);
    const sel = live.filter((e: any) => selIds.includes(e.id));
    return pinTargets(sel, live).map((e: any) => e.id);
  }, [selIds, geomTick]);

  /** Which pinned elements are showing their tack right now.
   *
   * Not all of them by default: a badge on every pinned element at all
   * times competes with the drawing, and the drawing is the point. Hover
   * and selection are the moments the user is asking about one element —
   * and the preference is there for the sessions where they would rather
   * see the whole shape of what is settled. */
  const tacks = useMemo(() => {
    if (pinAlways) return pinnedEls;
    const show = new Set<string>(selOwners);
    if (hoverPinId) show.add(hoverPinId);
    return pinnedEls.filter((e: any) => show.has(e.id));
  }, [pinnedEls, pinAlways, selOwners, hoverPinId]);

  /** The one tack a MULTI-selection gets, on its union box's corner.
   *
   * Two or more selected elements drove no UI at all before this — every
   * anchored surface in the app is `selIds.length === 1 ? … : null`. A
   * per-element tack each would stack N badges over a region the user is
   * treating as one thing, so the group gets one badge and one verb. */
  const groupTack = useMemo(() => {
    if (viewingRevn != null || selOwners.length < 2 || !apiRef.current) return null;
    const els = apiRef.current.getSceneElements()
      .filter((e: any) => !e.isDeleted && selOwners.includes(e.id));
    const box = unionBox(els);
    if (!box) return null;
    const b = screenBox(box, camera);
    return { ids: els.map((e: any) => e.id), n: els.length,
             pinned: els.every((e: any) => e.locked),
             style: { left: b.left + b.width, top: b.top + b.height } };
  }, [selOwners, geomTick, viewingRevn, camera]);

  /* ---------------- Phase 6: wireframing power tools ---------------- */

  useEffect(() => {
    try { localStorage.setItem("wysiwyg-theme", theme); } catch { /* private mode */ }
  }, [theme]);

  /** PNG of the artifact as it stands. Exports always render light: the
   * canvas theme is a reading preference, the artifact is paper. */
  const exportPng = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const els = api.getSceneElements().filter((e: any) => !e.isDeleted);
    if (!els.length) { toast("Nothing to export — this artifact is empty."); return; }
    try {
      const blob = await exportToBlob({
        elements: restoreForRender(els) as any,
        // clip:false — frame membership must not crop annotations that
        // spill outside their screen frame (v0.3 assessment); padding
        // keeps estimated text extents from being cut at the edge
        appState: {
          viewBackgroundColor: "#faf8f2", exportWithDarkMode: false,
          frameRendering: { enabled: true, name: true, outline: true, clip: false },
        },
        files: api.getFiles ? api.getFiles() : null,
        mimeType: "image/png",
        exportPadding: 40,
      } as any);
      const name = String(artifacts[currentArtifact!]?.name || currentArtifact || "artifact")
        .replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "artifact";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${name}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast(`Exported ${name}.png`);
    } catch (e: any) {
      toast(`Export failed: ${e.message}`);
    }
  }, [artifacts, currentArtifact, toast]);

  const doTidy = useCallback(async () => {
    if (Object.values(dirtyRef.current).some(Boolean)) {
      toast("Save first — tidy works on the committed state.");
      return;
    }
    const aid = currentRef.current;
    if (!aid) return;
    try {
      const r = await apiPost("/api/tidy", { artifact: aid });
      toast(`✨ ${r.headline} (save #${r.revn} — revertable like any other)`);
      await refresh();
    } catch (e: any) {
      toast(`Tidy failed: ${e.message}`);
    }
  }, [refresh, toast]);

  const zoomFit = useCallback(() => {
    const api = apiRef.current;
    if (!api) return;
    const els = api.getSceneElements().filter((e: any) => !e.isDeleted);
    if (!els.length) { toast("Nothing to fit — this artifact is empty."); return; }
    api.scrollToContent(els, { fitToViewport: true, viewportZoomFactor: 0.85 });
  }, [toast]);

  const insertFrame = useCallback((w: number, h: number, label: string) => {
    const { x, y } = dropClear(w, h, true);
    const id = `frame-user-${Date.now().toString(36)}`;
    const els = [{
      id, type: "frame", x: Math.round(x), y: Math.round(y),
      width: w, height: h, name: `SCREEN — ${label}`,
      angle: 0, strokeColor: "#bbb", backgroundColor: "transparent",
      fillStyle: "solid", strokeWidth: 1, strokeStyle: "solid",
      roughness: 0, opacity: 100, groupIds: [], frameId: null,
      roundness: null, boundElements: [], locked: false,
    }];
    insertElements(els);
    revealInsert(els);
    setInsertOpen(false);
    toast(`${label} frame added — rename it on canvas, Save to record it.`);
  }, [dropClear, insertElements, revealInsert, toast]);

  /** Paste a mermaid diagram as the USER's own drawing (WP9).
   *
   * Deliberately different from the agent's `mermaid` seed command:
   * that maps flowchart/erDiagram to skill grammar through apply; this
   * converts any natively-supported mermaid type to raw shapes that
   * arrive exactly like shapes you drew — dirty canvas, your Save,
   * your authorship. */
  const importMermaid = useCallback(async () => {
    const def = mermaidText.trim();
    if (!def) return;
    setMermaidBusy(true);
    try {
      const [{ parseMermaidToExcalidraw }, { convertToExcalidrawElements }] =
        await Promise.all([
          import("@excalidraw/mermaid-to-excalidraw"),
          import("@excalidraw/excalidraw"),
        ]);
      await ensureElkLayouts();
      const { elements: skeletons, files } = await parseMermaidToExcalidraw(def, {
        themeVariables: { fontSize: `${IMPORT_FONT_SIZE}px` },
      });
      const converted = widenChoppedContainers(
        skeletons as any, convertToExcalidrawElements);
      if (!converted.length) throw new Error("the diagram converted to nothing");
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const e of converted) {
        minX = Math.min(minX, e.x); minY = Math.min(minY, e.y);
        maxX = Math.max(maxX, e.x + (e.width || 0));
        maxY = Math.max(maxY, e.y + (e.height || 0));
      }
      // r5-10: land CLEAR of what is already drawn. The import used to
      // centre on the viewport, which on any artifact you were looking
      // at meant 36 shapes straight on top of the one you had open —
      // "your paste landed directly on the Daily Run". Right of
      // everything live, top-aligned with it, and the view follows.
      const live = (apiRef.current?.getSceneElements() || [])
        .filter((e: any) => !e.isDeleted);
      let dx: number, dy: number;
      if (live.length) {
        let right = -Infinity, top = Infinity;
        for (const e of live) {
          right = Math.max(right, e.x + (e.width || 0));
          top = Math.min(top, e.y);
        }
        dx = Math.round(right + IMPORT_GAP - minX);
        dy = Math.round(top - minY);
      } else {
        const c = sceneCenter();
        dx = Math.round(c.x - (minX + maxX) / 2);
        dy = Math.round(c.y - (minY + maxY) / 2);
      }
      const shifted = converted.map((e: any) => ({ ...e, x: e.x + dx, y: e.y + dy }));
      if (files && apiRef.current?.addFiles) apiRef.current.addFiles(Object.values(files));
      insertElements(shifted);
      apiRef.current?.scrollToContent(shifted as any,
        { fitToViewport: true, viewportZoomFactor: 0.85 });
      setMermaidOpen(false);
      setMermaidText("");
      toast(live.length
        ? "Diagram imported clear of what was already there — it's your drawing now; Save to record it."
        : "Diagram imported — it's your drawing now; Save to record it.");
    } catch (e: any) {
      toast(`Mermaid import failed: ${e?.message || e}`);
    } finally {
      setMermaidBusy(false);
    }
  }, [mermaidText, insertElements, sceneCenter, toast]);

  const insertTemplate = useCallback((kind: string, name: string) => {
    // built at the origin first: the template's own size is what decides
    // where it fits, and only the built elements know it (each archetype
    // carries its own extent, and a screen-sized one clears more than a
    // note does).
    const built = templateElements(kind, 0, 0);
    if (!built.length) return;
    const bs = built.map(elBox);
    const bx = Math.min(...bs.map((b) => b.x));
    const by = Math.min(...bs.map((b) => b.y));
    const spot = dropClear(Math.max(...bs.map((b) => b.x + b.w)) - bx,
                           Math.max(...bs.map((b) => b.y + b.h)) - by);
    const dx = Math.round(spot.x - bx), dy = Math.round(spot.y - by);
    const els = built.map((e) => ({ ...e, x: e.x + dx, y: e.y + dy }));
    insertElements(els);
    revealInsert(els);
    setInsertOpen(false);
    toast(`${name} added — edit the labels, Save, and the agent reads it back.`);
  }, [dropClear, insertElements, revealInsert, toast]);

  const labelSave = useCallback((revn: number, current: string) => {
    const v = window.prompt("Bookmark this save:", current || "");
    if (v == null) return;
    const label = v.trim();
    apiPost("/api/save-label", { revn, label })
      .then(() => {
        toast(label ? `Bookmarked save #${revn} — 🔖 ${label}` : `Bookmark cleared on save #${revn}`);
        refresh();
      })
      .catch((e) => toast(e.message));
  }, [refresh, toast]);

  /* -------- onion skin: keep the old revision as an overlay -------- */
  const compareWithLive = useCallback(() => {
    const revn = viewingRef.current;
    if (revn == null) return;
    setGhost({ revn, scenes: { ...viewScenesRef.current } });
    backToLive();
  }, [backToLive]);

  const ghostEls = useMemo(() => {
    if (!ghost || !currentArtifact) return [];
    return (ghost.scenes[currentArtifact] || []).filter((e: any) => !e.isDeleted);
  }, [ghost, currentArtifact]);

  /* -------- prototype walk -------- */
  const framesOf = useCallback(() => {
    const api = apiRef.current;
    if (!api) return [] as any[];
    return api.getSceneElements()
      .filter((e: any) => e.type === "frame" && !e.isDeleted)
      .slice()
      .sort((a: any, b: any) => (a.x - b.x) || (a.y - b.y));
  }, []);

  const walkTo = useCallback((idx: number) => {
    const api = apiRef.current;
    const fs = framesOf();
    if (!api || !fs.length) return;
    const i = ((idx % fs.length) + fs.length) % fs.length;
    const f = fs[i];
    const members = api.getSceneElements()
      .filter((e: any) => e.frameId === f.id && !e.isDeleted);
    api.scrollToContent([f, ...members], { fitToViewport: true, viewportZoomFactor: 0.85 });
    setWalkIdx(i);
  }, [framesOf]);

  const hasFrames = useMemo(() => {
    if (!currentArtifact) return false;
    const els = (viewingRevn != null
      ? viewScenes[currentArtifact]
      : buffersRef.current[currentArtifact] || artifacts[currentArtifact]?.elements) || [];
    return els.some((e: any) => e.type === "frame" && !e.isDeleted);
  }, [currentArtifact, state, viewingRevn, viewScenes, dirtyMap]);

  // walking is per-artifact — switching views ends the walk rather than
  // silently pointing the index at a frame that isn't there
  useEffect(() => { setWalkIdx(null); }, [currentArtifact]);

  useEffect(() => {
    if (walkIdx == null) return;
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (/^(INPUT|TEXTAREA)$/.test(t.tagName) || t.isContentEditable)) return;
      if (e.key === "Escape") setWalkIdx(null);
      else if (e.key === "ArrowRight") { e.preventDefault(); walkTo(walkIdx + 1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); walkTo(walkIdx - 1); }
      else return;
      e.stopPropagation();
    };
    // capture: Excalidraw binds arrow keys for nudging a selection, and it
    // would eat them before the walk ever saw them
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  }, [walkIdx, walkTo]);

  const walkFrames = walkIdx != null ? framesOf() : [];

  const emptyCanvas =
    !viewingRevn && currentArtifact != null &&
    !(dirtyMap[currentArtifact]) &&
    !(artifacts[currentArtifact]?.elements || []).length;

  const whoseMove = state?.whose_move || "agent";
  const viewingSave = viewingRevn != null ? state?.saves?.find((s: any) => s.revn === viewingRevn) : null;

  /* ---------------- render ---------------- */
  return (
    <div className="app">
      {/* ============ round header ============ */}
      <header className="header">
        <div className="brand"><span className="logo" /> WYSIWYG&nbsp;Grilling</div>
        {/* the round counter only advances when an agent revision LANDS,
            so under `pulled` cadence it sits still while the agent keeps
            moving — the header read "Round 5" ten exchanges in. The "+"
            says the number is behind rather than quietly lying. */}
        <div className="round" title={pending.length
          ? `${pending.length} agent revision(s) held — the round advances when they land`
          : undefined}>
          Round {state?.round ?? "…"}{pending.length ? "+" : ""} — <span className="whose">{whoseMove === "user" ? "your move" : "agent reading"}</span>
        </div>
        <div className="crumbs">
          {state?.project || "…"} · <b>{currentArtifact ? artifacts[currentArtifact]?.name || currentArtifact : "no artifact yet"}</b>
        </div>
        <div className="spacer" />
        <span className={`chip ${whoseMove === "agent" ? "agent-busy" : "your-move"}`}>
          <span className="dot" />
          {viewingRevn != null ? "viewing history" : whoseMove === "agent" ? "agent is reading…" : pending.length ? "revision waiting" : "your move"}
        </span>
        <div className="cadence" title="canvas update cadence: per-round = agent revisions land automatically on a clean canvas; pulled = they always wait behind the banner">
          <button className={cadence === "per-round" ? "active" : ""} onClick={() => apiPost("/api/config", { patch: { canvas_updates: "per-round" } }).then(refresh)}>per-round</button>
          <button className={cadence === "pulled" ? "active" : ""} onClick={() => apiPost("/api/config", { patch: { canvas_updates: "pulled" } }).then(refresh)}>pulled</button>
        </div>
        <button className="icon-btn" title="add a sticky note (yours — reads as a requirement)" disabled={viewingRevn != null} onClick={addStickyNote}>🗒 note</button>
        <button className="icon-btn" title="pin a question on the selected element — the agent answers on its move" disabled={viewingRevn != null} onClick={askUserPin}>❓ ask</button>
        <div className="insert-wrap">
          <button className="icon-btn" title="drop a screen frame or an archetype template where you are looking — clear of what is already drawn there"
            disabled={viewingRevn != null}
            onClick={() => setInsertOpen((o) => !o)}>+ insert ▾</button>
          {insertOpen && (
            <div className="insert-menu" onMouseLeave={() => setInsertOpen(false)}>
              <div className="group">Screen frames</div>
              <div className="item" onClick={() => insertFrame(360, 640, "Phone")}>
                Phone frame <span className="dim">360×640</span></div>
              <div className="item" onClick={() => insertFrame(768, 1024, "Tablet")}>
                Tablet frame <span className="dim">768×1024</span></div>
              <div className="item" onClick={() => insertFrame(1280, 800, "Desktop")}>
                Desktop frame <span className="dim">1280×800</span></div>
              <div className="group">Archetypes</div>
              {TEMPLATES.map((t) => (
                <div key={t.id} className="item" onClick={() => insertTemplate(t.id, t.name)}>
                  {t.name} <span className="dim">{t.hint}</span></div>
              ))}
            </div>
          )}
        </div>
        <button className="icon-btn" title="paste a mermaid diagram as your own drawing (flowchart, sequence, class, ER, state)"
          disabled={viewingRevn != null} onClick={() => setMermaidOpen(true)}>⌗ mermaid</button>
        <button className="icon-btn" title="snap to the grid, re-route arrows, normalize z-order — lands as an ordinary revision you can revert"
          disabled={viewingRevn != null} onClick={doTidy}>✨ tidy</button>
        <button className="icon-btn" title="copy a context update to paste to the agent" onClick={copyContextUpdate}>⧉ context</button>
        <button className="icon-btn" title="download this artifact as a PNG" onClick={exportPng}>⇓ png</button>
        <button className="icon-btn" title="zoom to fit everything on this artifact" onClick={zoomFit}>⤢ fit</button>
        <button className="icon-btn" title={hasFrames ? "walk the screens like a prototype — ←/→ to move, Esc to exit" : "no screen frames on this artifact yet — insert one first"}
          disabled={!hasFrames} onClick={() => (walkIdx == null ? walkTo(0) : setWalkIdx(null))}>▶ walk</button>
        <button className="icon-btn" title={theme === "dark" ? "switch the canvas back to paper" : "dark canvas (the chrome stays dark either way)"}
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "☀" : "🌙"}</button>
        <button className="icon-btn revert-btn"
          disabled={!anyDirty || viewingRevn != null}
          onClick={() => setRevertPrompt(true)}
          title={anyDirty ? "discard ALL unsaved changes and restore the current save" : "no unsaved changes to revert"}
        >↺ revert</button>
        <button
          className="save-btn"
          disabled={!anyDirty || viewingRevn != null}
          onClick={() => doSave()}
          title={viewingRevn != null ? "read-only while viewing history" : anyDirty ? "checkpoint your edits — the agent reads them at Save" : "no unsaved changes"}
        >
          Save
          {anyDirty && viewingRevn == null && <span className="dirty-dot" />}
        </button>
      </header>

      {/* ============ banners ============ */}
      {viewingRevn != null && (
        <div className="view-bar">
          <span>👁 Viewing save <b>#{viewingRevn}{viewingSave ? ` · ${viewingSave.short_id}` : ""}</b> ({viewingSave?.headline || "…"}) — read-only. Nothing is lost; “Back to live” returns to the present.</span>
          <div className="grow" style={{ flex: 1 }} />
          <button className="icon-btn" onClick={compareWithLive}
            title="return to the present, keeping this save as a translucent outline over the live canvas">👻 Compare with live</button>
          <button className="icon-btn" onClick={workFromHere}>✎ Work from here</button>
          <button className="icon-btn" onClick={backToLive}>↩ Back to live</button>
        </div>
      )}
      {staleInfo && (
        <div className="banner stale">
          <span>⚠ This canvas moved on (now at revn {staleInfo.head_revn}) — another tab or the agent saved first. Refresh to continue; your edits here stay until you choose.</span>
          <div className="grow" />
          <button onClick={async () => { setStaleInfo(null); setDirtyMap({}); buffersRef.current = {}; const s = await refresh(); if (currentRef.current && s) showArtifact(currentRef.current, s, true); }}>Load latest (discard this tab's edits)</button>
          <button onClick={() => setStaleInfo(null)}>Keep editing</button>
        </div>
      )}
      {state?.rollback && (
        <div className="banner rollback">
          <span>↺ The canvas rolled back past save #{state.rollback.matches_revn} (history head is #{state.rollback.head_revn}) — a git revert? The agent will ask; or re-anchor now.</span>
          <div className="grow" />
          <button onClick={() => apiPost("/api/rollback/accept", {}).then(() => { toast("Re-anchored."); refresh(); })}>Re-anchor here</button>
        </div>
      )}
      {freshPins.length > 0 && (
        <div className="banner pin-only">
          <span>❓ The agent asked {freshPins.length === 1 ? "a question" : `${freshPins.length} questions`} — no canvas changes. Answer in the rail, or in chat.</span>
          <div className="grow" />
          <button onClick={() => setSeenPins((d) => [...d, ...freshPins.map((p: any) => p.id)])}>Got it</button>
        </div>
      )}
      {narrOpen && lastSave?.summary && (
        <div className="banner narration">
          <span>
            📖 Save #{lastSave.revn} recorded: <b>{lastSave.summary.headline}</b>
            {Object.keys(lastSave.summary.verb_counts || {}).length > 1 &&
              ` — ${Object.entries(lastSave.summary.verb_counts)
                .map(([k, v]) => `${v}× ${k.replace(/_/g, " ")}`)
                .join(", ")}`}
            {lastSave.summary.suppressed
              ? ` (${lastSave.summary.suppressed} low-signal suppressed)` : ""}
            {lastSave.tripwires?.length
              ? ` · ⚠ ${lastSave.tripwires.length} tripwire${lastSave.tripwires.length > 1 ? "s" : ""} fired — see the rail`
              : ""}
            {" — the agent narrates from this on its move."}
          </span>
          <div className="grow" />
          <button onClick={() => setNarrOpen(false)}>Got it</button>
        </div>
      )}
      {(showAllPending ? pending : pending.slice(0, 1)).map((p: any) => (
        <div key={p.id} className="banner pending">
          <span>
            ✎ Agent revision waiting
            {p.artifact ? ` on ${p.artifact}` : ""}
            {p.note ? `: ${p.note}` : ""}
            {" — "}
            <span style={{ opacity: 0.8 }}>{opSummary(p.ops)}</span>
            {". Your unsaved work is safe either way."}
          </span>
          <div className="grow" />
          {!p.deferred && <>
            <button onClick={() => resolvePending(p.id, "apply_now")}>Apply now</button>
            <button onClick={() => resolvePending(p.id, "after_save")}>After I save</button>
          </>}
          <button onClick={() => resolvePending(p.id, "discard")} title="drop this revision — the agent is told">Discard</button>
          {p.deferred && <span style={{ opacity: 0.8 }}>lands after your next Save</span>}
        </div>
      ))}
      {pending.length > 1 && (
        <div className="banner pending">
          <span style={{ opacity: 0.85 }}>
            {showAllPending
              ? `${pending.length} revisions waiting`
              : `+${pending.length - 1} more revision${pending.length > 2 ? "s" : ""} waiting`}
          </span>
          <div className="grow" />
          <button onClick={() => setShowAllPending((v) => !v)}>
            {showAllPending ? "Collapse" : "Show all"}
          </button>
        </div>
      )}

      {/* ============ main ============ */}
      <div className="main">
        <div className="center">
          <div className={`canvas-wrap ${theme === "dark" ? "dark" : ""}`}
            ref={wrapRef}
            onPointerMove={onWrapPointerMove}
            onPointerLeave={onWrapPointerLeave}>
            <Excalidraw
              theme={theme}
              excalidrawAPI={(api) => {
                apiRef.current = api;
                // debug affordance for a local tool: lets a headless
                // agent (or a human devtools session) interrogate the
                // client-side scene when it diverges from server state
                (window as any).excalidrawAPI = api;
              }}
              onChange={onChange}
              onLinkOpen={(el: any, event: any) => {
                const link = el?.link || "";
                if (link.startsWith("artifact:")) {
                  event?.preventDefault?.();
                  showArtifact(link.slice("artifact:".length));
                }
              }}
              viewModeEnabled={viewingRevn != null}
              initialData={{
                appState: {
                  viewBackgroundColor: "#faf8f2",
                  currentItemFontFamily: 6,
                  currentItemRoughness: 1,
                  currentItemStrokeWidth: 1,
                },
                libraryItems,
              }}
              UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false } }}
            />
            {leaderLines.length > 0 && (
              <svg className="leader-lines">
                {leaderLines.map(({ a, b }, i) => (
                  <line key={i}
                    x1={(a.x + (a.width || 0) / 2 + camera.scrollX) * camera.zoom}
                    y1={(a.y + (a.height || 0) / 2 + camera.scrollY) * camera.zoom}
                    x2={(b.x + (b.width || 0) / 2 + camera.scrollX) * camera.zoom}
                    y2={(b.y + (b.height || 0) / 2 + camera.scrollY) * camera.zoom}
                  />
                ))}
              </svg>
            )}
            {/* onion skin. A DOM sibling of the canvas, never a scene
                element: an old revision drawn into the scene would land in
                the dirty buffer and get saved as if the user drew it. */}
            {ghostEls.length > 0 && (
              <svg className="ghost-layer">
                <g transform={`scale(${camera.zoom}) translate(${camera.scrollX} ${camera.scrollY})`}>
                  {ghostEls.map(ghostShape)}
                </g>
              </svg>
            )}
            {ghost && (
              <div className={`ghost-chip ${walkIdx != null ? "below-walk" : ""}`}>
                👻 comparing with save #{ghost.revn}
                {ghostEls.length === 0 && <em> — this artifact was empty then</em>}
                <button onClick={() => setGhost(null)}>clear</button>
              </div>
            )}
            {walkIdx != null && walkFrames.length > 0 && (
              <div className="walk-bar">
                <b>▶ {walkFrames[walkIdx]?.name || `Screen ${walkIdx + 1}`}</b>
                <span className="pos">{walkIdx + 1} / {walkFrames.length}</span>
                <span className="hint">←/→ screens · Esc exits · links jump between artifacts</span>
                <div style={{ flex: 1 }} />
                <button onClick={() => walkTo(walkIdx - 1)}>←</button>
                <button onClick={() => walkTo(walkIdx + 1)}>→</button>
                <button onClick={() => setWalkIdx(null)}>✕ exit</button>
              </div>
            )}
            {/* click opens the full-stack modal — the old hover card sat
                in the canvas's stacking context with pointer-events:none,
                so its buttons never received a click (capability
                assessment: 'Propagate' clicks fell through to the canvas) */}
            {tripTagsForCurrent.map(({ el, t }: any) => {
              // hug the shape, not its bounding box (r3-1) — the third
              // instance of that bug, found by grepping
              const p = screenPt(markerAnchor(el, 0, 0, "tr"), camera);
              return (
                <div
                  key={t.id}
                  className="trip-mark"
                  style={{ left: p.left, top: p.top - 10 }}
                  onClick={() => setAnchored({ kind: "tripwire", data: t,
                    el: { x: el.x, y: el.y, width: el.width, height: el.height } })}
                  title="mapping tripwire — click to read and answer"
                >
                  <span className="trip-q">?</span>
                </div>
              );
            })}
            {/* tooltip presence dots (v0.3) — hover-only content needs a
                discoverable tell. Anchored to the SHAPE's edge, not its
                bounding box (v0.6): on a diamond or an ellipse the
                bottom-right corner is empty canvas, so the dot floated
                ~40px clear of the decision node it belonged to and read
                as a stray mark. */}
            {tooltipDots.map((e: any) => {
              const p = screenPt(markerAnchor(e), camera);
              return (
                <div key={`tip-${e.id}`} className="tip-dot"
                  style={{ left: p.left - 4, top: p.top - 4 }}
                  title="has a tooltip — hover the element" />
              );
            })}
            {/* the thumb-tack. Excalidraw renders a locked element no
                differently at ALL — no badge, no border, no handles — so
                without this a pin is invisible, and since a locked element
                cannot be click-selected, unpinning would exist only in a
                menu. A `.canvas-wrap` child with its own pointer-events,
                never inside the canvas's stacking context: the v0.3 hover
                card lived there behind pointer-events gating and its
                buttons never received a click. */}
            {tacks.map((e: any) => {
              const p = screenPt(markerAnchor(e), camera);
              return (
                <button key={`tack-${e.id}`} type="button" className="pin-tack"
                  style={{ left: p.left, top: p.top }}
                  title="pinned to canvas — click to unpin"
                  onClick={() => setPinned([e.id], false)}>📌</button>
              );
            })}
            {groupTack && (
              <button type="button" className="pin-tack group"
                style={groupTack.style}
                title={groupTack.pinned
                  ? `${groupTack.n} pinned elements — click to unpin all`
                  : `pin all ${groupTack.n} selected elements to the canvas`}
                onClick={() => setPinned(groupTack.ids, !groupTack.pinned)}>
                {groupTack.pinned ? "📌" : "📍"}
              </button>
            )}
            {hoverTip && <TooltipCard x={hoverTip.x} y={hoverTip.y} text={hoverTip.text} />}
            {ctl && viewingRevn == null && (
              <AnchoredPopover
                style={anchorStyle(ctl.el, camera,
                  appStateRef.current?.width || 800)}
                onClose={() => setCtl(null)}>
                <button className="ctl-flip"
                  title={`${ctl.kind} — click to flip; Save records it`}
                  onClick={() => flipControl(ctl.hostId)}>
                  {ctl.checked ? "☑ → ☐  uncheck" : "☐ → ☑  check"}
                </button>
              </AnchoredPopover>
            )}
            {anchored && (
              <AnchoredPopover
                style={anchorStyle(anchored.el, camera,
                  appStateRef.current?.width || 800)}
                onClose={() => setAnchored(null)}>
                <AnchoredQuestion
                  kind={anchored.kind}
                  data={anchored.data}
                  onAnswer={viewingRevn != null ||
                    ["answered", "resolved", "dismissed", "pruned"]
                      .includes(anchored.data?.status)
                    ? null
                    : (a: string) => {
                        const path = anchored.kind === "pin"
                          ? "/api/pins/answer" : "/api/tripwires/answer";
                        apiPost(path, { id: anchored.data.id, answer: a })
                          .then(() => {
                            toast("Answer sent — the agent picks it up on its next move.");
                            refresh();
                          })
                          .catch((e) => toast(e.message));
                      }}
                  onFullStory={() => {
                    setDetailItem({ kind: anchored.kind, data: anchored.data });
                    setAnchored(null);
                  }}
                  onClose={() => setAnchored(null)}
                />
              </AnchoredPopover>
            )}
            {tooltipEdit && (
              <AnchoredPopover
                style={anchorStyle(tooltipEdit.el, camera,
                  appStateRef.current?.width || 800)}
                onClose={() => setTooltipEdit(null)}>
                <TooltipEditor
                  initial={tooltipEdit.initial}
                  onSave={(text) => setElementTooltip(tooltipEdit.elId, text)}
                  onClose={() => setTooltipEdit(null)}
                />
              </AnchoredPopover>
            )}
            {emptyCanvas && (
              <div className="empty-state">
                <div className="empty-card">
                  <h2>A blank canvas, on purpose</h2>
                  Draw anywhere and hit <b>Save</b> — the agent narrates what it
                  reads. Or answer in chat and let the agent seed the first
                  sketch. Nothing here is precious; everything is editable.
                </div>
              </div>
            )}
            {!artifactIds.length && !currentArtifact && (
              <div className="empty-state">
                <div className="empty-card">
                  <h2>No artifacts yet</h2>
                  The agent seeds the first wireframe, flow, or domain sketch
                  when the conversation needs one. You can also just draw —
                  your first Save creates an artifact.
                </div>
              </div>
            )}
          </div>
          <HistoryGraph
            saves={state?.saves || []}
            branches={state?.branches || []}
            head={state?.head || "main"}
            headRevn={state?.head_revn || 0}
            viewingRevn={viewingRevn}
            checkoutRevn={state?.checkout_revn ?? null}
            dirty={anyDirty}
            showArchived={false}
            onViewCommit={viewCommit}
            onSwitchBranch={(name) => {
              if (Object.values(dirtyRef.current).some(Boolean)) {
                toast("Unsaved edits — Save before switching branches.");
                return;
              }
              apiPost("/api/branch/switch", { name })
                .then(async () => {
                  setViewingRevn(null); viewingRef.current = null;
                  const s = await refresh();
                  if (currentRef.current && s) showArtifact(currentRef.current, s, true);
                  toast(`Switched to ⎇ ${name} — the agent will catch up on what changed.`);
                })
                .catch((e) => toast(e.message));
            }}
          />
        </div>

        <Rail
          state={state}
          currentArtifact={currentArtifact}
          viewingRevn={viewingRevn}
          onAnswerPin={(id, answer) => {
            apiPost("/api/pins/answer", { id, answer })
              .then(() => { toast("Answer sent — the agent picks it up on its next move."); refresh(); })
              .catch((e) => toast(e.message));
          }}
          onAnswerTripwire={answerTripwire}
          onGoto={gotoElement}
          onDismissPin={dismissPin}
          onOpenDetail={(kind, data) => setDetailItem({ kind, data })}
          onLabelSave={labelSave}
          onSwitchBranch={(name) => {
            if (Object.values(dirtyRef.current).some(Boolean)) {
              toast("Unsaved edits — Save before switching branches.");
              return;
            }
            apiPost("/api/branch/switch", { name })
              .then(async () => {
                setViewingRevn(null); viewingRef.current = null;
                const s = await refresh();
                if (currentRef.current && s) showArtifact(currentRef.current, s, true);
                toast(`Switched to ⎇ ${name}`);
              })
              .catch((e) => toast(e.message));
          }}
          onArchive={(name, archived) => {
            apiPost("/api/branch/archive", { name, archived })
              .then(() => { toast(archived ? `Archived ⎇ ${name} — hidden, never deleted.` : `Restored ⎇ ${name}`); refresh(); })
              .catch((e) => toast(e.message));
          }}
          onViewCommit={viewCommit}
          inspector={selEl && currentArtifact ? (
            <Inspector
              el={selEl}
              artifactType={artifacts[currentArtifact]?.artifact_type || "flow"}
              artifacts={artifacts}
              docs={docsList}
              disabled={viewingRevn != null}
              onPatch={(patch, cdPatch) => patchElement(selEl.id, patch, cdPatch)}
              onPin={(pinned) => setPinned([selEl.id], pinned)}
              onEditTooltip={() => openTooltipEditor(selEl)}
            />
          ) : null}
        />
      </div>

      {/* ============ filmstrip ============ */}
      <div className="filmstrip">
        <div className="artifact-dropdown">
          <button onClick={() => setDropOpen(!dropOpen)}>
            <span className="lbl">All artifacts ▴</span>
            <span>{currentArtifact ? artifacts[currentArtifact]?.name || currentArtifact : "—"}</span>
          </button>
          {dropOpen && (
            <div className="dropdown-menu" onMouseLeave={() => setDropOpen(false)}>
              {groupedArtifacts.length === 0 && <div className="group">nothing here yet</div>}
              {groupedArtifacts.map((g) => (
                <React.Fragment key={g.name}>
                  <div className="group">{g.name}</div>
                  {g.ids.map((aid) => (
                    <div key={aid} className={`item ${aid === currentArtifact ? "current" : ""}`}
                      onClick={() => { setDropOpen(false); showArtifact(aid); }}>
                      <span>{artifacts[aid]?.name || aid}</span>
                      <span className="tag" style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>
                        {artifacts[aid]?.artifact_type}
                        {artifacts[aid]?.tier === "extended" && <span className="tier-chip"> ext</span>}
                      </span>
                    </div>
                  ))}
                </React.Fragment>
              ))}
            </div>
          )}
        </div>
        {currentConceptName && (
          <div className="strip-scope" title="the filmstrip shows one concept's views — use All artifacts for the rest">
            {currentConceptName}
          </div>
        )}
        <div className="thumbs">
          {(() => {
            const renderThumb = (aid: string, dim: boolean) => {
              const hasTrip = openTripwires.some((t: any) => (t.changed || "").startsWith(aid + "#") || (t.sibling || "").startsWith(aid + "#"));
              const ld = (state?.lint_debt || {})[aid];
              const lintN = ld ? (ld.errors || 0) + (ld.warnings || 0) + (ld.notes || 0) : 0;
              const lintTier = ld?.errors ? "error" : ld?.warnings ? "warning" : "note";
              return (
                <div key={aid} className={`thumb ${aid === currentArtifact ? "current" : ""}`}
                  style={dim ? { opacity: 0.7 } : undefined}
                  onClick={() => showArtifact(aid)}
                  title={`${artifacts[aid]?.name || aid} (${artifacts[aid]?.artifact_type})${dim ? " — another concept" : ""}`}>
                  <SceneThumb elements={(viewingRevn != null ? viewScenes[aid] : buffersRef.current[aid] || artifacts[aid]?.elements) || []} />
                  <div className="tname">
                    {artifacts[aid]?.name || aid}
                    <span className="tkind">
                      {artifacts[aid]?.artifact_type}
                      {artifacts[aid]?.tier === "extended" ? " · ext" : ""}
                    </span>
                  </div>
                  <div className="badges">
                    {dirtyMap[aid] && <span className="badge dirty" title="unsaved edits" />}
                    {hasTrip && <span className="badge trip" title="open mapping tripwire" />}
                    {lintN > 0 && <span className={`badge lint ${lintTier}`} title={`${lintN} layout finding${lintN > 1 ? "s" : ""} — see the Layout rail section`}>{lintN}</span>}
                  </div>
                </div>
              );
            };
            return (
              <>
                {stripViews.here.map((aid: string) => renderThumb(aid, false))}
                {stripViews.rest.length > 0 && (
                  <div className="strip-scope" style={{ alignSelf: "center", opacity: 0.6 }}
                    title="artifacts of other concepts — every drawing is one click away">
                    ·&nbsp;·&nbsp;·
                  </div>
                )}
                {stripViews.rest.map((aid: string) => renderThumb(aid, true))}
              </>
            );
          })()}
          <div className="thumb suggest" onClick={() => setSuggestOpen(true)}>+ suggest<br />a view…</div>
        </div>
      </div>

      {/* ============ modals ============ */}
      {forkPrompt?.open && (
        <div className="modal-scrim"
          onClick={(e) => { if (e.target === e.currentTarget) setForkPrompt(null); }}>
          <div className="modal small">
            <h2>Name this branch</h2>
            <p>You're saving on top of save #{state?.checkout_revn} — this forks a new branch (the old line stays intact; nothing is lost).</p>
            <input autoFocus value={forkPrompt.name}
              onChange={(e) => setForkPrompt({ open: true, name: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") { const n = forkPrompt.name.trim() || "alt"; setForkPrompt(null); doSave(n); } }} />
            <div className="actions">
              <button className="ghost" onClick={() => setForkPrompt(null)}>Cancel</button>
              <button className="primary" onClick={() => { const n = forkPrompt.name.trim() || "alt"; setForkPrompt(null); doSave(n); }}>Fork &amp; Save</button>
            </div>
          </div>
        </div>
      )}
      {revertPrompt && (
        <div className="modal-scrim"
          onClick={(e) => { if (e.target === e.currentTarget) setRevertPrompt(false); }}>
          <div className="modal small">
            <h2>Revert all changes?</h2>
            <p>
              Discards every unsaved edit
              ({Object.values(dirtyMap).filter(Boolean).length} artifact{Object.values(dirtyMap).filter(Boolean).length === 1 ? "" : "s"})
              and restores {state?.checkout_revn != null
                ? <>the checked-out save <b>#{state.checkout_revn}</b></>
                : <>save <b>#{state?.head_revn}</b></>}.
              Nothing already saved is touched — but the unsaved edits are gone for good.
            </p>
            <div className="actions">
              <button className="ghost" autoFocus onClick={() => setRevertPrompt(false)}>Keep editing</button>
              <button className="danger" onClick={revertAll}>↺ Revert all changes</button>
            </div>
          </div>
        </div>
      )}
      {suggestOpen && (
        <div className="modal-scrim"
          onClick={(e) => { if (e.target === e.currentTarget) { setSuggestOpen(false); setSuggestText(""); } }}>
          <div className="modal small">
            <h2>Suggest a view</h2>
            <p>What question should a new view make tangible? The agent proposes views when there's a real seed — your ask lands in its next move.</p>
            <textarea rows={3} autoFocus value={suggestText} placeholder="e.g. What does checkout look like on mobile? A state diagram for orders?"
              onChange={(e) => setSuggestText(e.target.value)} />
            <div className="actions">
              <button className="ghost" onClick={() => { setSuggestOpen(false); setSuggestText(""); }}>Cancel</button>
              <button className="primary" disabled={!suggestText.trim()}
                onClick={() => {
                  apiPost("/api/suggest-view", { text: suggestText.trim(), concept: concepts.find((c: any) => (c.views || []).includes(currentArtifact))?.id })
                    .then(() => { toast("Suggestion recorded — the agent will pick it up."); setSuggestOpen(false); setSuggestText(""); })
                    .catch((e) => toast(e.message));
                }}>Send to agent</button>
            </div>
          </div>
        </div>
      )}

      {/* ============ toasts ============ */}
      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className="toast">{t.text}</div>)}
      </div>
      {docView && (
        <DocReader path={docView.path} content={docView.content}
          onClose={() => setDocView(null)} />
      )}
      {mermaidOpen && (
        <div className="modal-backdrop" onClick={() => setMermaidOpen(false)}>
          <div className="modal mermaid-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span className="modal-kind">⌗ import mermaid</span>
              <button className="modal-close" onClick={() => setMermaidOpen(false)}
                title="close">✕</button>
            </div>
            <p className="dim">
              Converts to plain shapes that arrive as <em>your</em> drawing —
              edit freely, then Save. Native types: flowchart, sequence,
              class, ER, state.
            </p>
            <textarea
              className="mermaid-input"
              autoFocus
              spellCheck={false}
              placeholder={"flowchart TD\n  A[Start] --> B{Decision}\n  B -->|yes| C[Do it]\n  B -->|no| D[Stop]"}
              value={mermaidText}
              onChange={(e) => setMermaidText(e.target.value)}
            />
            <div className="modal-foot">
              <button disabled={!mermaidText.trim() || mermaidBusy}
                onClick={importMermaid}>
                {mermaidBusy ? "Converting…" : "Import"}
              </button>
              <button onClick={() => setMermaidOpen(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
      {detailItem && (
        <QuestionModal
          kind={detailItem.kind}
          data={detailItem.data}
          debt={(state?.pin_debt || []).find(
            (d: any) => d.id === detailItem.data?.id)}
          onClose={() => setDetailItem(null)}
          onGoto={gotoRefOf(detailItem.data, detailItem.kind)
            ? () => gotoElement(gotoRefOf(detailItem.data, detailItem.kind)!)
            : null}
          onAnswer={viewingRevn != null ||
            ["answered", "resolved", "dismissed", "pruned"]
              .includes(detailItem.data?.status)
            ? null
            : (a: string) => {
                const path = detailItem.kind === "pin"
                  ? "/api/pins/answer" : "/api/tripwires/answer";
                apiPost(path, { id: detailItem.data.id, answer: a })
                  .then(() => {
                    toast("Answer sent — the agent picks it up on its next move.");
                    setDetailItem(null);
                    refresh();
                  })
                  .catch((e) => toast(e.message));
              }}
        />
      )}
    </div>
  );
}
