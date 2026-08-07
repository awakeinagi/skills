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

## Element ops

**add** — full spec through the `make_element` funnel:

```jsonc
{"op": "add", "element": {
  "type": "rectangle" | "ellipse" | "diamond" | "arrow" | "line" | "text" | "frame",
  "id": "semantic-slug",      // omit → minted from label; NEVER a nanoid
  "label": "Pay now",         // bound text is built for you (never a text prop)
  "x": 60, "y": 330, "width": 320, "height": 48,
  "role": "node" | "annotation" | "pin",   // default node; text w/o container → annotation
  "kind": "button" | "nav" | "input" | "entity" | "priority" | …,
  "intent": "why this exists (customData)",
  "frameId": "screen-checkout",            // wireframe screen membership
  "backgroundColor": "#e9e5da", "strokeColor": "#1e1e1e",
  "fontSize": 16 }}
// arrows take endpoints at the op level; geometry+bindings are computed:
{"op": "add", "element": {"type": "arrow", "id": "t-a-b", "label": "yes"},
 "from": "step-a", "to": "step-b"}
```

**mod** — `attrs` is `{attribute: newValue}`. Special attributes:

```jsonc
{"op": "mod", "id": "confirm", "attrs": {
  "label": "Order Placed",   // set/replace bound label ("" or null removes it)
  "from": "other-node",      // arrows only: rewire start (re-routes + rebinds)
  "to": "other-node",        // arrows only: rewire end — fires REWIRED
  "x": 100, "y": 200, "width": 180,
  "frameId": "screen-b",     // wireframe: regroup into another screen
  "customData": {"intent": "…"},   // merged, not replaced
  "backgroundColor": "#fde8e8" }}
```

**del** — deletes the element, its bound label, and cleans every reference
(bindings, frame membership). Deleting a mapped element tombstones the link
into the save record: `{"op": "del", "id": "old-step"}`

**reorder** — z-order: `{"op": "reorder", "id": "bg-panel", "index": 0}`

**pin** — element-anchored question (❓ on canvas + interactive rail entry;
answers arrive as `pin_answer` events carrying the text):

```jsonc
{"op": "pin", "id": "pin-review", "target": "review-order",
 "question": "Can review be skipped for repeat buyers?"}
{"op": "resolve_pin", "id": "pin-review"}   // after it's settled in chat
```

`resolve_pin` updates both the canvas element and the registry pin record.
When a pin's TARGET element gets deleted, the server auto-prunes the
registry pin — but the leftover ❓ element stays on canvas until you remove
it with an ordinary `{"op": "del", "id": "pin-review"}` in your next
revision (tidying wreckage, not a proposal).

A batch of ONLY pin/registry ops is a **pin-only revision** — the UI styles
it "agent asked a question" with no Apply action.

## Registry ops (ride the same batch — no silent registry writes)

```jsonc
{"op": "registry", "action": "upsert_concept", "id": "checkout",
 "name": "Checkout", "views": ["checkout-flow"], "glossary": "Checkout"}
{"op": "registry", "action": "remove_view", "concept": "checkout",
 "view": "old-artifact"}                 // concept survives unviewed
{"op": "registry", "action": "add_mapping", "concept": "checkout",
 "elements": ["checkout-wireframe#pay-button", "checkout-flow#payment"]}
{"op": "registry", "action": "annotate_mapping", "index": 0,
 "note": "intentionally-divergent: wireframe uses marketing copy"}
{"op": "registry", "action": "remove_mapping", "index": 0}
{"op": "registry", "action": "resolve_tripwire", "id": "tw-8-1"}
{"op": "registry", "action": "decline", "concept": "checkout",
 "view_type": "domain", "kind": "suggestion", "reason": "user: later"}
```

## Reading state back

- `canvas.py status` → `HEAD_REVN`, `ROUND`, `WHOSE_MOVE`, `ARTIFACTS`,
  `OPEN_PINS`, `OPEN_TRIPWIRES`, `EVENTS_LOG`, `DIRTY`, `PENDING`.
- `GET <url>api/state` → everything (registry, config, scenes, saves,
  pins, tripwires, pending).
- `GET <url>api/save-record/<revn>` → a full save record (also on disk:
  `project_knowledge/saves/NNNN-*.json`).
- `canvas.py wait --timeout 540` → prints new events as JSON lines; exit 3 on
  quiet timeout (that's your cue for queued work or a nudge — never a retry
  loop without doing something useful between).
- `canvas.py screenshot --artifact <id>` → PNG path (needs the browser open;
  context only, never truth).

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
