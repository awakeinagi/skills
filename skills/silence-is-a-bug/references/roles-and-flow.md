# Roles and flow

Who pins, who fixes, when, and how a defect gets from the moment it is
witnessed to the moment it is permanently caught. Works with a team of
people, a team of agents, or one person wearing both hats an hour apart.

## The one-line rule

**Every defect found in shipped code, by anyone, during any activity,
becomes a pin before anyone fixes it — and the person who pins it is not
the person who fixes it.**

Everything below is that rule surviving contact with real projects.

## The pinner role

Give the role a name and a charter, because it must be able to refuse
work. In an agent setup, that is a subagent definition whose first
boundary is "you never fix the defect". On a human team it is a rotating
duty, and the person holding it can say "not mine to fix" without it
being a political act.

**The handoff format**, which the caller can produce in twenty seconds
mid-task without losing their thread:

> Here is the input. Here is what the system wrongly does with it. Here
> is what the checks reported (nothing). Make it a pin.

**The pinner's contract**, which callers can rely on:

1. **Reproduce first.** Confirm the defect exists as described *and*
   that the named check is genuinely silent — or fires with the wrong
   value or the wrong sign. Run the real checks over the real artifact
   and read what comes back. **If the check actually fires correctly,
   report "not a miss" with the finding as evidence and stop.** A
   catalogue padded with entries that were never broken teaches nobody
   anything and costs every future run. Dismissals with evidence are a
   valued output, not a failure to deliver.
2. **Minimize.** The smallest artifact that still exhibits the defect:
   fewest elements, round numbers, no incidental content. Then
   re-confirm the defect survived minimization — a minimization that
   also removed the bug has changed the subject.
3. **Encode by the taxonomy** (`pin-taxonomy.md`), with the expected
   finding bound by value or identity and the neighbour at the opposite
   pole.
4. **Prove both directions.** The pin fails for the right reason — read
   the message, do not accept the colour — and the neighbour passes.
   Then run the whole tier: nothing else may break, and nothing may turn
   unexpected-pass.
5. **Update the ledger** in the same change, and record the origin in
   the entry's comment.
6. **Report** the pin's id, its state and tier, the ledger delta
   (`before -> after`), and whether it joins an existing family or opens
   a new one.

**The boundaries:**

- **Never fix the defect.** Not even when it is a one-liner — especially
  then. That is not the pinner's call to make; name it in the report and
  leave it.
- **A repair may be proposed, clearly fenced** as a proposal, naming
  where it would go and what it would cost. Never applied, never wired
  in. A proposal is an argument for the owner to accept or reject, and
  plausible one-line repairs measure worse than what they replace often
  enough that this matters.
- **Never delete or weaken a pin to get green.** A pin that starts
  passing is the signal the whole system exists to produce.

## Routing: how defects get lost, and how not to lose them

A defect that was not handed off does not exist. Two specific ways they
evaporate, both worth designing against:

- **Buried in a report.** A defect mentioned in the findings section of
  a review, an implementation report, or an addendum, and never
  extracted, is exactly as lost as one in a scratch note. Whoever closes
  an artifact's cycle reads every findings and concerns section **in
  full** and enumerates each candidate **by name** with a one-line
  description. Reading the head of a report to check its counts is not
  reading the report.
- **Judged too small to bother.** The judgement is not available at
  witness time. Pin it; the owner can rule it wontfix with a reason,
  which is a decision, recorded.

**The one boundary:** a defect *introduced by the change currently under
review* is a review finding, not a pin. It was born and caught inside
the same cycle, and an independent reviewer sits on both sides of the
fix, which mitigates the same-hands concern. Two tests must both hold:

1. The defect did not exist before this change. If the change merely
   *exposed* something pre-existing — a consumer that was always wrong,
   a check that never fired — that pre-existing half is shipped code and
   gets pinned.
2. The fix lands inside this same review cycle. A finding deferred past
   the change's completion is no longer "in review" and gets pinned like
   any shipped defect.

## When fixes are taken up

**A pin is not fully filed until something owns its flip.** Whatever
your project uses to say "this work is somebody's and it is scheduled" —
an issue with the pin's name in it, a line in a milestone's inventory, a
backlog entry — decided when filed, not left floating. A pin in the
count with no owner is a filing error, not a backlog.

**Triage each new pin as reachable or latent**, at pin time:

- **Reachable** — a normal user or agent flow gets there. Queue its fix
  next. Cap the interruption: at most one fix insertion between planned
  work items, with further pins accumulating to the next natural seam.
- **Latent** — it needs a corrupted disk, a crafted input no flow
  produces, or a state the system cannot currently reach. The pin rides
  to the planned work that owns its region. The pin *is* the guard, and
  nothing unreachable can be stepped on meanwhile.

**Fixes are taken up only when their owning work item executes, in plan
order — never opportunistically.** Not mid-batch, not mid-review, not
because a pin looks like a five-minute fix. The temptation to fix a
defect the moment you witness it is precisely the same-hands pattern the
structure exists to prevent.

The count of outstanding pins audits all three rules at once: every pin
it counts has an owner, a scheduled flip, and a fix it is waiting to
catch. Derive that count from the runner; never maintain it in prose.

## The review loop

Every substantive change gets fresh eyes; every finding is fixed or
ruled, never silently dropped.

- Fix rounds are scoped: fix, plus covering tests, plus an appended fix
  report, plus one scoped re-review that **verifies each finding by
  measurement** rather than by reading the fix. Nothing is taken on the
  report's word.
- The scoped re-review is also where a fix's own covering test gets
  bite-proofed. A fix arriving with a test that passes before the fix is
  a fix nobody has evidence for.
- Minor findings are ledgered, not looped — unless someone deliberately
  calls a minors wave and closes the pile.
- **Findings that conflict with the plan go to the human.** Neither the
  implementer nor the reviewer dismisses a finding because a document
  mandates the behavior. Documents are wrong at a measurable rate.
- **Declare deviations from a brief, with reasons, at delivery.** Some
  of the best design decisions in any arc are implementer deviations
  argued at delivery time and upheld on review. A silent deviation is a
  different thing entirely.

## When several writers share a tree

**Skip this section if your team works on branches and merges through
pull requests** — your version control is already doing the isolation
these rules reconstruct by hand. It is for the case where several
writers (usually agents) act on one working tree at the same time.

The pins are the thing most worth protecting there, because they are the
only record of defects nobody is currently working on.

- **One writer per file, ever.** Two writers in one catalogue file
  produce duplicate entries and loader errors. Partition by file before
  dispatching. When the loader's duplicate-id refusal is what caught the
  collision, that is the system working — do not disarm it.
- **Side work runs in its own worktree.** Isolation prevents the whole
  class of concurrency incidents rather than detecting instances of it;
  build and verify in isolation, fold in whole when done.
- **Explicit-path scoping is not isolation.** A commit "by explicit
  path" takes the *working tree's* content for that path, whoever wrote
  it. Read the staged diff before every commit, and grep it for the
  other writers' symbols. Re-check that the base has not moved
  immediately before committing.
- **Hold until reported.** Mid-flight instructions cross with finishes
  more often than not. An agent acknowledges a batch when it picks it
  up; new work waits for the report; no writer starts while a reviewer
  is measuring the live tree.
- **Deliver reports as a durable file, not only as a message.** Final
  messages drop; a file in the workspace carries the record either way.
- **Reconcile crossings by measurement.** When you learn someone may
  have done your work, verify live — history, duplicate scan, suite run
  — then drop your duplicate unless yours is better on a named axis, and
  say which in your report.
- **Never remove a worktree or directory you did not create**, and clean
  up by the exact paths you created rather than by glob. A short
  mnemonic prefix collides by construction in a shared temp directory,
  and the near-misses are measured in minutes.
