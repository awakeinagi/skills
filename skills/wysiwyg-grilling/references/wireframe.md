# Wireframe — first-class type reference

*The question a wireframe answers:* **"Is THIS the screen you mean?"**

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
| Image | `rectangle` with an X drawn via two `line`s, or `kind: image` | picture slot |
| Note | `text`, `role: annotation` | interaction note |
| Priority | small `text` number, `kind: priority` | ordering |

Everything inside a screen carries that frame's `frameId`. ~360×480 phone
frame or 720×480 desktop; blocks 20px inset.

## Semantic facts

| Fact | Fires when | Narrate as |
|---|---|---|
| `screen_added/deleted {name}` | frame add/del | a screen entered/left the design |
| `block_moved_within_screen` | move, same frameId | layout preference |
| `regrouped {from_screen, to_screen}` | frameId changed | **content changed screens** — a different sentence than a move |
| `label_renamed {from, to}` | nav/button label edit | wording decision; checks wireframe↔flow mappings |
| `priority_changed {from, to}` | priority number edit | importance shift |

Plus universal facts. When a wireframe is the round's subject, the
interface-state checklist — **empty / loading / error / disabled /
no-results** — is a source of frontier questions ("what does this screen
show while the cart loads?"), never a gate.

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

Complexity budget: **8–12 blocks per screen**; more → split the screen or
add a view.

---
*Template structure from diagram-design (MIT — see NOTICE.md). Archetypes
and the annotation taxonomy re-expressed from wireframe-spec and wireframing
skill conventions; interface-state list re-expressed from ui-ux-pro-max
(short factual material, rewritten — provenance noted, nothing copied).*
