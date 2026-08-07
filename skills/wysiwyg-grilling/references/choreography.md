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

## Proportionality (the admission bar, expanded)

The SKILL.md bar in one line: a question must clear "the answer changes the
design", not merely fit the budget. What that means in practice:

- **Approve even if you can imagine improvements.** Manufacturing a gap
  from brevity is the most common reviewer error — a thin screen or a
  three-node flow legitimately collapses questions it doesn't need.
- **Stay in lane**: never ask at the wrong fidelity layer. No pixel/
  contrast/font questions on a low-fi wireframe; no implementation
  questions on a domain diagram. The layer that owns the concern gets the
  question, later.
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
and tradeoff surfacing from bm629 (MIT).*
