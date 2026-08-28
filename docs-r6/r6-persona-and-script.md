# Run 6 — persona and turn script (written BEFORE turn 1)

Target: `wysiwyg-grilling` @ v0.9, subject sha `2fd9fcf` (`fold-wp4`), pinned in
`RUN-PIN.txt`. Discovery symlink verified pointing at that worktree.
**One arm** (user ruling 2026-08-26). Assessor: me, first-hand, no orchestrator
layer. Project: `~/docs/optimization/r6/projects/argus6`.

---

## Baseline, measured at setup — not carried from the v0.9 recipe

The recipe's §1/§5 figures are eight days and one release-close stale. Re-derived:

| | recipe said | measured at `2fd9fcf` |
|---|---|---|
| suite | ~1507 | **2058** |
| detectors | 30 / 27 proven / 0 UNCOVERED | **37 / 33 / 3 render-tier / 1 UNCOVERED** (`label_spills_past_body`) |
| type checker | "mypy is the binding gate", pyright clean | **both dropped**; `ty@0.0.21` only |

Project baseline (`argus6`, from the promoted R4 fixture, head revn 31):

```
elements           211        locked/pinned          1
arrows              36        server-owned          28
authored (exempt)    8        stale signature        0
```

**Two setup findings about the FIXTURE, logged now so they are not re-found as
findings about the skill:**

1. `tests/fixtures/argus-r4-arm4/` carries **empty nested `project_knowledge/`
   shells** — a stray server start got promoted with it. The real content is
   flattened at the fixture root. A naive `--project <fixture>` reads
   `ARTIFACTS=0`. R6's project root was rebuilt by hand from the flattened copy.
2. **The checklist's beat 1 premise is false for this fixture.** It expects
   "pre-v0.9 arrow geometry" to trigger the consent-gated re-route offer. All 36
   arrows validate: 0 stale signatures. v0.9's router signature still matches
   R4-era arrows. `reroute` correctly reports `ARTIFACTS=0` and explains per
   artifact. Beat 1 is re-aimed below.

---

## A0 — adapter inventory, measured against the CLI (not carried)

`canvas.py --help` advertises none of the `x-*` namespace; the hiding mechanics
hold. Three subcommands registered.

| Cap | Status | Evidence |
|---|---|---|
| 0 live surface | **built** | `/tmp/wysiwyg-grilling/<sha1(root)[:12]>.{state.json,events.jsonl,server.log,shots}` — `canvas.py:434-443` |
| 1 act-as-user | **built** | `x-as-user` — **10** verbs: rename, move, delete, **toggle**, note, ask, tooltip, answer, config, checkout (the reference table lists 9 and omits `toggle`) |
| 2 served log | **NOT built** | fallback: `lastmsg.py --grep`, with both stated weaknesses live |
| 3 truth-vs-stored | **built** | `x-geometry --diff` |
| 4 stats `--json` | **NOT built** | hand-rolled census per turn |
| 5 typed event log | **file only** | `EventLog` at `canvas.py:25730` writes `{seq, ts, type, ...}` JSONL to the runtime dir. No `x-events` CLI; read the file directly |
| 6 checkpoint/restore | **NOT built** | `statebox.py` |
| 7 fixture export | **NOT built** | `statebox.py promote` |

**The gap that will cost R6 most is capability 2.** R5's attribution was 14
`tool-silent-shipped` of 19 — the axis that decides code-fix vs docs-fix — and
it is exactly the axis with no first-class instrument. Every attribution in R6
carries its evidence source, and any resting on the transcript grep is marked
`attr-evidence: transcript` so a reader can discount it.

---

## A2 — The persona (gate: nothing is sent until this exists)

I am playing the **operations-risk person who has just inherited these
diagrams** from the analyst who built them. I did not draw any of this. I am
not here to make it prettier; I am here to find out **whether I can trust it**,
because I have to hand it to compliance and put my name on it.

- **Vocabulary is operational and sceptical, never the tool's.** They say *out
  of date*, *does this still hold*, *who signed off*, *sign-off*, *the handover*,
  *stale*, *I need to show this to compliance*, *is that still running*. They say
  *"the picture"*, *"that box"*, *"the line between those two"*. They **never**
  say artifact, lint, revision, pin, tripwire, concept, registry, mermaid,
  relayout, checkpoint, event log, verb, fact.
- **What they notice:** contradiction and staleness above all — a picture that
  disagrees with what they were told, a label naming something that was
  decommissioned, a number that cannot still be right. They notice *doubt*
  before they notice ugliness. This is the opposite lens from R5's analyst, who
  noticed ugly before incorrect, and the difference is deliberate.
- **What they do not notice:** anything internal. They will not read a warning
  count, will not name a check, will not observe a 79px offset unless the
  picture looks wrong at a glance. They do not know the tool has a tidy command
  until they want one and ask in plain words.
- **Manner:** careful and a little wary. Asks *"is that still true?"* more than
  they ask for anything to be built. Slow to accept a claim; will re-ask.
  Sometimes says nothing about a question for several turns — deliberate, not
  politeness to be repaired.
- **They will not:** teach the agent its tool, correct its tool usage, name a
  capability, suggest a command, or acknowledge that anything is being tested.

**Steering check, run against every draft message before sending:** *would this
person, who has never opened this tool before, have known that word?* If the
sentence contains a term from the skill's own vocabulary, rewrite it.

**Why this persona and not R5's:** R5's carried prediction is that R6's headline
will be a **cached derivation** — "the model believes something about the
artifact that the artifact does not say." A persona whose entire concern is
*whether the picture is still true* walks into that class by temperament rather
than by my steering it there. Choosing the lens is legitimate; naming a beat is
not, and no message will.

---

## A3 — Turn script (beat × probe), 14 turns

Sources: **[C]** = v0.9 checklist §2 beat; **[P5]** = R5's carried prediction or
discriminating test; **[E]** = e2e row marked *planned, run 6*; **[H]** =
SESSION-HANDOVER §3 obligation; **[R]** = the user's 2026-08-26 tidy ruling.

| T | Beat | What it probes |
|---|---|---|
| 1 | Kickoff: I've taken this over, before I trust it — is any of it out of date? | **[C1 re-aimed]** catch-up narration on an *inherited* project. **[P5]** resume #1 of the resume-twice test. Does it reach for state/checks unprompted, and does it relay the honest "nothing to re-route" or manufacture work from an inherited scene? |
| 2 | *Symptom without cause*: "some of these arrows don't look right to me" — withhold which, withhold why | Blind-spot diagnosis against a **measured** truth: 4 endpoints on `daily-run-flow` genuinely arrive crooked and the tool already says a re-route would not fix them. Finds them, misses them, or fabricates? |
| 3 | Real UI (1 of 2): hand-draw a bent correction arrow with a deliberately tiny final leg; then rename **one** label in the tab and save with no other change | **[C2]** arrival lint's *final-leg* sentence, not "bring it across the face". **[P5 §6 DISCRIMINATOR]** — the unrun cross-arm test: does a lone browser rename mint phantom `reordered` facts? Isolates client array order from the `x-as-user` write path |
| 4 | "Leave that one where it is" → pin it; then "this is a mess, can you tidy it up" | **[R]** the ruling, both directions: a pinned element must survive tidy; and tidy must **narrate** what it moved. Denominator is sharp — 1 of 211 pinned, 28 owned arrows movable, 8 authored exempt-without-a-pin |
| 5 | *Silent config flip*: change cadence to pulled out of band, never mention it; chat-only turn | Standing probe — reads state or trusts its own optimism. **[C8]** the pending-banner pulled cadence, never exercised by any run |
| 6 | "Build the new intake flow" — 10+ nodes, long horizontal runs, small vertical offsets | **[C3]** the 89° regime on a **fresh** scene: expect no grazing arrivals and no scene-guard weirdness |
| 7 | "Everything ends up at the reconciler" — 5 arrows onto one small node | **[C4]** contention feet: spill to adjacent borders, no shared attach points, honest shortfall lint where a side is genuinely impossible |
| 8 | *Correct work in flight*, then discard it after it queues | Supersede-vs-stack. **[P5 test 2 / E r5-2]** the round must return to where it started — a round derived from queue depth un-derives itself |
| 9 | "Make the risk boxes white with a light grey edge" — plus one element authored `#ff00` and one bare `000000` | **[C5]** colour honesty: unreadable-colour REPORTS with restate-in-hex guidance, no confident wrong ratios, no silence; the 16 CSS keywords and `transparent` stay quiet |
| 10 | *Aside that contradicts the spine* + *late revelation*, both thrown away casually | Fidelity: does a late fact restructure the work or just get filed. **[H]** cosmetic-repair thrash — count cosmetic revisions issued per round |
| 11 | Freedraw a loop around a cluster by hand; add a note on a framed scene | **[C7]** freedraw survives save/reload with geometry intact — the named residual; file a client-half failure against the CLIENT. **[C6]** hugs inside containing frames, no phantom fallbacks |
| 12 | Real UI (2 of 2): two browser contexts, save from the stale one | **[E]** the stale-save 409 banner — deferred by two runs. **[E]** `autoResize: False`: does the client actually stop growing the box |
| 13 | Check out an earlier state and diverge from it | **[C8]** branch fork/archive — the protocol's "least-tested surface in almost every stateful skill" |
| 14 | "Give me something for the compliance people who don't care how it works", then close | *Two audiences* probe. **[P5 test 3]** resume the project a second time and assert the second resume mints nothing |

**[C9] event-loop doctrine**: passed in R5 at n=2. Do **not** re-litigate;
spot-confirm only if a beat exercises it naturally.

**[H] second obligation** — do agents actually *consume* the lint/harness
category under pressure rather than bypassing it — is not a turn. It is scored
across T6–T10, the turns that put the agent under drawing pressure.

## Standing instruments (scheduled, not hoped for)

| Instrument | Cadence |
|---|---|
| Render every artifact at full size and **look** | after T4, after T8, before close |
| **Cold look** — fresh subagent, images only, one question, no context | after T7 and at close |
| Read what the agent **was told**, not only what it did | every turn; `lastmsg.py --grep`, marked `attr-evidence: transcript` |
| **`/api/state` lint vs a fresh CLI `lint`**, compared after every repair | every turn from T4 — the cached-derivation instrument |
| Element/arrow census + `x-geometry --diff` | every turn that draws |
| **Outside questioner** on the findings | once, before the tally |

## Harness note

Spawn prompt per `agents/session-agent.md`, with R5's amendment kept: it forbids
only foreground calls that never return and **permits background watching**, so
that an agent never reaching for the event log is a result rather than an
artefact. The known blind spot stands and is carried into limits: the
`wait`-style affordances still go unexercised, for the fifth run running.

## Known limits, declared before the run

- **One arm, one agent.** Every behavioural finding is n=1 and will be marked
  so. R5's only settled behavioural result was settled *because* it had two arms;
  R6 buys no equivalent, by ruling.
- **Deliberate deviation from bare-dir isolation.** The protocol says the project
  is an empty directory. R6 starts from an inherited project on purpose — the
  v0.9 feature under test (consent-gated re-route, catch-up on load) only exists
  for inherited scenes, and the persona is built on it. Recorded, not hidden.
- **The assessor wrote the method.** Reproduction of planned beats is close to
  uninformative here; what counts is beats R6 runs that the protocol does *not*
  contain. Harvest those, discount the rest.
- **Capability 2 absent** — attribution rests on transcript archaeology, which
  cannot separate "the tool emitted this" from "the agent quoted its docs".

## Ledger

`~/docs/optimization/r6/.ledger/r6.jsonl`. Every finding carries `--rival`,
`--discriminator` and `--found-by`. Findings are written **as they happen**,
never batched at the close.
