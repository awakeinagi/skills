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
and not only to deflect font-and-spacing scrutiny: visual polish measurably
suppresses **problem-finding**, in the user and in the reviewer (Kurosu &
Kashimura 1995, re-expressed — see NOTICE.md). The gray ceiling is about
not anaesthetising the right feedback. Bonus it buys for free: every
distinction on this canvas is carried by position, size, shape or text —
never color alone — so WCAG 1.4.1 holds by construction; a design that
needs a distinction the palette can't draw has found a real finding. A
state variant (empty cart vs full cart) is a **second wireframe view of
the same screen**, not styling on one.

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
| Help | `rectangle`, `kind: help`, small, real label | where help lives (3.2.6 lint watches its slot) |
| Sticky bar | `rectangle`, `kind: sticky-bar`, flush with a frame edge | pinned chrome — floats OVER content; declaring it arms the 2.4.11 focus question |
| Feedback | `rectangle`, `kind: feedback`, label = the message | post-action confirmation (banner/toast) — the constructive answer to "what tells them it worked?" |

KPI, checkbox, toggle, and slider are composed kinds: the label stays the
semantic name (renames fire `label_renamed`; the value never pollutes the
fact), and `mod {"value": …}` / `mod {"checked": …}` fire typed
`value_changed` / `state_toggled` facts. Hover-only detail belongs in
`tooltip` (markdown), not extra rows.

Everything inside a screen carries that frame's `frameId`. **Frame
coordinates are declared 1:1 with CSS pixels** (v0.4 ruling — the tilde is
retired): a phone frame is 360×480 CSS px, desktop 720×480; blocks 20px
inset, 12px gutters (seeder: 12-col grid, 8pt spacing rhythm — 4pt for
dense data UI). The declaration is what makes geometry claims honest: the
2.5.8 target-size question ("closer than a thumb") runs at NOTE tier
against real pixels, question attached, never a verdict. Within-group gaps
stay smaller than between-group gaps or the grouping doesn't read (lint
NOTE). In a variant set, sibling screens share row baselines and text
alignment — row alignment is what makes a variant pair diffable at a
glance. That alignment is now load-bearing, not just tidy: it is how the
lint pairs the two screens' blocks, so a label that changed on one frame
and not the other gets caught (v0.6). Declare the pairing explicitly with
`customData.variant_of: <frame-id>` on the second frame when the shapes
differ enough that inference would miss it.

**Reading order is a computed fact**: a frame's content, sorted top-to-
bottom then left-to-right (6px row tolerance), is the only sequence claim
a wireframe can make — exactly what WCAG 1.3.2 will ask about later.
`reading_order_set` narrates it when a screen first lands;
`reading_order_changed` fires only when an edit reorders it (quiet rounds
stay quiet). A submit button preceding its inputs in that order is a lint
WARNING. Focus order (2.4.3) is a question, never a verdict — W3C is
explicit that focus need not follow visual layout.

## Form conventions (GOV.UK, OGL v3 — see NOTICE.md)

The evidence-backed defaults for any form screen. Seeder rules:

- **Label above the input**, sentence case, no colon. Never a placeholder
  as the label (it vanishes on focus). Hint text: one short sentence under
  the label, drawn as decoration.
- **"(optional)" in the label of optional fields — never asterisks** on
  required ones (asterisk-in-label is a lint WARNING).
- **Input width ∝ expected answer length** — width is a channel carrying
  data-model information. Buckets (grid-aligned):

  | Expected answer | Width | Examples |
  |---|---|---|
  | 2–3 chars | 60px | day, CVC |
  | 4–6 chars | 96px | year, sort code |
  | ~10 chars | 168px | postcode, phone |
  | ~20 chars | 264px | name fields |
  | long text | full width | email, address line |

  Known field types seed at their bucket. Three-plus inputs sharing one
  width draws the lint question: "is every answer the same length?"
- **Error message wording** (auto-checked when error text changes): DO
  reuse the question's own words and name the specific failure ("Enter a
  postcode like AA1 1AA"). DON'T: "please", "sorry", "invalid", "oops",
  bare negatives ("wrong input"). One narration line when something
  trips; silence when clean.
- **One question per frame** is GOV.UK's default and this skill's named
  **offer, not default**: when seeding a form flow, name the option ("the
  split version is N frames, one question each — want that instead?") and
  let the conversation pick. One question ≠ one input (a date of birth is
  one question, three fields); research can justify merging; internal
  high-frequency tools may group. A multi-frame form is ONE artifact with
  N frames — one view, one budget check per frame.

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
| `reading_order_set {screen, order}` | screen first drawn | "linearised, this screen reads: … — that the order you mean?" |
| `reading_order_changed {screen, order, was}` | an edit reorders the linearisation | surfacing a layout decision the user may not have noticed |

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
  action button. A list screen owes its **empty-state twin** (below).
- **Detail/form screen**: nav, title block, 2–4 inputs at bucket widths,
  primary + secondary buttons — and the one-question-per-frame offer.
- **Dashboard**: nav, 2×2 block grid with labeled regions, one callout
  annotation.
- **Error variant** (of a form screen): summary block titled **"There is
  a problem"** at frame top, one row per errored field; inline error
  adjacent to each failed input, **identical wording both places**; the
  user's input preserved in the boxes. Seeds when an error state gets
  discussed and has no shape.
- **Check answers**: one row per collected answer with a Change link,
  then the commit button ("Accept and send"). The **review-step arm of
  the irreversibility guard**: multi-step data entry ending in an
  irreversible submit gets this frame (flows: confirm / undo / review).
- **Task list**: one row per task with a **text status** ("Completed" /
  "Not started") — no overall percentage bar, any-order completion.
  Status text is exempt from the progress-indicator question.
- **Confirmation**: the terminal frame after an irreversible submit —
  panel with reference number, then a "What happens next" block. A flow
  whose submit has no post-submit frame draws the question.
- **Start page**: title, one-line summary, "Before you start — you'll
  need:" list, Start button. The constructive answer to cross-screen
  memory questions: if an input's value arrives from off-canvas (a
  letter, an email), this is where the user learns to bring it.
- **Address lookup** (mini-flow, three frames): postcode lookup → pick
  from list → "enter it manually" fallback. The canonical justified
  multi-field merge — and it always carries the fallback path.
- **Confirm-email loop** (flow + holding frame): enter email → "Check
  your inbox" frame (with "not received? resend / change address"
  escape hatches) → re-entry via the link. The journey leaves the app —
  say so on the canvas.
- **Empty-state twin**: any frame whose main region is a repeated-row
  list gets its brand-new-user variant proposed once — reason ("No
  orders yet") + guide-to-action. Drawn as a variant screen in the same
  batch: a pair costs one view.
- **Sign-in / create-account pair**: email+password frames plus the
  forgot-password branch — a composition of question pages; seed it as
  such rather than inventing new vocabulary.

Complexity budget: **max 9 blocks per screen** (see `references/layout.md`
for the two-limit budget); more → split the screen or add a view.

---
*Template structure from diagram-design (MIT — see NOTICE.md). Archetypes
and the annotation taxonomy re-expressed from wireframe-spec and wireframing
skill conventions; interface-state list re-expressed from ui-ux-pro-max
(short factual material, rewritten — provenance noted, nothing copied).
Output mode and fold from this project's v0 refinement audit; app-shell,
12-col/8pt rhythm, skeleton/empty/error state-quality, and
internal-≤-external spacing from bm629/agent-skills (MIT). Form
conventions, width table, error-wording rubric and the error-variant /
check-answers / task-list / confirmation / start-page / address-lookup /
confirm-email archetypes from the GOV.UK Design System (OGL v3, ©
Crown copyright — see NOTICE.md); empty-state pairing re-expressed from
NN/g guidance (cited, nothing copied); aesthetic-usability grounding:
Kurosu & Kashimura 1995 (cited).*
