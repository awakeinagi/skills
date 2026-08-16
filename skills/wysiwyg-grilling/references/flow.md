# Flow — first-class type reference

*The question a flow answers:* **"Is THIS the order things happen in?"**
Flow is the **hub type**: default-mapped to wireframes (screen↔step,
button↔transition) and domain diagrams (entity↔steps acting on it). Create
those element links eagerly as you draw the pairs.

## Primitives

| Element | Spec | Meaning |
|---|---|---|
| Step | `rectangle`, `role: node`, label required | one state/stage |
| Decision | `diamond`, `role: node`, label = the question | branch point |
| Start/End | `ellipse`, `role: node` | terminal |
| Transition | `arrow` with `from`/`to` (+ optional label like "yes") | order |
| Merge point | small filled `ellipse` (r≈4) where branches re-join | re-convergence |
| Lane | `frame`, `kind: lane`, label = the owner | responsibility zone |
| Annotation | `text`, `role: annotation`, `annotates: <id>` | side note |

## Node kinds

Steps carry a `kind` naming what the box *is*, because "an LLM writes this"
and "everything freezes here" are different claims than "code runs":

`source` (data enters) · `transform` (deterministic work) · **`agent`**
(nondeterministic — a model decides) · **`control`** (gate, cutoff,
deadline, freeze) · `sink` (leaves the system) · `store` (data at rest —
a legal flow terminal like sink; draw it as a rounded rectangle via
`roundness`).

Two duties ride the kinds: **every `agent` node owes a "what does it do
when it's unsure?" question** — its answer is a content rule the design
needs and nobody volunteers; and kind-pairings are linted (`source → sink`
with no transform between is a structural ERROR — see
`references/layout.md`).

## Lanes (ownership overlay)

When a second owner is named for any step, add lane frames rather than a
new artifact: each lane is a `frame` (`kind: lane`) owning the steps inside
it. Facts: `lane_added/deleted {owner}` (a party entered the process),
**`ownership_changed {step, from_lane, to_lane}`** (flagship —
responsibility moved), `handoff_added {from_lane, to_lane}` (a transition
crosses lanes — boundary crossings are where failures live; each deserves
a question). Budget: 5 lanes. (The `swimlane` artifact type in config is
this overlay under an older name — prefer a `flow` with lane frames; a
standalone swimlane artifact draws but narrates generically.)

Seed the **first node centered** and let the flow grow in both directions —
a left-edge start guarantees a 2000px crawl rightward. Branches: "yes"
continues the travel direction, "no" drops below; re-join at a merge dot.
64px tall steps on the 320/160 grid (`references/layout.md`). What governs
legibility is **clear run** — the empty gap between two connected nodes'
edges, where the arrow and its midpoint label live. Keep ≥140px of clear run
on unlabeled arrows and ≥ (label width + 40px) on labeled ones; with 160px
nodes that means a center-to-center pitch of ~320px, more when labels are
long. Labels are short verb phrases ("Review order", not "The user then
reviews their order").

An **async step is three nodes, not one**: trigger → in-progress →
(success | error). Drawing only trigger→success hides two states the
design must eventually hold — seed all three and let the user prune.

**Seeding from mermaid** (v0.8) — **seed, then drag.** The seed is a
first draft, not a layout: a careful hand layout on the 320/160 grid
still reads better, and the seed's job is to get every node and edge onto
the canvas correctly bound so you can arrange them.

**Write it this way. The three knobs are free and they close most of the
gap:**

```
%%{init: {'flowchart': {'nodeSpacing': 100, 'rankSpacing': 140}}}%%
flowchart LR
  <the happy path — every edge, start to finish>
  <then the exceptions, branches and back-edges>
```

`LR` over `TD` is the biggest lever: it eliminates floating diamond
endpoints and turns a three-screen scroll into a one-screen band.
**Declaration order is the cheapest and it matters most** — dagre's
within-rank ordering follows it, so *declare every edge of the happy path
first, start to finish, then the exceptions.* At 12 nodes the three
together measured 0 lint findings, 0 crossings, 0 shared corridors and 0
false junctions: parity with the hand layout, not superiority.

**The honest ceiling.** An older version of this page claimed the seed
was worth it above ~7 nodes. That is withdrawn — it was measured, and
then the drawing was rendered and looked at, and it failed. At **8**
nodes, one above the stated threshold, the seed drew a flow wrong in two
ways at once:

- **It manufactured a relationship.** Two unrelated edges — the `no`
  branch into "Order cancelled" and the return from "Request new card"
  into "Authorise payment" — land on the same horizontal line, 31px of
  it shared. What renders is a single straight stroke with **an
  arrowhead at each end**, reading as *"Order cancelled ⟷ Authorise
  payment"*. Neither direction is in the source. Confirmed by rendering
  the seed and looking at it, not inferred from coordinates.
- **An arrow stopped 60px clear of the node it binds**, its arrowhead
  floating in open canvas short of the "Auth succeeded?" diamond.

The lint names the second and **says nothing about the first** — so
"read the lint and move on" would have shipped the invented
relationship. The hand layout of the same eight nodes was correct first
try and took about two minutes. Happy-path-first removes the
manufactured relationship outright, which is why the recipe above is not
optional.

**Where it is worth it, narrowly:**

- **`erDiagram` → domain, always.** 0.27s, no browser, a genuinely good
  picture. Use it every time.
- **Structural capture above ~20 nodes**, where the value is "everything
  is on the canvas and correctly bound" rather than "it is laid out".
  Fidelity is flawless at every size measured (5/8/12/20/34 nodes: counts
  match, labels byte-identical, shapes and reflexive edges correct,
  nothing dropped or invented). It is the *layout* that fails, never the
  capture. Wall clock is flat at ~10–11s whatever the size — it is the
  browser launch — so the argument is real at 34 nodes and absent at 5.
- **`--from-skeletons` replay** (0.30s, offline).

It is **not** worth it in the 8–15 node band this page used to target.

**Choosing a layout engine.** Two are usable: **`dagre` (the default)**
and **`elk`**, selected in frontmatter:

```
---
config:
  layout: elk
---
```

**Dagre stays the default, and the reason is measured, not traditional.**
On the same 12-node graph, twice, ELK's placement drew *more* defects
through our router — TD: 1 error + 4 warnings under ELK against 0 errors
+ 3 warnings under dagre; the LR recipe: 4 warnings against 2. The ELK
error was an arrow running 30px inside the box it lands on. Its edges
land closer together and this router does no nudging, so compaction
turns into collisions.

**What ELK is genuinely better at is size**, which is worth reaching for
when a drawing is too wide to read: the LR recipe went **3400px →
2124px wide** (−37%) and the TD graph **1628px → 1400px tall** (−14%).
So: leave it on dagre, and if the seed comes back too wide to take in,
re-seed with `layout: elk` **and re-read the lint** — you are trading
width for routing defects, and the lint is where that trade shows up.

`swimlane` and `cose-bilkent` also exist and neither is usable for
flowcharts (`cose-bilkent` fails outright with "Root node is required").

**A layout name that is not registered still falls back to dagre in
silence** — no error, no note. Measured both ways on this build: asking
for `elk` moves all 12 of 12 nodes off dagre's placement, while asking
for a name nobody registered moves 0 of 12. So a typo in that frontmatter
looks exactly like a layout that worked. Check the drawing changed.

**One silent degradation to know about:**
- **Most mermaid node shapes degrade to a plain rectangle, silently.**
  Read off the converter's own shape switch, these are all the distinct
  results you can get: `[]` rectangle · `([])` and `()` rounded
  rectangle, *indistinguishable from each other* · `(())` circle ·
  `((()))` double circle · `{}` diamond. Everything else — `[(Cylinder)]`,
  `[[Subroutine]]`, `{{Hexagon}}`, the trapezoids and parallelograms —
  comes back as a plain rectangle with no warning, so `[(Database)]`
  reads exactly like `[Step]`. Use the five that survive and carry the
  rest as node kinds (`mod attrs.kind`) instead.

Diamonds map to decisions, `([stadium])` stays rounded, edge labels ride
along, `A --> A` routes as a reflexive loop. Immediately after seeding:
classify the kinds (`mod attrs.kind` — mermaid can't carry them) and heed
any label-run warnings by spreading nodes. Subgraphs are refused
(converter limitation) — seed flat, add lanes as frames after.
`--relayout` runs the same dagre over an existing messy flow as
revertable `mod x/y` ops; it carries lane frames along with their members
and names any node the user placed by hand before it moves anything.

## Semantic facts (what the differ gives you to narrate)

| Fact | Fires when | Narrate as |
|---|---|---|
| `step_added` / `step_deleted` | node add/del | new/removed stage |
| `transition_added/deleted {between}` | bound arrow add/del | order change |
| **`rewired {arrow, from, to}`** | a binding endpoint changed | **the flagship fact** — the user re-ordered reality; lead with it |
| `branch_added` | diamond added | a decision entered the design |
| `sequence_reordered` | ≥2 rewires in one save | wholesale re-sequencing |
| `step_orphaned` | a node has no transitions | lint-style observation, not an error |

Plus universal facts (added/deleted/renamed/label_added/type_changed/moved/
annotated/…). A rectangle→diamond `type_changed` usually means "this step
became a decision" — say that, not the mechanics.

## Mapping rules

- wireframe↔flow: screen ↔ step, button/nav ↔ transition. Link eagerly.
- domain↔flow: entity ↔ the steps that act on it. Link eagerly.
- A flow renaming a step that maps to a wireframe screen fires a tripwire —
  flag and offer, never sync.

## Seed archetypes

- **Pipeline**: Start → 3–5 steps → End. Use when the user describes any
  linear process.
- **Decision fork**: linear head, one diamond, two labeled branches ("yes"/
  "no") that re-join or terminate separately.
- **Loop-back**: a failure branch arrowing back to an earlier step
  (label the return arrow with its reason: "validation fails").

Elaborate a flow in this order, each stage a round or less: happy path
first (no errors yet) → decision branches → the fixed edge-case walk
(empty/null, invalid input, timeout, interruption, permission denial,
back/cancel) → resilience (an irreversible action needs **one of
confirm / undo / review step** — multi-step data entry gets the review
step, i.e. the check-answers frame; and no gratuitous confirm on
reversible ones). An irreversible terminal submit also owes its
**confirmation frame** (reference + what-happens-next,
`references/wireframe.md`) — a flow ending at a bare submit points into
void. **Reverse Narrative** — walking
backward from the end asking "what must have happened for this?" — is the
cheapest way to find missing steps (`references/choreography.md`).

Complexity budget: **max 9 nodes AND max 12 arrows** — two limits, and the
arrow limit is the one that triggers a second view (edges collide, nodes
don't; see `references/layout.md`). Over it → split into another view
(e.g. "happy path" + "error handling"), never shrink.

---
*Structure of this reference follows diagram-design's per-type template
(MIT, © cathrynlavery — attribution in NOTICE.md). Draw/don't-draw gate
informed by utility-mermaid-diagrams (Apache-2.0). Shape→semantic map
re-expressed from user-flow-diagram conventions (provenance note, no text
copied). Node kinds and lane facts from this project's v0 refinement
audit; async-three-node rule, elaboration ladder, and irreversibility
guard from bm629/agent-skills (MIT); center-start growth adapted from
EventStorming (Brandolini, via melodic-software, MIT); merge-dot and
branch-direction conventions from excalimate flowcharts (MIT).*
