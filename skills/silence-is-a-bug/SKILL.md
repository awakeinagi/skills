---
name: silence-is-a-bug
description: >-
  Hunt the defects your checks never reported, and prove a check speaks
  before you trust its silence. Use this whenever something broke that a
  test, lint, validator, schema, assertion, or alert should have caught
  ("the suite is green but it's broken", "why didn't CI catch this", "how
  did this ship"), whenever you are about to claim a check works, whenever
  a review or a user finds a defect in shipped code (pin it before anyone
  fixes it), when coverage is high and bugs still escape, and whenever
  anyone says "mutation testing", "test the tests", "prove this guard
  bites", "write a regression test for this bug", "silent failure",
  "returned nothing", or "nothing fired". Prefer this over writing a quick
  regression test by hand — the point is not that a test exists, it is
  that the check is proven to speak, and that the person who found the bug
  is not the person who declares it fixed.
---

# Silence is a bug

A check that says nothing and a check that found nothing are
indistinguishable from outside. Everything here exists to tell them
apart, and to make sure the defects you already know about cannot be
quietly forgotten.

This is a way of working, not a tool. It fits a REST service, a
compiler, an ETL job, a game engine, a billing system — anywhere you
rely on something automated to tell you the software is healthy.

## The premise

Three failures, all real in shape, none of them exotic:

- An authorization check with full line coverage that had never once
  denied a request in production. It read a `roles` list the serializer
  had stopped populating; an empty list denies nothing. Every test
  called the handler directly, so the middleware was never in the path.
- A schema validator that caught its own `ValidationError` and logged it
  at debug level. The daily report said `rejected rows: 0` for a month.
  Nobody reads a zero as a broken validator; they read it as clean data.
- A drift metric that compared this week's sample to itself and reported
  0.3% for a 30% shift. It answered the most important question of the
  week, confidently, backwards.

The shared shape: **silence reads as health.** Line coverage cannot
distinguish these from working checks — in all three the lines ran, on
schedule, and reported nothing. Neither can a reviewer reading the code,
an incident retro, or a dashboard. Only a deliberately
injected defect can, because only then do you know what the check
*should* have said.

The second premise, from the same failures: **judge the artifact, not
the path.** When a computed value and the thing a user actually receives
disagree, the received thing is the fact. A handler can return the right
object while the serializer drops a field; an aggregation can be exact
while the export writes the columns in the wrong order. Point your
checks at the delivered artifact wherever you can afford to.

### What "check" means here, and which half applies to you

A **check** is anything automated that is supposed to tell you the
software is healthy: a test assertion, a lint rule, a schema validator,
a runtime guard, a monitor. A **finding** is one thing a check says,
carrying what it measured.

The doctrine has two halves, and they have different entry costs:

- **The half that applies today, to any project.** Pins and the flip
  contract, seeded defects with neighbours, "a silence needs a firing
  beside it", who-fixes, review by measurement. These need nothing but
  your existing test suite.
- **The half that presupposes checks you can enumerate.** The coverage
  ledger, the registry gate, discovery sweeps, mortality. These assume
  your system has *named* checks emitting *identifiable* findings — a
  registry, a rule set, an error-code table. Many services do not have
  that layer, and building one is a project of its own; do not start
  there. Start with the first half, and build the first check only when
  a pin needs one (`references/from-zero.md`, step 2).

## The loop

```
witness a defect  ->  PIN it (a failing test that describes correct behavior)
                  ->  route it to an owner (who is not you)
                  ->  owner fixes it, later, against a pin they did not write
                  ->  the pin flips to green in the same change as the fix
                  ->  re-earn it: stub the check, the flipped test must FAIL
                  ->  the check joins the ledger as proven
```

Around that loop sit two sweeps that find defects nobody witnessed:
**discovery** (perturb the input, demand a response) and **mortality**
(kill each check, demand that something notices). Both are below.

## The doctrine

### 1. A defect becomes a pin before it becomes a fix

When you find a bug, do not fix it first. Write the test that describes
the **correct** behavior. Because the bug is live, that test fails
today — it is a **pin**, marked expected-to-fail so the suite stays
green overall and prints its own count of outstanding pins.

Every pin is a bug being tracked, visible on every run, blocking nobody.
It is a to-do item that checks itself off and refuses to be forgotten.

The load-bearing trick is **the flip**: when someone fixes the bug, the
pin starts passing — and a good runner reports an expected failure that
passed as a hard failure. The fixer is forced to notice, drop the
marker, and turn the pin into a plain regression test **in the same
change as the fix**.

Verify this property in your runner before you rely on it; the flip
alarm is the whole mechanism, and a runner that merely *skips* pinned
tests gives you a to-do list that never checks itself off.

| Runner | Mechanism | Does an unexpected pass fail the run? |
| --- | --- | --- |
| pytest | `@pytest.mark.xfail(strict=True)` | Yes, with `strict` |
| unittest | `@unittest.expectedFailure` | Yes |
| RSpec | `pending` | Yes (`skip` does not) |
| Vitest | `test.fails` | Yes |
| Jest | `it.failing` | Yes |
| Go, JUnit, others | no native equivalent | Build it: see `references/from-zero.md` |

The pytest and unittest rows were run and confirmed; treat the rest as
leads and settle yours with the ten-line probe in
`references/from-zero.md` step 0. It takes a minute, and this is the one
property everything else in this skill rests on.

Two absolute rules, both cheap to break and expensive to have broken:

- **Never delete or weaken a pin to get green.** The pin is the record
  that the defect existed; deleting it un-finds the bug.
- **Never mark a test expected-to-fail that you have not watched fail
  for the right reason.** Most runners swallow *errors* exactly like
  assertion failures, so a pin whose setup crashes looks identical to a
  pin catching a real defect. Run it, read the message, confirm it is
  the mismatch you predicted — not a typo'd import.

**The flip has a second half: re-earn the control.** A pin that goes
green must be re-proven — stub the check it calls so it reports nothing,
and confirm the test now FAILS. A test that still passes was asserting
the defect's *absence*, not the check's *presence*, and has silently
stopped discriminating.

The hazard is structural, not carelessness. While red, the claim is "the
check is silent here", and a dead check satisfies that claim — which
trips the unexpected-pass alarm and gets caught. Once green, the same
line asserts health, and a dead check agrees with it. The re-earning is
one stub and one test run — expect FAILED — so it costs about what the
single test costs. It falls on the person who has the context, and it
asks one question about one test, so there is no false-positive surface
to manage.

### 2. A seeded defect is a quadruple, and it asserts what it measured

The unit of the catalogue is a record with four parts. Make the loader
refuse any record missing one, so the discipline is mechanical rather
than remembered:

```
Pin(
  "expired_token_still_authorizes",
  base      = a request that legitimately succeeds,   # known-good
  defect    = expire the token by one second,         # exactly ONE change
  expect    = Finding("auth_denied", reason="token_expired"),
  neighbour = (the same request, unexpired, Silence("auth_denied")),
)

Pin(
  "drift_understated_on_shifted_sample",
  base      = last week's sample against the reference distribution,
  defect    = shift the numeric column by +30%,
  expect    = Finding("drift", magnitude=(0.30, tol=0.02), direction="up"),
  neighbour = (an unshifted sample, Silence("drift")),
)
```

Two decisions are the whole design:

**`expect` binds what the check measured, never that it spoke.** A rate
limiter can return the right 429 and the wrong `Retry-After` — one
second where the window is a minute — and every client hammers the
service back down. A geometry check can announce "15px inside the shape"
about a point sitting 50px *outside* it. A typechecker can report
`expected int, got str` and point at the wrong line. An existence
assertion passes all three. You need to be able to say *you spoke, and
you said the wrong thing.* For numeric findings that means magnitude and
direction with a tolerance band; for categorical ones it means the
identity — which rule, which element, which reason. The corollary for
new checks: **a finding carries what it measured** ("2.3:1 against the
page background; needs 4.5:1", "row 41: `order_id` null, column is
`NOT NULL`"), not just disapproval. A check that only disapproves cannot
be pinned precisely, so it will never be provably right.

**The neighbour is mandatory, and it is the live half.** Every "it fires
on the defect" ships with "and it stays quiet on the legitimate
look-alike" — or the reverse, when the defect is an over-fire. The pin
itself is usually expected-to-fail and therefore masked; the neighbour
runs ungated in every commit, which is what stops a crashed or dead
check from hiding inside that mask. Pair by the check's poles, not by a
fixed rule: an over-fire pin gets a neighbour proving the check still
fires legitimately; a wrong-answer pin gets a neighbour proving silence
on the healthy case.

Two failure modes worth pre-empting:

- **A crash in the check is not coverage.** A check that raises should
  report a distinct `check-error` state, and your silence matcher should
  refuse to match any run where a check crashed. Otherwise "it looked
  and found nothing" and "it exploded on line 3" are the same green.
  (A crash in the *system under test* is a different animal: that can be
  exactly what a pin asserts — see `references/pin-taxonomy.md` on
  outcome-pins.)
- **Design the scene without confounds.** The defect case must differ
  from the neighbour in the defect and *nothing else*, verified across
  **every output channel** — errors, warnings, notes, exit code, logs.
  A probe that filters one channel out of its own evidence will certify
  a confounded pair. When an incidental finding is unavoidable, give the
  neighbour the *identical* incidental finding so the defect stays the
  only variable.

### 3. A silence needs a firing beside it — through the entry point under test

Any assertion that a check is quiet needs a positive control next to it
that makes the same check speak. **A second absence proves nothing.**

The second half is the one people get wrong, and they get it wrong
because the wrong control pattern-matches to rigour: **the firing must
run through the entry point under test.** Asserting that *something*
happened somewhere in the system reads right, passes, and closes a
dead-substrate hole while the dead-*check* hole stays wide open —
because it never calls the function you are making claims about.

- A test that proves "the request pipeline is alive" by checking that
  the app returns 200 on `/health` does not prove the authorization
  middleware is alive. Call the middleware.
- A test that proves "rows moved" by counting output rows does not prove
  the quarantine rule ran. Make the quarantine rule reject something.

Neither error is visible by reading. Both are visible in one stubbed
run. So make the stub sweep **routine**, not a lucky catch: at each
phase gate, stub each check under test to report nothing, and require
every test claiming to guard it to fail. Where this has been measured
from scratch, the vacuous class came back several times the estimate
made beforehand, and included pins that had been flipped green recently.
Treat your own estimate as a floor until you have swept once.

Resist automating this as a static-analysis rule. Legitimate
partial-control shapes exist in quantity, and a guard that cries wolf
becomes catalogue padding that everyone learns to ignore.

### 4. Whoever finds it does not fix it

The person who discovers a defect writes the pin. The person who owns
the code writes the fix, later, against a pin they did not author.

This is not ceremony. An acceptance test and a fix from the same hands
is how defects ship: the test drifts toward whatever the fix happens to
do, and the two agree with each other rather than with the requirement.
Witnessing routes to the pin now; fixing happens when the owning work
item is executed, in plan order — never opportunistically, never
because it "looks like a five-minute fix". That temptation is the whole
failure mode wearing a helpful face.

Corollaries that make it work in practice:

- **A pin is not fully filed until something owns its flip** — an issue,
  a milestone's inventory, a backlog entry that names it. Decided when
  filed. A pin in the count with no owner is a filing error, not a
  backlog.
- **A defect found in the change currently under review is a review
  finding, not a pin** — born and caught in the same cycle, with an
  independent reviewer on both sides of the fix. Two tests must both
  hold, and `references/roles-and-flow.md` spells them out; the one that
  catches people is that a change which merely *exposed* a pre-existing
  defect leaves that pre-existing half as shipped code, which gets
  pinned.
- **The finder may propose a repair, clearly fenced as a proposal, never
  applied.** A proposed repair inside a finding is a hypothesis:
  plausible one-line repairs measure *worse* than what they replace
  often enough that they ship only with the same measurement standard as
  the finding itself. And a guard proves the property it **evaluates**,
  never the property its author had in mind — a gap invisible from
  inside the guard.
- **"Not a miss" is a valued outcome.** Reproduce before pinning; if the
  check fires correctly, say so with the finding as evidence and stop. A
  catalogue padded with entries that were never broken teaches nothing
  and costs every future run.

**Two regimes, and say which one you are in.** With more than one pair
of hands, the separation is by *person*: whoever pins does not fix, and
it is a charter, not a preference — give the role its own agent
definition or its own rotating duty so it can refuse work. Alone, the
separation is by *time and intent*: write the pin against the
requirement, before you have a fix in mind, and do not edit the pin
while making it pass. A pin you had to loosen to get green was written
by the fix.

### 5. Coverage means the checks, not the lines

For every **finding a check can emit** — one ledger row per finding, not
per check, since one check with three findings can be proven on one of
them and blind on the other two — ask: has anything ever proven it
fires, with the right value and sign? Keep the answer in a ledger with
exactly three states — `proven`, `deferred` (its proof is gated behind
an expensive tier), or `UNPROVEN` **with a written reason** — and a gate
test that fails on any other state.

Then a new check arrives with its proving pin, or it arrives named as
unproven. Never quietly. The authorization check from the premise had
~100% line coverage and had never once been proven to deny anything;
line coverage said healthy, this ledger would have said unproven from
day one. That distinction — *has this check been proven to fire
correctly* versus *were its lines executed* — is the one worth carrying
into any argument about coverage numbers.

Close the back door with a **registry gate**: a test that derives the
set of findings from the source of truth — the registry, a reflective
scan, or a parse of the source in languages where the set is not
discoverable at runtime — and fails with "re-enumerate" when it
disagrees with the ledger's keys. Derived, never maintained by hand.

Every number in this system is derived at read time by the runner or a
script — the outstanding-pin count, the ledger rows, the registry count.
None of them is maintained by hand in prose, because a number in prose
is a measurement with a date on it, and it will be stale before it is
read twice.

### 6. Kill each check and see who notices

Detector coverage asks whether a check was ever proven. Mortality asks
the sharper question: **if this check went blind today, would anything
fail?** Measure it rather than arguing it — build a throwaway copy of
the tree, patch exactly one check so it reports nothing, run the real
suites against the corpse, record which tests fail. A check whose death
costs nothing is a check you only believe in.

The witness count is a free second output and the only one of its kind:
a check that drops to a single witness is one refactor away from
unguarded, and nothing else in your toolchain will tell you.

Rule 3 applies **one level up**: a harness that kills checks needs its
own liveness controls, because a kill that silently did not take effect
fabricates a clean row — and does so quietly, which is the failure mode
this whole skill is about. Give every kill three independent witnesses:
**inertness** (the instrumentation is installed on the control run too,
and the suite must be green and the same size), **a drop counter** (a
kill that destroyed no findings is an error, not a "nobody noticed"
row), and **surgicality** (only the intended check went blind). Build
detail for all three is in `references/harness-design.md`.

And the limit, which is the reason rule 3 exists separately: **a noticed
death is not a good control.** Mortality works at check level — "does
this check's death cost anything anywhere". A check whose death fails
eight tests can still have a ninth that claims to guard it and does not.
That per-pin question belongs to the stub sweep in rule 3.

Mortality costs one full suite run per check — multiply before you
schedule it. Run it at a phase gate, never per commit, and keep it out
of the default run.

### 7. Review by measurement

Reviews that find things do not read code; they re-run it.

- **Derive, don't trust.** Re-compute the arithmetic, re-run the repro,
  re-count the enumeration. Almost every real finding in a review comes
  from re-measuring something a report asserted.
- **Peer measurements are re-run, not inherited.** When someone else's
  number is load-bearing for your decision, measure it again. The check
  is usually seconds, and it is where the findings live: a PASS verdict
  that wasn't, a "three tests" that was four, a cost estimate off by an
  order of magnitude.
- **Bite-proof or it didn't happen.** Any claim that a guard catches X
  is demonstrated: break it deliberately, quote the failure, restore.
  House format: `baseline PASS -> broken FAIL (message quoted) ->
  restored PASS`.

`references/evidence-norms.md` is the full set and the canonical copy —
probe direction, observation scoping, why a number in prose is a
measurement with a date on it, how to state a deflating calibration.
It is short, and it is the difference between a report that is right and
a report that sounds right.

## Discovery: finding defect classes nobody witnessed

Everything above re-finds defects you know about. Discovery asserts the
inverse property: **a meaningful change to the input must produce a
change in what the checks say.**

Sweep perturbations through the pipeline and flag **survivors** — real
changes nothing responded to. (Note the inversion against the term's
usual meaning: in code mutation testing a survivor is a mutated *line*
your tests missed. Here it is a changed *input* your checks missed. Same
idea, different thing mutated — say which you mean in mixed company.)

- A service: drop a required field, negate a quantity, swap two ids,
  expire a credential. Demand a change in the observable surface —
  status, error identity, audit record.
- A compiler or analyzer: swap a comparison operator, delete a branch,
  reorder two independent statements. Demand a change in diagnostics or
  emitted output.

Every survivor gets one of three dispositions, recorded with a reason:
**promote** (it becomes a pin, and probably a new check), **allow**
(legitimate invariance — write down why, so the judgement is never
re-made), or **bug** (the perturbation or the predicate is wrong; fix it
and re-sweep rather than leaving the entry). **An undispositioned
survivor is a failure**, enforced by the exit code: a report that is
noisy once gets acted on, and a report that is noisy forever gets
ignored. `references/harness-design.md` has the sweep's build detail.

If you already run an off-the-shelf mutation tester (mutmut, Stryker,
PIT, cargo-mutants, go-mutesting — most ecosystems have one), keep it.
It mutates your *code* and asks whether your *tests* notice, which is a
real and complementary question. This doctrine mutates the *input and
state* and asks whether your *checks* notice.

The gap between them is worth naming precisely rather than asserting a
result: code mutation testing measures your tests against your code as
written. It cannot ask whether a check is **wired into the path a user
actually takes** (mutants in a middleware are killed by unit tests that
never mount it), and it cannot ask whether a finding's **value** is
right (a mutant that changes a reported number is killed by any test
asserting that number, and unkilled by tests asserting only that
something was reported). Those two questions are where the premise's
three failures lived. If you want to know which of the two regimes your
own escaped bugs came from, classify the last ten before investing in
either.

## Cheap tier, expensive tier

Split checks by what they can see, and gate the expensive half.

- **Cheap tier** — reads stored or intermediate state. Fast, no external
  dependencies, so it runs in whatever gate every change passes through.
- **Expensive tier** — reads the real artifact: a real database, a
  deployed endpoint, a compiled binary, a browser rendering the page.
  Some truths exist only here — connection-pool exhaustion, link-time
  symbol resolution, one layer painting over another — and no amount of
  reasoning over the model reaches them.

Three rules keep the split honest:

- **Gate it behind an explicit flag, and make the gated-on path RAISE
  when its dependency is missing.** An environmental failure must never
  mark checks green. "Browser not found, skipping" is how an entire tier
  goes dark for a quarter.
- **An expensive-tier check is only as trustworthy as the surface it
  looks through.** If your renderer, exporter, or test harness has its
  own known defect, a check pointed at it inherits that defect and
  reports confidence. Pin equivalence between any second path and the
  primary — and know the ceiling: if path B is built on path A's output,
  their agreement cannot clear a defect they both inherit.
- **Assert what is stable across environments.** Identities, counts,
  orderings, structural facts, and *relative* steps travel. Absolute
  environment-sensitive numbers do not — a latency figure, a rendered
  colour value, a memory high-water mark. A committed assertion on one
  of those pins which machine the runner happened to land on: an
  environmental failure wearing a calibration's costume. Measure across
  environments before calibrating, not just across runs, and put the
  threshold where every known environment clears it with margin.

## Starting from zero

If the project has no harness yet, do not build one. Build the first
pin, then let need pull the rest. The full on-ramp, in order, with what
each step buys and the smallest thing that counts as done, is in
`references/from-zero.md`. The short version:

1. Take the most recent bug that got past your checks. Write the pin.
   Do not fix it.
2. Write the first check: the smallest thing that inspects the real
   artifact for the property you care about and returns a finding that
   carries what it measured.
3. Give that check its first seeded defect and its neighbour.
4. Start the ledger with those two rows, and the gate test that fails on
   any finding that is neither proven nor unproven-with-a-reason.
5. Add the registry gate, so the ledger cannot silently fall behind.
6. Only then: discovery sweeps, then mortality.

Steps 1 and 3 are worth doing on their own; a project can live there for
a year and still be much better off. Steps 4 onward only pay once your
checks are numerous enough that you cannot hold them in your head.

If your runner has no expected-to-fail mechanism (Go and JUnit, among
others), step 1 needs a small piece of scaffolding first —
`references/from-zero.md` step 0 has the portable recipe.

A mature suite starts at step 3 or 4 — the pins exist, the ledger does
not, and the vacuous-pin class is waiting for its first stub sweep.

## The gate checklist

At each phase gate — whatever your project calls the point where a chunk
of work is declared done: a milestone, a release candidate, a sprint
boundary:

- [ ] Every pin has an owner and a scheduled flip.
- [ ] Every finding is `proven`, `deferred`, or `UNPROVEN` with a reason.
- [ ] Every flip in this window was re-earned (stub the check, the
      flipped test fails).
- [ ] The stub sweep ran over the checks this window touched.
- [ ] Mortality ran; no check dropped to a single witness unnoticed.
- [ ] The discovery sweep has no undispositioned survivors.
- [ ] The expensive tier ran at least once, and its gated-on path was
      proven to raise when its dependency is absent.

Skip the lines whose machinery you have not built; the first three apply
to any project with a test suite. Nothing here runs automatically at the
right moment, which is exactly why the checklist exists — all of it rots
silently otherwise.

## Glossary

The reference files use this vocabulary without redefining it.

- **check** — anything automated that is supposed to tell you the
  software is healthy: a test assertion, a lint rule, a validator, a
  runtime guard, a monitor.
- **finding** — one thing a check says, carrying what it measured.
  Ledger rows are per finding, not per check.
- **artifact** — the thing your system actually delivers: the response
  body, the written file, the rendered page, the row in the table. (Not
  a CI build artifact.)
- **pin** — a test that fails today on purpose, describing correct
  behavior for a known defect.
- **flip** — the forced transition pin -> green when the defect is
  fixed, because an unexpected pass fails the run.
- **re-earn** — after a flip, stubbing the check to prove the now-green
  test still discriminates.
- **quadruple** — the catalogue's unit: known-good base, one injected
  defect, the expected finding with what it measured, and a neighbour.
- **neighbour** — the legitimate look-alike at the check's other pole,
  run ungated, which keeps a dead check from hiding behind a masked pin.
- **masked** — under an expected-to-fail marker, so the runner reports
  neither its result nor its failure message.
- **error-red** — a pin that failed by crashing before the check was
  consulted, as opposed to **red by assertion**, where the check
  answered and answered wrongly. Only the second is healthy.
- **detector coverage** — has each finding ever been *proven* to fire
  correctly. Not line coverage.
- **survivor** — an input change that no check responded to. (In code
  mutation testing the same word means a mutated line your tests
  missed.)
- **disposition** — a survivor's recorded verdict: promote, allow, bug.
- **mortality** — killing a check to measure whether its death costs
  anything.
- **witness** — a test that fails under a kill and did not under the
  control run.
- **stub sweep** — blinding each check and requiring every test that
  claims to guard it to fail.
- **bite-proof** — demonstrating a guard fails under the condition it
  guards: break it, quote the failure, restore.
- **vacuous pin** — a test that passes whether or not its check works.

## References

This file is the doctrine at working depth. Each reference is the
**canonical copy** of its own subject: where one restates something from
here, the reference wins on detail and this file wins on framing.

| File | Read it when |
| --- | --- |
| `references/from-zero.md` | The project has little or no check infrastructure, or your runner has no expected-to-fail mechanism. Start here if you are adopting any of this. |
| `references/pin-taxonomy.md` | You are writing a pin and are unsure how to encode it — catalogue, bespoke class, or awaiting-a-check — or whether to pin the outcome or the mechanism. |
| `references/harness-design.md` | You are building or extending the harness itself: loader enforcement, the coverage ledger, sweeps, the mortality driver, tiering. Skip it until a pin needs something it describes. |
| `references/roles-and-flow.md` | You are routing defects between people or agents: who pins, who fixes, when fixes are taken up. Its last section is only for teams sharing one working tree with concurrent writers. |
| `references/evidence-norms.md` | You are writing a report, review, or claim — or reading one. Short, and where most wrong conclusions get caught. |

There are no bundled scripts, deliberately. The harness runs in the
project's own language against the project's own checks, so anything
shipped here would be a claim of portability it could not keep. The
snippets in the references are illustrations of shape, not code to copy.
