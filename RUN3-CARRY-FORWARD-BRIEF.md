# Run 3 — carry-forward brief (assessor-side only)

**To:** the session driving `argus3` (session `5feaae42`, server pid `167225`, port `45353`).
**From:** the session that just shipped `skill-optimizer` v0.2.0.
**Status of your run when this was written:** T3 complete, findings R3-1 and R3-2 recorded.

---

## 0. Read this first

**Nothing in this document may reach the `argus3-designer` agent.** Not in a
chat message, not in a file inside the project directory, not as a hint when it
gets stuck. It is assessor-side bookkeeping. An agent that can see the harness
is an agent performing for it, and your run's best signals — the silent cadence
flip, the planted cross-channel contradiction — only mean anything because it
does not know it is being scored.

**Do not change how you play the user because of this document**, with one
exception: start writing findings into the ledger as they happen instead of
batching them at the end.

**I have not touched `argus3`.** No saves, no config changes, no canvas reads
through the API. Your run is uncontaminated by me. Keep it that way — I will
not act on it unless you hand it over explicitly.

---

## 1. What is already done for you

I back-filled **run 2** and seeded **run 3** into a shared ledger, so the
cross-run comparison works without you re-entering anything.

```
Ledger:  /home/cognizac/Projects/wysiwyg_grilling_skill.worktrees/capability_assessment/.ledger/
         r2.jsonl   11 turns, 10 live findings, 9 distinct causes, 1 withdrawn (R2-2)
         r3.jsonl   3 turns, 2 findings (R3-1, R3-2)

Scripts: /home/cognizac/.claude/skills/skill-optimizer/scripts/{ledger,statebox,lastmsg}.py
```

Set this once per shell and every command below is copy-pasteable:

```bash
cd /home/cognizac/Projects/wysiwyg_grilling_skill.worktrees/capability_assessment
L="python3 /home/cognizac/.claude/skills/skill-optimizer/scripts/ledger.py --dir .ledger"
$L --run r3 tally          # sanity check: 3 turns, 2 findings
```

Everything is append-only. Nothing you add can overwrite what is there.

---

## 2. Before you read section 4 — declare your remaining plan

This matters for a validity reason, so do it before scrolling.

Section 4 lists seven standing probes from the new assessment protocol. If you
read them first and then run them, we cannot tell whether the protocol
*reproduced* your coverage or merely *dictated* it.

So, first: **write down the beats you already intend to run for the rest of the
session** — turn number and what each probes — and record them:

```bash
$L --run r3 turn add --n 99 --beat "PLANNED (pre-brief): T4=…, T5=…, T6=…" \
   --probes "declared before reading the protocol's probe list"
```

Then read section 4 and note which of its probes you had **not** already
planned. That difference is the acceptance-test result I need, and it is the
only part of this exercise that can't be reconstructed afterwards.

---

## 3. During the rest of the run

### 3a. Write findings as they happen

```bash
$L --run r3 finding add \
   --id r3-N --sev P0|P1|P2|P3|ENV \
   --attr <see 3b> \
   --cause <short-slug> \
   --where canvas.py:1234 \
   --title "one sentence" \
   --evidence "what you MEASURED, not what you concluded" \
   [--behavioural]          # about the AGENT, not the tooling → marks it n=1
```

`--cause` is the important one and it is free text. Findings cluster on it, and
the clusters become the work packages. Reuse a slug across findings **and
across runs** when the cause is genuinely the same — `bbox-anchor` is already
shared by `r2-11` and `r3-1`, which is what makes the recurrence visible.

To withdraw something that doesn't survive contact:

```bash
$L --run r3 finding withdraw r3-N --reason "…"
```

It stays in the report, struck through. Don't delete findings — the record of a
wrong finding is what stops run 4 re-filing it.

### 3b. Attribution — the axis run 2 didn't have

Every finding gets one. It answers *"was the agent told?"*, and it decides
whether the remediation is **code** or **docs**. The two are indistinguishable
from the outcome alone.

| `--attr` | Meaning | Fix |
|---|---|---|
| `tool-told` | Tool signalled, agent acted | Working as intended |
| `tool-told-ignored` | Tool signalled, agent didn't act | **Docs / affordance** |
| `tool-false-alarm` | Tool signalled and was **wrong** | **Code** — narrow the check |
| `tool-silent-covered` | No signal; the agent's diligence saved it | **Code** — a hole a less careful agent falls through |
| `tool-silent-shipped` | No signal; the defect landed | **Code**, P0/P1 |
| `unknown` | You haven't found out yet | Go find out |

Three rules:

- **`tool-silent-covered` is not a pass.** Score the mechanism. Run 2's
  flagship variant-frame divergence was caught by the agent while the tool said
  nothing — that is a finding, not a success.
- **`tool-false-alarm` is not "working as intended" just because the tool
  spoke.** Run 2 had three of these (11 false tripwires, the glossary alias
  notes, Q25 on a bare percentage). Noise costs the agent real effort and
  trains it to discount the channel.
- **Attribution needs evidence.** Quote the tool's actual output. "It probably
  warned" is `unknown`.

### 3c. How to answer "was the agent told?" — the fallback

`canvas.py` has **no served log** (adapter capability 2 is unbuilt), so use the
transcript reader:

```bash
M="python3 /home/cognizac/.claude/skills/skill-optimizer/scripts/lastmsg.py"
$M --agent argus3 --grep "LAYOUT_"        # or WOULD_APPLY, TRIPWIRE, HEADLINE, ECHO=
$M --agent argus3 3                       # its last 3 user-facing turns
```

Two weaknesses you must state whenever you rely on it:

1. It works only because you own the transcript.
2. It **cannot** tell "the tool emitted this" from "the agent quoted the docs at
   itself." The script now resolves real tool names and warns you how many hits
   came from `Read`/`Glob`/`Grep` — discount those before assigning
   attribution. On the argus3 transcript, 8 of 24 `LAYOUT_` hits were the agent
   reading `layout.md`, not the lint saying anything.

**Never `Read` the agent's `.output` file** — it is a symlink to the full
transcript and will flood your context.

### 3d. Before any probe that mutates the canvas you are about to score

Run 2's differential test left a real overlap in the final tally and forced the
verdict to read *"5 findings, of which 1 is mine."* Don't repeat that:

```bash
S="python3 /home/cognizac/.claude/skills/skill-optimizer/scripts/statebox.py"
$S checkpoint <argus3>/project_knowledge before-differential
# …probe…
$S restore <argus3>/project_knowledge before-differential --yes
```

If pollution reaches the tally anyway, name it in the ledger and subtract it in
the verdict.

---

## 4. The seven standing probes

**Only read this after doing section 2.** Which of these have you run or
planned? Which had you not thought of?

| Probe | Generic form | What it catches |
|---|---|---|
| **Silent config flip** | Change a setting out of band, never mention it | Does the agent read state or trust its own optimism — *you did this at T2* |
| **Correct work in flight** | "Before you apply that, X is wrong" while queued | Supersede-vs-stack — *you did this at T3* |
| **Symptom without cause** | Describe a defect as a user would, withhold the mechanism | Blind-spot diagnosis; independent corroboration of a finding you already hold |
| **Age something** | Leave one question unanswered for several turns | Standing-nag — **note R3-2 says this is currently unobservable under `pulled`, which is itself the finding** |
| **Stale-then-commit** | Invalidate queued work, then let it land | Eviction paths that validation pushed out of reach |
| **Differential control** | Covered case *and* uncovered case, tabled — three saves, not one | The silent half, where wrong fixes hide |
| **Fork / rewind** | Return to an earlier state and diverge | Branch handling — least-tested surface in every stateful skill |

Two more coverage notes from the ledger:

- **`bbox-anchor` is now a cross-run recurrence.** `r2-11` fixed the tooltip
  dot in `App.tsx`; `r3-1` is the same bug in the pin seeder, never grepped for.
  Worth asking what *else* anchors to a bounding-box corner.
- **`extended-types-dead` (`r2-9`) is the standing discoverability item.** v0.6
  added guidance on when an extended type beats a first-class one. If the agent
  still never picks one, that is a **docs** finding, not a code one. Ask for
  "the mechanics **and** something simpler for people who don't care about the
  mechanics" if you haven't yet.

---

## 5. At close — the five things that only happen once

### 5a. PROMOTE THE FIXTURE — do this first, the day the run ends

`argus3` lives in `/tmp`. **Run 2's project was swept before anyone saved it**
— the scoped annotation, the archived branch, seven artifacts, all gone.
`.scratch/argus-v05/` survives only because run 1 copied it in time.

```bash
A=/tmp/claude-1000/-home-cognizac-Projects-wysiwyg-grilling-skill-worktrees-capability-assessment/5feaae42-7a5a-49a0-a771-7a113b0a7a4c/scratchpad/argus3
$S promote "$A/project_knowledge" .scratch/argus-v06
```

`.scratch/` is gitignored, matching the `argus-v05` convention. `promote`
rewrites absolute paths to `{{PROJECT_ROOT}}`/`{{HOME}}` and **reports every
substitution** — read that output, it never substitutes silently. Then confirm
nothing else holds an absolute path (`STATEBOX.json` deliberately records the
source; that one is provenance).

This fixture is the regression corpus every new check gets validated against.
It is worth more than the findings.

### 5b. Cluster, tally, compare

```bash
$L --run r3 clusters      # findings grouped by cause → this is the WP cut
$L --run r3 report        # markdown: turn log, findings table, evidence, causes
$L --run r3 tally --json
$L diff r2 r3             # mechanical comparison — do NOT hand-write this table
```

Then **discount the raw counts in prose**. Deliberate overrides, your own probe
pollution, and several notes from one cause all inflate the number.

### 5c. Name the headline, and predict the next one

Both prior runs' worst findings were the same shape:

> *The model believes something about the drawing that the drawing does not say.*

Build the clustering table — one row per finding, columns
`| What the model believes | What the drawing does |` — and write the resulting
sentence down. Then **predict where run 4's worst finding will be** and put the
prediction in the method doc. It has been right twice.

R3-2 already looks like a fourth instance in new clothing: the *header* believes
it is the agent's move while the *screen* is asking the user to decide.

### 5d. Draft the run-4 checklist

```bash
$L --run r3 checklist --from r3
```

It is a draft. Two things only you can do:

- Make each item a **falsifiable in-session assertion** that names who should
  catch it — *"rename a label on one variant frame only; **the lint** must catch
  it, not the agent"* — not "verify X works".
- Keep at least one **discoverability item**: an affordance that provably
  exists but the agent may never reach. If it isn't reached, that's a docs
  finding. This is the cheapest instrument for separating docs from code.

Carry deliberate non-fixes into a `Still open` list with reasons, so a decision
made on purpose isn't re-found as a new finding in run 4.

### 5e. Reissue the method doc

`~/docs/optimization/wysiwyg-grilling_v0.7.md`, superseding v0.6 on what
changed and pointing back at it for what didn't. Say which in the header.

---

## 6. What to send back to me

A single message. Paste the command output verbatim rather than summarizing —
I want the mechanical numbers, not your reading of them.

1. **`$L diff r2 r3`** — verbatim.
2. **`$L --run r3 clusters`** — verbatim.
3. **`$L --run r3 report`** — verbatim (this is the notes file's spine).
4. **The gap list from section 2**: which of the seven probes you had already
   planned, which you had not, and any beat **you** ran that is *not* in that
   list. That last group is the most valuable thing in this whole document —
   it is a gap in the written protocol, and I will fix the protocol.
5. **The headline sentence** and your run-4 prediction.
6. **The fixture path** and `promote`'s substitution report.
7. **Attribution for every finding**, with the evidence you used — and flag any
   you had to leave `unknown`, and why.
8. **Anything the ledger made harder rather than easier.** If a field didn't fit
   a finding, or you wanted a bucket that doesn't exist, say so. Back-filling
   run 2 already exposed one missing bucket (`tool-false-alarm` — three of run
   2's findings were the tool speaking *wrongly*, which the original four-way
   taxonomy scored as "working as intended"). There are probably more.

---

## 7. What I will do with it

- Fold the gap list into `references/assessment-mode.md` (protocol) and
  `references/carry-forward.md` (artifacts).
- Fix whatever section 6.8 turns up in `ledger.py`.
- Not touch your run, your findings, or your notes file. The R3 notes file is
  yours; the ledger is shared and append-only.
