# Capability assessment — round 3 (against v0.6)

Re-test of `skills/wysiwyg-grilling` after commits `13203a9` (WP1),
`fd660c9` (WP2–WP5), `e5691e5` (WP6). Method:
`~/docs/optimization/wysiwyg-grilling_v0.6.md`. Round-2 notes and findings:
`capability-assessment-notes-optimizer_R2.md`.

- **Project:** `<scratchpad>/argus3` (empty at start; sha1 `d0f433f5ca4e`)
- **Agent:** fresh subagent, no access to the repo, the prototype, or prior notes
- **Human:** me, playing the analyst from `capability-demo-chat-session.md`
- **Harness:** `canvas.py x-as-user` / `x-pending` / `x-geometry` (WP6), not `drv.py`
- **Length:** 12 turns, 33 saves, 8 views, 7 footnoted SVGs + a HANDOVER.md — closed at revn 33, `PENDING=0`, `OPEN_TRIPWIRES=0`, 4 pins deliberately left open
- **Result:** 16 findings (6 P1) across 15 causes, 1 withdrawn; **3** lint findings at close, all explained
- **Ledger:** `.ledger/r3.jsonl` — authoritative counts, rivals, discriminators
- **Status:** CLOSED

## Re-test checklist status (v0.6 §9)

| # | Item | Status |
|---|---|---|
| 1 | Elbow-arrow label lands readably; exported SVG matches live canvas | **SPLIT** — arrow labels: **PASS** (`x-geometry --diff` 0px drift on all 9 across two artifacts; an independent overlap test using `arrow_label_anchor` over every bound arrow label vs every non-endpoint node returns zero). Node labels: **FAIL** → **R3-4**, the client PNG export disagrees with both the live canvas and the SVG |
| 2 | Resize an image placeholder → its X follows | **PASS** — blocked until T11 because the agent had drawn no `kind: image` in six artifacts; asking for a Deep-Dive *"with a price chart across the top"* minted `dd-chart`. Shrunk it 200 → 120 high: `dd-chart-x1` points became `[[0,0],[680,120]]` and `dd-chart-x2` moved to y=372 with `[[0,0],[680,-120]]`. Both diagonals span exactly the box's 252..372 — **zero spill**. WP1b's `_recompose_xbox` closes R2-10, the defect the *user* spotted in round 2 |
| 3 | Agent reaches for `apply --check --render` under `pulled` | **PASS** — used unprompted on the *first* batch of T1, before the cadence flip; then 3× on one batch at T2, iterating on the returned PNG until the `help drifts across screens` note cleared |
| 4 | Rename on one variant frame only → **the lint** catches it | **PASS — caught by the lint, not the agent.** Renamed `brief-synthesis` on the clean frame only; `LAYOUT_WARNING` fired at once naming both frames and both strings, with waive key `var:weekly-brief:brief-synthesis` exactly as WP2 specified. In round 2 this class was `tool-silent-covered`; it is now `tool-told`. The agent had also set `customData.variant_of` unprompted, so the explicit-pairing path is what ran |
| 5 | Nudge + re-tooltip mapped elements → zero tripwires; rename → fires | **PASS, both directions** — run as three separate saves at T5: move `m-downstream` 40px → `TRIPWIRES=0`; tooltip `enrich` → `TRIPWIRES=0`; rename **the same** `enrich` → `TRIPWIRES=1`. In round 2 the first two both fired. The silent half is the half that matters here |
| 6 | Aliased glossary entry → one term, one alias, no registry notes | **NOT REACHED** — the `**Term** / **alias**:` syntax v0.6 added was never used. Asked to record the alpha/excess-return divergence, the agent wrote `**Alpha**: the return we are trying to capture. _Avoid_: excess return` — the *other* convention. 14 terms, zero registry notes, so nothing is wrong; the new syntax simply sat unused. Third affordance in the "remediation aimed at paths the agent doesn't take" class — see Watching |
| 7 | View whose scope narrows → does it reach for `rename_artifact`? | **PASS on selection, FAIL on effect.** The one-pager's audience rejected it, so its scope narrowed; the agent chose rename over redraw unprompted and said why — *"Renamed rather than redrawn, so its id, history, mappings and pins all survive — only the title in the rail moved."* The op then failed to persist twice over: **R3-12** wrote it during a dry run, **R3-10** reverted it on the real apply |
| 8 | "The mechanics **and** something simpler" → is an extended type chosen? | **PASS — the first time in three assessments.** `argus-one-page` landed `type=dfd`, `tier=extended`, and the agent named the cost unprompted: *"this is an extended diagram type, so when you edit it I'll read your changes as 'a box moved' rather than 'a step became a decision' — the narration gets blunter. Worth it here; I wouldn't use it for the mechanics."* Attribution `tool-told` — v0.6's `view-progression.md` paragraph doing exactly what it was written for |
| 9 | Percentage on a wireframe → no progress-indicator question | **PASS** — `Confidence 0.70` and `Risk limit 2.5% of portfolio` sit on the thresholds screen; admin-console lint is a width-table note plus a 3.2.4 note, and **no** progress-indicator question |
| 10 | Diamond tooltip dot sits on the shape | **PASS** — `confidence-gate` (240×120): dot lands at (1620,510), outline test `60/120 + 30/60 = 1.00`, exactly on the stroke. Confirmed analytically and in the live UI. See R3-1: the pin glyph on the *same* diamond is at factor 2.09 |
| 11 | Final lint count (R1: 16, R2: 5) | **3** at the true close (2 before the handover round added one) — and **none is unexplained**: a lint the agent overruled out loud at T2, a *recorded budget override with its reason*, and R2-2, the heuristic v0.6 chose not to fix. See Verdict |

## Round log

### R1 — kickoff brief (verbatim from the demo session)

**Agent:** named the archetype falsifiably — "a *document generator on a
deadline*" — and derived the view debt from the naming ("because you named a
deadline"). Drew **one** artifact, `daily-run-flow` (9 nodes, 8 arrows), and
labelled its own inventions as inventions: the confidence gate and its
`Held back` sink are marked drawn-because-consequences, with "delete both if
the threshold only annotates". Compressed `Ingest 4 sources` with a stated
hypothesis for splitting it (the portfolio DB is a *scoping* input, not an
enrichment input).

Its best move was a question about nothing on the canvas: *"where is the agent?
Everything you described is a fixed DAG on a cron… if it doesn't decide
anything, 'agent' is a word that will mislead whoever builds this."*

**Tooling:** clean. 1 lint finding, the view-debt note, correctly confined to
the `registry` bucket. `x-geometry --diff` reports **0px drift** on both bound
arrow labels — WP1a's guarantee holding — and the render agrees: `passes` and
`below` sit on their strokes with the `SVG_GROUND` backing breaking the line
cleanly around them.

**`apply --check --render` used on the very first batch, unprompted.** In v0.5
plain `--check` took a round to be adopted; the render flag was found
immediately. Re-test item 3 passes on round 1.

**Finding R3-1** from the render: the ❓ on the `confidence-gate` diamond floats
79px off the shape.

### R2 — settle numbers, answer where-is-the-agent; cadence flipped to `pulled`

Canvas half: answered `pin-agent-unsure` from the rail ("escalate, but not to a
human — nobody reads a queue"), then flipped `canvas_updates=pulled`
**silently**. Chat half: the two real decisions (publishability, escalation),
thresholds 0.70 / 2.5%, the PDFs differ in content not framing, gate is a
split, draw the admin console. A contradiction was planted deliberately across
the two channels — the rail answer says escalation never reaches a human, the
chat message says it escalates to me.

**Agent:** queued the console as pending #1 and explained the banner, but —
unlike round 2's agent — **did not remark on the cadence flip as a signal**.
It adapted correctly without narrating that it had read the state. Behavioural,
sample of one; noted, not a finding.

It took the pin answer as a *principle* and followed it somewhere I hadn't:
three separate ways a signal becomes invisible (unscored, sub-threshold, held
run), each defensible alone, together meaning "the analyst's mental model of
what Argus saw this morning can be wrong in three directions and the product
never says so". Proposed a count rather than a queue. It then declined to patch
the three now-wrong labels on the flow *this round* — "you've got one banner
already and I'd rather you review one thing at a time" — which is the right
read of a `pulled` cadence.

It stated a position instead of asking: the `Portfolio DB` toggle should not
exist, because switching it off doesn't degrade the run, it produces "a run
about nothing", and four identical-looking toggles claim four identical-looking
consequences.

It did **not** catch the planted cross-channel contradiction. (Round 2's agent
did catch its equivalent.)

**Tooling — WP1c is delivering.** Under `pulled` the agent ran
`apply --check --render` **three times on one batch**, using the returned PNG
to iterate: the second run's `LAYOUT_NOTE` about help drifting between screens
is gone by the third. That is exactly the blind spot WP1c existed to close, and
it closed without a hint. Registry-only ops (the glossary) landed immediately
without a banner, correctly — nothing is drawn.

It overruled one lint out loud rather than silently (the equal-field-width
question on a settings screen with right-aligned values) and invited
disagreement.

### R3 — kill the dead end, a throwaway sticky, correct a *queued* batch

Canvas: deleted `held-back` + `t-gate-held` (cascade took both bound labels;
**no orphans** — `HEADLINE=deleted Held back (+3 more)`), and dropped a
throwaway sticky: *"compliance won't let us act on a Form-4 signal until it's
48h old"* — chosen because it contradicts the spine of the drawn flow, where
all four enrichers feed the same pre-open deadline. Chat: corrected the
**still-queued** console twice (drop the Portfolio DB toggle; a rerun
regenerates only the reports that changed) and asked for it fixed before it
lands.

**Agent:** `"supersedes": 1` on the corrected batch, verified server-side —
pending #1 gone, #2 in its slot. *"console v2 (replaces the old one — same
slot, nothing to review twice)."*

It took the sticky further than I had: the insider detector isn't on the same
clock as the other three, so a Form-4 signal either publishes un-actionable or
arrives on a morning nothing triggered it — **and a cluster is multiple
filings, so the cluster may still be forming while the 48h clock runs.** It
pinned rather than resolved.

It drew a **normal/degraded variant pair** for the Weekly Brief unprompted,
with the argument for drawing both stated: *"the degraded Brief isn't the clean
one minus a row. NVDA drops out and PFE becomes #1… the two documents look
equally finished."* That sets up the WP2 re-test for free.

### R4 — apply from the UI; push on the T1 compression; two audiences

The one deliberate real-UI turn. Clicked **Apply now** on the console banner,
reading the header immediately before and after (R3-2's differential control,
below). Chat: pushed on `Enrich (4 scorers)` as one box — *"it hides the only
question anyone ever asks me, which is where did this number come from"* — and
asked for the mechanics **and** a separate one-pager for the head of desk and
compliance, who *"do not care about scorers and never will"*.

**Agent:** refused the compromise and said why — *"those aren't two levels of
detail on one picture, they're two different grammars"* — then drew the
one-pager as a **`dfd`**, the first extended-tier artifact in three
assessments, and volunteered the cost (see re-test item 8).

Unpacking the middle produced three facts the single box had hidden: **EDGAR
feeds three of the four scorers** (so a bad EDGAR morning is "a run with one
opinion in it", not a degraded run); **fundamentals is labelled quarterly but
runs 250 times a year**; and **the 48h hold is its own box**, separate from the
detector, because *"drawing them as one thing would have buried your sticky
note inside a scorer."*

It invented exactly one box on the one-pager — `D2 · Record of each run` — and
argued it is load-bearing twice: compliance asking "what did Argus tell the
desk on 14 March" needs a *place*, not a person, and the console's missing Runs
tab can only list what something wrote down.

It flagged its own budget override out loud (10 nodes against a budget of 9,
*"compressing it would recreate the box it replaces"*).

### R5 — the deferred path, the tripwire differential, a user pin

Canvas: applied the DFD and the mechanics from the banner, **deferred** the
Weekly Brief with *After I save*, then ran the tripwire differential as three
separate saves, then added a user `❓ ask` on the 48h box. Chat: gave the
agent the correction it had asked for on fundamentals (*price is in the
denominator — the filings are quarterly, the ratios are daily*), confirmed
`D2` does not exist, and deliberately **did not mention the rename**.

**Deferred flush is correct:** deferred at revn 7, banner switched to
*"Discard | lands after your next Save"*, flushed at revn 9 on the **first**
user save after (revn 8).

**Agent:** ruled on the 48h hold by *changing its own drawing's shape* — the
box leaves enrichment because *"it was never enrichment"* — and then followed
the ruling into a consequence I had not seen: NVDA's cluster was the clean
Brief's top item at 0.88, so per-document admission re-ranks that frame and the
coverage line **grew a fifth slot** (`1 held 48h`). It flagged extending its
own four-slot design without asking, and said why it earned a slot rather than
a footnote.

It **superseded the failure-modes view before I applied it** — *"its EDGAR cell
still said 'hold the run', which your ruling had already overturned; you'd have
been applying something I knew was wrong."* Second use of `supersedes`, this
one proactive rather than corrective.

It also **declined to queue a sixth revision**: *"adding a sixth that
restructures the daily-run spine would be me filling a queue faster than anyone
can review it."* Correct read of a deep queue under `pulled`.

The shortfall notice is drawn at 480px against the Brief's 960 — *"put the
three side by side and you can see it isn't the product before you read a
word, which is a stronger guarantee than any wording."*

### R6 — the flagship: rename on one variant frame while saying "everywhere"

Canvas: renamed `brief-synthesis` on `brief-normal` **only**. Chat: "make it
that everywhere it appears", plus the per-document ruling on the 48h hold, a
go-ahead on the domain model, and the planted vocabulary divergence
(*"internally we all say alpha, not excess return"*).

**Re-test item 4 passes by mechanism** — see the checklist. The agent then
*credited the mechanism* rather than its own reading: *"Worth knowing how I
caught the second one: … because the two Brief frames are declared a variant
pair, the lint flagged that one screen said 'changed' and its twin said
'moved'."*

**The version-over-version pair is the most valuable measurement in either
run**, and it only exists because of the attribution axis:

| Run | Outcome | Attribution |
|---|---|---|
| 2 | the flagship divergence was caught | `tool-silent-covered` — the agent read the canvas, the tool said nothing |
| 3 | the flagship divergence was caught | `tool-told` — the lint fires with the question, the pairing and a waive key |

Without the axis both runs report "caught" and WP2 looks like work that changed
nothing. With it, run 2 is a hole and run 3 is a closed one. **Carry this pair
verbatim into the v0.7 method doc.**

*Which path ran:* the agent had set `customData.variant_of` unprompted, so the
**explicit** pairing path was exercised. The **inferred** fallback is untested
by this run.

**The alpha declaration went to the glossary, not to a mapping annotation** —
recorded as a term-to-avoid so *"if it ever creeps back onto a diagram, it gets
challenged instead of quietly accepted"*. `divergence_policies` is still `[]`
and every mapping still has `kinds: null`. That is defensible — what I declared
was a *vocabulary* preference, not a divergence between two mapped elements —
so item 7 is re-run properly at R7.

### R7 — the domain model, and a deliberate divergence with teeth

Canvas: applied the domain model and the dashed `D2`, then renamed
`admin-console#src-edgar` → `SEC EDGAR (public, no SLA)`. Chat: declared the
console/flow naming split **settled and deliberate** — *"two different
altitudes of the same fact and they are meant to disagree. Don't reconcile
them, and don't ask me again in three rounds"* — which is the honest way to
force `intentionally-divergent` on the many-to-one mapping the 3.2.4 note has
been standing on since R4.

**One user rename fired three tripwires** (`TRIPWIRES=3`,
`OPEN_TRIPWIRES` 1 → 4), one per co-mapped sibling in the four-element
many-to-one mapping. Arguably correct per pair, but one user action producing
three separate questions is adjacent to the false-alarm cluster — see Watching.

**The domain model, measured:** 9 relationship labels, `drift=0px` on every
one, no label/box overlaps, cardinalities on all edges, attributes on all
entities. `expected alpha` appears as a `Signal` attribute — the R6 vocabulary
ruling propagated into a drawing made after it.

## Findings

### R3-1 — the pin ❓ glyph floats off a diamond (P2)

`canvas.py:2177-2178` places every agent pin glyph at the target's **bounding-
box top-right corner** plus a fixed 8px:

```python
px = (anchor["x"] + anchor.get("width", 0) + 8) if anchor else 40
py = (anchor["y"] - 8) if anchor else 40
```

Measured on round 1's `daily-run-flow`, distance from the glyph centre to the
target's actual stroke:

| Pin | Target | Type | Gap |
|---|---|---|---|
| `pin-agent-unsure` | `enrich` | rectangle | 22px |
| `pin-late-source` | `ingest` | rectangle | 22px |
| `pin-confidence-gate` | `confidence-gate` | **diamond** | **79px** |

On a rectangle 22px reads as "attached to the corner". On a diamond the bbox
corner is empty canvas, so the ❓ sits 3.6× further out with nothing between it
and the shape — visible in `r3-r1-flow.png`, where it reads as a stray mark
rather than as a question about the gate.

**This is R2-11 in a second renderer.** WP5 fixed the *tooltip dot* in
`App.tsx:1464` with a per-type inset (`diamond → 0.5`, `ellipse → 1-√½`); the
pin seeder was never grepped for the same shape. Two ways this one is worse:
the ❓ is the primary visual carrier of an open question rather than a 4px
affordance hint, and it is **baked into stored element geometry**, so it ships
identically in the live canvas, the PNG and the `export --with-footnotes` SVG
the handover is built from.

Fix: reuse the `App.tsx` inset rule in the seeder. Same three-line shape.

### R3-2 — under `pulled`, `whose_move` can never return to the user (P1)

`whose_move` has exactly three writers (`grep whose_move canvas.py`):

```python
5692:  if author == "user":   self.registry["whose_move"] = "agent"
5695:  elif author == "agent": self.registry["whose_move"] = "user"
6097:  # the `set_round` registry op
```

5692/5695 are both inside `commit()`. **Under `pulled` cadence the agent's
batches queue instead of committing**, so 5695 never fires. After the first
user save of the session, `whose_move` is pinned to `"agent"` and stays there
for as long as the cadence is pulled.

Read live from the running app while two banners were on screen:

```
Round 1+ — agent reading
argus3 · Argus Daily Run
agent is reading…
…
✎ Agent revision waiting on admin-console: … Apply now  After I save  Discard
+1 more revision waiting
```

The header tells the user the agent is busy at the same moment the screen asks
them to make two decisions. This is the *normal* state under pulled, not an
edge case — the cadence v0.5 and v0.6 were largely built to support.

Three consequences, worst last:

1. **The status line is wrong** whenever it matters most.
2. **`App.tsx:1221`'s `pending.length ? "revision waiting"` arm is near-dead.**
   It is the third arm of a ternary whose second arm catches
   `whoseMove === "agent"` first, and the only thing that sets `whose_move`
   back to `"user"` is an applied agent commit — which is exactly the event
   that empties the pending queue.
3. **Pin aging freezes, so the standing-nag machinery stops.** `commit()` line
   5694 advances `registry["round"]` on the same never-taken branch, and
   `pin_debt()` computes `age_rounds = round - pin.round`. Confirmed live:
   `pin-late-source` was asked in round 1 and after three conversational rounds
   still reports `age 0r`. The v0.6 method doc asks round 3 to "leave one pin
   unanswered for several rounds" to observe standing-nag behaviour — under
   `pulled` there is nothing to observe, because nothing ages.

The only escape hatch is the `set_round` registry op (6095–6098), which also
sets `whose_move` — and `set_round` appears in **no** reference file and not in
`SKILL.md`. Implemented registry actions: 15. Documented in
`ops-reference.md`: 11. The other three undocumented ones (`apply_now`,
`after_save`, `discard`) are user-side banner actions and correctly absent;
`set_round` is agent-facing and simply missing.

Fix: derive the displayed state from the pending queue rather than from
`whose_move` alone (pending > 0 ⇒ the user's move), and advance `round` on a
**queued** agent revision as well as a committed one, so pins age under pulled.

**Differential control, run at T4.** Before the UI apply: `WHOSE_MOVE=agent`,
`ROUND=1`, `pin-late-source age 0r`, header *"Round 1+ — agent reading"*.
After clicking **Apply now**: `WHOSE_MOVE=user`, `ROUND=2`,
`pin-late-source age 1r`, header *"Round 2+ — your move"*. The diagnosis holds
and is now **scoped**: the freeze is not permanent, it lasts exactly as long as
the user does not apply — which under a genuinely pulled workflow (queue
several, apply later) is the whole span.

~~*Retracted claim:* `App.tsx:1221`'s `pending.length` arm is near-dead.~~ The
after-state shows it live — a commit landing while another revision is still
queued gives `whose_move = "user"` **and** `pending = 1`, and the chip read
*"revision waiting"*. Recorded rather than deleted so round 4 doesn't re-file
it.

### R3-3 — applying a revision for an artifact outside the current concept changes nothing on screen (P3)

Clicked **Apply now** on the `admin-console` banner. Server confirms it landed
— `REVN 4→5`, `ARTIFACTS=admin-console,daily-run-flow`, *Admin Console 1 view*
in the rail. On screen the canvas, the header's artifact name and the filmstrip
were all unchanged; the only feedback was the banner disappearing.

`currentConceptViews` (`App.tsx:577`) scopes the filmstrip to the concept
owning `currentArtifact`, and the agent had registered *Admin Console* as its
own concept, so the new artifact sits in a different group.

**Near-miss recorded so round 4 doesn't re-file the inflated version.** I first
measured this as *"the applied artifact is unreachable"*, and it is not —
opening `.artifact-dropdown button` yields `ARGUS / Argus Daily Run · flow` and
`ADMIN CONSOLE / Admin Console · wireframe`. The wrong reading came from an
invalid probe: `body.innerText` renders the **collapsed button's static `▴`
label** identically to an open-state indicator. The menu was simply shut. The
symptom matched the hypothesis for an unrelated reason.

### R3-4 — the PNG the agent takes of its own work disagrees with the canvas the user reads (P1)

Three renderings of one element. `m-news-label`, text `'News stream ·
continuous'`, **no newline**, `autoResize: false`, `156×40` — already sized by
`fit_label_in` for *two* lines — inside container `m-news` at `180×72`:

| Renderer | Result |
|---|---|
| **Live canvas** (zoomed screenshot) | wraps to `News stream ·` / `continuous`, inside the box — correct |
| **Server SVG** (`export`) | two `<text>` runs at `x=370`, the box centre — correct, via `wrap_label_text` at `canvas.py:3747` |
| **Client PNG** (`snapshot`, `TIER=1`) | **one line, clipped at both borders** — the `N` cut off left, the final `s` past the right edge |

Root cause `App.tsx:534`: the screenshot handler feeds `exportToBlob` from
`buffersRef` / `state.artifacts[aid].elements` — the **server's** stored
elements, with `text` unwrapped — and only falls back to
`apiRef.current.getSceneElements()`, the editor scene Excalidraw has already
re-wrapped, as the last resort.

`fit_label_in`'s docstring assumption — *"The client wraps bound text to the
width we allot — so keep `text` UNWRAPPED"* — is **true of the live canvas and
false of the export path**.

Why it is P1 rather than cosmetic: `snapshot` is the agent's own self-review
instrument, so it reviews a render worse than what it drew, and may "fix"
overflow that does not exist. Compounded by a tier split —
`apply --check --render` returned `DETAIL=chrome` (tier 2, SVG, wraps) while
`snapshot` with a tab attached returned `TIER=1` (client, does not wrap), so
**the same artifact renders two different ways depending on whether a browser
happens to be open.**

Same cause slug as R2-8 and R2-10 (`stored-vs-rendered`) — round 2's headline
cause, recurring in round 3 in a new place.

### ~~R3-5 — a tripwire armed by a *user* edit never reaches the agent (P1)~~ — **WITHDRAWN**

**Wrong as stated.** I enumerated four *CLI* surfaces and concluded no surface
carries a user-armed tripwire's question. I never checked the HTTP API — and it
is documented, at `ops-reference.md:304`, in the section headed *"Reading state
back"*, immediately beside `status`:

> `GET <url>api/state` → everything (registry, config, scenes, saves, pins,
> **tripwires**, pending).

The agent disproved it at T7: it ran `status | grep OPEN_TRIP`, got the bare
count, then called `urllib.request.urlopen('…/api/state')` and read
`s['tripwires']` directly. Its turn opened *"The EDGAR rename fired three
tripwires — all one cause, so I've answered the cause rather than the count."*
It **was** told, through a documented route, and acted. T5's miss was the agent
not looking — behavioural, n=1 — not an absent surface.

The narrow residue is re-filed as **R3-9** at P3. Kept struck through rather
than deleted so round 4 doesn't re-file the overclaim.

*What produced the error:* I checked the surfaces the agent uses *most* and
stopped when four came back negative, instead of enumerating the documented
routes. Four negatives felt like proof; it was four samples. The
`--found-by assessor-query` label is doing real work here — the same instrument
that found R3-2 produced this.

### R3-9 — user-armed tripwires are the only standing nag you must *pull* (P3)

`ops-reference.md:299–303` promises of `LINT_DEBT` and `PIN_DEBT`: *"Both also
ride every apply response."* Tripwires armed by a **user** save do neither —
`_print_debt` carries lint and pin debt only, `_print_tripwires` prints just
this batch's, `status` gives a count, `lint` doesn't carry them, and there's no
subcommand. The only route is `GET api/state`, which is documented and which
the agent used correctly.

So: an ergonomics asymmetry, not a hole. The other two standing nags are
**pushed**; this one must be **pulled** by parsing raw JSON. Observed
consequence, once: at T5 the agent ran `lint` and two `apply`s — none of which
mention tripwires — and missed my rename; at T7 it went to the API and caught
all three. Same agent, same session, two turns apart; the difference was
whether it thought to pull. The remedy is one line in `_print_debt`, not a new
subsystem.

### ~~R3-5, original text, retained for the record~~

`_print_tripwires` is called from exactly two sites, `canvas.py:8048` and
`:8074`, both inside `cmd_apply`, and both print tripwires fired by **the
agent's own batch**. Its docstring says so; both docs say so and are accurate
(`SKILL.md:330`, `ops-reference.md:335` — *"Tripwires fired by **your** batch
print as `TRIPWIRE=` lines"*). Four surfaces checked, four negative:

| Surface | Carries a user-armed tripwire? |
|---|---|
| `apply` / queued response | No — only this batch's |
| `status` | A bare **count** (`canvas.py:7738`), no question text |
| a `tripwires` subcommand | Does not exist |
| `_print_debt` standing nags | `LINT_DEBT` and `PIN_DEBT` only |

Observed at R5: I renamed the mapped element `enrich`; the tripwire fired
correctly (WP3 working). The agent then ran `lint` and two `apply`s and never
mentioned the rename. **It is not inattention** — no command it ran could have
told it, and the one carrying the count it last ran at the *end of the previous
turn*, before my edit existed.

The generalisable form, worth a discipline rule: **before tuning a check's
precision, prove its signal has a delivery path — who receives it, through
which surface, in every direction it can fire.** WP3 correctly narrowed the
arming verbs after round 2's eleven false tripwires, and thereby tuned the
accuracy of a channel with no delivery path in the direction the feature exists
for. Precision on an undelivered signal is invisible work.

### R3-6 — the queued apply branch skips the tripwire and standing-nag block (P2)

`cmd_apply`'s queued branch prints `queued/pending_id/reason/hint` and
`_print_layout`, then `return 0` — **before** the `_print_tripwires` and
`_print_debt` calls that both the committed branch (`8047–8049`) and the
offline branch (`8074–8075`) make.

Verified against the response the agent actually received under `pulled`:
`QUEUED=true`, `HINT`, seven `ECHO` lines, one `LAYOUT_NOTE` — and no
`LINT_DEBT`, no `PIN_DEBT`, no `TRIPWIRE`, at a moment when `status` reported
`OPEN_TRIPWIRES=1` and `LINT_DEBT=admin-console 2N; enrichment-mechanics 1E/1N;
registry 1N`.

**This one is a documented promise broken, not just an omission.**
`ops-reference.md:302` states of `LINT_DEBT` and `PIN_DEBT`: *"Both also ride
every apply response."* Under `pulled` cadence they ride none of them, because
every apply takes the queued branch. The doc is accurate for `per-round` and
false for the cadence the last two versions were built around.

**The weight is the "third time", not the omission.** The code comment directly
above this branch records that v0.4's assessment already patched one omission
here — *"a queued revision still owes its echo — swallowing it left the agent
with a success line and nothing to check it against"*. The echo was added; the
tripwires and the standing nags were not. Three defects across three
assessments on roughly eight lines.

So the fix must be **structural, not another patch**: the shape is an early
return skipping a shared epilogue, and a fourth fix that adds one more call
before it is a fix the next contributor forgets again. Single exit, a `finally`,
or hoist the epilogue into the caller — close the class, not the instance.
Filed under `pulled-blindness`, which now carries R2-3 and R3-6.

**A note on my own process.** I first read the scaled tier-1 PNG as showing
arrow labels overlapping foreign boxes and nearly filed *that*. An independent
overlap test using `canvas.arrow_label_anchor` over every bound arrow label
against every non-endpoint node returned **zero** overlaps, and the exact
coordinates agree (`a-edgar-fund-label` "financials" spans x 621–717;
`m-fundamentals` starts at x 760). The lint was right and the scaled picture
misled me. The wrapping defect is what was actually there.

---

## Round log, continued

### R8 — stale-then-commit (eviction)

Deleted `m-fundamentals`, an element pending #9 modifies, then applied #9.
Prediction recorded **before** the test. Result: `ERROR=invalid op batch: op 1
(mod): no element with id 'm-fundamentals' in this artifact`, `PENDING` 4 → 3
so the entry was **evicted rather than left armed** (v0.4's fix holding three
versions on), an `agent_revision_failed` event carrying the pending id and the
full error, non-zero exit. The v0.6 doc's still-open silent-drop class does
**not** extend to `mod` ops on missing targets.

The deletion also orphaned two arrows — **R3-13**, which neither the agent nor
I noticed, and which the *user* spotted in a screenshot four turns later.

### R9 — the fork

`checkout 13` + a save on top → `HEAD=alt-0019`, both branches intact. The
agent named the branch, corrected my mental model (*"it rewound the whole
canvas, not just the Brief"*), listed what is absent on the branch versus main,
stated correctly that there is no merge, and **declined the history operation
in favour of the artifact one**: *"branches are alternatives you switch
between, not versions you view together… what gives you before-and-after side
by side is a fourth frame."* That is a **selection** result — the first of the
method's three questions — and neither prior run produced one this clean.

It also found its own evicted revision unaided, by tailing the events log whose
path `status` prints.

### R10 — the compensating pair

Scope-narrowing ask (→ `rename_artifact`, item 7) plus the orphaned arrows
reported as a symptom with the cause withheld. The agent diagnosed both
orphans from that description alone, named them, and identified them as **one
deletion producing two symptoms at opposite ends of the diagram**.

Chasing a state anomaly nobody had probed — *why does an artifact already carry
a name from a still-queued batch?* — produced **R3-12** and, with it, the
explanation of **R3-10**. Two defects pointing opposite ways, whose combined
symptom ("the name never changed") was evidence for neither.

### R11 — the close

Applied all five remaining revisions; 8 views, `PENDING=0`. Ruled on the
Deep-Dive's two vintages (it is a Friday document — the weekly cadence is the
design, not a lag), released the blotter onto the compliance page, and asked
for handover in the user's terms: *"what would you put in front of them first,
second, third… the thing I most want them to understand isn't any single
picture, it's the stuff you and I worked out along the way."*

Item 2 was unblocked here: the price chart minted `dd-chart`, and shrinking it
200 → 120 re-derived both diagonals exactly.

---

## Verdict

### Final lint — 4 findings, zero unexplained

Round 1: **16**. Round 2: **5**. Round 3: **4** — of which **two are recorded budget overrides on one diagram**, both broken deliberately and for the same stated reason: *a legibility rule shouldn't win against a disclosure obligation*. Counted as **3 distinct causes**.

| Finding | Status |
|---|---|
| `admin-console`: three 680px inputs — "is every answer the same length?" | **Deliberate override.** The agent overruled it out loud at T2 and invited disagreement: settings rows with right-aligned values, so width carries no information |
| `argus-one-page`: budget override, 10 nodes / 10 arrows | **The mechanism working, not a defect.** The override carries its reason: *"the fourth source is a disclosure obligation on a compliance page — dropping it to meet a legibility budget would be the wrong trade"* |
| `registry`: `daily-run-flow` is named after concept `Run` but registered under `Argus` | **Known, deliberately unfixed.** This is R2-2, withdrawn in v0.6 because the fix broke two tests that encoded the opposite behaviour. It fired exactly where the v0.6 notes predicted |

Nothing at close is unexplained, and nothing is mine — my own pollution (the
sticky-note overlap in `weekly-brief`, the `ZZZ-DRY-RUN-PROBE` rename, the
26px nudge) was caught by the lint where applicable and cleared before the
count.

### Project shape

8 views · 28 saves · 16 concepts · 4 mappings (2 `intentionally-divergent`,
both `kinds`-scoped) · 14 glossary terms · 16 pins (5 open) · 5 tripwires
(1 open) · 1 ADR · 2 branches.

### The headline

Round 2's was *the model believes something about the drawing that the drawing
does not say*. Round 3 forces the mirror onto it:

> **The model and the artifact can disagree in either direction, and nothing
> checks.**

| | The model believes | The artifact does |
|---|---|---|
| R3-4 | the PNG shows what the user sees | the canvas wraps; the export does not |
| R3-10 | the record's rename is the artifact's name | the same record stores the old one |
| R3-12 | a dry run changes nothing | it wrote to disk, unversioned |
| R3-13 | an arrow points at something | its target was deleted |
| R3-16 | an endpoint is on the border | it is 56px inside the box |

R3-12 is the sharpest because it runs the *other* way from every prior
instance: the artifact says something the model never recorded — no revn, no
save record, no event. **The only way to undo an unversioned write was another
unversioned write.**

Second, narrower cluster — `many-to-one-as-pairs` — is the same disease in the
registry rather than the geometry: a mapping is stored as a relation over N
elements and **every consumer immediately explodes it into pairs**. Two
independent checks (R3-7, R3-8). Scoped to the checks that *read* mappings: the
eviction path touches mappings not at all, which is what keeps this a pattern
rather than a posture.

### Measurement validity — read this before the counts

**The instrument changed mid-measurement.** A6b (discriminate before filing),
the attribution axis, `--found-by`, the instruments section and the cold look
did not exist for round 2; several were written *during* round 3 in response to
it. R3-12 came from chasing an anomaly; R3-10's proof came from distrusting a
confound. Round 2 had neither practice.

> Round 3 found more P1s than round 2 **with a better instrument**. These
> counts are not like-for-like and must not be read as a trend. What *is*
> comparable is round 2's findings re-tested item by item — which is what the
> re-test checklist is for, and why it, not the totals, is the spine of the
> comparison.

The one row of the cross-run diff that *is* meaningful:

| | r2 | r3 |
|---|---|---|
| `tool-silent-covered` | 3 | **0** |
| `found-by agent` | 3 | **0** |

Round 2 had three findings where the agent's diligence covered a hole the tool
never mentioned. Round 3 has none. That is what v0.6 bought.

---

## Findings — authoritative index

Regenerated from `.ledger/r3.jsonl` at close. Full evidence, rivals and
discriminators: `ledger.py --run r3 report`. The prose above covers R3-1
to R3-6 and R3-9 in depth; this is the complete list and the numbers of record.


| # | Sev | Attribution | Found by | Cause | Finding |
|---|---|---|---|---|---|
| r3-10 | **P1** | `tool-silent-shipped` | assessor-query | mixed-batch-write-order | rename_artifact is silently reverted when the same batch also draws, and the save record stores the stale name so the revert is durable (`canvas.py:5681`) |
| r3-12 | **P1** | `tool-silent-shipped` | assessor-query | dry-run-side-effect | apply --check persists a rename_artifact to disk — the dry run mutates the project outside the commit history (`canvas.py:6347`) |
| r3-13 | **P1** | `tool-silent-shipped` | user-look | binding-to-nothing-unchecked | deleting a node orphans its bound arrows, and the detached-endpoint lint explicitly skips bindings whose target no longer exists (`canvas.py:4098`) |
| r3-2 | **P1** | `tool-silent-shipped`<br>`capability-undocumented` | assessor-query | pulled-whose-move | under pulled cadence nothing commits, so the header, the round counter and pin aging all freeze until the user applies (`canvas.py:5692`) |
| r3-4 | **P1** | `tool-silent-shipped` | assessor-look | stored-vs-rendered | the PNG the agent takes of its own work renders unwrapped label text, so it disagrees with the canvas the user reads (`App.tsx:534`) |
| r3-6 | **P1** | `tool-silent-shipped` | assessor-query | pulled-blindness | the queued apply branch returns before the tripwire and standing-nag block, so pulled cadence silently loses a documented promise (`canvas.py:8036`) |
| r3-1 | **P2** | `tool-silent-shipped` | assessor-look | bbox-anchor | the pin ❓ glyph is placed at the target's bounding-box corner, so on a diamond it floats clear of the shape (`canvas.py:2177`) |
| r3-16 | **P2** | `tool-silent-shipped` | user-look | endpoint-check-one-sided | an arrow that terminates deep INSIDE its bound box passes the detached-endpoint check, which only tests the far side (`canvas.py:4101`) |
| r3-17 | **P2** | `tool-silent-shipped` | agent | registry-is-branch-blind | the registry is global while scenes are per-branch, so on a branch it asserts views that do not exist and marks their debt paid (`canvas.py:5408`) |
| r3-7 | **P2** | `tool-false-alarm` | assessor-query | many-to-one-as-pairs | a divergence tripwire fans out as arity-minus-one questions, because emission iterates pairs while suppression reasons about the mapping (`canvas.py:5834`) |
| r3-11 | **P3** | `tool-silent-shipped` | assessor-query | branch-origin-unjoined | a branch records neither why it exists nor where it forked, and its head advances past the fork point (`canvas.py:5685`) |
| r3-14 | **P3** | `tool-silent-shipped` | assessor-query | apply-response-omits-branch | the apply response never names the branch it landed on, though the UI's save toast does (`canvas.py:8044`) |
| r3-15 | **P3** | `tool-told` | assessor-query | state-claim-from-memory | the agent asserted a queue state it had been shown correctly, without re-reading after changing it _[sample of one]_ |
| r3-3 | **P3** | `tool-silent-shipped` | assessor-look | apply-feedback-invisible | an applied artifact outside the current concept never becomes reachable in the filmstrip (`App.tsx:577`) |
| r3-8 | **P3** | `tool-false-alarm` | assessor-look | many-to-one-as-pairs | the 3.2.4 one-function-one-label check explodes a many-to-one mapping into a cartesian product, so a legitimate compression reads as three names for one action (`canvas.py:5141`) |
| r3-9 | **P3** | `tool-told-ignored` | assessor-query | standing-nag-asymmetry | user-armed tripwires are the only standing nag with no CLI surface and no ride on the apply response (`canvas.py:8085`) |
| ~~r3-5~~ | ~~P1~~ | — | — | — | ~~a divergence tripwire armed by a USER edit has no surface that carries its question to the agent~~ — **WITHDRAWN**: WRONG AS STATED. I enumerated four CLI surfaces and concluded no surface carries a user-armed tripwire's question. I did not check the HTTP API, and it is documented — ops-reference.md:304, in the section headed 'Reading state back', immediately beside status: 'GET <url>api/state -> everything (registry, config, scenes, saves, pins, TRIPWIRES, pending)'. The agent proved it at T7: it ran status | grep OPEN_TRIP, got the bare count, then called urllib.request.urlopen('http://127.0.0.1:45353/api/state') and read s['tripwires'] directly, and its turn opened with 'The EDGAR rename fired three tripwires - all one cause, so I've answered the cause rather than the count.' The agent WAS told, through a documented route, and acted. T5's miss was the agent not looking, which is behavioural and n=1, not an absent surface. Residue re-filed as r3-9 at P3. |


## Causes

- **many-to-one-as-pairs** (2) — r3-7, r3-8
- **apply-feedback-invisible** (1) — r3-3
- **apply-response-omits-branch** (1) — r3-14
- **bbox-anchor** (1) — r3-1
- **binding-to-nothing-unchecked** (1) — r3-13
- **branch-origin-unjoined** (1) — r3-11
- **dry-run-side-effect** (1) — r3-12
- **endpoint-check-one-sided** (1) — r3-16
- **mixed-batch-write-order** (1) — r3-10
- **pulled-blindness** (1) — r3-6
- **pulled-whose-move** (1) — r3-2
- **registry-is-branch-blind** (1) — r3-17
- **standing-nag-asymmetry** (1) — r3-9
- **state-claim-from-memory** (1) — r3-15
- **stored-vs-rendered** (1) — r3-4


## Confirmed working

_Mechanisms observed doing their job. A run that found six defects across forty exercised mechanisms is not the same result as six across eight, and the findings table alone cannot tell them apart._

- **WP1a arrow_label_anchor** — 9 relationship labels on the domain model at drift=0px, no overlaps; 2 on daily-run-flow and 7 on enrichment-mechanics likewise. An independent overlap test using the same helper over every bound arrow label vs every non-endpoint node returns zero across all artifacts
- **vocabulary propagation** — T6's 'we say alpha, not excess return' ruling appears as the attribute 'expected alpha' on the Signal entity in argus-domain, a drawing made after it; the agent also recorded 'excess return' as a term-to-avoid rather than merely omitting it
- **WP2 variant-frame check** — fired on a one-frame rename with both frame names, both strings and the var:weekly-brief:brief-synthesis waive key; explicit customData.variant_of path exercised, inferred fallback untested
- **WP3 divergence verb scoping** — differential control, three separate saves: move 40px -> 0 tripwires, tooltip -> 0 tripwires, rename the same element -> 1 tripwire. In run 2 the first two both fired
- **intentionally-divergent + kinds scoping** — re-test item 7 PASS: after an explicit 'they are meant to disagree' the agent annotated the mapping intentionally-divergent with kinds ['label_renamed','renamed'] rather than blanket, GENERALISED it unprompted to a second mapping ('same altitude rule as mapping 1'), and the annotation cleared both the four open tripwires (4 -> 0) and the standing 3.2.4 note (admin-console 2N -> 1N). Run 2 also passed this item, so this is stability rather than a new result — but the generalisation to a second mapping is new
- **documented api/state as the full-state route** — the agent reached tripwire questions the CLI does not print by calling GET api/state, exactly as ops-reference.md:304 describes; it then answered the CAUSE rather than the three counts — 'all one cause, so I've answered the cause rather than the count'
- **pending eviction on user-invalidated work** — T8 predicted H1 and got it. Deleted m-fundamentals, then applied pending 9 which mods it: ERROR=invalid op batch: op 1 (mod): no element with id 'm-fundamentals' in this artifact (check the artifact's current elements via canvas.py status or the state API); PENDING 4->3 so the entry was EVICTED not left armed (the v0.4 fix holding); agent_revision_failed event emitted carrying pending_id and the full error; non-zero exit. The rival — the v0.6 doc's still-open silent-drop class, batch lands minus one op with nobody told — is FALSIFIED for mod ops on missing targets
- **rename_artifact reached for unprompted (re-test item 7)** — the one-pager's audience rejected it so its scope narrowed; the agent chose rename over redraw and said why — 'Renamed rather than redrawn, so its id, history, mappings and pins all survive — only the title in the rail moved.' The OP was reached for correctly and its EFFECT was then lost to r3-10; scoring the selection, not the outcome
- **branch narration without documentation (prediction (c) FALSIFIED)** — I predicted the agent would not name the fork because version branching is essentially undocumented (a grep of SKILL.md + all references yields flowchart branches and one branch_switch event name at SKILL.md:256). It named alt-0019, explained that checkout rewound the WHOLE canvas not just the Brief, enumerated what is absent on the branch vs main, stated correctly that there is no merge, and redirected the user to a fourth frame as the thing that actually delivers side-by-side. H2 wins: HEAD=<name> in status plus the state was enough
- **symptom-without-cause diagnosis (probe 3)** — I reported the orphaned arrows the way the persona would — 'a line arriving at the risk model that starts in the middle of nowhere' and 'the financials line stops before it gets anywhere' — and withheld that my own deletion caused them. The agent diagnosed both from that alone, named a-edgar-fund and a-fund-down, identified them as ONE deletion producing two symptoms at opposite ends of the diagram, and explained why they did not look related. It also caught the second-order risk unprompted: 'I'd otherwise have re-drawn the enrichment view with three scorers and quietly rewritten the sharpest finding on it, since EDGAR takes out three of four becomes two of three'
- **text-fit lint and the drawings themselves (cold look, corroboration by contradiction)** — The cold observer reported text overflowing or colliding in FOUR of six artifacts — 'SEC EDGAR · event-driven' cut by both borders, 'Score (4 scorers, 4 clocks)' overflowing both sides, the sticky running 80 percent past its own edge, two green annotations printed over each other on the one-pager, 'D2 · Record of each run' overflowing its dashed box. NONE of it is in the drawings. The server SVG wraps every one: 'Score (4' / 'clocks)', "until it's 48h old", 'Nothing Argus produces leaves the firm. Every', 'hours before they can be acted on', 'D2 · Record of each'. The lint is correct to be silent because it measures the WRAPPED dimensions. So the drawings are sound, the lint is sound, and the artifact I handed the observer is not — see r3-4
- **the overlap lint on user-authored notes** — the cold observer called weekly-brief panel 3 'pure mush' where three text runs interleave. The lint had already caught it, three times, naming the cause: usernote-56aba555 'grew to fit its label and now overlaps notice-title', the same against notice-body, plus a label-pair overlap — each with the remedy. Cause was MY sticky note, so this is assessor pollution correctly detected: tool-told, and subtract from the tally
- **the cold look as an instrument (first run)** — Six artifacts, fresh subagent, no transcript, no findings list, no beat list, prompt was 'does anything here look wrong, judge them as drawings'. Yield: independently rediscovered r3-13 with better measurements than mine (155px gap, 40px height mismatch, 'a line ending nowhere plus a line beginning nowhere'), independently rediscovered r3-1 AND extended it — 'the badges hug a corner in the other diagrams, so the anchoring convention is inconsistent across the set' — produced the corroboration-by-contradiction that quantifies r3-4's cost, produced one genuinely new finding (r3-16), one false positive (the sag), and one held candidate. It also confirmed the 26px LAYOUT_ERROR is real by describing it unprompted as 'terminates in empty space, arrowhead lands roughly 35px above the box'
- **export --with-footnotes as handover (re-test of R2 item 6)** — offered unprompted and executed: seven SVGs each carrying its hover tooltips as numbered footnotes plus the appended glossary, a HANDOVER.md with a first/second/third reading order, and the three cross-cutting rationales written out as prose because 'none of them survive looking at a picture'. It also named what does NOT survive the export — walk mode and the five open pins — rather than letting the user discover it


### Tally at close

    run r3  target wysiwyg-grilling  vs v0.6
      turns             11
      findings          16  (15 distinct causes)
      withdrawn         1
      severity          P1 6  P2 4  P3 6
      attribution       tool-told 1  tool-told-ignored 1  tool-false-alarm 2  capability-undocumented 1  tool-silent-shipped 12
      found by          agent 1  assessor-query 9  assessor-look 4  user-look 2
      → code fixes      14
      → docs fixes      2
      → 6 found only by LOOKING at a rendered artifact — no state query would have reached them
