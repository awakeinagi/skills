# ADR-0001 — Timeliness outranks completeness

**Status:** accepted
**Date:** 2026-08-07
**Evidence:** conversation round 2; canvas saves `f1861c2`, `c007f5c`

## Context

The Morning Tearsheet is read between 07:30 and 08:00, mostly on phones, by
people commuting. Its usefulness collapses to roughly zero once the market
opens at 09:30 — a perfect report delivered at 09:40 is not a late report, it
is a wasted one.

The pipeline depends on several external sources (market data, the positions
file, news, an earnings calendar), each of which can be slow or down on any
given morning. The default engineering instinct in this situation is to retry
and to block delivery until the inputs are complete.

The team has been burned before by exactly that behaviour in a vendor report.

## Decision

**The 07:15 send is a fixed point. Nothing may delay it.**

Concretely:

1. A **Cutoff** at 07:00 freezes the input set. Whatever has arrived is what
   the report is built from. Nothing is waited for past this moment.
2. Sections whose inputs missed the Cutoff are **still rendered**, marked
   unavailable and carrying the reason ("risk unavailable — vendor timeout").
   They are never silently omitted; a vanished section reads as a broken tool
   and destroys trust in the whole sheet.
3. Every failure mode resolves toward "send something at 07:15". There is no
   path in the system whose outcome is "send later".

## Consequences

**Accepted:**

- The team will, some mornings, receive an incomplete tearsheet. This is the
  intended behaviour, not a defect, and must not be "fixed" by a well-meaning
  retry loop added later.
- Every upstream component needs a hard timeout rather than a retry budget.
  "Retry until success" is incompatible with this decision.
- The 20-minute window between Cutoff and send is a real constraint on the
  analysis and agent steps, not slack. It needs measuring, and it needs its
  own timeout.

**Resolved since (save `3f07023`):**

- The Cutoff alone never solved the case that motivated it — the positions file
  lands around 06:50 but occasionally at 06:58 or 07:02, and a 07:02 morning
  still misses any fixed cutoff. The fix is not a time. A third input state,
  **stale**, was accepted: when today's file is late, the sheet uses yesterday's
  closing book, labelled "positions as of yesterday's close". Yesterday's book
  is nearly right because nothing traded overnight.

  This applies only to inputs that degrade honestly. It does **not** apply to
  prices: a stale quote does not announce itself as stale, it simply misstates
  the market, and a wrong number is worse than an absent one.

  With this in place the exact Cutoff minute stops being load-bearing for
  positions, which removes the pressure to keep pushing it later — the pressure
  that would otherwise have eroded this ADR one exception at a time.

## Alternatives considered

- **Retry until complete, send when ready.** Rejected — this is precisely the
  vendor-report behaviour the team was burned by.
- **Send a placeholder at 07:15 and a corrected sheet later.** Rejected
  implicitly: a second sheet arriving after people have read the first is
  worse than an honest gap, and nobody re-reads on a train.
- **Move the whole run earlier.** Rejected — earlier means less pre-market
  information, which degrades the product on every normal morning to protect
  against a rare one.
