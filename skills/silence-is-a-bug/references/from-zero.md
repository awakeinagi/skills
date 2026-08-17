# Starting from zero

For a project with no harness — or with a normal test suite and none of
the machinery in SKILL.md. Each step buys one specific thing and is
useful on its own; stop wherever the value stops.

The order matters. Every step after the first exists because the step
before it turned out not to be enough, and building them in the reverse
order produces an elaborate harness pointed at nothing.

## Step 0: confirm your runner has a flip alarm

The whole method rests on one property: **a test marked expected-to-fail
that starts passing must fail the run.** If it merely skips, or reports
a note nobody reads, a pin becomes a to-do item that never checks itself
off — which is the exact thing you were trying to escape.

Test it before you trust it, in ten lines. Two of the confirmed runners
below, since the marker is never the same twice:

```python
# pytest -- strict is the whole point; bare xfail exits 0 on a pass
@pytest.mark.xfail(strict=True)
def test_flip_alarm_works():
    assert True          # passes on purpose

# stdlib unittest -- no strictness knob; expectedFailure is already strict
class Pins(unittest.TestCase):
    @unittest.expectedFailure
    def test_flip_alarm_works(self):
        self.assertTrue(True)
```

Run it. If the suite goes red, you have a flip alarm — pytest prints
`[XPASS(strict)]`, unittest prints `FAILED (unexpected successes=1)`,
both exit nonzero. If it goes green with a note (`1 xpassed`, exit 0),
you do not, and the note will be invisible inside a thousand-line CI
log.

**If your runner has no native mechanism** (Go's `testing` and JUnit
among them), build the smallest one that fails the run. Three parts:

1. **A registry** — a file listing each pin's test name and a one-line
   reason it is expected to fail. Plain text or JSON; something a
   script can read.
2. **Exclusion from the normal run**, by whatever selection mechanism
   your toolchain has: a build tag (`//go:build pinned`), a tag or
   category annotation, a separate package or suite, a name pattern the
   default selection excludes. Pick one and write it down — the three
   parts have to agree on it.
3. **An inverting gate step** that runs the pinned selection **in a
   separate process** and fails if any pin passed.

The separate process matters, and it is the part people get wrong.
In-process inversion is unavailable in most runners without native
support: in Go, a failing subtest marks its parent failed and there is
no API to clear that, so a `TestPins` that "inverts the result" reddens
the suite for exactly the pins that are behaving correctly. Do the
inversion where you can see a process's exit status or its
machine-readable output instead — `go test -tags pinned -json` parsed
for pins that reported `pass`, a shell wrapper inverting `$?`, a Maven
profile whose failure condition is negated. The gate then reports, per
pin: "still failing (expected)" or "PASSED — the defect is fixed; drop
its registry entry and move the test into the normal run."

Wire that gate into the same CI job that runs your suite, and print the
outstanding-pin count in its output. A pin count nobody sees is the
to-do list that never checks itself off, one level up.

This is a small amount of code, written once — but measure it in your
own stack before quoting a size to anyone; a number from someone else's
toolchain is not evidence about yours.

Whichever route you take, prove the alarm bites the day you build it:
make a pin pass, watch the gate go red, restore. A flip alarm nobody has
watched fire is itself an unverified check.

## Step 1: pin the last bug that got past you

Not a hypothetical. Take the most recent defect a user or a colleague
found that your checks did not — the incident with a postmortem, the
Slack thread, the "how did this ship". Write the test that describes
correct behavior, watch it fail, read the failure message, confirm it is
the mismatch you predicted, and only then mark it expected-to-fail.

Do not fix it in the same session. If you are the only person on the
project you will fix it yourself eventually, and that is fine — the
separation you are preserving is in *time*, not in headcount. A pin
written before the fix is written against the requirement; a test
written after the fix is written against the fix.

Done when: the suite is green, prints one outstanding pin, and someone
who reads only the test name knows what is broken.

## Step 2: write the first check

Most existing systems have validation without having *checks* in this
sense: middleware, struct tags, database constraints, assertions inside
tests. Those enforce; they do not report findings you can enumerate,
prove, or ledger. So the first check is a new, small thing — and it is
worth being explicit that this is a change to production code, however
small, not a test-only move.

Do not retrofit your whole validation layer. Write one check, for the
property the step-1 pin is about, in the narrowest place that can see
the real artifact: a function called at the end of a request handler, a
post-condition on a batch, a verifier invoked by the pin itself. If it
earns its keep, the second one is cheap and the shape is already
decided. If it does not, you have lost an hour.

A check is the smallest thing that inspects the real artifact and
returns findings. Three properties make it pinnable; without them you
will not be able to prove it works.

**It reads the artifact, not the intention.** Parse the response body,
open the written file, query the database, look at the rendered pixels.
A check over the value you were about to write is a check over your own
belief.

**Its finding carries what it measured**, not merely disapproval:

```python
Finding(code="null_in_required_column", row=41, column="order_id",
        measured=None, expected="non-null")

Finding(code="latency_budget", route="/checkout",
        measured_ms=1840.0, budget_ms=800.0, direction="over")
```

A finding that says only "invalid" cannot be pinned by value, so no pin
will ever be able to catch it answering wrongly — only silently.

**It reports its own failure distinctly.** A check that raises returns a
`check-error` state rather than an empty result. Empty means "looked and
found nothing"; those must never be the same value.

Done when: you can call one function over a real artifact and get back a
list of findings with numbers or identities in them.

## Step 3: the first seeded defect and its neighbour

Take a known-good artifact. Change exactly one thing so the defect your
check exists to catch is present. Assert the finding **by value**. Then
build the neighbour: the legitimate look-alike where the check must stay
quiet, run ungated on every commit.

```
base      : an order with a valid, unexpired payment token
defect    : set the token's expiry one second in the past
expect    : Finding("auth_denied", reason="token_expired")
neighbour : the same order, token unexpired -> Silence("auth_denied")
```

```
base      : a batch where every row satisfies the schema
defect    : null out one value in a NOT NULL column
expect    : Finding("null_in_required_column", row=41, column="order_id")
neighbour : a null in a genuinely nullable column -> Silence(...)
```

Check the pair across **every** output channel — errors, warnings,
notes, exit code, logs — and confirm they differ only in the defect. A
pair that also differs in some incidental way is a pair whose result you
cannot attribute.

Done when: seeding the defect makes the check say the right thing, and
the neighbour proves the check is not simply shouting at everything.

## Step 4: the ledger

One row per finding your checks can emit, in exactly three states:

```
auth_denied                 proven      pin: expired_token_still_authorizes
null_in_required_column     proven      pin: null_in_order_id
latency_budget              deferred    expensive tier; gated behind PERF=1
schema_drift                UNPROVEN    no pin yet; needs a two-version fixture
```

Keep it wherever a test can read it: a table in a source file, a map
literal inside the harness, a JSON or YAML file next to it. The format
is not the point — the gate test parsing it is. Prefer whatever your
reviewers will actually notice in a diff.

Add a gate test that fails when a finding code is in none of the three
states. That single test is what makes the ledger unable to rot: a new
check arrives with its proving pin, or it arrives named as unproven —
and both are fine, while silence is not.

Write real reasons in the `UNPROVEN` column. "TODO" tells the next
reader nothing about whether the gap is five minutes or a week.

Done when: adding a check with no pin and no ledger row fails the suite.

## Step 5: the registry gate

The ledger's weakness is that it is a separate list. Close it with a
test that derives the set of findings from the source of truth and
fails when that set and the ledger's keys disagree, with a message that
says "re-enumerate".

"Derives" depends on your language. Where the set exists at runtime, read
it: a registry map, a decorator scan, a reflective walk of the
constructors. Where it does not — Go, and most compiled languages, where
an unreferenced constant is invisible to `reflect` — parse the source
instead: an AST walk in a test, or a generate step that emits the list
and a check that the emitted list is current. A grep is a last resort
and a bad one; it over-counts on prose and comments, which is a lesson
every project seems to learn twice.

The point is that nobody has to remember. Someone adds a check in a
hurry at the end of an unrelated task; the suite tells them what they
owe before review does.

Done when: adding a finding code anywhere fails the suite until the
ledger names it.

## Step 6: the stub sweep

You now have pins. Some of them are vacuous — they pass whether or not
their check works — and you cannot tell which by reading.

Stub each check to report nothing. Run the tests that claim to guard it.
Every one of them should fail; the ones that pass are decoration. Fix
them by giving each an assertion that binds a value the dead check
cannot produce.

Run this the first time expecting bad news. The class is reliably larger
than the estimate, and it tends to include tests that were flipped green
recently — because a pin that was honest while red becomes vacuous the
moment its claim flips from "the check is silent here" to "the check is
healthy here".

Done when: every test claiming to guard a check fails when that check is
blinded.

## Step 7 and beyond

- **Discovery sweeps** when you want defect *classes* rather than known
  defects — perturb inputs, demand a response, disposition survivors.
- **Mortality** when the suite is big enough that "which tests guard
  this check" is no longer answerable by reading.
- **A second tier** when some truth exists only in an expensive artifact
  (real browser, real database, deployed endpoint).

`references/harness-design.md` covers all three in build detail.

## What to skip

- **Don't build a catalogue format before you have three pins.** The
  shape you would design from zero is wrong in ways only real entries
  reveal.
- **Don't add a check because it is easy to add.** Every check owes a
  ledger row and a proving pin forever.
- **Don't start with mortality.** It measures the guarding of checks you
  have not written yet, at the cost of a full suite run each.
- **Don't automate the stub sweep as a lint.** Legitimate partial
  controls are common enough that the rule cries wolf, and a noisy guard
  gets ignored, which costs you the real hits.
