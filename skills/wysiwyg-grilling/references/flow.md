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
back/cancel) → resilience (undo/confirm on irreversible actions — and no
gratuitous confirm on reversible ones). **Reverse Narrative** — walking
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
