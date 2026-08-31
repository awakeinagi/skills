# Session handover — harness + candidate-board arc COMPLETE; v0.9 is next

Current through 2026-08-13 (branch `v0.9_mutation_harness` @ `2319a17` —
v0.9 execution mid-flight: Phases 1-2 SHIPPED, WP4 underway; drain 27 and falling).
Superseded content from earlier phases is reproduced in the R5 notes.

**State in one line: run 5 complete and committed; the mutation harness is
built, self-guarded, and red by intent (drain 29); ~12 product defects
discovered and pinned by the tooling arc; all user decisions RESOLVED; four
repos idea-mined with everything dispositioned; methodology documented in
`docs/design/`; v0.9 (WP1 first) is the next work.**

**Picking up after a compaction, in order:**
1. `docs/design/README.md` — the methodology + operating guide (new agents
   start here).
2. This file §1/§1a — exact counts and the red inventory.
3. `docs/todo/feature-backlog.md` — every queued item with verdicts
   (Batch D = items 15-17, CLOSED 2026-08-13 by the curator — report at
   `docs/superpowers/reviews/2026-08-11-mutation-harness/batch-d-report.md`;
   dependency-parked = 19-20; rejected list at the bottom).
4. `V0.9-PLAN.md` — the near-future work. **WP1 ships alone and first**
   (failure-path cluster; now opens against five pinned store-integrity
   defects incl. the A1a total-loss crash; gate = cause a failure, then
   diff the state). WP4 = drain its remaining reds (18 owned at Phase-2 gate; re-derive from `mutants list --red` + the ledger, not this sentence (scope rules bind: ellipse +
   label-arm mutants must flip; ASPIRATIONAL flips owe real other-pole
   neighbors).
5. Execution/review record if needed:
   `docs/superpowers/reviews/2026-08-11-mutation-harness/progress.md`.

---

## 1. Where the code is, and the gotcha that still bites

- Branch **`v0.9_mutation_harness`** @ `2319a17`, cut from `v0.8_correctness`
  @ `cb533ab`, in the worktree
  `~/Projects/wysiwyg_grilling_skill.worktrees/capability_assessment`.
  **Nothing is merged to `main`.** No `v0.9_failure_paths` branch exists yet —
  the harness branch is the only v0.9-era code on disk.
- **The gotcha:** `~/.claude/skills/wysiwyg-grilling` symlinks into *this
  worktree*. Whatever is checked out here is the skill every agent on this
  machine discovers. Verify before any assessment work.
- Health, re-measured 2026-08-13 after curator Batch D + follow-up: **~780+ unit tests (moving target — trust the SDD ledger, not this number)** (`python3 -m unittest discover -s tests`, ~9s) →
  `OK` (skips and expected-failure counts move with the drain — read them from the SDD ledger); with the render tier enabled
  (`MUTANTS_RENDER=1`, same discover command, ~97s, real chromium) the render-enabled run `OK` — the skips are the render tests;
  **12 Playwright e2e**; `uvx pre-commit run
  --all-files` green. The expected failures are red *by intent* — **do not
  read a count from this block**, which carried "29 (26 model + 3 render)"
  from a 2026-08-13 measurement until curator batch 23 deleted the numbers
  on 2026-08-15 (task 46 §9 C4 flagged the "3 render" as long dead). The
  block disclaims itself, but a disclaimed number is still the number
  people quote; §1a below carries the per-file measurement and the command
  that produces it. Its last red count — a parenthesised `ef=3` for the
  render tier, correct on the day and invisible to the guard that watches
  the real sentence — went the same way at v0.9 TASK-MICROFIX, for the
  same reason: a census number restated in prose is a fifth copy however
  small, and this block's own disclaimer is the evidence that disclaimers
  do not hold. (live source of truth: the SDD ledger at .superpowers/sdd/2026-08-13-v0.9-work-packages/progress.md — THIS FILE's counts lag by design between gate seams) (Batch D added
  `parity_clipped` — render_svg clips center-aligned labels at the viewBox's
  min side; the follow-up proved `text_overflow` both arms GREEN; coverage
  table 13 rows, UNCOVERED ledger 48).

### 1a. The mutation harness — landed, red, and it is WP4's acceptance spec

Built out-of-band between run 5's plan and v0.9's first line of code.
Spec: `docs/superpowers/specs/2026-08-11-mutation-harness-design.md`.
Plan: `docs/superpowers/plans/2026-08-11-mutation-harness.md`. Both are
**local-only** — `docs/` is gitignored by repo policy (see §4 item 4).

Eight files, all tracked and all covered by pre-commit: `tests/instruments.py`
(verbatim port of the four spike measurement scripts, bugs preserved on
purpose), `tests/tests_helpers.py`, `tests/test_instruments.py`,
`tests/test_mutants.py` (engine + catalog + coverage gate + sweep),
`tests/test_mutants_render.py`, `tests/pngdiff.py`, `tests/test_pngdiff.py`,
`tests/mutants_sweep.json`.

**What is red, and why that is the deliverable.** The counts are NOT
restated here. They are stated once, in the guarded "durable form of the
counts" sentence further down this section, and derived per half in code:
`CATALOGUE_RED_IDS` and `HAND_AUTHORED_RED_CLASSES`, each checked by its
own `TestCoverage` sibling. Read them there, or run `mutants list --red`.

WHY THIS PARAGRAPH LOST ITS NUMBERS (v0.9 TASK-E9ENVELOPE, found by
TASK-FRAMING). It was the FIFTH census hand copy, and by the time anyone
looked, **all five of its claims were false**. Every figure below is
QUOTED FROM THAT DEAD PARAGRAPH and paired with what was live *at that
moment* — this is a record of an autopsy, not a statement of the census,
and nothing in it should be read as current: the per-file counts (it
said 17 / 3 / 0 against a live 14 / 3 / 0), the total (20 against 17),
the catalog/hand-authored split (8 / 12 against a derived 5 / 9), how
many render reds are ungated (it said two of three; all three are), and
the two suite totals (`=19` and `=20`; both runs read the same number).
It had been wrong by one before this wave and drifted three further with
the flips. For what is red TODAY, read the guarded sentence and the two
derived halves named above — the live per-file counts have already moved
again since this autopsy was written, which is the whole argument.

The damning part is not the drift, it is that this paragraph broke three
rules its OWN section states below it: *"Do not restate any of that as a
suite `expected failures=N`"*, *"THE TWO HALVES ARE NOW BOTH DERIVED, AND
NEITHER IS RESTATED HERE"*, and the guarded sentence it silently
duplicated. The rules were right; this paragraph simply predated them and
nobody reconciled it. Four guards have now been built one staleness at a
time, and a fifth copy was still sitting twenty-eight lines above the
newest of them.

**This is not the rejected proposal in disguise.** Replacing the guarded
sentence's numbers with a bare command was rejected because it would have
deleted the only statement of them — removing staleness by removing the
information. This deletes a DUPLICATE of a statement that survives three
paragraphs down and is checked. State a derived fact once, in the place
that is checked, and point at it from everywhere else.

Task 50 briefly drained the render file — the first time it had been empty
since it was built, after `test_mutant_opacity_ghost_is_invisible_to_tier_one`
flipped — and batch 23 put three back the same day: the interleaved
back-loop and the band-trapped scrap (both from task 48's corpus replay,
both ungated arithmetic) and the label riding foreign ink (gated). The
drain lasted hours, which is the honest thing to record about it. Task 49
had likewise emptied `tests/test_backend.py` and batch 23 refilled it the
same day with the tier-1 ceiling; the self-report task flipped that one on
2026-08-16 and the file is empty again. Its cycle — emptied, refilled
within a day, emptied by the task the red was filed against — is the
intended one running at full speed, and the only reason it is legible is
that nobody restated the total while it was moving.

Do not restate any of that as a suite `expected failures=N`. This paragraph
was rewritten on 2026-08-15 claiming one N across three lines at once and a
concurrent curator batch adding a red in `test_backend.py` falsified it the
same hour. Measure per file:
`grep -cE '^\s*@unittest\.expectedFailure\s*$' tests/<file>.py`. That is
drain state, not an invariant; see the census comment above `CATALOGUE`
before reconciling anything by hand.

**THE TWO HALVES ARE NOW BOTH DERIVED, AND NEITHER IS RESTATED HERE.** The
catalog half is `CATALOGUE_RED_IDS` in tests/test_mutants.py, checked
against the live decorator by
`TestCoverage.test_the_catalogue_reds_are_the_ones_declared`; the
non-catalog half is `HAND_AUTHORED_RED_CLASSES`, checked by its sibling
`..._the_hand_authored_red_classes_are_the_ones_that_exist`. Read the ids
there, or run `mutants list --red`. This paragraph used to transcribe them
and went stale FOUR recorded times, most recently omitting batch 21's
`headless_chain_reads_through_node` while the sentence beside it counted
"four of the six" — a total that only adds up at seven. Batch 22 built the
missing guard rather than correcting the list a fifth time.

One warning the rebuild earned: **the totals came back to 17 and 21 by
coincidence.** Task 56 and the curves fold-in took the model count down to
15 and batch 22's two new reds put it back, so the numbers this paragraph
had carried since Task 56 were wrong for a week and are right again today
for different reasons. A matching total proves nothing here; only the split
does, and only because something now checks it.

Non-catalog red classes: *none*

The roster is ONE LINE and every entry sits on it, because the guard reads
a single line and a continuation is invisible to it — which it proved by
passing over a second entry the first time it was written across two
lines. **It is empty as of 2026-08-26 and the emptiness is spelled
`*none*`**, never as a blank: at zero reds an unreadable line and a
correct one would otherwise say the same thing to the guard, and it now
refuses both.

What drained it was the five-round ungroup fold (`74976c8`), which cleared
all three reds at once — two in `TestAnUngroupIsNotRevokedByTheNextSave`,
one in `TestOnlyAGroupingGestureChangesGrouping` — so both classes left
the line rather than dropping to a number. Ctrl+Shift+G used to be
honoured for exactly one save and then silently revoked, because
`_interpret_user_composites` decided `ungrouped_by_user` against the
PREVIOUS SAVE and the base stopped carrying the group; the gesture is now
REMEMBERED on the host and reconciled wherever a scene arrives, including
the routes that never commit. The two reds in the first class were split
so that a repair which kept re-grouping while merely narrating it would
clear only the narrower one. No such repair landed — the re-grouping
stopped — so the split never had to do its work, and both cleared
together.

ONE curator flipped all three in one pass, deliberately: two hands editing
a two-line roster is how a transcription goes wrong, and this line is the
half no live value owns. Each flip was witnessed with the mask lifted
rather than credited from the colour — `tests/` held at `74976c8` while
`canvas.py` varied, `d145e5e` and `8f997f6` both reporting `failures=3
errors=0` by assertion, `74976c8` reporting `failures=0`, and the ungated
neighbours green at every revision, which is what separates a mechanism
repaired from one gone quiet.
The user ruled that a composed host whose part-shaped ink is drawn but
linked to nothing is DEAD and must keep being reported, and that what is
wrong is the finding calling it "missing". That left
`duplicated_widget_reported_as_bare` right about its subject and void about
its remedy: it took the ruling's assertable half and went green, and the
half left over is a two-scene property (these two pictures must not read
alike) that no catalogue `expect` slot can hold. The per-file durable count
did not move — one decorator left `TestMutantCatalogue` and one arrived
here.

**That line is the SIXTH census guard's subject and is now CHECKED.**
`TestCoverage.test_the_handover_transcribes_the_hand_authored_red_classes`
parses it out of this file and compares it to the live decorators, so a
class arriving or leaving fails the default suite in the commit that moved
it. The grammar is fixed and small: `` `TestClassName` (N) `` entries
separated by commas, or the literal `*none*` when there are none. Anything
else on that line is a PARSE ERROR and not an empty read — at zero reds a
correct transcription and a garbled one both enumerate nothing, so the
guard cannot be allowed to accept "nothing found" as agreement. Deleting
the line fails too. Everything after this paragraph is history; the roster
itself lives on that one line and nowhere else.

WHY IT TOOK NINE STALENESSES. This transcription kept its own casualty
list and the list grew to eight while the sentence beside it said *"Read
the dict, not this sentence"* — an instruction to distrust the words,
issued by the words, with nothing behind it. The dict was guarded from
batch 16 on; the paragraph describing the dict never was, so every drift
had to be found by a human who happened to read both. The ninth is the one
that made the point unarguable: `HAND_AUTHORED_RED_CLASSES` went to `{}` at
TASK-INKBAND-SPACING on 2026-08-20 and the paragraph went on naming six
classes and eleven reds over a tree that has none. **A pointer at a checked
fact is not a check.** The eight before it, kept for the shape: five by
2026-08-16, then the seventh and eighth together that day when Task 52
arrived to delete one entry and found `TestReplayOrderFidelity` (2) had
drained and left while batch 25's three arrivals had never been transcribed
at all — a flip and an addition in the same window, which is the motion
that leaves a total right and a roster wrong.

`TestSnapshotTierOne` was the fifth and the only one outside
`tests/test_mutants.py`; it left on 2026-08-16 when the self-report task
recovered the tier-1 ceiling. It was never in `HAND_AUTHORED_RED_CLASSES`,
which scans one module and could not have seen it. **That residual
survives this fix**: the new guard derives from the same single-module
scan, so a red in `tests/test_backend.py` or `tests/test_mutants_render.py`
is invisible to it. Those two files have their own guards — the durable
counts and the render row — and the gap is the union of what none of them
name, stated here rather than discovered a tenth time.

Batch 21's six were the biggest single-batch addition on record; Task 56 then
took four of the twenty-one back off. Of batch 21's, four still have no
owner: the r5b-2 cache/replay drift (2, addendum wave) and the
bound-label anchor model (2, addendum wave / label model port). The headless
e1 chain (1, Task 24 follow-up) and the corner bias's collinear-waypoint
over-fire (1, owner of `_label_off_corner`) are likewise still open.
Each red
seeds a known defect and asserts what the detector *should* say, so v0.9 has
an executable definition of done. **Drain them to zero, flipping each in the
same change as its fix** — an unexpected success IS the signal. Never delete
a mutant to get green. `ASPIRATIONAL` holds 5 checks awaiting their lints
(each flip must add a real other-pole neighbor). The **shape-blindness
family is CLOSED on the lint tier as of v0.9 Task 56**: all six pinned
instances (endpoint lint, `_seg_hits_rect` diamond + ellipse, `fit_label_in`,
the text/node bbox checks in three arms, and `min_clearance`) are green. Task
56 landed the last of it — `shape_overlap`/`shape_span`/`shape_area` in
canvas.py, called from the text loop, the arrow-label loop and the shape pair
loop, the three sites `_seg_hits_rect` and `marker_inset`'s callers never
reached. It left TWELVE new pins behind (four catalog entries —
`ellipse_clearance_overfire`, `boxed_overlap_hides_a_near_miss`,
`stacked_diamonds_near_miss`, `diagonal_ellipses_near_miss` — and the
hand-authored `TestShapeBlindPairOverlap` and
`TestShapeOverlapFindsTheMaximum`, plus the rhombus and shoulder-clip arms
in `TestShapeBlindAnnotationOverlap`), all green, each verified to be the
sole failure under a defect of its own. Two of those came out of the
review's fix round and are worth knowing about as a pattern: the near-miss
arm has to choose WHICH axis to report a gap on, and a rhombus reads the
same either way — so a wrong choice was invisible to every rhombus pin and
took a diagonally-staggered ELLIPSE to see (96 of 294 conic near-misses
silent). Separately, `_OVERLAP_TOL` could loosen 500x with the whole suite
green, because every other pin reads an INTEGER out of a lint sentence;
`TestShapeOverlapFindsTheMaximum` now holds the search against a dense
scan. **When adding a shape pin, ask what the rhombus cannot express.**

What is NOT closed is the WRITE/RENDER
tier the ellipse spike's §10 found: three places that PLACE a glyph off a
bbox corner (`render_svg`'s footnote marker, `cmd_x_as_user`'s `ask` pin,
`pin_spot`'s collision fallback) still drop markers into an ellipse's void,
with `marker_anchor` sitting unused right there. No pin, no owner — the
picture is still wrong after every check has gone quiet.
A FOURTH write-side site now has a pin: `_fan_point` spreads attach points
along the BOUNDING BOX side, so the auto-fan itself parks feet in an
ellipse's corner void — `fanned_ellipse_foot_floats_in_the_void`, red, and
the silence there is structural rather than incidental. On a circle the
miss is `0.118r` and `endpoint_tol` is `max(14, 0.2r)`, so no circle at any
size is loud enough to trip the endpoint lint from its own fan, and
`instruments.float_diamond` — the independent cross-check that caught
`edge_anchor` on the bounding box — filters `type != "diamond"` and never
looks. This is the shape-blindness family's FIRST write-side instance and
it corrects Task 56 §8.5's guess in passing: the diamond is loud (17.9 to
53.0px, both checks, every size), the ellipse is the shape with no reader.
The dedupe-by-defect guard protects
the catalog against same-defect-different-id collisions; the non-catalog
classes are defended by file-section convention + reviewer vigilance (id
prefixes REJECTED — they disarm the duplicate-id guard). Structural notes for
the flip author:

- The two **export reds** (`test_red_freedraw_*`/`test_red_image_*`) and the
  **z-order red** FLIPPED in v0.9 WP4 Task 21 — `render_svg` paints in array
  order and has branches for all nine classes. They lived OUTSIDE `CATALOGUE`
  by design (model detectors cannot observe `render_svg` markup), which is
  still why `mutants list --red` shows fewer reds than the suite carries —
  explained at the `CATALOGUE` definition. `TestExportCompleteness` and
  `TestPaintOrder` now carry no reds at all.
- **The catalog now carries NO reds at all** (v0.9 WP7 task 29,
  2026-08-16), for the first time since it was built. The sentence here
  read "all three remaining catalog reds are ASPIRATIONAL" after WP5 task
  25, "four of the five" after Task 54, and "four of the eight" from
  batch 22 before that. All three of those last reds were WP7's contrast
  trio — `gray_text_on_ground` (`contrast_text`), `pale_stroke_node`
  (`contrast_object`), `tiny_font_text` (`min_font`) — the previous
  sentence predicted that "this list now empties in one change or not at
  all", and one change is what emptied it: the three lints landed
  together, each took a `DETECTORS` row, each mutant traded its borrowed
  `Silence("endpoint_gap")` for its own check's quiet pole, and
  `ASPIRATIONAL` and `RULE8_EXEMPT`'s three borrow rows went with them.
  **Read the emptiness as an event, not as a state reached** — the same
  instruction the render row below carries, which was empty for part of
  2026-08-15 and refilled the same day. A catalog with no reds says every
  defect anyone has thought to pin is caught; it says nothing about the
  defects nobody has thought of yet. `CATALOGUE_RED_IDS` is the derived
  constant and it is now the empty set.
  (`near_miss_clearance`/`min_clearance` and
  `unroled_text_over_node`/`text_overlaps_node` went when Task 23 landed
  both lints; `phantom_passthrough_shared_attach`/`phantom_passthrough`
  went with WP4b's e1 in Task 24;
  `framed_node_escapes_its_lane`/`frame_containment` went with WP5 task
  25, which built the containment lint AND the producer-side fix that
  had been making escaped members.) The other four are against checks
  that already ship, and there were three until 2026-08-15:
  `diamond_clearance_overfire` (`min_clearance`) FLIPPED with Task 56 when
  the pair loop learned to measure the drawn outline, and batch 22's two
  have since gone the same way, both with Task 51:
  `curved_short_finals_escape_the_corridor` (`shared_corridor`) when the
  corridor's straightness gate came off — it could only suppress, and
  what it suppressed was 5 of the corpus's 6 corridors as soon as the
  corners were rounded — and `fanned_ellipse_foot_floats_in_the_void`
  (`float_diamond`) when the auto-fan stopped placing feet in an
  ellipse's corner void AND `float_diamond` learned to read a conic.
  That entry demanded both halves and got both; the check still carries
  the diamond's name, which is a census edit somebody owes. Of the
  older two, `tolerable_gap_hides_interior_run` (`crosses_through_bound`)
  FLIPPED with Task 54 on 2026-08-16, and
  `headless_chain_reads_through_node` — batch 21's, filed deliberately
  carrying no magnitude because the shipped template could not honestly
  give it one — FLIPPED the same day with v0.9 TASK-24-FOLLOW-UP, which
  paid that debt in the change it named: a second lint template reporting
  the span the eye completes ACROSS a headless chain's node, plus the
  magnitude on the entry. No non-aspirational red is left in the catalog.
  Task 54's was the reverse fault in the shape family, which the shape
  fix did NOT touch: `lint_layout`'s interior-run walk is gated on
  `if not outside:`, and the rectangle branch had no tolerance floor
  under it, so moving an arrow's tail 1px PAST the node it crosses added
  an overshoot to a crossing and took the lint from one error to total
  silence, out to the 14px where `endpoint_gap` speaks and reports the
  gap rather than the crossing. The fix gave that branch the floor the
  diamond branch has had since WP4 (`max(outside, inside) <= tol` zeroes
  both, which for a box is the same `abs(shape_clearance)` test), and
  all 24 frozen fixtures replay byte-identical across it — the silent
  band was corpus-empty, which is why nothing in the corpus argued
  against the gate for two versions.
  Neighbor quality differs in kind and
  the distinction is worth keeping: phantom's silence was *contingent* (the
  same builder with a shared attach point fires the borrowed
  `shared_corridor` check, so the quiet is evidence about the picture),
  where a liveness-only neighbor is merely *structural*. The strongest form
  the catalog has held is `tolerable_gap_hides_interior_run`'s, which is not
  a
  Silence at all: mutant and neighbor run the SAME builder 3px apart and
  both assert `crosses_through_bound` at 98px, so the pair pins a GATE — the
  red said the check goes quiet, the green says what it must say when it
  speaks, and neither pole can be satisfied by accident. It is now green on
  both poles, which is the shape a flipped gate is supposed to leave behind:
  the two ASSERTIONS are unweakened and still 3px apart, so the pair now
  says the check answers the same 98px on either side of the bit that used
  to silence it. Read it as the model when a gate needs pinning.
- The **shape-blindness family** ran to six pinned instances, superseding an
  older three-instance sentence that stood here — the endpoint lint (bbox
  corners), `_seg_hits_rect` (over-fire) and `fit_label_in` (labels sized to
  the bbox overflow the inscribed diamond by a measured 11px) were the first
  three. Same root cause throughout; one clipping primitive addressed all
  six, but it had to be CALLED from each site, which is what the per-site
  pins measured — and the last three calls landed in Task 56. The standing
  obligation the family left behind was DISCHARGED by WP5 task 25:
  `frame_containment` measures the member's DRAWN outline against the
  lane, through `shape_overlap`, in the predicate as well as the
  magnitude — a rhombus can corner a lane so the two boxes overlap by
  10px and no ink does, and the box reading calls that contained. The
  seventh site did not open. The family's count stands at six.

Catalog reds, transcribed from live `mutants list --red` on 2026-08-15 —
**re-run it rather than trusting this table** — though as of curator batch
23 (2026-08-15) you no longer have to take that on faith. The model row is
now CHECKED: `TestCoverage.test_the_handover_transcribes_the_reds_it_declares`
parses it out of this file and compares it to the live decorators, so a
flip or an addition that does not edit the row fails the default suite.
That guard exists because this hand copy went stale a FIFTH time — batch
22 added `CATALOGUE_RED_IDS` and its comment claimed to force this table
through the same edit, which it never did, and the row sat two ids short
underneath it. The render row joined it as CHECKED at curator batch 26
(2026-08-16): `render_red_method_names` derives that file's decorated
method NAMES and
`TestCoverage.test_the_handover_transcribes_the_render_rows_reds` compares
the row to them. The `other` row is the one hand copy left in this table
— `durable_red_counts` derives its NUMBER and nothing derives its names —
so the warning applies to that one and only that one:

| tier | red mutants |
|---|---|
| model (default suite) | *none* — READ OFF the guard rather than counted by hand. DRAINED 2026-08-30 by WP-B, the fixer, the same day it refilled, which is the shortest life this row has recorded. as_user_move_strands_bound_arrows (spelled without backticks now that it is green, for the reason at the end of this row) left by the repair its own entry's matrix calls A: cmd_x_as_user's move loop now hands the ids it translated to _x_user_carry_feet, and each bound foot moves by the NODE's delta minus the ARROW's own — so a connector carried along as a group peer does not move twice, one whose far node stayed put has that end pulled back onto it, and one that stayed while its node moved has that foot follow. STATED AS THE PROPERTY, which is this entry's whole lesson: WP6 enumerated three gestures BY NAME and the fourth survived the work package written to close it, so the repair adds no fourth name and a fifth peer of the same shape needs no new clause. THE MEASUREMENT THE CURATOR LEFT OWED WAS TAKEN BEFORE THE REPAIR WAS CHOSEN, and it is what decided between rows A and B. R6's browser control drove server-routed arrows only, so what a real drag does to a HAND-BENT bound arrow was unmeasured; measured now, in a real tab, on a node bound to a five-point hand-authored path: the bound foot follows the node, every interior waypoint stays exactly where the user put it, nothing is re-elbowed, and the routed mark is left alone — even where that leaves a server-routed arrow's signature stale, which the save path accepts at zero errors. Row B, delegating to route_arrow, would have flattened the path the client preserves, so it was not taken, and row C stays refuted with the save path untouched. A SECOND MEASUREMENT SCOPED THE POPULATION: a LINE carrying real bindings on disk reaches the client with startBinding and endBinding STRIPPED, so no drag can carry a bound line's foot and the driver mirrors isBindingElement — arrows only. The verb's silent half is held ungated by test_drag_as_user_moves_the_host_and_carries_its_label, which is what refuses a repair that translates the whole bound component: the far node must not move, and e1 must still bind feed. PREVIOUSLY REFILLED 2026-08-30 by the mutant-curator out of assessment run R6 (r6-5), and this one's subject is not the drawing and not a detector — it is an INSTRUMENT, which is a red shape this row has not carried before. x-as-user move is the verb the assessment drives every user gesture through, contracted to use "the same write path the real client uses — not a reconstruction of it"; its loop carries the host, its containerId and frameId children and its groupIds peers, and an arrow bound through startBinding matches none of them. So the driver strands the arrows bound to the node it drags and posts a picture no browser drag can produce, and the endpoint lint then reports a real defect in a scene only the instrument could author. Measured: same node, same 40x24 delta, same 0-error baseline, byte-identical narration, 2 LAYOUT_ERRORs on the user path against 0 on the agent's. THE RIVAL WAS REFUTED BEFORE THE RED WAS WRITTEN, and it matters because the two readings need opposite fixes — a Playwright drag of a three-arrow node reports the arrows followed it in the client and leaves zero errors after the save, so Excalidraw re-binds before anything is posted and this red accuses the ADAPTER, never the save path. Its expect is Silence("endpoint_gap") with no element=, which is the general property (the drag leaves the referential state a real drag leaves) and not a list of arrow ids: v0.8's WP6 existed to make this verb mirror the real client, enumerated three gaps BY NAME, and the fourth survived the work package written to close it. Second instance of the class in one branch — the loop's own comment records the first, where the same blind spot manufactured half of r4-10's orphaned X strokes. It drained the same day, by the repair described at the top of this row; the fix was not the curator's, and the separation held. DRAINED BEFORE THAT on 2026-08-26 by leaf-1.1.6, the fixer, one day after it refilled: doubled_ink_reads_as_a_healthy_count (spelled without backticks now that it is green, for the reason at the end of this row) left by the last TWO of the three repairs its own entry had measured, taken TOGETHER — the entry offered them as alternatives and they are halves of one repair. composed_host_gaps splits its arms: the MISSING arm counts owned parts and nothing else, the SURPLUS arm counts owned plus unowned ink CONGRUENT to a part the host already owns. A body block's four untagged copies are drawn ink and 8 != 4, and the authored backdrop panel matches no owned shape so it is not billed. WHAT THE ENTRY DID NOT PREDICT, and it took a reviewer to find: congruence ALONE, kept as one number `owned + copies`, makes an ownership count and an ink count CANCEL — strip a backlink and owned falls by one while copies rises by one, because the orphan is congruent to its own surviving siblings. Measured through Store.commit, that first draft silenced body at 1 and at 2 stripped backlinks and image at 1, which is exactly the drag-and-the-parts-stay-behind damage contract C12 is about; slider, toggle and checkbox escaped only because their parts differ in shape from one another, which is why every slider-shaped pole in the tree was green over it. The SPLIT is what makes the C12 narrowing unreachable and it is a stronger guarantee than the first draft claimed: loose decoration is not an input to the missing arm at all, brute-forced at 70 attempts across 7 kinds and 5 loose-ink arrangements with 0 quieted. BOTH HALVES OF THE CENSUS MOVED IN THAT ONE CHANGE and it is the same repair twice, which is why this drain does not have the relocation shape the one below it had: the tail of the ERROR now splits on unowned_ink_inside, so a widget whose ink is drawn-but-unlinked stops reading like one whose ink is gone, and TestTheStrippedHostFindingTellsDrawnFromGone left the non-catalog roster above at the same time. What did NOT move is the ruling's floor — the HEAD of both sentences is untouched, both pictures still parse at 2 missing, and test_both_pictures_are_still_reported is the ungated pole that goes on saying so. This row EMPTIED and REFILLED on the same day, 2026-08-26, for two unrelated reasons, and the pair is worth reading together because they are the two ends of one axis. WHAT ARRIVED: a composed host can draw its parts TWICE — once tagged, once not — and composed_host_gaps counts tagged parts against the floor, reads 4 == 4, and says nothing; its surplus arm cannot reach it either, because untagged ink is not counted as a part. The repair on this branch stopped the ungrouped-duplicate gesture MANUFACTURING that state and did not remove it from the scenes already on disk that catch_up() re-reads every session. Its denominator is DERIVED by sweep_doubled_ink() and pinned ungated by test_every_composed_kind_can_reach_a_doubled_ink_state, so no number about it is written here. Three repairs were measured against patched copies of canvas.py and only one clears every pole: counting unowned ink toward the drawn total reports the doubled block AND silences wc, the narrowing contract C12 forbids; splitting the arms keeps wc and then bills the user for an authored backdrop panel; only requiring the ink to be CONGRUENT to a part the host already owns clears all three. WHAT LEFT, and it was EMPTIED by a USER RULING and not by a repair, which is a drain shape this row had not carried before: duplicated_widget_reported_as_bare (spelled without backticks now that it is green, for the reason at the end of this row) was RE-JUDGED, not fixed: it asked the check to go silent on a host whose part-shaped ink is drawn but linked to nothing, and the user ruled that such a widget is DEAD — drag it and the parts stay behind — so a finding there is correct and narrowing would leave the class reported by nothing at all, including in every scene already persisted to disk. Its expect now asserts the ruling's own half (the check FIRES on that picture, at 2 missing) and is green, and that green leg is what REFUSES the narrowing: measured against a patched canvas.py that takes it, the mutant's finding list is empty and the test fails. THE DEFECT DID NOT LEAVE WITH THE ROW, and reading this *none* as "nothing is red about composed hosts" is the one wrong conclusion available — what remains is a two-scene property (ink drawn and ink gone must not read alike) that no catalogue expect slot can hold, and it is now TestTheStrippedHostFindingTellsDrawnFromGone in the non-catalog roster above, which is why the per-file durable count did not move. Everything from "WHAT WAS RED HERE" onward is the superseded reading, left standing because the correction is the record. Filed by curator batch 46 on 2026-08-25; its sibling composed_host_remedy_reproduces_itself (spelled without backticks now that it is green, for the reason at the end of this row) left on 2026-08-26 by f87c34c, which made the three input paths agree — candidate repair (iii) of the four it was measured green against, and not the message-only one it was measured still-red against. Its row in test_mutants.py carries the witnessing and one finding worth reading: the folded lineage NEVER held a state where that red was red and its neighbour green. It went from no check at all straight to a window at 9a387da/e07384d where composed_part_floor gave input NO FLOOR — which is WRONG-1 from that pin's own fix-agnosticism matrix, the mutant reading green because the check had gone blind — and the NEIGHBOUR failed on both shas, which is the entire reason it is an input pole and not a copy of the sibling's body block. The tip settled on ONE ROW, ALWAYS with all three readers agreeing, and the neighbour passes again. WHAT WAS RED HERE (superseded 2026-08-26 by the ruling above; the remedy this paragraph argues for was ruled against, the subject it describes is unchanged): a duplicated widget is reported as a bare rectangle while its own track and thumb are drawn. Ctrl+Shift+G, save, Ctrl+D, save — two blessed gestures, no op and no CLI anywhere — lands a copy whose parts sit inside its box while nothing names it owner, because _rehost_duplicated_parts drops the copy's backlinks and reconcile_composed's slider arm only re-derives parts that already exist; it is persisted, so the load-time catch-up re-reads it every session. The COUNT the check publishes is true. What is wrong is that its stated cause contradicts the scene, and an agent relaying it tells the user their slider is an empty box while they are looking at a slider. Neither pole reads the message: the mutant says the check must be quiet where the parts are drawn, the neighbour says it must speak where they are not, and together they assert the one thing a rewrite cannot supply — that this check DISTINGUISHES the two pictures. It does not: the two scenes produce a byte-identical ERROR sentence, measured. Silence and not a truer sentence is the right pole because the repo already ruled the class out of scope — composed_host_gaps' own docstring carves out "part-shaped decoration owned by NOBODY ... That is a second instrument and it is not this one" and believed the carve-out automatic. Measured, it holds for exactly the two kinds whose reconcile arm MINTS (body at 4 owned, kpi at 1) and is false for the four whose arms only re-derive: slider, toggle and image at 0 owned with the ink drawn, and checkbox at 0 unchecked / 1 checked. It flips for the WP that owns the paste gate, by re-hosting the copy's backlinks or routing the class to that second instrument; two repairs were measured green against patched copies of canvas.py and three wrong ones measured still-red, each caught by a different leg — deleting the slider floor kills the NEIGHBOUR, erasing unowned ink on commit trips the INK GUARD that drives the real save path, and rewording the causal clause leaves the MUTANT red. That last is the pin's stated opinion: a sentence hedged until it is true of both pictures has not repaired the blindness, it has paid for the lie with the information. THE OTHER TWO REDS THIS BRANCH CARRIES ARE NOT CATALOGUE ENTRIES and are not named here by design — they are hand-authored classes, counted by HAND_AUTHORED_RED_CLASSES, which is the other half of this census and where the remedy-fixpoint sweep lives. Any id listed here is a live red owned by its own fix task. This row states what is red, never how many drains it has seen — the count is derived below and the narrative is not, so a claim about the row's *history* is one nothing checks. (Backtick an id here ONLY as a live red: the guard below harvests every backticked token in the row, so a backticked function name reads as a live red id) |
| render (`tests/test_mutants_render.py`) | *none* — emptied 2026-08-17 by TASK-C4, which flipped the clean-stripe-bands red on `_refuse_unmeasurable`'s blank padding ring. Second drain in this file's life; the first, at Task 50, was refilled the same day. The row stays here empty rather than being deleted, because the guard below reads it and a deleted row is a guard matching nothing |
| other (`tests/test_backend.py`) | *none* — the glossary-challenge red (refilled 2026-08-17 by curator batch 27) drained the same day when TASK-POLISH landed the glossary ruling; this row was stale for a few hours and it is the one row of this table whose NAMES nothing derives, which is why (flagged by TASK-C4, corrected at the MICROFIX-2 fold; the claim that only the model row was guarded outlived batch 26 and was corrected on 2026-08-20) |

The render row proved the warning above it the same day it was
written, back when it was the unchecked one: v0.9 TASK-24-FOLLOW-UP
flipped two of its three in one commit and the guarded model row and the
durable counts both failed in that commit while THAT row sat two entries
stale until the next one. Nothing derived it, so nothing said so — which
was the argument for deriving it rather than transcribing it more
carefully, and batch 26 took that argument. It now applies unchanged to
the `other` row.

The bottom two rows are `@unittest.expectedFailure` method names rather
than catalog ids because they sit outside `CATALOGUE`. The render row was
EMPTY for part of 2026-08-15 — the first time since the file was built,
after three reds left on consecutive days, each the same way (the fix
landing in the same commit as the marker's removal) but for three
different reasons. Curator batch 23 refilled it the same day with three
more, so read the emptiness as an event rather than as a state reached.

WHAT REMAINS IS UNGATED, which is why the gated and ungated runs of this
module report the same expected-failure count. The history is worth
keeping because it is all movement in one direction: the paragraph here
once said "two of the three… that is why the two runs differ by exactly
one"; curator batch 25 added three ungated reds (`_scene_bbox` bounding
stored geometry rather than ink, and the client tier reading clean stripe
garbage as healthy), none needing a browser because each replaces the
renderer or measures a pure function; Task 51c flipped the row's last
GATED red by stopping `render_svg` occluding; TASK-FRAMING flipped batch
25's two `_scene_bbox` reds together.

**The parity is not enforced and the count is not stated here.** This
passage read "measured at **3 and 3**" until 2026-08-16, when both runs
measured **1** — the third figure in this section to be falsified by the
flips recorded two paragraphs above it, in the same file, on the same
day. Derive it if you need it (`MUTANTS_RENDER=1` and without, over
`tests/test_mutants_render.py`), and read the parity as an event rather
than a state reached: the moment any gated red is added the two runs
differ again.

The durable form of the counts, since totals here go stale between commits:
`grep -cE '^\s*@unittest\.expectedFailure\s*$' tests/<file>.py` reads
<!-- live:durable_red_counts -->**0 / 0 / 0**<!-- /live:durable_red_counts --> for `test_mutants.py`, `test_mutants_render.py` and
`test_backend.py` (the three numbers are a LIVE VALUE as of 2026-08-18 —
`tests/livedoc.py refresh` writes them from the same grep the line above
prints, and `TestCoverage.test_the_handover_transcribes_the_durable_red_
counts` stays as the second lock on the sentence's freshness, uniqueness
and anchor; see this repo's `tests/livedoc.py` header on why a TOTAL may
be live where the catalogue-reds table beside it may not. Re-derived
2026-08-18 at the COMMIT-NARRATION task's second fold, which flipped the
SECOND of curator batch 35's pair by making
`_interpret_user_composites` restore the posted order after
reconciliation, so a byte-identical repost of a two-composite scene
stops narrating `reordered` facts about elements the server itself
displaced — draining that class 1 -> 0 and taking the tree 5 -> 4, with
the 4 that remain being curator batch 34's pairs, whose owner is
elsewhere; the 5 was
re-derived 2026-08-18 at the same task's first fold, which flipped the
FIRST of that pair (`TestALineDecorationRemeasureIsNotAUserEdit`) by
teaching `_geometry_derived` to recognise a COMPOSED PART, so a composed
check stroke's browser-re-measured height stops narrating as `resized`
against a box nobody clicked — taking the tree 6 -> 5, the count having
reached 6 by way of curator batches 34 and 35 landing two pairs on the
same day. The two folds are two changes and not one on purpose: the
mechanisms are independent, each was proven to own exactly its own red
by stubbing it and watching that red alone return, and the curator's
routing note had predicted the count would pass through 1. Before that
it was
re-derived 2026-08-18 at the FINAL FIX ROUND's fold,
which flipped the freedraw pair (`TestAFreedrawKeepsTheGeometryItWasGiven`)
by giving `make_element` a `freedraw` branch — points, pressures,
simulatePressure — and drained the tree 2 -> 0; the 2 was
re-derived 2026-08-18 at curator batch 33's fold, which
landed that pair RED — `make_element` dropped a freedraw's points and the
client then drew the whole artifact blank — with the flip owned by the
FINAL FIX ROUND, the wave's last writer; the 0 was
re-derived 2026-08-18 at TASK-ROUTERLEG's fold, which
flipped the router-graze red in the same change that landed the scoring
term and its scene guard, draining the tree again 1 -> 0; the 1 was
re-derived at curator batch 32's fold, which
lands `router_elects_a_grazing_final_leg`'s hand-authored red
(`TestTheElectedRouteIsReadableWhereItLands`) RED against today's router,
taking the drained tree 0 -> 1 with the flip owned by TASK-ROUTERLEG's
fold, next in the train; the 0 was
re-derived 2026-08-18 at TASK-ARRIVALLINT's fold, which
flipped `grazing_arrival_reads_as_square` by promoting
`arrival_through_side` to a live lint and added no red, taking
`test_mutants.py` DOWN BY ONE from the 1 below. That is the only claim
this parenthesis makes about the total, deliberately: the number beside
it is guarded and will fail loudly the moment work landing around it
moves the count, but a sentence describing what the *whole harness*
looks like is guarded by nothing and goes quietly false at every fold —
which is exactly what the re-derivations recorded below are. The
1 was re-derived 2026-08-18 at TASK-ELBOX's fold, which
flipped the elBox and pin-hug reds together with their e2e half and took
the merged tree 3 -> 1; the 3 was
re-derived 2026-08-17 at TASK-VOCAB's fold, which
flipped the vocabulary red and took the merged tree 4 -> 3; the 4 was
re-derived at TASK-COLORPARSE's fold, which
flipped `css_keyword_stroke_is_never_read` and took the merged tree
5 -> 4; the 5 was re-derived at curator batch 31's fold onto
batch 30's, and the arithmetic is the warning below happening a THIRD
time in one wave: batch 30 wrote **4** and batch 31 wrote **2**, each
true of its own head and both wrong of the merged tree, which is 4 + 1.
The grep is the resolution; neither branch's literal is. Batch 31 added
ONE model red — `TestThePinHugSurvivesItsOwnContainer`, the ❓ glyph
pulled off its hug by the frame that CONTAINS its target, on 54 of 291
corpus elements — taking `test_mutants.py` 4 -> 5. Its other three pins
are green by construction and two are not in this file at all: the
flattening-fidelity pair lives in `test_mutants_render.py` and is
UNGATED-green there, and the `elBox` behavioural red is an e2e
`test.fail()` in `frontends/wysiwyg-grilling/tests/e2e/placement.spec.ts`,
which this grep does not and should not reach — so a reader counting
batch 31's reds finds two, and only one of them is in this sentence's
scope. That e2e red is the delivered half of batch 30's `elBox` source
read below; both flip in TASK-ELBOX's fold, together.
The batch-30 re-derivation this replaces read 4, and re-derived was the
operative word there too: that batch
recorded **5** at its own head and the fix round overtook it before the
fold, so NEITHER SIDE of that number was true of the merged tree — the
MICROFIX-2 lesson arriving again, one wave later, and the resolution is
the grep rather than either side. Batch 30 added FOUR model reds, taking
`test_mutants.py` 1 -> 5: the CSS-keyword contrast blind spot, `elBox`
unioning a stored magnitude with a point coordinate, a `rerouted` fact
minted for an arrow whose drawn path did not move, and the accept guard's
revert restoring geometry while leaving the binding and the route
signature stamped. `b4f095c` then FLIPPED the fourth — the whole-element
snapshot — taking it 5 -> 4 in the same change that folds it, which is
the flip contract honoured in the only window it had. That red was filed
hours earlier by the TASK-ATTACH review, against a class the batch had
written fully green, so one class joined `HAND_AUTHORED_RED_CLASSES` and
left it inside a single batch.
The previous re-derivation, at the TASK-MICROFIX-2 fold, read 1 / 0 / 0, where
the merged value matched NEITHER side of the rebase conflict — C4's 10/0/0
and MICROFIX-2's 6/1/0 were each true of their own head and both wrong of
the merged tree; the resolution was the grep, not a side — the rebase conflicted
here, and the resolution was the grep rather than either side, which is
what the instruction below asks for. TASK-C4 FLIPPED the
clean-stripe-bands red on `_refuse_unmeasurable`'s blank padding ring and
added none, taking `test_mutants_render.py` 1 -> 0 and emptying that file
for the second time in its life. The first drain, at Task 50, lasted
hours before batch 23 refilled it; this one is a flip of the LAST
survivor of that refill. The 10 is NOT TASK-C4's: curator batch 29 landed
under it during the work and ADDED one model red —
`TestARetypedValueReachesDiskButNotHistory`, the retyped-focus divergence
FOLLOWUP-B surfaced — taking `test_mutants.py` 9 -> 10. Two tasks moved
two different files between two commits and neither number is what either
task would have written alone, which is the entire case for deriving this
sentence at the head that ships it. The previous re-derivation, taken at
TASK-FOCUS-FOLLOWUP-A's fold, read 9 / 1 / 0: it FLIPPED the
`_stamp_route` ordering red and added none, taking
`test_mutants.py` 10 -> 9 and emptying
`TestStoredBindingsDescribeTheFinalInk` — the THIRD class to make the
join-and-leave round trip inside one batch cycle, and the second batch-27
arrival to drain the day after it was filed. That class keeps every green
pole it had and gained one, a fan-side arm covering the second call site,
so the flip cost the dict a LINE and not a number. This figure was
derived at the rebased head and not carried across the rebase; the
paragraph it sits in has now been re-derived five times in two days by
five different tasks, which is the whole argument for the grep — and the
instruction to whoever folds next is the same one every re-derivation
above followed: on a conflict here, re-run the grep at your own head and
write what it says, never pick a side. This sentence is a measurement,
not an opinion two branches can hold. The
previous re-derivation, still true of everything below it, follows:
taken at TASK-REROUTE's fold, which
FLIPPED the no-op-rewire red and added none, taking `test_mutants.py`
11 -> 10 — the arrival batch 28 predicted by name and by number when it
filed the red, so this movement is the expected arithmetic and not a
drain. The flipped assertion STAYS in the file as the third arm of the
three-arm invariant it always pinned; only its decorator went. What
remains of batch 28's pair is the obstacle-set asymmetry between the op
path and the tidy path, still a CLASS PIN rather than a new defect —
TASK-REROUTE's own report filed the same divergence as a curator seed
from the other side. Batch 28's entry, for the record: it ADDED two model
reds and flipped none, taking `test_mutants.py` 9 -> 11. Batch 27's
re-derivation, still true of everything below
it, follows: re-derived 2026-08-17 during TASK-POLISH, which is
draining what curator batch 27 filed that morning — the activation-bar
orphan flipped first, taking `test_mutants.py` 13 -> 12 and emptying
`TestFurnitureIsNotAnUnconnectedNode`, which therefore LEAVES
`HAND_AUTHORED_RED_CLASSES` one day after joining it; then the glossary
ruling, taking `test_backend.py` 1 -> 0 for the second time in three
days — that file drains to zero, gains one, and drains again, which is
why nobody may carry this figure forward; then owner-aware significance,
taking `test_mutants.py` 12 -> 11 and `TestBatchPathIntegrity` 2 -> 1,
which is the SECOND time in two days that one class has gone 2 -> 1 on a
roundness ruling and the two rulings are different ones; then the
annotation budget, taking `test_mutants.py` 11 -> 10 and emptying
`TestTheAnnotationBudgetCountsWhatTheClientDraws`, the second class to
leave `HAND_AUTHORED_RED_CLASSES` in this one task; and finally the
commit-caller field gate, taking `test_mutants.py` 10 -> 9 and emptying
`TestBatchPathIntegrity` — the THIRD class this task drained out of that
dict, which now holds only the producer/emitter pins. Batch 27's own
re-derivation, still true of everything it describes, follows: taken at
that batch, folding onto
v0.9 WP8 task 30 of the same afternoon. THREE CHANGES MOVED THIS NUMBER
WITHIN HOURS AND THEY WENT BOTH WAYS, which is the whole reason it is
derived: task 30 FLIPPED the mod-roundness red — `mod roundness` on a
server-routed arrow is now refused by name instead of accepted and
re-derived away — taking `TestBatchPathIntegrity` 2 -> 1 and
`test_mutants.py` 8 -> 7; batch 27 then ADDED six reds, five here and one
in `test_backend.py`, which had been drained to zero the day before.
The six: `hyphen_in_the_role_blinds_the_object_reader`, the first entry
in this file whose subject is the HARNESS's own reader rather than a
product check; `TestStoredBindingsDescribeTheFinalInk`, a binding solved
one line before the snap that moved its foot;
`TestFurnitureIsNotAnUnconnectedNode`, an activation bar reported as an
orphan on a correct sequence;
`TestTheAnnotationBudgetCountsWhatTheClientDraws`, a budget that has
never counted a sticky the client drew; `TestRenderSvgDrawsBothArrowheads`,
a fallback picture that draws one end of every relation; task 30's OTHER
filed defect, a
roundness-only mod discarded in silence, which puts
`TestBatchPathIntegrity` back at 2 the same day it reached 1; and, in
`test_backend.py`, the glossary challenge that is re-asked after it has
been ruled on. Nobody may add or
subtract along that chain — run the grep. It read 8 / 1 / 0 at the
spike-program curator
batch, which ADDED six model reds rather than flipping any — three
`CATALOGUE` entries in the attach-side family and three hand-authored
producer/emitter pins — the largest single upward movement this sentence
has recorded. TWO MOVEMENTS IN A DAY AND THEY WENT OPPOSITE WAYS, which
is why this number is derived and not carried: it read 2 / 1 / 0 at v0.9
WP7 task 29, which flipped the whole contrast trio and took
`test_mutants.py` 5 -> 2, emptying the CATALOGUE half of that file's reds
entirely — and the very next commit refilled it, 2 -> 8. Anyone folding
those two changes must DERIVE this from the grep above rather than adding
+6 to the number they last read; the batch was written against 5 and
lands on 2, and adding would have produced 11.
It read 5 / 1 / 0 at v0.9 WP7 task 28, which
drained `TestLoadFindingsReachTheAgent` 4 -> 0 — the largest single
departure the hand-authored census has recorded, and one task rather
than four because the four reds were one fix's four unreached surfaces;
it read 9 / 1 / 0 at v0.9 WP5 task 25, which
flipped `framed_node_escapes_its_lane` when `frame_containment` became a
real lint; 10 / 1 / 0 at curator batch 26 — which ADDED a model
red rather than flipping one, the `commit`-callers pin, and the first
of the two upward movements of this sentence since batch 25 — 9 / 1 / 0 at
TASK-24-FOLLOW-UP, 11 / 3 / 0 at
TASK-MICROFIX, 14 / 3 / 0 at TASK-E9ENVELOPE, 16 / 3 / 0 at TASK-FRAMING
and 17 / 3 / 0 before batch 25). Do not restate any of it as one suite ef
total.

**THIS SENTENCE IS NOW GUARDED — stop hand-editing it and let the guard
tell you.** `TestCoverage.test_the_handover_transcribes_the_durable_red_
counts` derives all three numbers with the very grep printed above and
fails when they part. Built at TASK-E9ENVELOPE, and watched failing
against the real staleness before the numbers were corrected.

WHY THE NUMBERS STAY rather than being replaced by the bare command,
which was the standing queue item and is now superseded: this is the
same choice already taken and written down for the sibling guard on the
catalog-reds table — *"a reader opening the handover to learn what is
red should not have to run the suite to find out, and a transcription
that is CHECKED costs one line per flip"*. Deleting the numbers removes
the staleness by removing the information; guarding them removes the
staleness and keeps it. A live derived assertion beats both prose
forms, so the numbers stay, checked.

WHY IT EXISTS, because the record is the argument. This paragraph went
stale SEVEN times. Sixth: two numbers moved and only one was
TASK-FRAMING's — the render 5 → 3 was its two flips, while the model
20 → 16 had ALREADY been wrong at its base commit `286a5cb`, so the
drain happened somewhere in tasks 51/52/54 and the sentence was never
carried with it. Seventh: TASK-E9ENVELOPE's two model flips (16 → 14)
falsified it again in `03ac73d`, **the very next commit to touch a
red** — one commit after a careful hand correction. A hand copy that
cannot survive a single commit is not a transcription problem, it is a
missing assertion.

And the three guards standing beside it were green through every one of
those seven, correctly: they read the model ROW OF IDS, the coverage
SENTENCE, and the class/count map, and none of them opens this
paragraph. That is the distance rule again — a guard proves the property
it evaluates and no part of the property standing next to it. The
sharpest detail is that this sentence PRINTS ITS OWN DERIVATION, one
line above the numbers: everything needed to keep it honest was already
on the page for six of the seven events. Being cheap to derive is not
the same as being derived.

**Task 50 flipped `test_mutant_opacity_ghost_is_invisible_to_tier_one`**,
the oldest of the three and the one that could not be fixed where it stood.
Neither a product change nor a detector change was available: `render_svg`
emits no `opacity`, tier 2 rasterizes `render_svg`'s own output, and so a
0%-opacity ghost is ink to BOTH tiers by construction. What flipped it was
a third renderer — `client_ablation_findings` drives the real Excalidraw
bundle headless through the screenshot protocol `canvas.py` already ships,
and reads the picture the user's own "Export PNG" makes. Through it the
ghost's ablation delta is not small but EMPTY (full and ablated hash
identically), and the check fires. Note what did NOT happen: `render_svg`
still ignores opacity and the tier-1 blindness is pinned in place by the
flipped test's first assertion. The defect was not fixed, it was made
VISIBLE, which is the third kind of flip this row has recorded. **Task 48
flipped `test_mutant_l_shaped_remnant_hides_a_severed_back_edge`**, a
DETECTOR miss rather than a product defect: `ablation_continuity` read two
pieces of a severed back edge as one stroke because `_completed_by_eye` was
judging bounding boxes, the ink's shape having been thrown away twice over
before it got there. The fix was plumbing, not a threshold —
`pngdiff.tolerant_diff_mask` hands back the residual mask `tolerant_diff`
already computed and discarded, so the predicate now measures where the two
FACING ENDS point. The three synthetic over-merges curator batch 14 could
only reproduce on paper are pinned ungated in
`TestContinuityNarrowingRegime`, which is where a widening back toward the
bbox test gets caught without a browser. Nothing in `canvas.py` moved: the
drawing was never wrong, the instrument reading it was.

The third is **Task 46's `test_mutant_wrapped_text_overruns_the_frames_bottom`**,
and it is the opposite kind. There the picture really was wrong — a wrapped
text painted below the frame drawn around it — and the check had been right
about it all along, so the fix was a product change in `render_svg`'s bounds
loop. A detector miss and a product defect drain the same row and read the
same way in the count, which is the whole reason the row names them rather
than totalling them. The pair that second flip completed is worth keeping
too: `parity_clipped` has now had TWO mutants and both have flipped. Task 22
fixed the first —
`test_mutant_center_anchored_label_is_clipped_off_the_frame`, the centered
label whose leading glyphs fell off the min side — and its own cycle turned
up the same root cause on the other axis: `render_svg`'s bounds loop sized a
text by the UNWRAPPED string while `paint` wraps it, so four lines were
drawn where one line's height had been reserved and the tail left the
frame's bottom. Curator batch 18 pinned that as
`test_mutant_wrapped_text_overruns_the_frames_bottom`, with the review's
min-side amendment recorded on the same entry (a wrapped centered label also
got dead left margin from the same unwrapped measurement — slack, not
clipping, and one fix settles both), and it sat unowned until the gate
scheduled it. **Task 46 flipped it**: `canvas.painted_text_lines` states the
renderer's wrap once and the bounds loop READS it instead of restating it,
so both symptoms went in one change and the two sides cannot drift apart
again. Curator
batch 16's fourth entry — `test_mutant_composed_value_hides_under_its_opaque_owner`,
from the Task 21 review's F2, a composed KPI value banded
beneath its own opaque owner — **flipped green in Task 44**, which split
`normalize_z_order`'s decoration band by part tag so a composite's own
content paints above its owner. The other half of that split — filed by
Task 44 itself (report §8.1), added by curator batch 17 as
`test_mutant_composed_checkbox_state_hides_under_its_opaque_owner`, and
unowned until the gate scheduled it — **flipped green in Task 45**:
composed FURNITURE banded beneath its owner too, so an opaque
`backgroundColor` painted out a checkbox's own check stroke and the drawing
showed a checked control as an unchecked one. `band()` now reads the whole
part vocabulary (`COMPOSED_PART_KEYS`), so every composed part bands above
the owner it is drawn on and only an UNTAGGED `role: decoration` — a
standalone backdrop — still bands beneath. The corpus cost the deferral was
budgeted against came in at emission order only: 5 of 24 artifacts reorder,
all 24 pixel-identical at the transparent pole every shipped owner uses.

Three model rows came from the 2026-08-12 idea-mining arc via the
**mutant-curator agent**, and all three have since flipped: the two export
reds Task 21 fixed (`render_svg` painting nothing for `freedraw`/`image`,
a genuine product defect the curator discovered) and
`diamond_label_overflows_shape`, `fit_label_in`'s inscribed-area blindness,
which Task 17 fixed. That arc is fully drained. The curator + the `mutants`
CLI skill live under gitignored `.claude/` — machine-local like the docs,
discovery-registered.

The 2026-08-12 ELK spike's red,
`test_mutant_snapshot_cap_drops_the_rightmost_node`, **flipped in Task 20**
when `rasterize_svg`'s window and `render_svg`'s scale-down became one
shared ceiling. `test_mutant_opacity_ghost_is_invisible_to_tier_one` had a
different source and a different shape: `render_svg` never reads `opacity`,
so a node invisible on the canvas still contributes ink to tier 1. The two
were once described here as one phenomenon; they are not.

**That paragraph used to end "it flips when ablation runs against the
tier-2 render, which honours opacity", and that sentence was wrong** — kept
here because the error is instructive rather than embarrassing. Tier 2 does
not honour opacity and never could: it rasterizes `render_svg`'s SVG, which
has no `opacity` in it to honour. The renderer that honours opacity is the
Excalidraw CLIENT, which is not tier 2 but a third path entirely, and
naming it wrongly is what let the flip sit unowned for two versions looking
like a small piece of wiring. **Task 50 built that path and flipped it**;
the account above is the corrected one.

`phantom_passthrough_shared_attach` is red for a **missing check**: the
picture is wrong and no detector in the harness can say so. Its `expect` names
`phantom_passthrough`, which has no `DETECTORS` entry at all (declared in
`ASPIRATIONAL` with that reason, so it reads as deliberate rather than as a
typo). It pins the exact geometry ELK shipped — two edges on one attach point
on a node, drawn as one unbroken 448px stroke through it — and fulfils the
sweep survivor's **promote** disposition. Flip it by landing WP4b item 1's
lint and giving it a `DETECTORS` entry.

`test_mutant_snapshot_cap_drops_the_rightmost_node` was red for **missing
ink** — the drawing itself absent from the PNG — until Task 20 made the
raster window follow the drawing and `validate_png` measure against the
drawing's extent; it drives `canvas.py snapshot` end to end and now proves
the rightmost node is in the file.

`collinear_overlap_corridor` is **green and must stay green** — it is the
control saying `corridor.py` already works.
`test_mutant_label_backdrop_severs_connector` also passes: tier 1 reproduces
the opaque label backdrop, so **r5-14's class is now caught from pixels**.

**Two CLI surfaces:**

- `python3 tests/test_mutants.py --coverage` — one row per detector: proven
  (naming its mutant), render-tier (naming its gated test), or UNCOVERED with
  a reason. Coverage totals:
  <!-- live:detector_coverage_totals -->**37 detectors, 33 proven, 3 render-tier, 1 UNCOVERED**<!-- /live:detector_coverage_totals -->
  — a LIVE VALUE as of 2026-08-18: `tests/livedoc.py refresh` writes it
  from `coverage_table()`, and it is still CHECKED (since v0.9 WP7 task 29)
  by `TestCoverage.test_the_handover_transcribes_the_coverage_totals`, which
  parses that sentence and compares it to `coverage_table()`. It is checked
  because it had drifted: it read "18 detectors ... 15 proven" from a
  batch-21 measurement, and `label_on_foreign_node` was registered by the
  curves fold-in without this line moving. The LEDGER was never wrong — the
  gate has always refused an unproven detector — only this hand copy of its
  totals was, which is the same disease as the reds table above and is now
  closed the same way. `crosses_through_bound` was
  the last unproven row and the only one left from day one; batch 20 drained
  it with `tolerable_gap_hides_interior_run`. Every registered detector now
  has something watching it speak — so the next `DETECTORS` entry added
  without a mutant will be the ONLY row in the table, which is the loudest
  this gate has ever been. Keep it that way. Batch 21 fixed a hole in the
  EVIDENCE column while adding to it: `coverage_table` counted a red
  mutant's own `FindingSpec` as proof, so a red sorting before the green
  proof silently displaced it and the row went on saying "proven" while
  citing the entry that asserts the check is SILENT. It had already
  happened once unnoticed (`crosses_through_bound`, whose row stands on its
  neighbour). A red's own expectation no longer counts; a NEIGHBOUR's still
  does, since neighbours are ungated. No status moved.
- `python3 tests/test_mutants.py --sweep` — the discovery sweep. Current
  state: **8 cells run, 7 skipped, 1 survivor**
  (`move_node_onto_rank:chain:ebb2e1f6`, phantom pass-through, dispositioned
  **promote** → V0.9-PLAN WP4b item 1). Exit 0. An undispositioned survivor
  exits non-zero *and* fails a default-suite test.

**The UNCOVERED ledger is 46 rows** (re-measured 2026-08-14) — and as of
curator batch 20 they are ALL finding codes enumerated out of `lint_layout`
and `validate_scene`, each with a `canvas.py` line reference and "no proving
mutant yet". Zero of them are registered detectors any more. Note the
asymmetry: `--coverage` prints only the 22 rows in `DETECTORS`; these 46 live
in the `UNCOVERED` dict in `tests/test_mutants.py` and are the backlog, not
the table. (That row count said 18 until 2026-08-16 — a FOURTH hand copy of
the same derived fact, one paragraph below the one batch 23 put a guard on
and outside its reach, since the guard parses only the totals sentence. It is
corrected here by derivation and left as prose deliberately: three of these
have now gone stale, and the useful record is that the guard's scope is the
sentence rather than the section.) The ledger drains a row at a time and that is the intended
lifecycle: `shared_attach_point` (canvas.py:5688) left on 2026-08-12 when the
ELK spike fired the lint in production and `shared_attach_point_fan_failed`
promoted it into `DETECTORS`; `annotation_overlaps_node` left with Task 23;
`crosses_through_bound` — the last row that was a real detector gap rather
than an un-promoted template — left with batch 20.

**Gate integrity:** pre-commit inspects **tracked files only**. All eight
harness files are tracked, so the green above genuinely covered them — but if
you add a harness file and leave it untracked, pre-commit will pass without
ever looking at it.

### The formerly-pending ten paths — RESOLVED 2026-08-12 (see §4 item 1)

Committed on `v0.8_correctness` in two logical commits (fixture set; run-5
record) and merged into the working branch. The four R4-era stragglers
(R4-arm3/arm4 notes, the two mock-chat files) were archived to
`~/docs/optimization/r4/` — recoverable, deliberately out of the repo.

## 2. What run 5 found

**19 findings / 18 causes / 7 P1 / 0 P0**, two arms, ledgers `.ledger/r5.jsonl`
(arm A, driven first-hand) and `.ledger/r5b.jsonl` (arm B, via a delegated
`skill-optimizer` orchestrator). Full account:
**`capability-assessment-notes-optimizer_R5.md`**.

Headline: *v0.8 fixed the surfaces the agent reads; run 5 found the surfaces
that switch off — and underneath them, write paths that are atomic when they
succeed and lossy when they fail.* The second clause is the agent-under-test's
own phrasing, produced unprompted after living through two of the defects.

Notable passes worth not re-testing: the **event-loop doctrine works** (r4-6
recovered, PASS n=2 — both fresh agents armed `Monitor` unprompted and extended
the shipped filter); referential integrity, canvas-first pins, the pending
banner's three buttons, cadence flip both directions, branch fork/switch/
archive, and pin ageing over an **eleven-round** horizon all held.

## 3. What to do next

**`V0.9-PLAN.md` is the work.** Eight packages, sequenced, each with its
findings, root cause, fix, differential controls and acceptance gate. Two
things about it that matter more than the package list:

- **WP1 ships alone and first** — it is the only cluster that *destroys or
  corrupts user-directed work*. Its gate is one discipline: **cause a failure,
  then diff the state.** "Assert the happy path still works" is what shipped
  all three of its defects.
- **New definition of done:** for anything whose deliverable is a rendered
  image, the gate is a **rendered** check, not an assertion over stored
  geometry. `r5-14`'s path was provably correct and the picture was still
  broken.

**Start WP4 by reading `--coverage` and the red mutant list in §1a.** The
harness turned WP4 from a prose description of five defects into eight
executable ones. Two obligations it discovered are now written into
V0.9-PLAN's WP4 and must not be re-derived: the **approach-axis** convention
for `endpoint_gap` magnitudes (a perpendicular-distance fix will not flip the
mutants), and the **straight-run exemption** question for
`ablation_continuity`.

**New agents start at `docs/design/README.md`** — the methodology
(mutation-first development: reds, the flip contract, the drain number,
the guard architecture) and the operating guide (curator flow, review
discipline, the coordination rules each bought by an incident, and where
work is tracked). Written 2026-08-13 to carry the arc's learnings forward.

**The consolidated feature backlog is `docs/todo/feature-backlog.md`** —
one row per opportunity from the four idea-mines with verdicts, sources,
which items are already pinned red in the harness (⛳), the dependency-parked
pair, and the explicitly-rejected list (MCP adapter, ELK, id prefixes) so
nothing gets re-proposed. Machine-local like all of docs/.

**Run-6 probe queue (from the 2026-08-12 idea-mines — canonical home
`docs/research/<slug>_idea_mining_2026-08-12.md`, one per mined repo):**
(1) **cosmetic-repair thrash** — Nimbalyst's field data shows agents
redrawing the same diagram 3-4× after looking at it; our guards (Draw Gate,
one-view-per-round, proportionality) govern how much to draw, not how many
times to redraw after a render — an agent issuing three cosmetic-repair
revisions in one round is our shape of it, untested. (2) The idea-mines'
calibration worth re-testing rather than assuming: two independent projects
in this space ship ZERO diagram-quality checking — our lints + harness are a
category, not a degree; run 6 should confirm agents actually *consume* that
category under pressure rather than bypassing it.

`TestArgusR5Fixture.test_sticky_note_forces_a_repair_on_every_load` is
**red-by-intent** — it pins `r5-13`'s wrong behavior and must be *flipped*
when WP4 lands, not deleted. Its body says so. Note its polarity is the
**opposite** of the eight mutants: it asserts today's *wrong* behavior, so it
is **green now and goes red when the fix lands**, whereas a mutant asserts the
right behavior and is red now. Its state is unchanged by the harness work.

## 4. Decisions — four RULED 2026-08-12; the fifth RULED 2026-08-17

1. **The commit — RESOLVED: split commits on `v0.8_correctness`, R4 archived
   out.** Commit 1: the fixture set (argus-r5 + test_backend + e2e README —
   they must travel together). Commit 2: the run-5 record (R5 notes,
   V0.9-PLAN, this file, .gitignore). The four R4-era stragglers were moved
   to `~/docs/optimization/r4/` instead of being committed. `v0.8_correctness`
   was then merged INTO `v0.9_mutation_harness` so the working branch carries
   everything and the fixture-smoke test runs un-skipped.
2. **The so1 filings — RESOLVED: all six filed to
   `~/docs/optimization/.ledger/so1.jsonl`, and C3 (the `lastmsg.py --grep`
   inversion) fixed immediately** — run 6 must not inherit an instrument that
   inverts answers.
3. **The ELK experiment — RESOLVED: run 2026-08-12, verdict REJECT.** The
   conjunctive rule failed on both sources (classes moved in opposite
   directions; hypothesis falsified); ELK *relocated* the manufactured
   relationship into a 448px shared corridor rather than removing it. No
   dependency lands. Full report:
   `~/docs/optimization/r5/mermaid-spike/ELK-RESULTS.md`; verdict + the
   spike's incidental snapshot-truncation finding recorded in V0.9-PLAN WP5.
4. **`docs/` gitignore — RESOLVED: stays fully local.** The convention stands
   (adr/ and prototypes/ have been local through eight versions). Accepted
   consequences, recorded once: committed code references documents a fresh
   clone cannot read; the harness's FIX-SOON backlog
   (`docs/superpowers/reviews/2026-08-11-mutation-harness/`) exists only on
   this machine.
5. **The skill's own palette vs its own contrast lints — RULED 2026-08-17,
   in three parts.** v0.9 WP7 task 29 shipped `contrast_text` (WCAG 1.4.3,
   4.5:1) and `contrast_object` (1.4.11, 3:1), and the first thing they found
   was this skill's own palette: annotation ink `#5c8a5f` at **3.89:1** (3
   findings, the whole of `contrast_text`'s output on the 24 frozen
   artifacts), and composed-furniture grey `#b8b2a5` at **2.06:1** (16
   elements on 4 artifacts across 3 fixtures — reported by nothing, because
   `lint_layout` exempted server-composed parts outright). Moving either
   floor to fit the palette was ruled out from the start as the threshold
   fudge the harness contract forbids. The ruling instead:

   1. **Darken both, keep the families.** Annotation ink is now **`#47704b`
      (5.55:1)** and furniture grey **`#8d877a` (3.48:1)** — same green, same
      warm grey, both verified with `canvas.py`'s own `contrast_ratio`. The
      v0.3 authorship colour language (agent notes green, user stickies
      yellow) survives, which is what made this a visual-identity call rather
      than a lint-tuning one. The furniture grey is now the named constant
      `FURNITURE_INK` instead of five string literals.
   2. **The composed-furniture exemption is gone.** It keyed on *what the
      element is*, not on who chose the colour, so a regressed default and a
      user's own bad recolour were both silently unreported — that is the
      blind spot it cost 16 findings to find. Furniture is checked like
      everything else now; compliant furniture is quiet by being compliant.
   3. **Every contrast finding carries a computed fix.** The message names
      the nearest shade of the same hue that clears the floor, walked in HSL
      lightness against the background that element is actually drawn on and
      through its own opacity — general to both checks, not a per-colour
      table. Where opacity rather than colour is the cause, no shade is
      offered and "raise its opacity" stands alone.

   **What this does NOT do is drop the corpus count.** The fixtures are
   frozen records of real sessions and keep the old ink, so the 3 annotation
   findings STAND and the 16 furniture ones now SHOW — 19 true statements
   about old drawings. All 19 are itemized BY SHAPE, in the three fixture
   classes that carry them — and the shares are 8 / 8 / 3, which is worth
   reading off the assertions rather than trusting, since a wrong partition
   that still sums to 19 is exactly what the first draft of this paragraph
   shipped:

   - **arm 3 — 8**, in `TestArgusR4Arm3Fixture.
     test_standing_lint_is_the_arms_deliberate_record`: 2 slider tracks on
     `admin-console` and 6 chart-placeholder X strokes on `dashboard`, all
     furniture, no annotation ink on this fixture.
   - **arm 4 — 8**, in `TestArgusR4Arm4Fixture.
     test_this_arms_share_of_the_pre_ruling_palette_findings`: 6 X strokes on
     `dashboard-wireframe`, plus 1 annotation on `dashboard-wireframe` and 1
     on `enrichment-flow`.
   - **run 5 — 3**, in `TestArgusR5Fixture.
     test_this_runs_share_of_the_pre_ruling_palette_findings`: 2 slider tracks
     and 1 annotation, all on `admin-console`.

   That accounts for the 16 furniture (2 + 6 + 6 + 2) and the 3 annotation
   findings separately, which is the check a bare sum cannot do. The split is
   load-bearing rather than incidental: a partial rebase that cleared one
   fixture's share and left the rest fails the class that owns the survivors,
   which a single arm-3 assertion could not see. **The two NEW classes pin no
   warning total** — their findings are matched by criterion and pre-ruling
   hex, so an unrelated check waking up on `dashboard-wireframe` or r5's
   `admin-console` does not fail a test about the palette. Arm 3's older test
   does still pin totals (7 and 8) beside its shape clauses; that predates the
   ruling and is left alone.
   **TASK-FOCUS-FOLLOWUP's fixture rebase is what clears them**, and takes
   all three shares to 0.

   **The full statement lives at the mint site in `canvas.py`**, on purpose:
   this entry and that comment are the only two copies a fresh clone can
   read, because decision 4 above keeps `docs/` local and the design doc
   with it.

## 5. The mermaid spike — read this before touching WP5

A 13-project spike ran after the assessment. Its verdict overturned the doc
claim: **the seed is not better than a competent hand layout at 8 or 12 nodes**,
and at 8 nodes — one above the documented "~7" threshold — it drew *a
relationship that does not exist in the source* while lint reported
`FINDINGS=0`.

But three authoring knobs close most of the gap, free:

```
%%{init: {'flowchart': {'nodeSpacing': 100, 'rankSpacing': 140}}}%%
flowchart LR
  <happy path, every edge, start to finish>
  <then every exception / branch / back-edge>
```

The spike workspace is archived at **`~/docs/optimization/r5/mermaid-spike/`**
— five stdlib measurement scripts, the .mmd corpus (size ladder, direction and
knob variants, the `lay-*` layout sweep, deliberate failure inputs), the
hand-laid controls, and 60 renders. Its README says what each file is and how
to re-run the two open experiments. The originals were in a session scratchpad
and are gone.

`LR` over `TD` is the biggest lever; **declaration order is the cheapest and
removes the 8-node defect outright**. Init directives reach the canvas; `curve`
and every other mermaid edge setting is inert because **canvas.py's router
computes all arrow geometry** — which is why WP4's router work improves seeded
*and* hand-laid drawings at once and is the highest-leverage geometry work in
the plan. `erDiagram → domain` is an unambiguous win (0.27s, no browser).

## 6. Instruments — one trap and one replacement

- **Do not trust `lastmsg.py --grep`.** Literal substring, tool outputs only, so
  every regex silently returns "no hits" and a tool *invocation* is invisible.
- **Use `~/docs/optimization/r5/tgrep.py`** instead — reads tool inputs *and*
  outputs, real regex, labels which side each hit came from.
- **Schedule the cold look, and measure before ruling on it.** Both cold looks
  paid; the second was better because it measured pixels. It also overturned one
  of my dismissals — see [[assess-the-picture-not-the-path]] and
  `~/docs/optimization/r5/cold-look-triage.md`.

### 6a. `npm ci` and the elk packages — witnessed once, NOT reproducing

Salvaged from a commit message on a branch that was dropped, because it was the
only record of it in the tree — and then corrected, because its stated cause is
wrong and acting on it would have cost a tracked-file change for nothing.

**What was seen.** On 2026-08-18, building the web bundle (`693534e`,
TASK-ELBOX), `npm ci` omitted `@mermaid-js/layout-elk` and `elkjs` from
`node_modules` despite both being plain non-optional lockfile entries with valid
integrity; `vite build` then failed to resolve the dynamic import in
`ensureElkLayouts`. Seen three times that day, with and without `--prefix`, in
two checkouts. Worked around by unpacking the two tarballs and verifying them
against the lockfile's own sha512.

**Re-measured 2026-08-26, and it does not reproduce.** Same coin, not a
different one: node v24.14.0 and npm 11.9.0 are the exact versions named, and
the lockfile is byte-identical to the one it was seen against —
`git rev-list --count fd52255..HEAD -- frontends/wysiwyg-grilling/package-lock.json`
is **0**. A real `npm ci` in an isolated extraction exits 0 and both packages
land on disk at 0.2.2 and 0.9.3, with `vite`, `react` and `mermaid` present as
controls and 263 top-level dirs. `--dry-run` was not accepted as the answer: a
resolution plan cannot speak to what lands on disk.

**So the original note's advice is WITHDRAWN.** It said "a stale lockfile is the
likely cause and regenerating it is a tracked-file change that wants its own
review". The lockfile is not the cause — it is fine, and regenerating it would
be a tracked-file change, a review and a fold spent on a wrong diagnosis.
Whatever bit that build three times was environment-transient. If it recurs,
clear the npm cache before touching a tracked file.

Kept rather than deleted only because the workaround is worth having written
down if it comes back.

### 6b. THE CONTROL DRIFTS ONTO ITS SUBJECT — the v0.9 close's signature failure

The most repeated failure of the v0.9 close, across six different agents
including the controller. Instances kept arriving right up to the last fold,
so NO COUNT IS WRITTEN HERE — it would have been wrong within the hour, twice.
It is recorded as one hazard rather than a pile of anecdotes because the shape
is identical every time and the shape is what you have to recognise.

**Every instance failed TOWARD "all clear".** Not one produced a false alarm.
A probe that breaks loudly gets fixed in a minute; a probe that quietly agrees
with you ships.

The seven, each real:

1. A counterfactual archived `HEAD`. Once its agent committed, the "before"
   arm was running the very file it was the control for, and went green. Its
   own words: *"it failed in the direction that reports success."*
2. Another counterfactual stashed against its author's own committed draft
   instead of the base, so both arms were the fix.
3. Two probes hardcoded `sys.path` to the working tree, so they read the FIXED
   file while reporting on a spliced one.
4. An extraction `cd`'d into a scratch directory and lost git context,
   producing empty archives — caught only because `tar` errored.
5. A full-suite run on a `git archive` extraction reported one failure that was
   entirely the harness: `git ls-files` exits 128 outside a repository. The
   same test fails identically on an unmodified extraction, which is the only
   reason it was not published as a finding.
6. A probe run from the wrong working directory returned an empty dict, which
   read as a clean zero.
7. A door sweep never opened the door it was named for — every spec it built
   already carried a `label`, and the code path only fires when one is MINTED.
   It reported 3 revocations where the truth was 11.
8. A grid read its scenes only AFTER the user save, and so missed every cell of
   a defect that lasts exactly one revision — in the scene the batch commits
   and the client renders.
9. A class sweep excluded 133 of 180 classes by selecting for `TestCase`, when
   most of them derive from a local `Base`. It reported covering 180 and
   covered 36.
10. THREE separate agents, and the controller, enumerated the same thing the
    same wrong way: they grepped CALL SITES of a function, when the writes they
    were hunting also occur as dict literals and keyword arguments. An AST
    census found 11 writes across 5 functions where the greps found 5. A shared
    method produces a shared blind spot, and agreement between reviewers is not
    independent confirmation when they used the same instrument.

**IT HAS A SECOND DIRECTION, and the same root cause produces both.** An
extraction-vs-checkout mismatch makes an instrument that reads NOTHING go
silently green — that is the six above. It also makes an instrument that
correctly REFUSES throw a spurious RED, loudly. Instance 5 is that second
kind: one failure in 2047 tests that looked exactly like a finding about the
code under ablation.

`livedoc.tracked_prose_files()` is the cheap canary for both, because it is
the one place in this tree that turns the mismatch into a refusal you can see
immediately: run `python3 tests/livedoc.py check` inside any extraction and it
raises `git could not list tracked markdown ... exit status 128` in under a
second. A reviewer hit exactly that during this close, from a different entry
point and before the suite run existed, and recorded it as "the instrument
working correctly" — which was true and one inference short. It also predicted
that ANY full-suite run in an extraction would show exactly one failure, which
is the conclusion that would have saved a control run.

**What catches it, in order of cost:**

- **Pin every control to an explicit sha.** Never `HEAD`, never "the working
  tree", never a stash. Four of the seven die on this rule alone.
- **Make the probe assert its own edit landed** — re-parse the file and check
  the mutation is present and the original absent. Refuse loudly if not, the
  way one agent's transplant printed `PROBE IS A NO-OP: head and ctrl versions
  are identical` rather than reporting a clean pass.
- **Run the negative pole.** A probe that cannot demonstrate a failure has not
  demonstrated a pass. If the control does not fire, you have no instrument.
- **When a single failure appears in a large run, control it before reading
  it.** Instance 5 was one failure in 2047 tests and looked exactly like a
  finding.
- **Run `python3 tests/livedoc.py check` first in any extraction.** One second,
  and it tells you whether you are in a repository before you spend 400s
  finding out from a test.

The corollary that generalises: **a measurement without a control is an
observation, not a finding** — and the specific danger of a drifted control is
that it converts an observation into a confident wrong finding, with a number
attached.

## 7. Live state you may want, or may want to clean up

Both assessment servers are **still running**, so the projects are browsable:

- arm A — `http://127.0.0.1:34507/` — 6 artifacts, round 6, 6 open pins
- arm B — `http://127.0.0.1:34593/` — 7 artifacts, round 12, **1 revision on
  the banner** (real work: the re-sent sentiment-scorer paragraph. Apply it
  *before* stopping the server — a queued revision does not survive a restart,
  which is `r5-18` itself)

`canvas.py --project <dir> stop` when done. Projects live under
`…/scratchpad/argus5` and `argus5b`; arm A is already promoted as a fixture, arm
B is archived at `~/docs/optimization/r5/projects/argus5b`.

## 8. Repo rules that bite

`canvas.py` is **stdlib-only, single-file, Python ≥3.9**, Google docstrings on
everything, **no autoformatter** (match the packed style by hand), "touch a
function → bring it to standard". The web client is a committed Vite build: edit
`frontends/wysiwyg-grilling/`, `npm run build`, and commit
`skills/wysiwyg-grilling/scripts/web/` as its **own** commit with `--no-verify`
(the hooks exclude that tree) and say why in the message. Full standard:
`AGENTS.md`.
