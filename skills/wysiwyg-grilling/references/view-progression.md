# View progression — what a project owes

Read this at session start on any new project, and whenever a trigger below
fires mid-session. It answers two questions the Draw Gate alone cannot:
*which* views this project will need, and *when* each becomes decidable.

## The floor

The Draw Gate and the one-view-per-round budget are ceilings — they say when
NOT to draw. This file is the floor: nothing else ever puts a view in your
head, and a session without a floor reaches views only when the user asks
(which means too late — the user is doing your job).

Round 1, one line in chat, correctable: **name the archetype** ("this is a
document generator: a pipeline whose product is a thing someone reads") —
and with it the **parties** (people, systems, outside organisations) and
the **two or three things that pass between them**, one line each. That
parties-and-passings line is the 1000ft foundation: it costs no view, it
feeds the archetype naming, and everything later refines it. It fixes the
**view set** the project owes and the order those views become decidable
in. Un-drawn views in that set are **view debt**: record it ON the
registry when you name the archetype — `{"op": "registry", "action":
"upsert_concept", "id": "<umbrella-concept>", "owed": ["domain", ...]}`
— never draw it speculatively. Registering a view of an owed type pays
that debt automatically; unpaid debt shows in `status` (`VIEW_DEBT=`),
nags at NOTE tier on every apply, and renders as a dashed "owed" chip in
the rail. Debt is paid at most one view per round, only when its trigger
fires. An archetype that never earns its second view was the wrong
archetype — say so and re-name it (and rewrite the `owed` list to
match).

**The debt is the project's; the view is its own concept's.** `owed`
gets written on the umbrella because that is where the archetype is
named — it does NOT mean the paying view belongs there. Draw the owed
wireframe of the report onto concept `report` (`create.concept`), and
the umbrella's `wireframe` debt clears anyway: payment is project-wide
by type (ADR 0010). Filing views on the umbrella to make the nag go
away is the failure this rule exists to prevent — it buys `VIEW_DEBT=none`
with a concept graph in which nothing but the umbrella has a view.

**Party-shaped openings seed a party map.** When the user's opening
material describes parties exchanging things ("the vendor sends us files,
we send the team a report") rather than a process order, the first seed
may be a **DMF-mode sequence** (`references/sequence.md`): 3–5 party
boxes, 2–3 numbered messages with contents. The boxes at this altitude
are actors and systems — things that already exist; no bounded context is
invented to draw it, and when one crystallizes later, the party's kind
flips to `context`, which is itself a narratable design event. The Draw
Gate still decides: a process-shaped opening seeds the archetype's usual
first view instead.

## Archetypes (defined by what the project produces)

| Archetype | Produces | View set, in decidability order |
|---|---|---|
| **A. Document generator** | report, digest, email, briefing | flow → output wireframe (normal + degraded, one pair) → domain → failure view if a deadline exists |
| **B. Data pipeline / ETL** | data, not a document | flow (happy) → **flow (failure & degradation)** → domain with cardinality → sequence if call order is contested |
| **C. Interactive application** | screens someone drives | **wireframe (primary screen)** → flow (paths between screens) → wireframe state set → domain |
| **D. Multi-actor workflow** | approvals, handoffs | flow → lane overlay → sequence (protocol) → domain |
| **E. Integration / service** | responses for other software | **sequence** → wireframe of the response payload → flow (internals) → failure view (always owed) |
| **F. Decision support** | a judgement | **domain first** — the disagreement is definitional before procedural → flow → wireframe |

Note the order *reverses* between archetypes (C leads with a wireframe, A
with a flow, F with a domain) — that reversal is why naming must happen in
round 1, not when a question is already stuck.

## Triggers (every one is the same shape)

**A view is owed when the question on the table cannot be made tangible in
any view now on the canvas.** Each type has its tell:

- **Flow** — the user described an order ("then", "after", a clock time).
  Cheapest seed; usually round 1.
- **Wireframe** — *a node's noun outweighs its verb.* "Send to Team" hides
  the thing being sent. Wireframe the **deliverable**, whatever it is —
  screen, report, email, CLI transcript, API payload. Pipelines "don't have
  screens", but they have outputs, and the output is the product.
- **Domain** — *a glossary term's definition names another glossary term.*
  That's a relationship, and a flow can't hold it: flows are verbs, domains
  are nouns. (Mechanical twin of the existing glossary discipline: a
  cross-referencing glossary is a domain diagram specified in prose.)
- **Sequence** — a step is labelled with an actor ("Vendor API",
  "Agent: …"). The flow is smuggling a conversation into a box.
- **Failure / degradation view** — the conversation named a **deadline,
  timeout, or outside party**. Degradation crammed into annotation strings
  on the happy path is the complexity budget being dodged.
- **Lane overlay** — a second owner is named for any step; "who is
  responsible when this fails" is unanswerable on a single-owner flow.
- **Structural hole** — a `LAYOUT_ERROR`-class gap (black hole node, illegal
  pairing) is a conversation the team hasn't had: it becomes a frontier
  question, and sometimes the question needs a new view to be askable.
- **Friction** — edits keep colliding in one region of a crowded artifact
  (repeated moves to make room). Split by *where the elbows are*, not only
  by node count — friction is a split trigger orthogonal to the budget.
- **Audience shift** — the conversation's audience changed altitude
  (exec/PM vs architect vs operator). Suggest the view at the new
  audience's level rather than stretching one diagram across all of them;
  ≤20 elements at any level.

## Sharpening the Draw Gate

- If a sentence says it faster, write the sentence — a drawing must beat
  prose at *this* point, not decorate it.
- Depict the **mechanism**, not its name: a box labeled "Auth" is a noun; a
  gate the request passes through is a claim.
- When comparing options, **draw the difference** — two small variants side
  by side beat one annotated composite; the user deletes the wrong one.
- Match complexity to the stakes: a forced-minimal sketch of a genuinely
  complex mechanism is as misleading as clutter (the budget's
  counterweight, not its repeal).
- Introduce element kinds progressively — start with the two or three the
  conversation needs, add kinds when the current grammar plateaus, and
  narrate the increment ("adding a lane now because two owners exist").

## Altitude ceiling

The project-level ladder behind the archetypes runs Understand → Discover
→ Decompose → Strategize → Connect → Organise → Define → **Code** — and
this skill's altitude ends at *Define*. Class diagrams, schemas, and
implementation choices mean the grilling stage is over; say so rather
than drifting down. (One candidate future view sits just inside the
ceiling: a core-domain quadrant chart — complexity × differentiation,
flagship fact `strategic_position_changed` — which passes the promotion
test but needs the `strategic` attribute in real use first. Flagged, not
promoted.)

## Budget clarifications

- The unit of the one-view budget is the **question**, not the artifact: a
  normal/exception wireframe pair drawn in one batch answers one question
  and costs one view.
- The session's **first artifact is not a suggestion** — it is the Draw
  Gate opening. Seed it when the conversation has material (unchanged).
- Abstract disagreement gets drawn, not argued: when chat loops twice on
  the same point, the next move makes it visible and lets the user edit it.

---
*Archetype/view-set framing and triggers from this project's v0 refinement
audit. Rush-to-goal sequencing, friction split, incremental notation, and
"structural holes are unheld conversations" adapted from EventStorming
(Alberto Brandolini, eventstorming.com; via melodic-software's skill, MIT).
Audience→level from softaworks C4 (MIT). Draw-the-difference,
mechanism-not-name, complexity-matches-stakes re-expressed from the
built-in artifact-diagramming skill.*
