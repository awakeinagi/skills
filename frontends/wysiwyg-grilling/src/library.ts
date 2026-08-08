/** Low-fi wireframe vocabulary: the stencils that ship in the Excalidraw
 * library panel, and the archetype templates the "+ insert" menu drops at
 * the viewport centre.
 *
 * Both produce plain element skeletons — `restoreElements` fills in seeds,
 * versions and the rest on the way into the scene. Everything here matches
 * the canvas's low-fi look on purpose: one stroke colour, roughness 1, no
 * fills except the pale paper tint that marks a "filled" affordance. */

const INK = "#1e1e1e";
const TINT = "#f1eee6";
const LABEL_INK = "#4a4433";

type El = Record<string, any>;

/**
 * Shared defaults. Excalidraw repairs most of this, but library items are
 * restored through a stricter path than scene elements, so they carry the
 * full skeleton.
 * @param o Overrides merged over the skeleton.
 * @returns A fully-populated Excalidraw element record.
 */
function base(o: El): El {
  return {
    angle: 0,
    strokeColor: INK,
    backgroundColor: "transparent",
    fillStyle: "solid",
    strokeWidth: 1,
    strokeStyle: "solid",
    roughness: 1,
    opacity: 100,
    groupIds: [],
    frameId: null,
    roundness: null,
    seed: 1,
    version: 1,
    versionNonce: 1,
    isDeleted: false,
    boundElements: null,
    updated: 1,
    link: null,
    locked: false,
    ...o,
  };
}

/* ------------------------------------------------------------------ *
 * stencils                                                            *
 * ------------------------------------------------------------------ */

let seq = 0;
const sid = () => `wgs-${++seq}`;

const rect = (x: number, y: number, w: number, h: number, o: El = {}) =>
  base({ id: sid(), type: "rectangle", x, y, width: w, height: h, ...o });

const ell = (x: number, y: number, w: number, h: number, o: El = {}) =>
  base({ id: sid(), type: "ellipse", x, y, width: w, height: h, ...o });

const seg = (x1: number, y1: number, x2: number, y2: number, o: El = {}) =>
  base({
    id: sid(), type: "line", x: x1, y: y1,
    width: Math.abs(x2 - x1), height: Math.abs(y2 - y1),
    points: [[0, 0], [x2 - x1, y2 - y1]],
    lastCommittedPoint: null,
    startBinding: null, endBinding: null,
    startArrowhead: null, endArrowhead: null,
    ...o,
  });

/**
 * Free-standing text (no container) — the stencils never use bound labels,
 * so a stencil can be pulled apart without dragging a container along.
 * @param x Scene x of the text's top-left corner.
 * @param y Scene y of the text's top-left corner.
 * @param text The text content.
 * @param size Font size in px.
 * @param o Overrides merged over the defaults.
 * @returns A free-standing Excalidraw text element.
 */
const txt = (x: number, y: number, text: string, size = 14, o: El = {}) =>
  base({
    id: sid(), type: "text", x, y,
    width: Math.max(8, text.length * size * 0.58), height: Math.round(size * 1.25),
    text, originalText: text,
    fontSize: size, fontFamily: 6,
    textAlign: "left", verticalAlign: "top", lineHeight: 1.25,
    containerId: null, autoResize: true,
    ...o,
  });

const item = (name: string, elements: El[]) => ({
  id: `wg-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
  status: "published" as const,
  elements: elements as any,
  created: 1,
  name,
});

const checkboxRow = () => {
  const els: El[] = [];
  ["Remember me", "Send updates", "Public profile"].forEach((label, i) => {
    const y = i * 30;
    els.push(rect(0, y, 18, 18));
    if (i === 0) els.push(txt(3, y + 1, "✓", 14));
    els.push(txt(28, y + 1, label, 14));
  });
  return els;
};

const tabs = () => [
  rect(0, 0, 90, 34, { backgroundColor: TINT }),
  rect(90, 0, 90, 34),
  rect(180, 0, 90, 34),
  txt(26, 9, "Tab 1", 13),
  txt(116, 9, "Tab 2", 13),
  txt(206, 9, "Tab 3", 13),
];

const table = () => [
  rect(0, 0, 320, 140),
  rect(0, 0, 320, 32, { backgroundColor: TINT }),
  seg(107, 0, 107, 140),
  seg(214, 0, 214, 140),
  seg(0, 68, 320, 68),
  seg(0, 104, 320, 104),
  txt(10, 8, "Column", 12),
  txt(117, 8, "Column", 12),
  txt(224, 8, "Column", 12),
];

const modal = () => [
  rect(0, 0, 360, 240),
  rect(0, 0, 360, 40, { backgroundColor: TINT }),
  txt(14, 11, "Dialog title", 15),
  txt(330, 10, "✕", 16),
];

export const libraryItems = [
  item("Checkbox row", checkboxRow()),
  item("Slider", [
    seg(0, 10, 200, 10),
    ell(113, 3, 14, 14, { backgroundColor: TINT }),
  ]),
  item("Dropdown", [
    rect(0, 0, 200, 36),
    txt(12, 10, "Choose one", 14),
    txt(176, 9, "▾", 14),
  ]),
  item("Tabs", tabs()),
  item("Table", table()),
  item("Breadcrumb", [txt(0, 0, "Home › Section › Page", 14)]),
  item("KPI card", [
    rect(0, 0, 180, 100),
    txt(16, 18, "1,284", 28),
    txt(16, 64, "Active users", 12),
  ]),
  item("Modal", modal()),
  item("Image", [
    rect(0, 0, 200, 140),
    seg(0, 0, 200, 140),
    seg(0, 140, 200, 0),
  ]),
  item("Avatar", [
    ell(0, 0, 64, 64),
    ell(22, 12, 20, 20, { backgroundColor: TINT }),
    ell(12, 38, 40, 34, { backgroundColor: TINT }),
  ]),
];

/* ------------------------------------------------------------------ *
 * archetype templates                                                 *
 * ------------------------------------------------------------------ */

export const TEMPLATES: { id: string; name: string; hint: string }[] = [
  { id: "list", name: "List screen", hint: "nav · search · rows" },
  { id: "form", name: "Form screen", hint: "nav · fields · submit" },
  { id: "dashboard", name: "Dashboard grid", hint: "nav · 2×2 blocks" },
  { id: "fork", name: "Decision fork", hint: "3 states · 1 decision" },
];

/**
 * Build a template's elements centred on (cx, cy).
 *
 * Boxes carry `customData.role = "node"` and a *bound* label (containerId +
 * boundElements) rather than a free text sitting on top — that is the shape
 * the server reads as "this box is called X", so a template narrates
 * properly on the very first Save.
 * @param kind Template id from TEMPLATES ("list" | "form" | "dashboard" | "fork").
 * @param cx Scene x to centre the template on.
 * @param cy Scene y to centre the template on.
 * @returns The template's Excalidraw elements, positioned around (cx, cy).
 */
export function templateElements(kind: string, cx: number, cy: number): El[] {
  const ts = Date.now().toString(36);
  let n = 0;
  const nid = () => `tpl-${ts}-${++n}`;

  const out: El[] = [];
  let ox = 0, oy = 0; // set once the template's own size is known

  const box = (
    x: number, y: number, w: number, h: number, label: string,
    o: El = {}, type = "rectangle",
  ) => {
    const id = nid();
    const lid = `${id}-label`;
    out.push(base({
      id, type, x, y, width: w, height: h,
      boundElements: [{ id: lid, type: "text" }],
      customData: { role: "node" },
      ...o,
    }));
    out.push(base({
      id: lid, type: "text",
      x: x + 8, y: y + h / 2 - 9, width: Math.max(10, w - 16), height: 18,
      text: label, originalText: label,
      fontSize: 14, fontFamily: 6,
      textAlign: "center", verticalAlign: "middle", lineHeight: 1.25,
      containerId: id, autoResize: false,
      strokeColor: LABEL_INK, customData: { role: "label" },
    }));
  };

  const heading = (x: number, y: number, text: string, size = 18) =>
    out.push(base({
      id: nid(), type: "text", x, y,
      width: Math.max(8, text.length * size * 0.58), height: Math.round(size * 1.25),
      text, originalText: text, fontSize: size, fontFamily: 6,
      textAlign: "left", verticalAlign: "top", lineHeight: 1.25,
      containerId: null, autoResize: true, strokeColor: INK,
    }));

  if (kind === "list") {
    ox = -160; oy = -190;
    box(0, 0, 320, 44, "App", { backgroundColor: TINT });
    box(12, 56, 296, 32, "Search…");
    ["First item", "Second item", "Third item"].forEach((t, i) =>
      box(12, 100 + i * 60, 296, 52, t));
    box(12, 292, 130, 36, "Load more", { backgroundColor: TINT });
  } else if (kind === "form") {
    ox = -160; oy = -150;
    box(0, 0, 320, 44, "App", { backgroundColor: TINT });
    heading(12, 58, "Create account");
    ["Email", "Password", "Confirm password"].forEach((t, i) =>
      box(12, 92 + i * 48, 296, 36, t));
    box(12, 244, 296, 40, "Create account", { backgroundColor: TINT });
  } else if (kind === "dashboard") {
    ox = -210; oy = -160;
    box(0, 0, 420, 44, "Dashboard", { backgroundColor: TINT });
    [["Revenue", 0, 56], ["Signups", 220, 56],
     ["Churn", 0, 188], ["MRR", 220, 188]].forEach(([t, x, y]) =>
      box(x as number, y as number, 200, 120, t as string));
  } else if (kind === "fork") {
    // no arrows on purpose: bound arrows need real bindings, and an
    // unbound arrow that *looks* connected is a lie the server would read
    // as structure. The user draws the edges.
    ox = -280; oy = -90;
    box(0, 60, 140, 60, "Start");
    // a diamond wraps its bound label at width/2 − padding, not width −
    // padding. Size it for the text or Excalidraw breaks the word mid-way,
    // and the next Save reads that re-wrap as the user renaming the node.
    box(180, 40, 200, 100, "Approved?", {}, "diamond");
    box(420, 0, 140, 60, "Ship it");
    box(420, 120, 140, 60, "Send back");
  } else {
    return [];
  }

  for (const e of out) {
    e.x += cx + ox;
    e.y += cy + oy;
  }
  return out;
}
