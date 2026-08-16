# Task 51 report — the fan repair + the attribution fix

BASE `bc48bb11e6093a25cd107046f03723fa9bd0f1a1`, HEAD `b87c99dd`.
Three commits, three reds flipped, each re-proven. Full suites and
`uvx pre-commit run --all-files` green at HEAD.

| commit | what |
|---|---|
| `6cb4218` | 51a — the corridor stops reporting the corner style |
| `1fecd18` | 51b — the fan puts its feet on the ink, one lane apart |
| `b87c99d` | 51c — the export stops painting over other people's ink |

---

## 0. Rule 6: what was stale at my head

Three of the brief's numbers had moved, and one of them changes what
LINTPROMOTE is told.

- **"5 of the 6 corpus corridor findings are between AUTHORED arrows."**
  Still 6 findings, but the split is **5 authored/authored + 1 MIXED**,
  not 5+1-unspecified. The sixth pairs a server-routed arrow with an
  authored one and is structurally out of the fan's reach — §4.
- **`CATALOGUE_RED_IDS` was 8, the handover table was 8, both correct at
  BASE**; they are 6 at HEAD, and the checked-table guard bit twice
  during this task exactly as batch 22 intended.
- The brief's framing that the F8 red "pins the fan's 16px-pitch
  failure" does not survive measurement: F8 is a defect in the corridor
  INSTRUMENT and the pitch collision is a defect in the fan. They are
  two independent bugs that happen to meet at the number 16. Both are
  fixed; §1 and §2 treat them separately.

---

## 1. The corridor arm (51a) — the straightness gate came off

**The measurement that decided it.** `shared_corridors` ran two stages:
classify a drawn stretch by its chord (`_axis`), then require the bow off
that chord to stay inside `_reads_as_line`'s band. Stage 2 is a pure
filter over stage 1's own reading — it can only ever SUPPRESS — so the
only question is whether the suppression is ever right. Over the 24
frozen artifacts:

| corpus | shipped (gate on) | gate off |
|---|---|---|
| as stored | 6 | **6, finding for finding** |
| every arrow rounded | 1 | **6, the same six** |

Rounding corners moves no endpoint. A corridor count that falls 6 → 1
when the corners are rounded is reporting the corner style, not the
drawing — which is the F8 pin's own claim ("the drawing does not
change") stated at corpus scale instead of on one minimized scene.

**Why not the wider band the entry ruled out.** Batch 22 simulated
widening `_reads_as_line`'s floor to 25px and recorded six things it
broke (`TestTiltBand` both ways, `TestFalseBidiOnTheShippedFan`,
`TestRenderedPath`, `curved_elbow_spurious_bidi`,
`long_run_curve_hides_bidi`'s neighbour). All six read `_reads_as_line`,
and this change does not touch it. `false_bidi` keeps the gate and keeps
it strictly, and that split is defensible rather than expedient:
`false_bidi` claims two arrows read as ONE BIDIRECTIONAL LINE, which a
hooked 18px final does not at any separation; the corridor claims only
that two runs share a lane, and a lane survives a bow.

**Why no band could have worked, measured.** The bow of a Catmull-Rom
span is exactly `2/27` of the neighbouring leg's perpendicular reach —
F8's finals are 22.18px on 300px of approach and 13.68 on 185, and
`curved_elbow_spurious_bidi`'s is 7.41 on 100. **Both scenes sit on the
same ratio.** Any band keyed to the leg that produced the bow admits
both, and `curved_elbow_spurious_bidi` must stay silent. The only
discriminator left is the span's own extent, where F8 is bowed 27.7% and
`spurious_bidi` 41.2% — so a band that separates them is a band in
`(0.28, 0.41)`, i.e. exactly the bigger constant the harness rejected.
The gate had to leave the corridor rather than be retuned.

**Honest cost.** With the gate gone, the corridor reads the chord's
fixed coordinate as the lane. The chord is on the page (a Catmull-Rom
span interpolates both stored vertices), so this is not a relapse to
stored geometry — but two runs whose chords are further apart than `tol`
while their bows bring the drawn strokes together in the middle are not
reported. Reaching that needs a bow of most of `tol` and hence a
neighbouring leg of ~14x it, with the strokes still separating at both
ends. Nothing in the corpus reaches it; recorded in `_stretch_axis`'s
docstring as a known residual rather than built for.

I also measured a mean-of-the-drawn-ink lane (chord classification,
lane at the ink's centroid). It invented a corridor the sharp corpus does
not have (`e-fund-out`~`e-tech-out`) and was dropped.

---

## 2. The fan repair (51b) — two failures, one function

### 2a. The feet

`_fan_point` spread slots along the BOUNDING BOX side. Reproduced at my
head before touching anything: three arrows converging on a 300x300
node's left side land at `(200, 175) / (200, 250) / (200, 325)` on a
rectangle, an ellipse and a diamond **identically** — 17.71px and 53.03px
of empty canvas on the two curved shapes, under every tolerance the
endpoint lint has. `edge_anchor` was fixed for this in WP4, so the router
was landing on the ink and this post-pass was sliding it back off.

The box slot still parameterises the fan; it is now pulled onto the
outline along the ray from the node's centre (monotone along a side, so
slot order is preserved). After:

| shape | feet | focus |
|---|---|---|
| rectangle | (200,175) (200,250) (200,325) | -0.5, 0, 0.5 — **unchanged** |
| ellipse | (216,183) (200,250) (216,317) | -0.447, 0, 0.447 |
| diamond | (250,200) (200,250) (250,300) | -0.333, 0, 0.333 |

`shape_clip` returns exactly 1 for a rectangle, so that row is bit-for-bit
what it always was.

**Two silent no-ops the repair would have had without `_edge_side`.** A
foot standing on an outline is on no bbox edge, so (i) `binding_focus`
scored it 0 and the client's first re-render collapses a focus-0 fan back
onto one point — the v0.3 defect the function's own docstring warns
about — and (ii) the per-side ledger dropped it, so a fan could be formed
once and never maintained. `_edge_side` grew an outline arm: within `eps`
of the drawn outline, classify by the axis the point is furthest along
**in the shape's own units** (`|dx|/a` vs `|dy|/b`; raw `|dx|` vs `|dy|`
names the wrong side on any node much wider than tall). Both are pinned.

### 2b. The pitch

`length * k / (N + 1)` puts three feet on a 64px side exactly 16px apart:
clear of the lint's own 12px coincidence window and **exactly ON**
`instruments.shared_corridors`' 16px lane, so the fan's own output was a
merged stroke by the drawing's own measure. Cramped sides now widen to
`FAN_LANE_PITCH = 18`, bounded by the side (`FAN_EDGE_MARGIN = 8`, the
same window the slide fallback refuses to leave) and never narrower than
the even spread:

| side, N | before | after | lane pitch |
|---|---|---|---|
| 300, 3 | 175/250/325 | same | 75 → unchanged |
| 64, 3 | 116/132/148 | 114/132/150 | 16 → **18** |
| 40, 2 | 113/127 | 111/129 | 13.3 → **18** |
| 40, 3 | 110/120/130 | 108/120/132 | 10 → 12, honestly short |

40px cannot hold three lanes 16px apart and the code does not pretend
otherwise; what it must not do is put slots out of order or off the end,
and that is pinned.

The two constants are coupled to a number that lives in another file
(`shared_corridors`' `tol`). Said out loud at the constant.

### 2c. The reader

`float_diamond` filtered `type != "diamond"` and never looked at a conic.
Curator batch 22's validated `_dist_to_ellipse` proposal applied
unchanged — derived in the instruments, not borrowed from
`canvas.shape_clearance`, which is the property that let this instrument
catch `edge_anchor` on the bounding box months before the lint agreed.
**Corpus effect: 0 findings, no over-fire.**

I did NOT rename `float_diamond`, and the decision is recorded in its
docstring: the string is the `DETECTORS` key, the collector's check name,
a coverage-table row, two catalogue entries and a handover row, and a pin
asserts it as the CHECK NAME. That is a census edit and the curator's,
not a fix's.

### 2d. New writer-side regression

`TestFanAttachPoints` (6 tests, test_backend.py). Both catalogue pins for
this family hand-build their scenes, so neither can notice the fan
ceasing to PRODUCE the bad picture. This class is that half.

---

## 3. The attribution fix (51c) — route 2, measured

**Chosen: route (b), stop `render_svg` occluding.** The label's gap is
now masked out of the labelled arrow's OWN stroke (`arrow_label_break`),
which is the same mechanism the client uses.

The measurement that chose it, made at my head rather than inherited:
route 1 (subtract inside `ablation_findings`) closes the test **and
nothing else**. The occlusion stays, and the occlusion is a product
defect, not only an instrument artifact — an unrelated arrow passing
under a bound label comes out of the EXPORT with a hole punched in it,
and that export is the only picture a headless agent ever sees of its own
drawing. An instrument is the wrong place to close a defect in the thing
it measures. Route 2 also collapses the tier-1/client divergence C6
recorded, which route 1 leaves standing.

**A third defect fell out with it, unlisted anywhere.** r5-14's
struck-through label was reachable by EMISSION ORDER alone: a backdrop
painted before its arrow is painted straight back over, which is what
`TestPaintOrder`'s bound-label pair pinned as a live defect pole. A gap
cut out of the ink cannot be filled back in by ordering, so that picture
is now unreachable.

**The corollary held.** Nothing was mirrored into
`client_ablation_findings`; it needed no change under either route.

**C6's docstring obligation, discharged in three places** — the flipped
red records which route was taken and why route 1 was refused; the paired
C6 green records that the divergence it measured is what chose the route
and that its own "WHEN THIS FLIPS" prediction held; `arrow_label_break`
carries the mechanism and the cost of the opaque version.

**Four tests moved with the change, in the same commit, each saying what
it now covers and what it no longer covers** (the brief's test-trap rule,
applied to pins rather than to corridor counts):

- `TestPaintOrder`'s two bound-label pins → assert the glyphs' emission
  order (array order still reaches the markup) and no longer a
  struck-through label (no order can produce one). `_label_offsets` now
  refuses a scene whose stroke carries no break at all, so the pins
  cannot pass on a renderer that dropped the break.
- `test_bound_labels_reach_the_export_with_their_backdrop` →
  `..._with_their_stroke_break`, exact tag count moved from `{text 1,
  rect 1}` to `{text 1, defs 1, mask 1, rect 2}` — still exact, so an
  implementation that stopped emitting the break still fails.
- `test_svg_paints_a_backing_under_an_arrow_label` →
  `test_svg_breaks_the_stroke_under_an_arrow_label`, and it now asserts
  **exactly one** opaque `SVG_GROUND` fill (the paper), which is the
  property "nothing is painted over anybody else" stated directly.

---

## 4. The post-repair corridor census — LINTPROMOTE's input

Measured at HEAD over all 24 frozen artifacts, `isDeleted` filtered.
**6 findings, all `kind: fan`, in 2 of 24 fixtures. Identical to BASE.**

`argus-r4-arm3/enrichment-pipeline` — 5, every one AUTHORED/AUTHORED:

| a | b | overlap |
|---|---|---|
| `e-edgar-sent` | `e-edgar-fund` | 60 |
| `e-edgar-fund` | `e-edgar-insider` | 80 |
| `e-sent-out` | `e-fund-out` | 160 |
| `e-fund-out` | `e-insider-out` | 120 |
| `e-insider-out` | `e-tech-out` | 80 |

`argus-r4-arm4/enrichment-flow` — 1, **MIXED**:

| a | b | overlap | routed |
|---|---|---|---|
| `e-in-sentiment` | `e-edgar-sentiment` | 80 | `92e1e7f2c0` (server) / `authored` |

**Which vanished: none. Which remain: all six, and the auto-fan's own
contribution to this census is zero.** The fan moves not one corpus
arrow, because on every node side at most one member is fannable — the
rest are authored. Verified by replaying `fan_attach_points` over both
fixtures at HEAD: `moved: []`, census unchanged.

**The mixed pair is the one LINTPROMOTE should look hardest at.** Its two
final runs sit at y=112 and y=128 — 16px apart, exactly on the corridor
tolerance — and the fan is structurally unable to reach it: fanning needs
two or more fannable members on one side, and the authored partner is
forbidden. Moving the server arrow alone is a one-sided nudge the fan
does not do and, per the edge-nudging spike, should not learn to do at
route time. So the whole residue is a lint signal, not a router backlog.

### Deltas the brief asked to be stated

Measured BASE vs HEAD across all 24 fixtures, per fixture and in total:
**zero movement on every axis.** `errors 0→0`, `warnings 35→35`,
`edge_crossings 17→17`, `corridors 6→6`, `float_diamond 0→0`,
`score_layout` `crossings 22.638→22.638` and `crossing_angle
23.690→23.690`, `defects 74→74`. 0 of 24 fixtures differ in any field.

The brief expected crossing-score drift "by design where fans merge" and
label-channel movement from the 76px/36px bias bands. Neither happened,
and the reason is the census above rather than luck: the fan moves no
corpus arrow, so no label recenters and no crossing changes. The
frozen-fixture corridor counts the test trap warned about did not move
either, which is why no corpus-count test needed editing.

---

## 5. The three flip re-proofs (contract's second half)

Each stubs the detector the flipped test calls and confirms the test
FAILS. All three under 20ms.

| red | stub | result |
|---|---|---|
| `curved_short_finals_escape_the_corridor` | `instruments.shared_corridors → []` | FAIL, "no finding of check='shared_corridor' element='fa+fb'", 0.01s |
| `fanned_ellipse_foot_floats_in_the_void` | `instruments.float_diamond → []` | FAIL, 0.01s |
| `test_mutant_a_label_riding_foreign_ink_is_not_a_severed_run` | `ablation_findings → []` | FAIL on the liveness ghost, 0.01s |

The third is worth reading: it fails on `assertDetectorSpoke`, i.e. on
the ZERO-EXTENT GHOST rather than on the silence it asserts — which is
precisely what batch 23's "THE LIVENESS CONTROL IS A FIRING" paragraph
said would happen the day this pin went green. The hole it was built to
prevent is not there.

No assertion was weakened. The two catalogue scenes are unchanged, both
mutants' magnitude bands are untouched, and every marker came off in the
same commit as its fix.

---

## 6. Verification at HEAD

- `python3 -m unittest discover -s tests -q` → **1078 tests, OK**,
  skipped 38, expected failures **17** (was 19 at BASE).
- `MUTANTS_RENDER=1 …` → **1078 tests, OK**, expected failures **17**
  (was 20 at BASE).
- `uvx pre-commit run --all-files` → **15/15 Passed**.
- `git status` clean; six model reds and two render reds remain, and
  `CATALOGUE_RED_IDS`, `catalogue_red_ids()` and the handover table all
  agree (the checked guard was the thing that caught me twice).

Test count rose 1072 → 1078: `TestFanAttachPoints`' six.

---

## 7. Concerns

1. **`float_diamond` is now a lie in the identifier** and I deliberately
   did not fix it. It reads ellipses. Renaming moves the `DETECTORS` key,
   `_collect_float_diamond`, the coverage table, `float_diamond_center_
   zero`, two catalogue `FindingSpec`s and a handover row — a census edit
   the curator owns. Recorded in the function's docstring and in the
   handover. **Queued for the curator.**

2. **`_stretch_axis` is now a two-line wrapper over `_axis`** carrying a
   long docstring. It earns its keep as the documented seam (the
   micro-segment trap and the gate's removal are both recorded there and
   both are things a future reader will re-propose), but someone may
   reasonably want it inlined. If it goes, that prose has to land in
   `shared_corridors`.

3. **The corridor's known residual** (§1, drawn strokes converging while
   their chords do not) is recorded, not pinned. It is not reachable from
   the corpus and I did not construct a mutant for it — that is a curator
   judgement about whether an unreachable class earns a pin.

4. **The mixed corridor pair is not fixable by anything in this task**
   (§4) and it sits *exactly* on the 16px tolerance. If LINTPROMOTE tunes
   that tolerance in either direction, that finding appears or disappears
   on its own. Worth knowing before the number is touched.

5. **`FAN_LANE_PITCH` and `shared_corridors`' `tol` are coupled across
   two files** with only a comment holding them together. canvas.py is
   stdlib-only and single-file, so it cannot import the instruments;
   there is no way to make this a shared constant. A test that reads both
   and asserts `FAN_LANE_PITCH > tol` would close it — I did not add one
   because the assertion belongs in the test tier and I had no obvious
   home for it that was not `TestFanAttachPoints`, which already asserts
   the effect (`> 16`) rather than the constant.

6. **I committed the two canvas.py-touching commits with `--no-verify`**,
   having run `uvx pre-commit run --all-files` green over the exact
   working tree immediately before each. The reason is the guide's own
   §5 note: pre-commit in a shared worktree misattributes concurrent
   agents' writes, and curator-batch-25 and impl-mortality are live. The
   final pre-commit run at HEAD is green, so nothing is owed — but the
   commit hooks themselves did not execute on those two, and a reviewer
   should know that rather than infer it from the trailer.

7. **The render cache cold-started** on every canvas.py change, as the
   brief predicted. The gated suite ran 178s on the first post-change run
   and 110–127s after. Not a regression.
