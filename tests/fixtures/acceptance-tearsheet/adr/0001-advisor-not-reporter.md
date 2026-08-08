# ADR 0001 — The tearsheet is an advisor, not a reporter

**Status:** accepted
**Date:** 2026-08-07
**Evidence:** saves 4ea9f68 (section seeded), 2d8d88e (role confirmed), and the
round-3 ruling on rule collision.

## Context

The stated purpose of the morning tearsheet is to tell PMs two things before
the 08:00 meeting: what changed overnight, and what needs a decision today.
The four sections originally specified — P&L snapshot, top movers, news by
holding, macro calendar — all answer the first question. None answers the
second.

That left a fork. Either "what needs a decision" is something a PM derives for
themselves from the four sections, which makes the system a reporter; or the
agent states it, which makes the system an advisor. The distinction is not
cosmetic: an advisory line read on a phone eight minutes before a meeting will
influence positions.

## Decision

The sheet leads with an explicit **Needs a Decision Today** section, placed
above the fold. The agent forms and states views rather than only summarising.

Three constraints make the role tolerable:

1. **Cite your section.** Every advisory item names the section it was derived
   from. No free-floating opinions.
2. **Citations are followable.** A text label is not sufficient. Each advisory
   item links through to the underlying story or number. A citation the reader
   cannot follow is decoration.
3. **Opinions may be hedged; numbers may not.** Where the two uncertainty
   policies collide, the advisory block follows the *news* rule
   (include-and-flag), carrying the low-confidence marker through into the
   advice — "consider trimming X (based on unconfirmed report, from News)".
   The numbers rule (omit-and-flag) stays absolute for figures themselves,
   including figures appearing inside advisory items.

When inputs are incomplete the section still runs, marked partial. It is not
suppressed: incomplete data is precisely when a PM most needs the read.

## Consequences

- The system carries advisory liability it would not carry as a reporter. The
  citation chain is the audit trail when an item turns out to be wrong, and is
  therefore load-bearing rather than a nicety.
- Every advisory item needs a resolvable link target, which constrains the
  delivery format: the sheet cannot be flat text.
- Two uncertainty policies now coexist by design rather than by accident. Any
  future change to either must be checked against this ADR — the asymmetry
  ("an opinion can be hedged, a number can't") is the decision, not an
  oversight.
- Expect re-litigation the first time an advisory item is wrong in front of the
  desk. The question to return to is whether the citation chain worked, not
  whether the agent should have had an opinion.

## Alternatives rejected

- **Reporter only.** Simpler and lower-liability, but leaves half the stated
  purpose unserved and pushes synthesis onto a PM with eight minutes.
- **Suppress advice when inputs are incomplete.** Rejected because it removes
  the read exactly when data gaps make it hardest to form independently.
