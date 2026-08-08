# ADR-0002 — No human reviews the tearsheet before it is sent

**Status:** accepted
**Date:** 2026-08-07
**Evidence:** canvas save `74a9380` (the proposed review gate was deleted and
the send step moved into the space it left)

## Context

An LLM agent writes the final report. That report reaches the entire
investment team, before the open, and people will make decisions with it in
their hands.

A human sign-off gate was proposed on the canvas and explicitly deleted. The
deletion was not passive: the send step was then pulled left into the vacated
space, closing the gap — the design says not merely "no reviewer" but "no
pause of any kind between model output and the team's inbox".

This decision also interacts with ADR-0001. A reviewer is incompatible with a
hard 07:15 send unless the reviewer is contractually awake, which is a
staffing commitment nobody wants to make for a daily 07:05 window.

## Decision

**The agent's output is delivered without human review.**

Consequently, the qualities a reviewer would have supplied must be supplied by
the system itself:

1. **The agent states what it knows and flags what is thin.** A one-line
   hedge ("coverage thin on this one") is the expected form.
2. **The agent never guesses, and never silently drops an item.** Omission
   without a marker is the failure mode that would most damage trust, because
   it is invisible.
3. **The agent is never made responsible for spotting salience it could be
   told about.** Earnings is the first instance: which names report is a
   deterministic calendar lookup performed upstream and handed to the agent as
   an instruction, not something the model must notice while reading news.
   Any future "the sheet should make a big deal of X" requirement should be
   solved the same way.

## Consequences

**Accepted:**

- On a bad morning, a wrong or misleading sentence reaches the whole team with
  nothing in its way. The mitigations above reduce but do not eliminate this.
- Trust in the sheet is now a property of the pipeline's design rather than of
  anyone's attention, and it is spent all at once if the sheet is confidently
  wrong.

**Resolved since (save `3f07023`):**

- The writing agent was a single point of failure for the entire report, not
  merely for its prose. Summarisation failing degrades correctly — the numbers
  still arrive and the news section is marked unavailable. But the *writing*
  step hanging produced no sheet at all, violating ADR-0001.

  A **Fallback Renderer** was added: a deterministic, model-free template that
  ships the computed numbers if the agent misses its slot. The sheet is uglier
  and has no prose; it exists. With it, "we always send at 07:15" is a property
  of the system rather than a hope about model latency.

  Corollary worth stating: the LLM is now a *quality* dependency, never an
  *availability* dependency. Any future feature that makes the model
  load-bearing for delivery re-opens this ADR.

## Alternatives considered

- **Rotating human sign-off.** Rejected by deletion of the gate; incompatible
  with a hard 07:15 send without a staffing commitment.
- **Send unreviewed, with a reviewer able to issue a correction afterwards.**
  Not discussed; likely inherits the "nobody re-reads on a train" problem
  noted in ADR-0001.
