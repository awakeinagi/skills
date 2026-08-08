# ADR 0002 — The tearsheet never falls back to yesterday's content

**Status:** accepted
**Date:** 2026-08-07
**Evidence:** saves 007e76f (cutoff policy), ba24733 (failure modes view).

## Context

The pipeline fires on cron at 06:45 and must deliver by 07:30, before an 08:00
meeting. That is a 45-minute window covering two source pulls, deterministic
analysis, a news-summarising agent working over the whole book, and a
report-writing agent. Several things can miss the window: a feed can be late,
the writer can still be working, the book can fail to load.

The obvious availability move — serve the last good sheet when today's is not
ready — is available and was rejected.

## Decision

**Nothing stale ever ships.** There is no fallback to a previous day's
content, in whole or in part.

The rules that follow from it:

- **At 07:30, whatever is finished goes.** Unfinished sections receive exactly
  the same treatment as missing data: present, named, marked unavailable. The
  deadline does not care *why* a section is empty, so a late writer and a late
  feed converge on one mechanism.
- **Late is worse than incomplete.** After 08:00 the meeting has started and
  nobody reads it, so an incomplete sheet on time beats a complete sheet late.
- **Except when the book fails.** That is the floor. Without positions, three
  of four sections have nothing behind them, and a sheet of "unavailable" is
  worse than no sheet. The run aborts and pages on-call instead. This is a real
  recovery path, not a euphemism: ops rotation is awake from 06:30 covering the
  Asia close, and a book reload inside forty minutes is realistic.

## Consequences

- Availability is deliberately traded for trustworthiness. Some mornings there
  will be no tearsheet at all, and that is the intended behaviour.
- Freshness needs no reader-side verification. Anything on the sheet is from
  this morning's run, so the sheet needs no "as of" hedging per section — only
  gap markers.
- The unavailable-section treatment is load-bearing UI, not an error state
  bolted on late. It appears on a normal morning whenever any input is slow,
  which is why the degraded sheet is drawn as a first-class variant.
- The 45-minute window has not been validated against real runtimes. If the
  writer routinely misses it, this ADR forces the fix into the pipeline
  (faster/staged writing) rather than into a stale fallback.

## Alternatives rejected

- **Serve the last good sheet when today's is not ready.** Rejected outright:
  stale numbers presented as fresh is the one unforgivable failure, because a
  PM cannot tell by looking.
- **Hold delivery until complete.** Rejected: after 08:00 the artifact has no
  reader.
