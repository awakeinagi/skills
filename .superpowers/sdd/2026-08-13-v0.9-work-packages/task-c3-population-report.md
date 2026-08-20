# TASK-C3-POPULATION — the reroute narration's population gap

**Branch** `impl-c3-population` · **head** `a0bbaec` · branched from `bedbc56`
· worktree `/tmp/impl-c3-population`

**Gate:** `uvx pre-commit run --all-files` — all 17 hooks pass, including
eslint/tsc (frontend deps installed locally; `node_modules` is gitignored and
not in the commit). Suite: **1729 passed, 46 skipped, 4 xfailed, 799 subtests**
— identical to the reviewer's tip baseline.

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
