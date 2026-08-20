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
   under SSH/containers). What they can DO once it's open — sticky notes,
   ❓ ask, insert, tidy, the cadence toggle, and `▶ walk` (steps a
   wireframe's screen frames like a prototype) — is
   `references/canvas-app.md`, with the moment each one is worth naming.
   You cannot see their screen: an affordance you never mention is one
   they never find.
3. `CATCHUP_REVN=N` means a reconciliation landed as save record N — so
   **read record N before you decide it was the user**: its `headline`
   says which of the two happened, and only one of them is theirs
   ("out-of-session drift reconciled: N change(s) differ from history"
   against "load-time repair: … — no outside edits", which is the
   loader tidying its own store and owes no narration). For real
   out-of-session edits your **first move is the Catch-up Narration** —
   read the record (`saves/`), narrate the facts framed "since we last
   spoke…". Edits
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
   **The user can now pin questions at you** (❓ ask in the app) and drop
   sticky notes: user pins are frontier input and get answered FIRST;
   their notes read as requirements. **Which pins are theirs is not on
   the `PIN_DEBT=` line** — `apply` and `status` both print id, status,
   age and target-edit count and nothing else. `direction` (`user` or
   `agent`) is computed and carried in `GET /api/state`'s `pin_debt`,
   and that is the only place it is readable, so "theirs first" is a
   rule you have to open the state for. Every apply/status also restates
   the standing nags —
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
   same way — its lines come back marked `ECHO(queued)=` — but it has not
   landed: narrate it as pending, never as drawn. Correcting one means
   re-sending with `supersedes: <id>`, not queueing a second.
3. **Narrate** (see contract below).
4. **Draw Gate**: revise the canvas **only if structure, relationship, or
   flow carries the point better than words**. Purely verbal matters touch
   nothing. One unreviewed revision out at a time — never stack a second
   *proposal*. Two things are not proposals and ride freely: **repairing the
   mechanical wreckage of the user's own edit** (dangling arrows after their
   deletion, orphaned ❓ elements, and **orphaned notes** — that's executing
   their instruction, no consent needed beyond narrating it; exception: a
   note left deliberately in a deleted thing's place is a **tombstone** —
   ask before moving it, and carry its meaning into CONTEXT.md, because
   positional anchoring won't survive tidy) and **cosmetic repairs to your own
   still-unreviewed revision** (spacing, label legibility — fold them into
   that same move). **A user asking for a still canvas** ("don't push
   anything while I look at this", flipping cadence to `pulled`)
   suppresses this step and this step only — the reading is still owed.
   Answering a turn full of rulings with a held-my-hands line is the
   failure here, not drawing.
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
of truth for what the design **says** — the facts are; it is the best
available evidence of whether the drawing is **legible** — and know its
tiers: tier 1 (connected tab) IS the user's renderer; tier 2 (headless)
and tier 3 (SVG) approximate it, so a tight fit that looks fine there can
still wrap live. When legibility is the question, prefer tier 1; `x-geometry
--diff` names stored widths the editor will wrap. Take one when you
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
- **Ports** (v0.9): the `ECHO=` line names where on a node each end of an
  arrow lands — "leaves the bottom of validate-order, arrives at
  enrich-record's left edge, off centre". It names **ids**, like the rest
  of the echo, not labels. That is the drawing read back to you, so an
  arrow you meant to enter from above and that reports leaving a side face
  is a routing bug you can see without a screenshot. **When the echo is
  silent about an end, describe the PATH and never guess a side** — the
  silence means the arrival slides along the face or comes in obliquely,
  which is exactly the case where a named side would be a sentence the
  user cannot find in the picture. "Off centre" is deliberately as far as
  it goes; it never says which way off.
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
  the count. A **glossary** challenge needs no annotation to settle it:
  answering it records the ruling against the term itself, so the other
  entities drawn from that term stop asking. Still act on the answer —
  "the glossary follows" is a CONTEXT.md edit you owe — but do not
  re-ask, and do not read the silence as the drawing having converged.

## The event loop — watch the log, react to their moves

**This is standing procedure, not a fallback. After every move of yours,
arm a watch on the events log, and treat what lands as the user's move.**
The log path is printed by `start` and `status` as `EVENTS_LOG=`; one
JSON line per event.

**Arm it like this** (`Monitor`, `persistent: true` — you are re-invoked
per event and your turn still ends normally). Filter to the user's events
or you will be woken by your own echoes; in a real session your own
revisions were 87% of the traffic, and an unfiltered agent narrated its
own drawing back as the user's move:

```
tail -n 0 -F "$EVENTS_LOG" | grep -E --line-buffered \
  '"type": ?"(save|pin_answer|tripwire_answer|checkout|checkout_live|branch_switch|branch_archive|suggest_view|config_changed|reconciliation)"'
```

That list is **every** type that belongs to the user, and it is exactly
what `wait --for user` matches server-side. Do not trim it: two
independent agents had to add `checkout`, `checkout_live` and
`branch_archive` back after copying an older, shorter version.

**The event is not a notification — for some moves it is the only place
the content exists.** `status` will tell you a pin is `answered` (in
`PIN_DEBT=`); it will never tell you *what* the user answered. That text
is in the `pin_answer` event and in `/api/state`, nowhere else. The same
is true of a `config_changed` patch. If you only ever poll `status`,
canvas-first questions become a place where the user's answers go quiet.

**Four things the log is the only witness to:**

| You see | It means | Do this |
|---|---|---|
| `config_changed` with `canvas_updates` | the user changed the **cadence** mid-session | re-read `CADENCE=` in `status`; under `pulled` your next apply queues instead of landing, and the response says `QUEUED=true` |
| `reconciliation` | either a load-time repair or a real outside edit | read the record's `headline` — it names which ("load-time repair: … — no outside edits" against "out-of-session drift reconciled: N change(s) differ from history") |
| `checkout` / `checkout_live` | the user is looking at, or working from, an **older revision** | do not apply onto their head until they save; a save on top of a checkout forks a branch |
| `save` with `forked: true` | they just forked | say so, and say what is stranded on the old branch |

**Event taxonomy** — all sixteen types, and who a type belongs to
decides your reaction:

| Theirs (a move — narrate it, take the round) | Yours (ignore) | System |
|---|---|---|
| `save`, `pin_answer`, `tripwire_answer`, `checkout`, `checkout_live`, `branch_switch`, `branch_archive`, `suggest_view`, `config_changed` | `agent_revision`, `agent_pending`, `agent_revision_discarded`, `agent_revision_failed`, `agent_revision_noop` | `server_started`, `reconciliation` |

`agent_revision_failed` is yours, but it is not an echo to ignore: it
fires when a revision the **user** pulled could not be applied, and its
`error` field is the only place that is ever said. Re-read state and
redraw — the user clicked Apply and got nothing.

`agent_revision_noop` is the quieter sibling: the user pulled something
held and it turned out to have nothing left to do — the classic case is
a re-route whose drawing has moved on, and a queued revert to a save
that already matches head reaches it the same way. Nothing was written,
nothing is broken and nothing is owed — but the banner you were told
about is gone, so read the event's `headline` for which no-op it was and
don't narrate a change that never happened.

**Tier 3 — no `Monitor` tool?** `canvas.py wait --timeout 540` (defaults
to `--for user`, the same filter server-side; exits 3 on timeout, and the
wait is clamped to 540s no matter what you pass, strictly under Bash's
600s ceiling). **Tier 2 — `AskUserQuestion`** is for before the canvas
exists, or a question that genuinely cannot live on an element.

**Re-arm after every move, and after any restart.** A watch does not
survive the server going down. The queue does: a revision behind the
banner is persisted under `project_knowledge/.pending/` and restored on
start, so after a restart read `PENDING=` for what is still waiting to be
pulled — and a malformed entry is quarantined by name rather than
silently dropped.

**An arriving user event IS the user's move.** Read it, narrate it, act —
don't wait for a chat message to confirm what the log already told you.

**Rounds under a chat-only user.** The round counter advances on canvas
authorship alternation, so a user who moves **in chat alone** never
advances it — and pin ageing (`age_rounds`, the standing nag) freezes with
it, silently. The tool cannot see a chat turn; **you can**: when the
user's move arrived as chat with no save, include
`{"op": "registry", "action": "set_round", "round": <current+1>}` in your
next batch. The apply response prints `ROUND_STALL=` when it smells this
(several agent-side commits, open pins, no user save) — treat that line
as this instruction firing.

**`ROUND` is not evidence that your round advanced.** While anything is
queued, `status` shows the *derived* round — committed plus one for your
uncommitted turn — so a chat-only turn can look advanced when nothing has
committed it. If the user's move arrived as chat with no save, include
`set_round` regardless of what `ROUND` says.

**Questions go to the canvas.** Once the server is up, your default
channel for questions is a **pin on the element it is about** — `{"op":
"pin", ...}` with `detail` (why it matters) and `examples`, answered from
the rail or in chat, whichever the user prefers. Chat remains for
narration and for questions with no anchor ("what's missing from this
account?"); `AskUserQuestion` is for before the canvas exists. The
consequence you accept: **open pins are now the question backlog**, so
their ageing must be real — which is exactly why the round must advance
(above). Never let politeness answer your own pins; an unanswered pin
ageing in the rail is information.

**Waiting is working time**: queue doc updates (glossary/spec/ADR rides your
NEXT revision, narrated and veto-able — mechanics like save records are
immediate), prep next-round questions, registry hygiene. But never a second
canvas revision while one is unreviewed. If the user is silent for
~`nudge_after_minutes` (config, default <!-- live:nudge_after_minutes -->10<!-- /live:nudge_after_minutes -->): exactly **one** nudge, then
indefinite patience. Chat is always a legitimate reply channel — a chat
answer to a pinned question resolves the pin (`resolve_pin`, narrated).

## Drawing — the op batch (primer)

Write path: `canvas.py apply --file batch.json` (or stdin). The server
validates the whole batch, applies atomically, writes a save record
(`author: agent`), and answers with the revn + headline. Errors name the
offending op — fix and resend; nothing partial ever lands. The one
exception is a message starting `internal error`, the backstop for a fault
nobody predicted: it names the exception instead of an op, and it says in
its own words what the store was left holding — normally `nothing partial
landed`, but if the write had already begun it tells you the revision is
only partly written and to re-read the project. A 409 means your
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
  binds. Rewire with `mod {"attrs": {"to": "other-node"}}` rather than
  deleting and re-adding, because that is what lets the rewire be told
  as a rewire. **The fact is typed per artifact**, so don't wait for one
  spelling: a flow fires `rewired`, a domain fires
  `relationship_rewired`, a sequence fires `actor_reassigned` (an
  endpoint moved to another lifeline), and **a wireframe fires none of
  them** — there, the intent echo is all you get. Only ARROWS may carry
  `from`/`to`: a `line` given either is a rejected batch, because a
  bound line is routed once and never re-routed, so it drifts off the
  node it claims. Make it an arrow, or leave it as decoration.
- **Registry ops ride the same batch** — there is no silent registry write.
  Every `model.json` change appears in that round's narration.
- **Complexity budget: max <!-- live:node_budget -->9<!-- /live:node_budget --> nodes AND max <!-- live:arrow_budget -->12<!-- /live:arrow_budget --> arrows per artifact** — two
  separate limits (<!-- live:domain_node_budget -->8<!-- /live:domain_node_budget --> entities on a domain view), and the arrow limit is
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
- **A deletion's fallout prints as `CONSEQUENCE=` lines** (arrows left
  half-bound, notes and mappings pointing at the deleted element) —
  narrate them in the same round and resolve them (re-target with `mod
  from/to`, or delete the wreckage). Each also stands in the lint until
  resolved, at **three different tiers** — don't read them as one
  severity: a connector left bound to a deleted element is an **ERROR**,
  a mapping member pointing at one is a **WARNING**, and a note whose
  anchor is gone is a **NOTE** (deliberately, because a tombstone left
  on purpose is legitimate).
- Default-mapped pairs: wireframe↔flow, domain↔flow, and sequence↔flow
  (flow is the hub) — create element links **eagerly as you draw those
  pairs**. Domain↔wireframe stays inference-only.

**Mermaid seeding (v0.8)** — **seed, then drag.** For a first draft,
write mermaid instead of coordinates:
`canvas.py mermaid --file d.mmd --artifact <id> --concept <c>`. Two
mapped types, chosen semantically: **flowchart → flow** (dagre lays the
nodes out; the ops ride through apply like any revision, so lints,
budgets and narration all apply) and **erDiagram → domain** (pure text
parse — attributes, cardinality and reflexive relations survive as
grammar; needs no browser at all). Everything else — sequence, class,
state — is refused by name: their conversion is dead geometry to this
grammar. Flowchart conversion needs the server up (a connected tab
services it; otherwise a headless one is launched).

That refusal is about **your** seed path. The toolbar's ⌗ import is the
**user's**, and is deliberately different: it converts every type the
vendored library handles natively — flowchart, sequence, class, ER and
state — into plain shapes that land as their own dirty canvas, for them
to Save. So a user pasting a sequence diagram has not hit a bug, and the
dialog's "Native types" line describes that path, not `canvas.py
mermaid`. What arrives is shapes, not grammar: kinds, bindings and
registry facts are still yours to add on the round after they save.

**Where it earns its keep is narrow, and the ceiling is measured.**
`erDiagram → domain` every time. Flowcharts: for **structural capture
above ~20 nodes** — everything on the canvas, correctly bound, ready to
arrange — and for `--from-skeletons` replay. **Not** in the 8–15 node
band: at 8 nodes a seed drew two unrelated edges onto one line, so the
picture shows a double-headed arrow between two steps that have no
relationship at all — and no check names it — plus a second arrow
stopping 60px clear of the node it binds. The hand layout took two
minutes and was correct first try. The capture is flawless at every size; the
LAYOUT is what fails. When you do seed a flowchart, use the recipe in
`references/flow.md` — the `nodeSpacing`/`rankSpacing` init directive,
`flowchart LR`, and **every edge of the happy path declared first, start
to finish, then the exceptions** — which is what removes that defect.

Know the edges: subgraphs are refused (the vendored converter degrades
them to a picture — seed flat, then add lanes as frames), mermaid can't
carry node kinds, so classify `source/transform/agent/control/sink` with
a `mod` pass right after seeding, most node shapes silently degrade to a
plain rectangle (flow.md lists the five that survive), and dense output
may draw label-run warnings — spread nodes, don't shrink labels. Two
layout engines work, `dagre` (default) and `elk` via `config: layout:`
frontmatter; dagre is the default on measurement, since ELK's tighter
placement draws more routing defects through this router — reach for it
only when a seed is too wide to read, and re-read the lint when you do.
An UNREGISTERED layout name falls back to dagre silently, so a typo there
looks like success. A seed is **seed-only**:
once landed the drawing is the truth, and the mermaid text is never
re-applied over user edits. To re-lay an existing messy flow instead,
`canvas.py mermaid --relayout --artifact <id>` — plain `mod x/y` ops,
revertable, queued behind the banner under `pulled` cadence; lane frames
are carried along with their members, and anything the user placed by
hand — a dragged lane as readily as a dragged step — is named in the
output before any of it moves.

Per-type guidance (primitives, fact tables, seed archetypes):
`references/wireframe.md` · `references/flow.md` · `references/domain.md` ·
`references/sequence.md`. Cross-type geometry — grid, connector rules,
budgets, the lint contract — is `references/layout.md`: read it with your
first drawing batch of a session. What the USER can do in the app, and
when to point them at it, is `references/canvas-app.md`. First-class types
(<!-- live:first_class_types -->wireframe, flow, domain, sequence<!-- /live:first_class_types -->)
narrate with typed facts; extended types
(<!-- live:extended_types -->ER, class, swimlane, dfd, mindmap, architecture<!-- /live:extended_types -->)
draw fine but narrate generically —
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
- **A settled glossary term IS a registry entry**: when a term lands in
  CONTEXT.md, the SAME batch carries `{"op": "registry", "action":
  "upsert_concept", "name": "<Term>", "glossary": "<Term>"}` (narrated,
  veto-able) — at settlement only, never speculatively. A glossary entry
  without its registry op is the v0.1-acceptance failure mode (15 terms,
  zero concepts) and the server now nags it at NOTE tier. This is what
  makes mappings real joins; the domain view is then simply the concept
  set, drawn. A term-entry **earns a rail row when it gains a view** —
  the rail groups view-less terms as vocabulary, and that is correct: a
  12-round session mints ~20 terms and ~7 views, and the seven must not
  drown. The complexity budget applies to the domain *view*, never the
  concept set. Use the CONTEXT-FORMAT term shape (`**Term**:`).
- **Two names, one concept? The alias entry, not `_Avoid_`**:
  `**Term** / **alias**: definition` records a term that stays
  legitimate for a different audience ("PipelineRun" at the desk,
  "Run" in client copy) — the parser resolves the alias to the
  canonical term, so lints and mappings join correctly. `_Avoid_:` is
  for a **rejected** synonym ("we don't say Alpha anymore — it claimed
  a risk adjustment nobody makes"). Two consecutive assessed sessions
  reached for `_Avoid_` and then wrote prose to carry the audience
  nuance it cannot express; the alias syntax existed all along.
- **Tooltips are load-bearing, and their facts need a canonical home**:
  the export carries them as footnotes precisely because they hold
  consequences ("EDGAR off costs three scorers"). But mappings and
  tripwires join on element IDENTITY, never tooltip CONTENT — a fact
  duplicated across three tooltips has no drift detector (a live
  session falsified one fact in three tooltips and only the agent's
  sweep caught it). Drift is only half of it: **a fact that lives only
  in hover text on a running server is one crash from being lost.** So:
  a consequential fact stated in a tooltip also lives in CONTEXT.md or
  an ADR — that is its canonical home — and the tooltip is the courtesy
  copy you propagate FROM it; when the fact changes, grep the tooltips.
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
- **`locked` is now ENFORCED, and it is the user's "Pin to Canvas"**. It
  stopped being advisory in v0.9. **A pin binds the TOOL, and only the
  tool** — that is the whole promise, and it is worth stating both halves
  because a sentence you are told to rely on is worse than useless when
  it is 95% true.

  **What it covers**: every non-user pass that could reposition an
  element — tidy's snap, its router and its group cascade; the fan;
  contention feet; re-routes; the two post-apply routing passes; the
  group carry behind a `mod x/y`; a deletion's cascade; z-rebanding;
  relayout; the composed-part reconciler; bound-label re-centring; and
  the load-time repairs. **That list is illustrative, not a roster** —
  <!-- live:pin_guard_sites -->Twenty-three<!-- /live:pin_guard_sites -->
  guard sites hold the promise, more than are named here, and some of
  them refuse an op rather than decline a move. Each has a test that
  goes red if that guard is deleted. That last clause is a claim about a
  sweep, not a
  number you can read off the tree, so it is not stated as a frozen
  score: re-derive it with `python3 tests/guard_mutants.py` **from the
  skill's own source repo** — it is a maintainer's sweep, not something
  an installed copy of this skill can run — which mutates each site out
  of `canvas.py` and reports the test that noticed. It is also NOT in
  the commit gate (manual stage: it rewrites `canvas.py` in place), so
  the freshest thing anyone can honestly tell you is when it last ran:
  measured 2026-08-20 at `7053b14`, every site observed.

  **What it deliberately does NOT cover, and cannot**:
  - **The user's own hand.** They drag, delete and unlock their own
    pinned elements in the app, and `x-as-user` is the CLI standing in
    for exactly that. Refusing there would be the tool overruling the
    person who set the pin.
  - **Deletion by the user in the app.** A pin is a POSITION guard, not
    a preservation guard: Excalidraw's own delete has no `locked` check.
    Selecting a pinned element to delete it is the hard way — the client
    makes a locked element unselectable — so the route that actually
    loses work is a **cascade**: delete an unpinned FRAME and its pinned
    children go with it, delete an unpinned CONTAINER and its pinned
    bound label goes. Say so if the user's words suggest they think a
    pin protects against loss.
  - **Bookkeeping.** A pinned arrow whose target is deleted loses the
    dead binding and does not move. A stale binding is corruption, not
    arrangement.
  - **Replay.** Rebuilding a recorded revision reproduces what happened;
    a pin set afterwards cannot retroactively edit history.

  Three things follow for you:
  - **Your ops on a pinned element are refused**, and so are ops on
    anything one dependency hop away (an arrow bound to it, a group
    sibling, its container or frame, or the body a pinned part belongs
    to — the last one holds even after the widget is ungrouped).
    **The binding hop is the one that is NOT symmetric**: a pinned NODE
    holds ops on the arrows bound to it, but a pinned ARROW does not
    hold ops on the nodes it lands on — you may delete or move them, and
    the arrow keeps its path. The group, container and frame hops do run
    both ways. (The asymmetry is what makes the bookkeeping bullet below
    reachable at all.) *Everything else in the batch still
    applies* — read the response's `notes`: it says "5 of 8 op(s)
    applied; ops 3,4,6 held: login-box is pinned". Do not re-send the
    whole batch; the other five landed.
  - **Pinning and unpinning are the ONE thing you may still do**, because
    the doctrine above tells you to. `mod {"attrs": {"locked": false}}`
    is never refused. But **ask first** — a pin is usually the user's,
    and unpinning to make room for your own layout is exactly what it
    exists to prevent. Bundling an unpin with a move in one op is
    refused; unpin in one op, move in the next, so both narrate.
  - **Bookkeeping is not protected.** If a pinned arrow's target is
    deleted, its dead binding is cleared and it does not move. Say that
    plainly — "I cleared the dead binding; it has not moved, it's
    pinned" — because a cleared binding and a drag look identical in a
    diff.
- **Housekeeping now narrates.** Repairs that used to move things
  silently — the routing post-pass re-drawing arrows no op named — report
  it in `notes`, aggregated to one line with a count. Read it and pass it
  on: an arrow that moved because of *your* unrelated op is the user's
  drawing changing under them, and it was invisible until v0.9.
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
prompt, people skip details they've privately decided aren't relevant.

**If the drawings are being handed on** — to a colleague, a ticket, a doc —
say so out loud, because two things don't survive leaving the canvas:
hover-only tooltips and the `▶ walk` prototype mode. `canvas.py export
--artifact <id> --with-footnotes` writes an SVG with the tooltips numbered
underneath and the glossary appended, so the artifact carries its own
detail. Name the glossary as the thing to read first.

**If the drawings are going into a repo, a PR or another tool** — a
different job, and a narrower one — `canvas.py export --artifact <id>
--format mermaid` writes flowchart text for a **flow** or **domain**
artifact. Use it for a diffable review form (a `.mmd` shows "you added a
control node and rewired two edges" in three lines; the `.excalidraw`
shows a coordinate storm) and for seeding another diagrammer. **Do not
use it for handover** — mermaid cannot carry a tooltip, so the SVG above
is strictly better at that. `--format er` writes an `erDiagram` from a
domain whose cardinality was actually settled, and names the unsettled
relations one by one rather than inventing a claim nobody made. **The
naming is per relation; the outcome is not** — one relation without a
settled cardinality refuses the whole export and writes no file at all,
so don't go looking for a partial `.mmd` holding the entities and the
relations that were fine. Settle them, or use `--format mermaid`.
Wireframes and sequences refuse by name, each with its own reason.

**The mermaid and `er` exports** carry a first line stamping themselves
`%% wysiwyg-grilling: <id> at revn N — a SNAPSHOT of the drawing, never
read back`, and print a `DROPPED=` count of everything with no mermaid
form: pins, annotations, notes, frames, freehand, images, plain lines,
and — except under `--format er`, which is the one form that carries
them — the decoration text a domain seeder draws inside an entity. Say
that count out loud: an export is one-way.

**The SVG export has neither**, and don't infer a clean sheet from the
silence: no stamp goes in the file and no `DROPPED=` is printed, so the
answer to "is this still what the drawing says?" lives only in the revn
you noted when you ran it. Say the revn yourself when you hand an SVG on.

**Text can leave; text never comes back over a drawing that exists** —
`canvas.py mermaid` seeds NEW artifacts only, and an exported file is
never read back. The drawing stays the truth.

Then `canvas.py stop`. A watchdog also reaps the server, and what it
watches is **inactivity, not death** (`WYSIWYG_IDLE_MINUTES`, hours not
minutes): a session that is very much alive but has left the canvas
untouched that long comes back to a dead server, so re-`start` rather
than assuming your URL still answers. Either way state on disk is always
sufficient to resume — next session's catch-up reconstructs.

## Degraded modes

- **No server / no browser**: grill verbally; artifacts and records on disk
  are still readable and `apply` still works file-direct.
- **Unsignalable runtime**: the web app shows a copyable context update after
  Saves ("please review save #7…") that the user pastes into chat — treat it
  exactly like a wait event: read the save record from disk and narrate.
- **Protocol mismatch from `start`/`status`**: stop/start the server; if it
  persists, tell the user to update the skill and restart the session.
