# Curator — C-3's second axis (binding), three reds

**Branch:** `curator-c3-denominator`
**Worktree:** `/home/cognizac/Projects/wysiwyg_grilling_skill.worktrees/curator_c3_denominator`
**Base:** `bedbc56` (candidate tip). Isolated worktree; no tree shared with an
implementer. **No fix written. `canvas.py` untouched** (`git diff --stat` names
three files, none of them `canvas.py`).

All three defects reproduced on `bedbc56` before anything was encoded. Tier:
**model** — these are `user_note` / `housekeeping` claims about a save the
pipeline has already written, so they are hand-authored classes rather than
`Mutant` catalogue entries, following the precedent the file already sets one
section up ("No `FindingSpec` in this file's vocabulary can be asked about a
`user_note`").

---

## The three reds

All three are **red BY ASSERTION**, not error-red — the mask was lifted with
`--runxfail` and each raised an `AssertionError` from the arm it was written
for, with the magnitude in the message. Verbatim below.

### 1. `TestARerouteCountsThePopulationItQuotes` — C-1 (Critical)

`tests/test_mutants.py`, red arm `test_the_note_counts_the_population_it_names`.

Reproduced through `apply_batch` → user `commit` → `reroute`:

```
server_routed_connectors: ['l1', 'a1']
changes:                  a1 (True), l1 (True), l2 (True)
NOTE: re-routed 3 of 2 server-routed connector(s); 3 moved or were re-aimed:
      a1: path moves up to 40px; start/end re-aimed | l1: path moves up to
      13px; start/end re-aimed | l2: path moves up to 13px; start re-aimed
facts: ['a1', 'l1', 'l2']
feet:  l1 140->127, l2 140->153, a1 unmoved in y
```

Red, mask lifted:

```
AssertionError: Tuples differ: (['l2'], 1) != ([], 0)
```

Magnitude and direction: **one element (`l2`) counted, named and factualised
from outside the population `M` quotes; N over M by 1, numerator ABOVE
denominator.**

**It does not prescribe a repair.** The assertion is that the numerator, the
per-element list, the `rerouted` facts and the denominator describe ONE
population. Proven fix-agnostic by two counterfactual probes run from `/tmp`
as runtime monkeypatches (nothing written to the repo):

| probe | what it did | this red |
|---|---|---|
| A | widened `server_routed_connectors` to one resolved binding | flips (unexpected success) |
| B | narrowed `fan_attach_points` to decline one-end-bound | flips (unexpected success) |

### 2. `TestTidyCoversTheConnectorItMoved` — I-1 (Important)

Red arm `test_every_connector_the_press_redrew_is_covered_by_a_number`.

```
population: ['l1']
NOTE: tidy: snapped 0 node(s) to grid, re-routed 1 of 1 server-routed
      connector(s), normalized z-order
l1 140 -> 127
l2 140 -> 153     <- 13px, narrated nowhere
```

Red, mask lifted:

```
AssertionError: 1 != 2
```

Magnitude and direction: **under by 1, in the SILENT direction** — `N <= M`
holds by construction here, so the connector is in NEITHER number and tidy
mints no per-element geometry fact, making the `user_note` the only surface
that could have named it.

Flips under both probes A and B.

The fixture needed **no off-grid node** — `snapped` stays 0 and tidy writes
because `normalize_z_order` has work, which is one fewer moving part than the
review's construction.

### 3. `TestHousekeepingDoesNotCallALineAnArrow` — I-2 (Important)

Red arm `test_the_sentence_does_not_call_a_line_an_arrow`.

```
housekeeping: re-routed 2 arrow(s) no op named (l1, l2) so they still meet
the shapes they bind — none of them is pinned
```

Red, mask lifted:

```
AssertionError: 'arrow(s)' unexpectedly found in 'housekeeping: re-routed 2
arrow(s) no op named (l1, l2) ...'
```

Magnitude and direction: **2 elements introduced as arrows, 0 of which are
arrows** — over-claiming a type of every element the sentence names. The count
is over the right population; only the noun is wrong.

Independent of the denominator axis: unaffected by probes A and B, as it should
be. Asserts that the noun is not falsified by the population under it — **not**
that the word must become `connector(s)`, so a fix that splits the sentence by
type or drops the collective noun also flips it.

---

## Neighbours

Five, all ungated, all asserted in every commit:

* `TestARerouteCountsThePopulationItQuotes.test_the_both_ends_bound_pole_is_coherent`
  — same scene, `l2`'s far binding restored: "3 of 3", three names, three facts.
* `TestTidyCoversTheConnectorItMoved.test_the_both_ends_bound_pole_counts_both_feet`
  — "2 of 2", feet at `[127, 153]`.
* `TestHousekeepingDoesNotCallALineAnArrow.test_the_arrow_pole_says_a_true_sentence`
  — the same sentence at the arrow pole, where it is true.
* Two `test_the_ablation_leaves_the_drawing_alone` controls, one per
  denominator class — the ink is identical across the ablation, so the reds are
  narration findings and not geometry ones.

**The two controls fire under probe B, and that is documented as their job.**
B is a geometry repair: it makes the ablation change the picture (half-bound
feet stay at `[140, 140]`). Somebody stopping the fan from sliding a connector
the user drew should decide that on purpose, so the arms are left to say so
rather than taught to accept it.

---

## The 163/0 figure, re-derived

Re-derived independently over `tests/fixtures/**.excalidraw`, using
`canvas._router_owns` and the same id-index rule `server_routed_connectors`
uses:

```
artifacts: 24
{'owned': 163, 'both': 153, 'one': 0, 'none': 10, 'router_owns': 163}
files with a one-end-bound server-owned connector: []
```

**The reviewer's 163 / 0 is exactly right.** `_router_owns` and the
type+`server_owns_geometry` spelling agree at 163, so the population is not an
artefact of which predicate was used.

**Zero classification: EMPTY POPULATION.** Two further checks were run so the
classification does not rest on the one count:

* The **10 both-ends-unbound** connectors are not a latent witness either —
  with no bound end the fan has no group to place them in. `reroute_scene` run
  over every one of the 24 artifacts moves **nothing outside `M`**, and **no
  artifact yields `N > M`**.
* A second, independent empty population sits under I-2: **0 of the 163 are
  `line`s.** (The corpus holds 20 raw `line` elements across 3 artifacts —
  `admin-console`, `dashboard`, `dashboard-wireframe` — and none is
  server-routed, so none can enter `drifted`.) The corpus cannot falsify the
  noun either.

**Reachability denominators.** `Store.reroute` writes at all on **7 of 24**
artifacts (`reroute_is_fossil`). Of those 7 — and of all 24 — **0** can state
C-1, because the statement needs a one-end-bound server-owned connector and
there are none. So the corpus denominator for all three reds is **0 of 24**,
and every scene here is built in flight through `apply_batch` + a real
`commit(author="user")`.

---

## Suite state

| | reviewer's baseline at `bedbc56` | this branch |
|---|---|---|
| passed | 1729 | **1734** (+5 neighbours/controls) |
| skipped | 46 | 46 |
| xfailed | 4 | **7** (+3 reds) |
| subtests | 799 | 799 |

`uvx --with pytest-xdist pytest tests -n auto -q` → `1734 passed, 46 skipped,
7 xfailed, 799 subtests passed in 145.14s`. Nothing else moved.

Model tier alone: `487 passed, 7 xfailed, 362 subtests passed`.

**Detector coverage delta: none, and none was available.** `mutants coverage`
prints 34 rows and **zero `UNCOVERED`** entries at `bedbc56`; the table is
about `collect_findings` finding codes, and these three defects are narration
surfaces no detector owns. Nothing to delete, nothing to promote there.

---

## Two files touched beyond the catalogue, both mechanical

`tests/test_mutants.py`'s own gates fired on the new reds, which is them
working:

1. `HAND_AUTHORED_RED_CLASSES` — updated to name the three classes with their
   red counts, with the origin and the expected flip pattern recorded in the
   comment above it (the first two flip together on any denominator repair; the
   third flips on a string and is independent).
2. `SESSION-HANDOVER.md:379` and
   `frontends/wysiwyg-grilling/tests/e2e/README.md:20` — **live values**, not
   prose. `test_the_handover_transcribes_the_durable_red_counts` demanded them
   and named its own tool; `python3 tests/livedoc.py refresh` moved
   `4 / 0 / 0 → 7 / 0 / 0` and `1779 → 1787`. **If either conflicts on the
   fold, re-run `livedoc.py refresh` rather than resolving by hand.**

`_fan_connector` gained `bind_end: bool = True` and `_fan_store` gained
`half_bound: bool = False`, both defaulted off, so every pin written against
that fixture measures the scene it was written against. This is the dedupe
duty: one base scene, now three doors, differing by exactly the field under
test.

---

## Handing back

**One trap, deliberately left open, and it needs your ruling.**

C-1's red asserts coherence only — per instruction, it must not pick between
narrowing the numerator and widening the denominator. Coherence is silent about
**coverage**, and the two fixes are not equivalent there:

* Widen `M` → `l2` is counted and named. Both reds flip. Coherent and covered.
* Narrow `changes` → the note says "2 of 2", `l2` appears on no surface, **and
  `l2` is still slid 13px.** C-1's red goes green. That is not a new defect —
  it is I-1's defect arriving at the reroute door.

So a numerator-narrowing repair would flip C-1 while moving its silent half one
door over, and nothing in the catalogue would say so. I did not encode a
coverage arm at the reroute door because doing so would pick the fix on this
harness's authority. It is recorded instead as a named trap in the class
docstring ("WHAT THIS RED DELIBERATELY DOES NOT SAY"), with the instruction
that whoever narrows the numerator owns extending
`TestTidyCoversTheConnectorItMoved`'s pin to the reroute door in the same
change.

**Recommendation:** rule now that the three doors close on ONE population
decision, and hand all three findings to a single owner. The review reached the
same conclusion from the other direction ("the choice belongs with the same
owner as I-1, because the two doors disagree today about which end of that gap
to close").

**Not encoded, out of my charter:** review findings I-3 (the `id: null`
retirement paragraph — a documentation defect over a
structurally-unreachable zero), I-4 (five spellings of `_router_owns`), M-1
(dead `routed.add`) and M-2 (docstring section order). None is a check that
missed a defect in the picture; none is a mutant. They are unowned as of this
report.
