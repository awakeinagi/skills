# Evals

Exercises against the skill's load-bearing claims. Each one is a prompt a
naive user would arrive with, a thing to build, and **a fact that can be
checked** — an exit code, a message, a set of witnesses.

The standing rule, and the reason this file exists at all: **an eval that
asserts only the shape of an answer is itself a finding.** "The agent
mentioned the neighbour" is not a result. "Blinding the check left the
suite at exit 0" is. Every expected outcome below is something a terminal
prints.

## How to run one

Build a **throwaway** project — a scratch directory, never the suite you
depend on. The one used to produce the measurements below is two files
and about sixty lines:

- `app.py` — exports a list of order records to a CSV artifact. It
  carries one live defect: amounts are divided by 10 instead of 100, so
  every exported figure is 10x too large.
- `checks.py` — two checks over the *artifact*, not over the values that
  were about to be written. `null_in_required_column` reads the CSV back
  and reports the row index of any empty `order_id`; it works.
  `amount_scale` compares the export's total against a baseline it
  recomputes **from the export itself**, so its ratio is always 1.0 and
  it is permanently silent — the drift metric from the premise, in
  miniature. Findings carry `code`, `measured`, `expected`, `where`. A
  `CheckRun` carries `findings` and `errored`; `Silence(code).matches`
  refuses any run where `errored` is set.

That gives you a healthy check, a silent check, a live system defect, and
both poles to pair against. Blind a check with an environment variable
that swaps it for a stand-in returning `[]` — that is the stub sweep, the
mortality kill, and the re-earn, all with one lever.

Then follow the skill **literally**, as someone who has not built this
before. Where the instructions do not survive being followed, that is the
result; fix the skill, not the eval.

## Provenance of the recorded results

Every "Measured" line below was produced on **2026-08-17**, at commit
`b1b9b23`, on Linux with Python **3.12.3**, pytest **9.1.1**, Vitest
**4.1.10**, Jest **30.4.1**. They are observations scoped to that moment,
not standing facts — re-run them rather than citing them. A runner's
behaviour on this is exactly the kind of thing that changes in a major
version, which is the whole reason E1 exists.

There is no runnable harness in this directory, for the same reason the
skill bundles no scripts: it would have to be written in one language
against one runner, and would then be a claim of portability it could not
keep.

---

## E1 — The flip contract

**Prompt.** "I want to track known bugs as tests that are expected to
fail. Does my runner actually tell me when one starts passing?"

**Build.** One test marked expected-to-fail whose body passes on purpose.
Nothing else.

**Checkable fact.** The run **exits nonzero** and names the test as an
unexpected pass. A green run with a note is a failure of this eval, not a
pass — that runner gives you a to-do list that never checks itself off.

**Measured.**

| Runner | Marker | Output | Exit |
| --- | --- | --- | --- |
| unittest | `@unittest.expectedFailure` | `FAILED (unexpected successes=1)` | 1 |
| pytest | `@pytest.mark.xfail(strict=True)` | `FAILED … [XPASS(strict)]` | 1 |
| pytest | `@pytest.mark.xfail` (bare) | `1 xpassed` | **0** |
| Vitest | `test.fails` | `Error: Expect test to fail` | 1 |
| Jest | `it.failing` | `Failing test passed even though it was supposed to fail.` | 1 |

**What it catches.** The bare-`xfail` row is the trap: a suite that looks
like it has a flip alarm and does not. RSpec and Go were not re-measured
(no interpreter available here) and remain leads in SKILL.md's table.

---

## E2 — Red for the right reason

**Prompt.** "I marked my pin expected-to-fail and the suite is green.
That means the pin is catching the bug, right?"

**Build.** Two pins under the mask. One fails on a real assertion
(`assertAlmostEqual(10.0, 1.0)`). One crashes before the check is ever
consulted — a typo'd dict key raising `KeyError`.

**Checkable fact.** Run with `-v`. Both print the **same** words and the
run is green. If your runner distinguishes them, say so — that is a
finding in the skill's favour and the table should record it.

**Measured.** Both printed `... expected failure`, no traceback, no
message, even verbose. `OK (expected failures=2)`, exit 0.

**What it catches.** The claim in rule 1 — "most runners swallow errors
exactly like assertion failures" — holds verbatim for unittest, and it is
why a pin must be watched failing and read before it is marked, and why
the harness owes a red-by-assertion guard that re-runs pins with the mask
lifted.

---

## E3 — The two-pole rule, and what a neighbour does not close

**Prompt.** "I have a pin, and a neighbour beside it like the skill says.
Am I covered?"

**Build.** Four tests over the working check, all claiming to guard it: a
row-count assertion ("the pipeline is alive"), an existence assertion
(`assertTrue(findings)`), a value-bound assertion (`where == 2`), and a
silence neighbour. Plus the silence-shaped pin for the live `amount_scale`
defect and its own silence neighbour. Then blind one check at a time and
record which tests fail.

**Checkable fact.** Per check, the **list of tests that fail under the
blinding**. Any test that claims to guard a check and passes while it is
blind is vacuous; name it.

**Measured.**

- Blinding `null_in_required_column`: `FAILED (failures=2)`, exit 1. The
  value-bound and existence assertions both failed; **the row-count test
  passed** — the control that never runs through the entry point under
  test, exactly as rule 3 predicts.
- Blinding `amount_scale`: `OK (expected failures=1)`, **exit 0 — zero
  witnesses**. The masked pin stayed masked and its silence neighbour was
  satisfied by the corpse.

**What it catches.** The second measurement is the one that changed the
skill. A silence-shaped pin plus the silence neighbour rule 2 prescribes
for it leaves the dead-check hole wide open; only a firing-shaped ungated
companion closes it. SKILL.md rule 2 previously claimed the neighbour
"stops a crashed **or dead** check from hiding inside that mask", which
this run falsified for the dead half.

---

## E4 — `expect` binds what was measured

**Prompt.** "My test asserts the check reported a finding. Isn't that
proof it works?"

**Build.** A check that fires on the right artifact but names the **wrong
row** — off by one. Assert it two ways: existence, and by value.

**Checkable fact.** The existence assertion passes over the wrong answer;
the value-bound one fails and prints the mismatch.

**Measured.** `existence assertion PASSES: True`, `value-bound assertion
PASSES: False`, the finding reading `where=1` where the null sits at row
2.

**What it catches.** "You spoke, and you said the wrong thing" is
unreachable from an existence assertion. Same shape as the rate limiter
with the one-second `Retry-After`.

---

## E5 — A crash is not a silence

**Prompt.** "The check returned an empty list, so the artifact is clean."

**Build.** Replace a check with one that raises. Run it through two
helpers: the naive one that catches and returns `[]`, and the strict one
that records `errored` and hands back a `CheckRun`.

**Checkable fact.** The naive helper's result is indistinguishable from a
clean artifact; the strict `Silence` refuses to match.

**Measured.** Naive: returns `[]`, `assertEqual(findings, [])` **passes**.
Strict: `findings=[] errored=True`, `Silence(...).matches(run)` returns
**False**.

**What it catches.** The `Silence` snippet in `harness-design.md` run as
written. Note the asymmetry this exposes and E3 confirms: a strict
silence catches a *crashed* check and never a *dead* one.

---

## E6 — A seed that seeded nothing

**Prompt.** "My catalogue entry passes. The defect is covered."

**Build.** An operator that nulls the `order_id` of a row matched by id,
and an over-fire entry whose expectation is `Silence` on another check.
Run it against a present id and an absent one. Then add the digest
post-condition from `harness-design.md` — and run the snippet **exactly
as written**, with a shallow `copy`.

**Checkable fact.** Against the absent id, the entry passes with the
artifact unchanged. The guarded `seed` raises. And with a shallow copy,
what the guard does to a *legitimate* seed.

**Measured.**

```
target=A1 changed=True   over-fire entry (expect Silence) PASSES: True
target=X9 changed=False  over-fire entry (expect Silence) PASSES: True
guarded seed on absent id: ValueError: null_order_id produced no change
```

Shallow copy, legitimate seed of a present id:

```
base before: [{'order_id': 'A1', ...}, {'order_id': 'A2', ...}]
ValueError: null_order_id produced no change: nothing was seeded
base after:  [{'order_id': '',   ...}, {'order_id': 'A2', ...}]
```

**What it catches.** Two things. The no-op seed reads as a passing entry
— "the original silence in a new costume". And the shipped snippet's
`copy` was shallow: it raised on a seed that *did* change something,
while silently corrupting the known-good base for everything built on it
afterwards. Fixed to `deepcopy`.

---

## E7 — The re-earn

**Prompt.** "The fix landed, the pin went green, I dropped the marker.
Done?"

**Build.** A silence-shaped pin: "the export draws no `amount_scale`
finding". Red today (the export is 10x). Apply the fix, watch it flip.
Then blind the check and run it again. Then rewrite it to bind something
a dead check cannot produce — silence on the good artifact **plus** a
firing on a seeded one — and blind again.

**Checkable fact.** Three runs, three outcomes. Before the fix: FAIL, and
the message must be the value mismatch (`measured=10.0, expected=1.0`),
not a crash. After the fix: pass. After the fix, check blinded: **the
original pin still passes** and the rewritten one fails.

**Measured.** `AssertionError: Lists differ: [Finding(code='amount_scale',
measured=10.0, ...)] != []` → `OK` → with the check blinded, the
silence-shaped pin `ok` and the rewritten pin `FAIL: 0 != 1`.

**What it catches.** The structural hazard in rule 1, reproduced end to
end: while red the pin claimed "the check is silent here", which a dead
check satisfies and the flip alarm would catch; once green it claims "the
check is healthy here", which a dead check also satisfies and nothing
catches. One stub, one run, expect FAILED.

---

## E8 — Kill a check, and prove the kill bit

**Prompt.** "I killed the check and nothing failed. Nobody guards it."

**Build.** A kill that patches the check **in the registry** while one
test calls the module function directly. Run three ways: control, kill
with only the direct-path test selected, kill over the whole suite. Have
the blinded stand-in append one line per finding it destroyed — the drop
counter.

**Checkable fact.** The witness set (not the count) per run, and the drop
counter's value.

**Measured.**

| Run | Result | Witnesses | Drops |
| --- | --- | --- | --- |
| control, no kill | `OK`, exit 0 | — | — |
| kill, direct-path test only | `OK`, exit 0 | 0 | **0 (file never created)** |
| kill, whole suite | `FAILED (failures=1)`, exit 1 | 1 | 1 |

**What it catches.** The middle row is a fabricated clean row: it reads
"nobody notices this check going blind" when in truth the kill never
reached the path the test uses. Nothing in the suite output says so — the
drop counter reading zero is the only signal, which is why rule 6 makes
it an error rather than a row. The whole-suite row is also the honest
demonstration that witness *sets* beat witness counts.

---

## What an eval here must never do

- **Assert that the agent said the right words.** Every expected outcome
  above is a runner's output. If an eval can pass without anything being
  executed, delete it.
- **Run against a suite anyone depends on.** These blind checks, corrupt
  artifacts, and leave pins in odd states on purpose. Scratch directory,
  every time.
- **Carry a number forward.** The results above are dated and pinned to a
  commit and four tool versions. Re-run before citing.
