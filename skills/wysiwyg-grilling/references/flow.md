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
| Annotation | `text`, `role: annotation` near its subject | side note |

Left→right main line; branches drop below; 64px tall steps. What governs
legibility is **clear run** — the empty gap between two connected nodes'
edges, where the arrow and its midpoint label live. Keep ≥140px of clear run
on unlabeled arrows and ≥ (label width + 40px) on labeled ones; with 160px
nodes that means a center-to-center pitch of ~320px, more when labels are
long. Labels are short verb phrases ("Review order", not "The user then
reviews their order").

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

Complexity budget: **8–12 nodes**. Over it → split into another view (e.g.
"happy path" + "error handling"), never shrink.

---
*Structure of this reference follows diagram-design's per-type template
(MIT, © cathrynlavery — attribution in NOTICE.md). Draw/don't-draw gate
informed by utility-mermaid-diagrams (Apache-2.0). Shape→semantic map
re-expressed from user-flow-diagram conventions (provenance note, no text
copied).*
