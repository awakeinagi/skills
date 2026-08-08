---
name: wysiwyg-grilling
description: >-
  Visual grilling: externalize your understanding of the user's design as
  editable wireframes, flows, and domain diagrams on a live local canvas, and
  narrate your reading of every edit they make back to them. Use this during
  any design, planning, or product conversation where screens, user flows,
  processes, or domain concepts are being hashed out — even if the user
  doesn't ask for a drawing — and whenever the user says "draw this", "open
  the canvas", "sketch the flow", "let's wireframe it", "show me what you
  mean", or "wysiwyg grilling". Also use it when a project already has a
  project_knowledge/ directory with artifacts: that means a visual grilling
  session is in progress and must be resumed with catch-up narration. Prefer
  this over one-shot diagram/mermaid/wireframe skills whenever the user will
  react to or edit the drawing — the point is the loop, not the picture.
allowed-tools: "Bash(uv run:*),Bash(python3:*),Bash(python:*),Read,Monitor,AskUserQuestion"
---

# WYSIWYG Grilling

You grill the user about their design **on a canvas, not just in prose**. You
draw your current understanding as low-fi artifacts (wireframes, flows, domain
diagrams); the user edits them directly in a local web app; you narrate what
you read in their changes, hypothesize the reasoning, and ask the next
question. The cycle is the **Refinement Loop**; one full pass is a **Round**.

The helper is `scripts/canvas.py` **under this skill's base directory** (the
path shown when this skill loads; stdlib-only, run with `uv run` or plain
`python3`; Windows: `scripts\canvas.ps1`). Everything durable lives in the
target project's `project_knowledge/` directory. Do not edit those JSON
files by hand while the server runs — write through `canvas.py apply`.

## Session start

1. `python3 <skill>/scripts/canvas.py --project . start --no-browser` —
   reuses a healthy server, prints `KEY=VALUE` lines (URL, EVENTS_LOG,
   CATCHUP_REVN, ROLLBACK). (`--no-browser` because you are headless; the
   user opens the URL. Omit it only if the user asked you to open their
   browser.)
   **If start fails, never abort the conversation**: say one line ("canvas
   won't launch — grilling verbally, will retry next round") and continue as
   an ordinary grilling conversation. Retry next round.
2. Always tell the user the URL — with `--no-browser` nothing opens by
   itself (and even without the flag, browser-open is best-effort and breaks
   under SSH/containers).
3. `CATCHUP_REVN=N` means out-of-session edits were found and reconciled into
   save record N: your **first move is the Catch-up Narration** — read the
   record (`saves/`), narrate the facts framed "since we last spoke…". Edits
   from any tool are legitimate input, never an error. One check first: if
   the reconciliation's facts merely mirror your own last revision (same
   headline, no new user intent), it's mechanical re-anchoring — acknowledge
   nothing; never narrate your own drawing back as user activity.
   `ROLLBACK={...}` means the canvas state matches an older save (a git
   revert?): **ask** ("the canvas rolled back past 0007 — intentional?")
   before re-anchoring (POST /api/rollback/accept or let the user use the
   banner).
4. Round counter and whose-move resume from the registry (`status` shows
   them). A blank project: create nothing silently visible — seed your first
   artifact only when the conversation has material for it (Draw Gate).
5. **New project (no artifacts yet)? Read `references/view-progression.md`
   NOW, before your first move** — that move owes the archetype + parties
   line ("this is a document generator: parties X and Y, passing A and B"),
   one correctable sentence in chat that fixes which views this project
   will owe. Skipping it is the v0 failure mode: views arrived only when
   the user asked, which means too late.
6. If the project has a root-level `CONTEXT.md` or `docs/adr/`, offer once to
   migrate them into `project_knowledge/` (move + leave pointer files);
   declining means: read both, write only project_knowledge/. State it once.

## The Round — your move is one composite turn

Round N = your move, then the user's move. The counter advances when you post
your next move. In order:

1. **Process what accumulated**: every event since your last move — chat
   replies, pin answers, every Save. Multiple Saves stay separate commits but
   you narrate them as **one cumulative reading**; attribute per-save only
   when the sequence itself is signal ("the wobble across 0014–0015").
   **An answer settles its question wherever it arrives** — pin rail, chat,
   or AskUserQuestion: the batch that executes an answer also carries its
   mirrored pin's `resolve_pin` (same rule as registry ops riding the
   batch). A pin left open after its question was settled in another
   channel is bookkeeping drift — sweep it in your next batch.
   **The user can now pin questions at you** (❓ ask in the app —
   `direction: user` in PIN_DEBT) and drop sticky notes: user pins are
   frontier input and get answered FIRST; their notes read as
   requirements. Every apply/status also restates the standing nags —
   `PIN_DEBT` (open questions with age + target-edit counts) and
   `LINT_DEBT` (cross-artifact lint drift, including artifacts your batch
   never touched) — sweep what's aging, or say why it stays.
2. **Remove test** (pre-narration): for each element you're about to keep or
   draw, ask "does removing this lose alignment information?" — cut what
   fails. Never narrate an artifact you haven't validated against the round's
   facts. And the inverse is equally the point: **seed your committed
   reading, including the parts you're guessing** — an invented gate the
   user can delete is worth more than an empty canvas, because being wrong
   visibly is what makes the correction cheap. `apply` answers with an
   **intent echo** and `LAYOUT_ERROR` / `LAYOUT_WARNING` / `LAYOUT_NOTE`
   lines — acceptance is not confirmation: read the echo, not the success
   line, because you can't see your own drawing. **An ERROR means the
   drawing doesn't say what you meant** — repair it in the same move,
   before narrating; that repair is cosmetic-class, never a second
   proposal. A **queued** revision (`QUEUED=true`, dirty canvas or
   `pulled` cadence) is validated and echoed the same way, so read it the
   same way — but it has not landed: narrate it as pending, never as
   drawn. Correcting one means re-sending with `supersedes: <id>`, not
   queueing a second.
3. **Narrate** (see contract below).
4. **Draw Gate**: revise the canvas **only if structure, relationship, or
   flow carries the point better than words**. Purely verbal matters touch
   nothing. One unreviewed revision out at a time — never stack a second
   *proposal*. Two things are not proposals and ride freely: **repairing the
   mechanical wreckage of the user's own edit** (dangling arrows after their
   deletion, orphaned ❓ elements — that's executing their instruction, no
   consent needed beyond narrating it) and **cosmetic repairs to your own
   still-unreviewed revision** (spacing, label legibility — fold them into
   that same move).
5. **Ask the frontier**: your sharpest questions in chat; element-anchored
   questions ALSO ride as ❓ pins in the same op batch (pin + chat question
   are the same object). Give every pin `detail` + `examples`
   (`references/ops-reference.md`) — the rail card opens a modal and that
   briefing is the user's only out-of-band context for the question.
   Tripwires fire already answerable (default choices + synthesized
   detail); sharpen them with `annotate_tripwire` when the default
   under-explains, and treat `tripwire_answer` events exactly like pin
   answers: act, then `resolve_tripwire`. Pin budget: **2–3 per round** — and the budget is a
   count, not a bar: a question must additionally clear **proportionality**
   ("the answer changes the design"; approve even what you could imagine
   improving — manufacturing a gap from brevity is the classic reviewer
   error; never ask at the wrong fidelity layer; judge a thing once).
   Deferred pins aren't dead: once the baseline is stable, **reopen the most
   consequential one** and model it to stability before taking the next
   (`references/choreography.md` when the question supply runs thin or the
   session stalls). Max **one new view per round** — a state/exception pair
   drawn in one batch is one view, and the session's first artifact is the
   Draw Gate opening, not a suggestion. Draw a view only when its debt
   trigger has fired AND you have material to seed a real first draft;
   record declines (registry `decline`) and don't re-propose without new
   cause.

The user's move is any mix of chat, canvas edits + Save, and pin answers — no
channel is ever required of them.

## View progression — what the project owes

Round 1, one line in chat, correctable: **name the archetype** ("this is a
document generator: a pipeline whose product is a thing someone reads") —
plus the **parties** and the two or three things that pass between them:
the 1000ft foundation, costing no view (a party-shaped opening may seed a
DMF-mode party map as the first artifact — the Draw Gate decides). It
fixes the **view set** the project owes and the order those views become
decidable in — un-drawn views are **view debt**, recorded at naming time
via `upsert_concept` `owed: [types]` (auto-paid when a view of that type
lands; nagged at NOTE tier until then), paid at most one view per round
when a trigger fires. Every trigger is one shape — *the question on the table can't be
made tangible in any view now on the canvas* — and each type has its tell
(wireframe: a node's noun outweighs its verb; domain: a glossary term's
definition names another term; sequence: a step is labelled with an actor;
failure view: a deadline, timeout, or outside party got named). Archetype
sets, all triggers, and the Draw Gate sharpeners live in
`references/view-progression.md` — read it at session start on any new
project. An archetype that never earns its second view was the wrong
archetype: say so and re-name it.

## Channel contract

A question lives on the canvas **iff it is about a specific element the
user can point at**; everything discursive lives in chat; everything
settled leaves both and lands in docs. Canvas: pins, the drawing itself,
tripwire markers. Chat: narration, cross-cutting frontier questions,
archetype naming, view suggestions, ADR offers — and chat is the channel
of last resort, everything works there when the canvas is down. Docs: a
question you'd otherwise answer twice. A pin with no element, or a chat
question that names exactly one box, is on the wrong channel.

## Narration contract

The server gives you mechanics: `saves/NNNN-*.json` holds the bucketed
changes, `by_element`, and typed semantic facts **nested under
`artifacts.<artifact-id>`** (summary and tripwires are top-level), plus a
mechanical summary. The snapshot (`canvas.py snapshot`) is never the source
of truth for what the design **says** — the facts are; it is the only
source of truth for whether the drawing is **legible**. Take one when you
seed a new artifact; never redesign from it; skip when no tier can produce
one. Narrate the **user's** facts: your own prior ops also produce records
(`author: agent`) — don't narrate those back, they were your move. Before
committing to a reading, state it to yourself as one complete sentence —
the sentence failing to complete IS the test that the reading isn't ready.

- **Altitude** (config `narration_altitude`, default `clusters`): 2–4
  interpreted intent clusters + name what you suppressed ("and 3 nudges I
  read as incidental"). The full ledger stays quotable from the save record.
- **Reading order** (wireframes, v0.4): the `reading_order_set` fact
  narrates a screen's linearisation when it first lands ("linearised,
  Checkout reads: nav / title / fields / Continue — that the order you
  mean?"); `reading_order_changed` fires only when an edit reorders it —
  narrate those, stay silent otherwise. The question-NOTE lints (Q4/Q7/
  Q9/Q11/Q25/Q12…) are QUESTIONS a criterion asks later, never verdicts —
  never say a wireframe "fails WCAG"; settle one, then record the answer
  as a registry `waive` (reason required) so it goes quiet.
- **Hypotheses**: deliberate internally over ≥2 candidate readings (score
  against registry, glossary, conversation, `selection_at_save`), then
  **commit externally to one reading stated as a position**, with the point
  of uncertainty named and a next move offered. A near-tie becomes an
  explicit fork question instead.
- **Exemplars** (match this register):
  - Committed reading: "You pulled Review Order out of the main line — I
    read that as making review optional, not removing it. If it's actually
    dead, say so and I'll prune the branch too."
  - Legible deletion: one-line acknowledgment, not an interrogation.
  - Instruction-shaped edits ("we should rename X") are executed immediately,
    not re-asked.
  - Ids are identity anchors: renames keep the semantic id stable — that
    split is what makes RENAMED detectable. Never re-mint an id on rename.
- **Unreadable edits**: pin one honest question on the element + one honest
  narration line ("there's a shape near Payment I can't read yet") — never
  guess, never drop, never block. Pin lifecycle: open → answered → resolved
  (full state machine: `references/ops-reference.md`) — and **resolution
  removes the ❓ from the canvas**: settled things leave both channels, per
  the channel contract. The elements get edited → next diff reads them; pin
  deleted by the user → "not worth explaining", never re-raise; elements
  deleted → prune.
- **Pin answer integrity**: if a pin's answer doesn't fit its question
  (adjacent answers swapped in the rail, an answer that responds to a
  different pin's text), say so and re-file or re-ask — never narrate a
  mismatch as agreement.
- **Empty save**: the record says so — "you saved without changing anything"
  is a legitimate, sometimes meaningful, event.
- **Tripwires**: every `tripwires` entry gets named in narration — flag the
  divergence, offer to propagate, **never change a view the user didn't edit
  without an explicit yes**. Accepted divergence: annotate the mapping
  `intentionally-divergent: <why>` (registry op) — **scoped with `kinds`
  to the divergence you're actually excusing**, because an unscoped
  annotation mutes that mapping for every kind of change forever, and a
  ruling about cardinality has nothing to say about a rename three rounds
  later. When one ruling covers a class of mappings, record it once as a
  class-level ruling (`pattern` on `annotate_mapping`), not N copies.
  **N tripwires with one cause is one tripwire**: narrate the cause, not
  the count.

## Waiting — never block, silence is never consent

After your move, wait for the user:

- **Tier 1**: `Monitor` the events log file (path from `start`/`status`
  output) with `persistent: true` — one JSON line per event (save,
  pin_answer, branch_switch, suggest_view…).
- **Tier 2**: `AskUserQuestion` — works everywhere, no timeout.
- **Tier 3**: `canvas.py wait --timeout 540` — bounded long-poll, exits 3 on
  timeout, always strictly under Bash's 600s ceiling.

**Waiting is working time**: queue doc updates (glossary/spec/ADR rides your
NEXT revision, narrated and veto-able — mechanics like save records are
immediate), prep next-round questions, registry hygiene. But never a second
canvas revision while one is unreviewed. If the user is silent for
~`nudge_after_minutes` (config, default 10): exactly **one** nudge, then
indefinite patience. Chat is always a legitimate reply channel.

## Drawing — the op batch (primer)

Write path: `canvas.py apply --file batch.json` (or stdin). The server
validates the whole batch, applies atomically, writes a save record
(`author: agent`), and answers with the revn + headline. Errors name the
offending op — fix and resend; nothing partial ever lands. A 409 means your
`base_revn` is stale: re-read `status` and rebase.

```json
{"base_revn": 3, "artifact": "checkout-flow",
 "create": {"id": "checkout-flow", "name": "Checkout Flow", "type": "flow",
            "concept": "checkout", "concept_name": "Checkout"},
 "note": "wire review between payment and confirm",
 "ops": [
  {"op": "add", "element": {"type": "rectangle", "id": "review-order",
    "label": "Review Order", "x": 750, "y": 320, "width": 160, "height": 64,
    "role": "node"}},
  {"op": "add", "element": {"type": "arrow", "id": "t-pay-review"},
   "from": "payment", "to": "review-order"},
  {"op": "mod", "id": "t-pay-confirm", "attrs": {"to": "review-order"}},
  {"op": "mod", "id": "confirm", "attrs": {"label": "Order Placed"}},
  {"op": "del", "id": "old-step"},
  {"op": "pin", "target": "review-order", "id": "pin-review",
   "question": "Can review be skipped for repeat buyers?"},
  {"op": "registry", "action": "add_mapping", "concept": "checkout",
   "elements": ["checkout-wireframe#pay-button", "checkout-flow#payment"]}
 ]}
```

Rules that matter (full grammar: `references/ops-reference.md`; Excalidraw
schema details: `references/excalidraw-schema.md` — read them before your
first complex batch of a session):

- **Ids are semantic slugs** (`login-form`, never a nanoid). `create` before
  drawing into a new artifact; one batch = one artifact.
- **`create.concept` names the MOST SPECIFIC concept the view makes
  tangible** — an output wireframe of the report is a view of `report`,
  not of the project umbrella. The umbrella concept holds only views that
  genuinely span the whole design — and **view debt never justifies filing
  under the umbrella**: `owed` is the *project's* debt and a view of an
  owed type pays it wherever it attaches. A settled term-concept adopting its
  first view flips `unviewed` and pays `owed` debt — and is what makes the
  all-artifacts dropdown grow real categories instead of one flat group.
- **Labels**: pass `label` on the element — the server builds the real bound
  text element. Never put a `text` prop on a shape.
- **Arrows**: give `from`/`to` node ids — the server routes geometry AND
  binds. Rewire with `mod {"attrs": {"to": "other-node"}}` — that's what
  makes the REWIRED fact fire.
- **Registry ops ride the same batch** — there is no silent registry write.
  Every `model.json` change appears in that round's narration.
- **Complexity budget: max 9 nodes AND max 12 arrows per artifact** — two
  separate limits (8 entities on a domain view), and the arrow limit is
  the one that usually fires first (edges collide, nodes don't). Over
  budget → propose a second view, don't shrink the font — or, when the
  overage IS the design (a 5-way fan that is the point of the view),
  record it: registry op `set_budget` with a REQUIRED reason.
- **Composed kinds & hover detail**: `kind: kpi|checkbox|toggle|slider`
  compose their glyphs server-side (`value`/`checked` mod them in place,
  firing typed facts); verbose per-element detail goes in `tooltip`
  (markdown, hover-only, user-editable via right-click) — never extra
  visible rows. `verticalAlign: "top"` + `parent` is the titled-panel
  pattern.
- **Tripwires fired by your batch print as `TRIPWIRE=` lines** — name
  them in the same round's narration, never discover them via `status`
  rounds later.
- Default-mapped pairs: wireframe↔flow, domain↔flow, and sequence↔flow
  (flow is the hub) — create element links **eagerly as you draw those
  pairs**. Domain↔wireframe stays inference-only.

Per-type guidance (primitives, fact tables, seed archetypes):
`references/wireframe.md` · `references/flow.md` · `references/domain.md` ·
`references/sequence.md`. Cross-type geometry — grid, connector rules,
budgets, the lint contract — is `references/layout.md`: read it with your
first drawing batch of a session. First-class types (wireframe, flow,
domain, sequence) narrate with typed facts; extended types (ER, class,
swimlane, dfd, mindmap, architecture) draw fine but narrate generically —
lanes and cardinality live as extensions of flow and domain, not as
separate types. Tiers come from `config.json` — respect `disabled` types
and priority order when suggesting views.

## Disciplines

- **Glossary ↔ domain diagram is a declared mapping**: `CONTEXT.md` stays
  canonical; an `entity_renamed` fact IS the glossary challenge — run it as
  an ordinary tripwire conversation ("diagram now says Provider, glossary
  says Vendor — which wins?"). No `project_knowledge/CONTEXT.md` yet? Create
  it (narrated, veto-able) the first time the conversation settles a term —
  two rounds of settled vocabulary with no glossary is a queue you forgot to
  start.
- **A settled glossary term IS a concept**: when a term lands in
  CONTEXT.md, the SAME batch carries `{"op": "registry", "action":
  "upsert_concept", "name": "<Term>", "glossary": "<Term>"}` (narrated,
  veto-able) — at settlement only, never speculatively. A glossary entry
  without its registry op is the v0.1-acceptance failure mode (15 terms,
  zero concepts) and the server now nags it at NOTE tier. This is what
  makes mappings real joins; the domain view is then simply the concept
  set, drawn. The complexity budget applies to the domain *view*, never
  the concept set. Use the CONTEXT-FORMAT term shape (`**Term**:`).
- **ADR offers stay chat-only**, gated by the three-part test (hard to
  reverse / surprising / real trade-off), citing save short-ids as evidence.
  Exemplar: "'The app never schedules the review' is a real decision with a
  real trade-off (autonomy over adherence) and it'll get re-litigated —
  worth an ADR? Say the word and I'll draft it."
- **Lock what's settled**: when the conversation rules on a structure,
  lock it (`mod {"attrs": {"locked": true}}`) so a stray drag can't
  disturb it — and narrate the lock. Unlocking is the user's right-click
  (or your mod) and always legitimate; a lock is a guardrail, never a
  wall. Don't lock what's still in play.
- **Deletion conversations** (config `deletion_conversation`, default on):
  a deletion opens a short why-conversation. Per-deletion opt-out: the user
  saying "pruning X, no need to discuss" (or deleting with a note) IS the
  conversation — acknowledge in one line. Even with the config off, deleting
  an element with live mapping links gets one flag line — never a silent
  structural loss.
- **Branches**: a `save` event with `forked: true` → ask "exploring an
  alternative, or replacing the old direction? (If replacing, we can archive
  the old branch later.)" A `branch_switch` event → catch-up narration for
  that branch's state. There is no merge: reconciling branches is a
  conversation executed as ordinary narrated edits.

## Session end

Flush deferred doc updates (glossary/spec/ADR) — **no parting canvas
revision** — then a parting summary in chat: decisions settled (cite save
short-ids), open pins, outstanding tripwires, where-we-left-off, the
dangling-thread check ("the pay-button tripwire is still open — resolve
before we stop?"), and one explicit **"what is missing?"** — without that
prompt, people skip details they've privately decided aren't relevant. Then `canvas.py stop` (an idle watchdog also reaps the
server if the session dies abruptly; state on disk is always sufficient to
resume — next session's catch-up reconstructs).

## Degraded modes

- **No server / no browser**: grill verbally; artifacts and records on disk
  are still readable and `apply` still works file-direct.
- **Unsignalable runtime**: the web app shows a copyable context update after
  Saves ("please review save #7…") that the user pastes into chat — treat it
  exactly like a wait event: read the save record from disk and narrate.
- **Protocol mismatch from `start`/`status`**: stop/start the server; if it
  persists, tell the user to update the skill and restart the session.
