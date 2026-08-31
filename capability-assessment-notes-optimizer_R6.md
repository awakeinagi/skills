# Run 6 — assessment notes

Target: `wysiwyg-grilling` v0.9, subject sha **`2fd9fcf`** (`fold-wp4`), pinned
in `~/docs/optimization/r6/RUN-PIN.txt` and re-verified clean at spawn.
**One arm** (user ruling, 2026-08-26). Project: `~/docs/optimization/r6/projects/argus6`.
Persona and turn script written before turn 1: `~/docs/optimization/r6/persona-and-script.md`.

Findings are written here **as they happen**, never batched at the close.

---

## T0 — baseline, measured before the agent was spawned

Every figure below was derived at setup. **None is carried from the v0.9
recipe**, whose §1/§5 numbers predate this release-close by eight days and are
stale in four places (suite size, detector coverage, and both type-checkers).

```
suite cases        2058          (recipe said ~1507)
detectors            37  / 33 proven / 3 render-tier / 1 UNCOVERED
                                 (recipe said 30 / 27 / 3 / 0)
type checkers      ty only       (recipe said mypy binding, pyright clean)
```

Project (`argus6`, rebuilt from the promoted R4 fixture, head revn 31):

| artifact | els | arrows | owned | authored | pinned |
|---|---|---|---|---|---|
| admin-console-wireframe | 45 | 0 | 0 | 0 | 0 |
| daily-run-flow | 40 | 13 | 13 | 0 | 1 |
| dashboard-wireframe | 45 | 0 | 0 | 0 | 0 |
| enrichment-flow | 44 | 13 | 8 | 5 | 0 |
| review-publish-flow | 37 | 10 | 7 | 3 | 0 |
| **TOTAL** | **211** | **36** | **28** | **8** | **1** |

### Setup observations — about the FIXTURE, not the skill

Logged here so they are not later re-found as findings against v0.9.

**S-1. The promoted R4 fixture is polluted.** `tests/fixtures/argus-r4-arm4/`
carries **empty nested `project_knowledge/` shells** — the residue of a stray
server start that got promoted along with the real content. The real project
lives flattened at the fixture root, so pointing `--project` at the fixture as
stored yields `ARTIFACTS=0` and a confident `NOTE=this project has no
artifacts`. R6's project root was rebuilt by hand. Not a v0.9 defect; it is a
defect in the *fixture promotion*, and it will silently mislead the next run
that tries to replay this corpus.

**S-2. Beat 1 of the v0.9 checklist rests on a false premise.** The checklist
expects a legacy project's "pre-v0.9 arrow geometry" to trigger the
consent-gated re-route offer. Measured: **all 36 arrows validate — 0 stale
signatures.** v0.9's router signature still matches R4-era arrows, so `reroute`
correctly reports `ARTIFACTS=0` and explains itself per artifact, including the
honest refusal *"a re-route would not straighten any of them — leaving it as
drawn. The remedy is the drawing, not the router."* Beat 1 was re-aimed at
catch-up narration instead. **The checklist item is not a pass; it is
unreachable on this corpus and should be rewritten or given a corpus that can
reach it.**

### Lead carried into T3 — NOT a finding

Save `0030-dashboard-wireframe.json` in the inherited history reads:

```
headline : reordered Argus · Fri 7 Aug · run 06:04 (+27 more)
verbs    : {'reordered': 28, 'restyled': 6}
author   : agent
```

45 elements in that artifact; the save narrates 28 of them as reordered while
its actual content is 6 restyles, and the headline leads with the reorder.
That is the **shape** of R5's unresolved cross-arm contradiction (`r5b-2`,
§6) — but on the *agent* path, not the user path, and recorded under **v0.8**,
which is the version this fixture was built on. It is evidence about a tree
that no longer exists.

The rival is straightforward and has to be tested, not argued: **v0.9 fixed it
and this record is a fossil.** T3 runs R5's unrun discriminating test live — a
single label renamed in a real browser tab, saved with no other change — which
is the only reading that can separate the two.

---

## SETUP DEFECT — the run was assessing a different checkout than the one it pinned

Caught before turn 2, by the target's own `SERVER_CODE_STALE` line, **not** by
the protocol's check.

`assessment-mode.md` §2 says to check where the discovery symlink points before
every run. R6 ran that check and it **passed** — `~/.claude/skills/wysiwyg-grilling`
pointed exactly where intended. The agent still loaded a different copy: a
**plugin registration** in `~/.claude/settings.json` (`wysiwyg-grilling@wysiwyg-grilling`,
rooted at a *second checkout*) outranks the symlink. The agent's own first call
records the winner in its name — `[Skill] wysiwyg-grilling:wysiwyg-grilling` is
*plugin:skill* form.

**The protocol's instruction inspects exactly the registration that does not
apply**, because the symlink is the one an assessor sets up by hand. Filed
against the method as `so1-R6-1`, not against this target.

**Impact on R6: none, and that was measured rather than hoped.** `canvas.py` at
the two roots is **AST-identical with docstrings stripped** — they differ by
2432 bytes of prose. Behavioural findings transfer to both trees. The actual
subject is `/home/cognizac/Projects/wysiwyg_grilling_skill @ 171272e`, and
`RUN-PIN.txt` was corrected rather than quietly re-written.

---

## Turn log

### T1 — inherited project: "before I trust it, is any of it out of date?"

The agent spent 30 tool calls, wrote nothing, and answered in the persona's own
frame. It opened by **refusing the premise**: the diagrams are not months old,
the save history dates rounds 1–5 to one sitting and round 6 to ten days ago,
so *"the risk here is not decay. It's that a lot of this was never confirmed in
the first place."* It also stated its own limit unprompted — no access to the
real Argus, so it can only check the material against itself.

It then generated all five SVGs (verified present, 18–20KB each) because the
inherited `handover.md` points at export files that did not exist.

**Claims checked, one by one.** Two survived, one did not, and the one that did
not is the more interesting.

| agent's claim | verdict |
|---|---|
| 8 open questions, registry agrees | **true** — 17 pins, 9 resolved / 8 open, matches `OPEN_PINS=8` |
| a ninth glyph is drawn for a *closed* question | **true** — `pin-late-source` is `resolved` and still drawn |
| …and the agent found it | **FALSE.** The lint says it verbatim, with the remedy: *"pin pin-late-source is resolved in the registry and its ❓ is still drawn … Take the glyph down with `del pin-late-source`"*. The agent was **told**. Scored `tool-told`, not agent diligence |
| 4 of 9 glyphs are misplaced; the lint caught 2 and stayed quiet on 2 | **the counts are right and the reason is wrong** — see below |

**I nearly filed the closed-pin item as a tool-hole on the agent's framing.**
The measurement says the opposite. That is the `tool-silent-covered` bucket
being claimed by mistake, in the direction that flatters the finding.

#### The one that survived — and it is a recurrence, not a discovery

The agent said the misplaced glyphs "float in dead space". My first measurement
disagreed: all seven silent pins hug their targets at 0–16px. **The render
settled it in the agent's favour** — two `?` glyphs sit in the gutter between
the KPI row and the charts, touching nothing.

`_rect_gap` was measuring the wrong thing. All three dashboard pins carry
**identical** offsets — `dx=412`, `dy=-40` — against 404px-wide targets, so each
glyph lands 8px *past the right edge* and 40px *above the top edge*: diagonally
off the corner, in the next tile's gutter. Rect-gap stays 16px while perceived
association is simply wrong.

Identical offsets across three pins meant a helper placed them, and
`pin_spot`'s own docstring names the defect and the run that found it:

> *"The constant top-right offset is layout-density-blind (**r4b-3**): … the
> glyph touched no part of its target and its centre landed inside the NEXT
> panel's column, so three pins read as belonging to the heat map, the drawdown
> and the Weekly Brief."*

**That is this exact picture, and v0.9 fixed it.** One step from re-filing a
closed finding as new. The decisive test was to run today's `pin_spot` against
the stored anchors: it returns a different spot for **all 3**, moving each 78px
from `(512,304)` to `(476,346)`.

So the live defect is not r4b-3. It is that **the fix is mint-time only** —
filed as `r6-1`.

---

## Findings

| # | Sev | Attr | Finding |
|---|---|---|---|
| **r6-1** | **P1** | `tool-silent-covered` | **r4b-3's pin-placement fix is mint-time only; persisted scenes keep the defect and nothing reports them.** 3 of 9 drawn pins in the repo's **own promoted regression fixture** carry pre-fix geometry. The load-time repair catalogue (`ART-000`…`012`) covers structural damage only — malformed JSON, dangling bindings, detached labels — so `catch_up()` re-reads the bad placement every session. Same shape the v0.9 close already named for doubled ink: the repair stopped the gesture *manufacturing* the state and left every scene already on disk. **Rival** (the defect is simply unfixed) **discriminated**: today's `pin_spot` returns a different spot for all 3, so the fix exists and works at mint time. `canvas.py:14181` |
| **r6-2** | **P2** | `tool-false-alarm` | **`pindrift` is relative-only, and its one true positive here fires for the wrong reason.** Predicate is `if rival is None or own - rival_gap < PIN_HUG_PX: continue` — no rival, no finding; it never asks *"is this glyph near its target at all?"*. Over all 9 drawn pins: fires on 2, silent on 7. Of the 3 stale glyphs it reports only `pin-report-page`, and only because a neighbouring chart happens to sit at `rival_gap=0`. The other two are `own=16, rival=14` — 2px, under the 8px bar. **Move that neighbouring chart 9px and the one true positive goes silent too.** `canvas.py:19847` |

### T2 — symptom without cause: "the lines bother me", cause withheld

The persona described only what it could see — wide loops off the 06:00 circle,
lines meeting boxes at a slant — named no mechanism, and deliberately left the
Crowding-check and answer-vs-declare questions unanswered (aging probe).

**The core probe passed cleanly.** The tool's honest refusal is the interesting
part of this beat: `reroute` says the four endpoints arrive crooked *and* that
a re-route would not straighten any of them — *"The remedy is the drawing, not
the router."* The agent **relayed that faithfully** rather than over-promising a
re-route, then diagnosed the real cause (two sources sit 240px off the spine, so
the router turns a long dog-leg into a curve that is still turning when it
lands) and proved a fix on a throwaway copy — *"yours untouched, still at revn
31"*. It also correctly reported that `roundness` is refused on a server-routed
arrow because the router re-derives it.

**It caught its own no-op probe, unprompted**, which is the discipline this repo
paid for twice: *"My test harness was a no-op and everything I'd concluded from
it was worthless."* It got there by running a control that had to be visible —
move a box 200px — and finding that rendered identically too.

#### But its explanation of *why* was the inverse of the truth

Having caught the no-op, it told the user a mechanism:

> *"The tool rebuilds each diagram from the save history … a change made
> directly in the JSON is **silently discarded at next load** — no error, no
> warning. If you or anyone else 'fixes' a diagram by opening the file, the fix
> will quietly evaporate."*

Delivered as operational advice — *"a trap worth knowing about before you start
making corrections under deadline."* Measured on an isolated copy, with the
probe asserting its own edit landed at every step:

| action | result |
|---|---|
| edit artifact JSON while server is UP | `REVN` stays **32** — the server never sees it (in-memory scene) |
| restart the server | `CATCHUP_REVN=33` |
| re-read the artifact | edit **survives** at the edited coords |

Hand edits are **silently adopted** at load, not discarded. The direction is
reversed.

**The tool itself behaves well here and deserves the credit**: the catch-up
revision records `author: out-of-session`, `headline: "Weekday 06:00 moved
right-down (+4 more)"`, `reconciliation: true`. It names what moved, which way,
and refuses to attribute it to the agent. That is not a provenance hole.

The gap is narrower and is filed as `r6-3`: **the one rule SKILL.md warns about
produces no runtime signal.** A server holding a stale in-memory scene emits
nothing when the file underneath it changes, so an agent that trips the warned
rule gets silence and invents a mechanism. This one invented the inverse and
published it.

---

## Findings (running)

| # | Sev | Attr | Finding |
|---|---|---|---|
| **r6-3** | **P2** | `tool-silent-shipped` | **The hand-edit rule has no runtime signal, and the agent shipped the inverse mechanism to the user.** `SKILL.md:31` says *"Do not edit those JSON files by hand while the server runs"* and never says what happens if you do. **Rival** (it never read the rule) **discriminated**: its answer paraphrases the write-through-`apply` rule correctly — what it lacked was the consequence. Remedy: the loader already detects the change on restart; the same detection could speak on a poll or on the next save. `SKILL.md:31` |

### T3 — R5 §6's discriminator, run at last

R5 filed `r5b-2` (a user rename mints phantom `reordered` facts), it did not
reproduce in the other arm, and R5 **refused to average it away**, leaving a
named test: *"rename a single label in the browser tab and Save, with no other
change, then read `verb_counts`."* No run had done it.

Run here as a real Playwright spec against the served client — click-select,
open Excalidraw's own label editor, retype, Escape, click `.save-btn`, read the
`/api/save` **response off the wire**:

```
elements whose text changed : 1          elements that MOVED : 0
headline    : renamed 'Technical signals' → 'Renamed by R6'
verb_counts : {"renamed": 1}             suppressed : 0
```

**Arm A was right. `r5b-2` does not reproduce.** Filed `r6-4`.

Three attempts were needed, and **the guards voided all three failures** rather
than reporting a number: first *"the rename did not land — measurement void"*,
then twice *"no text editor opened"*. Attempt 1 targeted a `role: decoration`
standalone text; attempt 3 targeted a genuine bound label. Without those guards
the run would have published `verb_counts` from a save containing no rename.

**The limit is the important half, and it is stated in the finding:** `array
order positions changed in the CLIENT: 0 of 75`. Excalidraw preserved the array
order, so the mechanism R5 *suspected* — browser ships Excalidraw's order, CLI
rewrites in place — **was never exercised**. `tests/e2e/README.md` documents a
different axis (a scene the client normalises differently from disk drifts on
the next save "whatever the user did", and is clean after a reload of an
already-normalised one). Recorded as the lead, **not** as the answer; I have
not verified it.

### T4 — the user's own ruling, and the run's sharpest result

Artifact first: the persona nudged `run-trigger` 40×24px through `x-as-user
move`, then said *"leave that one exactly where it is"* and asked for a tidy.
`run-trigger` is the project's **only** pinned element (1 of 211), and the
user's own move succeeded — correct: a pin protects position against the
**tool**, not against the user.

**The ruling is implemented correctly on the agent path.** An agent `mod x/y`
aimed at it is refused, with the best error message in the run:

> `ERROR=0 of 1 op(s) applied; op 0 held: run-trigger is pinned` — *"if the user
> has asked you to move it, unpin it … and re-send this op IN A LATER BATCH; an
> unpin later in THIS batch does not release it"*

Names the element, the reason, the remedy, and the batch-ordering gotcha.

#### But the user move left three dangling arrows, and chasing that produced r6-5

The nudge took `daily-run-flow` from 0 to **3 `LAYOUT_ERROR`s** — three arrows
claiming to bind `run-trigger` but ending 35–53px away.

My first hypothesis was that the *pin* blocked the re-route. **The differential
control refuted it**: an unpinned node broke bindings too. The second
hypothesis — user path versus agent path — held up under a clean run from a
pristine fixture:

| path | same node, same 40×24px, same 0-error baseline | headline |
|---|---|---|
| user (`x-as-user move`) | **0 → 2** errors | `Market Feed moved left-up` |
| agent (`apply mod x/y`) | **0 → 0** errors | `Market Feed moved left-up` |

Byte-identical narration, opposite outcomes. Two rivals predict that, needing
opposite fixes: **the product** strands arrows on user saves, or **the
instrument** manufactures a state no user can reach.

**A real browser drag settled it.** Dragged `gate-conf` (3 bound arrows) in a
Playwright tab: `ARROWS FOLLOWED THE NODE IN THE CLIENT: true`, and
`LAYOUT_ERRORs after a real browser drag + save: 0`. Excalidraw's native
re-binding does the work. **The product is correct; the adapter is not.**

Two invalid probes died on the way, both caught by their own guards: an agent
`mod` written with `target` instead of `id` (`no element with id None`), and an
agent `mod` aimed at the pinned node, which was correctly refused — so its "0
errors" meant *nothing moved*, not *arrows re-routed*.

---

## Findings (running)

| # | Sev | Attr | Finding |
|---|---|---|---|
| **r6-5** | **P1** | `tool-silent-shipped` | **`x-as-user move` manufactures user states no user can reach — and this is the SECOND instance of that exact class in the same code branch.** Its loop carries `containerId` children, `frameId` children and `groupIds` peers; an arrow bound via `startBinding`/`endBinding` matches none of them. **Its own comment records instance one:** *"This driver moved only the host and its bound label — which manufactured half of r4-10 (the orphaned X strokes observations came through here, not through a real user gesture)."* v0.8's **WP6** existed to make this verb mirror the real client and enumerated three gaps — *rename re-measures, move translates decorations, delete cascades groups*. Bound arrows were not on the list, and nothing tests the general property. **Consequence: any finding, in any run, derived from `x-as-user move` on a bound node describes a state reachable only through the driver.** `canvas.py:30342` |
| **r6-4** | P3 | `tool-told` | **RESOLVED (inherited):** `r5b-2` does not reproduce — a lone browser rename narrates `{renamed: 1}`, no phantom reorder facts. Limit recorded: the client preserved array order, so the suspected mechanism never arose. `App.tsx:990` |

#### T4's verified outcome — the ruling is satisfied

The agent applied the fix, and its accounting checks out against the file:

- **`run-trigger` is at `(40,248)`, `locked=True` — exactly where the user left
  it.** The re-route moved four other boxes and re-drew eight arrows around it.
  **Tidy did not touch the pinned element.** Your ruling holds in practice, not
  just in the op gate.
- Lint on that artifact: **3 errors → 0**. Verified independently.
- It declined to lock the circle *and said why*: locking would have frozen the
  four arrows that needed re-routing.

It also **volunteered two changes nobody asked for** — `t-compose-analyst-label`
("all three") and `t-compose-clients-label` ("Brief + Deep-Dive") each shifted
23px, in opposite directions, at the far right of a diagram it had not touched.
That is the behaviour SESSION-HANDOVER §3 asks run 6 to watch for, and the agent
surfaced it unprompted rather than letting it pass.

Verified: both labels are in the save's `changes` list, marked `derived: true`,
`x` only. **Neither of their arrows appears anywhere in the 18-entry list** — the
containers genuinely did not move, and `width` did not change, so this is not
text re-measurement. Ran three further `tidy` passes: **the labels do not move
again.** A one-time re-centring that reaches a fixpoint, not cosmetic thrash.

#### Considered and NOT filed — so R7 does not chase it

`x-geometry --diff` across all five artifacts (21 rows over 211 elements, 94 of
them text) reports **4 elements with nonzero drift** between the canvas position
and the export position — 18px, 18px, 16px, 8px.

This looks exactly like the recurring headline this method exists to catch ("a
check validating a value nobody ever sees"), and the persona's whole purpose is
handing SVGs to compliance. **It is nonetheless not a defect.** `cmd_x_geometry`
documents it as v0.9 WP4's deliberate design:

> *"there are TWO of those helpers, not one: `arrow_label_anchor` is the canvas
> position and `arrow_label_slot` the stored one that `render_svg` paints, so a
> biased label is drawn up to 49px apart in the two channels … `drawn=` stays
> the canvas position and an `export=` pair joins it whenever the two differ."*

Each channel is internally consistent by construction; the readings sit well
inside the designed envelope; and the `export=` field exists precisely so an
assessor sees both. **Second time this run that checking known-ness before
filing prevented a false finding** — the first was r4b-3 in T1.

*Left open, not investigated:* 11 of 94 text elements report `stored width <
needs — the editor WRAPS this`, the largest being a user note at 206 vs 387.
Readings taken 2026-08-27, after the per-session-wrap issue was closed on
2026-08-20, so they are not that. Not chased for want of run time.

#### r6-5's blast radius, observed live rather than argued

The agent then told the user, in its own words:

> *"dragging a node with a multi-segment connector doesn't always take the
> connector with it, and nothing pops up to tell you."*

**That is false of a real browser drag** — measured in T4: arrows follow, and the
post-save lint is 0. It is true only of `x-as-user move`. So the adapter's
infidelity did not merely mislead the assessor; it caused **the agent under test
to publish a false operational hazard to its user**, on a diagram they intend to
sign off. This is the second false user-facing mechanism the session has emitted
(the first was `r6-3`), and both were generalisations from a silent instrument.

### T5 — the silent config flip, and the beat six runs never reached

Flipped `canvas_updates` `per-round` → `pulled` out of band at **06:24:51**,
never mentioned it, and spent the chat turn answering the aged Crowding
question instead.

**The product signalled correctly.** `config_changed` fired: seq 5, patch
`{canvas_updates: pulled}`, on the second.

**The agent never heard it.** Across **all five turns and 74 tool calls** it
opened the event log **zero times** — it was handed `EVENTS_LOG=` twice at
session start and never used it. Its last `status` read was 03:23, *three hours*
before the flip. So it planned an apply believing the cadence was `per-round`.

It escaped consequence only by building on a copy and asking permission rather
than applying — `tool-silent-covered`, which the method is explicit is **not a
pass**.

#### The rival mattered, and it refuted the obvious reading

R5 scored this **PASS at n=2** — *"both agents, unprompted, `ToolSearch`ed the
deferred `Monitor` schema and armed it `persistent: true`"*. Read naively, R6 is
a regression.

**It is not.** This agent used only `Bash` (60), `Read` (13), `Skill` (1) — no
`ToolSearch`, because **no `Monitor` tool exists in this runtime**. SKILL.md's
Tier-3 fallback for exactly that case is `canvas.py wait`, a blocking call **this
harness forbids**. Two of the three documented routes were structurally
unavailable. Logged as **harness-blocked and counted** per `assessment-mode.md`
§3, not filed as a product regression.

That is now the harness limitation's **second** cost this run, and the protocol
says to count them: *"If a run accumulates several blocked by the same harness
limitation, that is the argument for building the other harness shape."*

#### [C8] pulled cadence — exercised for the first time in six runs, and it PASSES

The v0.9 checklist lists this under "never-exercised union". Driven directly:

```
QUEUED=true
PENDING_ID=1
REASON=cadence is set to pulled
HINT=The revision will land behind the pending-revision banner; the user
     chooses when. It validates and lints clean against the current head —
     read the echo, not this line.
ECHO(queued)=op 0 (mod label): market-feed ('Market Feed (pulled test)') …
LAYOUT_NOTE(queued)=…
```

`REVN` held at 33, `PENDING=1`. It leads with `QUEUED=true`, names the reason,
says who decides, echoes the op, and labels its lints `(queued)`.

**This is what bounds r6-6 to P3.** An agent that misses `config_changed` cannot
narrate a landed change, because the apply tells it before it speaks. The
missed event costs a planning assumption, not a false claim.

#### What the agent did well, against the §3 obligation

SESSION-HANDOVER §3 asks run 6 to confirm agents *consume* the lint/harness
category **under pressure** rather than bypassing it. This turn is direct
evidence. Given a decisive user ruling and an obvious fix, its first attempt was
refused by the checker on two counts — it made Crowding check a *black hole*
(flow in, none out), and the new direct arrow ran **through** the box it was
meant to bypass. The agent accepted the refusal, changed approach, and credited
it: *"Good catch by the tool, not by me."*

| T | Beat | Status |
|---|---|---|
| 1 | Inherited project: is any of it out of date? | **done** — r6-1, r6-2 |
| 2 | *Symptom without cause*: the lines look wrong | **done** — r6-3; core probe PASSED |
| 3 | R5 §6 discriminator (real browser) | **done** — r6-4, inherited item CLOSED |
| 4 | Pin + tidy: the user's ruling | **done** — r6-5; **ruling verified satisfied** |
| 5 | Silent config flip + chat-only | **done** — r6-6; **[C8] PASSES**, §3 obligation evidenced |
| 6 | Go-ahead under pulled cadence + a fresh dense flow | next |

---

# WORK PACKAGES — everything needed to pick this up cold

Written so an agent with **no session context** can curate and fix. Each package
states the defect, an exact reproduction, the measured evidence, the rivals
already refuted (do not re-litigate them), the traps that produce false
readings, and what a fix must and must not do.

Supporting artefacts, all tracked in `docs-r6/`:

| file | what it is |
|---|---|
| `r6-findings.jsonl` | the machine-readable ledger — every finding with `--rival`, `--discriminator`, `--found-by` |
| `r6-persona-and-script.md` | the run's persona, turn script, A0 adapter inventory, and declared limits |
| `r6-run-pin.txt` | subject sha + the mid-run correction about which checkout was actually assessed |
| `r6-probe.py` | the per-turn measurement probe, including the lint-drift comparator |
| `r6-subject-control.sh` | asserts the subject tree has not drifted; proven to fail in both directions |

**Cause count: 7, not 8.** The ledger mechanically reports 8 because `r6-1` and
`r6-8` were filed under different slugs before they were understood to share a
root. **They are one defect.** Cutting two work packages here would produce two
reds for one change.

---

## WP-A (P1) — pin geometry is computed at mint time and never re-derived

Covers `r6-1` + `r6-8`, and `r6-2` is a candidate *repair route* for it rather
than a separate defect.

### Face A — `--relayout` detaches every pin

```bash
# 1. copy the corpus. DO NOT mutate the original; it is a live assessment corpus.
cp -r ~/docs/optimization/r6/projects/argus6 /tmp/<your-agent-id>/argus6
cd /tmp/<your-agent-id>/argus6

# 2. THE TRAP. This project carries canvas_updates: pulled. Under `pulled` a
#    relayout QUEUES behind the pending banner and reads as "nothing changed".
#    Measuring without this step yields a false negative. I made this mistake.
python3 -c "import json;p='project_knowledge/config.json';c=json.load(open(p));c['canvas_updates']='per-round';json.dump(c,open(p,'w'))"

python3 <skill>/scripts/canvas.py --project . start --no-browser
python3 <skill>/scripts/canvas.py --project . mermaid --relayout --artifact onboarding-intake
```

Measure with the product's own helpers — `canvas.arrival_census(els)` and
`canvas._rect_gap(pin, target)` — never hand-rolled trigonometry:

| metric | before | after |
|---|---|---|
| oblique arrivals | 1/30 | **7/28** |
| pin gap to own target | 8, 8, 8 px | **748, 1703, 1419 px** |
| `LAYOUT_WARNING`+`LAYOUT_ERROR` | **0** | **10** |

The command reports only `RELAYOUT=`, `MOVES=14`, `REVN=`. It never says it
detached three pins.

### Face B — load never re-derives it either

`tests/fixtures/argus-r4-arm4` — **our own promoted regression fixture** —
carries 3 of 9 drawn pins at pre-fix r4b-3 geometry (`dx=412, dy=-40` against
404px-wide targets, so each glyph sits 8px past the right edge and 40px above
the top, diagonally off the corner in the neighbouring tile's gutter). Today's
`pin_spot` returns a different spot for all three, moving each **78px** from
`(512,304)` to `(476,346)`.

The load-time repair catalogue `ART-000`…`ART-012` covers **structural** damage
only (malformed JSON, dangling bindings, detached labels). Placement quality is
not in it, so `catch_up()` re-reads the bad geometry every session.

### Why no check catches either face

`pindrift` (`canvas.py:19847`) is relative-only:

```python
if rival is None or own - rival_gap < PIN_HUG_PX:   # PIN_HUG_PX = 8
    continue
```

No rival ⇒ no finding; it never asks *"is this glyph near its target at all?"*.
Over 9 drawn pins it fires on 2, silent on 7. Of the 3 stale glyphs it reports
one, and **only because a neighbouring chart sits at `rival_gap=0`** — move that
chart 9px and the one true positive goes silent too.

### Rivals already refuted — do not re-litigate

- *"r4b-3 is simply unfixed."* Refuted: today's `pin_spot` returns a different
  spot for all 3, so the fix works at mint time. `pin_spot`'s docstring names
  r4b-3 and the three pins by their tiles.
- *"The relayout measurement was a misconfiguration / a queued no-op."*
  Refuted by re-running from a clean copy with cadence reset.

### Acceptance — what a fix must achieve, and what it must not disturb

1. After any operation that moves a pin's target, the pin's gap to that target
   is preserved. **Assert the relation, never the literals 748/1703/1419** — a
   legitimate fix must not leave the pin red over an honest drawing.
2. `--relayout` must not increase `LAYOUT_WARNING`+`LAYOUT_ERROR` on a scene
   that was clean, and must say what it changed.
3. **The silent half:** a pin that is *correctly* placed must not move, and
   `pindrift` must stay quiet on a legitimate near-tie. A fix that re-places
   every pin on every load would satisfy (1) and wreck this.
4. At least **three** honest repairs exist — re-derive inside relayout,
   re-derive on load, or teach `pindrift` the absolute question. Per
   `skills/silence-is-a-bug/SKILL.md` §8 the red must flip under **more than
   one**, or it has silently prescribed the author's favourite.

---

## WP-B (P1) — `x-as-user move` manufactures user states no user can reach

Covers `r6-5`. **This is assessment infrastructure, not the product**, and the
distinction is the whole finding.

### Reproduction

Identical unpinned node, identical 40×24px delta, identical 0-error baseline,
**byte-identical narration** (`HEADLINE=Market Feed moved left-up`) on both:

| path | `LAYOUT_ERROR` after |
|---|---|
| `x-as-user move --target market-feed --dx -40 --dy -24` | **0 → 2** |
| `apply` with `{"op":"mod","id":"market-feed","attrs":{"x":…,"y":…}}` | **0 → 0** |

Errors read *"arrow X claims to bind Y but its start point ends Npx away"*.

### The rival, and how it was refuted — read this before writing anything

Two explanations predict that table and need **opposite** fixes: **(A)** the
product fails to re-route bound arrows on a user save, or **(B)** the instrument
fabricates a state a browser drag never produces.

**(B) is correct.** A real Playwright drag of a 3-arrow node reports `ARROWS
FOLLOWED THE NODE IN THE CLIENT: true` and **0** `LAYOUT_ERROR`s after
drag-and-save. Excalidraw's native re-binding does the work. **Do not write a
red that accuses the save path.** The spec is at
`frontends/wysiwyg-grilling/tests/e2e/r6-user-drag-bindings.spec.ts`.

### Where, and why it recurred

`cmd_x_as_user`'s `move` branch (`canvas.py` ~30342). Its loop carries `other is
el`, `containerId` children, `frameId` children and `groupIds` peers. An arrow
bound via `startBinding`/`endBinding` matches none of them.

**Its own comment records instance one of this class** — *"This driver moved only
the host and its bound label — which manufactured half of r4-10 (the 'orphaned X
strokes' observations came through here, not through a real user gesture)."*
v0.8's **WP6** existed to make this verb mirror the real client and enumerated
three gaps — *rename re-measures, move translates decorations, delete cascades
groups*. **Bound arrows were not on the list, and nothing tests the general
property.** That is why pinning by name is the wrong shape here.

### Blast radius — the reason this is P1 and not tidy-up

The verb is assessor-adapter capability 1, contracted to use *"the same write
path the real client uses — not a reconstruction of it"*, and its docstring
warns of exactly this drift. Observed live: the agent under test generalised
from the fabricated state and told its user *"dragging a node with a
multi-segment connector doesn't always take the connector with it, and nothing
pops up to tell you"* — **false of a real browser drag**. Any finding in any
prior run derived from `x-as-user move` on a bound node describes a state only
the driver can produce. **R5's arm A used this verb for all user edits.**

### Acceptance

1. After `x-as-user move` on a bound node, the referential state matches what a
   real client drag leaves: **zero new dangling-binding errors**.
2. Pin the **general property**, not the arrow ids — "leaves the same
   referential state a real client drag leaves". WP6 missed this gap precisely
   because it pinned three named gaps.
3. **The silent half:** moving an *unbound* node must still produce no errors,
   and the verb must not start re-routing geometry the user authored.

---

## Deliberately NOT filed — so the next run does not chase them

- **Canvas-vs-export label divergence.** `x-geometry --diff` shows 4 of 94 text
  elements differing between `drawn=` and `export=` by 8–18px. This is v0.9 WP4
  **by design**: `arrow_label_anchor` is the canvas position, `arrow_label_slot`
  the stored one `render_svg` paints, documented as diverging *up to 49px*, with
  `export=` existing so an assessor sees both. Each channel is internally
  consistent.
- **The 23px label re-centring** the agent volunteered in T4. Recorded
  `derived: true`, containers genuinely unmoved, and stable across three further
  `tidy` passes — a one-time re-centring reaching a fixpoint, not thrash.
- **The pulled-cadence "escape"** at revn 34. The server log shows exactly one
  `POST /api/pending/resolve`, at the second it landed, with heartbeats
  throughout: a human released it from the banner. The gate worked.

## Left open, not investigated

- **11 of 94 text elements** report `stored width < needs — the editor WRAPS
  this`, the largest a user note at 206 vs 387. Readings taken 2026-08-27, after
  the per-session-wrap issue closed on 2026-08-20, so they are not that.
- **[C3] partial miss:** the fresh 14-node intake flow carries **1 oblique
  arrival of 30 endpoints** where the checklist expects zero. The scene-guard
  then correctly declines to reroute it and it is stable across passes.
- Beats 7–14 of the turn script (freedraw, 409 banner, fork/archive, two
  audiences) were not reached.

---

# CLOSE

## Scorecard

```
8 findings / 7 distinct causes        7 turns (planned 13-14)
P1: 3   P2: 2   P3: 3   P0: 0   ENV: 0
attribution: tool-silent-shipped 3 · tool-told 2 · tool-told-ignored 1
             tool-false-alarm 1 · tool-silent-covered 0
found by:    assessor-query 6 · user-look 1 · agent 1
withdrawn: 0   ·   undiscriminated: 0
```

**The cause count is 7, not the ledger's 8.** `r6-1` and `r6-8` were filed under
different slugs before they were understood to share a root. Reported both ways
because the mechanical number is what `ledger.py diff` will compare against.

| Run | Findings | Causes | Headline |
|---|---|---|---|
| r3 | 13 | 6 | the check validates a value nobody sees |
| r4 | 14 + 9 | 8 | capability outgrew correctness |
| r5 | 19 | 18 | the mechanisms are right; the modes turn them off |
| **r6** | **8** | **7** | **the instrument was the defect** |

## Verdict — the raw counts, discounted

Eight findings is the lowest count in four runs, and the run was cut short at 7
turns of a planned 13–14 when the host process exited and took the session agent
with it. Neither number should be read as "the skill is healthier". Read this
instead:

**Two of the three P1s are not defects in the product at all.** `r6-5` is the
*assessment adapter* manufacturing states no user can reach; `r6-8`+`r6-1` are
derived geometry never re-derived. Only the second is something a user meets.
The run's honest headline is **the instrument was the defect** — and that
matters more than a bug count, because every behavioural finding any prior run
derived from `x-as-user move` on a bound element is now suspect, R5's arm A
included.

**Three findings I nearly filed dissolved on contact**, and the record of them
is worth more than the ones that survived:

1. Pin glyphs in the gutter looked like a fresh defect. `pin_spot`'s own
   docstring names it as **r4b-3**, already fixed. The live defect turned out to
   be narrower and better: the fix is mint-time only.
2. Canvas-vs-export label divergence looked like this method's signature
   headline ("a check validating a value nobody sees"). It is **v0.9 WP4 by
   design**, up to 49px, with `export=` existing precisely so an assessor sees
   both.
3. A revision "escaping" the pulled-cadence gate looked like a broken guarantee.
   The server log shows one `POST /api/pending/resolve` at the second it landed:
   **a human pulled it from the banner.** The gate worked.

Checking known-ness before filing paid twice; reproducing before believing paid
once. **A fourth**: my own relayout measurement first read "no change", which I
nearly reported as the agent overstating its case — the copy had inherited
`pulled` cadence and the relayout was sitting behind the banner.

**Scored on mechanism, not outcome.** `tool-silent-covered` is **0** this run,
which is unusual and real: where the agent was saved, it was saved by a *signal*
(the `ECHO(queued)=` marker in `r6-7`), not by its own diligence. The redundancy
the docs designed in did load-bearing work.

**What went right, stated as precisely as what went wrong.** The agent under
test was a genuine second finder — `r6-8` is `--found-by agent`. It caught its
own no-op harness unprompted (*"everything I'd concluded from it was
worthless"*), accepted a checker's refusal and credited it (*"Good catch by the
tool, not by me"*), refused to report before checking disk truth, and diagnosed
a banner release it was never told about. Against SESSION-HANDOVER §3's
obligation — *do agents consume the lint category under pressure* — the answer
is **yes, demonstrably**.

**An unplanned test the product passed.** The host process was killed mid-run
with a queued revision outstanding and a server holding unflushed state. On
restart the loader named exactly what differed, attributed it to
`out-of-session` rather than to the agent, preserved the pending queue, and lost
nothing. Nobody designed that test.

## Known limits — these change how the findings should be read

- **7 turns, not 13.** The host process exited and the session agent became
  unreachable (bare-name messaging is per-session). Beats 9–14 were never run:
  freedraw survival, the 409 stale-save banner, `autoResize: False`,
  fork/archive, two-audiences, and the *correct-work-in-flight* probe that T8
  had just set up. **Three of these are now deferred for a third consecutive
  run.**
- **One arm** (user ruling). Every behavioural finding is n=1 and marked so.
- **The event channel was structurally unreachable**, twice costing a
  measurement: no `Monitor` tool exists in this runtime and SKILL.md's Tier-3
  fallback (`canvas.py wait`) is a blocking call this harness forbids. Counted,
  per `assessment-mode.md` §3 — *"If a run accumulates several blocked by the
  same harness limitation, that is the argument for building the other harness
  shape."* R6 accumulated two. **That argument is now made.**
- **The assessor wrote the method.** Beats reproduced from the protocol prove
  little; the informative part is what R6 ran that the protocol does not
  contain — the plugin-vs-symlink resolution check (`so1-R6-1`) and the
  AST-identity test for "does it matter which checkout I assessed".
- **Deliberate deviation:** the run started from an inherited project rather
  than a bare directory, because the v0.9 features under test only exist for
  inherited scenes.
- **`x-as-user move` was used before `r6-5` was known.** T4's user nudge went
  through it. That turn's findings were re-derived through other paths before
  filing; no finding rests on it.

## Fixture promoted — `tests/fixtures/argus-r6/`

6 artifacts, revn 37, 9 open pins, 261 elements. Promoted the day the run ended,
per `fixtures.md`. **Deliberately excludes** `.pending/`, `.backups/` and any
nested `project_knowledge/` shell — that residue is exactly what makes the R4
fixture read as `ARTIFACTS=0` (S-1). Verified to load before committing.

It is a better corpus than R4's for anything about *fresh* scenes: it carries a
16-node flow the router built from `from`/`to` alone, four arrows contending on
one node, and a 40-endpoint arrival census.

---

# THE R7 RE-TEST CHECKLIST — the contract between runs

One item per fix, each a falsifiable assertion observable in-session. R7 answers
this as a table with **in-session evidence**, not a bare PASS.

| # | Item |
|---|---|
| 1 | **Move a node that a pin points at, then look.** The glyph must still hug it. A pin's gap to its target must survive any operation that moves the target. Fails today at 748–1703px after `--relayout`. |
| 2 | **Run `--relayout` on a clean artifact.** `LAYOUT_WARNING`+`LAYOUT_ERROR` must not increase, and the command must say what it changed. Today: 0 → 10, reported as `MOVES=14`. |
| 3 | **Load a project whose pins carry pre-fix geometry.** Either the loader repairs placement, or a check says it did not. Today both are silent — `ART-000`…`012` covers structural damage only. |
| 4 | **`x-as-user move` a bound node.** Zero new dangling-binding errors, matching what a real browser drag leaves. Today: 0 → 2 on the user path, 0 → 0 on the agent path, identical narration. |
| 5 | **The silent half of 1–4.** A correctly-placed pin must not move; `pindrift` must stay quiet on a legitimate near-tie; an unbound node's move must still be clean. A fix that re-places every pin on every load satisfies 1 and wrecks this. |
| 6 | **Discoverability, with a grace period.** Does an agent reach the event log unprompted when a `Monitor`-class affordance exists? R5 scored this PASS at n=2; R6 could not test it. If it is not reached under a runtime that permits it, that is a **docs** finding, not a code one. |
| 7 | **Fresh-scene arrivals.** Build a 12+ node flow at long horizontal runs and small vertical offsets and run `arrival_census`. R6 measured 1 oblique of 40, stable, with the scene-guard correctly declining. Beat 3 wants 0. Decide whether 1-in-40 is the target or the checklist is. |
| 8 | **Deferred a third time — schedule them first, not last.** Freedraw survival across save/reload; the 409 stale-save banner (needs a second browser context); `autoResize: False`; fork/archive. |

## Still open — deliberate non-fixes, carried so they are not re-found

- **Canvas-vs-export label divergence** (4 of 94 text elements, 8–18px):
  v0.9 WP4 by design, envelope 49px. Not a defect.
- **`pindrift` relative-only** (`r6-2`): folded into WP-A as a candidate repair
  route, not a separate red.
- **11 of 94 text elements** report `stored width < needs`, largest 206 vs 387.
  Not investigated for want of run time. Readings post-date the 2026-08-20
  wrap-coin fix, so they are not that.
- **`so1-R6-1`** is a defect in *this method*, not the target: "check the
  symlink" inspects the one registration that does not win.

---

# CORRECTIONS — four errors in my own findings, found by the curators

Filed by `curator-r6-pin-rederive` while building WP-A's reds, and **verified
independently before being accepted**. All four are mine. They are appended
rather than edited into place, because the record of a wrong figure is what
stops the next run rebuilding on it.

Three of the four are the same shape, and it is the shape this repo's own v0.9
§3b table names: **the measurement that was easy to take displaced the one the
claim was about.**

### C-1 — "`--relayout` never says it detached three pins" is FALSE

**Verified.** Re-run from a clean copy with cadence reset, the standing lint
names it explicitly:

```
LAYOUT_WARNING=…: pin pin-intake-escalation asks about escalation
  ('Escalated to a human') but is drawn 1944px from it
```

The *command* prints only `RELAYOUT=`, `MOVES=16`, `REVN=`. The *lint* speaks
afterwards. I measured "0 → 10 warnings" and **never read what the ten were** —
having counted them, I asserted their content.

**This changes `r6-8`'s attribution from `tool-silent-shipped` to `tool-told`,
delayed.** The defect stands and is still P1: a documented one-command feature
takes a clean diagram to a broken one. But "and nothing tells you" was wrong,
and that clause was doing rhetorical work the evidence did not support.

The verification run also surfaced something worse than the pins, which I had
not looked for: **10 of the 12 warnings are user-shaped arrows whose bindings
the relayout stranded** — `a-banding-review`, `a-banding-refuse`,
`a-identity-reject`, `a-sanctions-reject`, `a-review-reject`, both endpoints
each. Relayout detaches *authored* geometry, not just glyphs.

### C-2 — "78px" was Manhattan, presented as a distance

`(512,304) → (476,346)` is `dx −36, dy +42`. **Euclidean 55.3px**; `36+42 = 78`.
My probe computed `abs(dx)+abs(dy)` and I labelled the result a distance.

A unit substitution, committed while writing up a run whose own §3b table lists
*"an 8-bit colour / a float"* and *"a count of passes / milliseconds"* as the
canonical instances. Three of the seven in that table die at the step *name the
unit the claim is in before choosing how to measure it*.

### C-3 — "3 of 9 fixture pins" was a partial count reported as a total

**5 of 9.** The three dashboard glyphs at 55.3px, **plus** `pin-rerun-unit` and
`pin-threshold-blast` on the admin console at 37.4px each. `pin-late-source`
differs by 0.6px and is correctly excluded.

I ran the `pin_spot` comparison **on one artifact** and wrote the result as a
census of the fixture. A population substitution — the fourth axis in §3b.

### C-4 — "move that chart 9px and the true positive goes silent" was never tested

**False as stated.** The heatmap *overlaps* the glyph, so ±9px on any axis
leaves `rival_gap = 0` and the finding stands. The curator swept it rather than
reasoning about it.

The substance holds, and it now has a real discriminator: **40px** of tile
separation takes the nearest rival to 18.0px and the finding vanishes, while
`pin-report-page`'s own gap stays 16.1px. Use 40px.

I asserted 9px from the shape of the predicate without running it. That is a
prediction laundered into a measurement by being written in the indicative —
the escalation §3b describes, committed once rather than three times.

### What this says about the run

Every one of the four was caught by **someone recomputing a number they did not
produce**. None was caught by my being careful; I was being careful. §3b's
conclusion, restated by its own subject:

> **This class has no floor you reach by being careful. It has one you reach by
> having someone else recompute — including recomputing the summaries.**

The briefs asked both curators to report anything contradicting them. Both did.
That instruction cost one sentence and bought four corrections.

---

# S-3 — promoting a run's project into `tests/fixtures/` is a CORPUS CHANGE, not a filing action

**My error, caught by the suite only because a live value disagreed with itself.**

`fixtures.md` says to promote the finished project the day the run ends, and
R6's close did that: `tests/fixtures/argus-r6/`, verified to load, deliberately
free of the `.pending`/nested-`project_knowledge` residue that makes the R4
fixture read as `ARTIFACTS=0` (S-1). Every check I ran on it passed.

**I did not re-run the full suite after promoting it.** Five tests fail:

```
TestTheCorpusKeepsItsTwoDeliberateFossils::test_exactly_these_two_artifacts_still_carry_legacy_routing
TestEverySavedRecordAddressesItsOwnContents::test_every_frozen_save_records_hashes_to_its_stored_short_id
TestLintCallsALineALine::test_the_corpus_carries_no_line_in_the_lint_population
TestTheOscillatorIsTheRouter::test_nine_frozen_artifacts_never_settle
TestShapeAwareLabelRoom::test_the_fitter_does_not_grow_a_box_that_has_the_room
```

Control: with the fixture removed, all five pass — 29 tests, OK.

**`tests/fixtures/` is not an archive. It is a curated corpus whose population
is itself pinned**, and the tests say so in their own docstrings:

> *"The rebase left two artifacts fossilised, and this says why. v0.9
> TASK-FOCUS-FOLLOWUP-B re-routed the frozen corpus through `Store.reroute` and
> took every offer the gate made BUT TWO."*

The intake flow R6 commissioned carries legacy routing — I measured that at T6
and reported it as a `[C3]` result. Promoting it makes the corpus carry **four**
deliberate fossils where a test pins exactly two, adds a `line` to a population
pinned as line-free, and adds artifacts to a nine-artifact oscillator set.
Every one of those tests is doing its job.

**Resolution:** the corpus is left as it was. The R6 project is preserved at
`docs-r6/fixture-argus-r6/` — tracked and durable, satisfying what `fixtures.md`
is actually for (the project surviving the week) **without** joining the frozen
population that checks are measured against. Promoting it properly is a
deliberate act that re-decides four curated invariants, and it is not R6's to
make on the way out of the door.

**Two method corrections this buys:**

1. **`fixtures.md`'s "promote the fixture the same day" is incomplete.** It
   says nothing about the destination being a *measured* population. A run that
   follows it literally breaks the tree, and every check it ran on the fixture
   in isolation will have passed.
2. **A promoted fixture needs the FULL suite, not a load check.** I verified the
   fixture loaded, which is the check S-1 was about, and that is orthogonal to
   the one that mattered.

**How it was actually caught, because that is the transferable part.** Not by
review, and not by care. The fold's `livedoc refresh` re-derived
`unittest_suite_cases` to **2070** where the tree I had tested reported **2068**.
Two tests I could not account for meant the thing I tested was not the thing I
was folding. The census disagreeing with itself is what forced the re-run.

`corpus_projects` had also moved 5 → 6 and `corpus_census` 24 → 30 artifacts in
the same refresh — both loud, both mine, and I read them as *bookkeeping
following the fixture* rather than as *the corpus population changing under four
tests that pin it*. The signal was there one step before the failure and I
filed it as noise.
