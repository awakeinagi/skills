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

## 6b. THE SEVENTH, CONTINUED — and what my instrument got wrong

Written after §5, which is left standing above exactly as first filed so
the correction has something to be a correction *of*.

### 6b.1 My framing was wrong, and so were my numbers

**The check was never silent.** I routed this as an absence. `over_w` is
`longest > room_w`, which is precisely the condition under which the
client chops, so `text_overflow` **speaks on every scene in this class**.
It was wrong about *direction* — `too wide` where the picture is too wide
**and** too tall. That is worse than a silence, because a direction is
what a reader acts on, and reporting it as a silence would have sent the
next reader looking for missing code rather than a wrong term.

**My headline case does not reproduce.** Re-rendered through the shipped
client in my own tree: the 200x100 diamond with `'Acknowledged escalation'`
paints **two** lines (`'Acknowledge'` / `'d escalation'`), not the three I
predicted. Same line count as the server stores, so the stored height is
right and `text_overflow` is **correct** on that scene. My second scene,
the 90x100 rhombus, does reproduce — six painted lines against four
stored.

**The cause, and it is the wave's signature error in a new costume.**
`probe_seventh.py` predicted the browser's chop by accumulating
per-character advances **out of `canvas.NUNITO_ADVANCE`** — the server's
own table, whose disagreement with the browser *is* the defect. The
instrument was made of the thing under test. It was worse than a metric
error: the chop *positions* were wrong in both scenes (`'Sen'`/`'d for'`
where chromium draws `'Send'`/`'for'`), and the one count that matched
matched **by luck**.

### 6b.2 The fix

`client_wrapped_lines` transcribes `Id` → `$$` → `z$`. `text_overflow`
keeps its width on `wrap_label_text` and moves its height onto the new
function. Corpus census unchanged at `warnings=56`, so the fix invents
nothing: the server chops slightly earlier than chromium, but no corpus
label carries a token near its cap.

Commits: `aec24f5` (the curator's red, alone and still red, verified red
**by assertion** in my tree first) then `0165ea3` (the flip). Red before
fix, in that order, deliberately — a mutant that shares a commit with its
fix is born green and proves nothing.

### 6b.0 RETRACTED — read this before §6b.3

**§6b.3 below is WRONG and is kept only so the dead end is not re-walked.
`BOUND_TEXT_PADDING = 5` is CORRECT.** Two curators, with instruments
neither derived from the other nor from mine, put the total padding at
**10.000 exactly**: one fitted token length at fixed size (five lengths,
zero residual, intercept 10.000); the other swept three font sizes, where
the intercept is `2p` and — the clever part — *does not depend on which
face does the measuring* (three words, three exact hits, zero misses).
Both also confirm the three shape arms. My own table-free fit agrees:
`w*(N) = 8.0000N + 10.0000`, zero residual.

**My root cause is refuted too.** I wrote that `Yn = 5` (byte 248725) and
`_s` (byte 555675) were "300KB apart, almost certainly different module
scopes". There is exactly **one** `Yn=` in the 1.4MB bundle, and `_s`
references `$O` and `zO` from the *same declaration cluster*, 5 and 11
bytes from it. Verified myself. **The transcription was faithful.** The
"distant offsets" rule I proposed does not follow from anything and
should not be written down; the existing rule about minified identifiers
stands untouched and is unrelated.

**What was actually happening:** the render tier decides wraps in
**Times New Roman** — chromium's default serif — because nothing awaits
`document.fonts.ready`, and paints in the real face once it lands. My own
letter-based advances confirm the face independently of the curators'
digit-based ones: worst deviation from Times **0.0032 em (0.05px)**,
from Nunito **0.1587 em (2.54px)**, a 50× separation. The server's table
is not implicated and is fine.

**THE CHECK THAT WOULD HAVE KILLED THIS IN ONE MINUTE, and the most
useful thing in this report:** *before changing a parameter, ask whether
the parameter can even reach the symptom.* `'Weekday 06:00'` measures
116px and the ellipse arm at w=160 yields **113 before any subtraction**.
The server predicts a re-wrap at `2p=10`, at `2p=2` and at `2p=0`. **One
of my own six counterexamples was unexplainable by every value of the
constant I was proposing to change** — no browser, no font theory, and I
never asked. Re-verified here; note that only ONE of the six is
unreachable, because the 230px row is at fontSize 14, not the 16 I
assumed on first re-check. That assumption would have published a second
wrong number, in the same week and by the same mechanism as the first.

**Numbers withdrawn:** "8 of 291" AND "14 of 14". Both are single-session
reads off an oracle now known to flip per session (5 fresh sessions gave
1, 2, 1, 1, 1 painted lines for one scene; 20 exports *inside* a session
were 20/20 identical). The six I called false positives are precisely the
labels nearest the cap, which is exactly the population a per-session
metric flip moves. Neither number is usable until the tier awaits fonts.

**The table is confirmed correct from the PAINT, which does not race**
(curator-boundpad): fitting inked span against token length cancels the
bearings, and Nunito paints at **9.113 px/char against the table's
9.152 — 0.4%**. And the wrap is face-**blind**: the same threshold, 84,
for four families whose painted spans are 78 / 92 / 80 / 86. A wrap that
gives four visibly different faces one answer is not measuring any of
them, which is why my 8.000 px/char (0.5 em exactly) reads as synthetic
fallback metrics rather than as any shipped face.

### 6b.0b The two defects that are actually here, and who has them

**(i) Neither the render tier nor the product's export path awaits
`document.fonts.ready`.** `frontends/wysiwyg-grilling/src/App.tsx`
~1062-1115 calls `exportToBlob` directly on `restoreForRender(els)` —
and `restoreForRender` is what performs the re-wrap, so **the wrap and
the paint can resolve in different faces**. The client measures a string
once per page load and reuses it, so **each session freezes a coin
flip**: five fresh sessions gave 1, 2, 1, 1, 1 painted lines for one
scene, while twenty exports inside a single session were 20/20
identical. Cold — 12 of 13 sessions — the wrap reads the fallback. This
is upstream of every text measurement in this tier, mine included. **Not
mine**; the render tier is not this task's, and the App.tsx half is the
frontend's.

**CLOSED — and I logged it as unowned twice after it was already
routed.** The coordinator handed it to the client stream when I raised
it. Fixed at **`f1e5bc9`** (source) plus **`4730af0`** (bundle), which
shut both doors: the agent screenshot path and the user's own export. An
acceptance pin exists on `curator-fontrace`, **green on arrival with no
`expectedFailure` marker** — the fix was already on its base, so
red-by-intent would have been a lie; it proved the red by swapping the
pre-fix bundle back in instead. That is the same shape as the height
twin one section down, reached independently, and it is the honest
handling of a mutant born after its fix.

**A FOURTH INSTRUMENT FAILURE, and the one most worth carrying, because
it is about a test that was doing everything right.** A neighbouring
test exists *specifically* to catch this race and names it in its own
docstring — and it stayed green through every pre-fix run. The reason is
not laxity: **within a single session the broken bundle is perfectly
uniform, and uniform on the WRONG value**, because the client measures
each string once per load and reuses the result. So the test's stability
check passed, honestly, on a measurement that was wrong every time.

*Repeatability WITHIN a load and repeatability ACROSS loads are
different properties, and only the second can see a load-time race.* My
own twenty-exports-in-one-session result said exactly this and I read it
as reassurance rather than as the finding; it was the same shape as
reading four faces' identical thresholds as confirmation earlier the
same day. A determinism check is not a correctness check, and a defect
that is deterministic per session will pass every determinism check ever
written for it.

**(ii) `client_wrap_width` rounds the wrong way.** Python's `round` is
banker's; the client's `Math.round` is half-up. **Mine**, and taken.
Counted rather than characterised, because the characterisation that
reached me was wider than the truth:

* the diamond arm disagrees on **495 of the 1981 integer widths in
  20..2000** — and they are exactly `w ≡ 1 (mod 4)`, **not** "every odd
  width". Banker's and half-up agree whenever `floor(w/2)` is odd, so
  half of the odd widths are unaffected;
* **the ellipse arm has ZERO disagreements** over that whole range,
  because `w/2*sqrt(2)` essentially never lands on a half. The relayed
  "and on the ellipse arm wherever the term lands on a half" is true in
  principle and empty in practice;
* **no corpus container is affected**: of the widths in play today
  (60, 90, 100, 110, 130, 160, 200, 230, 240) not one is `4k+1`.

So it is real, deterministic, browser-free and currently unreachable
from the corpus — which is precisely why the mutant must pick an
affected width deliberately (e.g. **165**: the client gives 73, this
file gives 72). Red first from the curator, flip second, same shape as
`chopped_token_reads_as_one_line`.

**A judgement worth recording because it cost a deliverable:** a curator
declined to write the fonts mutant at all, on the grounds that a silence
expectation on a coin-flip oracle would encode a 1-in-5 outcome as
ground truth. **No mutant is better than a mutant that pins a race** —
the harness's own purpose turned against the harness.

### 6b.0c The rounding defect — closed, and the zero that nearly waived it

The one defect left standing after the padding, the shape arms and the
advance table were each measured and cleared. Two curator pairs
(`6947ea7`, `e87b4e6`), one fix (`4842672`).

**The defect is a transcription, not a model.** `Math.round` is half
toward +Infinity; Python's `round` is half to even. Every client rule in
`canvas.py` transcribes a `Math.round`, so `round` was wrong wherever the
halved extent lands on a tie — and only there. On a 181px rhombus the
client allows 81px and this file allowed 80.

**Five sites, not the two reported.** I re-read `_s`, `qg` and `yd` from
the bundle rather than taking the count: two diamond arms, two ellipse
arms, and `client_grown_extent`'s. The ellipse arms cannot reach a tie —
irrational for every rational width, zero disagreements — and are changed
anyway, through one `js_round` helper. **A rule typed at five sites where
only three can be wrong is how the fourth gets it back.** Both pairs flip
on that one line.

**Scope, counted rather than characterised** — three separate prose
characterisations of this defect were wider than the truth today,
including two of the coordinator's and one of the curator's own:
`w = 4k+1` **only**, because banker's and half-up agree whenever
`floor(w/2)` is odd. 495 of the 1981 integer widths in 20..2000.

**NOTHING EXECUTES A SENTENCE.** All three of those characterisations
were prose *about an encoding that was itself correct*: the catalogue
entry's condition said `4k+1` the whole time, while the sentences around
it said "every odd width", "both arms", "the height arm nobody looked
at". So the gap this task kept falling into is not between code and
reality — it is between **code and its description**, and it survives
every gate in the repo, because ruff, mypy, the suite and the mutants
all read the code and none of them reads the paragraph next to it. Every
live-value marker in this repo exists for one instance of this; the
general case has no guard and probably cannot have one. The only defence
observed working today was cheap and manual: **re-derive the number from
the encoding before repeating the sentence.** It caught the padding
retraction, the `4k+1` count, and the five-sites-not-two correction.

**The near-miss was tried and rejected, not considered.** Measured by
monkeypatching each candidate on the commit tree:

| rounding | cap mutant | headroom mutant | neighbours |
|---|---|---|---|
| `round` (the defect) | RED | RED | green |
| `ceil` (the near-miss) | silent | silent | green |
| `floor(x+0.5)` (the fix) | silent | silent | green |

`ceil` passes both pairs and is wrong. It agrees with `Math.round` on all
1981 integer widths and parts on 1710 of 3800 fractional ones, so the
pairs structurally cannot see it — a mutant's magnitude lives at a
boundary and a boundary cannot also be wide.

**THE ZERO THAT WOULD HAVE WAIVED THE FIX WAS TRUE, AND IRRELEVANT.**
This is the part that generalises past this fix. "The corpus has no
fractional container widths" holds — **0 of 291** — and is exactly the
evidence one would cite to skip the scene. It is the wrong evidence:
**the corpus is a survey of STORED state, and this defect lives in
IN-FLIGHT state.** Traced instead of reasoned about: an op carrying
`width: 180.4` reaches `client_wrap_width` **as 180.4** during
`apply_batch`, because the fitter runs before `normalize_element` snaps
the stored value to 180. `Math.round(90.2)` is 90 and `ceil` is 91 — one
pixel, on the agent's own write path, invisible to any corpus survey.

So the debt is paid rather than passed on:
`TestShapeAwareLabelRoom.test_the_cap_rounds_a_half_the_way_the_client_
rounds_it` sweeps tenths across 20.0..400.0 for both the cap and the
headroom against a hand-transcribed `Math.round`, and **fails on `round`,
fails on `ceil`, and passes on nothing else.**

**THREE KINDS OF ZERO are now catalogued by this task**, and they are
different failures wearing one number:

1. a zero over an **empty** population — the corpus carries no instance;
2. a zero over the **wrong** population — the fractional case above,
   where the survey measured stored state and the defect lives in flight;
3. a zero produced by the **default parameter** — the height twin, mute
   at fontSize 16 because `text_dims` quantizes to `20 * lines` and a
   one-pixel gap needs an odd height, while being numerically wrong at
   every other size the corpus uses.

None of the three is a false measurement. Each is a true count over a
population that is not the one the defect lives in.

### 6b.3 A NINTH — RETRACTED, see §6b.0. `BOUND_TEXT_PADDING` is wrong

Re-deriving the denominator in the browser, `'compliance'` refused to chop
at a cap my code computes as 75 while chromium paints it at 81px of ink.
That is impossible, so I stopped predicting the cap and **measured** it:
one word, three shape arms, 1px width steps, container at `opacity: 0` so
the only ink is the text.

| shape | narrowest width keeping the word whole | shape term | minus advance |
|---|---|---|---|
| rectangle | w=84 | `w` = 84 | **2** |
| diamond | w=167 | `round(w/2)` = 84 | **2** |
| ellipse | w=119 | `round(w/2*sqrt(2))` = 84 | **2** |

All three land on **exactly 84**. This **confirms the three shape arms by
measurement** — the first check on them that is not a reading of minified
source — and **falsifies the padding**: the client subtracts about 2 where
`client_wrap_width` subtracts 10, so every cap is ~8px too narrow.

**Root cause, and it is a family member of a rule this wave already
wrote.** The transcription took `Yn = 5` from byte 248725 and `_s` from
byte 555675 — **300KB apart in a minified bundle**, almost certainly
different module scopes, and nothing checked they were the same binding.
The wave's existing rule is *never pin a minified identifier, because the
minifier renames it*. This is its sibling: **never assume two minified
symbols from distant offsets share a scope.** Both are one error —
treating a build artefact's incidental structure as if it carried meaning.

**What caught it was reality declining to do something impossible.** I
would not have found it if the browser had not refused to chop a word my
arithmetic said it must.

**Consequence, re-derived in the browser over all 291 bound single-line
corpus labels** (each rendered in its own real container, painted lines
counted off the raster, no cap consulted in the verdict): TASK-TEXT-TRUTH's
headline *"14 of 14 are re-wrapped by the client"* is **8 of 291**, not
14. `client_wrap_width` predicts 14; the six it over-predicts are all
within 8px of the tight cap, which is the error itself. **The room rule is
still right in kind — the halving is confirmed — and its case is weaker
than stated.**

Not fixed by me, per the coordinator's ruling: **a curator writes the red
against the current constant and I flip it**, because I am the party that
just measured the area and a re-calibration routed by that party is what
burned this wave the same morning. Routed to `curator-boundpad` with both
rigs and an explicit request to re-derive rather than believe me. It is
confined to unfolded branches — `BOUND_TEXT_PADDING` has zero occurrences
on the folded tip — so nothing shipped with it.

### 6b.4 Numbers from §5 that are now withdrawn

* the 200x100 diamond row — **wrong**, chromium paints two lines;
* the chop positions in all three rows — **wrong**, server table;
* "0 of 291" — **survives**, but re-derived: the 24 tightest were rendered
  and none is chopped, and the rest sit at ≥66px of server margin, which
  under-states the true margin because the table reads wide;
* "tightest margin 16px" and "chopped on any diamond up to 182px" — both
  **server-side numbers**. The browser's threshold for `'compliance'` is
  **166px**, not 182. Neither figure should be quoted from §5.

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
