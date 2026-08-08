# Morning Tearsheet — glossary

Canonical vocabulary for this project. The domain diagram (when we draw one)
maps to these terms; a divergence between a diagram label and an entry here is
a tripwire to talk about, not something either side syncs silently.

Started round 2. Cite the save short-id where a term was settled.

## Terms

**The book** — the team's own positions, loaded fresh every morning. It is
the first step in the pipeline because it scopes everything downstream: which
names get news pulled, which get analysed, whose earnings land on the
calendar. *(settled 007e76f)*

**Tearsheet** — the morning deliverable. Four named sections plus, provisionally,
a synthesis block. Read by PMs on phones in the ten minutes before the 08:00
meeting. *(settled 4ea9f68)*

**Cutoff** — 07:30, hard. Whatever is ready ships; anything missing is flagged
in place. The governing principle is *late is worse than incomplete*, because
after 08:00 the meeting has started and nobody reads it. *(settled 007e76f)*

**Top movers** — the largest overnight moves within the book, each carrying a
one-line explanation of why. Book-scoped, not market-wide. *(settled 007e76f)*

**Include-and-flag** — the news policy. A story the agent is unsure is relevant
gets included and marked low-confidence rather than dropped. Skimming a little
noise beats missing something that moves a position. *(settled 007e76f)*

**Omit-and-flag** — the numbers policy, and deliberately the opposite of the
news policy. A number or claim the agent is unsure of is never printed; the
slot shows the gap instead. A confidently wrong number in front of a PM is the
worst outcome the system can produce. *(settled 007e76f)*

**Advisor, not reporter** — the sheet leads with "Needs a Decision Today". The
agent makes calls rather than only summarising. *(settled 2d8d88e)*

**Cite-your-section** — the constraint that makes the advisor role safe: every
item in the advisory section names the section it came from. No free-floating
opinions. *(settled 2d8d88e)*

**Abort-and-page** — the floor under "late is worse than incomplete". If the
book itself fails to load, the run aborts and pages on-call rather than
shipping a sheet of "unavailable". No sheet beats an empty sheet. *(settled ba24733)*

**Never-stale** — the sheet never falls back to yesterday's content. Stale
numbers dressed up as fresh is the one unforgivable failure. An unfinished
section is flagged unavailable exactly like missing data; the deadline does not
care why a section is empty. *(settled ba24733)*

**Market data** — index, FX, rates, prices *and* the macro calendar, one
vendor, pulled off the cron in parallel with the book. Deliberately *not*
scoped to holdings: the macro context is needed every morning regardless of
what the book holds. Only news is position-scoped. *(settled 2d8d88e)*

**Followable citation** — an advisory item links through to the story or the
number it rests on. A text label naming the section is explicitly *not* enough:
a citation the reader cannot follow is decoration. Constrains the delivery
format — the sheet cannot be flat text. *(settled round 3)*

**Rule precedence** — where the two uncertainty policies collide, the advisory
block follows the *news* rule, carrying the low-confidence marker through into
the advice ("consider trimming X, based on unconfirmed report, from News"). The
numbers rule stays absolute for figures themselves, including figures inside
advisory items. **An opinion can be hedged; a number can't.** *(settled round 3)*

**Partial advice** — when inputs are incomplete the advisory section still
runs, marked partial. It is never suppressed: incomplete data is exactly when
the read is most needed. *(settled round 3)*

**Ops rotation** — awake from 06:30 covering the Asia close, so they take the
06:50 page. A book reload inside forty minutes is realistic, which is what
makes abort-and-page a genuine recovery path rather than a euphemism for "no
sheet today". *(settled round 3)*

## Decisions recorded

- `adr/0001-advisor-not-reporter.md`
- `adr/0002-never-send-yesterdays-sheet.md`

## Open

- Delivery channel for "send to PMs". Device is settled (phones); the channel
  is not — and ADR 0001's followable-citation requirement now constrains it,
  since the format must support resolvable links.
- The 45-minute window (06:45 cron → 07:30 cutoff) has never been checked
  against real runtimes for a news agent working over the whole book.
- Whether "top movers" has a fixed count or a threshold.
