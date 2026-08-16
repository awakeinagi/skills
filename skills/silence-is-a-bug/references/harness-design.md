# Harness design

Build notes for the machinery itself. The snippets are pseudo-Python
because something had to be picked; the shape is the point, and every
construct has an ordinary equivalent elsewhere. Where a language changes
the answer rather than the syntax — compiled languages have no
monkeypatching, no runtime enumeration of unreferenced symbols — that is
called out at the place it bites.

The vocabulary (pin, finding, neighbour, masked, witness, mortality) is
defined in the glossary at the end of `../SKILL.md`.

Contents:

- [The catalogue](#the-catalogue)
- [The seeding layer](#the-seeding-layer)
- [The harness's own guards](#the-harnesss-own-guards)
- [The coverage ledger](#the-coverage-ledger)
- [Discovery sweeps](#discovery-sweeps)
- [Mortality](#mortality)
- [Tiering](#tiering)
- [The command surface](#the-command-surface)

## The catalogue

One record per seeded defect, with the loader enforcing the parts rather
than a convention asking for them:

```python
@dataclass(frozen=True)
class Pin:
    id: str
    build: Callable[[], Artifact]   # a known-good artifact
    op: str                         # the operator that injects the defect
    args: dict                      # exactly one defect's worth
    expect: FindingSpec | Silence   # what the check must say
    neighbour: Neighbour            # the other pole, ungated

    def __post_init__(self):
        if self.neighbour is None:
            raise ValueError(f"{self.id}: a pin without a neighbour "
                             "proves a check fires, not that it discriminates")
```

Enforce at load, not at review:

- **No neighbour, no load.** The neighbour is the differential control;
  without it, an over-eager check and a working check look the same.
- **No duplicate ids.** Two people adding a pin for the same defect
  under the same name should collide loudly. This is a real detection
  layer under concurrency — do not defeat it with per-author prefixes.
- **`expect` binds a value or an identity, never mere existence.**
  Reject a `FindingSpec` that carries no measured value and no identity
  fields; "it emitted something" is not a specification.

The neighbour is built and run **as-is** — the operator is never applied
to it. That is the point: it is the legitimate look-alike.

### Silence must be strict

```python
class Silence:
    def matches(self, run: CheckRun) -> bool:
        if run.errored:      # a crash is not silence
            return False
        return not run.findings_for(self.code)
```

A check that raises reports a distinct `check-error` state, and
`Silence` refuses to match over any run where a check crashed. Without
this, "the check looked and found nothing" and "the check exploded on
line 3" are the same green.

This needs a place to live. Whatever runs your checks — a function, a
harness method, a test helper — should catch what the check throws
(exception, panic, error return) and return a result object carrying
both the findings and the error state, rather than letting it propagate
into the test's own failure. A crashed check that fails the test looks
like a caught defect.

## The seeding layer

An operator takes a known-good artifact and returns it with exactly one
thing changed: expire a token, null a column, swap two nodes, drop an
edge, reorder two statements.

**An operator that did not change anything must fail loudly.** An
operator that filters by an id no longer present no-ops silently, and a
"mutated" artifact that was never mutated is the original silence in a
new costume — the pin then proves the check stays quiet on a healthy
artifact, and reads as a passing test.

```python
def seed(op, artifact, **args):
    out = OPERATORS[op](copy(artifact), **args)
    if digest(out) == digest(artifact):
        raise ValueError(f"{op} produced no change: nothing was seeded")
    return out
```

## The harness's own guards

The harness is a check, so it needs checks. Each of these is one test
that fails loudly:

- **Red-by-assertion.** Re-run every expected-to-fail pin at engine
  level with the mask lifted, and fail on two states: it raised before
  the check was consulted ("error-red" — the pin is broken, not the
  code), or it passed ("secretly green — flip it"). Without this, a pin
  with a typo in its fixture is indistinguishable from a pin catching a
  real defect.
- **Every silence has a firing beside it.** Walk the catalogue; for each
  pin whose `expect` is a `Silence`, require some other entry proving
  that same check fires. A catalogue of nothing but absences is a
  catalogue that a fully dead check would satisfy.
- **Semantic dedupe.** Same defect registered under two ids. Say in the
  guard's own comment which duplicate classes it cannot see; a guard
  that overstates its reach is worse than no guard.
- **Staleness of any committed record.** The sweep record, the ledger,
  any enumerated pointer comment: a test that recomputes and compares.

## The coverage ledger

One row per finding code, three states, gate test on the fourth:

```
code                     state       proof / reason
auth_denied              proven      pin: expired_token_still_authorizes
latency_budget           deferred    expensive tier, gated behind PERF=1
schema_drift             UNPROVEN    needs a two-version fixture; ~1 day
```

`deferred` means the proof exists but is env-gated. `UNPROVEN` needs a
real reason — the reason is what lets a reader decide whether to fix it
today.

Pair it with the **registry gate**: derive the set of codes from the
source of truth (the registry, a reflective scan of the source, the
decorator table) and fail when it disagrees with the ledger's keys.
Derive it — never maintain the count by hand. A hand-maintained count in
a comment goes stale twice before anyone notices, and the second time it
gets "corrected" to another wrong number.

## Discovery sweeps

Verify mode re-finds known defects. Discovery asserts the inverse: a
meaningful input change must move the checks' output.

```
for each operator, for each target in the corpus:
    seeded = seed(operator, artifact, target)
    before, after = check(artifact), check(seeded)
    if before == after:  survivor(operator, target)
```

Every survivor gets a disposition recorded with a reason:

- **promote** — worth a curated pin, and usually a new check.
- **allow** — legitimate invariance. Write down why, so the judgement is
  never re-made by the next reader.
- **bug** — the operator or the equality predicate is wrong. Fix it and
  re-sweep; do not leave the entry.

Undispositioned survivors set the exit code. The report may be noisy
once; a permanently noisy report is one everybody stops reading, which
is the same as not having it.

Commit the sweep record and test that it stays byte-identical unless a
sweep deliberately regenerates it. A record that never drifts across a
long arc of work is the quiet proof that the machinery itself never
drifted.

## Mortality

**The question:** if this check went blind today, would anything fail?

**The method:** copy the tree, patch exactly one check to report
nothing, run the real suites against the corpse, record which tests fail
(the *witnesses*). Repeat per check. Compare against a control run where
the instrumentation is installed but no check is killed.

**"Patch" depends on your language.** In a dynamic one you wrap or
monkeypatch in the copied tree. In a compiled one you have two honest
options: put the checks behind an interface and swap the implementation
under a build tag, or apply a source patch to the tree copy before
building — slower, but it needs no production change. Whichever you
pick, the kill must reach every path the tests exercise; see the
inertness and drop-counter witnesses below for why that is not something
to assume.

A **witness**, precisely: a test that fails, errors, or turns unexpected
pass under the kill and did **not** under the control run. The control
run is green, so the subtraction is a no-op by construction — write it
anyway, because the day it stops being a no-op is the day a row would
otherwise be fabricated.

### Three witnesses in front of every kill

Rule 3 applies to this harness too, and it is not theoretical: a kill
implementation that silently does not take effect produces a full table
of clean-looking rows. Two independent ways that happens, both observed:
a wrapper that breaks source reflection (so a reflective test fails for
the wrong reason and gets counted as a witness), and a kill applied on
one entry path while the tests exercise another — the library path
blinded, the command-line path untouched — which surfaces as a slow,
inexplicable run rather than as an error.

- **Inertness.** Install the instrumentation *unconditionally* — every
  wrapper in place on the control run too, passing arguments straight
  through — and require the suite to be green with the same test count
  as the unpatched tree. Raise rather than measure when it is not: a
  non-inert patched tree invalidates every row after it. This catches
  the reflection-breaking wrapper in a place where it cannot be mistaken
  for a check's death. If your wrapping mechanism can disturb reflection
  (Python decorators without `functools.wraps`, proxies that hide the
  underlying type), preserve identity — and let inertness prove you did,
  rather than assuming it.
- **The drop counter.** Count the findings each kill actually destroyed
  during the run. A kill that destroyed nothing is an **error**, never a
  "nobody noticed" row. Persist the count on disk, one record per
  destroyed finding. Do this unconditionally: most runners fork per
  package or per worker, plenty of suites shell out, and an in-memory
  counter then reads zero for the wrong reason — while a file already
  appended survives a worker killed by signal.
- **Surgicality.** Run the catalogue's own artifacts through the checks
  and require the killed check to stop answering while every other check
  answers exactly what it did before. Measure "stopped answering" by
  **value**, not by count: a check that emits one summary record per run
  keeps its count under blinding and changes only the number inside,
  which a count-only probe reads as a kill that never bit. Checks that
  cannot be exercised without the expensive tier rest on inertness and
  the drop counter alone — say so in the class's own docstring rather
  than leaving it to be discovered.

### Reading the results honestly

**Witness counts are not comparable across a commit that flips a
silence-shaped pin.** Such a pin turns unexpected-pass when the check is
killed, and unexpected-pass is correctly counted as a witness (a pin
going green *is* a death noticed). Flipping it therefore removes a
witness with zero change to the check, and the drop reads exactly like a
lost test. The converse pin does not do this at all: a firing-shaped pin
stays failing under the kill and contributes nothing either way. So "the
correction equals the number of pins" is wrong in both directions — only
silence-shaped pins can contribute, and each must be checked rather than
counted.

Better: **never count pins.** Read the list of unexpected passes the
driver already reports for the run in front of you. A pin count in prose
is a measurement with a date on it, and the recurring failure mode is
not inventing the number — it is carrying a real number forward into a
sentence about a different commit.

**Expensive-tier witness counts carry noise that is not the check's.**
Measured directly by running each kill twice on the same copy and the
same machine: the kills themselves were deterministic (identical drop
counts every time), and witness *sets* reproduced exactly for most
checks — but a flaky test appeared under two unrelated kills and was a
witness to neither. It passed the control run, so subtracting the
control did not catch it. Read an expensive-tier witness count as "about
this many", and **diff the witness sets rather than the counts** before
believing a movement. Have the driver report ids, not just totals.

### What it cannot prove

**A noticed death is not a good control.** Mortality works at check
level: "does this check's death cost anything anywhere". It says nothing
about any *individual* test. A check whose death fails eight tests can
still have a ninth that claims to guard it and does not — the check
survives because some other test noticed, and that ninth test is
decoration. Expect a check-level table that is spotlessly clean to sit
on top of a substantial pile of vacuous pins.

Three different questions, three different instruments:

| Question | Instrument |
| --- | --- |
| Is this check proven anywhere? | the coverage ledger |
| Does every silence have a firing beside it? | the catalogue guard |
| Does this check's death cost anything? | mortality |
| Does **this** assertion mean what its docstring says? | the stub sweep |

### Cost

One tree copy, then **one full suite run per check**. That is the whole
cost model: multiply your suite's wall time by your check count before
scheduling it, and re-multiply whenever either grows.

Fan the kills over workers and give each worker its own copy of
everything a test can observe. A temp directory is the easy half; a
shared database is the hard half, and sharing one silently correlates
your rows — give each worker its own schema, its own container, or its
own database, and prove the isolation before trusting a single result.

Run it at a phase gate rather than per commit, and exclude it from the
default run by whatever selection mechanism your toolchain offers (a
build tag, a test category, a name outside the discovery pattern, a
separate suite) so the everyday run pays nothing for it.

## Tiering

Split checks by what they can see. The cheap tier reads stored state and
runs on every commit; the expensive tier reads the real artifact.

- **Gate the expensive tier behind an explicit flag**, and make the
  gated-**on** path raise when its dependency is missing. "Browser not
  found, skipping" is how a whole tier goes dark for a quarter while the
  dashboard stays green. An environmental failure must never mark checks
  green.
- **Ablation is a useful technique where the artifact is producible on
  demand.** Produce the artifact, produce it again with exactly one
  element removed, and diff. An element whose removal changes nothing
  contributed nothing — it is invisible, unreached, or dead. Remove one
  middleware and diff the response envelope; remove one validation rule
  and diff the quarantine set; remove one element and diff the rendered
  page. Give the diff a comparator tolerant of the noise your medium
  generates by construction — timestamps and request ids for a response
  body, a one-pixel dilation for an anti-aliased raster — so you are not
  tuning a threshold per test. If your artifact cannot be re-produced
  cheaply, skip this and pin behaviour directly; ablation is a
  convenience, not a requirement.
- **A tier is only as trustworthy as the surface it looks through.** If
  the renderer, exporter, or client has its own known defect, a check
  pointed at it inherits that defect and reports confidence. Pin
  equivalence between any second path and the primary, and state the
  ceiling: when path B consumes path A's output, agreement between them
  cannot clear a defect they both inherit. The genuinely independent
  path is the one worth wiring, and until it exists, say so.
- **Assert what is build-robust.** Geometry, identities, magnitudes and
  *relative* steps travel; absolute environment-sensitive thresholds do
  not. Measured across environments spanning twenty major versions of
  one renderer, geometric magnitudes came back identical while rendered
  contrast on small text straddled the accessibility threshold in both
  directions. Put an absolute environment-sensitive threshold only on a
  pole where every known environment clears it with margin — and when
  calibrating, prove stability across environments, not just across
  runs.

## The command surface

Wrap the harness in a small command-line surface, and talk to it through
that rather than importing its internals. The catalogue's shape will
change; the command surface should not.

| Command | What it does |
| --- | --- |
| `list [--check CODE] [--red]` | The catalogue: id, state, operator, both poles. |
| `run <id>` | Run one pin **and its neighbour**; judge each against what the catalogue declared. |
| `run --all [--tier ...]` | The whole tier, runner output passed through. |
| `coverage` | The ledger, verbatim. |
| `sweep` | Discovery; exit code = undispositioned survivors. |
| `disposition <id>` | That survivor's verdict, or the entry to add. |
| `new <slug> --check CODE` | Emit a paste-ready scaffold. |
| `seed <op> --artifact F -o OUT` | Apply one operator to one artifact. |

Two behaviours are worth building deliberately:

- **`run` prints the evidence behind every red, always.** Most runners
  report an assertion failure and an unrelated `KeyError` identically
  under an expected-failure mark — same text, no traceback, even in
  verbose mode. Re-run each red with the mask lifted, print the message,
  classify it as red-by-assertion or error-red, and exit nonzero on
  error-red.
- **`new` does not edit the catalogue.** It emits the complete block —
  builders, registration, both test methods — with unfilled markers, and
  names the places to paste it. The catalogue is code, and code is
  edited by whoever is accountable for it.
