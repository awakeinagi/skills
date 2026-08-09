# Argus — shared vocabulary

The words we use for Argus, and the numbers that are settled. This file is
canonical: if a diagram and this file disagree, the disagreement is a
conversation, not a typo.

**New here?** Read this file first, then `docs/handover.md` — it says
which diagram to open in what order, what is settled, what is still an
open question, and which boxes are drawn but not built.

## Terms

**Portfolio**: the set of holdings Argus is run against. Holds Positions.
Lives in the Portfolio DB — the one DataSource we own.

**Position**: one holding inside a Portfolio. A Position is of exactly one
Instrument.

**Instrument**: the tradable thing a Position is of.

**DataSource**: an upstream feed Argus pulls from. **Five configured, four
on**: the market feed, SEC EDGAR, the news sentiment stream, and the
Portfolio DB are on; the macro calendar (Fed days, CPI prints) is
**switched off** as noisy. Off is a state, not a deletion — the console
toggles it, and Fed-day surprises are the acknowledged reason it might go
back on. All sources go through the same normalize step. A DataSource
feeds PipelineRuns.

**PipelineRun**: one execution of the pipeline. Scheduled weekdays at 06:00,
before the open; also startable by hand from the admin console with
different settings. A PipelineRun emits Signals.

**Signal**: what a PipelineRun emits — the output of enrichment and scoring
for one name. Signals inform Reports. A Signal carries a confidence; below
the signal confidence threshold it goes to the **Review queue** rather
than to the output. It is never silently dropped.

**Review queue**: where sub-threshold Signals are held for a human to
vet. A non-empty queue **blocks publication** — nothing unvetted reaches
a client, ever (ADR 0003). Vetting either promotes a Signal to the output
or rejects it.

**Excess Return**: the client-facing name for what the desk calls alpha —
return against a benchmark. Same number, plainer word, because clients
kept asking what alpha meant. Use this name everywhere a client can see
it.

**Report**: a document cut from Signals. Three of them: the Weekly Brief,
the Risk Deep-Dive, and the Signals Digest. All three are cut from the same
daily run, at different cadences. A Report **opens in place** from the
dashboard — reading one must not require downloading a PDF. Whether the
page or the PDF is the primary form is open.

**Insider cluster**: three or more *distinct* insiders buying inside a
10 trading-day window. Size does not gate the cluster — it weights the
score. This is a formula, not a judgement, which is why it belongs beside
the other thresholds in the admin console. A **Conviction** signal.

**Risk**: deliberately narrow. A fundamentals-and-tone number, computed
from the sentiment scorer and the fundamentals ratios only. Momentum and
insider buying are *not* risk. The Risk Deep-Dive is named after this
narrow number.

**Conviction**: the other half of the judgement — technical signals and
insider clusters, including whatever the macro calendar contributes. Risk
and Conviction are computed on separate paths and meet only at the
aggregator.

## Routing — which source reaches which enricher

Settled, and the rule the whole design hangs on: **news must never touch
technical signals.** Momentum and breadth come off the market feed and
nothing else; headline sentiment leaking into a momentum read has flipped
a breadth call twice this quarter.

| Source | Reaches |
|---|---|
| SEC EDGAR | sentiment scorer (filing tone), insider detector, *(and fundamentals — unconfirmed)* |
| News | sentiment scorer only |
| Market feed | technical signals only |
| Portfolio DB | nothing — it **scopes** the run to our names |
| Macro calendar | off |

Normalization happens **per source**, not into one pool. Each source keeps
its identity through it.

**Crowding**: portfolio-level concentration — several Positions on the
same side of the same signal. Nobody computes it today. Drawn on the
Signal Formation view as an unbuilt stage, deliberately, because Risk is
narrow by design and can never see it.

## Settled values

These are decided and should not drift without a conversation. The
console tunes **one router and one alarm** — neither of them discards
anything.

| Value | Setting | Kind |
|---|---|---|
| Signal confidence threshold | 0.70 | **router** — below it, a Signal goes to the Review queue, which blocks publication until vetted |
| VaR alert threshold | 2.5% | **alarm** — nothing is dropped; crossing it tells the analyst |
| Schedule | weekdays 06:00, before the open — never weekends | |
| Insider cluster | 3+ distinct insiders, 10 trading-day window | |

## Cadence

Every run produces everything; the **cut** decides what goes out that day.
Nothing accumulates across days — Monday's Brief is Monday's numbers, not
the week's.

| Report | Cut |
|---|---|
| Signals Digest | every weekday |
| Weekly Brief | Monday's cut |
| Risk Deep-Dive | first-of-month cut |

## Distribution

| Report | Analyst | Clients |
|---|---|---|
| Weekly Brief | yes | yes |
| Risk Deep-Dive | yes | yes |
| Signals Digest | yes | **never** |

Withheld by practice, not by policy. Nothing in the design enforces it.

## Open, parked

Deliberately without an answer. Parked, not forgotten. The full list,
with what hangs on each, is in `docs/handover.md`.

- **The 09:28 problem.** The queue isn't clear and the open is two
  minutes away. Publish nothing, publish partial and say so, or publish
  and chase? "Nothing unvetted" and "before the open" cannot both be
  absolute. This absorbed the older late-source question — a source that
  never answered is the same shape.
- **Rerun versioning.** A Monday rerun re-cuts Monday's Brief. If that
  Brief already went to clients, there are two versions of a document
  with one name.
- **The dashboard's other half.** Portfolio value, Excess Return, the
  equity curve, the sector heat map and drawdown need a benchmark series,
  a history of portfolio value, and a sector on every Instrument. No
  DataSource supplies any of the three.
- **What an Instrument carries.** The vocabulary stops at "a Position is
  of an Instrument". The heat map proves that is too thin.
- **The dashboard's other half.** Portfolio value, Alpha, the equity
  curve, the sector heat map and drawdown are all on the morning screen
  and **none of them is an output of the pipeline described above**. They
  need a benchmark series, a history of portfolio value over time, and a
  sector for every Instrument. No DataSource supplies any of the three.
  Either there is an unnamed source and store, or there is a second
  system and the dashboard is two products sharing a screen.
- **What an Instrument carries.** The vocabulary says a Position is of an
  Instrument and stops. The heat map proves Instruments need at least a
  sector, and reference data rarely arrives alone.
