# Capability assessment — round 2 (against v0.5)

Re-test of `skills/wysiwyg-grilling` after commits `52c9879` (WP1–WP2) and
`a3c1a6b` (WP3–WP7). Method: `~/docs/optimization/wysiwyg-grilling_v0.5.md`.
Round-1 notes and findings: `capability-assessment-notes-optimizer_R1.md`.

- **Project:** `<scratchpad>/argus2` (empty at start; sha1 `74117024dcf7`)
- **Agent:** fresh subagent, no access to the repo, the prototype, or round-1 notes
- **Human:** me, playing the analyst from `capability-demo-chat-session.md`
- **Length:** 11 rounds, 40 saves, 7 artifacts — closed at revn 40
- **Result:** all 8 re-test items pass; 11 new findings (2 P1, 6 P2, 3 P3)

## Re-test checklist status

| # | Item | Status |
|---|---|---|
| 1 | Held revisions narrate as pending, not drawn; `supersedes` on correction | **PASS** — noticed the silent flip unprompted (R2); `supersedes: 1` verified server-side (R3) |
| 2 | Agent reaches for `apply --check` | **PASS** — used before every substantive apply from R1; caught the round-1 P1 `set_budget` error pre-queue |
| 3 | Rename after a scoped divergence annotation → tripwire fires | **PASS** — differential control run: covered verb silent, uncovered verb fires (see R9) |
| 4 | Agent scopes annotations with `kinds` | **PASS** — `kinds: [entity_renamed, label_renamed, renamed]`, unprompted, with the reason stated a round before it acted |
| 5 | Text-fit `LAYOUT_WARNING` reaches the agent and is repaired same move | **PASS** — 3 arrow-label warnings + 1 `LAYOUT_ERROR` (miracle node) hit `apply --check`, repaired by spreading endpoints; none reached the canvas |
| 6 | Handover offers `export --with-footnotes` + glossary | **PASS** — offered in R7 *before* I raised handover; delivered 5 footnoted SVGs + a reading order in R9 |
| 7 | Agent mentions `▶ walk` / `tidy` / `insert` (canvas-app.md reached) | **PASS** — `+ insert` (R3), `▶ walk` (R5), both unprompted and at the right moment |
| 8 | Final lint count (round 1: 16 notes ≈ 4 causes) | **PASS** — **5** at close, of which 2 are deliberate overrides, 1 is mine (test B), 2 are R2-7 |

## Round log

### R1 — kickoff brief (verbatim from the demo session)

**Agent:** named the archetype unprompted ("a document generator: a scheduled
pipeline whose product is a thing someone reads") and made it falsifiable —
"correct me on that, because it decides what we draw and in what order". Drew
**one** artifact, `daily-run-flow` (9 nodes, 8 arrows), compressed the four
sources and four scorers into single boxes with the detail in tooltips, and
said *why* the compression is provisional ("if the four sources behave
differently at 06:00, the fan-out earns its own view"). Labelled three
elements as its own guesses and invited deletion. Registered `Argus` with
`wireframe` + `domain` as debt and stated it will pay one per round.

Three pins, all on real forks (aggregator-unsure, where the thresholds bite,
do sub-threshold signals survive) — each with a `detail` body that names the
downstream consequence, not just the question. Two more questions in chat that
aren't about any single box (a "Weekly" report from a daily pipeline; 06:00 is
not the deadline, the open is).

**Tooling:** clean. Artifact lint 0/0/0. The single registry note (view debt)
appears **only** in the `registry` bucket — WP4's registry-scope leak is fixed
and visible on the first save (round 1 duplicated registry notes into every
artifact bucket).

**Affordances named to the user:** `🗒 note`, `❓ ask`, tooltips + right-click
to edit, "drag anything, delete anything, double-click labels to rename",
"every Save is a commit". No `▶ walk`/`tidy`/`insert` yet — nothing to walk.

### R2 — settle the numbers; cadence flipped to `pulled`

Canvas half: answered `pin-unsure` from the rail (escalate/block), then
`POST /api/config {"patch": {"canvas_updates": "pulled"}}` — **silently**, no
mention in chat. Deliberate probe: a real user flips that toggle in the UI
without announcing it, and it tests whether the agent notices its work is
being held rather than drawn. Chat half: thresholds 0.70 / 2.5%, schedule
doesn't branch, both gates bite, degraded path ships partial, draw the admin
console next. `pin-held` left open on purpose to age.

**Agent:** noticed the silent cadence flip *unprompted* and led with it — "the
console is drawn but **held in the pending banner** — you flipped the canvas to
`pulled`, and I read that as 'stop moving things under me'". Then did the thing
round 1's agent could not: it declined to prune the `held` box *because* the
cadence was pulled ("I've left it standing and the pin open rather than pruning
something under a pulled cadence").

It also caught a contradiction between my two channels — the rail answer ("a
blocked run is fine") against the chat answer ("we never delay") — and resolved
it structurally rather than asking me to pick: missing *source* ships partial,
uncertain *judgement* blocks, and the flow currently draws both leaving the
Aggregator through identical arrows, "which is the diagram claiming they obey
the same rule". It noticed I had added a party ("clients") that wasn't in its
round-1 1000ft reading. Started `CONTEXT.md` with six settled terms.

It named **Discard** to the user as a real option, with a reason to use it.

**Tooling — the v0.5 surfaces all landed:**

- `apply --check` before *every* substantive apply, four times. On `.batch2b`
  it caught `ERROR=registry op 5: set_budget needs an existing artifact (got
  'admin-console')` → `WOULD_APPLY=false`. **That is exactly the round-1 P1**:
  a `set_budget` registry error that got `{ok:true, queued:true}` and produced
  a permanently dead banner. Now it never reaches the queue. Round 1's agent
  invented a copy-the-project-to-a-temp-dir workaround for this; round 2's
  agent just used the flag.
- The queued response carried `LAYOUT_NOTE`s, so the agent self-corrected 12
  off-grid elements between two check runs before queueing anything.
- `canvas.py lint` used twice, unprompted.
- Registry notes stayed in the `registry` bucket; artifact buckets clean.

**UI (screenshot `r2-banner.png`):** banner reads *"Agent revision waiting on
**admin-console**: … — adds 16, asks 3, records 5. Your unsaved work is safe
either way."* with **Apply now / After I save / Discard**. Header shows
`Round 1+ — your move`. Toolbar exposes `▶ walk`, `tidy`, `+ insert`, `png`,
`context`, `fit`. Every WP6 item confirmed live.

### R3 — kill the held box, a throwaway note, and correct a *queued* batch

Canvas: deleted `held` + its `no` arrow + `pin-held` (answering the pin by
action rather than words), and dropped a throwaway sticky — *"watch out for
crowded trades…"* — the demo's hardest beat. Chat: told it the four
per-enricher rerun buttons are wrong **while the console is still queued**, and
explicitly asked it to fix before it lands. This is the natural, non-cheating
way to force the `supersedes` path (checklist item 1, second half). Also
resolved the escalation split and approved an ADR.

**Agent:** `supersedes: 1` in the batch, verified server-side — entry 1 gone,
entry 2 queued, `PENDING=1`. Its words: *"I superseded the queued revision
rather than adding a second, so there's still exactly one banner and nothing to
un-apply."* **Checklist item 1 is a full pass.** In round 1 the same situation
produced a second banner beside a dead one.

It made the throwaway sticky the headline of the round — *"the one requirement
on the canvas that the pipeline as drawn actively works against"* — and got the
mechanism right: four enrichers feeding one risk model means a crowded position
arrives as three confirmations, and the confidence gate *amplifies* it because
a crowded signal is a high-confidence signal. It pinned rather than resolved,
naming the two designs. It also generalised my correction into a rule ("draw
what exists, not what was discussed") and wrote ADR-0001, then noticed while
writing it that "3 PDF Reports" as one box had become a lie.

It named **`+ insert`** to the user, with a reason ("starter templates and a
fork shape — draw it wrong and I'll read it"). **Checklist item 7: pass** —
`references/canvas-app.md` is being reached and surfaced.

### R4 — apply from the UI

Clicked **Apply now** in the browser (not the API) — landed, `PENDING=0`,
3 artifacts, **3 lint findings total**. Console renders clean and legible; no
text-fit warnings because nothing overflows. Chat: crowding is a fifth stage
that must *lower* confidence, the held-Brief rule, take the domain model, and
seeded the dashboard KPI row (`Alpha`, VaR, Sharpe, hit-rate) to set up the
flagship rename.

### R5 — the deferred path, a user pin, and a rename

**Agent (R4 turn):** drew the domain model and *waived the Q25 note itself*,
diagnosing it correctly — "the 'progress indicator' complaint was the linter
reading `2.5%` as a completion bar, now waived". Independent confirmation of
R2-1: the agent had to spend a waive to silence a false positive. It also
recorded the 10-block budget override with a reason rather than shrinking the
screen.

On substance it committed to a definition of `Signal` instead of pinning a
question mark, then argued against its own choice; pushed back on my ruling
(crowding per-name misses the concentrated-theme case) and was right; left
`vetted?` off `Report` deliberately with the fork stated; and kept `Signal`
**out** of the glossary on principle — *"writing down my guess as canonical
would launder a guess into a fact."* It also declined to draw the contrarian
screen because cadence was pulled and I had unsaved work, while stating plainly
that the flow currently says something I'd told it was wrong.

**Harness:** clicked **After I save** (`DEFERRED=true`), then made a save →
flushed cleanly, `PENDING=0`, revn 11. Added a **user-authored pin** (`❓ ask`,
`author: "user"`) on the aggregator — the user→agent pin direction, untested in
round 1's early rounds. Renamed `Instrument` → `Security` on the domain model;
facts came back `renamed` + `entity_renamed`, no tripwire (correct — nothing
mapped to it yet).

**Planned probe (later round):** queue-time validation now stops invalid
batches from ever queueing, so the only way left to reach WP1b's eviction path
is a batch that goes stale *after* queueing. Delete an element a pending batch
modifies, then Apply — commit must evict and emit `agent_revision_failed`
rather than re-arming. This is a realistic user action, not a synthetic one.

### R6 — the flagship rename

**Agent (R5 turn):** answered the *user's* pin first, gave a concrete answer
(vet from the held report card itself), then argued against its own answer and
pinned it — *"screens where the only destructive action lives among readouts
are where people click things they meant to read."* Drew normal and degraded
frames side by side, and said: **"Two frames means ▶ walk now does something —
it steps the frames like a prototype."** With `+ insert` in R3 that closes
checklist item 7 completely; `▶ walk` appeared in **no** documentation file
before v0.5.

It refused to invent chart contents (citing its own rerun-buttons mistake as
precedent), flagged that nothing on the screen is distinguished by colour and
turned it into an accessibility question rather than a limitation, and asked
"over what period?" for all four KPIs. It also proposed a domain split with a
named seam because three newly-settled entities pushed the model past the
legibility ceiling.

**Registry state:** 8 mappings — but every one has `note: null, kinds: null`.
**Checklist item 4 so far: the agent has not used `kinds`, and has not
annotated any mapping divergent.** No divergence has arisen that would call
for it, so this is not yet a failure; it is a not-yet-reached.

**Harness:** applied the dashboard, then renamed `Alpha` → `Excess Return` on
the *normal* frame only, while telling it in chat to do it "everywhere". The
mismatch between what I did (one label) and what I said (everywhere) is the
demo's flagship beat and the fidelity test: the degraded frame still says
`Alpha`, so 3.2.4 has a genuine same-function-different-label divergence to
find.

### R7–R8 — the banner stack, and the eviction path

**Agent (R7 turn):** wrote ADR-0002 for the alpha/Excess Return split "not to
explain the decision, but to survive the moment in six months when someone
finds the Deep-Dive saying alpha and the Brief saying Excess Return and reaches
for the tidy-up", kept **one glossary entry with both names** so nobody
concludes they're two metrics, and drew the split into the domain rather than
leaving it in prose.

Then, unprompted, it described `kinds` scoping exactly as v0.5 designed it:

> "When I do it, I'll **scope it to naming only**, so a real change to that
> metric — its period, its calculation, its removal — still surfaces normally.
> An unscoped annotation would mute that mapping forever, which is a different
> way of losing the same information."

That is checklist item 4's intent stated back verbatim, with the correct reason.
It hasn't *executed* it because the divergent element doesn't exist on canvas
yet (the Deep-Dive isn't wireframed), and it said so — a principled
not-yet-reached, not a miss.

It also offered **`export --with-footnotes`** before I raised handover at all,
with the right justification: "Tooltips carry a lot of load in these drawings
now… and they're hover-only." That is checklist item 6, volunteered early.

**UI — the four-banner wall is fixed (screenshot `r7-stack.png`).** Four
pendings on four artifacts render as **one** banner with artifact + op summary
(`adds 5, changes 7, deletes 5`), a `+3 more revisions waiting` line, and a
**Show all** toggle. Round 1's screenshot of this exact situation was four
stacked full-width banners covering the canvas.

**WP1b eviction, verified live.** Queue-time validation means an invalid batch
can no longer *reach* the queue, so I forced the only remaining failure mode: I
deleted `position` from `argus-domain` **after** a batch modifying it had
queued, then clicked Apply now. Result: commit failed, entry `6` was **evicted**
(`PENDING` 4 → 3), and the log carries
`agent_revision_failed … op 2 (mod): no element with id 'position'`. In round 1
that entry would have stayed armed forever. The frontend's `catch` does
`toast(e.message)` for 4.2s, so the user is told — though the text is the raw
op-level error, which is developer-facing prose for an analyst.

### R9 — fork, late revelation, handover

**Agent (R8 turn):** offered **save-history bookmarks** by name and pre-labelled
three of them ("thresholds settled", "gate became a filter", "before the domain
split") — a third `canvas-app.md` affordance surfaced at the moment it was
useful. It then said something more useful than the bookmarks: *"if what you
actually want to check is a decision rather than a picture, `CONTEXT.md` and
the two ADRs carry the reasoning and won't require you to squint at an old
diagram."*

It read the failed apply correctly — *"that's the queue doing its job, not
damage"* — and treated my reason for deleting Position as a boundary claim it
had drawn straight through, then recorded the *absence* as a glossary entry
because "why isn't Position on the domain model" is a question someone will ask
in three months and "the answer being absence is unsatisfying". It split
`Finding` into two shapes because I'd said "same shape on the diagram,
completely different Monday", and **flagged that this made ADR-0001's paging
rule stricter than what I had originally told it** rather than slipping the
change in. It left the unescalated-material arrow missing on purpose.

**Harness:** `POST /api/checkout {revn: 4}` then a save on top → forked
`alt-0026` automatically. Final message carries the beat-10 late revelation (a
third reader — compliance, quarterly, must reconstruct why a Brief was held)
and an explicit handover ("my analyst starts Monday").

### R9 close — and the differential controls

**Agent's final turn** archived `alt-0026` rather than leaving a second
timeline, but first explained why the two-gate idea was wrong in a way I hadn't
seen — *"a cheap pre-filter drops the weak scores before crowding is measured,
which makes every theme look more one-sided than it is; the pre-gate would
corrupt the exact measurement it was protecting"* — and preserved the idea in a
new **"Rejected ideas worth remembering"** section of `CONTEXT.md`. It took the
late compliance fact and derived three structural consequences, including that
the Review button was never a Release button but a **form** ("vetting has to
capture a reason"), which retroactively reframed a pin it had left open two
rounds earlier. It produced a handover pack: five footnoted SVGs, a numbered
reading order, three things to say out loud that the drawings won't, and an
honest note that two exports were stale with the commands to refresh them.

**Checklist item 4 — PASS, verified server-side.** The annotation it wrote:

```json
{"concept": "finding",
 "elements": ["output-and-vetting#ov-finding", "dashboard#btn-review"],
 "note": "intentionally-divergent: the button says 'finding' (the umbrella
          word, right for UI copy); the domain distinguishes Unsure Call
          from Breach",
 "kinds": ["entity_renamed", "label_renamed", "renamed"]}
```

**Checklist item 3 — PASS, with the differential control.** Three saves against
that exact mapping:

| Test | Change | Verbs | Tripwire |
|---|---|---|---|
| A | rename `btn-review` → "Review finding" | `renamed`, `label_renamed` | **silent** (covered) |
| B | nudge `btn-review` down 40px | `moved` | **fires** `tw-30-1` (not covered) |
| C | append a word to a tooltip on a *different* mapped element | `tooltip_changed` | **fires** `tw-31-1` |

A and B together are the round-1 P1 closed and proven in both directions: the
annotation silences what it was recorded for and nothing else. In round 1 a
blanket `intentionally-divergent` note muted that mapping permanently and the
flagship rename tripwire never fired at all. 11 tripwires fired during the
session and all 11 were resolved.

Test C is a new finding (R2-6 below).

### R10 — the pipelines (the demo's `pipeline-flow` / `pipeline-dfd` beat)

The demo answers "draw the pipelines" with a **pair**: a first-class
`pipeline-flow` (5 sources → ingest+normalize → 5 parallel scorers → risk model
+ aggregator → KPI store / report data, 14 nodes over budget on purpose) and an
extended-tier `pipeline-dfd` in real DFD notation (`P1 enrich + score`,
`D1 signal store`, `P2 assemble outputs`, labelled data flows).

I asked for it the way the analyst would: the five stages and how each behaves
differently, plus *"whatever you draw, I also want something simpler I can put
in front of people who don't care about the mechanics — last time we produced
one diagram that tried to be both and it served neither."*

**Agent:** drew two views at deliberately different altitudes —
`Enrichment — the five stages` (maintenance) and `Argus at a glance` (six boxes,
four sources in, three outputs labelled by reader, ops on the failure path,
click-through links to the mechanics). Drawing it made two claims structural
that had been anecdotal: EDGAR feeds three stages wanting three different things
(so "EDGAR is down" was never one failure), and two of five stages aren't
stateless, drawn as a shared `Prior runs' data` store. It ran one node over
budget and recorded why rather than shrinking. It surfaced two things I hadn't
said — Fundamentals can contribute a 90-day-old Score that nothing downstream
can distinguish, and the partial-run rule doesn't survive contact with
sentiment, which *degrades* rather than failing.

It also **used `kinds` to fix R2-6 for itself**, scoping the two chronically
noisy mappings to `moved` only so layout edits stop nagging while a genuine
rename still surfaces.

Both views are `flow`. No extended type was used → **R2-9**.

### R11 — reporting a legibility defect without naming the cause

Deliberate probe. I reported the symptom the way a user would — *"hard to read
and I can't put my finger on why… the little words on the arrows are floating
around… one of them looks like it's sitting on top of the EDGAR box"* — and
said nothing about the mechanism.

**Agent diagnosed it unaided**, and named the gap:

> "'numbers' wasn't near the EDGAR box — it was **inside** it… Arrow labels get
> placed at the path's midpoint, and when a path is a long sweeping curve, the
> midpoint is nowhere near anything you'd read as that arrow. **The layout lint
> passed clean on all of it.** … Legibility is the one thing I can't check by
> reading the response — I have to render it and look, and I hadn't."

It relaid out both flows (canvas 1700×1120 → 1360×864, hand-routed arrows
because the auto-router had been giving up with "obstacles in every slot" and
falling back to arcs), then found and fixed a milder version of the same disease
on `daily-run-flow` unprompted. Verified render confirms the fix.

Two things this round establishes beyond the finding:

- **It reinvented the round-1 workaround for R2-3.** *"I applied it to a
  throwaway copy of the project and rendered it before queueing."* Two agents,
  two rounds, independently building the same escape hatch for the same missing
  feature is the strongest evidence in either assessment that
  `apply --check --render` is a real gap.
- **It volunteered the limit of its own coverage:** *"the domain views and the
  wireframes I've only ever seen through the lint, which we've just established
  is not the same as seeing them."*

## Final tally

Measured at revn 40, round 11 of the session.

| | Demo | Round 1 | Round 2 |
|---|---|---|---|
| Artifacts | 6 | 6 | **7** |
| Extended-tier artifacts | 1 (`pipeline-dfd`) | 0 | **0** |
| Concepts / mappings | 5 / — | — / 23 | 26 / 14 (**3 `kinds`-scoped**) |
| Glossary terms | — | 28 | 25 |
| ADRs | 0 | 2 | 2 (both with rejected-options sections) |
| Saves | — | 38 | 40 |
| Branches | none | 1 forked | 1 forked + **archived, rationale preserved** |
| Tripwires | 1 (never fired) | — | 20 fired / 16 resolved |
| Handover exports | — | none | **7 footnoted SVGs + reading order** |
| **Lint findings at close** | — | **16** | **5** |

Of the 5 remaining lint findings: two are deliberate budget overrides the agent
recorded with reasons, one is an overlap **I** created with differential test B,
and two are both symptoms of R2-7. **Zero genuine open defects.** Nothing
illegible shipped through the lint's own channels — the three text-fit warnings
and the structural `LAYOUT_ERROR` were caught at `apply --check` and repaired
before landing. What *did* ship illegible (R2-8, R2-10) was invisible to the
lint by construction, and was caught by looking.

## Findings (round 2)

| # | Sev | Finding |
|---|---|---|
| R2-4 | **P1** | **A rename that lands on one state-variant frame and not its twin is invisible to every check.** I renamed `kpi-alpha` → `Excess Return` on the normal frame; `kpi-alpha-d` still read `Alpha`. Both committed, and `canvas.py lint --artifact dashboard` reports **nothing** about it (only a budget override and a Q12 glossary question). Verified in code: 3.2.4 is built from `pairs`, which joins a *wireframe element to a flow element through a mapping* (canvas.py:4847, comment "mapped elements only"); tripwires compare mapped siblings across artifacts. Two frames of one screen inside one artifact — the normal/degraded pair the skill itself encourages — are compared by nothing. The agent caught it and named the gap precisely: *"nothing flagged it, because a divergence between two frames of the same screen isn't something the tripwire system watches… this particular gap is mine to catch, not the tool's."* This is the demo's flagship beat passing on agent diligence alone, which is the round-1 lesson repeating in a new place. |
| R2-8 | **P1** | **The lint's label geometry does not correspond to the drawing.** On `enrichment-detail` (revn 37) the render showed the arrow label `'numbers'` sitting **inside** the `SEC EDGAR` box. Measuring the *stored* geometry found **zero** label/node overlaps, and `canvas.py lint --artifact enrichment-detail` reported exactly one finding — the agent's own budget override. Cause, read from the code: the bound-label collision check (canvas.py:4318–4329) compares `la["x"]/la["y"]`, i.e. stored coordinates, and **no check compares a bound arrow label against nodes at all** — only `role: annotation` text is tested against nodes (4331+). Bound arrow labels are not painted at their stored position; the renderer places them along the arrow path. Consequence: `layout.md` connector rule 2 ("labels sit 6–10px perpendicular off their segment", marked *seeder + lint WARNING*) is unenforceable for bound labels — the seeder's offset does not survive to the render, and the lint validates a position nobody sees. **Independently corroborated by the agent** when I reported the symptom without the cause: *"'numbers' wasn't near the EDGAR box — it was inside it… Arrow labels get placed at the path's midpoint, and when a path is a long sweeping curve, the midpoint is nowhere near anything you'd read as that arrow. The layout lint passed clean on all of it."* |
| R2-10 | P2 | **A `mod` that resizes a container leaves its decoration geometry stale, and no lint can see the result.** On `dashboard`, the degraded frame's charts placeholder was shrunk from 116px to 72px high during round 7's held-state restructure (to give the Weekly Brief its own full-width row). Its two crosshatch `line` decorations were **not** re-derived and still run y 304 → 420 — geometry identical to the untouched normal frame's, which is the artifact's own built-in control. Measured: `charts-degraded` bottom = 376, both X lines end at 420, `shelf-degraded` starts at 392, so the X **overshoots by 44px into the Reports panel** and crosses two foreign boxes. Nothing links a decoration to the box it decorates (`parent` exists for nested wireframe blocks but isn't used here), and `role: decoration` is filtered out of both `shapes` and `arrows` before any collision or crossing check runs (canvas.py:3918–3922) — stated as policy in `layout.md:98`, *"`role: decoration` exempts furniture from connector lints and budgets entirely."* So connector rule 5 ("never cross a foreign box") structurally cannot fire on it. Found by the user in a screenshot after eleven rounds; neither the agent nor I had noticed. |
| R2-6 | P2 | **A divergence tripwire fires on purely presentational facts.** Test C: appending one word to a `tooltip` on `admin-console#sl-confidence` — nothing visible changes on the canvas — produced `tw-31-1`, *"changed but its mapped sibling didn't. Divergence, or should it propagate?"* Test B: a 40px nudge did the same. The agent independently reported the same thing: *"Eleven tripwires fired and they were all one thing: my own tooltip and layout edits on mapped elements."* All 11 were false. The verb set that arms a divergence tripwire should exclude presentation-only facts (`moved`, `tooltip_changed`, `reordered`, `block_moved_within_screen`) and keep the meaning-changing ones (renames, rewires, cardinality, value, state). |
| R2-3 | P2 | **Under `pulled`, the agent cannot see its own drawing.** It said so: *"I can't see the rendered screen until you apply it, so if it reads as cramped, that's why."* `snapshot` and `/render` serve committed state only, so the one defect class the agent is structurally blind to (legibility) is exactly the feedback that `pulled` removes. `apply --check` returns lint but no render. |
| R2-7 | P2 | **An aliased glossary entry parses as one malformed term.** The agent deliberately wrote `**Excess Return** / **alpha**: one number, two names…` as a *single* entry so nobody could conclude they were two metrics — exactly the reasoning the skill asks for. `TERM_RE` (canvas.py:4503) is `(?:[-*+]\s+)?\*\*(.+?)\*\*\s*(?::\|—\|–\|-{1,2}\s)`; the non-greedy group backtracks past ` / ` and captures **`Excess Return** / **alpha`** — raw markdown in the term name. The registry then emits two contradictory notes: "settled glossary term has no registry concept: `'Excess Return** / **alpha'`" *and* "concept references glossary terms CONTEXT.md doesn't define: `'Excess Return'`". Good behaviour, punished. There is no alias syntax in the glossary format. |
| R2-1 | P2 | **Q25 progress-indicator question fires on any percentage.** `_PROGRESS_RE` (canvas.py:3833) is `\bstep \d+ of \d+\b\|\bprogress\b\|\b\d+\s*%`, so the slider label `VaR alert  2.5%` was read as a progress indicator and drew a GDS citation about 12-step indicators. Any threshold, KPI delta, or completion rate in a wireframe trips it. Measured in code, not inferred. |
| R2-5 | P2 | **An artifact's name is immutable after creation.** The agent split `argus-domain` into a signal-formation view and reported: *"I can't rename an artifact. The rail will still call it 'Argus Domain' even though it's now the signal-formation half. Content is right, label is stale, and there's no op for it."* Verified: `name` is read from `create.get("name")` at canvas.py:5930 and never written again (`_write_artifact` falls back to `aid.replace("-"," ").title()`); the registry op vocabulary is `upsert_concept / remove_view / add_mapping / annotate_mapping / remove_mapping / resolve_tripwire / annotate_tripwire / decline / set_round / set_budget / waive` — no rename. A view whose scope legitimately narrows carries a wrong label forever, and the only workaround is recreating the artifact, which discards its history. |
| R2-11 | P3 | **The tooltip-presence dot is anchored to the bounding-box corner, so it floats free of non-rectangular shapes.** `App.tsx:1457–1463` places the dot at `(e.x + e.width, e.y + e.height)`. On a rectangle that lands on the border and reads as attached; on a **diamond** (every decision node) or an ellipse the bounding-box corner is empty canvas, so the dot sits ~40px off the shape and reads as a stray mark rather than as the tell it is. The dot exists precisely because tooltip content is hover-only and otherwise undiscoverable (its own code comment: *"hover-only content needs a discoverable tell"*), and this session put real weight on it — 33 dots carrying the never-built rerun note, the one-pass crowding rule, why Position is absent, and both ADR cross-references. Also found by the user in a screenshot. |
| R2-9 | P3 | **Extended artifact types are documented only as a downgrade, and were never chosen.** Asked for the mechanics *and* "something simpler for people who don't care about the mechanics" — the demo's answer is a first-class `pipeline-flow` plus an extended-tier `pipeline-dfd` in DFD notation (P1/D1 numbering, labelled data flows). The agent drew both as `flow`. Across two full assessments no extended type (`er`, `class`, `swimlane`, `dfd`, `mindmap`, `architecture`) has ever been used. The only guidance is `SKILL.md:344` — they "draw fine but narrate generically" — plus a parenthetical in `flow.md:45`. That is a reason *not* to pick one and there is no reference file saying when a DFD beats a flow. Six configured types are effectively dead. |
| R2-2 | P3 | **The concept-reattach heuristic still steers views off the project concept.** `view 'daily-run-flow' is named after concept 'Run' but is registered under 'Argus' — reattach it`. The agent complied, so `Argus` now holds 0 views while glossary terms (`Confidence threshold`, `VaR alert threshold`, `Weekly Brief`) sit in the registry as view-less concepts. v0.5 inverted the umbrella direction but left the name-token match; §9 predicted this would still fire and it did, on round 2 of a clean project. |


## Verdict

**All eight re-test items pass.** Every P1 from round 1 is closed, and three of
them were closed *visibly in the session* rather than only in tests: the agent
never once believed it had drawn something it had queued, `apply --check`
caught the exact `set_budget` error that produced round 1's dead banner, and a
stale pending was evicted instead of re-arming. The scoped-annotation fix was
proven with a differential control in both directions.

**The v0.5 documentation work paid off more than the code did.** `▶ walk`,
`+ insert`, save bookmarks and `export --with-footnotes` all reached the user
unprompted and at the moment each was useful — and every one of them appeared
in *no* documentation file before v0.5. The agent offered the handover export
before I raised handover, and stated `kinds` scoping correctly a full round
before it had cause to use it.

**Lint burden fell from 16 findings to 5**, and of those 5 only two describe a
real problem (both R2-7). Nothing illegible shipped: three text-fit warnings
and a structural `LAYOUT_ERROR` were caught at `apply --check` and repaired
before reaching the canvas, against round 1's three shipped overflows.

**The remaining gaps are new ones, not survivors — and they cluster.** Five of
the eleven findings are the same shape, and it is round 1's headline shape:
**the model believes something about the drawing that the drawing does not
say.**

| | What the model believes | What the drawing does |
|---|---|---|
| R2-4 | two frames of a screen are unrelated | they are the same control, renamed once |
| R2-8 | the label is where it was stored | the renderer puts it somewhere else |
| R2-10 | decorations are furniture, exempt | the X now crosses two foreign boxes |
| R2-3 | the agent can see what it drew | under `pulled` it cannot |
| R2-6 | a moved box may be a divergence | it is a nudge |

The first three are silent failures of *coverage*; R2-6 is a failure of
*precision* in the other direction. In every one of them the agent was the last
line of defence, and in every one of them it held — it caught the frame
divergence by reading, diagnosed the label placement from a bare symptom
report, and swept the false tripwires twice before scoping them away. **Score
the mechanism, not the outcome:** a less careful agent ships all five.

**Two findings came from the user looking at screenshots** (R2-10, R2-11) after
eleven rounds in which neither the agent nor I had noticed them. That is worth
recording as a property of the method, not an embarrassment: I verify by
querying state, and state is exactly where these defects are invisible. The
assessment needs a systematic *look at the picture* step, not just a render-on-
suspicion habit.

### Recommended order

1. **R2-4** (P1) — extend same-function label comparison to frames within one
   artifact, or state plainly in `wireframe.md` that state-variant frames are
   the agent's duty and not the tool's.
2. **R2-8** (P1) — derive rendered label positions from the renderer's own code
   and route the label lints through them. Today `layout.md` connector rule 2
   is documented as *seeder + lint WARNING* and is enforceable as neither.
3. **R2-10** (P2) — re-derive decoration geometry when its container is
   resized, or narrow the decoration exemption: keep decorations out of budgets
   and routing, but let "this line extends past the box it belongs to" reach a
   NOTE.
4. **R2-6** (P2) — restrict the tripwire verb set to meaning-changing facts.
   All 20 tripwires this session were presentation noise; the agent had to
   spend two `kinds` annotations to stop them recurring.
5. **R2-3** (P2) — let the agent render a queued artifact (`apply --check
   --render`, or snapshot-of-pending). **Two agents in two rounds
   independently invented the same copy-the-project workaround for this.**
6. **R2-7** (P2) — give the glossary an alias syntax, or make `TERM_RE` refuse
   to swallow `**` inside a term name.
7. **R2-1** (P2) — drop `\b\d+\s*%` from `_PROGRESS_RE`; it fires on every
   threshold and KPI delta in any wireframe.
8. **R2-5** (P2) — add an artifact rename/retitle op.
9. **R2-11** (P3) — anchor the tooltip dot to the shape's edge, not its
   bounding-box corner, so diamonds and ellipses keep their tell.
10. **R2-9** (P3) — write the one paragraph saying when an extended type beats
    a first-class one. Today the only guidance is that they narrate worse, and
    predictably none has ever been chosen.
11. **R2-2** (P3) — the concept-reattach heuristic still steers views off the
    project concept onto glossary terms.

### Method notes for the next run

- **The silent cadence flip is worth keeping.** Flipping `pulled` without
  mentioning it in chat is what proved the agent reads config rather than
  trusting its own optimism. Announcing it would have tested nothing.
- **Correcting a *queued* batch is the honest way to force `supersedes`** — no
  coaching required, and it is what a real user does.
- **Deleting an element a pending batch modifies** is now the only natural
  route to the eviction path, since queue-time validation closed the others.
- **Run the differential control as three saves, not one.** Covered verb,
  uncovered verb, and a second mapped element — table it.
- **Look at every artifact, at least once, at full size.** Both screenshot-
  discovered findings (R2-10, R2-11) were invisible to `status`, `lint` and
  `state`. Querying state cannot find a defect whose whole nature is that state
  does not record it.
- **Do not reimplement the renderer to measure it.** My first attempt to
  quantify R2-8 used `points[len//2]` as the label anchor; that is wrong for
  multi-segment arrows and produced two confident false overlaps that the render
  disproved. The finding survived only because it rests on the render and on the
  code, not on my model of the renderer. This is the round-1 "measure before
  believing a screenshot" rule with a second edge: *measure the right thing, and
  when your measurement and the picture disagree, the picture is the fact.*
- **Report a defect without naming its cause.** R11's probe — describing the
  symptom the way a user would and withholding the mechanism — produced the
  session's cleanest diagnostic result and independently corroborated a finding
  I had already written. Worth making a standing beat.
- Round 1's fixture is at `.scratch/argus-v05/`; round 2's is the live
  `argus2` project, which additionally carries a scoped annotation, an archived
  branch, footnoted SVG exports, and 11 resolved tripwires.
