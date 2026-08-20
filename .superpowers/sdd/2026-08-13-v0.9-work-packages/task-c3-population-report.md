# TASK-C3-POPULATION — the reroute narration's population gap

**Branch** `impl-c3-population` · **head** `179899e` · branched from `bedbc56`
· worktree `/tmp/impl-c3-population`

Commits: `a0bbaec` (the five review findings) · `285d1d8` (this report) ·
`179899e` (the six noun sites from the scope addition).

**Gate:** `uvx pre-commit run --all-files` — all 17 hooks pass, including
eslint/tsc (frontend deps installed locally; `node_modules` is gitignored and
not in the commit). Suite: **1730 passed, 46 skipped, 4 xfailed, 799 subtests**
— the reviewer's tip baseline plus the one pole I added.

> **Read § "Live silent disarmament" first.** The mutant regexes are not
> "not dead today" — `connector_noun` already emits `"line"`, so a
> line-typed finding goes unmatched **on the current tip**, and
> `curator-c3-denominator` is writing line-typed mutants right now.

All five findings reproduced independently on `bedbc56` before any edit. The
reviewer's numbers were right; the reproductions are in this report so the
figures are re-derived rather than inherited.

---

## C-1 — which repair, and why

**Widened the denominator.** `server_routed_connectors` now takes **one**
resolving binding instead of two. One word: `all(...)` → `any(...)`.

### The evidence for choosing it

**1. The two repairs give the user different sentences, and one of them is C-3.**

| repair | what the user is told about `l2`'s 13px move |
|---|---|
| widen `M` | `re-routed 3 of 3 …; l2: path moves up to 13px; start re-aimed` |
| narrow `redrew`/list/facts | *nothing* |

Narrowing restores `N <= M` and pays for it by dropping a real mover out of the
note, the per-element list **and** the `rerouted` facts. That is C-3 verbatim —
an operation moves an element it never names — re-entering through the
denominator. `Store.tidy` (I-1) already fails exactly that way, in silence. So
narrowing would have made the loud door match the broken quiet one, when the
task was to fix both.

**2. The helper's own summary line already described the wider set.** Its first
line reads *"The connectors a geometry pass owns and may redraw"*. The
both-ends clause was copied from `routable_arrows`, where it belongs —
`route_arrow` needs a pair to route between. The implementation was narrower
than the name; the name was right.

**3. The boundary is measured, not read.** Every connector shape through
`reroute_scene` on a scene normalized by `Store.commit`:

| shape | server-owned | old `M` | moves? |
|---|---|---|---|
| bound both ends | yes | yes | **13px** |
| bound start only | yes | **no** | **13px** |
| bound end only | yes | **no** | **13px** |
| unbound | yes | no | no |
| bound to a missing id | yes | no | no |
| marked `authored` | no | no | no |
| bent past 3 points | no | no | no |

The widened set is **exactly** the movers. Only the **fan** moves a one-end
connector — `contention_feet` drops it at its own `any(n is None …)` gate
(canvas.py:5705-5708, and its docstring correctly says so) and the router loop
drops it too. One mover of three is enough, and the fan is the one that runs
twice.

> The first run of that probe reported the *unbound* case moving as well. That
> was my fixture omitting `width`/`height`, so `rebuild_bound_elements` filling
> them in read as a redraw. Had I not re-run it through the real doors I would
> have reported a defect that does not exist and drawn the population boundary
> in the wrong place.

**4. Causation, proved by revert-and-rebuild.** With every prose change left in
place, flipping the predicate back `any` → `all`:

```
all(...)  ->  "re-routed 3 of 2 …"   /  tidy: "re-routed 1 of 1", l2 silent
any(...)  ->  "re-routed 3 of 3 …"   /  tidy: "re-routed 2 of 2", l2 named
```

The one word is the sole cause. I-2's noun is unaffected by the flip, which
confirms that fix is independently caused.

**5. The invariant now holds where it can be seen.** `changes ⊆ M` and
`N <= M`, checked across all 24 frozen artifacts (12 produce a non-empty change
list; 0 violations) and on the in-flight one-end-bound case
(`M = changes = {a1, l1, l2}`, `N = 3`).

### The zero that proves nothing

`M` is **unchanged on 0 of 24** artifacts. Re-derived here rather than
inherited: 976 elements, **163** server-owned connectors, split
`{0 bindings: 10, 2 bindings: 153}` — **zero** bound at exactly one end. That
zero is empty-population. The corpus differential, the mutant classes and the
whole suite are all structurally incapable of stating C-1, which is why the
evidence above is built in flight.

### The comment that licensed the loose scan

Retired. It read *"Nothing outside that population can move today; this is what
makes the sentence survive the day something does"* while `l2` moved 13px from
outside it. It held for the loop it guarded and not for the population it
named. It is true again **only because `M` moved**, and the replacement says
so, says what was measured, and says that the executable form of the claim is
`N <= M` at both doors — not the comment.

---

## I-4 — the equivalence question

**All five spellings are equivalent. I collapsed three of them.** Measured, not
read, per your warning.

Differential: all five spellings plus both collapsed forms, evaluated on every
element of the 24 frozen artifacts (976) and on **435 adversarial shapes** built
by crossing `type` × point-string × `customData.routed` mark × `locked`,
including missing `type`, missing `points` and `customData: None`.

```
frozen corpus    n=976    owns-disagreements=0   held-disagreements=0
adversarial      n=435    owns-disagreements=0   held-disagreements=0
frozen corpus    _router_owns True on 163/976;  held True on   0/976
adversarial      _router_owns True on  56/435;  held True on  18/435
```

**The separation figures are the point.** The corpus holds **no pinned
connector at all**, so its `held` zero would have been another
empty-population zero — the adversarial arm exists to make the pin clause
answer both ways (18 True, 38 owns-True-but-unpinned). No spelling raised on
any shape.

Collapsed `apply_ops`' `declined`, `_tidy_pass`'s `pinned |=` and
`Store.reroute`'s `held` to `_router_owns(e) and pinned_to_canvas(e)`. Operand
order moved (`pinned` was asked first, now second); both predicates are pure
reads and `_router_owns` still gates on type **first**, so
`server_owns_geometry` is never asked about a rectangle. The rationale and the
differential live in `_router_owns`' docstring, where the next reader will hit
it.

**I did not add a census.** Your reasoning stands on its own: a syntactic
census is coupled to spelling, catches the harmless revert, and misses the
dangerous simplification.

`git blame` confirms the third copy: `held = sum(` and the ownership clause from
`07135123`, the type clause from `59055b32`, alternating line-by-line. Nobody
typed that predicate as a unit.

---

## The others

**I-1.** Fixed by the same widening — `Store.tidy`'s `redrew` iterates
`connectors`, so widening the population puts `l2` in both numbers.
`1 of 1` with `l2` silent → `2 of 2`. Recorded at `_tidy_pass`' `Returns:`,
including *why no arithmetic could catch it*: `N <= M` held by construction the
whole time the sentence was missing a mover.

**I-2.** `re-routed 2 arrow(s) no op named (l1, l2)` → `2 connector(s)`.
Reproduced through `apply_batch`. The suite's one assertion on that line
(tests/test_backend.py:23617) matches `"no op named"` and never the noun, so it
was blind to this by construction.

**I-3.** Paragraph rewritten, code untouched, as directed. It now says the
divergence is **unreachable, not retired**; that the `index.get` lookups never
run for the divergent case because the gate above them is `routable_arrows`
membership and that helper drops `id: None` (canvas.py:6206); that the two
behaviours still differ and the new one is the better one; and that the
"40 scenes byte-identical" evidence was a structurally-unreachable zero read as
a wide-population zero — left written down because the number *looks* like
corroboration.

**M-1.** Dead `routed.add(e["id"])` deleted. Verified by control flow rather
than by eye: `routed` is written at exactly two sites in `_tidy_pass` (the
`routable_arrows` seed and this add), the loop `continue`s on non-membership,
and `routed` is not returned — so nothing reaching the add was ever absent. A
comment marks the spot, because it read as though the population were still
being accumulated there.

**M-2.** `routable_arrows`' "THE WIDER SIBLING IS …" section moved **above**
`Args:`, matching every other multi-section docstring in the file. Its
equivalence claim is now **false** as a consequence of C-1 — a one-end-bound
*arrow* is in the wider set and not in `routable_arrows` — so it is restated as
a **strict subset**, and the docstring now says outright that the relation is
prose-only, that the sole comparing test
(`test_the_denominator_is_the_arrows_the_pass_was_handed`) runs on a scene its
own docstring says holds no bound `line` and can therefore only observe the
case where the two must coincide, and that **whoever widens either helper
should expect nothing to go red**.

---

## Handed back, not fixed

1. **`Store.reroute`'s `held` counts a population `M` no longer contains.** It
   counts every pinned server-owned connector, including one bound to nothing,
   which `M` now excludes. I collapsed its *spelling* (I-4) and left its
   *population* alone: a pinned connector with no resolving binding could not
   have moved, so naming it is noise rather than a wrong number, and narrowing
   it is a separate judgement from C-1's. Marked as a deliberate choice in the
   comment so the next reader does not read it as an oversight.

2. **`server_routed_connectors` may over-count by one shape the corpus does not
   hold.** A *marked* server-owned **line** carrying more than 3 points is in
   `M` and yet immovable — the router loop takes arrows only, the fan takes 2-
   and 3-point paths only. The corpus does hold four connectors over 3 points
   (`r-pipeline-rerun` at 5 points, `f-edgar-sentiment`, `r-instrument-issuer`,
   `r-story-section` at 4) but all four are both-ends-bound **arrows**, which
   the router does redraw. An over-count keeps `N <= M` and reads as "that one
   needed no redraw"; it is the under-count that prints a number smaller than
   the list beneath it. Documented in `Returns:` rather than special-cased.

3. **Acceptance tests.** None written, per the split — `curator-c3-denominator`
   owns those. Worth flagging for it: the widening is byte-identical on all 24
   artifacts and the suite passes unchanged, so **every one of these fixes needs
   an in-flight fixture**; a corpus-driven pin cannot see any of them. The
   `_fan_store` builder in tests/test_mutants.py is one `endBinding=None` away
   from the C-1 case.

---

# Scope addition — the arrow-noun family in `apply_ops` narration

Three sites handed to me, **three more found by my own sweep**. All six gate on
`("arrow", "line")` and hardcoded the narrow noun. All six reproduced through
the real doors before the edit; after it the arrow and line poles emit
**byte-identical** sentences.

| # | site | source | before (with a `line`) |
|---|---|---|---|
| 1 | `_pin_kin` relation phrase | assigned | `op 0 names c1, which is **an arrow** bound to the pinned n1` |
| 2 | housekeeping `drifted` | assigned (= I-2) | `re-routed 1 **arrow(s)** no op named (c1)` |
| 3 | housekeeping `stranded` | assigned | `1 pinned **arrow(s)** (c1) bind a shape this batch moved` |
| 4 | `Store.tidy` could-not-settle noop | **my sweep** | `re-routing these **arrows** keeps undoing the attach-point fan` |
| 5 | queued-pending note | **my sweep** | `re-route d's legacy **arrows**` |
| 6 | `reroute` CLI subcommand help | **my sweep** | `a re-route of **arrows** an older router drew` |

On #4 I am **not** claiming reachability. The oscillation is router-vs-fan and
the fan does move bound lines, but I could not construct a line-driven cycle.
The noun is wrong either way and `mod points` is the remedy for both types, so
I fixed it and said so in the comment rather than leaving a sentence I would
have to justify.

## The denominator, derived rather than inherited

You were right not to trust the previous sweep's population. Mine:

**Method** — AST walk of canvas.py collecting `ast.Constant` string nodes,
minus those that *are* docstrings (first statement of a module/class/function
body), minus bare type tokens (`"arrow"`, `"line"`). Comments are not literals
and never enter. Reproducible; the script is 40 lines.

**Result — 24 prose literals carrying `arrow(s)` at the tip:**

* **6 wide-population misses** — the table above. All fixed.
* **18 correct**, each checked against the gate that feeds it, not by eye:
  * **arrow-only by an explicit filter (7)** — `arrival_census`,
    `_repair_label_refit_bindings` (×2), sequence-direction
    (`[a for a in arrows if a.get("type") == "arrow"]`), the arrow-budget note
    (`real_arrows = …`), erDiagram cardinality (`if type != "arrow": continue`),
    mermaid seed counts. The budget one already carries a comment saying the
    filter exists *to keep the word honest* — the pattern done right.
  * **arrow-only by value, not type (2)** — the bidi and lane checks, gated on
    `arrowhead_of`. Your reviewer's clearance confirmed.
  * **names both types explicitly (3)** — `points only applies to
    arrows/lines`, `'from'/'to' only apply to arrows and lines`, `A line never
    carries an arrowhead…`.
  * **not a scene population (5)** — budget-override config echo, registry
    `set_budget` validation (×2), mermaid seed totals, the deleted-target
    re-target line (which already carries a comment proving its filter, and
    already files the separate question it raises).

I make no claim about the earlier sweep's 28/10/17 — different scope, possibly
tests and frontend too. What is certain is that all six of these were outside
whatever it counted.

## Live silent disarmament — flagging, not fixing

You asked to hear immediately if a noun change made a mutant regex go quiet.
**None of mine did** — those regexes match lint findings, and I touched no lint
message. But checking that turned up something worse than the latent risk you
described.

The lint producers **do not hardcode the noun**. They format it:

```
canvas.py:15361   "%s %s claims to bind %s but its %s point ends %dpx %s …"
                  % (connector_noun(a), a["id"], …)
```

and `connector_noun` returns `"arrow"`, `"line"` or `"connector"` by type. So
the wide-population repair already landed on the *lint* surface — and the
mutant harness's consumers were never updated. Measured on one scene, one field
apart:

```
type=arrow   user-shaped arrow c1 claims to bind n2 but its end point ends 100px away
             _ENDPOINT_RE -> MATCHES
type=line    user-shaped line  c1 claims to bind n2 but its end point ends 100px away
             _ENDPOINT_RE -> *** NO MATCH — the detector reads as silent ***
```

This is **live on the current tip**, not a future rename hazard. A line-typed
mutant in these families produces a real finding that the harness cannot see:
the detector reads as silent, so the mutant reads as a **survivor**, and an
ablation arm over it would read **green**.

Affected consumers, all in tests/test_mutants.py: `_ENDPOINT_RE`,
`_RUNS_INSIDE_RE`, `_PASSES_THROUGH_RE`, `_SHARED_ATTACH_RE`, `_PHANTOM_RE`,
`_SHARED_LANE_RE`, `_FALSE_BIDI_LINT_RE` — every one anchored on a literal
`arrow ` / `arrows `.

It is dead **today** only because the corpus's 20 lines are all
`role: decoration`, which `lint_layout`'s `arrows` list filters out —
`connector_noun`'s own docstring says exactly that, and calls the family
reachable through shipped ops. **This collides directly with
`curator-c3-denominator`**, which is authoring line-typed mutants as I write.
Not mine to fix (harness regexes are curator territory, and a fix and its
acceptance test from one pair of hands is what we are avoiding) — but it should
reach that agent before it files a survivor that isn't one.

## The test that asserted the old string

`test_dependent_ops_sink_and_unrelated_ones_do_not` (tests/test_backend.py)
asserted `"is an arrow bound to"` and **only ever built the arrow arm** — a
guard that can see one of the two cases its subject covers, which is how the
defect survived.

Updated, not weakened. It is now two poles over one `_sank(etype)` builder that
re-types `t1` through an ordinary user save (the only door that mints a bare
`line`) and **asserts the re-type took** before the batch runs, so a `line` arm
that quietly stayed an arrow cannot pass for the wrong reason. The line pole
asserts the shared sentence **and** `assertNotIn("arrow", said)` — a repair
widening the word to "arrow or connector" would still name a type the element
does not have.

**Both poles proved to fail under ablation** before being kept: flipping the
phrase back to `"is an arrow bound to"` reds both; restoring greens both.

---

# Rebase onto `271e519` — the flips, and the arrow-pole question

**Head `85c9b8e`.** Suite **1784 passed / 46 skipped / 2 xfailed / 810 subtests**;
`uvx pre-commit run --all-files` all 17 hooks green. Base was 1780 / 46 / 5 —
three reds flipped, plus the one line pole I added.

## Bookkeeping — done

Rebased onto `271e519` (two live-value conflicts, both resolved to the base's
value and re-derived by `livedoc.py refresh` afterwards). Dropped the three
`@unittest.expectedFailure` markers **in the same commit as the fix**.

`HAND_AUTHORED_RED_CLASSES` **re-derived by its own guard, not edited from a
count** — I dropped the markers first, ran the guard, and took the dict it
printed:

```
measured {'TestOneComposedPartPredicateHasThreeSites': 2}
```

All three classes carried exactly one red, so each **leaves the dict entirely**
— 4 entries to 1, three *lines* and not three *numbers*, which is precisely the
shape that guard's own message warns about. A count-based edit would have
written zeros.

One repair closed all three: C-1 and I-1 are the loud and quiet halves of the
same `server_routed_connectors` predicate, so widening it covered both doors;
the third was the noun.

## The fourth failure — the guard was right, and my fix was half-right

**Answer: when every connector is an arrow, the sentence should say
"arrow(s)".** The guard's expectation was **not stale**, and I did not touch it.

**Establishing which the fixture builds, since the ids read as lines.**
`_fan_store` names its connectors `l1`/`l2` on **both** poles — the ids are the
shared builder's, not a type claim. Measured:

```
arrow pole: l1.type=arrow  l2.type=arrow   connector_noun(l1,l2)='arrow'
line  pole: l1.type=line   l2.type=line    connector_noun(l1,l2)='line'
```

So the arrow pole's population really is 100% arrows. My previous commit
printed `connector(s)` unconditionally, and the guard fired exactly as
designed. It was right to: **a blanket noun buys the line pole's honesty with
vagueness on the arrow pole** — a second imprecise sentence for the repair of a
false one, which is the trade this file argues against one door over, running
the other way. `connector` is not *false* of an arrow, but it is less than the
sentence knows, and the sentence names its elements so a reader can check the
word against them.

**The repair is `connector_noun`** — this repo's existing one-site answer to
exactly this question, whose docstring already records the same defect on the
lint surface. All three branches measured through `apply_batch`:

| population | sentence |
|---|---|
| all arrows | `re-routed 2 **arrow(s)** no op named (l1, l2)` |
| all lines | `re-routed 2 **line(s)** no op named (l1, l2)` |
| mixed | `re-routed 2 **connector(s)** no op named (c1, c2)` |

Applied at the three sites that **name their elements**: `_pin_kin`'s relation
phrase, housekeeping `drifted`, housekeeping `stranded`.

**Both the guard and the red now pass, neither relaxed.** The guard asserts
`"re-routed 2 arrow(s)"` on the arrow pole — true again. The red asserts
`assertNotIn("arrow(s)")` on the line pole — the sentence says `line(s)`.

## What deliberately keeps a fixed noun

Not everything should be dynamic, and the distinction is the one
`connector_noun`'s docstring draws against `noun_of`:

* **`re-routed N of M server-routed connector(s)`** (both N-of-M doors) — names
  a **population**, a category, not the types of specific elements.
  `_FAN_NOTE_RE` pins that wording deliberately and its comment says why.
  Unchanged.
* **`Store.tidy`'s could-not-settle noop, the queued-pending note, the CLI
  subcommand help** — no population is in hand at any of them; they describe a
  mechanism or a capability. They keep the neutral "connectors".

I also updated my own line-pole test to match the rule rather than pin a
literal: it now asserts the noun is not *falsified* (`assertNotIn("arrow")`)
instead of demanding the word `connector`, and the arrow pole asserts
`"is an arrow bound to"` — back to the string it always had, because on an
arrow that string is true.

## Untouched, as instructed

* The curator's C-1 red asserts coherence only and stays silent about coverage;
  its docstring documenting the trap for whoever narrows the numerator later is
  unchanged.
* No check loosened, no control scene moved.
* The live-disarmament finding above still stands and is still not mine to fix.
