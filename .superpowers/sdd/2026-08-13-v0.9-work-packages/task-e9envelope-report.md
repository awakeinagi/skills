# TASK-E9ENVELOPE — the batch pipeline's envelope covers its whole surface

> **Follow-up landed after the report: see §9.** TASK-FRAMING measured
> that my two flips left SESSION-HANDOVER.md's durable-count sentence
> stale (16 / 3 / 0 against a live 14 / 3 / 0). I corrected it and
> **built the guard** that has been filed-not-built through seven
> staleness events.


**BASE `286a5cb`** (v0.9 Task 54). **LANDED `03ac73d`** on `v0.9_wps`,
on top of TASK-FRAMING's `627242d`, which arrived under me mid-landing
and is what step 5 of the landing method exists to catch. Their two
files (`tests/test_mutants_render.py`, `SESSION-HANDOVER.md`) are
disjoint from my four, so the move needed no rebase — but that was
knowable only after checking, which is the point of the step.

---

## 1. The escape inventory, before and after

RULE 6 discharged, and **the surface was wider a third time.** The
spike swept 41 batches and found 7 escapes across two functions. Batch
25 swept 56 and found 18 across four call sites. I swept **209** —
eight bad values (`dict`, `list`, `int`, `str`, NaN, inf, `bool`, empty
string) across every field of every op kind, on **both** surfaces — and
found this:

| | at `286a5cb` | after |
|---|---|---|
| raw escapes, `apply_batch` | **39** across 6 call sites | **0** |
| raw escapes, `check_batch` | **53** across 9 call sites | **0** |
| batches ACCEPTED storing unusable geometry | **21** | **0** |
| `--check` says `ok: True` for a batch that crashes or corrupts | **11** | **0** |
| batches applied clean (the control) | 45 | 22 |

The control row is the one to read sceptically: 23 batches that used to
be "applied" are now refused. Every one of them is a batch that stored
something broken or crashed later — the 21 stored-bad plus two that
applied only because `"" or 0` coerced an empty string to a coordinate.
No batch that stored a drawable scene stopped applying, which is what
the poles in `TestBatchPathIntegrity` hold down.

### Per call site, apply surface (39)

| site | phase | n | trigger |
|---|---|---|---|
| `_validate_batch:12098` | pre-scan | 2 | unhashable `resolve_pin` id |
| `_validate_batch:12125` | pre-scan | 2 | unhashable `pin` id |
| `_validate_batch:12196` | pre-scan | 6 | non-dict `customData` |
| `det_seed:257` | **commit** | 8 | non-str `id`, via `normalize_element` |
| `_round_geom:989` | **commit** | 16 | NaN/inf `x`/`y`/`width`/`height`, from `add` AND `mod attrs` |
| `headline_for:5900` | **commit** | 5 | non-str `question`, via `mechanical_summary` |

**Two sites the earlier measurements never named**: `headline_for`, and
the `mod attrs` half of `_round_geom` — the geometry escape has two
doors and only the `add` one had been swept.

**The structural finding, and it corrects the framing everyone
inherited:** all 39 escapes come from OUTSIDE the `try`, 10 from the
pre-scan and 29 from the commit. Zero come from inside `apply_ops`.
The E-9 arm was never broken and never leaked; the escapes were
evidence of where it *stopped*, not that it failed. Verified by
tracing each chain: `apply_batch -> commit -> normalize_element ->
det_seed`, and so on.

### Per call site, check surface (53)

The dry run was **worse than apply**, which was not expected and is the
finding with the most consequence. It shares `_validate_batch`'s 10,
and then adds faults from work `apply_batch` never does at all:

| site | n | why apply never hits it |
|---|---|---|
| `lint_layout:8936/8938/9299/9300/9870` | 22 | apply does not lint the scene it just built |
| `describe:7907` | 12 | reached through `intent_echo` on a would-be scene |
| `intent_echo:7958` | 5 | same |
| `bbox_pts:8104` | 2 | same |

So an agent that ran `--check` **to avoid** a crash was reaching for a
surface with more crash sites than the one it was avoiding. Worse
still, the 11 `ok: True` cases: the dry run does not reach `det_seed`
or `_round_geom`, so it cheerfully approved batches that die on apply,
including batch 25's `op 0 (pin): 42 targets n1`.

---

## 2. The envelope design

Two layers, because "convert the traceback" and "name the offending op"
are different contracts and only one of them can be met by an `except`.

**Layer 1 — `op_field_faults`, a per-op field gate in the pre-scan.**
Runs before `closed_here` (the comprehension that died on an unhashable
id), so it is the first thing after the existing shape gate. It follows
`index_fault`'s established shape exactly: a predicate returning a
phrase ready to follow `"op N (kind): "`. It checks `id` (string, one
rule for every op kind), `question` (string), `customData` (dict), the
five geometry fields and `points` (finite numbers) — on `add`'s
`element` and on `mod`'s `attrs`, which is the two-door case.

The `id` rule is the one worth pointing at: it closes a pre-scan fault
and a commit fault from one line, because identity is a string in every
consumer beneath — a dict key in each index the pipeline builds, and
`.encode()`d by `det_seed` for the render seed, eight call sites and
one phase later.

**Layer 2 — `internal_error`, one builder behind four arms.** The
pre-scan and the commit are wrapped at the CALL, in `apply_batch`,
deliberately rather than inside the method: that covers the pre-scan
end to end without anyone having to judge which of its lines can fault.
`apply_ops`' existing arm was rewritten to call the same builder, its
spelling unchanged to the character, so the four cannot drift into
saying different things about the same fault.

**Why `--check` agrees with apply is structural, not observed.** Both
surfaces decide through `_validate_batch`, so a field the gate refuses
is refused identically on both. The dry run then gets two arms of its
own — one around the shared validate call, one around the lint/echo
half that is its alone. The measurement backs it: 0 escapes, 0 `ok:
True` lies, on all 209.

---

## 3. "Nothing partial landed" — verified, not asserted

The brief said verify it or make it so. It was **not** true, and making
it so was a real change. `commit` mutates the registry and artifact
meta while the batch is still deciding — pin lifecycle, created-artifact
seed, registry ops — and r5-8 fixed that for the registry-op arm
**alone**. A fault anywhere else in that window kept the writes.

Fixed by taking a pre-image in `apply_batch` and restoring it on any
unpredicted fault. Injection at seven points, snapshotting registry,
artifact meta, scenes, records, save files, artifact files, and
`model.json` on disk:

| injected at | window | state after | message |
|---|---|---|---|
| `_seed_created_meta` | pre-persist | **identical** | nothing partial landed |
| `_apply_registry_ops` | pre-persist | **identical** | nothing partial landed |
| `mechanical_summary` | pre-persist | **identical** | nothing partial landed |
| `_check_tripwires` | pre-persist | **identical** | nothing partial landed |
| `write_json` | PERSIST | moved: registry | only partly written |
| `_write_artifact` | PERSIST | moved: registry, records, saves | only partly written |
| `_save_registry` | PERSIST | moved: registry, scenes, records, saves, artifacts | only partly written |

So the claim is true for every fault before the first write and false
for every fault after it. Rather than let the persist block inherit a
promise it cannot keep, it raises its own error naming the revision and
telling the agent to re-read the project. Rolling memory back there
would only add a second disagreement to the first.

**Cost.** The pre-image is an unconditional `copy.deepcopy` of the
registry and meta on every apply. Measured on the largest shipped
project (`argus-r5`, 70 KB registry, 22 concepts / 14 pins, 6
artifacts, 23 records): **1.4 ms against a 128 ms apply — 1.1%.**

---

## 4. The D-2 ruling, and a correction to the pin that asked for it

**RULING: refuse, at the validator, for all of it.** Recorded in the
red's own docstring so the reasoning travels with the test.

**First, the pin's account of the asymmetry was wrong, and I measured
it before fixing anything.** Both the spike and batch 25 state that the
two behaviours live inside ONE function — `_round_geom` raising on a
NaN `width` while coercing a NaN inside `points` to 0. Measured:
`_round_geom(float("nan"))` **raises**, on every field including the
coordinates inside `points`. The NaN in `points` never reaches it.
Traced live with `sys.settrace`: it is dropped in **`fan_attach_points`**
(canvas.py:3474), which rebuilds an arrow's polyline from its endpoints
for reasons that have nothing to do with validation, and takes the NaN
out as a side effect. `make_element` still returns `[[nan, nan], [1,
1]]`; `normalize_element` is handed `[[0, 0], [1, 1]]`.

That makes the case for refusing **stronger** than the version in the
file. The two behaviours were never one function's decision about bad
input — they were two unrelated functions that happened to be handed
it. There was no policy to preserve.

Why refuse rather than coerce:

1. **No honest value exists.** NaN `x` coerced to 0 puts the element on
   the origin, on top of whatever is there; NaN `width` to 0 makes it
   invisible. Both are wrong pictures told confidently, which is the
   failure this whole campaign is about.
2. **Neither existing behaviour was a considered policy.** One is
   `int(round(nan))` raising by accident of the stdlib; the other is an
   arrow repair pass. Elevating either would dress an accident as a
   decision.
3. **Only a refusal can name the op.** `_round_geom` is handed one
   scalar — no op index, no field name, no artifact. SKILL.md's
   contract is not expressible there.

`_round_geom` is left exactly as it was. After the gate no batch-borne
NaN reaches it, and it still guards paths that do not come through a
batch.

---

## 5. Both flips, with the partial-state evidence

Markers off in the fix commit; **no assertion weakened** — the D-1 red
gained no new tolerance and the D-2 predicate is untouched.

### The per-site evidence (the brief's ask)

The red's four cases are one per call site by design, so the fix was
staged one gate rule at a time and the suite run at each state:

| state | failing subTests | which flipped |
|---|---|---|
| gate off, envelopes only | **11 of 11** | none |
| + `id` rule | 7 | both pin-id cases, apply and check (4) |
| + `customData` rule | 9 | that case, both surfaces (2) |
| + geometry rule | 6 | `width` NaN (2) and all three D-2 storage cases |
| + `id` and `customData` | 5 | the two above, additively |
| **full gate** | **0** | — |

The rules are disjoint and additive — 4 + 2 + 5 = 11, and the
`id`+`customData` state fails exactly the 5 the geometry rule owns. The
four sites are independently covered, not jointly closed by one lucky
change.

**The `gate off` row is the load-bearing one.** With the envelopes in
and the gate out, all four faults DO come back as `BatchError` on both
surfaces — and every subTest still fails. An envelope can say `internal
error … — TypeError` without being able to say which op, and the dry
run still answered `ok: True` for the non-string pin id. Converting the
traceback was never the whole contract; naming the op was. That is why
the red asserts `"op 0"` and `ok is False` rather than an exception
type, and it is the clearest vindication of how the curator wrote it.

### The mutation check on the fix

Seven independent breakages, each caught by exactly the guard that owns
it, quiet pole green throughout:

| mutant | caught by |
|---|---|
| M1 pre-scan arm removed | the pre-scan pole (FAIL) |
| M2 commit arm removed | the commit pole **and** `test_a_crashing_registry_op_leaves_nothing_behind` |
| M3 commit restore removed, envelope kept | the commit pole (FAIL) |
| M4 persist arm inherits the default sentence | the persist pole (FAIL) |
| M5 dry-run read-back arm removed | the read-back pole (ERROR) |
| M6 dry-run validate arm removed | the pre-scan pole's check half (ERROR) |
| M7 field gate removed | both reds, 11 subTests |

**M3 survived the first draft**, and that is worth recording. The
commit pole's carrier was a bare `add`, which mutates *nothing* in the
commit window — no pin lifecycle, no created meta, no registry op — so
deleting the restore it exists to guard left the class green. The
carrier gained a `set_round` registry op, which writes early in the
window, and M3 now fails. A pole that cannot fail is the dead-detector
shape this repo keeps finding; it was found here by the mutation check
and not by reading.

### Four firing poles added

The reds now prove the **gate**, and say nothing about the backstop
behind it — the gate refuses their four inputs before any envelope
runs. That would have left four `except Exception` blocks nothing
evaluates, which is precisely the shape the E-9 spike was written
about. So each new arm got a pole, injecting into a different leaf so
no single over-broad `try` satisfies them all: `role_of` (pre-scan),
`_check_tripwires` (commit window), `write_json` (persist),
`project_lint` (the dry run's own half). Plus one quiet pole asserting
the shared carrier lands with both its effects.

`TestPinIdentityIntegrity`'s existing pole covers the fourth arm
(`apply_ops`) and is **untouched and green** — it proves the wiring;
these prove the width.

---

## 6. SKILL.md

The sentence was `Errors name the offending op — fix and resend;
nothing partial ever lands.` It is now true for every *predicted*
fault, and there are two residual cases, so it got the honest
qualifier rather than being left to overclaim:

- An `internal error` message names the **exception**, not an op —
  the backstop cannot know which op a fault arose on.
- A fault during persistence leaves a revision partly written.

Both are now self-describing: the message says which case it is and
what the store holds. The added clause says exactly that.

---

## 7. Verification, at the landed tip

All measured at `03ac73d` in the shared tree, **after** TASK-FRAMING's
`627242d` — not carried over from my pre-merge runs.

| | result |
|---|---|
| default suite | **1121, OK, ef=17** (38 skipped) |
| gated render tier (`MUTANTS_RENDER=1`) | **1121, OK, ef=17** |
| catalogue (`mutants run --all`) | **91, OK, ef=5** |
| `mutants coverage` | **0 UNCOVERED** |
| `mutants sweep` | **exit 0** |
| `pre-commit run --all-files` | **all 15 hooks pass** |
| default suite **on the 3.9 floor** (`uv run --python 3.9`) | **1121, OK, ef=17** |

That last row is not routine and is worth keeping. `aeac121` landed
between my commit and this report because `tests/test_backend.py` had
stopped importing on the declared 3.9 floor and nothing noticed —
pre-commit runs bare `python3`, which is 3.12 here. So I ran my own
work at the floor rather than assuming: `canvas.py` imports, the three
new predicates return the right phrases, and the whole suite passes on
3.9.25. The closeout chore for a standing floor run still stands; this
discharges it for this commit only.

**The red count, reconciled — and my own commit message states it from
the wrong side.** That message says `ef 21 -> 19`, which is my delta
measured from MY base, and the absolute figure is wrong at my tip
because TASK-FRAMING landed in between. Derived from the decorators
rather than counted by hand:

```
git grep -c '^\s*@unittest\.expectedFailure\s*$' <commit> -- tests/
  286a5cb (my BASE)          21   = 16 test_mutants + 5 test_mutants_render
  627242d (TASK-FRAMING)     19   = 16 + 3   <- their two flips
  03ac73d (this task)        17   = 14 + 3   <- my two flips
```

There is **no disagreement** between the two reports: 21 -> 19 -> 17,
two flips each, in different files. Both figures are right once the
commit each was taken at is named — which is batch 25's warning about
how a census row gets "corrected" into being wrong, arriving on
schedule. The tip is **17**. The commit message is not amended: the
guide forbids rewriting a shared tip, and a report is the right place
for the correction.

## 8. Concerns

1. **I touched a file outside my named surfaces.**
   `tests/test_failure_paths.py`'s
   `test_a_crashing_registry_op_leaves_nothing_behind` expected a raw
   `RuntimeError` to propagate out of `apply_batch` — it was pinning
   the ABSENCE of an envelope on a path whose subject was never the
   exception type (its own docstring says the guard cannot ask what
   raised). It now expects the `BatchError`, and I **added** assertions
   rather than relaxing any: the message names `RuntimeError` verbatim,
   `__cause__` is the original exception, and all four state assertions
   are unchanged. Strictly stronger. Not TASK-FRAMING's file, so no
   collision — but a reviewer should confirm the tightening reading.

2. **The `_round_geom` asymmetry survives outside the batch path.**
   My gate is on the batch pipeline. A NaN arriving through `/api/save`
   (a client post) still meets `_round_geom` raising and
   `fan_attach_points` coercing. The ruling above says what SHOULD
   happen there; enforcing it is a different entry point and a
   different owner. Worth a curator pin if anyone wants it held down —
   I did not file one, because filing a pin against my own fix is the
   run-5 shape.

3. **`bool` geometry is now refused where it used to be coerced.**
   `{"x": true}` was silently read as x=1; it is now an error naming
   the op. I judged this right on `index_fault`'s stated precedent
   (`bool` is an `int` subclass and a JSON `true` is not a position),
   and no test moved. It is nonetheless a behaviour change nobody
   asked for, so it is called out rather than buried in the diff.

4. **The dry run's extra crash sites are closed by input validation,
   not by making the lint total.** `lint_layout` and `describe` still
   assume numeric geometry; they are simply no longer handed anything
   else through a batch. A scene that became malformed by some other
   route — a hand-edited artifact file, a load-heal miss — would crash
   them again, and the dry-run envelope would convert it to `ok:
   False` rather than fix it. That is the right layering, but it means
   "the lint is total" is NOT what this task proved.

5. **The catalogue's prose counts are now two commits staler, and they
   must be RE-DERIVED, not adjusted.** I updated
   `HAND_AUTHORED_RED_CLASSES` (`TestBatchPathIntegrity` 3 -> 1) and
   its guard passes. But the long prose paragraph near
   `CATALOGUE_RED_IDS` narrates suite totals in sentences nothing
   derives, and two tasks flipped reds in the same window — so anyone
   patching it from either report alone will write a wrong number.
   The derivation and the three-commit reconciliation are in §7. Per
   the derive-don't-count instruction I did not hand-edit that
   paragraph.

6. **`SESSION-HANDOVER.md` is TASK-FRAMING's in this window.** They
   committed it in `627242d`; I did not touch it, so its render row
   reflects their work and knows nothing of my two flips or the five
   tests I added. Whoever next reconciles it should re-derive both
   rows rather than add my numbers to theirs.

---

## 9. Follow-up — the durable-count sentence gets a guard

Routed by TASK-FRAMING within minutes of my fix landing, measured not
inferred, and correct: my two flips moved
`SESSION-HANDOVER.md`'s "durable form of the counts" sentence and I had
not carried it. It read **16 / 3 / 0**; the tree greps **14 / 3 / 0**.

I corrected the sentence **and built the guard**, because the recurrence
record is the argument for not just carrying a number again:

- **Seventh staleness of this one paragraph.** The sixth was fixed BY
  HAND in TASK-FRAMING's `627242d`, after standing wrong-by-four since
  tasks 51/52/54. My `03ac73d` — the very next commit to touch a red —
  falsified it again. A hand copy that cannot survive one commit is not
  a transcription problem, it is a missing assertion.
- **Three guards stood green beside it through all seven**, correctly:
  they read the model row of ids, the coverage sentence, and the
  class/count map, and none of them opens this paragraph. The distance
  rule again — a guard proves the property it evaluates and no part of
  the property standing next to it.
- **The sentence prints its own derivation**, one line above the
  numbers. Everything needed to keep it honest was on the page for six
  of the seven events. Being cheap to derive is not being derived.

`TestCoverage.test_the_handover_transcribes_the_durable_red_counts`,
plus `durable_red_counts()` / `handover_durable_counts()` beside the two
existing transcription helpers in `tests/test_mutants.py` — my surface,
which is why I built it rather than filing it on.

**Watched failing before the numbers were corrected**, per rule 8:
`(16, 3, 0) != (14, 3, 0)`, by assertion, not by error. Stub-checked
both directions: blinding the derivation regex leaves it FAILING (not a
dead guard reporting agreement over an empty read), and rewording the
sentence past its anchor raises an `AssertionError` naming the file
rather than silently returning nothing.

**Design choice worth stating, since it could reasonably go the other
way.** It matches the GREP, not the runner. Those are different numbers
— the grep counts decorator lines in the source, a runner drops a
decorated method whose class is gated or skipped — and the handover's
sentence is explicitly about the first, because it prints that grep.
Reading it the other way would fail honestly-written prose.

**One attribution corrected on the way through.** TASK-FRAMING credited
my commit with fixing `tests/test_backend.py`'s Python 3.9 import. It
did not — `03ac73d` does not touch that file. `aeac121` does, and its
own message credits TASK-FRAMING with finding it. I only verified
downstream: the full suite passes under `uv run --python 3.9`. Worth
recording because a plausible name copied without deriving it is the
same failure mode as the count this section is about.

### Re-verified at the follow-up tip

| | result |
|---|---|
| default suite | **1122, OK, ef=17** (38 skipped) |
| gated render tier | **1122, OK, ef=17** |
| **on the 3.9 floor** | **1122, OK, ef=17** |
| `pre-commit run --all-files` | **all 15 hooks pass** |

1122 rather than 1121 is the new guard. `ef` is unchanged: a guard is
not a red.
