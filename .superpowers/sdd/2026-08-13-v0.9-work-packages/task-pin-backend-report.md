# TASK-PIN-BACKEND — "Pin to Canvas", the server half

**Status: COMPLETE.** Worktree `agent-a64d00431c219f4fc`, based on `10dc4bf`.
One commit: **`0713512`**.

All gates green. Suite **1575 tests OK on both floors** (3.9 and 3.12),
**4 expected failures preserved** (curator batch 34's — not mine, left red).
`MUTANTS_RENDER=1` OK. `tests/census_probes.py` 8/8 caught. `uvx ruff
check` / `mypy` / `uvx pyright` clean. `uvx pre-commit run --all-files`:
every Python hook passed; the two frontend hooks fail `eslint: not found` /
`tsc: not found`, which is the documented no-`node_modules` case (AGENTS.md
§Pre-commit) and not caused by this change — the only frontend file I
touched is a one-line livedoc marker in `tests/e2e/README.md`.
`MUTANTS_MORTALITY=1` was **not** run, as instructed.

---

## 1. The predicate, and every call site

```python
def pinned_to_canvas(el):
    return bool(el.get("locked"))
```

`canvas.py:2389`. Named after the noun the UI uses, never `pin` — in this
repo `pin` means a ❓ question glyph (`role: "pin"`, `pin_added`,
`TRANSPARENT_ROLES`), and the spike flagged the collision explicitly.

**It is the only reader of `locked` in the file**, enforced by
`TestLockedHasExactlyOneReader.test_locked_is_read_only_inside_the_predicate`,
which **tokenises** canvas.py and asserts exactly one `X.get("locked")`
code site. (Tokenising, not grepping — the first version failed on this
function's own docstring, which names the rule it enforces.) That test is
the direct answer to "do not spawn a second reader"; the wave's own
headline defect was one rule typed at two sites, and C-5 was three
subsystems disagreeing about "may the tool move this node".

Two helpers ride on it: `pinned_ids(els)` and `pinned_clause(n)` — one
formatter for the skip sentence, on `waive_hint`'s precedent, so six
passes cannot spell one sentence six ways.

**Call sites (every non-user mover):**

| # | Site | What it skips | How it says so |
|---|---|---|---|
| 1 | `_tidy_pass` snap | pinned nodes/frames | tidy's `user_note` |
| 2 | `_tidy_pass` routing loop | pinned arrows | tidy's `user_note` |
| 3 | `fan_attach_points` candidate loop | pinned arrows | returns count |
| 4 | `contention_feet` candidate loop | pinned arrows | returns count |
| 5 | `reroute_scene` router loop | pinned arrows | `Store.reroute`'s note |
| 6 | `normalize_z_order` | pinned hold their array index | — (see below) |
| 7 | `recenter_label` | pinned bound labels | — (consequence of 1–5) |
| 8 | `apply_ops` F1 post-pass | pinned arrows | `housekeeping` notes |
| 9 | `apply_ops` final routing pass | pinned arrows | `housekeeping` notes |
| 10 | `_cmd_mermaid_relayout` | pinned nodes/frames | `NOTE=` line |
| 11 | `pin_held_ops` (the agent-op gate) | every op touching a pin | error envelope |

Focus solving needed no site of its own — `solve_focus` is only reached
from inside the router, the fan and the feet, all of which drop pinned
arrows before they get there. That is the one-predicate design paying off
rather than an omission.

`recenter_label` is guarded at the single writer, not at its ~20 callers.
That is what makes a pinned label keepable at all: the spike called it "a
promise the code cannot keep" precisely because this function
repositions bound text unconditionally.

`normalize_z_order` is the one that needed a shape change rather than a
`continue`. Excalidraw renders array order, so the index **is** a
position — the one in z. The sort now runs over the slots the *unpinned*
elements occupy, and pinned elements stay at the exact index they arrived
at. With nothing pinned it is the old whole-list sort, element for
element.

**The zeros doctrine is honoured**: every skip clause is emitted only when
the count is non-zero. `pinned_clause` deliberately does not return `""`
for zero — that would put the decision in two places. Pinned by
`test_a_bare_zero_is_never_printed`.

---

## 2. The partial-application design

`pin_held_ops(els, ops)` → `(held_indices, why)`, run in
`Store._validate_batch` **before** `apply_ops` and **after** the envelope
shape gate.

**Where it lives is load-bearing.** It is in `Store`, not in
`make_handler`. `/api/apply`'s own pre-scan was this wave's **I-2
blocker** for sitting outside the validator's envelope; a pin gate in the
handler would repeat that with more at stake, because `check_batch` would
then dry-run a batch the apply path refuses and the banner would show the
user a revision that cannot land. Both surfaces now agree by construction,
and `commit_pending` (the banner path) funnels through `apply_batch` →
`_validate_batch` too.

**Atomic per dependency cluster.** The held set is closed over
`_pin_kin` for *every* pin **before any op is judged**. An op is held iff
it names an element in that closed set. So all ops touching a cluster are
held together and no cluster is ever half-applied. Judging ops one at a
time against the pins alone would apply an op on a group sibling and hold
the one on the body — a half-moved widget.

**Dependency = one hop, never the transitive closure.** Closing
transitively through a frame would make one pinned node in a screen frame
hold every op on every other node in that frame; the batch would fail
almost whole, which is the all-or-nothing behaviour partial application
exists to replace. A sibling in the same frame is two hops and applies
normally.

The four relations, as ruled: the same element; an arrow bound to it; a
group sibling; a container or frame relationship.

**One deliberate asymmetry, and the brief settled it.** The binding edge
runs *from a pinned node out to its arrows*, and **not** from a pinned
arrow back to its endpoint nodes. I wrote it symmetric first — a pinned
arrow *is* moved by an op on the node it binds — and the bookkeeping rule
killed it: "if a pinned arrow's target is deleted, clear the dead binding
and MOVE NOTHING" only means something if deleting that target is
*allowed*. Under the symmetric edge the `del` was held and the
bookkeeping branch was unreachable code. Group, container and frame edges
stay symmetric, because there the harm genuinely runs both ways.

The consequence is narrated rather than hidden: if an op moves a shape a
pinned arrow binds, the response says so — *"N pinned arrow(s) bind a
shape this batch moved. They have NOT been re-routed — they are pinned —
so they may no longer meet it."*

**What lands, and what is said.** Held ops are dropped, the rest applies,
one revision. The response carries:

```
5 of 8 op(s) applied; ops 3,4,6 held: login-box is pinned
  op 3 names login-box, which is pinned — unpin it (`mod {"id": "login-box",
       "attrs": {"locked": false}}`) if the user has asked you to move it
  op 4 names e1, which is an arrow bound to the pinned login-box, so it was
       held with it
```

Applied count leads, because the dangerous misreading of a partial apply
is "nothing happened" — the same misreading `saved_no_changes` used to
produce — and an agent that re-sends duplicates the five that landed.

**Three edges worth naming:**

- **Nothing survives** → `BatchError` (422), no revision. Committing an
  empty batch would land a "saved without changing anything" revision over
  a refusal.
- **The echo cannot lie.** `applied_reading` is handed the caller's
  untouched envelope, so it would have echoed held ops as drawn. It now
  filters on `record["pin_held_idx"]`.
- **Held ops never reach history.** `pin_held` is attached to the record
  *after* `commit` has persisted it and hashed `short_id`, so it reaches
  the response and never the save file. History records what the drawing
  became; a refused op never touched it.

**`mod locked` is legal**, per the ruling — `is_pin_flip(op)` is true only
when `attrs` is exactly `{"locked"}`. A **mixed** op (`{"locked": false,
"x": 999}`) is refused whole: the move was authored against a drawing the
agent was not entitled to rearrange, and letting it through on the
strength of an unpin in the same breath would make the pin a formality.
Unpin in one op, move in the next, and both narrate.

---

## 3. Narration facts added

The spike's waiting defect, confirmed: `locked` is a significant attr, so
N pin flips write N `mod` records, but `facts_for_scene`'s mod branch had
arms for `name`, `width/height`, `STYLE_ATTRS`, `link`, `frameId` and
`customData` — and none for `locked`, which is also not in `STYLE_ATTRS`.
Measured before the fix: pinning narrated **"saved without changing
anything"** with **empty `verb_counts`**. That is verbatim the bug fixed
for `link` (r4-9) and for tidy. **The third instance did not ship.**

| Fact | `headline_for` | SALIENCE position |
|---|---|---|
| `pinned` | `pinned <label> to the canvas` | above every geometry verb, below the semantic ones |
| `unpinned` | `unpinned <label>` | same band |
| `widget_ungrouped` | `you ungrouped <label> — its N parts no longer move with it, and I will not put them back together` | above both pin flips |

A bulk pin of 40 now reads **`pinned B0 to the canvas (+39 more)`** with
`verb_counts == {"pinned": 40}` — one headline plus `40× pinned`.

The headline says **"to the canvas"** and never a bare "pinned", because
`pin_added`/`pin_deleted` two lines below mean the ❓ glyph. Pinned by
`test_the_headline_says_canvas_so_it_cannot_read_as_a_question`.

**Housekeeping narration (closes C-3).** The routing post-pass moved
arrows no op named and said nothing — the review's measured case was two
user-drawn arrows moving 40px apart on a batch whose only op renamed an
unrelated box. `apply_ops` now takes a `housekeeping` out-param (the
`errors`/`pin_registry` idiom) and reports, **aggregated to one line with
a count**, never one per element. Three lines exist: arrows re-routed that
no op named; pinned elements left alone; and the dead-binding sentence.

**The bookkeeping sentence**, as ruled, verbatim from a live run:

```
op 0: cleared the dead binding on e1. It has not moved — it is pinned.
```

---

## 4. The grouping fix, and its measured drift

**Measured before** (`make_element` across all eight composed kinds):
**7 of 22 composed parts sat outside their body's group**, and *every one
of the seven was the bound label*. Cause: the label is minted ~120 lines
before any composite block knows whether it is building a slider or a
plain box, so none of the seven blocks that append `gid` to the body could
reach it. A slider's body/track/thumb shared `sl-grp`; its caption was in
no group at all.

**A second instance, unnamed in the brief, found by the same measurement**:
`kind: "body"` had the defect *mirrored* — `_compose_body_lines` mints its
waves into `bd-grp` while the owner block never adds `gid` to the owner,
so the parts were grouped with each other and not with the thing they
belong to.

**After: 22 of 22.** One pass, `_close_widget_group(out)`, at the end of
`make_element` — one site rather than edits to seven blocks. A plain
labelled box is explicitly *not* treated as a composite (it gets no
group), pinned by `test_a_plain_labelled_box_is_not_treated_as_a_composite`.

**This is what fixes C-6 at the root.** `_group_owners(els)` resolves each
group to its one owner and the parts it carries, and `_tidy_pass` now
snaps **owners only**, carrying parts by the owner's delta. The tear,
measured on the review's own case (a slider the user nudged 3px):

```
BEFORE   sl (+1,-1)   sl-thumb (+2,-1)   sl-track (0,0)   → 3 distinct deltas, TORN
AFTER    sl (+1,-1)   sl-thumb (+1,-1)   sl-track (+1,-1) → 1 delta, intact
```

The root cause was that a slider's thumb **is** a rectangle carrying
`role: decoration`, so tidy's `role not in ("label","pin")` guard let it
be snapped on its own account, while the track (a `line`) was not snapped
at all.

**`_group_owners` refuses to guess.** A group with no unique owner — a
user's own Ctrl+G over three boxes, where every member is an ordinary
node — resolves to nothing and is left entirely alone. Pinned by
`test_a_hand_made_group_with_no_owner_is_left_alone`.

**The check**: `composed_group_gaps(els)` plus a **waivable lint note**
(`waive_hint("widgetgroup:<aid>:<el>")`) asking *"Did you ungroup it
deliberately?"* — one finding per **widget**, not per part. It **reports
and never repairs**, because the identical reading is what the user's own
Ctrl+Shift+G produces.

**The user's ungroup** emits `widget_ungrouped`, headlines loudly, names
what they gave up, and nothing re-groups: `_group_owners` stops resolving
an owner the moment the shared group is gone, so tidy stops treating the
parts as one thing from that save onward with no further code.

**Backlinks kept.** `*_of` carries MEANING (which part is the thumb) and
`groupIds` carries STRUCTURE. `part_owner_id` exists to keep the two apart.

---

## 5. Corpus census — and why it proves nothing here

24 frozen artifacts:

| Measure | Before | After |
|---|---|---|
| artifacts | 24 | 24 |
| elements | 976 | 976 |
| **elements carrying `locked`** | **2** | **2** |
| composed-group gaps | 45 | 45 (reported, not repaired) |
| artifacts with gaps | 6 | 6 |

**Nothing moved, and that is the point.** The two locked elements are
`argus-r4-arm4/daily-run-flow:run-trigger` and
`argus-r5/admin-console:panel-pipelines` — both `role: node`, both locked
with exactly this meaning.

**The corpus differential proves nothing about this feature on its own**,
and I am saying so rather than citing the zero: **2 of 976 elements
(0.20%)** carry the attribute the guard reads. Per the zeros doctrine
(claims doc §5b) and the "corpus scope proves nothing" rule — count the
instances before citing the zero — a near-total zero over a population
that barely holds the attribute is evidence of nothing.

**The work is defended by CONSTRUCTED cases**: 35 new tests, and **both
directions on every guard** — pinned untouched *and* unpinned still
processed. The second assertion in each is the one doing the work: a
guard that skipped everything would pass a one-sided "the pinned thing did
not move" test while silently disabling the repair the pass exists for,
and no corpus differential could catch that.

**One correction to the brief:** it states the frozen corpus has 0 locked
elements. The spike said 2 and my measurement confirms **2**.

**A claim I checked and had to correct mid-flight:** I first wrote that
the 45 gaps "collapse to 24 findings" via per-owner aggregation. Measured:
they collapse to **45** — every affected widget has exactly *one* loose
part, always the bound label. The aggregation is real but the corpus does
not exercise it, and the docstring now says exactly that.

---

## 6. Anti-rot pins re-derived (not relaxed)

Two guards fired on my change, which is them working:

- `waive_hint` call sites **15 → 16** (15 findings + the definition).
- `lint_layout` append sites **63 → 64**, with a derivation paragraph in
  the pin's docstring and an **`UNCOVERED` row** for
  `widget_group_incomplete`. It takes the `budget_override_note` shape (a
  lint note with no `DETECTORS` entry) rather than `unreadable_color`'s,
  deliberately: **registering a detector obliges a proving mutant, and a
  fix's author does not write its own acceptance test.** The row names the
  mutant as a curator's to author and says what draining it needs.

`livedoc.py refresh` wrote three markers: canvas.py `~23.3k → ~24.2k`,
test_backend cases `809 → 844`, suite cases `1540 → 1575`.

---

## 7. Doctrine updated

`SKILL.md`'s "Lock what's settled" bullet expected narration that was
**impossible** before this change. It now states that `locked` is
enforced, that agent ops on a pin are refused with the rest of the batch
still applying, that pin flips are the one permitted act **and to ask
first**, and that bookkeeping is not protected.
`references/ops-reference.md` and `references/canvas-app.md` carry the
same, including the ungroup rule.

---

## 8. WIRE CONTRACT — what the client half must do

Sent to `impl-pin-client` in-session; restated here.

1. **The flag is native `locked` and nothing else. Do NOT write
   `customData.pinned_by`.** The spike recommended it so the server could
   refuse an agent unpin; the user ruled agent unpin is *legal* and
   narrated, so the key has no job. Two concrete harms if written anyway:
   `customData` and `locked` are both significant, so every pin would
   write two attr changes and the `customData` fact arm would fire
   alongside `pinned` (breaking `40× pinned`); and my predicate reads
   `locked` only, so an element with `pinned_by` and no `locked` would
   look pinned in the UI and be unprotected — while Excalidraw's own
   Ctrl+Shift+L, a third-party import, the existing Inspector checkbox and
   the 2 corpus elements would be protected with a dark badge.
   Pin = `locked: true`. Unpin = `false` or delete the key.

2. **Narration to assert.** Facts (= `verb_counts` keys): `pinned`,
   `unpinned`. Headlines: `pinned <label> to the canvas`,
   `unpinned <label>`. For an N-element bulk pin assert
   `verb_counts.pinned === N` and `/^pinned .* to the canvas/` — not a
   specific label, which depends on SALIENCE ordering. And assert it is
   **not** "saved without changing anything".

3. **`make_element` now accepts `locked` on `add`** (was silently dropped).

4. **Confirmed correct client-side, no change needed:** pin/unpin as one
   `updateScene` → one `/api/save` POST of the whole scene → one revision.
   `/api/save` is the USER path and is **never** gated — pinned means
   protected from the *tool*, never from the user. "Pin ALL targets owners
   only" is right.

5. **The refusal envelope.** On `/api/apply` the response carries `notes`
   with the lines in §2, and the record carries `pin_held` (strings) and
   `pin_held_idx` (op indices). A fully-held batch is a **422** with the
   same lines in `error`.

6. **Warning for the tack badge:** `recenter_label` is guarded, so a
   pinned bound label genuinely stops being re-centred. Copy must not
   promise re-centring.

7. **Nothing else needed from the client.** A badge read is already in
   every scene payload as `locked` on the element — no new endpoint.

---

## 8b. Cross-half verification (added after the client landed)

The client half (`impl-pin-client`, worktree `agent-a4eb858e55198637f`,
commits `3a913ec` / `9998cce` / `80812fa`) ships `pin.spec.ts` test 8
asserting this contract through the UI, and it is **red on their tree by
intent** — it stops at `"Save #9 recorded: saved without changing
anything"`, which is precisely the defect §3 closes, measured end to end
in a browser rather than in a unit test.

They asked me not to assume it would flip. I verified it against the real
`commit` path instead of assuming:

| case | headline | verb_counts |
|---|---|---|
| pin-only save, 6 owners | `pinned Box 0 to the canvas (+5 more)` | `{'pinned': 6}` |
| **pin + 1× resized + reorder drift** | `pinned Box 5 to the canvas (+12 more)` | `{'pinned': 6, 'resized': 1, 'reordered': 6}` |
| single pin | `pinned Box 0 to the canvas` | `{'pinned': 1}` |
| save **event** headline | `Save #2 recorded: pinned Box 0 to the canvas (+5 more)` | — |

All four satisfy both halves of their assertion (no "saved without
changing anything"; `/pinned .* to the canvas/`). The event headline is
the same `record["summary"]["headline"]` object as the banner's, so those
two assertions cannot drift apart.

**The second row is the one that mattered.** The client reported that the
FIRST save of any page carries restore drift (`1× resized, 5× reordered`)
regardless of user action, because their baseline fingerprint is taken
after Excalidraw's restore. I built that case deliberately: lock flips
plus a resize plus reorder churn in one save. `pinned` still headlines,
because §3 placed `pinned`/`unpinned` **above every geometry verb** in
SALIENCE — chosen for exactly this, so a bulk pin that also nudged
something cannot headline as the nudge. Note the headline names *Box 5*
in that row, not Box 0: the reorder changed which fact came first, which
is why the wire contract told the client to assert the regex and never a
specific label.

**A defect on the client's side of the wire, surfaced here so it is not
lost as scaffolding:** that first-save drift is a real disagreement
between the client's post-restore baseline and what the server stored. It
puts a spurious `resized` into the history of every session's first save,
and it is the same class of silent movement the C-3 work addressed —
except it originates client-side, so the `housekeeping` notes cannot see
it. It is not caused by this task and neither half fixes it. Flagged to
the client to record as a discovered defect rather than as "why the save
under test is the second one".

**Also confirmed by the client against this half, unprompted:** one
`updateScene` → one POST → one revision holds end to end (revn +1,
exactly one `save` event, every id `locked` on disk); and Pin ALL targets
owners only. They removed `customData.pinned_by` before anything landed.

## 9. Files changed

| File | What |
|---|---|
| `skills/wysiwyg-grilling/scripts/canvas.py` | the predicate, 11 guard sites, `pin_held_ops` + refusal lines, housekeeping out-param, 3 facts + SALIENCE + headlines, `_close_widget_group` / `_group_owners` / `composed_group_gaps` / `part_owner_id`, the lint note, `locked` in the `add` whitelist |
| `tests/test_backend.py` | 35 new tests across 8 classes; `waive_hint` count pin re-derived |
| `tests/test_mutants.py` | append-count pin 63→64 with derivation; `UNCOVERED` row |
| `skills/wysiwyg-grilling/SKILL.md` | the enforced-pin doctrine |
| `references/ops-reference.md`, `references/canvas-app.md` | `locked` semantics, ungroup rule |
| `AGENTS.md`, `tests/e2e/README.md` | livedoc markers only |

## 10. Open / handed on

- **A mutant for `widget_group_incomplete` is owed**, by a curator, not by
  me. The `UNCOVERED` row says so and says what it needs.
- **The 45 standing corpus gaps are deliberately unrepaired.** They surface
  as waivable lint notes. Closing them is a decision about someone's
  drawings, and the "never silently re-group" rule means it must be asked.
- **`MUTANTS_MORTALITY=1` not run**, as instructed.
