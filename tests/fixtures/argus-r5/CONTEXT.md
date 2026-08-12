# Argus — glossary

Argus is a document generator: a scheduled pipeline whose product is a thing
someone reads. It runs every weekday at 06:00, before the US open.

**Argus computes; the analyst decides.** There is no judgement inside the
pipeline beyond one LLM scoring sentiment — deliberately. The design intent is a
deterministic machine with one soft edge, read by a human who makes the call.

These are the terms as **you** have used them, not as I would name them.
Correct anything that reads wrong — the drawings are keyed to this file.

**Run**: one 06:00 weekday pass over all four sources, end to end. 06:00 is a
deadline rather than a start time: output that lands after the open is a
different product from output that lands before it.

**Enrichment**: the stage holding the four scorers — sentiment, fundamentals
ratios, technical signals, insider-cluster detection. Each scorer emits its own
Signal.

**Signal**: what a single scorer emits. Four scorers, four signals; they meet
at the Aggregator rather than travelling on separately.

**Sentiment Scorer**: an LLM reading news headlines and SEC filing tone. The
only model in the pipeline — the other three scorers are formulas. Filing tone
counts as sentiment, not as a separate score.

**Insider Cluster Detector**: watches SEC Form 4s for clusters. A formula, not
a judgement.

**Aggregator**: where the four Signals meet, and what the Risk Model reads —
the Risk Model does not read the scorers directly. Owns the confidence
threshold.

**Contrarian Screen**: **wishlist — discussed, never built.** A check for
crowded trades that would read the Aggregator's output and knock a Signal back.
Removed from the enrichment drawing on 2026-08-10 because the drawings describe
what exists. The term stays here because people say it; nothing implements it,
and nothing else covers crowded trades either.

**Risk Model**: reads the aggregate and joins it against the Portfolio DB for
exposure and concentration. Owns the risk threshold.

**Portfolio DB** / **the book**: our own holdings. **Not a feed and not a
Pipeline** — it is never pulled at 06:00, never toggled off, and has no rerun.
The Risk Model reads it directly for exposure and concentration. It does *not*
decide what gets scored; the whole universe is scored regardless.

**Pipeline**: one source, end to end. **Three** of them — Market Feed, EDGAR,
News. "Rerun the EDGAR pipeline" means re-pull EDGAR and re-run everything
hanging off it, both scorers. Nobody reruns a single scorer, and nobody reruns
the whole morning by hand. *Note: the pipelines are not disjoint — the Sentiment
Scorer hangs off both EDGAR and News. Open question.*

**Scorer**: one of the four things Enrichment runs. Three are formulas, one is
a model.

**Report**: one of the three PDFs — Weekly Brief, Risk Deep-Dive, Signals
Digest.

**Name**: the thing a Signal is about — the unit of "the whole universe gets
scored". The company, as the desk says it.
_Avoid_: instrument, security

**Ticker**: the code. Distinct from the Name, which is the company — so a Name
may carry more than one Ticker.

**Assessment**: what the Aggregator produces for one Name in one Run — the four
Signals combined, after the Contrarian Screen. Distinct from Signal, which is
what one Scorer alone emits. Keeping the two apart is deliberate.

**Holding**: one line of our book against one Name. What exposure and
concentration are computed over.
_Avoid_: position

**Risk threshold**: a **display filter, not a compute setting**. Set on the
Admin Console; committed by Apply.

**Confidence threshold**: also a display filter. Everything is scored
regardless of either threshold — they decide what you are *shown*. Moving one
re-filters Assessments that already exist; nothing re-runs.

*Those two entries are the canonical statement of what a threshold does.
Element tooltips are courtesy copies — when this changes, grep the tooltips.*

**Source switch**: the per-Pipeline toggle on the Admin Console. Deferred —
takes effect on the next morning's 06:00 run, not immediately. Unlike Rerun,
which is immediate.

**Run retention**: runs sit **alongside** each other, not overwritten — the
06:00 numbers are still retrievable after a rerun, because the analyst compares
against them. There is no comparison feature: it is done in two browser tabs and
memory. That is the part that does not survive a handover.

## Open questions

Recorded here so they survive the session, not because they are settled.

- **Is there a cutoff?** EDGAR is late about twice a month. Nothing established
  says Argus waits, gives up, or notices.
- **What should happen when a source is late?** Today: nothing. The run ships
  unmarked and the analyst spots it in the numbers, days later.
- **The Sentiment Scorer straddles two Pipelines** (EDGAR and News), so
  "rerun the EDGAR pipeline" recomputes something half-owned by News — and it is
  the one scorer that can return a different answer from identical inputs.
- **What does the Sentiment Scorer emit when it is unsure?**
- **Source switch vs Rerun on the same console row**: one is deferred to
  tomorrow, the other is immediate. What does Rerun respect?
- **Nothing covers crowded trades** since the Contrarian Screen was never built.
- **Nothing records what a run differed from**, so "which knob moved it" is
  unanswerable from inside Argus. Same hole as the silent degradation, seen from
  the other end: a run manifest (sources present, settings used, scorers that
  ran) would answer both.
- **"Rerun everything"** is a branch of the triage, but round 3 established that
  nobody reruns the whole morning by hand. Three button presses, or an exception?
- **Name vs Ticker cardinality**: if a Name can carry several Tickers, a Signal
  is about the company but a Holding is probably about the listing.
