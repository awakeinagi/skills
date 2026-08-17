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

Wireframe frames are **declared 1:1 CSS pixels** (v0.4): phone 360×480,
desktop 720×480 — the old tilde is retired. The declaration is what lets
the 2.5.8 target-size check measure something real (NOTE + question only,
never a verdict — references/wireframe.md).

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
   is flagged. The **pixel radius is suspended** for the sharp campaign
   (v0.9): nothing writes 8px or 6px anywhere. A routed corner is
   Excalidraw's proportional `roundness: {"type": 2}` — proposed by
   `derived_roundness` for any route with a turn, then granted or refused
   per arrow by `gate_curvature`, so whether a given elbow draws a corner
   at all is a decision, not a constant. Stage 3 may restore a radius; the
   §6 rule stands as written until it does.
2. **Labels ride the arc midpoint** (seeder + lint WARNING). A label bound
   to an arrow is placed by the *client*, at the midpoint of the path by
   arc length — it discards any offset you store, so the old "6–10px
   perpendicular" rule was unenforceable and is retired (v0.6). The client
   breaks the stroke behind the label and `render_svg` paints a matching
   backing, which is rule 2's other half: an opaque background. What IS
   linted is where the label actually lands — a label overlapping another
   label, or sitting on a box that is neither end of its arrow, is flagged.
   The second case reads as that box's caption and is why a long elbow is
   worse than a long straight run: move the bend, don't nudge the label.
3. **No shared attach points** (seeder + lint WARNING). N arrows on one edge
   of length L attach at `L·k/(N+1)` for k=1..N — or use binding `focus`
   values (±0.5 steps) to fan them. Two connectors sharing a point are
   flagged within 12px. The auto-fan only moves **server-routed paths of 2
   or 3 points** — it will not re-space a path you authored, or one carrying
   more waypoints than that, and the warning names which of the two
   disqualified it.
   The feet are not the whole story — see *Two strokes, one lane* below.
4. **Ports follow travel direction** (seeder). Use top/bottom ports when
   travel is mainly vertical, side ports only when mainly horizontal. Never
   puncture a side face to reach something above or below.
5. **Never cross a foreign box** (lint WARNING). A connector passing through
   a node that is neither its source nor destination is flagged. Prefer
   re-routing; where two arrows must cross, bridge the less important one
   with a hop arc (`a 8,8 0 0,1 16,0`) — never bridge both.

**Two strokes, one lane** (lint WARNING). Two arrows can leave a node
correctly spaced and still end up drawn on top of each other further along.
A pair holding the same line within **16px** for **60px or more** is flagged,
with the shared run and the separation in px: over that stretch the drawing
has one thick stroke where the model has two edges, and neither can be
followed to its own node. The auto-fan leaves **18px** between feet, so it
clears this by construction — a lane you are told about is one the fan could
not reach (an authored path, or one with too many waypoints). Repair by
moving a bend with `mod points`, not by nudging a foot.

**Two arrows that read as one bidirectional edge** (lint WARNING). When two
final legs land on one line pointing opposite ways and overlap, a reader
merges them into a single two-headed relation the model never asserted. If
the relation really is symmetric, say so with one arrow and two heads;
otherwise offset one final leg. **Unless the pair shares a node** — a fan out
of one source, or two edges converging on one target, is the layout aligning
things deliberately, and offsetting a leg to satisfy a checker would make the
picture worse. That case is silent on purpose.

Both read the path as DRAWN rather than the stored polyline, and both take a
`waive` if the pairing is intentional. They differ in one way worth knowing:
the **lane** answer is unchanged by rounding — it is taken from the chord
between the two points the ink is pinned to, so a rounded corner and a sharp
one give the same verdict — while the **bidirectional** answer can move,
because a bowed final leg stops reading as a straight line. Rounding one
corner can therefore silence a bidirectional finding without moving an
endpoint. The router will not curve an arrow whose drawn arrival leans off
square, which is what keeps this from happening to you by accident.

*(That paragraph read "rounding a corner never changes the answer" for both
lints until 2026-08-16. It was true of one and false of the other — written
in the very commit that deleted connector rule 3's false claim about parallel
runs. Recorded rather than quietly patched: this file has now shipped two
wrong invariance claims about connector geometry, and both were caught by
someone measuring rather than by any check.)*

**Cramped sides are a known false alarm** for the lane check. When a node's
side is too short to hold all the feet arriving on it, the auto-fan spreads
them as far as the side allows and stops — and what it produces then is a
lane the check reports. Measured over 120 fanned scenes: 53 report
themselves, all of them sides with four or more feet, diamonds worst. The
warning says so, and the repair is to move an edge to another border or grow
the node, never to re-run the fan.

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
frames → backdrops → arrows/lines → nodes → composed parts → bound
labels & pins. A composed PART is anything a composite draws on its
owner — content (a kpi/input `value`, an entity's attribute rows) and
furniture alike (checkbox box and check stroke, toggle track and thumb,
slider track and thumb, body waves, X-box strokes). All of it bands
above its owner, so an opaque fill can paint out neither the number the
tile exists to show nor the glyph that says the control is checked.
Only a standalone BACKDROP — a `role: decoration` with no part tag,
such as the thick low-opacity line behind parallel edges — still bands
beneath. Explicit `reorder` ops survive within their band, which is
also what keeps a check stroke over the box it was declared after;
cross-band placement rides `role: decoration`.

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

- **Detached endpoint** — an arrow's geometry isn't on the border of the
  node its binding names. Measured as distance to the shape's *perimeter*,
  so it fires **in both directions**: an endpoint short of the node, and
  one buried inside it. (Until v0.7 only the outward direction was
  checked, so an arrowhead stopping 56px inside a box passed while one
  26px short was an error.)
- **Binding to nothing** — the element a binding names does not exist,
  usually because it was deleted. Deletion does *not* cascade: the arrows
  stay, so the drawing asserts a flow out of nothing until you re-target
  the binding or delete the arrow with it.
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
**arrow label landing on a box that is neither end of its arrow** (v0.6 —
measured where the client draws it, not where it is stored; the two differ
on every elbow) ·
**text that does not fit the box it is drawn in** (v0.5 — measured, not
read off the stored width, and it covers composed rows too: KPI values,
entity attribute lines, fixed-width text. Wrapping text is judged on its
wrapped height and only called too wide when a single word cannot fit;
non-wrapping composed rows are judged on width. This is the one class of
defect you are structurally blind to, so it is the lint to actually read)
· element stranded far outside the
artifact's cluster · bidirectional arrow (both arrowheads — split it into
two labeled arrows) · activation bar that never closes · **a bound label
left more opaque than the box it names** (v0.9 — a label does not inherit
its container's opacity, so hiding a node leaves its caption at full ink:
a word floating with nothing under it, which is neither the hidden node
you asked for nor a visible one. The finding carries both values and the
gap. Set both or neither). Wireframe form
warnings (v0.4): submit button preceding its inputs in reading order ·
input with no label (3.3.2) · asterisk in an input label (GOV.UK:
"(optional)" instead) · same label mapped to different flow steps (3.2.4
mirror — the dangerous case) · **state-variant frames disagreeing on the
same block's label** (v0.6 — two frames of one screen with matching
reading orders are the same controls twice, so a rename that landed on
one and not the other reads as two different things. Nothing compared
them before: 3.2.4 needs a mapping to a flow, and tripwires only compare
across artifacts. Declare the pairing with `customData.variant_of` or
let it be inferred from equal reading orders; a deliberate difference —
a held state, error copy — waives with `var:<aid>:<slug>`).

And as **LAYOUT_NOTE** (style/budget): off-grid coordinate · unlabeled
arrow out of a decision or between services · orphan node/edge · budget
overruns · `opacity ≠ 100` on a static element (opacity is state, not
style) · within-group spacing ≥ between-group spacing (grouping only reads
when internal gaps are smaller than external ones). Wireframe question-
NOTEs (v0.4 — questions a criterion will ask later, never verdicts):
duplicate frame titles · ≥3 uniform-width inputs · declared sticky bar
over inputs (2.4.11) · help missing/drifting across screens (3.2.6) ·
targets closer than a thumb (2.5.8, needs the 1:1 px declaration) ·
progress indicator (Q25, waivable) · redundant entry along a mapped flow
path (3.3.7) · mapped same-function labels diverging (3.2.4) · wireframe
label matching a domain term (Q12, waivable). One-time questions go quiet
via the registry `waive` op (reason required) — a waived question is an
answered one.

## Legibility: colour and type size (lint WARNING)

Ops let you name any colour and any `fontSize`, and until v0.9 nothing
read either. Three checks now do. All three are **questions, never
verdicts** — nobody is told a drawing "fails WCAG"; each says what it
measured, says what the criterion asks for, and takes a `waive`.

| Check | Asks | Floor | Waive key |
|---|---|---|---|
| `contrast_text` | 1.4.3 — can this ink be read? | 4.5:1, or 3:1 at `fontSize` ≥ 24 | `ink:<aid>:<id>` |
| `contrast_object` | 1.4.11 — can this shape be picked out? | 3:1 | `stroke:<aid>:<id>` |
| `min_font` | is this type big enough to survive the snapshot? | 7px | `font:<aid>:<id>` |

The paper is `#fdfcf8`, not white, and that is not a detail: `#767676`
is the web's best-known "lightest grey that passes on white" and it
reads 4.42:1 here, just under. **A colour you validated against a white
background may not clear the floor on this ground.**

Three things worth knowing before you argue with a finding:

- **Opacity counts.** The ratio is taken after opacity is folded into
  the colour, because that is what a reader sees. The default ink
  `#1e1e1e` is 16.24:1 at full strength and **4.38:1 at 60%** — under
  the text floor while declaring nothing unusual anywhere. An element at
  opacity 0 is exempt: it is not faint, it is not drawn, and the
  `opacity ≠ 100` note already owns that.
- **An object needs only one of its two colours.** A pale stroke around
  a solid dark fill is a shape you can see, so the reading is the better
  of stroke and fill; a finding means neither reaches 3:1.
- **The font floor is not politeness.** It was MEASURED, not chosen: at
  7px a rendered word holds 8.5:1 of stroke, at 6px that halves to
  4.6:1 and the letters stop separating — while the DECLARED contrast
  is 16.24:1 at both sizes. A colour check cannot see this, which is
  why the floor is a separate check rather than a style note.

Composed parts — a slider's track, an X-box's strokes, a tile's value
row — are not asked about. The server minted those colours from its own
palette in response to your `kind:` composite; there is no decision of
yours to question. A decoration or annotation **you** styled is asked,
and answered with a waive.

**Known: annotations trip `contrast_text` on the default ink.** An
annotation you give no `strokeColor` is minted at `#5c8a5f`, which reads
3.89:1 — under the 4.5 floor. The finding is correct and the palette is
what disagrees with it; the fix is a pending product decision, not
something to work around. Until it is settled, pass a darker explicit
`strokeColor` on annotations you care about, or waive with
`ink:<aid>:<id>` and a reason. Do not read the finding as noise.

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
