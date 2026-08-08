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
  "ops": [ ... ] }
```

A stale `base_revn` → 409 with the current head; re-read `status` and rebase.
If the user has unsaved edits (or cadence is `pulled`), the batch **queues**
behind the pending-revision banner instead of landing — the response says so
(`QUEUED=true`). That is success, not an error; the user chooses when.

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
  "value": "+3.1%",           // kpi (string) / slider (number 0–100)
  "checked": true,            // checkbox/toggle state
  "tooltip": "markdown…",     // hover-only detail card (v0.3): rendered
      // on hover in the client, editable from the element's right-click
      // menu; NEVER exported to SVG/PNG. Verbose per-element detail
      // belongs here, not in more visible rows.
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
  "intent": "why this exists (customData)",
  "frameId": "screen-checkout",            // wireframe screen membership
  "backgroundColor": "#e9e5da", "strokeColor": "#1e1e1e",
  "roundness": {"type": 3},   // rounded rect (data stores, soft cards)
  "fontSize": 16 }}
// arrows take endpoints at the op level; geometry+bindings are computed
// (the router avoids foreign boxes and prefers orthogonal elbows):
{"op": "add", "element": {"type": "arrow", "id": "t-a-b", "label": "yes"},
 "from": "step-a", "to": "step-b"}
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
  "points": [[0,0],[80,0],[80,-160],[320,-160]],
      // arrows/lines: hand-authored waypoints, RELATIVE to the arrow's
      // x,y. v0.3: the path is marked routed:"authored" — YOURS. No
      // later pass re-routes, re-fans, or flattens it (that's what makes
      // `mod points` a real repair tool for shared attach points). A
      // rewire (mod from/to) is a new path request and re-routes.
      // Axis-aligned paths render as sharp elbows; narrates as a
      // `rerouted` fact — never an empty save.
  "kind": "sink", "role": "decoration", "intent": "…", "parent": "shelf",
  "document": "docs/x.md",   // these five fold into customData correctly
  "tooltip": "markdown…",    // set/replace hover detail; "" or null removes
  "verticalAlign": "top",    // on a shape: aligns its BOUND LABEL
  "value": "+3.4%",          // kpi/slider only — recomposes the glyph in
  "checked": false,          //   place; checkbox/toggle only. Both fire
                             //   typed facts (value_changed/state_toggled)
  "links_to": "other-artifact",  // sets the element's navigation link
  "locked": true,            // settled structure — the user can't drag it
  "x": 100, "y": 200, "width": 180,
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

**reorder** — z-order: `{"op": "reorder", "id": "bg-panel", "index": 0}`

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
// per-artifact complexity-budget override (v0.3): recorded intent, not a
// silencer — `reason` is REQUIRED and the lint restates it as a NOTE.
// The defaults stay 9 nodes / 12 arrows (8 entities on a domain view).
{"op": "registry", "action": "set_budget", "artifact": "pipeline-flow",
 "nodes": 14, "arrows": 18, "reason": "the 5-way ingest fan IS this view"}
{"op": "registry", "action": "set_budget", "artifact": "pipeline-flow",
 "clear": true}
// one-time-question waive (v0.4): the reason IS the recorded answer.
// Keys the lints consult: "q25:<artifact>" (progress indicator),
// "q12:<artifact>:<label-slug>" (whose-word). `clear: true` un-waives.
{"op": "registry", "action": "waive", "key": "q25:pipeline-flow",
 "reason": "user ruled: regulated flow, steps must show"}
```

## Reading state back

- `canvas.py status` → `HEAD_REVN`, `ROUND`, `WHOSE_MOVE`, `ARTIFACTS`,
  `OPEN_PINS`, `OPEN_TRIPWIRES`, `EVENTS_LOG`, `DIRTY`, `PENDING`,
  **`LINT_DEBT`** (standing cross-artifact lint counts — drift in
  artifacts your batch didn't touch) and **`PIN_DEBT`** (open/answered
  pins with age in rounds + how often their target changed; entries with
  `direction: user` are the USER'S questions awaiting your move — answer
  them first). Both also ride every apply response.
- `GET <url>api/state` → everything (registry, config, scenes, saves,
  pins, tripwires, pending).
- `GET <url>api/save-record/<revn>` → a full save record (also on disk:
  `project_knowledge/saves/NNNN-*.json`).
- `canvas.py wait --timeout 540` → prints new events as JSON lines; exit 3 on
  quiet timeout (that's your cue for queued work or a nudge — never a retry
  loop without doing something useful between).
- `canvas.py screenshot --artifact <id>` → PNG path (needs the browser open;
  context only, never truth).
- `POST <url>api/tidy {"artifact": id}` → grid-snap + re-route + re-fan +
  z-order as an ordinary agent revision (revertible).
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
