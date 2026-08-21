# Capability assessment — run 5 (wysiwyg-grilling v0.8)

**Target:** `v0.8_correctness` @ `cb533ab`, discovered through the global skill
symlink. **Method:** `skill-optimizer` assessment mode. **Date:** Aug 2026.

Two arms, run concurrently against isolated copies of the same fiction:

| Arm | Project | Assessor | Ledger |
|---|---|---|---|
| **A** | `argus5` | **the session lead, first-hand** — no orchestrator layer | `.ledger/r5.jsonl` |
| **B** | `argus5b` | a delegated orchestrator routed through `skill-optimizer` | `.ledger/r5b.jsonl` |

Arm A is the change from run 4, and it was the right call: every observation
below was measured directly rather than arriving as an orchestrator's
paraphrase. Arm B earned its keep differently from run 4: not by contrast of
method but by **cross-validation** — three of its five findings independently
reproduce arm A's, and two of the run's six P1s exist only because its agent
hit a wall arm A's agent had blamed itself for.

Gates before turn 1: 412 unit tests, 12 Playwright e2e, `pre-commit
--all-files` — all green, so nothing pre-broken is scored here.

---

## 1. Scorecard

```
19 findings / 18 distinct causes
P1: 7   P2: 9   P3: 3   P0: 0   ENV: 0
attribution: tool-silent-shipped 14 · tool-false-alarm 3
             tool-told-ignored 1 · tool-silent-covered 1
found by:    assessor-query 10 · user-look 5 · agent 4
withdrawn: 0   ·   undiscriminated: 0   ·   9 turns (A) / 11 (B)
```

Arm B filed five of its own (`.ledger/r5b.jsonl`): **three independently
reproduce arm A's** (r5b-1 = r5-8 registry atomicity, r5b-4 = r5-13 the note
padding, r5b-5 = r5-7 the adapter's note roles), **one is new and is a P1**
(r5b-3, folded in here as `r5-17`), and **one does not reproduce** (r5b-2,
§6). Three independent duplicates across isolated arms is the strongest
evidence in the run that those are real and not artefacts of one assessor.

| Run | Findings | Causes | Headline |
|---|---|---|---|
| r3 | 13 | 6 | the check validates a value nobody sees |
| r4 | 14 + 9 | 8 | capability outgrew correctness |
| **r5** | **19** | **18** | **the mechanisms are right; the modes turn them off** |

**Five of eighteen came from looking at pictures** and **three from the agent
under test volunteering a tooling defect it had tripped over** — including two
P1s (`r5-8`, `r5-18`) and one it had first attributed to its own carelessness
(`r5-17`). Ten came from the queries that account for most of the effort.

Cause count rose while severity fell — no P0s, and the P1s are narrow
mechanisms rather than the two-of-v0.7's-fixes-don't-work shape of run 4.

## 2. The headline

**v0.8 fixed the surfaces the agent reads. Run 5 found the surfaces that
switch off — and, underneath them, write paths that are atomic when they
succeed and lossy when they fail.**

The second clause is the agent's, not mine. Speaking to its user *as the
skill's author*, unprompted, it merged two defects I had filed separately:

> *"the earlier one was rejected batches still writing their registry ops...
> This one is the pending queue silently dropping on restart. **Both are the
> same shape — a write path that's atomic on the happy path and not on the
> failure path** — and both are invisible unless someone diffs the state
> afterwards."*

**I am adopting that as the headline and as one work package, but keeping the
two ledger causes.** The theme is one; the remediations are not — sandboxing
the registry during validation does nothing for a queue with no durable store,
and vice versa. Merging the causes would hide that from whoever does the work.
Recording the agent's cut and my disagreement is the honest version of both.

**Four of the six P1s are the same story** — a correct mechanism disabled by a
state the user can enter without announcing it, and all four live on the
`pulled`/queued axis or the derived values that ride it:

- **`r5-6`** — under `pulled` cadence the apply response is `QUEUED=true /
  PENDING_ID / PIN_DEBT` and nothing else. No `ECHO=`, no lint, no
  `CONSEQUENCE=`. v0.8's beat-3 fix (deletion fallout reaching the agent in the
  same breath) is unreachable by construction on this path, and the agent
  narrates its round blind. Under `per-round` the identical batch returns the
  full echo — same agent, same session, differential both directions.
- **`r5-2` / `r5-3`** — `effective_round = committed + (1 if queued)`. Pressing
  **Apply now** drains the queue, the `+1` evaporates (the commit auto-bump
  cannot fire when the base author is also `agent`), and the header goes from
  `Round 3+ — your move` to `Round 2`, dragging every open pin's age from
  "open 2 rounds" back to 1. Worse, the derived `+1` is *why* the agent skips
  `set_round` on a chat-only turn: `status` already showed the advance.
- **`r5-11`** — `--relayout` promises "user-placed nodes are named in the
  output before anything moves". It filters `customData.author == "user"`,
  which is stamped at creation and never by a drag; and `moved_user` is
  computed over ops that only ever contain mermaid-graph nodes, while the only
  elements carrying `author: "user"` (notes, pins) are never in that graph.
  **The filter matches the empty set by construction.** It silently overwrote
  both nodes the user had dragged.

- **`r5-18`** — a queued revision is **destroyed by a server restart**, with no
  warning at `stop`, no note at `start`, and no record anywhere afterwards.
  `stop` is the documented way to end a session, so the sanctioned shutdown
  discards user-directed work; the arm-B agent lost the single most important
  revision of an eleven-round session to it and could only tell because it
  went looking. **Attribution nuance:** arm B's orchestrator retracted this to
  `tool-silent-covered`, because that agent's still-armed Monitor caught the
  restart and it inferred the loss. I have left mine at `tool-silent-shipped`
  and the difference is real rather than a disagreement — in my probe there was
  no agent at all, and in arm B's the agent inferred the loss from a
  *reconciliation* event, never from any signal about the queue. The tool was
  silent in both. As arm B's own report puts it, *"a watchdog reaping the
  server after the human walks away has no agent left to cover it."*

`r5-11` is run 3's headline reincarnated one level up: not "a check validating
a value nobody sees", but *a check validating a value nobody sets*.

The remaining two P1s came from the agent under test rather than from any
probe — `r5-8` (a rejected batch still writes its registry ops) and `r5-17`
(a cross-artifact pin resolve strands its glyph). Both are the same shape the
arm-B agent named unprompted: **"a write path that's atomic on the happy path
and not on the failure path — and both invisible unless someone diffs the
state afterwards."** That sentence is the most useful thing either agent said
about the tool, and it belongs at the top of the v0.9 brief.

## 3. Findings

| id | sev | cause | found by | what |
|---|---|---|---|---|
| r5-1 | P2 | count-surface-disagreement | query | `lint` prints `ARTIFACTS=2` for one artifact (registry pseudo-scope counted); `status` uses the same key for a name list; undocumented in both |
| r5-2 | **P1** | round-derivation | look | Apply now moves the round backwards and un-ages every open pin |
| r5-3 | P2 | round-derivation | query | the derived `+1` masks the chat-only `set_round` obligation, so the advance is lost on apply |
| r5-4 | P2 | glossary-mapping-gap | query | renaming a domain entity that IS a glossary term fires no tripwire — 0 of 8 entities are mapped to their own concepts, so the detector has no domain coverage at all |
| r5-5 | P2 | border-collinear-routing | look | an edge leaving a side-edge midpoint runs 32px collinear with that box's border and reads as passing through it; the crosses-through lint's strict interior test cannot see the boundary |
| r5-6 | **P1** | pulled-drops-feedback | query | `pulled` strips ECHO/lint/CONSEQUENCE from the apply response |
| r5-7 | P3 | adapter-fidelity | query | `x-as-user note` stamps `note`/`note-text`; the real client stamps `annotation`/`label`, which ten branches consult and the former none. Latent — verified identical through `lint_layout` |
| r5-8 | **P1** | non-atomic-registry | **agent** | a rejected batch still applies its registry ops to the live registry; the next commit persists them |
| r5-9 | P2 | mermaid-adoption-zero | query | zero `canvas.py mermaid` invocations across 6 eligible diagrams and 2 arms, both agents having read the guidance twice |
| r5-10 | P2 | dialog-import-quality | look | the ⌗ dialog dumps onto the open artifact with hash ids and a mid-word-broken label; the CLI path fed the same text produces a clean seeded artifact |
| r5-11 | **P1** | check-input-never-set | query | `--relayout`'s user-placement guard can never fire |
| r5-12 | P3 | traceback-leak | query | `x-as-user checkout` without `--target` leaks a `ValueError` traceback against v0.8's no-traceback promise |
| r5-13 | P2 | note-geometry-vs-rule | query | every sticky note (both creators, `w-16`) violates the loader's `w-24` fit rule, so ART-011 repairs it on every load forever and the repair never persists — **and because the repair moves geometry, every resume of a project containing a note mints an out-of-session reconciliation commit** (found while promoting the fixture; pinned by `TestArgusR5Fixture`) |
| r5b-1 | **P1** | — | agent (arm B) | **duplicate of r5-8**, found independently |
| r5-14 | P2 | label-backdrop-erases-elbow | look | an edge label at the arc-length midpoint of a short elbowed path lands ON the corner; the renderer's opaque label backdrop erases the elbow and both approaches, so the connector reads as two disconnected stubs. Six labels, three artifacts, worst on v0.8's flagship self-loop |
| r5-15 | P3 | duplicate-export-caption | look | wireframe exports print the title twice top-left — the snapshot caption repeats the screen frame's own name |
| r5-16 | P2 | no-sequence-lint | query | the unconnected-node lint false-alarms on all four actor headers of a sequence diagram; `lint_layout` has no sequence arm, though the type is first-class with its own reference doc. 100% of lint's output on that artifact, and the only "fix" would make the diagram wrong |
| r5-17 | **P1** | resolve-pin-wrong-artifact | **agent, both arms** | resolving a pin from a batch scoped to another artifact marks it resolved and strands its ❓ on the canvas. Arm B filed it; **arm A's agent hit it and apologised for it as its own carelessness** — a tool whose silence converts its bug into the agent's guilt |
| r5-18 | **P1** | pending-lost-on-restart | **agent** | a queued revision is destroyed by a server restart, silently — `stop` is the documented shutdown, and no surface at stop, start or after records that anything was dropped. Reproduced in an isolated probe: `PENDING=1` → restart → `PENDING=0`, no warning anywhere |
| r5-19 | **P1** | stale-referential-cache | **agent** | the load-time referential findings are computed once at server start and never recomputed, so a long-lived server nags standing ERRORs for references later commits repaired. Same project, same moment: server says 2 errors, CLI says none, disk says none. The function's docstring states the principle it breaks two lines above the break |
| r5b-2 | P2 | phantom-reorder-facts | agent (arm B) | **does not reproduce in arm A — see §6**. Arm B's own report narrows it: four other saves through the identical path were clean, so it is intermittent *within* arm B too |
| r5b-10 | P2 | rail-mapping-row-burial | agent (arm B) | **filed by arm B, not independently verified here**: 15 identical `⚠ mapped — divergence flagged below` rows bury the concept list, where the OK variant prints the element pair. This is §6's *secondary* prediction landing one component over from where it was aimed |

## 4. The §8 checklist, item by item

**Fixed in v0.8 — asserted under assessment conditions**

| item | verdict | decisive evidence |
|---|---|---|
| r4-11 / r4b-1 self-loops, no traceback | **PASS** | `r-pipeline-rerun` is a clean 5-point orthogonal loop off the top-right corner; no point inside the box. A cold observer called it broken; the geometry says otherwise |
| r4-7 referential integrity | **PASS, both variants** | *arm A, in-session:* user-deleted a bound node → two `LAYOUT_ERROR`s naming each dangling binding *plus* `REPAIR=ART-005` lines naming what the loader fixed. *arm B, out-of-session (the variant r4-7 was actually filed against):* the server was killed and the project file hand-edited outside the session; on resume the reconciliation named the one real change and cleared the rest — "One deletion — `Friday only` in `morning-run-flow` — and every other artifact confirmed untouched" — then queued a repair for the two arrows the deletion had left bound to nothing. No phantom "saved without changing anything" |
| beat-3 deletion fallout | **PASS on the user path** | half-unbound warnings fired verbatim ("lost its end endpoint … deleting a bound node leaves this behind"); facts on the record as `arrow_orphaned: 2`. **FAIL on the queued path → r5-6** |
| r4b-9 orphaned note | **UNTESTABLE** | notes have no anchor: neither the assessor verb nor the real client's 🗒 sets `annotates`, so the check covers agent annotations only |
| r4-8/9/10 composed parts | **PASS** | toggle thumbs render at the checked end on a freshly drawn console; parts follow |
| r4-3 chat-only round | **PARTIAL** | `set_round` landed as *exactly* N on both turns that used it (beats-B anti-stacking intact); omitted on the one chat-only turn → r5-3 |
| **r4-6 event loop (the burned measurement)** | **PASS, n=2** | both agents, unprompted, `ToolSearch`ed the deferred `Monitor` schema and armed it `persistent: true` with the mine-vs-yours filter — *extended* with the three checkout/archive types SKILL.md's example omits |
| r4-13 / F5 rail | **PASS** | "2 CONCEPTS · 9 TERMS" with vocabulary collapsed; artifacts reachable |
| r4-5 alias syntax | **PASS (arm B)** | `**Research Cycle** / **Daily Sweep**`, and it declined to file the second name as *rejected* because "that would make the glossary say 'we don't say that'" |
| r4-4 rename vs glossary | **FAIL → r5-4** | no tripwire, no named mute; the agent's own diligence covered it (`tool-silent-covered`) |
| r4-1 crosses-through | **PASS, and beyond** | fired correctly and repeatedly on pasted mermaid content it had never seen. Boundary blind spot → r5-5 |
| r4b-3 pin glyph density | **PASS** | inside-top-right fallback is correct: the outside slot provably intersects the containing panel and the button above |
| duplicate ids heal at commit | **PASS** | no ART-003 across 22 revisions |

**New v0.8 surfaces — first exercise ever**

| item | verdict |
|---|---|
| agent-initiated mermaid seed | **NEVER CHOSEN → r5-9** |
| `--relayout` vs user-moved nodes | **FAIL → r5-11** (checkpoint line and ECHO are good; the guard is dead) |
| ⌗ dialog as the user | **narration PASS, import quality FAIL → r5-10** |
| canvas-first pins | **PASS** — three pins with `detail` + `examples` anchored to their subjects on turn 1, chat only narrating |
| pin answered from the rail | **PASS** — age lines visible ("open 2 rounds"), answer round-trips |
| pin ageing over a **long** horizon | **PASS, and the run's best argument for the mechanism.** Arm B carried one pin open **eleven rounds**; the counter kept it visible, the agent twice proposed filing it into a document and was refused both times, and it was still on the canvas at the close. The method's limits section says machinery that only activates over a long horizon will not be reached in a ten-turn run — eleven reached it, and the answer arrived only when the agent stopped asking about the design and asked what the user had *decided not to say* |

**Never exercised in any run — now exercised**

| item | verdict |
|---|---|
| pending banner (Apply now / After I save / Discard) | **PASS** — including `+1 more revision waiting` + `Show all`; the deferred path landed its revision on the next user save, same second |
| cadence flip mid-session, both directions, silent | **PASS** — caught via `config_changed`; but the cadence appears on **no CLI surface** (see §7) |
| branch fork / switch / archive | **PASS** — user checkout+save forked `alt-0022` (`forked: true`); the agent investigated before acting, restored `main`, archived the stray |
| stale-save 409 | still untested (needs a second browser context) |
| extended-tier artifact | still untested |

## 4b. What arm B's own report adds

Its orchestrator filed a full account (10 findings, 9 causes) and three parts
of it are worth carrying forward verbatim:

- **The method failed it in ways arm A never felt**, because arm A had no
  orchestrator layer: the A4 loop is not executable from a delegated assessor;
  `session-agent.md`'s shipped template contains the very bullet that burns the
  r4-6 measurement (it had to override it, and says so — *"that one-word edit
  is what let r4-6 be measured"*); **the cold look was structurally
  unavailable**, because the run permits exactly one subagent and that is the
  agent under test. All go to the so1 ledger, not here.
- **It hit the `lastmsg.py --grep` inversion independently**, on the same
  measurement, and was saved only by a passing system-reminder. Two assessors,
  same instrument, same near-miss, same headline at stake.
- **Its agent out-diagnosed it on a mechanism question**, which is the one
  place `finding-discipline.md` says the agent is normally the *worse*
  instrument. Its own note: *"on 'what is the shape of these two defects' it
  beat me, because it had lived through both."* Worth carrying into the method
  as a counter-example rather than a rule.
- **The variant-frame machinery got an unasked-for accessibility use.**
  Unable to draw a red band on a greyscale canvas, the agent drew the breached
  tile twice side by side and reported what that showed — *"the left one at
  2.8% is indistinguishable from the same tile at 2.4% ... it's what the tile
  looks like in a printout, in a screenshot pasted into an email, and to
  roughly one man in twelve"* — then proposed stating the limit as a second
  carrier *"because it also tells a new analyst what the threshold is, which
  the band never does."* A constraint in the tool surfaced a defect in the
  design.
- **Its verdict vs the benchmark is more useful than a score.** It judges the
  session ahead on structure (18 concepts / 14 terms / 30 mappings vs 5 / 4 / 3;
  two sequence artifacts where the benchmark has none) and **behind on
  density** — three of four dashboard panels are titled empty boxes where the
  benchmark's carry real rows, and its flow nodes are bare labels where the
  benchmark's carry subtitles. That is a fair loss and should be a v0.9 doc
  item: the skill teaches structure well and interior detail badly.

## 5. The §6 prediction, scored

> "round 5's headline lives in the seams of the new surfaces — most likely
> (a) narration of a user's pasted-dialog mermaid import, (b) the first real
> `--relayout` on a canvas with user-moved nodes, or (c) an event-loop miss."

**Half right, and wrong in an informative direction.**

- (a) **wrong** — the narration was excellent: *"Your paste landed 36 shapes
  directly on top of the Daily Run… I've transcribed all ten steps into their
  own artifact with your labels untouched."* The defect was in the **import**,
  not the narration (`r5-10`).
- (b) **right** (`r5-11`), and it is the run's sharpest finding.
- (c) **wrong** — the event loop is the strongest thing in v0.8. Two agents
  armed it cold and filtered it correctly.
- Secondary ("the collapsed vocabulary rail hides something") — **wrong**, the
  rail behaved.

What the prediction missed entirely is the **cadence axis**: three findings
live there and none was foreseen. The lesson for the next prediction is to
name the *modes*, not just the new features.

### The prediction for round 6

**The next headline will be a cached derivation — a value computed once at
server start or at batch time and never invalidated.** Both arms converged on
it independently. The evidence is that four findings are already that shape and
nothing tests any of them for staleness:

- `r5-19` — referential findings frozen at server start; the server and the CLI
  disagree about the same project at the same moment.
- `r5-2` / `r5-3` — a round derived from queue depth, which un-derives itself.
- `r5-13` — a repair recomputed on every load and persisted on none.
- `r5b-6`'s sibling shape — a nag channel reporting a world that no longer
  exists.

Arm B put the general form better than I can: **the model believes something
about the artifact that the artifact does not say — and this time the belief is
in the nag channel the agent is instructed to act on.**

Concrete tests run 6 should carry: hold one server across ≥10 commits and
compare its `/api/state` lint against a fresh CLI `lint` after every repair;
apply and then discard a queued revision and assert the round returns to where
it started; resume a project twice and assert the second resume mints nothing.

## 6. Cross-arm contradiction — do not average it away

Arm B filed **r5b-2**: a user rename or note-add mints phantom `reordered`
facts for every other element, over-reporting the headline
(`canvas.py:3400-3404`). **It does not reproduce in arm A.** Arm A's user
saves are clean:

```
0007 rename  → verb_counts {'entity_renamed': 1, 'renamed': 1}
0012 note    → verb_counts {'added': 1}
0005 (probe) → verb_counts {'renamed': 1}
```

Two isolated assessors, opposite facts about the same code path — which the
method treats as evidence of an **unmodeled axis**, not as noise to vote on.
The likely axis: arm A's user edits all went through `x-as-user`, which
rewrites the element array **in place**, while a browser save rewrites it in
Excalidraw's own order. The one save of mine that came from a real tab added
59 elements at once, so any reorder facts would have been swamped.

**Discriminating test for run 6 (not run here):** rename a single label *in
the browser tab* and Save, with no other change, then read `verb_counts`. That
isolates client array reordering from the CLI write path.

## 7. Not filed, deliberately

- **cadence appears on no CLI surface.** `canvas_updates` is read only inside
  the apply path; `status` prints twenty lines and none is `CADENCE=`. Not
  filed as its own defect because `r5-6` is the consequence that matters and
  the fix is the same; recorded here so run 6 does not re-derive it.
- **the ⌗ dialog advertises "Native types: flowchart, sequence, class, ER,
  state"** while SKILL.md tells the agent sequence/class/state are *refused by
  name* ("dead geometry to this grammar"). Same product, opposite advice to its
  two readers. Not filed — I did not test what a pasted sequence diagram
  actually produces. **Run 6 should.**

- **the state-variant frame check (R2-4's fix) produced nothing on the one
  three-frame wireframe in the run — and I could not establish why.** Arm B's
  dashboard carries `screen-dashboard`, `screen-dashboard-0605` and
  `screen-var-breach`; a user rename landed on one twin only ("Signals as of
  06:14" vs "Signals Today"). The positional fallback is gated on
  `len(frames) == 2`, so a third frame looked like the cause. **The probe that
  would have shown it was invalid**: I removed the third frame and its
  `frameId` members, but every element in that artifact has `frameId: None`
  (membership is positional), so the reading orders never changed and the
  2-frame control produced zero warnings too. A probe that fails for an
  unrelated reason confirms nothing in either direction, so this is not filed.

  It is also not a *user-visible* gap: the divergence WAS caught, by the 3.2.4
  mapping check — *"'Signals Today' / 'Signals as of 06:14' all map to
  morning-run-flow#aggregator through 2 separate mappings — same action, 2
  names; pick one?"* — so the attribution is `tool-told`, not
  `tool-silent-covered`. **Run 6 test:** build a wireframe with exactly two
  frames and `frameId` set on their members, rename one label, and assert the
  variant warning fires; then add a third frame and assert it still does.
- **cold-look false positives** (`cold-look-triage.md`): arrows "landing
  beside their targets" and the `holdings` label "detached" — measured, both
  wrong. The mid-run observer's strongest pattern claim ("? glyph placement is
  the most consistent problem") survived in **zero** instances. A cold look is
  a generator; A6b is what stands between it and the ledger.

  **But one dismissal was mine and it was wrong.** I ruled the "broken
  self-loop" a false positive because the *path* is clean — which it is. The
  closing observer measured the pixels and found a 35×13px gap; the label
  backdrop erases the elbow (`r5-14`). I verified the wrong layer, and the
  rule that would have caught me is written in the method I was following:
  *when a correct measurement and the picture disagree, the picture is the
  fact*, reinforced by *a recurring finding overrides its prior dismissal* —
  two independent observers had already reported it. **Both cold looks are
  worth their cost, and the second one was worth more than the first because
  it measured before asserting.**

## 8. Work packages for v0.9

1. **The cadence axis (r5-6, r5-2, r5-3, r5-18 + the unfiled `CADENCE=` gap).** Ships
   alone and first — one cause, three findings, and the mode is user-selectable
   at any moment. Queue-time responses must carry echo and lint computed from
   the staged scene (the ops are already validated; the information exists and
   is withheld), the round must commit forward on apply rather than evaporate,
   `status` must name the cadence, and **the queue must survive a restart or
   say loudly that it did not** — `r5-18` is the one finding in this run that
   destroys user-directed work rather than merely hiding it.
2. **Checks whose input is never set (r5-11, r5-4).** Both are a predicate over
   a field nothing writes. Fix the predicates *and* add the differential that
   would have caught them: assert the check fires on a real instance, not just
   that it compiles.
3. **Failure-path atomicity and durability (r5-8, r5-18).** One package, two
   mechanisms, per the agent's cut: validate the registry against a copy and
   commit it only when the whole batch passes; and give the pending queue a
   durable store, or make its loss loud. The shared acceptance test is the
   shape neither defect could survive — **diff the state after a failure**,
   not after a success.
4. **Geometry and text (r5-13, r5-14, r5-5, r5-10, r5-15).** `r5-13` leads: an
   8px constant makes every note-bearing project mint a reconciliation commit
   on every resume, which is noise on the one channel the agent is told to
   trust. Then `r5-14` — nudge an elbow label off the corner (or shrink its
   backdrop), because it currently makes correct connectors look broken and
   the self-loop is the worst case. Then the boundary case in the
   crosses-through walk, the dialog import's label wrap, and the doubled
   export caption.

   **All five are invisible to `status`, `lint` and `state` by construction,
   and four of the five were found by looking.** The v0.9 gate for this
   package should be a rendered diff, not an assertion over stored geometry —
   that is the concrete lesson of `r5-14`, where the stored path was correct
   and the picture was not.
5. **Mermaid doctrine (r5-9).** Either carry node kinds through the seed so the
   mandatory follow-up `mod` pass disappears, or move the threshold and stop
   recommending a path competent agents correctly decline six times out of six.
6. **Feedback that misattributes itself (r5-17, r5-16).** Both teach the agent
   the wrong lesson. `r5-17`'s silence made an agent apologise for a tool bug;
   `r5-16` gives a sequence diagram one lint finding whose only "fix" would
   corrupt it. A channel that is wrong in either direction gets discounted
   wholesale, which costs far more than the individual defect.
7. **Small (r5-1, r5-7, r5-12).**

## 9. Limits

- **One agent per arm, one run.** Behavioural findings are n=1 per arm; the
  event-loop result is n=2 because both arms produced it independently.
- **The human is played by the same model.** Persona written before turn 1 and
  kept; every draft message checked against "would this analyst know that word".
- **Arm B needed one harness-level nudge** (its orchestrator was never
  re-invoked when its subagent finished — an `skill-optimizer` defect, filed in
  the so1 candidates, not counted here). Nothing about the task reached it and
  nothing reached its subagent.
- **Nine turns.** Machinery that only activates over a long horizon was not
  reached; the 409 conflict and extended-tier artifacts remain untested.
