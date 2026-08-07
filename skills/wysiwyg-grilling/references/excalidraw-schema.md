# Excalidraw schema reference (WYSIWYG Grilling edition)

The skill's own reference — written because a widely-mined third-party
"excalidraw generator" reference encodes a **wrong text model** (inline
`"text"` props on shapes render as empty labels in real Excalidraw). Trust
this file, not that one. Normally you don't build elements yourself —
`canvas.py apply` + `make_element` do — but you read artifact files and save
records, and this is what's inside them.

## Document shape (normalized artifact file)

```jsonc
{ "type": "excalidraw", "version": 2, "source": "wysiwyg-grilling",
  "wysiwyg": {"artifact": "checkout-flow", "name": "Checkout Flow",
               "artifact_type": "flow", "migrations": ["0001-baseline"]},
  "appState": {"viewBackgroundColor": "#faf8f2", "gridSize": 20},
  "elements": [ /* z-order matters; see below */ ],
  "files": {} }
```

The normalizer (every write funnels through it) sorts keys, pins volatile
attrs (`version:1`, `versionNonce:0`, deterministic `seed` from the id,
`updated:1`), rounds geometry to 1px, and rebuilds `boundElements` from
scratch — so "no semantic change → no git diff" holds.

## The bound-text model (the thing the mined reference got wrong)

A label on a shape is a **separate element**:

```jsonc
// the shape — NO text prop anywhere on it
{ "id": "pay-button", "type": "rectangle", "x": 60, "y": 330,
  "width": 320, "height": 48,
  "boundElements": [{"id": "pay-button-label", "type": "text"}], ... }
// the label — a text element pointing back at its container
{ "id": "pay-button-label", "type": "text", "text": "Pay now",
  "originalText": "Pay now", "containerId": "pay-button",
  "fontSize": 16, "fontFamily": 6, "textAlign": "center",
  "verticalAlign": "middle", "lineHeight": 1.25, ... }
```

Both halves are required: `containerId` on the text AND the entry in the
container's `boundElements`. An inline `"text"` property on a rectangle
renders nothing. Frames are the exception — a frame's caption is its own
`name` property, not a bound label.

## Arrows: bindings do NOT route

`convertToExcalidrawElements` (and bindings generally) attach but **do not
compute geometry** — an arrow with bindings but default geometry renders as a
disconnected stub (feel-prototype finding). A correct bound arrow carries
explicit geometry AND bindings:

```jsonc
{ "id": "t1", "type": "arrow",
  "x": 210, "y": 172,                      // start point (absolute)
  "width": 80, "height": 0,                // span
  "points": [[0, 0], [80, 0]],             // relative route
  "startBinding": {"elementId": "cart", "focus": 0, "gap": 6},
  "endBinding":   {"elementId": "checkout", "focus": 0, "gap": 6},
  "endArrowhead": "arrow", ... }
```

Both endpoint elements list the arrow in their `boundElements`
(`{"id": "t1", "type": "arrow"}`). `canvas.py` computes edge-anchor geometry
for you; if you ever hand-build, compute x/y/width/height/points explicitly.

**Rebind sentinel**: while a user drags an arrow endpoint between targets,
Excalidraw parks coordinates at ±2^56. The differ suppresses these — a
binding change (the REWIRED fact) is the signal; the coordinate churn is
noise. Never read meaning into coordinates that large.

## customData conventions (ours, machine-readable — never flatten to pixels)

```jsonc
"customData": {
  "role":   "node" | "label" | "annotation" | "pin",
  "kind":   "button" | "nav" | "input" | "block" | "entity" | "priority" | …,
  "intent": "free-text: why this element exists",
  // pins only:
  "question": "…", "target": "element-id",
  "status": "open" | "answered" | "resolved", "answer": "…" }
```

`role` drives narration semantics (label geometry never narrates; pin
lifecycle facts; annotation facts). `kind: "button" | "nav"` makes wireframe
`label_renamed` fire mapping tripwires — real labels on nav/buttons are
load-bearing, not decoration.

## Font family ids

`1` Virgil (hand) · `2` Helvetica · `3` Cascadia · `5` Excalifont (hand) ·
`6` **Nunito — the skill default (legible; "no cursive anywhere")** ·
`7` Lilita One · `8` Comic Shanns · `9` Liberation Sans. The hand-drawn faces
ship in the bundle but are never the default; sketchy *shapes* carry the
low-fi signal, text stays legible.

## ID remap — the five reference kinds

If you ever copy elements between artifacts and re-mint ids, every one of
these must be rebound or the scene corrupts:

1. `id` (the element's own)
2. `groupIds` (list membership)
3. `startBinding.elementId` / `endBinding.elementId` (arrows)
4. `containerId` (bound text → container)
5. the ids inside `boundElements` (container → bound text/arrows)

(`frameId` is a sixth in practice — frame membership.) `regenerateIds: false`
is what preserves agent-minted semantic ids on the frontend.

## Geometry & misc facts

- `x`/`y` are the top-left corner (absolute scene coords); `points` are
  relative to `x,y`. Angles are radians.
- Deleted elements carry `isDeleted: true` in live scenes; normalized files
  simply omit them.
- `frameId` puts an element inside a frame (wireframe screens are frames);
  frame containment drives `regrouped` vs `block_moved_within_screen`.
- Element order in the array is z-order (reorder ops exist for it).
- Excalidraw ≥0.18 elements also carry a fractional `index` field — the
  normalizer strips it; the frontend regenerates on load. Upstream-version
  drift is absorbed by validate+repair plus the migration registry; a file a
  newer Excalidraw wrote either normalizes cleanly or fails addressably —
  never corrupts silently.
