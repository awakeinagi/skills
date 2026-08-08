# Wireframe — first-class type reference

*The question a wireframe answers:* **"Is THIS the screen you mean?"** —
and in **output mode** (below), "is THIS the deliverable you mean?" A
wireframe's subject is anything a person will look at: a screen, a report,
an email, a CLI transcript, an API payload rendered as its consumer sees it.

## The low-fi ceiling

Gray/black/white only; sketchy shapes; X-box for images; wavy-line stand-ins
for body text — **but real labels on nav and buttons**. Labels are
load-bearing for mappings and narration (`kind: button|nav` makes
`label_renamed` fire tripwires); body copy is not. No mid-fi styling, ever —
crisp fonts and real colors invite the font-and-spacing scrutiny low-fi
exists to deflect. A state variant (empty cart vs full cart) is a **second
wireframe view of the same screen**, not styling on one.

## Primitives

| Element | Spec | Meaning |
|---|---|---|
| Screen | `frame` (its `label`/name is the screen name) | one screen |
| Nav/header | `rectangle`, `kind: nav`, real label, light fill `#e9e5da` | chrome |
| Button | `rectangle`, `kind: button`, real label (dark fill `#1e1e1e` for primary) | action |
| Input | `rectangle`, `kind: input`, label = field name | form field |
| Content block | `rectangle`, `kind: block`, label = what it holds | region |
| Image | `rectangle` with `kind: image` — the X-box strokes compose automatically | picture slot |
| Body text | `line`s with `role: decoration` (never lint as connectors) | wavy stand-in |
| Nested card | `rectangle` with `parent: <container-id>` | card in a shelf |
| Titled panel | `rectangle`, `kind: block`, `verticalAlign: "top"` label + `parent`-nested cards | shelf with a header |
| KPI / stat tile | `rectangle`, `kind: kpi`, `label` = the metric NAME, `value` = the number (composed big row) | dashboard number |
| Checkbox | `rectangle`, `kind: checkbox`, `checked: true\|false` (composed glyph) | binary setting |
| Toggle | `rectangle`, `kind: toggle`, `checked` moves the thumb | on/off switch |
| Slider | `rectangle`, `kind: slider`, `value: 0–100` positions the thumb | threshold/range |
| Note | `text`, `role: annotation` | interaction note |
| Priority | small `text` number, `kind: priority` | ordering |

KPI, checkbox, toggle, and slider are composed kinds: the label stays the
semantic name (renames fire `label_renamed`; the value never pollutes the
fact), and `mod {"value": …}` / `mod {"checked": …}` fire typed
`value_changed` / `state_toggled` facts. Hover-only detail belongs in
`tooltip` (markdown), not extra rows.

Everything inside a screen carries that frame's `frameId`. ~360×480 phone
frame or 720×480 desktop; blocks 20px inset, 12px gutters (seeder: 12-col
grid, 8pt spacing rhythm — 4pt for dense data UI). Within-group gaps stay
smaller than between-group gaps or the grouping doesn't read (lint NOTE).
In a variant set, sibling screens share row baselines and text alignment —
row alignment is what makes a variant pair diffable at a glance.

## Output mode

When the deliverable is a document rather than an interactive screen:

- The frame **is the artifact** — a report page, an email body, a payload;
  its name is the deliverable's name, not a screen name.
- A **fold line** (`text`, `kind: fold`, dashed) is first-class: it marks
  the first screenful, which for most deliverables IS the product. Fact
  **`fold_crossed {block, from, to}`** fires when a block moves across it —
  that move is an editorial decision, not a nudge; narrate it as one.
- Seed archetype — **Output document**: title/meta bar, 1 synthesis block,
  3–5 section blocks, fold annotation after block 2. Its pair variant is
  the degraded/exception mode of the same document, drawn in the same
  batch (one view, one question).

## App shell

Chrome shared across screens (nav, header, tab bar) is authored **once**
as shell elements and reused, not redrawn per screen — mark them
`kind: nav` with a common `shell` group. A shell edit is the one legitimate
wide ripple: apply it to every screen that reuses the shell in one pass and
narrate the propagation. Shell elements drifting apart across frames (same
kind, diverging label/geometry) is a lint WARNING — the wireframe analogue
of a mapping tripwire.

## Semantic facts

| Fact | Fires when | Narrate as |
|---|---|---|
| `screen_added/deleted {name}` | frame add/del | a screen entered/left the design |
| `block_moved_within_screen` | move, same frameId | layout preference |
| `regrouped {from_screen, to_screen}` | frameId changed | **content changed screens** — a different sentence than a move |
| `label_renamed {from, to}` | nav/button label edit | wording decision; checks wireframe↔flow mappings |
| `priority_changed {from, to}` | priority number edit | importance shift |

Plus universal facts and `fold_crossed` (output mode). When a wireframe is
the round's subject, the interface-state checklist — **empty / loading /
error / disabled / no-results** — is a source of frontier questions ("what
does this screen show while the cart loads?"), never a gate. State-quality
notes that sharpen those questions: a loading state seeds as a **skeleton
mirroring the populated layout**, never a bare spinner (prevents layout
shift and is the honest claim about what loads); an empty state carries a
reason and a guide-to-action; an error state carries plain-language cause +
recovery. For critiquing layout itself, use the Observation→Problem→Fix
template and the entry-point/eye-flow/weight/emphasis question sets in
`references/choreography.md`.

## Mapping rules

- wireframe↔flow (default pair): screen ↔ step, button ↔ transition — link
  eagerly as you draw.
- wireframe↔domain: inference only; propose an explicit link only after
  repeated inferred hits.

## Seed archetypes

- **List screen**: nav bar, search input, 3 repeated row blocks, primary
  action button.
- **Detail/form screen**: nav, title block, 2–4 inputs, primary + secondary
  buttons.
- **Dashboard**: nav, 2×2 block grid with labeled regions, one callout
  annotation.

Complexity budget: **max 9 blocks per screen** (see `references/layout.md`
for the two-limit budget); more → split the screen or add a view.

---
*Template structure from diagram-design (MIT — see NOTICE.md). Archetypes
and the annotation taxonomy re-expressed from wireframe-spec and wireframing
skill conventions; interface-state list re-expressed from ui-ux-pro-max
(short factual material, rewritten — provenance noted, nothing copied).
Output mode and fold from this project's v0 refinement audit; app-shell,
12-col/8pt rhythm, skeleton/empty/error state-quality, and
internal-≤-external spacing from bm629/agent-skills (MIT).*
