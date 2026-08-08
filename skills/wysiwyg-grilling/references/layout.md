# Layout — cross-type drawing geometry

Read this before your first drawing batch of a session, alongside the type
reference for what you're drawing. Every rule here exists in one of three
enforcement forms — **(lint)** the server checks it and reports on the
`LAYOUT_ERROR / LAYOUT_WARNING / LAYOUT_NOTE` channel, **(seeder)** you (and
the server's routing) compute coordinates from it so the rule holds by
construction, **(duty)** you apply it as judgment at draw time. Rules without
one of these forms live in NOTICE.md as citations, not here.

## Grid (seeder)

Every coordinate and dimension is a multiple of 4; positions prefer
multiples of 20. Compute positions from grid indices — never freehand:

| Type | Column pitch | Row pitch | Node size |
|---|---|---|---|
| flow | 320 | 160 | 160×64 |
| domain | 320 | 140 | 180×64 |
| wireframe | frame + 20px inset | 12px gutter | full-width blocks |
| sequence | 250 (lifeline pitch) | 80 (message pitch) | header 160×60 |

The domain pitch is 320, not the 260 an older revision documented: with
180-wide entities, 260 leaves an 80px clear run — shorter than any
cardinality label, so every by-the-book relationship arrow warned on
arrival (v0.3 assessment). **Labeled vertical arrows need 1.5× row pitch
(flow: 240) or side-routing** — a 160px row pitch leaves a 64px run that
cannot hold a label clear of both nodes.

`x = startX + col * pitch`, `y = startY + row * pitch`. Off-grid coordinates
draw fine but read as sloppiness at every zoom — the lint flags them
(`LAYOUT_NOTE`) so drift gets caught, but the point is to never generate it.

Radial placement (domain Hub archetype, mindmaps): spoke *i* of *N* sits at
`angle = 2π·i/N`, `x = cx + r·cos(angle)`, `y = cy + r·sin(angle)`, r ≥ 250.

Sibling centering under a parent (trees, org shapes):
`totalWidth = N·nodeW + (N−1)·gap`, `startX = parentCenterX − totalWidth/2`.

## Connectors

The five rules below come from diagram-design §6 (see NOTICE.md), where they
are non-negotiable. They are why a diagram reads at a glance or doesn't.

1. **Orthogonal over diagonal** (seeder + lint WARNING). When source and
   destination share neither axis, route a two-bend elbow (radius 8px; 6px
   when tight), never a straight diagonal. A diagonal between off-axis nodes
   is flagged.
2. **Labels off the stroke** (seeder + lint WARNING). Arrow labels sit
   6–10px perpendicular off their segment, or carry an opaque background. A
   label bbox intersecting its own stroke is flagged — a label that hides
   its arrow explains nothing.
3. **No shared attach points** (seeder + lint WARNING). N arrows on one edge
   of length L attach at `L·k/(N+1)` for k=1..N, ≥12px apart — or use
   binding `focus` values (±0.5 steps) to fan them. Two connectors sharing a
   point, or parallel runs <12px apart, are flagged.
4. **Ports follow travel direction** (seeder). Use top/bottom ports when
   travel is mainly vertical, side ports only when mainly horizontal. Never
   puncture a side face to reach something above or below.
5. **Never cross a foreign box** (lint WARNING). A connector passing through
   a node that is neither its source nor destination is flagged. Prefer
   re-routing; where two arrows must cross, bridge the less important one
   with a hop arc (`a 8,8 0 0,1 16,0`) — never bridge both.

The router now avoids foreign boxes itself: it scores straight lines,
both L-elbow orientations, and bounded Z-detours, and picks the cleanest
(fewest crossings, then fewest bends). When it still can't find a clean
path, the lint flags it — repair by hand with `mod points` (waypoints
relative to the arrow's x,y; axis-aligned paths render as sharp elbows,
the route is re-stamped as server-owned, and the change narrates as a
`rerouted` fact). For N parallel edges between two clusters, one thick
low-opacity `role: decoration` backdrop line with the real arrows offset
±10px along it.

**Z-order** (now ENFORCED by a normalization pass on every apply):
frames → decorations → arrows/lines → nodes → bound labels & pins.
Explicit `reorder` ops survive within their band; cross-band placement
rides `role: decoration`.

## Budgets (lint NOTE → view suggestion)

**Max 9 nodes AND max 12 arrows per artifact — two separate limits.**
(Earlier material collapsed these into "8–12 nodes"; the arrow limit is the
one that matters, because edges collide and nodes don't.) Crossing the
*arrow* budget is the standard trigger for "propose a second view."

Per-type sub-limits: 5 lifelines (sequence) · 5 lanes (flow overlay) ·
8 entities (domain) · 2 annotation callouts per artifact (linted at NOTE
now, not just documented). Labels are linted against EACH OTHER too
(label↔label collision, WARNING) — stacked labels read as one caption.
Declared containment (`parent` on a block) exempts a nested pair from the
overlap warning; `role: decoration` exempts furniture from connector
lints and budgets entirely.

Budgets are legibility physics, not taste — over budget, split the view,
never shrink the font.

## Structural lint (semantic, not cosmetic)

These fire as **LAYOUT_ERROR** — the drawing doesn't say what you meant.
Repair in the same move, before narrating (cosmetic-class, never a second
proposal):

- **Detached endpoint** — an arrow's geometry doesn't reach the node its
  binding names.
- **Black hole / miracle node** — in a flow whose kinds require
  through-flow, a node with only inbound (black hole) or only outbound
  (miracle) arrows. Every such gap is a conversation not yet had — turn it
  into a frontier question, don't silently patch it.
- **Illegal kind-pairing** — an arrow directly connecting two node kinds
  that must route through a third (e.g. `source → sink` with no transform).
- **Time reversal** — a sequence message arrow pointing upward (dy < 0).
- **Unrequested consequence** — the intent echo shows an op did not do what
  it said (a rewire that didn't bind, a frame move that stranded members).

And as **LAYOUT_WARNING** (legibility): annotation overlapping any element ·
bound label wider than its container · element stranded far outside the
artifact's cluster · bidirectional arrow (both arrowheads — split it into
two labeled arrows) · activation bar that never closes.

And as **LAYOUT_NOTE** (style/budget): off-grid coordinate · unlabeled
arrow out of a decision or between services · orphan node/edge · budget
overruns · `opacity ≠ 100` on a static element (opacity is state, not
style) · within-group spacing ≥ between-group spacing (grouping only reads
when internal gaps are smaller than external ones).

## Annotations (seeder + lint WARNING)

A free `text` annotation declares its subject: `annotates: <element-id>` in
`customData`. The server draws a hairline leader to it and lints the
distance. An annotation near an element by coincidence of coordinates is
one layout-tidy away from labeling the wrong thing — the demo session
proved it.

---
*Connector rules, grid, and budgets from diagram-design §6–§7 (MIT — see
NOTICE.md; the two-limit budget corrects an earlier misreading). Fan-by-
focus, bridge arc, radial/sibling formulas, waypoint and bundling fallbacks
from excalimate (MIT). Black-hole/miracle and kind-pairing invariants
re-expressed from public DFD notation. Bidirectional/unlabeled-arrow
hygiene from softaworks C4 (MIT). Internal-≤-external spacing from
bm629/agent-skills (MIT). Opacity-is-state from excalimate.*
