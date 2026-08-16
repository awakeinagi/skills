# Evidence norms

Short, and where most wrong conclusions get caught. These apply to
reports, reviews, commit messages, and the sentence you are about to
write in chat. They are the same standard the harness applies to the
code: **if your tool demands a witness, so do your sentences.**

## Measure before asserting

The recurring self-observation, from people building exactly this kind
of instrument: every wrong claim they made — a count, a diagnosis, a
"no check exists for that" — was one they could have measured in under a
minute and did not, while the instrument they were building refused to
report any row whose kill it had not watched bite. Each miss cost a peer
more time than the measurement would have.

Before a load-bearing sentence, ask what command would settle it. If
that command takes under a minute, you do not have a claim yet; you have
a hypothesis and an unused terminal.

## A claim is worth exactly the experiment you ran

Not one word more. Worked example, and the shape recurs everywhere:

Two people work in one repository at once. One of them tests whether
`git commit <path>` can pick up a colleague's in-flight edits, finds
that it can, and publishes a confident ranking of three staging
approaches with that method at the bottom. What the experiment never
touched is the *other* exposure of a different approach on the list —
that a bare `git commit` takes every file anyone has staged, which is
worse — and it surfaces weeks later, from the one person who happened to
run that experiment instead. In the meantime the ranking reads complete,
gets cited as settled, and is recommended by someone who was freshly
burned by the sibling failure they did not know was a sibling.

The rule: a claim about what a method protects you from covers exactly
the condition your experiment constructed. Say which condition that was.

The corollary, and it is the honest sentence: **"clean by timing, not by
control."** When five uses of an unsafe method came out clean, say they
came out clean *by timing* — nothing prevented the bad outcome, it just
did not happen. A clean result from an unsafe method is the single most
effective way to keep an unsafe method in use.

## Probes read all the channels

A probe that filters one output channel out of its own evidence will
certify a confounded artifact. Quote full outputs — stdout, stderr, exit
code, the warnings you were not looking for, the log line at debug
level. Name your probe's blind spots when you find them; the root-cause
disclosure is worth more than the fix.

## Which direction am I testing?

**A probe is evidence only about the condition it actually constructed,
and the reassuring answer deserves the same scrutiny as the alarming
one.**

This is the norm that catches the most, because the failure is
symmetrical and neither half looks like an error:

- The loud probe fails and reads as diligence — while never having
  constructed its condition at all (one line of a two-line pattern, a
  fixture that never loaded, a flag that silently did nothing). It is
  about to file "the guard does not work" on no evidence.
- The silent probe passes and reads as safety — while being the actual
  defect (the correct copy masking a stale canonical, the cache hiding
  the query you meant to test).

Ask which direction you are testing *before* trusting a result, in
either polarity. Then confirm the probe produced its condition: assert
on the setup, not just the outcome.

## An observation is not more than it is

Four look-alike failures, one root: a state observed and a **cause**
asserted.

- **Scope every observation to its moment.** Say "at commit `abc1234`,
  just now, I saw X", not "X is the case". A race can make the standing
  version false before it is read.
- **Derive causes; never infer them from adjacency.** A fix landing
  between two commands is not the same as that fix causing the change
  the second command shows. Separate them with a real query — the
  history of that specific path, a bisect, a second run with the change
  reverted.
- **A count is true when written and read later as a fact.** Which
  brings us to:

## A number in prose is a measurement with a date on it

Derive it at read time or do not state it. If a number must appear in
prose, name the command that produces it and the commit it was taken at,
and expect it to be wrong the third time someone reads it.

Two supporting habits:

- **Before writing a warning about a number, check whether the file
  already says so.** Multiple people independently rediscovering a
  conclusion the file's own paragraph already records is a real and
  expensive pattern — including rediscovering that a naive `grep -c`
  overcounts, that the strict form is the right one, and that the figure
  should be deleted rather than recounted.
- **Prefer deleting a number to correcting it.** "The figure is
  derivable" is a better sentence than a fresh figure that will go stale
  on the same schedule as the last one.

## A sweep over a corpus with none of the affected class measures nothing

However many numbers it prints. "Zero of twenty-four moved" was offered
as evidence a rotation fix was safe — over a corpus where none of the
elements carried a rotation. The zero could not have been anything else.

Before offering a corpus sweep as safety evidence, state how many
instances of the affected class the corpus contains. If it is zero, the
pin is the evidence and the sweep is decoration; say so.

## Peer measurements are re-run, not inherited

In a busy concurrent wave, nearly every real finding comes from someone
re-running another person's measurement rather than accepting it: a PASS
verdict that wasn't, a "three tests" that was four, a safety ranking, a
first repair attempt, a cost estimate. None of them was caught by a
test; all of them were caught by a second measurement.

When a peer's number is load-bearing for *your* decision, re-measure it.
It usually takes seconds, and it is where the findings live.

## Bite-proof or it didn't happen

Any claim that a guard, test, or check catches X is demonstrated:

```
baseline  PASS
broken    FAIL  -- "AssertionError: expected drift<=0.02, measured 0.30"
restored  PASS
```

Break it deliberately, quote the failure message verbatim, restore.
"This test would catch that" is a hypothesis; the three lines above are
evidence.

## Both poles, always; and run the rival

A proposed fix demonstrates the defect case flipping **and** the
legitimate case staying put, in the same report. One pole is half an
argument.

And state the premise you are working from so it can be falsified —
then try to falsify it. Premise-falsifications are consistently worth
more than confirmations: "the slow endpoint is the database" (it is the
serializer), "this backlog needs a new scheduler" (five of the six items
are outside anything a scheduler touches), "nobody else validates this"
(several projects do — and the corrected claim, *others validate; nobody
tests their validators*, was sharper than the original).

## Corrections flow to the source

When a measurement kills a claim, the correction goes **into the
document at the claim**, dated, with the measurement — not only into the
review record nobody re-reads. This includes correcting briefs you were
given and reports you wrote yourself. Both are strengths, not
embarrassments; a document that has been corrected in place is worth
more than one that was right by luck.

A rejected proposal is retired **by name, with its reasoning intact**,
so it cannot be innocently re-proposed. Record the rejected forms on the
pin they concern — the third person to propose the same bad repair is
not being careless, they are reading a record that does not mention it.

## State the calibration honestly

Negative results are deliverables. "Not applicable", "not a miss",
"this source is thin, little to take", "the fix is right but for a
different reason than the report gives" — all of these are worth more
than padded findings, and all of them are load-bearing for whoever
decides what to do next.

Say the deflating version of your own result when that is what the
measurement supports. The alternative is a record that reads better than
the software works, which is the same failure mode as a check that says
nothing.
