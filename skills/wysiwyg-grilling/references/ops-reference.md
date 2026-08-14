# Op batch reference — the agent write path

`canvas.py apply --file batch.json` (or JSON on stdin). One grammar serves
both directions: these ops mirror the change vocabulary inside save records.
The server **validates the whole batch before applying anything** — any
invalid op rejects everything with an error naming the op and what's wrong.
On success: atomic write, normalized artifact, save record
(`author: "agent"`), event appended, response `REVN/SHORT_ID/HEADLINE`.

## Batch envelope

```jsonc
{ "base_revn": 3,            // REQUIRED — current head revn (status: HEAD_REVN)
  "artifact": "checkout-flow",  // target artifact id (optional if only one exists)
  "create": { ... },         // only when creating a new artifact (below)
  "note": "why this revision",  // shown in banners/timeline — write it
  "supersedes": 4,           // optional: replace this queued revision (v0.5)
  "ops": [ ... ] }
```

A stale `base_revn` → 409 with the current head; re-read `status` and rebase.
If the user has unsaved edits (or cadence is `pulled`), the batch **queues**
behind the pending-revision banner instead of landing — the response says so
(`QUEUED=true`). That is success, not an error; the user chooses when.

A queued batch is validated and linted **at queue time** and answers with
the same `ECHO=` / `LAYOUT_*` lines an applied one does (v0.5) — read
them. Before v0.5 nothing was checked until the *user* clicked Apply, so
an impossible batch came back `QUEUED=true`, got narrated as drawn, and
failed in the user's face minutes later.

**Fixing a queued revision:** re-send it with `supersedes: <pending_id>`
so the corrected batch *replaces* the original. Without it the user is
left with two banners for one intent and no way to tell them apart. A
revision that failed on the way in is already gone — the queue drops an
entry that cannot apply rather than re-offering it.

**`canvas.py apply --check`** dry-runs a batch: same validation, same
`ECHO=` / `LAYOUT_*` output, nothing committed, exit 5 if it would be
rejected. Use it when you cannot afford to be wrong — a batch you are
about to queue, or one you cannot see the result of.

It is **side-effect free**, including for registry ops. Until v0.7 that
was not true: `--check` on a batch carrying `rename_artifact` wrote the
new name to the artifact file with no revn, no save record and nothing
to revert. If you are reading an old project and a name looks like it
changed without a revision, that is why.

**`canvas.py apply --check --render`** adds a `PNG=` line: the proposed
scene, drawn. This is the only way to *look* at a revision before it
lands — under `pulled` cadence a queued batch is invisible to you until
the user applies it, and legibility is the one class of defect the
response cannot tell you about. Read the PNG before you queue, not after
the user complains the diagram is hard to read.

## `create` — new artifact

```jsonc
"create": {"id": "checkout-wireframe", "name": "Checkout Screen",
           "type": "wireframe",          // must be enabled in config.json
           "concept": "checkout",        // registers the view on a concept
           "concept_name": "Checkout"}   // used if the concept is new
```

`concept` takes the **MOST SPECIFIC** concept the view makes tangible —
an output wireframe of the report is a view of `report`, not of the
project umbrella. The umbrella holds only views that genuinely span the
whole design (typically the domain diagram and the end-to-end flow). If
the artifact id is named after a settled term, that term is almost
always the right concept, and the lint says so.

**View debt is not a reason to file under the umbrella.** `owed` is
archetype debt — the view set the *project* owes — so a view of an owed
type pays it wherever it attaches (ADR 0010). Drawing `report-wireframe`
on concept `report` clears the umbrella's `wireframe` debt.

## Element ops

**add** — full spec through the `make_element` funnel:

```jsonc
{"op": "add", "element": {
  "type": "rectangle" | "ellipse" | "diamond" | "arrow" | "line" | "text" | "frame",
  "id": "semantic-slug",      // omit → minted from label; NEVER a nanoid
  "label": "Pay now",         // bound text is built for you (never a text prop)
  "x": 60, "y": 330, "width": 320, "height": 48,
  "role": "node" | "annotation" | "pin" | "decoration",
      // default node; text w/o container → annotation. `decoration` is
      // visual furniture (wavy body-text lines, X-box strokes, backdrops):
      // exempt from connector lints and budgets, painted beneath arrows.
      // [KNOWN DEFECT 2026-08-12: the SVG export currently paints in
      // type buckets and DISCARDS array order across types, so "beneath
      // arrows" is not honoured there — pinned red by TestPaintOrder in
      // tests/test_mutants.py; the web client is unaffected. Do not rely
      // on cross-type z-order in exported/snapshot output until it flips.]
  "kind": "button" | "nav" | "input" | "entity" | "image" | "store"
        | "kpi" | "checkbox" | "toggle" | "slider"
        | "help" | "sticky-bar" | "feedback" | …,
      // v0.4 plain kinds (no composition, but the lints watch them):
      //   help       — where help lives; 3.2.6 slot-drift NOTEs.
      //   sticky-bar — pinned chrome flush with a frame edge; declaring
      //                it arms the 2.4.11 focus-obscured question.
      //   feedback   — post-action confirmation banner/toast.
      // kind: "image" on a rectangle composes the X-box for you (rect +
      // two grouped decoration strokes; they move and delete with it).
      // v0.3 composed kinds (same composite machinery):
      //   kpi      — big `value` row above, the label (the semantic NAME)
      //              pinned to the bottom band; renames narrate "Alpha",
      //              never "+3.1% Alpha". Pass "value": "+3.1%".
      //   checkbox — box glyph + check stroke; pass "checked": true.
      //   toggle   — pill + thumb; "checked" moves the thumb.
      //   slider   — track + thumb; pass "value": 0–100.
      //   input    — (v0.5) a settled value renders right-aligned in the
      //              field, label left: `Run at    weekdays 06:00`. Before
      //              v0.5 `value` was read only by kpi/slider, so an input
      //              handed one dropped it SILENTLY and the field drew as
      //              an empty labelled box.
  "value": "+3.1%",           // kpi/input (string) / slider (number 0–100)
  "checked": true,            // checkbox/toggle state
  "tooltip": "markdown…",     // hover-only detail card (v0.3): rendered
      // on hover in the client, editable from the element's right-click
      // menu. Not in the app's PNG export — but `canvas.py export
      // --with-footnotes` prints tooltips under the drawing, numbered
      // against markers, so a handed-on artifact keeps its detail.
      // Verbose per-element detail belongs here, not in more visible rows.
  "verticalAlign": "top" | "middle" | "bottom",
      // pins the bound label to the header band (titled panels: pair
      // with `parent` nesting for the shelf-of-cards pattern) or the
      // footer band (kpi does this for you); default middle
  "attributes": ["cash, mandate", "holds Positions"],
      // entities only: attribute rows rendered beneath the term label
      // (the label stays the EXACT glossary term); fires attribute_added
  "parent": "shelf-id",       // declared containment — nesting, not collision.
      // NOTE: frameId (screen membership) alone does NOT exempt overlap —
      // near-full containment inside the same screen does, but partially
      // overlapping siblings still lint; `parent` declares the intent
  "links_to": "other-artifact",  // in-canvas navigation (click follows)
  "document": "docs/brief.md",   // report reader: project_knowledge-relative
  "annotates": "node-id",     // what a note is ABOUT (v0.8) — anchors it for
      // the orphan checks: delete the target and the note is NAMED, not lost
  "intent": "why this exists (customData)",
  "frameId": "screen-checkout",            // wireframe screen membership
  "backgroundColor": "#e9e5da", "strokeColor": "#1e1e1e",
  "roundness": {"type": 3},   // rounded rect (data stores, soft cards)
  "fontSize": 16 }}
// arrows take endpoints at the op level; geometry+bindings are computed
// (the router avoids foreign boxes and prefers orthogonal elbows):
{"op": "add", "element": {"type": "arrow", "id": "t-a-b", "label": "yes"},
 "from": "step-a", "to": "step-b"}
// Reflexive (v0.8): `from` == `to` routes a self-loop off the node's
// top-right corner — "rerun of", "manages", a retry back into the same
// step. Keep the label short; cardinality goes in the tooltip.
{"op": "add", "element": {"type": "arrow", "id": "r-run-rerun",
 "label": "rerun of"}, "from": "pipeline-run", "to": "pipeline-run"}
// `type: "image"` is rejected in ops — real images arrive via the canvas
// (paste/drop in the browser) with their file blobs.
```

**mod** — `attrs` is `{attribute: newValue}`. Unknown attributes are a
validation ERROR (the silent `mod kind` no-op is dead). Special attributes:

```jsonc
{"op": "mod", "id": "confirm", "attrs": {
  "label": "Order Placed",   // set/replace bound label ("" or null removes it)
                             // — on a FRAME this renames the frame (its name)
  "name": "Checkout screen", // frames only: rename explicitly
  "from": "other-node",      // arrows only: rewire start (re-routes + rebinds)
  "to": "other-node",        // arrows only: rewire end — fires REWIRED
      // A rewire IS a new path request, so it re-routes — including over
      // a path the USER drew by hand. That is deliberate, and since v0.7
      // it narrates: `user_route_replaced`. If their shape mattered,
      // re-issue `mod points`. Dropping an endpoint back on the node it
      // already bound is not a rewire and fires nothing.
  "points": [[0,0],[80,0],[80,-160],[320,-160]],
      // arrows/lines: hand-authored waypoints, RELATIVE to the arrow's
      // x,y. v0.3: the path is marked routed:"authored" — YOURS. No
      // later pass re-routes, re-fans, or flattens it (that's what makes
      // `mod points` a real repair tool for shared attach points). A
      // rewire (mod from/to) is a new path request and re-routes.
      // Axis-aligned paths render as sharp elbows; narrates as a
      // `rerouted` fact — never an empty save.
  "kind": "sink", "role": "decoration", "intent": "…", "parent": "shelf",
  "document": "docs/x.md", "annotates": "node-id",
      // these six fold into customData correctly
  "tooltip": "markdown…",    // set/replace hover detail; "" or null removes
  "verticalAlign": "top",    // on a shape: aligns its BOUND LABEL
  "value": "+3.4%",          // kpi/slider only — recomposes the glyph in
  "checked": false,          //   place; checkbox/toggle only. Both fire
                             //   typed facts (value_changed/state_toggled)
  "links_to": "other-artifact",  // sets the element's navigation link
  "locked": true,            // settled structure — the user can't drag it
  "attributes": ["cash, mandate", "holds Positions"],
      // entities only: REPLACES the attribute rows, keeping the entity's
      // id — so its mappings, pins and rename detection all survive.
      // Before v0.7 this was accepted on `add` only and the workaround
      // (delete + re-add) silently dropped all three. Fires
      // attribute_added / attribute_removed, or `renamed` for a row
      // swapped in place.
  "x": 100, "y": 200, "width": 180,
      // moving a node re-routes the SERVER-ROUTED arrows bound to it.
      // It does not touch a path the user shaped by hand — theirs is
      // theirs — so those are left behind and the detached-endpoint lint
      // flags them. Re-issue `mod {from, to}` on each in the same batch:
      // a rewire is a new path request, which is what you want here.
  "frameId": "screen-b",     // wireframe: regroup into another screen
  "customData": {"intent": "…"},   // merged, not replaced
  "backgroundColor": "#fde8e8" }}
```

Composite integrity: mods that move an element carry its grouped
decorations (X-box strokes, attribute rows) along; deleting it deletes
them.

**del** — deletes the element, its bound label, and cleans every reference
(bindings, frame membership). Deleting a mapped element tombstones the link
into the save record: `{"op": "del", "id": "old-step"}`

**reorder** — z-order: `{"op": "reorder", "id": "bg-panel", "index": 0}`. The
`index` is a position in the element order, which is paint order: 0 draws
first (behind everything, as `bg-panel` wants) and an index past the end draws
last. A negative index is refused — it used to clamp to 0 silently, so `-1`
meaning "last" put the element behind everything instead.
[KNOWN DEFECT 2026-08-12: reorder changes stored order (which replay and the
web client honour) but has NO effect on the SVG export/snapshot across type
boundaries — see the TestPaintOrder pin. Flips when render_svg paints in
array order.]

**pin** — element-anchored question (❓ on canvas + interactive rail entry;
answers arrive as `pin_answer` events carrying the text):

```jsonc
{"op": "pin", "id": "pin-review", "target": "review-order",
 "question": "Can review be skipped for repeat buyers?",
 // OPTIONAL but write them: the rail card opens a detail modal — `detail`
 // is the comprehensive what/why (paragraphs split on blank lines),
 // `examples` are concrete cases. Brief the user like a stakeholder who
 // wasn't in the room: what hangs on this answer, what each choice
 // implies. A bare question is a missed teaching moment.
 "detail": "Repeat buyers abandon at re-review. But review is where\n"
           "price changes surface.\n\nSkipping trades friction for\n"
           "surprise-charge complaints — the answer decides which.",
 "examples": ["Amazon: 1-click skips review entirely",
              "Airline checkout: review is mandatory — prices move"]}
{"op": "resolve_pin", "id": "pin-review"}   // after it's settled in chat
```

**Pin state machine** — `open → answered → resolved`, with one writer per
transition: the *server* moves a pin to `answered` on a `pin_answer` event;
the *agent* moves it to `resolved` with `resolve_pin`, and only
`resolve_pin` closes a pin. A pin may go straight `open → resolved` when
its answer arrived off-canvas (chat, AskUserQuestion) — the batch that
executes the answer carries the `resolve_pin`. A pin sitting `answered`
for more than one round, or `open` after its question was settled in
another channel, is bookkeeping drift: sweep it in your next batch.

`resolve_pin` updates the registry record **and deletes the ❓ element**
(tombstoned in the save record) — settled things leave the canvas; no
explicit `del` needed. When a pin's TARGET element gets deleted, the server
auto-prunes the registry pin — but the leftover ❓ element stays on canvas
until you remove it with an ordinary `del` in your next revision (tidying
wreckage, not a proposal).

A pin id names **one** question. Reusing an id the registry has **ever**
filed — whatever its status: open, answered, resolved or auto-pruned — is
refused with an error naming it, because the resolve write-through is
id-global: one answer would close two questions and take the unanswered
one's ❓ off the canvas. Pruned ids are held back for the same reason they
are pruned rather than deleted — the stale ❓ may still be standing on a
canvas, and a resolve of the reused id would take it down as if it had
been answered. Omit `id` and one is minted for you — the minter dedupes
against the registry as well as the scene, so the easy path cannot reissue
a live pin's id either.

Conversely `resolve_pin` takes the ❓ and nothing else. Ids are minted per
scene, so an ordinary element that happens to share the id, here or on
another artifact, is left standing — and a `resolve_pin` **naming** one is
refused, because something that was never a question has nothing to
resolve. A ❓ drawn by hand with no registry record still resolves
normally. What the resolve does take is every ❓ carrying that id, on every
artifact, however it got there — a duplicate an older version minted before
the id check existed, or one re-drawn under the id later in the same batch.
Nothing is exempted: the glyph count on the canvas and the open-pin count in
the registry have to agree, and each exemption tried in turn left a ❓
standing under an id the registry had already marked resolved.

A batch of ONLY pin/registry ops is a **pin-only revision** — the UI styles
it "agent asked a question" with no Apply action.

## Registry ops (ride the same batch — no silent registry writes)

```jsonc
{"op": "registry", "action": "upsert_concept", "id": "checkout",
 "name": "Checkout", "views": ["checkout-flow"], "glossary": "Checkout"}
// a term settling into CONTEXT.md IS a concept being minted (ADR 0007):
// upsert with glossary set and no views — it lands `unviewed: true`
{"op": "registry", "action": "upsert_concept", "name": "The Book",
 "glossary": "The book"}
// view debt (ADR 0006): `owed` lists artifact types the archetype says
// this concept still owes. Recorded at naming time; registering a view
// of an owed type pays that debt automatically. The apply/status output
// nags unpaid debt at NOTE tier.
{"op": "registry", "action": "upsert_concept", "id": "checkout",
 "owed": ["domain", "sequence"]}
{"op": "registry", "action": "remove_view", "concept": "checkout",
 "view": "old-artifact"}                 // concept survives unviewed
{"op": "registry", "action": "add_mapping", "concept": "checkout",
 "elements": ["checkout-wireframe#pay-button", "checkout-flow#payment"]}
{"op": "registry", "action": "annotate_mapping", "index": 0,
 "note": "intentionally-divergent: wireframe uses marketing copy"}
// SCOPE the annotation (v0.5). `kinds` names the divergence you are
// excusing; anything else on that mapping still trips. Omit it and you
// mute the mapping for every kind of change forever — which is how a
// note about cardinality ("three tiles, one store") went on to swallow a
// rename, and the two views drifted apart in silence.
{"op": "registry", "action": "annotate_mapping", "index": 0,
 "note": "intentionally-divergent: three KPI tiles, one store",
 "kinds": ["cardinality_changed"]}
// verbs worth scoping to: renamed · label_renamed · entity_renamed ·
// rewired · relationship_rewired · value_changed · state_toggled ·
// cardinality_changed · type_changed · the *_deleted family
// Presentation-only facts (moved, resized, reordered, regrouped,
// restyled, tooltip_*) no longer arm a tripwire at all (v0.6), so you
// never need to scope one out — a 40px nudge is not a disagreement.
// a view's SCOPE can narrow — splitting a domain model leaves one half
// being something else. The id never moves (saves, mappings and pins are
// keyed on it); only the title the rail shows. v0.6: before this, the
// only way to retitle was to re-create the artifact and lose its history.
{"op": "registry", "action": "rename_artifact", "artifact": "argus-domain",
 "name": "Signal Formation"}
{"op": "registry", "action": "remove_mapping", "index": 0}
{"op": "registry", "action": "resolve_tripwire", "id": "tw-8-1"}
// tripwires are answerable in place like pins (rail card + red ? on the
// canvas near the diverged element): they fire with a default question,
// two choices ("Intentional divergence" / "Propagate"), and a synthesized
// detail. Sharpen any of it when the default under-explains — an empty
// choices list means free-text only. Answers arrive as `tripwire_answer`
// events; act on the answer, then resolve_tripwire.
{"op": "registry", "action": "annotate_tripwire", "id": "tw-8-1",
 "question": "Basket or Cart — which name wins?",
 "choices": ["Basket everywhere", "Cart everywhere", "Different things"],
 "detail": "The glossary says Cart; the wireframe now says Basket.\n\n"
           "Naming is the contract — pick one or split the concept.",
 "examples": ["UK retail says basket; US says cart"]}
{"op": "registry", "action": "decline", "concept": "checkout",
 "view_type": "domain", "kind": "suggestion", "reason": "user: later"}
// the session clock, for when it has drifted from the conversation —
// e.g. several rounds happened in chat with nothing drawn. Both fields
// are optional. Pin ages (PIN_DEBT) are measured against `round`, so
// setting it forward ages every open question with it: say why in the
// same turn. Under `pulled` cadence you should NOT need this — a queued
// revision already counts as your move made (see "Reading state back").
{"op": "registry", "action": "set_round", "round": 4,
 "whose_move": "user"}
// per-artifact complexity-budget override (v0.3): recorded intent, not a
// silencer — `reason` is REQUIRED and the lint restates it as a NOTE.
// The defaults stay 9 nodes / 12 arrows (8 entities on a domain view).
{"op": "registry", "action": "set_budget", "artifact": "pipeline-flow",
 "nodes": 14, "arrows": 18, "reason": "the 5-way ingest fan IS this view"}
{"op": "registry", "action": "set_budget", "artifact": "pipeline-flow",
 "clear": true}
// one-time-question waive (v0.4): the reason IS the recorded answer.
// Keys the lints consult: "q25:<artifact>" (progress indicator),
// "q12:<artifact>:<label-slug>" (whose-word), "324:<artifact>:<step-slug>"
// (one function, one label). `clear: true` un-waives. Every note that
// offers a key prints it — copy it out of the lint line.
{"op": "registry", "action": "waive", "key": "q25:pipeline-flow",
 "reason": "user ruled: regulated flow, steps must show"}
```

## Reading state back

- `canvas.py status` → `HEAD_REVN`, `ROUND`, `WHOSE_MOVE`, `ARTIFACTS`,
  `OPEN_PINS`, `OPEN_TRIPWIRES`, `EVENTS_LOG`, `DIRTY`, `PENDING`,
  **`LINT_DEBT`** (standing cross-artifact lint counts — drift in
  artifacts your batch didn't touch), **`PIN_DEBT`** (open/answered
  pins with age in rounds + how often their target changed; entries with
  `direction: user` are the USER'S questions awaiting your move — answer
  them first) and **`OPEN_TRIPWIRE`** (standing unresolved divergence
  questions, with their text — not just the count).
  **All three ride every apply response, including a queued one.**
  Nothing is pull-only: if a nag exists you will be told without asking.
- `ROUND` and `WHOSE_MOVE` count the pending queue. Under `pulled`
  cadence nothing commits until the user applies, so a queued revision
  is still your move made and their move owed — and pins age against it.
  Discard the queue and both go back; the committed value is
  `committed_round` in `api/state` if you need the raw one.
- `GET <url>api/state` → everything (registry, config, scenes, saves,
  pins, tripwires, pending).
- `GET <url>api/save-record/<revn>` → a full save record (also on disk:
  `project_knowledge/saves/NNNN-*.json`).
- `canvas.py lint [--artifact <id>]` → the standing findings in full.
  `status` gives you counts only ("dashboard 9N"); this gives you the
  sentences, without applying a batch to find out what they say.
- `canvas.py pending [--discard <id>]` → what is queued behind the user's
  banner, with each entry's artifact, note and op count.
- `canvas.py export --artifact <id> --with-footnotes` → SVG carrying its
  tooltips as numbered footnotes plus the glossary — for handover.
- `canvas.py wait --timeout 540` → prints new events as JSON lines; exit 3 on
  quiet timeout (that's your cue for queued work or a nudge — never a retry
  loop without doing something useful between).
- `canvas.py screenshot --artifact <id>` → PNG path (needs the browser open;
  context only, never truth).
- `POST <url>api/tidy {"artifact": id}` → grid-snap + re-route + re-fan +
  z-order as an ordinary agent revision (revertible).
- `POST <url>api/pending/resolve {"id": N, "action": ...}` → the banner's
  own buttons: `apply_now`, `after_save`, or `discard` (v0.5 — the user's
  way to say no). The user drives this; you supersede or re-send.
- `POST <url>api/save-label {"revn": N, "label": "v1 baseline"}` →
  bookmark a save (shown in timeline + graph).
- `GET <url>api/doc/<project_knowledge-relative .md>` → document content
  for the report reader (elements carry `customData.document`).

The intent echo covers EVERY op kind (add/mod/del/reorder/pin/
resolve_pin/registry) and reflects **post-apply state** — an op that
didn't do what it claims echoes that, not success. A registry-only batch
headlines its registry work ("registry: upsert concept …").

**Tripwires fired by your batch print as `TRIPWIRE=<id> <question>`
lines** (and ride the apply response as `tripwires`) — read them in the
same move; a divergence you learn about rounds later via `status` counts
is a divergence you narrated late (v0.3). Every op-made element carries
`customData.author: "agent"`; user-made elements carry `author: "user"`
— annotation facts and headlines say "my note"/"your note" from it.

## Save-record shape (what you read back)

Per-artifact material is **nested under `artifacts.<artifact-id>`** — not at
the top level:

```jsonc
{ "revn": 7, "base_revn": 6, "branch": "main", "author": "user",
  "short_id": "…", "selection_at_save": [...], "user_note": null,
  "artifacts": {
    "checkout-flow": {
      "changes":   [...],   // the ops below
      "inverse":   [...],   // pre-built, reverse order — replay = revert
      "by_element": [...],  // per-element rollup with attrs_changed
      "facts":     [...] }},// the typed semantic facts you narrate from
  "summary": {"verb_counts", "headline", "suppressed"},
  "tripwires": [...] }      // top-level, across artifacts
```

Change verbs: `{"op":"add","element":{full},"index":i}` ·
`{"op":"del","element":{full}}` · `{"op":"mod","id","attrs":[{attr,from,to}]}`
(`"derived":true` attr entries are measurement/routing churn — ignore them) ·
`{"op":"move","id","from":[x,y],"to":[x,y]}` · `{"op":"reorder",…}`. A
rewired fact's `"?"` endpoint means the binding points at nothing (its node
was deleted).
