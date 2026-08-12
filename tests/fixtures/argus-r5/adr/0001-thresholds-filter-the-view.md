# ADR 0001 — Thresholds filter the view; they do not gate the computation

**Status**: accepted (2026-08-10), re-confirmed the same day against the
analyst's own triage process — "rerun at 0.65" there is desk shorthand for
moving the slider, not a recompute
**Evidence**: canvas saves `0007`–`0010`; pin `pin-threshold-scope`

## Context

The admin console exposes two thresholds — risk and confidence. Drawing the
console next to the pipeline flows surfaced a contradiction nobody had noticed
in prose: the console offers "rerun an individual pipeline with new settings",
but a Pipeline is one source end to end, while both thresholds live *downstream*
of the point where all four sources merge. Risk belongs to the Risk Model,
confidence to the Aggregator — both after the fan-in.

So "rerun the EDGAR pipeline with a new confidence threshold" had no coherent
meaning. Three readings were possible:

1. Thresholds are compute settings, and moving one re-runs the aggregate for
   every Name.
2. Pipelines carry their own per-source settings, and the two thresholds are
   unrelated controls that happen to share a screen.
3. Thresholds are display filters over results that already exist.

## Decision

**Reading 3.** Every Name is scored on every run regardless of either
threshold. The thresholds decide what is *shown*. Moving one re-filters
Assessments that already exist; nothing recomputes. The console's Apply button
commits the two sliders and nothing else.

## Consequences

**We always pay to score the whole universe.** The cost of a run is fixed and
independent of how selective the analyst is feeling. A tighter threshold saves
nothing.

**In exchange, no question costs a recompute.** "What would I have seen at 0.4?"
is answered instantly, on data already on disk, including retrospectively. Under
reading 1 that question costs a full run and cannot be asked about the past at
all.

**This will be re-litigated.** Every analyst's first instinct is "lower the
threshold and rerun", and every finance lead's first instinct on seeing the
compute bill is "why are we scoring names we filter out?" Both questions have
the same answer and it is the trade-off above.

**It changes what a threshold *is* in the domain.** A threshold is not a
property of the Aggregator or the Risk Model. It is a property of the view. If
a future screen wants per-user thresholds, that is now a cheap change rather
than a pipeline change — which is a real second-order benefit and worth
remembering.

**Not decided here**: whether "rerun a pipeline with new settings" refers to any
settings at all. With thresholds excluded, no per-pipeline setting has yet been
named. Open.
