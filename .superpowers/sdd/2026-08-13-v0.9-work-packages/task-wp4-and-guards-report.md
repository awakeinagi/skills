# TASK-WP4-AND-GUARDS — implementation report

**Agent:** impl-wp4-and-guards. **Date:** 2026-08-19.
**Base:** `d0c401c` (tip of `v0.9_wps`). **Head:** `109807a` on
`worktree-agent-af26145014fade2fd`, in worktree
`/home/cognizac/Projects/wysiwyg_grilling_skill/.claude/worktrees/agent-af26145014fade2fd`.
**Scratch:** `/tmp/impl-wp4-and-guards/`, headered, read-only to others.

Six items, all closed. One seventh found and routed, not absorbed.

---

## 0. The headline, in five lines

* **Part 2 (both guard defects) — closed at `aefce59`.** One named
  constant, and a count taken from the comparison that decided there was
  drift at all. Curator batch 39's two reds flip; markers dropped.
* **Part 1 (four pins) — closed at `109807a`.** All four rewritten, two
  renamed, `label_budget` **and** `label_room` deleted.
* **The branch is legitimately RED on three items with named owners** and
  on nothing else. That is the shape the coordinator asked for.
* **A seventh** — the client character-splits an over-wide token and we
  never do — is filed with a mutant-curator (`curator-wordbreak`) with a
  bundle cite and a measured denominator. **Not fixed.**
* **1726 tests before the fold and 1726 after.** No test was lost.

## 1. The commit stack

```
109807a  WP4-AND-GUARDS: four pins that encoded a fiction, rewritten
1b96bf7  TASK-TEXT-TRUTH: the nine expectations this stream owns   (cherry-picked ab1150a)
e195d8d  TASK-TEXT-TRUTH: the room rule, measured against the client (cherry-picked 90e9e56)
aefce59  WP4-AND-GUARDS: the empty-save guard reads a name and counts for real
489166d  Curator batch 39: a guard coupled to a sentence, and a rule typed thrice (cherry-picked 14f1d32)
d0c401c  (base)
```

Two conflicts, both **live values only** (`AGENTS.md`,
`frontends/.../e2e/README.md`, `SESSION-HANDOVER.md`): resolved to the
base's side and re-derived with `python3 tests/livedoc.py refresh`. No
prose was merged by hand.

Shape chosen at the coordinator's direction (option (a)): the fold lands
in one place rather than two, and every red on the branch has a name.

---

## 2. Part 2 — the two guard defects

### 2.1 What was measured before anything was touched

`/tmp/impl-wp4-and-guards/probe_guard.py`, on the batch's own minimal
scene (one artifact, one text element, `lineHeight` drifted on disk —
a field `content_fingerprint` reads and the differ does not):

```
headline   'out-of-session drift reconciled: 0 change(s) differ from history'
artifact f    changes=0   facts=['saved_no_changes']
element n1    differs=True  ['lineHeight']
```

So both halves of the batch's claim reproduce: the arm's `changes` list
is empty, and one element genuinely differs.

### 2.2 (a) The coupling

`headline_for`'s `saved_no_changes` arm **said** the sentence at
canvas.py:10360; `catch_up` **matched** it as a substring at :20381 to
enforce *"a committed reconciliation may never claim nothing happened"*.
Two copies of one string, ten thousand lines apart, joined by equality
and nothing else.

**Repair:** `EMPTY_SAVE_HEADLINE` at module scope beside `SALIENCE`.
`headline_for` returns the name; the guard reads the name. Exactly what
the red asked for and nothing more — it deliberately did not prejudge
where the constant lives.

**Why the constant does not blind the operator.** Batch 39's reword
operator finds its site by *shape*: non-docstring string literals in
non-matching positions. A module-level `Assign` is exactly that, so
`produced_sentence_nodes` still returns **1**, and
`test_the_reword_operator_changes_exactly_one_producing_site` (ungated,
outside the mask) still passes and still asserts a one-line diff.
Verified: every other occurrence of the sentence in `canvas.py` (8221,
9601, 19796, 20754, 20886, 20939, 21234, 21443) is a comment or inside a
docstring, so none is a literal equal to it. The one non-canvas product
reference is a comment in `api.ts`.

### 2.3 (b) The structurally empty substitute

`n_changed = sum(len(p.get("changes") or []) ...)` — and this arm is
entered exactly when no artifact produced a fact, which is what mints the
empty-save sentinel (canvas.py:18486-18487). Measured `0` above.

**Real count, not a different sentence — and the reasoning, since I was
asked to decide and justify.** The red constrains the shape
(`re.findall(r"(\d+) change\(s\) differ", headline) == [1]`), but the
constraint and the right answer agree: the guard's job is to hand a
reader something to go looking with, and the useful half of that sentence
is the number. Dropping the number would satisfy "stop claiming nothing
happened" while giving the reader less than the defect did.

`content_drift_count(before, after)` counts elements that differ across
`{artifact: elements}` maps. It is taken over **the same pair `catch_up`
already compared** (`exp_scenes`, `disk`, both still in scope at the
guard), through **the same canonical units** `content_fingerprint` hashes
— I factored `content_units` out of the fingerprint so that the yes/no
and the magnitude cannot disagree, and so that this repair does not mint
the third copy of a rule batch 39 is about. `content_fingerprint`'s
behaviour is unchanged: same list, same order, same `json.dumps` call.

Same scene now reads `1 change(s) differ from history`.

**A branch I deliberately did not write.** `content_drift_count` could in
principle return 0 while `same` is False — an artifact appearing on disk
with zero live elements. It cannot happen through this arm: a newly
appearing artifact is not in `base_state`, so it does not `continue` at
:18273, its diff has changes, and `semantic_facts` produces facts, so the
empty-save sentinel is never minted and the arm is never entered. I
considered a fallback sentence for the zero and rejected it as
speculative code on an unreachable path; the invariant is stated in
`content_drift_count`'s docstring and in the guard's comment instead.

### 2.4 Evidence, both directions

| | reword red | count red |
|---|---|---|
| before fix (`489166d`) | expected failure (red) | expected failure (red) |
| after fix, markers still on | **unexpected success** | **unexpected success** |
| markers dropped | pass | pass |

Both directions were observed, not inferred. Full suite at `aefce59`:
**1726 tests, all green**, 6 expected failures remaining, all other
owners'.

---

## 3. Part 1 — the four pins

### 3.1 The claim I was told to check first, re-derived here

I did not take the "false on both sides of the wire" claim on report.
Three commands in my own tree:

* **Bundle, `ai` @553035** —
  `(t||!e.autoResize)&&(i=t?_s(t,e):e.width,a.text=Id(e.originalText,Wt(e),i))`.
  `t` is the container; with one present the wrap width is `_s(t,e)` and
  `e.width` — the box we store — is unreachable. `autoResize` is `||`-ed
  on the left, so it does not gate the wrap at all.
* **`_s` @555675, `Yn=5` @248725** — ellipse `round(w/2*sqrt(2))-10`,
  diamond `round(w/2)-10`, else `w-10`. `qg` (headroom) mirrors it.
* **`App.tsx` `restoreForRender`** — passes `refreshDimensions: true`,
  the option its own comment calls *"what re-wraps container-bound
  text"*, nine lines under a parenthetical repeating the fiction.

**Verdict: the belief is false on both sides.** The four pins were
rewritten on that basis.

The table in §A6.3 also re-derived, independently, in my tree
(`/tmp/impl-wp4-and-guards/wp4_pins_local.py`): all four rows match to
the pixel. The `width - 10` sweep reproduces (0 of 781 rectangles differ).

### 3.2 One correction to §A6.3 that changed a pin

§A6.3 note 2 says the cap is `width - 10` "exactly, and **none is
fractional**". True by *value*, and **`client_wrap_width` returns a
`float` on all 781 of those widths** (`150.0`, not `150`) — the rectangle
arm is `float(w) - pad`. That matters because the pin's original worry
(WP4 review F2) was about *bytes in saved JSON*, and a cap is an
intermediate nobody saves. Pinning the cap's type would have asserted the
one of the two that does not matter.

So the rewritten pin asserts on the **stored** width: over all 781
integer widths, `fit_label_in` stores an `int` (`text_ink_width` ends in
`math.ceil`), never a float, never fractional. The worry is preserved
where it bites.

### 3.3 The four, one line each

| was | now | why the old subject went |
|---|---|---|
| `test_fitter_leaves_a_label_no_wrap_can_improve` | `test_the_fitter_ignores_the_width_the_label_arrives_with` | **the subject does not exist**: no decline, one width, one answer. Replaced by the property that makes the walk *impossible* — the same 240x80 diamond fed arrival widths 171 / 17 / 9999 / 0 answers 104x40 every time |
| `test_box_containers_keep_the_budget_they_always_had` | `test_a_box_container_is_its_own_wrap_width` | its **title was its claim and the claim was false**: padding is 5 a side, not 12, so 160px allows 150 and every wrapping rectangle label moved 14px. The half about SHAPE survives — the rectangle arm is the empty one, which is what makes the halving the finding |
| `test_the_walk_never_steps_over_the_box_rule` | `test_a_narrow_rhombus_gets_the_narrow_cap_it_actually_gives` | no candidate set to keep a budget in. What it guarded survives as: the walk's `max(60, ...)` floor was a promise of room a 90px rhombus (cap **35**) does not have. Pinned with a sweep proving no floor crept back (min cap over 20..800 is 0) |
| `test_an_integer_wide_box_gives_an_integer_room` | `test_an_integer_wide_box_gives_an_integer_stored_width` | **worry survives, arithmetic and location both move** — see §3.2 |

Two were renamed because their names *were* the false claim, and a green
test whose name states a fiction is the worst of the four ways to leave
this. Neither name is referenced outside `tests/test_backend.py`
(grepped across `*.py`/`*.md`).

The class docstring now carries the three-way derivation, so the next
reader does not have to find this report.

### 3.4 What a mutant must construct to catch a revert of these four

I was told not to write my own acceptance tests; this is the spec instead.

* **The one that costs nothing to fake and must not be faked:** a revert
  to any walk over the label's own stored box passes a single-arrival-width
  pin trivially. The mutant must **feed one scene two arrival widths** and
  assert one answer. That is the only construction that distinguishes "the
  client's rule" from "a walk that happens to land there".
* **The rectangle arm must be a CONTROL, not a subject.** A revert to
  `width - 24` changes the rectangle by 14px and the diamond by 66px; a
  mutant that only inspects the diamond cannot tell a shape-term bug from
  a padding bug. It needs both, with the rectangle's *own* number.
* **The caps must not be derived from `client_wrap_width`.** The pins
  transcribe `_s` by hand (`160-10`, `round(160/2)-10`,
  `round(160/2*sqrt(2))-10`); a mutant that reads the function under test
  cannot notice that function being wrong. Ideally the client half comes
  from a browser.
* **The floor is the sharpest single assertion.** `min` over 20..800 of
  the diamond arm is **0**. Any reintroduced `max(60, ...)` — the most
  natural "safety" edit somebody makes to this code — fails only that.
* **The stored width's TYPE, not the cap's.** A revert that reintroduces
  float dust shows up in `lbl["width"]`, never in the cap, which is a
  float today and harmlessly so.

### 3.5 Dead code — named, as asked

`label_budget`'s only caller in the tree was the pin asserting it still
returned `width - 24`. **`label_room`'s only caller was `label_budget`.**
Deleting only the one handed to me would have moved the dead body one
frame up, so both are gone, with a comment at the site recording what
they were and why they stopped being the answer.

**`shape_band_width` STAYS**, and this is load-bearing given the
coordinator's correction: it is the geometric primitive
`diamond_label_overflows_shape`'s catalogue entry quotes its derivations
from, `test_band_width_reproduces_the_catalogue_derivations` still holds
those four numbers, and — as the coordinator relayed — `shape_band_span`
beneath it is where the curator's freshly found live `label_adrift`
defect lives. **Nothing I deleted is on `label_adrift`'s path**: I
verified `label_room` had zero callers besides `label_budget` before
removing it.

### 3.6 No silence was restored by loosening anything

Corpus census moved `warnings=50 → warnings=56`. Those +6 are the room
rule's own, itemised in TASK-TEXT-TRUTH's report §A3 ("+6 warnings, no
removals"); the count matches exactly. **No threshold was touched, in
either part.**

---

## 4. Judgements I was invited to reject, and what I did with them

* **"Read pin 1 first, its subject may not exist."** Accepted and
  confirmed. It is the only one of the four whose replacement is a
  *different property* rather than different numbers, and writing it
  first is what produced the arrival-width construction the other three
  lean on.
* **"(b) may be cosmetic."** Rejected — it is a genuine defect and
  reachable. Denominator: it is entered whenever disk diverges from
  history and the differ produces no facts, i.e. the whole
  significant-attrs blind spot; `lineHeight` alone reaches it, and the
  count it printed was `0` over a real drifted element. Not cosmetic.
* **"§A6.3's `label_budget` is dead."** Accepted, and extended by one
  function — see §3.5.
* **§A6.3 note 2's "none is fractional".** Corrected — see §3.2.

---

## 5. THE SEVENTH — filed, not absorbed

**The client character-splits an over-wide token; we never do.** Bundle
`Id` @274820 → `$$` → `z$`, which walks a too-wide word character by
character emitting a chunk per overflow. `canvas.wrap_label_text` is
`split()` plus a greedy join and emits the word whole. So where one
token's ink exceeds `client_wrap_width`, the stored line count
**under**-counts and the stored ink **over**-states, and `fit_label_in`
then grows the container to a height the client overflows. **Same defect
family as the one TASK-TEXT-TRUTH just fixed, one level down**: that
stream fixed *where* the client breaks, this is *how* it breaks.

Measured (`/tmp/impl-wp4-and-guards/probe_seventh.py`):

| scene | server stores | client paints | grown to / wanted |
|---|---|---|---|
| diamond 200x100, cap 90, `'Acknowledged escalation'` | 110x40, 2 lines | 82px, **3 lines** | 100 / **140** |
| diamond 90x100, cap 35, `'Send for second review'` | 52x80, 4 lines | 34px, **6 lines** | 180 / **260** |
| rectangle 80x60, cap 70, `'Reconciliation'` | 101x20, 1 line | 65px, **2 lines** | 60 / 50 |

The first row is the one that matters: 200x100 is the corpus's standard
node size and that is an ordinary label. Note the stored width **110
exceeds the cap 90**, which is the ART-011 re-fire shape (r5-13).

**Denominator, because an absence needs one.** Over the frozen corpus,
**291** bound labels reach a shape/frame container and **0 of 291** carry
a token wider than their cap today — a structural zero, not a live
miscount. What the zero does not say is the distance: tightest margin
**16px** (200px diamond, cap 90, widest token 74), then 20, then 30;
median 140. A plain ten-character word (`'compliance'`, 82px) is chopped
on any diamond up to **182px** wide, ellipse up to 129, rectangle up to
91. On the corpus's own standard 200px diamond the headroom is one
longer word.

**It is live inside a test I wrote.** `test_a_narrow_rhombus_gets_the_
narrow_cap_it_actually_gives` asserts the server's 4 lines on the 90x100
diamond. That pin is right about the server and the server is wrong about
the picture — flagged to the curator so the pin gets a note if the
defect is encoded.

**Routed to `curator-wordbreak` (mutant-curator), with the bundle cite,
the rigs, and the denominator — and explicitly with the verdict left
open**, per the coordinator's caution that a re-calibration routed by the
stream that just measured the area is "most persuasive and least
checked". I did not fix it and I did not tell the curator its
disposition.

---

## 6. Gates

| gate | result |
|---|---|
| `python3 -m unittest discover -s tests -q` at `aefce59` | **1726, all green** (6 expected failures, all other owners') |
| same at `109807a` | **1726**; 3 failures + 1 unexpected success, **all four other owners'** (2 curator catalogue moves, 2 line-height) |
| `TestShapeAwareLabelRoom` at `109807a` | **10 tests, OK** |
| `TestTheEmptySaveGuardIsCoupledToItsOwnWording` | **5 tests, OK** (was 3 pass + 2 expected failures) |
| `uvx ruff check` | passed |
| `mypy (canvas.py)` | passed |
| `livedoc check` | passed |
| pre-commit, all hooks | all passed **except** `backend tests` (the three named reds) and the three frontend hooks, which fail on missing `node_modules` — provisioning, not code |
| test count, `1b96bf7` → `109807a` | **1726 → 1726**, no loss |

## 7. Concerns

1. **`shape_band_width` now has one caller and it is a test.** After
   §3.5 the only thing reading it is
   `test_band_width_reproduces_the_catalogue_derivations` and the
   catalogue prose it pins. I kept it deliberately — the curator's live
   `label_adrift` defect is in `shape_band_span` beneath it and that work
   is in flight — but somebody should decide its fate once that lands,
   rather than letting it sit as geometry nothing consults.
2. **The branch is red on purpose and that is fragile in a wave.** Three
   reds with named owners is honest, but it is exactly the state a
   passing agent "helpfully" makes green. The commit message says so; a
   verifier should be told so too.
3. **My Part 2 fix and the room rule were verified together but authored
   apart.** `aefce59` is green standalone and I re-ran the full suite
   after the fold, so nothing rests on the order — but if the coordinator
   ever wants Part 2 without the fold, `aefce59` cherry-picks onto
   `d0c401c` cleanly with `489166d` beneath it.
