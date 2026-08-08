# Capability assessment — wysiwyg-grilling (optimizer round 1)

**Method.** A single subagent plays the agent side of a real grilling session in an
empty temp project (`/tmp/.../scratchpad/argus`), with the skill discovered normally
through `~/.claude/skills/wysiwyg-grilling`. I play the analyst from
`capability-demo-chat-session.md`: chat messages via `SendMessage`, and *real* canvas
edits through the same HTTP surface the web app uses (`/api/save`, `/api/pins/answer`),
plus `playwright-cli` + the server's `/render/<id>` endpoint to look at the drawings the
way a human would. The agent gets no help — no skill hints, no corrections, nothing about
the demo it is being measured against.

**Benchmark.** `capability-demo.html`: 7 artifacts across 5 concepts, a glossary, live
mappings, an open tripwire, an unanswered pin, a pending agent revision, an inferred
mapping awaiting promotion.

---

## Round-by-round log

### R1 — kickoff brief → `daily-run-flow`
Agent: named the archetype (**"document generator on a deadline"**) and the parties in
one correctable line before drawing anything; seeded one flow; opened 3 pins.

- **Good.** Archetype + parties line landed unprompted and *specifically* ("not a data
  pipeline — an ETL's product is data, yours is a Brief, a Deep-Dive and a Digest"), and
  it derived the owed view set from it. This is the v0 failure mode the skill was written
  to close, and it closed.
- **Good.** It stated three deliberate wrong guesses *as wrong guesses* ("the four
  sources fan into one Enrichment box — I'm fairly sure that's false, I drew the clean
  version so the mess is visible by contrast"). Seed-your-guess discipline, executed.
- **Good.** Pin quality is well above the demo bar: each has `detail` + `examples`, and
  each names what hangs on the answer ("if the scorers have different input sets, a dead
  source degrades some scores and not others — which decides what a partial run may
  publish").
- **Good.** It refused to draw the admin console and said why (a rerun is a second entry
  point; drawing it before knowing where it re-enters would draw a lie). Draw Gate held
  under pressure from an explicit user request for wireframes.
- **Gap — connector routing.** The trigger→source arrows wrap the long way around the
  source column (see `r1.png`). Legible, but visibly worse than the demo's routing. The
  agent cannot see this and no `LAYOUT_*` line flagged it.
- **Delta vs demo.** Demo round 1 seeded *five* artifacts; the skill's "max one new view
  per round" caps this at one. Different shape, not obviously worse — but it means the
  project reaches the demo's artifact count only around round 6+.

### R2 — pin answers + settled numbers → rewired flow, `argus-dashboard`, `CONTEXT.md`
- **Good.** Narration used exactly the prescribed shape: 3 interpreted clusters, a
  committed reading per cluster, uncertainty named. It found something *by drawing* that
  wasn't in the prose: "EDGAR feeds three of your four scorers — it's your most
  load-bearing source and also the one that goes stale."
- **Good.** Blew the arrow budget deliberately and recorded `set_budget` with a real
  reason ("the crossed diets *are* this view"), rather than shrinking the drawing.
- **Excellent, beyond the demo.** The dashboard was drawn as **normal + stale twin**,
  row-aligned for diffing, with the stale variant flagged as a committed guess to check.
  The demo has no degraded-state view at all.
- **Good.** `CONTEXT.md` created with 11 terms, each carrying a real definition; every
  term also landed as a registry concept in the same batch (the v0.1 failure mode —
  15 glossary terms, zero concepts — did not recur).
- **Good.** ADR offer gated correctly and made in chat only, citing the actual tension.

### R3 — user deletes an arrow + drops a throwaway sticky note
- **Excellent.** The throwaway note ("watch out for crowded trades") is the demo's
  hardest beat — the demo agent turned it into a contrarian-screen node. This agent went
  further and turned it into a *structural* argument: crowding is not a property of an
  instrument, only Portfolio DB knows what you hold, and the same Sentiment Index means
  the opposite thing for a name you already own. Then offered a genuine fork (fifth
  scorer vs. a change to the Risk Model) rather than just drawing a box.
- **Good.** Deleted-edge read as instruction, executed, and the *remaining* four diets
  locked with the lock narrated and the unlock path stated.
- **Good.** It wrote the user's own sentence onto the admin screen as a band —
  "replaces today's KPI Store. No confirmation. No prior version kept." — and said why:
  "it reads differently there than it does in chat." That is the skill's whole thesis,
  self-applied.
- **Good.** ADR re-offered once, with *new* evidence, not repeated verbatim.
- **Gap — wireframe primitive under-renders.** `input-schedule` renders as a 96px box
  reading just "Run at": the settled value (weekdays 06:00) never made it onto the
  screen. The demo's equivalent reads "weekdays ▸ 06:00 (before open)". No lint fired.

### R4 — user deletes the Portfolio DB toggle (element with live mapping links)
- **Good.** Deletion of a mapped element fired a `divergence` tripwire automatically
  (`tw-12-1`), so the structural loss could not be silent.

---

### R5 — the rename that didn't propagate (the demo's flagship beat)
User renamed **Alpha → Excess Return** on all three dashboard frames. A mapping
`argus-dashboard#kpi-alpha ↔ daily-run-flow#kpi-store` exists, the glossary's KPI Store
entry still reads "alpha", and the save record shows `renamed` ×3.

**No tripwire fired.** Root cause is in `canvas.py:5166`:

```python
if note.startswith("intentionally-divergent"):
    continue
```

In round 2 the agent annotated that mapping `intentionally-divergent: Alpha/VaR/Sentiment
Index are three distinct numbers inside one KPI Store — fan-in, not one function under
three names`. That annotation was about **cardinality**. The check treats it as a blanket,
permanent mute on the mapping, so an unrelated **label** divergence three rounds later is
silent. The code's own comment one screen below promises the opposite — "annotated so this
pair never trips again *for this reason*" — and the tripwire's user-facing `detail` text
says the same. Behaviour and contract disagree.

This is exactly the beat `capability-demo.html` is built around (dashboard says "Excess
Return", pipeline-flow's KPI-store field still says "alpha", tripwire open at round 9).
The demo's centrepiece cannot fire in the current implementation once a reasonable
annotation exists on the mapping.

Two things the agent did here that the demo does not:

- It **caught the un-propagated rename itself**, without the tripwire: "the flow's KPI
  Store node and the glossary both still said alpha", carried it into both, and recorded
  `_Avoid_: Alpha` in the glossary so the old word can't come back. The safety net failed
  and the discipline held — which is the right way round, but it means the P1 above is
  masked in a well-behaved session and would only bite a less careful agent.
- It read the rename's *reason* against the design: "you renamed the **internal**
  dashboard because **clients** kept asking — clients see the PDFs, so the name must
  already flow into the Report Payloads." That is the kind of inference the artifact set
  exists to make possible.

### R6 — pin channels, user-authored pins, cadence
User answered three pins in the rail, drew a `❓` at the agent (`direction: user`), and
set cadence to `pulled`. All three mechanisms worked on the server side.

### Dirty-canvas hold — verified separately
The pending-revision hold could not be exercised in-session: the web app heartbeats
`dirty:false` every ~3s, which overwrites any flag set out-of-band. Probed in an isolated
project instead — `apply` against a dirty canvas correctly returns
`{queued: true, reason: "the user has unsaved edits"}`. Works.
(Latent hazard, not tested: `dirty` is last-writer-wins across clients, so a second tab
sitting clean would clear a first tab's unsaved-edit flag and let an agent revision land
on top of live work.)

### Lint layer — inspected directly
`LINT_DEBT=admin-console-wireframe 2N; argus-dashboard 9N; argus-domain 1N;
daily-run-flow 3N; registry 1N`. Reading the actual notes turns up four separate problems
(findings 13–16 below). The headline count is inflated: of the 16 notes, **5 are the same
registry finding copied into every bucket** and **6 are two questions repeated across
three frames of one wireframe**. Two of the sixteen are real, distinct signal.

### R7 — pulling the queued revisions
Cadence `pulled` worked exactly as advertised: three `agent_pending` events, nothing moved
under the user, and the agent narrated the hold honestly *including* its own blind spot
("I can't see the layout checks until they land").

Then pulling them exposed the round's worst defect. **Queued batches are never validated
until the user pulls them** (`queue_pending`, `canvas.py:6116`, stores the raw batch;
`apply_batch` first runs inside `commit_pending`). Revision 1 contained
`mod report {attributes: …}` — not a legal attribute — so:

- the agent got `{ok: true, queued: true}` and had every reason to think it had drawn;
- it **narrated that drawing as done** ("I've put both faces on the Report entity");
- the *user* got the raw validator error, minutes later, for someone else's mistake;
- and because `commit_pending` only prunes the entry **on success**, the broken revision
  is **still in the queue** — the banner will keep offering it and it will keep failing.

The skill's own contract depends on this not happening: "acceptance is not confirmation:
read the echo, not the success line, because you can't see your own drawing." In pulled
mode (and on any dirty canvas) the echo never arrives, so the one instruction the agent is
given for catching its own errors is unavailable exactly when it queues work.

Registry says 5 artifacts; the agent's own summary said "seven artifacts, five concepts".
Minor, but it is counting something it cannot see.

### R8 — branching, and a thin turn
**Branching works.** `POST /api/checkout {revn: 1}` then a user save forked cleanly to
`alt-0032`, and the save event carried `forked: true` — the flag SKILL.md tells the agent
to react to. This is the capability the demo transcript explicitly says its session never
exercised, so it is newly covered here.

**The agent's reply to R7 was one line**: *"Found it — and I'm holding everything until
you say go."* The user's turn had carried a concrete error report **and three editorial
rulings**, one of them with an explicit "write that down somewhere so we don't rehash it."
None were acknowledged. The agent read "don't push anything while I poke at history" as
"say nothing," collapsing the Round's step 3 (Narrate) into step 4 (Draw Gate). The
narration contract is unconditional — every accumulated event gets a reading — but nothing
in SKILL.md says so for the case where the user has asked for a still canvas. Worth one
sentence in the Draw Gate: *a user asking you not to draw suppresses step 4 only; the
reading is still owed.*

### R9 — the deferred-validation defect, confirmed twice
A second queued batch failed on pull: `registry op 0: arrows must be a positive integer`.
**Two of the five held batches this session were invalid**, and in both cases the agent
had already told the user the work was queued and correct. In unheld mode the same
mistakes would have come back as an immediate 422 the agent could repair inside its own
move — which is precisely what SKILL.md tells it to do ("an ERROR means the drawing
doesn't say what you meant — repair it in the same move, before narrating").

The agent also discovered, correctly, that **there is no way to discard a pending
revision** — `/api/pending/resolve` takes only `apply_now` or `after_save`. It worked
around it by marking the broken entry deferred so a future save would evict it, and said
so plainly. Good handling of a bad API.

Against that, the same round contained the best work of the session: it noticed the
canvas had forked to `alt-0032`, worked out that both its held batches touched artifacts
that don't exist on that branch, switched back to `main` before pushing, and preserved the
user's sketch — unprompted. And it read the user's "idle sketch" as the view-split it had
predicted two rounds earlier, proposed the split with its cost stated ("EDGAR-feeds-three-
of-five stops being visible at a glance… a tooltip is not the same as a picture"), and
declined to draw it until told.

Correction to the R8 note: the agent *had* written ADR 0002 before sending its six-word
reply. The work happened; only the narration was missing. That makes finding 11 a
narration defect specifically, not a work defect.

### The banner pile-up — findings 2/7 compounding, seen from the user's chair
By the end of the session the app showed **four stacked "Agent revision waiting" banners**
covering most of the viewport (the `argus-domain` entity was pushed almost off-screen).
Reading the queue:

| id | state | what it is |
|----|-------|-----------|
| 1 | `deferred: true` | the `unknown attribute 'attributes'` batch — **permanently unappliable**, parked there since the agent had no discard action |
| 5 | armed | the `arrows must be a positive integer` batch — **failed on pull and stayed armed**, because `commit_pending` prunes only on success |
| 6 | armed | the agent's *corrected* retry of 5 — a **duplicate intent**, since nothing replaced 5 |
| 7 | armed | a follow-up cosmetic fix |

So four failure modes stack into one wall:

1. no validation at queue time (2) lets broken batches in;
2. a failed commit leaves the entry armed, so every click re-offers the same failure;
3. no discard (7) means a dead entry can never leave;
4. the agent's retry **adds** rather than supersedes, so the user now sees two banners for
   one intent with no way to tell the broken one from the good one.

Two more things visible in that screenshot: the banner stack is **uncapped** — it grows
without collapsing or summarising — and each banner names its *change* but not its
*artifact*, so three banners about `weekly-brief-page` render while the user is looking at
`argus-domain` with nothing saying so.

---

## Fixed — v0.5 round 1 (the hotfix)

Findings **1, 2, 4, 5, 7, 8** are closed; the whole banner-wall cluster and the
divergence-scoping P1 are gone. 199 tests pass (8 new), `pre-commit run
--all-files` is clean.

| Finding | Fix |
|---|---|
| 2 — held batches skip validation | `apply_batch`'s non-mutating half extracted to `Store._validate_batch`; new `Store.check_batch` adds a registry dry-run against a deep copy and returns echo + lint. `/api/apply`'s hold branch now 422s a bad batch **before** queueing it. |
| 8 — no echo in held mode | The `{queued: true}` response carries `intent_echo` and `layout_*`; `cmd_apply` prints them instead of returning early. |
| 4 — failed commit re-arms | `commit_pending` drops the entry and emits `agent_revision_failed` before re-raising. |
| 7 — no discard | `/api/pending/resolve` takes `discard`. |
| 5 — retry stacks | `supersedes: <id>` on the apply body replaces the queued original. |
| 1 — `intentionally-divergent` is unscoped | `changed_elements` now carries the firing **verbs**; `add_mapping`/`annotate_mapping` (and class policies) take `kinds`; an annotation excuses only what it names. |
| 6 — 3.2.4 ignores the annotation | `cross_lint` consults the mapping note and policies, and the note now prints a `324:<aid>:<step>` waive key. |

Also folded in from WP5 because the fix is useless without it: **`canvas.py apply
--check`** (dry run — the workaround the agent invented mid-session, now a verb),
and the offline apply path now uses `lint_lines` so it stops missing every
cross-artifact finding.

Verified by replay, not just unit tests: both live failures (`unknown attribute
'attributes'`, `arrows must be a positive integer`) are now rejected at queue
time over HTTP and by `--check`; the registry is byte-identical after a dry run;
discard clears the banner; and the demo's flagship tripwire **fires** when a
mapping annotated for `cardinality_changed` is later renamed — while the
inverse control (annotation scoped to `renamed`) correctly stays silent.

## Fixed — v0.5 round 2

Findings **3, 9, 10, 11, 13, 14, 15, 17, 18** closed; **12 withdrawn** (see
below). 218 tests, `pre-commit run --all-files` clean.

| Finding | Fix |
|---|---|
| 9 — text overflow invisible | The old check read the *stored* label width; it now **measures** with `text_dims`, checks both axes, and covers composed rows (`value_of`, `attr_of`, `parent`) the containerId-only check never saw. Wrapping text is judged wrapped (only "too wide" when a single word cannot fit); non-wrapping composed rows on width. Against the real Argus artifacts: 3 warnings, all genuine — the earlier naive version produced 16, of which 13 were false. |
| — (new, found while fixing 9) | **`render_svg` only split on `\n`**, so fixed-width text the live canvas wraps exported as one long line spilling out of its box. The snapshot is the agent's only way to see its own drawing, so it was being lied to about legibility. Now wraps to the stored width. |
| 10 — `input` drops its value | `value` was read only by `kpi`/`slider`, so `add` with a value on an input dropped it **silently** while `mod value` errored loudly. `input` now composes a right-aligned value row, `mod value` retexts in place, and it fires `value_changed`. |
| 13 — registry lint leaks into every bucket | Registry-scope findings go to the registry bucket only. |
| 14 — misfile heuristic contradicts SKILL.md | It now fires only when the claiming concept matches **more** of the artifact id than its current holder, so `argus-dashboard` under `Dashboard` is left alone while `report-wireframe` stuck on the umbrella is still caught. |
| 15 — duplicate Q12 notes | Deduped by waive key: three frames of one screen are one question. |
| 18 — nags about user elements | The off-grid check skips `author: "user"`. |
| 17 — `/api/config` silent no-op | An empty or unwrapped patch is now a 400. |
| 3 — tooltips don't survive handover | `canvas.py export --with-footnotes` writes an SVG with tooltips numbered against markers on their elements and the glossary appended. Verified on the real Argus domain: 7 footnotes, 22 glossary terms, markdown flattened. |
| 11 — no still-canvas rule | One sentence in the Draw Gate: a request for a still canvas suppresses **step 4 only**; the reading is still owed. |
| — (the big doc gap) | New `references/canvas-app.md`: every user-facing affordance (`▶ walk`, `✨ tidy`, `+ insert`, `⇓ png`, sticky notes, ❓ ask, cadence toggle, rail, right-click tooltips) with **when to mention each**, pointed to from Session start, the reference list, and Session end. |

Lint noise on the real fixture: **16 standing notes → 5**, with the signal kept.

Also added: `canvas.py lint` (findings in full — `status` gave counts only) and
`canvas.py pending [--discard]`. Frontend: Discard button, banner stack
collapsed past the first with a per-artifact label and an op summary, and the
round counter marked `N+` when revisions are held rather than silently stale.

## Running findings

| # | Sev | Finding |
|---|-----|---------|
| 1 | **P1** | `intentionally-divergent` mapping annotations are **unscoped and permanent** (`canvas.py:5166`). Annotating a mapping for one reason (cardinality) permanently silences every future divergence on it (a label rename). The demo's flagship tripwire — Alpha vs. alpha — never fires. The `detail` copy and the adjacent code comment both promise per-reason scoping that isn't implemented. Fix: record the annotation with the fact-kind/element-pair it covers and only skip matching kinds, or expire it on the next *different* verb. |
| 2 | **P1** | **Queued (held) batches skip validation entirely.** `queue_pending` stores the raw batch; `apply_batch` first runs when the *user* pulls. An invalid batch therefore returns `{ok:true, queued:true}` to the agent, which narrates the drawing as done; the user later gets a raw validator error; and since `commit_pending` prunes only on success, the broken revision stays queued forever behind the banner. Observed live: `op 5 (mod report): unknown attribute 'attributes'`. Fix: validate at queue time (dry-run `apply_batch` against a scratch state, or at minimum schema-check the ops) and return 422 to the agent then; and drop or quarantine an entry whose commit raises. |
| 3 | **P2** | **Node detail lives in hover-only tooltips, and hover doesn't survive handover.** Every node in the final flow is a bare noun — `Sentiment Scorer`, `Insider Clusters`, `KPI Store`. The demo's equivalent nodes carry a second line (`fetch all sources` / *market · EDGAR · news · macro*; `KPI store` / *alpha · VaR · sentiment*), which is most of why the demo reads richer. SKILL.md forbids this outright — "verbose per-element detail goes in `tooltip` (markdown, hover-only) — **never extra visible rows**" — which is the right call for canvas density and the wrong one for the artifact's actual destiny: this session ended with the user handing the diagrams to a second analyst who will read a PNG, not hover a canvas. The agent felt the gap and said so unprompted ("a tooltip is not the same as a picture"), then worked around it by writing the EDGAR fact into `CONTEXT.md` as prose. Either give the primitives a one-line subtitle slot, or have the export/render path emit tooltips as numbered footnotes. |
| 4 | **P1** | **A failed `commit_pending` leaves the entry armed** — `self.pending` is pruned only on the success path, so a batch that raises stays in the queue and the banner re-offers the identical failure on every click. Combined with 2 and 7 this is what produced the four-banner wall. |
| 5 | P2 | **A corrected retry adds a banner instead of superseding one.** The agent requeued a fixed version of a failed batch; the user was left with two banners for one intent and no way to tell which was broken. Queue entries need supersession (`replaces: <id>`), or the agent needs a way to withdraw. |
| 6 | P2 | **The banner stack is uncapped and artifact-anonymous.** Four banners consumed the viewport, and each names its change but not the artifact it targets — three `weekly-brief-page` banners rendered while the user was viewing `argus-domain`. Collapse past the first ("+3 more"), and label each with its artifact. |
| 7 | **P2** | **No way to discard a pending revision.** `/api/pending/resolve` accepts only `apply_now` and `after_save`, so a batch that can never apply cannot be removed — the agent had to mark it `deferred` and rely on a future save evicting it. Add a `discard` action, and auto-quarantine any entry whose commit raises. |
| 8 | **P2** | **In held/pulled mode the agent never gets its intent echo or `LAYOUT_*` lines** — the one mechanism SKILL.md gives it for catching its own drawing errors ("acceptance is not confirmation: read the echo"). The queue response should carry at least the echo and layout findings computed against the projected state, or SKILL.md should say plainly that a held revision is unverified until pulled. |
| 9 | **P2** | **Long label text overflows its element box and nothing flags it.** Three independent instances this session: the admin rerun band, the domain `Report` entity's attribute line (renders as "audience: us \| clients \| committe"), and the Weekly Brief footnote, which spills ~100px past its frame into open canvas. The agent cannot see any of it, `LAYOUT_*` never fires, and the demo's hand-built HTML never has to face the problem. A width-vs-measured-text check at apply time — even an approximation — would turn all three into `LAYOUT_WARNING` lines the agent could repair in the same move, which is exactly what the skill's "repair it before narrating" rule expects to exist. |
| 10 | P2 | The `input` wireframe kind renders its label only — a settled value passed by the agent does not appear, so a schedule input reads "Run at" with no time. Either the primitive needs a value slot or `references/wireframe.md` needs to say to put the value in the label. (Agent fixed it once told, so the primitive is usable — but it took a human noticing.) |
| 11 | P3 | SKILL.md's Draw Gate has no rule for "the user asked me not to draw right now." The agent collapsed narration into the draw suppression and answered a three-ruling turn with six words. One sentence — *a request for a still canvas suppresses the Draw Gate only; the reading is still owed* — would close it. |
| 12 | ~~P3~~ **WITHDRAWN** | I recorded "connector routing loops the long way around node columns" from `r1.png`. It is wrong. Measuring the actual round-1 save record, **every arrow is ratio 1.00** — a single bend, zero detour; a reconstruction of the same geometry routes identically with and without a detour penalty. What I read as wrapping was Excalidraw drawing single-bend L-routes as sweeping curves at 40% zoom. I built a detour budget for `route_arrow`'s `score`, could not make it change any output, and reverted it rather than ship a speculative change to layout scoring. Lesson for the next assessment: measure the geometry before believing a screenshot. |
| 13 | **P2** | **Registry-scope lint leaks into every artifact bucket.** The note "view 'argus-dashboard' is named after concept 'Argus' but is registered under 'Dashboard'" appears verbatim in the `registry` bucket *and* in all four artifacts' buckets, including `argus-domain` and `daily-run-flow`, which it has nothing to do with. It inflates every artifact's `LINT_DEBT` count and makes the nag look like four problems. Emit registry-scope findings once, in the registry bucket. |
| 14 | **P2** | **That lint's advice contradicts SKILL.md.** It fires because the artifact id starts with the project name and tells the agent to reattach `argus-dashboard` from concept `Dashboard` to the umbrella `Argus`. SKILL.md says the opposite: "`create.concept` names the MOST SPECIFIC concept the view makes tangible… the umbrella concept holds only views that genuinely span the whole design." The agent had it right; the lint is pushing it wrong, on a *name-prefix* heuristic. Either drop the heuristic or invert the advice. |
| 15 | P2 | **Duplicate lint notes are not deduped by waive key.** Q12 fires 6 times for 2 labels because the dashboard has three frames (normal / stale / off) of the same screen — and all three emit the *identical* waive key (`q12:argus-dashboard:weekly-brief`). One waive will silence three notes, so they were never three findings. Dedupe on the key before counting. Same shape as the skill's own rule: "N tripwires with one cause is one tripwire." |
| 16 | P2 | **`intentionally-divergent` is honoured by tripwires but ignored by lint** — the inverse of finding 1, on the same mapping. Lint 3.2.4 still says "'Excess Return' / 'Sentiment Index' / 'VaR' all map to `daily-run-flow#kpi-store` — same action, 3 names; pick one?" months after the agent recorded exactly why that is deliberate. One annotation, two subsystems, opposite readings: tripwires over-honour it (permanently, for all reasons), lint under-honours it (never). |
| 17 | P3 | `POST /api/config` silently ignores a body without a `patch` key: it returns `ok:true` with the config unchanged **and** appends a `config_changed` event with an empty patch. An agent reading the event log would narrate a cadence change that never happened. Reject an empty/unwrapped patch. |
| 18 | P3 | Off-grid lint nags the agent about the **user's** own sticky notes (`usernote-*`), which the agent didn't place and shouldn't re-grid. Skip `author: user` elements in the grid check. |
| 19 | ENV | `playwright-cli snapshot` returns a pre-hydration accessibility tree for this SPA, so rail refs are unusable; `eval` and screenshots are fine. Not a skill defect — noted so the next assessment doesn't lose time to it. |

## Verdict against the benchmark

Ten user turns. Final state:

| | demo (`capability-demo.html`) | this run |
|---|---|---|
| artifacts | 7 (incl. 1 extended-tier DFD) | **6**, all first-class |
| concepts with views | 5 | **5** (23 concepts total) |
| glossary | implied, not shown | **28 terms**, definitions that cite each other |
| ADRs | 0 (offer only) | **2, accepted**, one amended by the user |
| mappings | 5 listed | **23** live |
| branches | main only ("never needed them") | main + `alt-0032` fork |
| degraded/exception views | none | **3** (stale dashboard, source-off dashboard, degraded brief) |
| pins | 1 open, unanswered 3 rounds | 16 raised, 2 open at close |

**It cleared the bar, and it cleared it on the dimensions that matter most.** On artifact
*selection*: every view was earned by a question rather than requested, it refused the
admin console for a round with a reason that turned out to be right, and it declined to
draw the flow split until told. On *representing the user's ideas*: the two moments the
demo is built around — a throwaway sticky note becoming a real pipeline stage, and a
rename that hadn't propagated — were both handled, the first with a better argument than
the demo's (crowding is not a property of an instrument) and the second **without the
tripwire that was supposed to catch it**. On *quality*: the domain model is sharper than
the demo's, the ADRs have no counterpart there at all, and the degraded-state views are a
category the demo never attempted.

Where it fell short of the demo is narrower and mostly mechanical: nodes are bare nouns
because detail is confined to hover-only tooltips (finding 3), the demo's second-line
node text has no equivalent, and no extended-tier view was ever drawn.

**The most important behavioural result is that the agent's discipline covered for the
tooling twice.** It caught the un-propagated rename that finding 1 should have caught, and
it diagnosed the deferred-validation defect itself and built a dry-run workaround that
then caught a real overlap bug. Both are excellent agent behaviour and both are evidence
the safety nets have holes — a less careful agent shipping the same session would have
left "alpha" in two places and told the user two drawings existed that didn't.

## Opportunities (process / CLI / flow)

**Fix order.** 2 + 4 + 5 + 6 + 7 are one bug with five faces — the held-revision
path — and they produced the only genuinely bad user experience in the session (a wall of
banners, two of them dead). Fix that cluster first, as one change: validate at queue time,
evict on failure, add `discard`, let a retry supersede, cap the stack. Then 1 (the
unscoped `intentionally-divergent` mute), because it silently disables the skill's
headline capability. Then 9 (text overflow), because it is the one defect the agent
structurally cannot see or repair. The lint findings (13–16, 18) are a half-day of tidying
that would roughly halve the noise the agent has to triage every round.

**Process / CLI.**

- The harness has no way for a subagent's chat turn to reach the orchestrator: the
  agent's user-facing text had to be recovered from its transcript JSONL. A capability
  assessment run this way needs that plumbing stated up front.
- `canvas.py` has no first-class "simulate a user save" surface. Everything needed is
  reachable over HTTP, but a `canvas.py user-edit` / fixture-replay subcommand would make
  this kind of assessment (and the acceptance tests) far cheaper to write. The driver
  written for this run (`~/.cache/argus-sim/drv.py`) is the sketch of that command.
- The agent invented the right workaround for finding 2 — dry-running a batch against a
  throwaway copy of `project_knowledge/` before queueing. That deserves to be a real CLI
  verb (`canvas.py apply --check`), not something each agent has to reinvent under
  pressure.
- `ROUND` advanced to 5 across ten user turns, because it keys off applied agent
  revisions and held ones don't count. The number the UI shows the user ("Round 5 — your
  move") drifts from the conversation whenever cadence is `pulled`.
- Worth considering for the skill itself: a **handover mode**. This session ended exactly
  as a real one would — "I'm handing these to my analyst Monday" — and that is the moment
  the hover-only tooltip convention (3) and the bare-noun nodes stop working. A
  `canvas.py export --with-footnotes` would close it.
