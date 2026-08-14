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
  (`MUTANTS_RENDER=1`, same discover command, ~97s, real chromium) the render-enabled run `OK` — the 17 skips are the render tests,
  21 of them (ef=3); **12 Playwright e2e**; `uvx pre-commit run
  --all-files` green. The 29 expected failures (26 model + 3 render) are red
  *by intent* (see §1a). **The drain number is 27** (24 model + 3 render; live source of truth: the SDD ledger at .superpowers/sdd/2026-08-13-v0.9-work-packages/progress.md — THIS FILE's counts lag by design between gate seams) (Batch D added
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

**What is red, and why that is the deliverable.** Re-measured 2026-08-14
after Task 21, render tier re-counted after curator batch 16: **16
model-tier `expectedFailure` reds + 4 render-tier**, matching the suite's
`expected failures=16` default and `=20` under `MUTANTS_RENDER=1`. That splits as 8 catalog reds (`mutants list --red` is
authoritative) plus **8 non-catalog reds across four guarded classes** —
LoadFindings 4, ShapeBlind 2, BatchPath 1, and `TestSnapshotTierOne` 1,
the last living in `tests/test_backend.py` rather than with the others —
enumerated with reasons at the `CATALOGUE` pointer in tests/test_mutants.py.
Export, Store and PaintOrder have all drained to zero and left that list.
Each red
seeds a known defect and asserts what the detector *should* say, so v0.9 has
an executable definition of done. **Drain them to zero, flipping each in the
same change as its fix** — an unexpected success IS the signal. Never delete
a mutant to get green. `ASPIRATIONAL` holds 7 checks awaiting their lints
(each flip must add a real other-pole neighbor). The **shape-blindness
family is at FIVE pinned instances** (endpoint lint, `_seg_hits_rect`
diamond + ellipse, `fit_label_in`, the text/node bbox checks); WP4's one
inscribed-shape primitive addresses all of them, and is not done until the
ellipse and label-arm mutants flip too. The dedupe-by-defect guard protects
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
- **Seven of the eight catalog reds are ASPIRATIONAL** (re-measured
  2026-08-14): they flip when their LINTS land, not when any existing code
  changes, and each flip must give its mutant a real other-pole neighbor.
  One per aspirational check, exactly — `framed_node_escapes_its_lane`
  (`frame_containment`), `gray_text_on_ground` (`contrast_text`),
  `near_miss_clearance` (`min_clearance`), `pale_stroke_node`
  (`contrast_object`), `phantom_passthrough_shared_attach`
  (`phantom_passthrough`, WP4b e1), `tiny_font_text` (`min_font`),
  `unroled_text_over_node` (`text_overlaps_node`). The eighth,
  `long_run_curve_hides_bidi` (`false_bidi`), is the only catalog red
  against a check that already ships. Neighbor quality differs in kind and
  the distinction is worth keeping: phantom's silence is *contingent* (the
  same builder with a shared attach point fires the borrowed
  `shared_corridor` check, so the quiet is evidence about the picture),
  where a liveness-only neighbor is merely *structural*.
- The **shape-blindness family** now has three pinned instances: the endpoint
  lint (bbox corners), `_seg_hits_rect` (over-fire), and `fit_label_in`
  (labels sized to the bbox overflow the inscribed diamond by a measured
  11px). Same root cause; WP4's one clipping primitive addresses all three.

Catalog reds, transcribed from live `mutants list --red` on 2026-08-14 —
**re-run it rather than trusting this table**, which is exactly the kind of
hand copy that has gone stale here before (it named nine mutants of which
eight had already flipped):

| tier | red mutants |
|---|---|
| model (default suite) | `framed_node_escapes_its_lane`, `gray_text_on_ground`, `long_run_curve_hides_bidi`, `near_miss_clearance`, `pale_stroke_node`, `phantom_passthrough_shared_attach`, `tiny_font_text`, `unroled_text_over_node` |
| render (`MUTANTS_RENDER=1`) | `test_mutant_opacity_ghost_is_invisible_to_tier_one`, `test_mutant_l_shaped_remnant_hides_a_severed_back_edge`, `test_mutant_center_anchored_label_is_clipped_off_the_frame`, `test_mutant_composed_value_hides_under_its_opaque_owner` |

The render row is `@unittest.expectedFailure` method names rather than
catalog ids because all four sit outside `CATALOGUE`; the third pins
`parity_clipped`, the centered-text bounds clip that Task 22 owns, and the
fourth is curator batch 16's, from the Task 21 review's F2 — a composed KPI
value banded beneath its own opaque owner, which **Task 44** owns.

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
shared ceiling. The surviving render red,
`test_mutant_opacity_ghost_is_invisible_to_tier_one`, has a different
source and a different shape: `render_svg` never reads `opacity`, so a node
invisible on the canvas still contributes ink to tier 1. It flips when
ablation runs against the tier-2 render, which honours opacity. The two
were previously described here as one phenomenon; they are not.

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
  a reason. 13 detectors today (re-derived 2026-08-13 post follow-up): 9
  proven, 3 render-tier, 1 UNCOVERED (`crosses_through_bound`).
- `python3 tests/test_mutants.py --sweep` — the discovery sweep. Current
  state: **8 cells run, 7 skipped, 1 survivor**
  (`move_node_onto_rank:chain:ebb2e1f6`, phantom pass-through, dispositioned
  **promote** → V0.9-PLAN WP4b item 1). Exit 0. An undispositioned survivor
  exits non-zero *and* fails a default-suite test.

**The UNCOVERED ledger is 49 rows** — one real detector gap plus 48 finding
codes enumerated out of `lint_layout` and `validate_scene` on 2026-08-12, each
with a `canvas.py` line reference and "no proving mutant yet". Note the
asymmetry: `--coverage` prints only the 13 rows in `DETECTORS`; the other 48
live in the `UNCOVERED` dict in `tests/test_mutants.py` and are the backlog,
not the table. The ledger has drained **once**: `shared_attach_point`
(canvas.py:5688) left it on 2026-08-12 when the ELK spike fired the lint in
production and `shared_attach_point_fan_failed` promoted it into `DETECTORS`
— the intended lifecycle, a row at a time.

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

## 4. Decisions — ALL FOUR RULED, 2026-08-12 grilling session

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
