# Morning Tearsheet — glossary

Canonical vocabulary for this project. The domain/flow diagrams map onto these
terms; when a diagram label and a term here disagree, this file wins until we
agree otherwise.

## Terms

**Morning Tearsheet** — the single daily report sent to the whole investment
team before the open. One sheet for everyone, not one per person. (Per-PM
sections are a someday, explicitly not being built for. Save `74a9380`.)

**Hard Send** — 07:15 ET. The report goes out at this time regardless of what
is or is not ready. Not a target; a constraint everything upstream is designed
around. Readers open it 07:30–08:00, mostly on phones, commuting.

**Cutoff** — 07:00 ET. The moment the input set is frozen. Whatever has landed
by then is what the report is built from; anything that hasn't is not waited
for. The Cutoff exists to make Hard Send achievable rather than aspirational.
(Was 06:55; moved to 07:00 because the positions file typically lands ~06:50
and sometimes later. Save `c007f5c`.)

**Unavailable** — the state of a report section whose inputs missed the Cutoff.
The section is **still rendered**, carrying the reason: "risk unavailable —
vendor timeout". Never silently omitted — a vanished section reads as a broken
tool. A partial sheet on time beats a complete sheet late.

**Stale** — accepted third input state (save `3f07023`). An input served from
yesterday's data, explicitly labelled as such: "positions as of yesterday's
close". Applies to Positions, because yesterday's closing book is nearly right.
Does **not** apply to prices — a stale quote misrepresents itself as current,
which is worse than a gap. Stale is never a hole and never silent.

**Position** — one Instrument, a quantity, a cost basis, and an `as_of`. The
`as_of` is not decoration: it is what makes Stale representable rather than a
hack. Load-bearing beyond P&L — the position list defines relevance for
Holdings News and for the Earnings Check.

**Instrument** — the tradable line: ticker or ISIN. Several per Issuer.

**Issuer** — the company. **News and Earnings Events attach here, not to the
Instrument.** Holding several instruments of one issuer must still produce one
earnings block. Confirmed real: the book routinely holds options on names it
also holds equity in, so Issuer and Instrument are genuinely different objects
(save `a7b965d`).

**Price Point** — one Instrument, one timestamp, one *kind*: prior official
close, pre-market last, or official book mark. "Price" unqualified is banned in
this project; every use must say which. (Open — pin `pin-which-price`.)

**News Item** — one wire story as received.

**Story** — accepted. A cluster of News Items about one actual event, so that ten
wire items become one line rather than ten, and the clustering is an explicit
owned step rather than a silent choice by the model.

**Today** — the sheet's only forward-looking content: scheduled economic prints
("CPI 08:30"), Fed speakers, and held names reporting *after today's close*.
Accepted in principle and not yet drawn — no pipeline step produces an economic
calendar. Its placement is unsettled and contradictory: described as possibly
the most-read line on the sheet, but currently specified inside Macro News,
below the fold. Resolve before building.

**Metric** — a computed or fetched figure (P&L, exposure, VaR). Each has a
definition and a lineage.

**Section** — a region of the Tearsheet. **Availability lives here**, not on the
Tearsheet: each Section is independently fresh, stale, or unavailable.

**Run** — one morning's execution. Holds the Cutoff, the Hard Send, and the
record of what went wrong.

**Fallback Renderer** — the deterministic, model-free template that ships the
computed numbers if the writing agent misses its slot. Its existence is what
makes Hard Send true rather than merely intended.

**Holdings News** — news retrieved *per ticker we hold*. Depends on Positions.

**Macro News** — Fed, CPI, geopolitics. Deliberately independent of Positions,
so it runs straight off the trigger and never blocks on the book loading.

**Earnings Morning** — a day on which a held name reported after yesterday's
close **or** reports before today's open. Both windows count. On such days the
earnings block replaces Top of Sheet and everything below demotes one place.
The reporting name **keeps** its Holdings News line, kept short — people scan
that section as a checklist of every held name, so a missing line reads as a
bug rather than as editing (save `53b40a9`). Detected by deterministic calendar
lookup against Positions, never left to the agent to notice.

**Top of Sheet** — the agent-written 3-line synthesis at the head of a normal
morning's tearsheet. Absent on an Earnings Morning, where the earnings block
takes its place.

**Thin coverage** — the agent's required hedge when it is unsure. It states
what it knows and flags what is thin ("coverage thin on this one"). It never
guesses and never silently drops an item.

## Standing principles

- **Timeliness outranks completeness.** Established firmly by the user from
  prior experience with a vendor report. Every failure mode resolves toward
  "send something at 07:15", never toward "wait and retry".
- **The agent is never responsible for spotting salience it could be told
  about.** Earnings is the first instance: it is looked up, then handed to the
  agent as an instruction, rather than being something the model must infer
  from the news stream.
- **No human is in the loop before send.** The review gate was proposed and
  deleted (save `74a9380`). Consequences of this are the pipeline's problem to
  handle, not a reviewer's.
- **Nothing is ever silently missing.** Unavailable says why; Stale says as-of;
  thin coverage says it is thin. An invisible gap is the only failure mode that
  destroys trust in everything else on the sheet.
- **The tearsheet is a renderer, not a calculator** — for any figure that has
  an owner elsewhere. Confirmed and now concrete: VaR comes from the nightly
  risk run (available by 06:00) and is **fetched, never recomputed**. Two
  implementations of one metric will eventually disagree in public, and the
  tearsheet would lose that argument. See `tearsheet-contents.md`.
- **Prose sits above the numbers.** The agent-written synthesis is the product,
  not a garnish on it. Defensible only because of what stands behind it: the
  never-guess rule and the Fallback Renderer, which make a model failure cost
  prose rather than the sheet.

## Documents

- `tearsheet-contents.md` — what is in each section: definitions, sources, and
  behaviour when degraded. Seeded with unagreed guesses, marked as such.
- `adr/0001-timeliness-outranks-completeness.md`
- `adr/0002-no-human-review-before-send.md`
