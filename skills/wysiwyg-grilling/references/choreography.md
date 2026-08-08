# Choreography — question generation and round sequencing

Read this when a session stalls (rounds produce edits but no decisions),
when your question supply feels thin, or before a long grilling session on
a complex design. These are techniques, not obligations — the pin budget
and the proportionality bar in SKILL.md govern how much of what they yield
actually ships in a round.

## Round sequencing (the ladder)

Early rounds: **rush to the goal** — drive one thin path from trigger to
outcome end-to-end before decorating anything. A design with a complete
spine and no detail beats five perfect fragments; every later technique
needs the spine to work on.

Middle rounds: enrich by walking the structure, not by free-associating.
The techniques below each mine a different seam.

Late rounds: **raise the bar** — after the baseline is stable, reopen the
single most consequential deferred pin, model it to stability, then take
the next. This is the other half of the pin budget: the budget says which
questions wait; raise-the-bar says which waiting question comes back first.
A deferred pin that never comes back was either not worth pinning or is
quietly rotting — decide which.

## Question-generation techniques

- **Reverse Narrative**: walk the flow backward from its end state, asking
  at each node "what must have happened for this to be true?" Forward
  walks confirm what's drawn; backward walks expose what isn't — expect
  this to surface a large fraction of missing steps in one pass.
- **Magic Keywords**: re-read each rule or annotation prepending "Always…"
  or "Immediately…". Most stated rules break under one of the two ("we
  always send at 7:15" — even when the writer hangs?). Each break is a
  frontier question with teeth.
- **Speak Out Loud**: before narrating a committed reading, state it as one
  complete sentence ("When the file lands after the cutoff, the sheet
  ships with yesterday's book, labelled"). If the sentence won't complete,
  the reading isn't committed — the sentence failing IS the test.
- **Structural holes**: every `LAYOUT_ERROR`-class gap (black-hole node,
  illegal pairing, dangling transition) is a conversation the team hasn't
  had yet. Convert it to a frontier question before you convert it to a
  repair.
- **Irreversibility guard** (flows): every destructive or irreversible
  action carries a confirm or an undo — and reversible actions prefer undo
  over a modal confirm. Ask about both directions: the missing guard AND
  the gratuitous one.
- **Tradeoff surfacing** (domain/flow): when a design choice lands, ask
  "what did you give up to get this?" A decision with no named cost hasn't
  been examined; the answer is usually the ADR's trade-off line.
- **Complexity conservation** (Tesler): irreducible complexity doesn't
  vanish — it gets allocated to the user, the application, or the
  platform, and the allocation is the design decision. Two forms; the
  second is the one people dodge. When a step the user calls hard lands:
  **"who absorbs this complexity — the user, the app, or the platform?"**
  (Q14). When a simplification lands: **"you made this simpler for the
  user — where did the complexity go?"** (Q15). Pairs with tradeoff
  surfacing; the answer usually names the component that just inherited
  the work. (Larry Tesler, via nomodes.com — quoted with attribution.)
- **Arrow sunk-cost check**: a drawn arrow calcifies — once it exists,
  people rearrange everything else to preserve it. Periodically re-ask
  "is this relationship still right?" about old arrows, and treat
  deleting/redrawing one as normal, not as rework.
- **Clock precision**: wherever a time condition appears, on any view,
  ask which of **within / after / every** it means — "within 5 minutes",
  "after 5 minutes", and "every 5 minutes" are three different designs.
  Deadlines, cutoffs, retries, and schedules all hide this ambiguity.
- **"What is missing?"**: ask it explicitly near the end of any drawing
  session — without the explicit prompt, people skip details they've
  privately decided aren't relevant. (A session with no hotspots and no
  conflicts doesn't mean agreement — it means somebody was missing, or
  polite.)

## The question bank (v0.4 — a supply, not a licence)

The UI/UX research round (U1–U11) produced 32 questions. **Everything
here is subject to the pin budget and proportionality below** — the bank
nearly doubles the question supply, and over-grilling is this skill's
central risk. Status: *auto* = the server computes it (lint/fact fires
on its own); *trigger* = you ask it when its condition lands; *parked* =
supply only, awaiting its triage chunk — draw on it deliberately, never
sweep it.

| # | Question | Trigger · status |
|---|---|---|
| Q1 | "Linearised, this screen reads: A / B / C — is that the order you mean?" | screen first drawn or reordered · **auto** (`reading_order_*` facts, WCAG 1.3.2) |
| Q2 | "These are grouped by proximity — what's the group called?" | detected unlabeled group · parked (1.3.1) |
| Q3 | "This field has no label. What's it asking for?" | `kind: input`, no label · **auto** (WARN, 3.3.2) |
| Q4 | "All N fields are the same width — is every answer the same length?" | ≥3 uniform-width inputs · **auto** (NOTE, GOV.UK) |
| Q5 | "Screen 2 says Continue, screen 4 says Next. Same action?" | mapped same-function labels diverge · **auto** (NOTE, 3.2.4; the same-word/different-step mirror is a WARN) |
| Q6 | "You ask for the postcode on step 1 and again on step 4. Why twice?" | duplicate input label on a mapped flow path · **auto** (NOTE, 3.3.7 — its exceptions are the user's to claim) |
| Q7 | "This bar is pinned — when they tab to the last field, is it under it?" | declared `kind: sticky-bar` + inputs · **auto** (NOTE, 2.4.11) |
| Q8 | "How do they get out of this screen without finishing?" | frame with inbound transition, no exit · parked (H3) |
| Q9 | "Where does help live, and is it the same slot on every screen?" | `kind: help` on some frames / drifting slots · **auto** (NOTE, 3.2.6) |
| Q10 | "Two screens share a title — which one is the user on?" | duplicate frame titles · **auto** (NOTE, GOV.UK) |
| Q11 | "These two controls are closer than a thumb — intentional?" | 2.5.8 spacing circles intersect · **auto** (NOTE only, never a verdict; needs the 1:1 px declaration) |
| Q12 | "Whose word is this — yours or theirs?" | wireframe label matches a domain term · **auto** (NOTE, waivable; Nielsen H2) |
| Q13 | "What convention are you departing from, and what does it buy?" | unconventional nav/action slot · parked (Jakob) |
| Q14 | "Who absorbs this complexity — user, app, or platform?" | a step the user calls hard · **trigger** (Tesler) |
| Q15 | "You made this simpler for the user — where did the complexity go?" | a simplification lands · **trigger** (Tesler) |
| Q16 | "What's the messiest thing a real person could type here?" | any input · parked (GOV.UK validation) |
| Q17 | "Which of these fields can a user get wrong, and what stops them?" | form frame · parked (H5) |
| Q18 | "After they press this, what tells them it worked?" | primary button · parked — `kind: feedback` is the constructive answer (H1) |
| Q19 | "Is this a decision or a search?" | list of >5 options · parked (the only legitimate Hick salvage) |
| Q20 | "This says 'drag to reorder' — what's the tap-only way?" | drag/swipe annotation · parked (2.5.7) |
| Q21 | "Can they paste from a password manager?" | login/verify/code node · parked (3.3.8) |
| Q22 | "In your error variant, WHICH field is marked, and where does the text sit?" | error state discussed · parked — the error-variant archetype is the constructive answer (3.3.1) |
| Q23 | "What's the peak of this journey, and what's the end?" | flow complete, late round · parked (Peak-End) |
| Q24 | "This ends in a submit that can't be undone — where do they check their answers?" | irreversible terminal · **trigger** (irreversibility guard + check-answers archetype) |
| Q25 | "Does the *user* need to know where they are, or do *you* need them to? GDS removed a 12-step indicator from a live service and completion, time and volume were unchanged." | progress indicator drawn · **auto** (NOTE, waivable) — present BOTH sides; never advise deletion |
| Q26 | "On screen 3, what must they remember from screen 1 — and where did they get it?" | an input whose value plausibly originates off-canvas or on a distant frame (NOT every multi-frame flow) · **trigger** (H6) — the start-page archetype is the constructive answer |
| Q27 | "What's on the second level, and who decided it wasn't first-level?" | disclosure/"more" annotation · parked (NN/g) |
| Q28 | "What does the tenth time through this look like?" | flow the user calls repeated · parked (H7) |
| Q29 | "What is the user calculating or looking up that the system already knows?" | input whose value exists elsewhere in the design · **trigger** (offload) |
| Q30 | "Who uses the answer to this, and what for?" | field protocol sweep · **trigger** (Jarrett) |
| Q31 | "If it's required, what happens when someone types anything just to get past it?" | required fields, in the sweep · **trigger** (Jarrett) |
| Q32 | "Which of these are optional?" — then: say so in the label, never an asterisk | asterisk in label · **auto** (WARN, GOV.UK) |

Never absorbed, by ruling (see NOTICE.md "Refused absorptions" before
adding anything like them): a Miller 7±2 list-length lint, a Hick's-law
option-count lint, a Doherty 400ms response rule, a choice-overload
option cap — their primary sources don't support the popular claims.
"Reduce cognitive load" is likewise banned as critique vocabulary
(unfalsifiable; fails proportionality) — use the three structural moves:
offload (Q29), default ("why isn't the likely answer pre-selected?"),
pre-fill (3.3.7). The error-wording rubric
(references/wireframe.md) runs automatically when error text changes —
one narration line on violations, silence when clean.

## The heuristic sweep (late-round move)

Once per artifact, when its structure stabilizes — a named pass over the
**six decidable heuristics only**: H2 (system language vs user language —
Q12's home), H3 (exits: can they leave every screen?), H4 (consistency —
the 3.2.4 lints), H6 (cross-screen memory — Q26), H8 (minimalism: what
earns its place?), H10 (help — Q9). Findings only, in narration; a clean
sweep is one line ("swept Checkout against the six: clean"). **Never run
all ten** — H1/H5/H9 are only half-decidable at this fidelity and H7 is
out of lane; running them manufactures unsupportable questions.

## The field protocol (forms — Jarrett, CC BY 4.0)

Per form view, run as a **sweep, findings only** — never per-field pins
(a 6-field form × 3 questions = 18 pins against a budget of 2–3). Fill
the table yourself from context (conversation, glossary, mappings),
present it once in chat, and ask ONLY the cells you genuinely can't
infer:

| field | who uses the answer, for what | required? |

Q31 — "if it's required, what happens when someone types anything at all
just to get past it?" — fires only for fields established as required.
It's a data-quality question asked through the interface: the answer is
usually a validation rule, a default, or the discovery that the field
isn't really required.

## Proportionality (the admission bar, expanded)

The SKILL.md bar in one line: a question must clear "the answer changes the
design", not merely fit the budget. What that means in practice:

- **Approve even if you can imagine improvements.** Manufacturing a gap
  from brevity is the most common reviewer error — a thin screen or a
  three-node flow legitimately collapses questions it doesn't need.
- **Stay in lane**: never ask at the wrong fidelity layer. No pixel/
  contrast/font questions on a low-fi wireframe; no implementation
  questions on a domain diagram. The layer that owns the concern gets the
  question, later. The refusal list is specific, and **cite the criterion
  number when declining** — a specific refusal reads as competence, a
  vague one as evasion ("contrast is 1.4.3 and needs real colours —
  that's the design system's call, not this canvas's"):

  | Concern | Criterion | Whose call |
  |---|---|---|
  | color contrast | 1.4.3 / 1.4.11 | design system |
  | text resize | 1.4.4 | build |
  | text spacing | 1.4.12 | build |
  | focus visibility/appearance | 2.4.7 / 2.4.13 | build |
  | response-time thresholds | (Nielsen 0.1/1/10s, "sub-second") | performance budget |
  | keyboard accelerators | H7 | build |

  Target size (2.5.8) is deliberately NOT on this list — it's geometry,
  and geometry is what a wireframe claims; it runs at NOTE-only against
  the declared 1:1 px space.
- **Judge a thing once, in one place.** A concern already pinned, answered,
  or recorded in an ADR is settled — re-raising it because a new view shows
  the same object is duplication, not diligence.
- **Greenfield clause**: never flag inconsistency against shipped state
  that doesn't exist. A proposed model has nothing to drift from.

These four exist because everything else in this file increases what you
*can* ask. Only this section governs whether you *should* — enrichment
without proportionality produces a skill that asks better questions and
too many of them.

## Narrating critique (wireframes especially)

Structure a visual critique as **Observation → Problem → Fix**, and only
where a real problem exists: the neutral observation first ("the primary
action sits below three optional fields"), why it matters ("it's off the
scan path — at this fidelity that's the one layout claim that matters"),
then a concrete fix. Severity in three steps (pass / minor / major) beats
adjectives. Question sets that reliably find something: entry point (is
there one dominant element, does it match the screen's goal?), eye flow
(F/Z-pattern or intentional order, any dead ends?), weight (are hierarchy
levels distinct?), emphasis (exactly one primary zone per view?).

---
*Rush-to-goal, raise-the-bar, reverse narrative, magic keywords,
speak-out-loud adapted from EventStorming (Alberto Brandolini,
eventstorming.com; via melodic-software's skill, MIT). Proportionality,
stay-in-lane, non-duplication, greenfield clause from bm629/agent-skills
reviewer pairs (MIT). Observation→Problem→Fix and the four question sets
from owl-listener's critique-visual-hierarchy (MIT). Irreversibility guard
and tradeoff surfacing from bm629 (MIT). Question bank Q1–Q32 from the
v0.4 U-round: WCAG 2.2 criteria quoted from W3C (with notices — see
NOTICE.md); GOV.UK material under OGL v3; the field protocol from
Caroline Jarrett (CC BY 4.0); Tesler via nomodes.com (quoted with
attribution); Nielsen-heuristic and NN/g-derived items re-expressed,
cited, never copied. The debunked laws are recorded in NOTICE.md
"Refused absorptions" — do not lint them.*
