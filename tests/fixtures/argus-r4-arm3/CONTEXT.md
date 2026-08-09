# Argus — glossary

Terms settled in conversation. This file is canonical: when the drawings and
this file disagree, we have a decision to make, not a typo to fix.

## The run

**PipelineRun**: One execution of the pipeline for a given trading day;
scoring starts 06:00 on weekdays. Produces a complete set of Signals plus
the settings snapshot it ran under — the snapshot is what makes a Rerun
comparable to its original rather than a replacement. A PipelineRun is
never overwritten. Staleness is currently recorded here, at run level;
signal level is wanted but uncosted.

_Avoid_: Run (settled — PipelineRun is the word the team uses out loud).

**Rerun**: A second Run over the same day with different settings, started
from the admin console. It sits *beside* the original rather than replacing
it — numbers someone has already read stay readable. The Dashboard follows
the newest Run. A Report that has already been published is frozen: a
Rerun never reissues one.

A Rerun **is** a PipelineRun, not a separate kind of thing: it points at
the PipelineRun it re-ran (`rerun of 0..1`, a self-relationship). An
original points at nothing. That lineage is what makes "the Dashboard
follows the newest Run" answerable — newest *within a lineage*.

**Partial rerun**: Re-scoring a single enrichment pass and leaving the
other three as they were. Its Signals then come from two different
moments, and the PipelineRun's settings snapshot describes the whole day
when it only describes a quarter of it. **Known defect, not a design** —
an accident of how the console got built. A full rerun ("re-run ALL") does
a whole day properly and has no such problem.

**Cutoff**: 05:45. Source data not present by the Cutoff is not used for
that Run; the Run proceeds with Last-Good data instead. The Run is never
held: missing the open is worse than being slightly stale. Fetching starts
05:30 and retries inside that window; 06:00 is when *scoring* starts, not
fetching. The Macro Calendar is not behind this gate.

**Last-Good**: The most recent successfully ingested data from a source,
used in place of fresh data when that source misses the Cutoff.

**Stale**: The condition of anything derived from Last-Good rather than
fresh data. Surfaced as a flag on the Dashboard, and stated in the Signals
Digest.

## What gets analysed

**Watchlist**: The ~400 names Argus screens, held or not. The Watchlist —
not the portfolio — defines the universe; catching something before we are
in it is the point.

**Portfolio**: What we actually hold. Joined at the risk model for position
sizing, exposure and P&L. It is not a filter on the Watchlist.

## Settings

**Confidence Threshold**: 0.70. A **gate**, not a label. A Signal below
0.70 still exists internally; it reaches nothing a client sees. A scorer
that fails returns an *error*, never a zero — an absent score is not a
bearish one.

**Risk Alert**: Fires when 1-day 95% VaR crosses 2.5%. Surfaces on the
Dashboard the same day; it does not wait for the monthly Risk Deep-Dive.

## The model

**Instrument**: The tradable thing — ticker and asset class. Exists whether
or not we hold it.

**Position**: Our holding in one Instrument — quantity and cost basis. A
Portfolio holds many. Splitting Position from Instrument is what lets
Argus talk about a name we do not own.

**DataSource**: One of the five feeds. Market Feed, SEC EDGAR, News
Stream, Portfolio DB, Macro Calendar.

**Macro Calendar**: Fed dates, CPI prints and other scheduled macro
events. The fifth source, added long after the original four. It feeds the
aggregator directly — it does not pass the Cutoff or touch enrichment.
**Switched off by default since Aug 2026** — mostly noise outside Fed
weeks — but still wired in as a config option. Deliberately *not* drawn on
the run flow; the Aggregator's tooltip carries it, and the console
checkbox is the only place it appears in the product.

**Signal**: What a Run emits — a kind and a confidence, about an
Instrument.

**Report**: A published document with sections and a period. Once
published, frozen.

**Dashboard**: The live surface. Nav, four KPIs, the reports shelf, then
the charts. Follows the newest PipelineRun. Reading order is deliberate:
numbers, then the way into the Reader, then charts last.

**Excess Return**: Return minus benchmark. A simple difference, **not**
risk-adjusted. Labelled *Alpha* until Aug 2026, which overclaimed — alpha
implies a beta adjustment this figure does not make, and clients kept
asking what it meant.

_Avoid_: Alpha (settled — it was inaccurate, not merely jargon).

**Reader**: The report opened over the Dashboard, without downloading it.
People spend more time here than on the Dashboard itself — by that measure
it is the primary surface of the product, not a viewer. Download exists to
*send* a report to someone, not to read it. Reports are PDFs today, which
is regretted: nobody can link to a section.

**Admin Console**: The control surface. Source checklist, schedule,
threshold sliders, per-pipeline and whole-pipeline rerun, last-runs panel.
It is not part of a PipelineRun; it reaches into one.

## Deliberately open

Two questions are parked by choice, not by oversight, and their pins stay
on the canvas:

- **Is Watchlist one list or one per strategy?** A live team disagreement.
- **Is a Risk Alert a Signal with a kind, or its own concept?** No clean
  answer yet; not to be invented.
- **The Macro Calendar never passes a confidence gate.** Because it
  bypasses enrichment it gets no confidence score, so the 0.70 threshold
  cannot touch it — which is a structural reason for the noise that got it
  switched off. Rebuilding it as a scored signal would give it a dial
  instead of a switch. Parked; not this quarter.

**Review queue**: Where a Signal below the Confidence Threshold goes. It
is not discarded — a person looks at it, and approved signals rejoin the
pipeline. **Non-negotiable: no unreviewed low-confidence Signal may reach
a client**, so an uncleared queue blocks publication.

*(Whether a Signal formally carries an approval state, or "sits in the
queue, unlinked" suffices, is deferred pending compliance.)*

**Publication**: Getting a Report to a client. Review gate first, then the
calendar decides what is due, then render, then freeze. The review gate is
the only thing in Argus that can wait on a human.

**Aggregation**: Turning Scored Signals into the one view the Dashboard
and all three Reports read from. Confidence gate, combine per instrument,
risk model with the Portfolio joined, risk alert gate. The *combine* step
has never been specified — see the pin on it.

## The products

**Weekly Brief**: PDF. Published Monday morning.

**Risk Deep-Dive**: PDF. Published the first business day of each month.

**Signals Digest**: PDF. Published daily. Carries the Stale statement when
a source missed the Cutoff.
