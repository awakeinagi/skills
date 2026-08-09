# Session handover — skill-optimizer v0.2.x and assessment run 3

Written before a context compaction. Everything needed to resume without
re-deriving it.

---

## 1. The new arrangement (what changed, and what to do next)

Until now I have been a **relay**: another Claude session (`5feaae42`, renamed
`optimizer_R3`) drove assessment run 3 against `wysiwyg-grilling`, and the user
carried messages between us. That stops.

**From here: I spawn my own optimizer subagent and interact with it directly.**
The user's instruction — *"task it with optimizing the wysiwyg-grilling skill
and assess its performance."*

So there are two jobs, and they must not be conflated:

1. **The subagent runs `skill-optimizer` against `wysiwyg-grilling`.**
2. **I assess how well `skill-optimizer` itself performed** — which is the
   whole reason this work started (see §2).

### The decision to make first

Run 3 is **still open** and belongs to another session. Options:

| Option | Notes |
|---|---|
| **Fresh run 4, new project** | No collision. Clean test of the skill as written. Loses run 3's momentum but run 3's *findings* are already in the shared ledger, so nothing is lost. **Probably right.** |
| Take over run 3's close | Needs the other session stood down. The `argus3` agent is not addressable from here, so "taking over" means a fresh agent reading the canvas cold. |
| Wait for run 3 to close | Then do A8–A12 on it. |

**Do not touch `argus3`** (`/tmp/claude-1000/…/5feaae42-…/scratchpad/argus3`,
server pid was 167225, port 45353) unless the user confirms the other session
has stopped. Two assessors on one canvas invalidates both.

---

## 2. What `skill-optimizer` is now

`/home/cognizac/.claude/skills/skill-optimizer/` — **its own git repo** (I ran
`git init`; it wasn't under version control). 21 commits. `3fc9468` is the
v0.1.0 baseline; `f17f3b9` is v0.2.0; everything after is run-3 harvest.

`SKILL.md` is 46,807 bytes vs a 45,929 baseline — it crept ~900 bytes over
during the harvest. Worth a trim pass.

### Structure

- **Two modes.** Step 0 routes: *sweep* (the original claim-driven loop, mostly
  unchanged) vs *assessment* (A0–A12, multi-turn, play the user for real).
- **Attribution** is a triage axis beside severity: `tool-told`,
  `tool-told-ignored`, `tool-false-alarm`, `capability-undocumented`,
  `tool-silent-covered`, `tool-silent-shipped`. Decides code-fix vs docs-fix.
  A pass resting on `tool-silent-covered` is not a pass.
- **A6b DISCRIMINATE** — the single most important addition. Name the rival
  that explains the same observation, design a test whose outcome *differs*,
  predict, then file. Findings without a discriminator are flagged.

### references/

`assessment-mode.md` (protocol, 15 standing probes, §6b instruments),
`finding-discipline.md` (the rules), `observer-effect.md`,
`assessor-adapter.md` (7-capability contract), `fixtures.md`,
`carry-forward.md`, `postmortems.md`, plus the pre-existing `static-scan.md`,
`config-matrix.md`, `extraction-patterns.md`, `report-format.md`,
`spike-and-propose.md`.

### scripts/ (stdlib only, optional by design)

- `ledger.py` — turns, findings, `--rival/--discriminator`, `--found-by`,
  multi-valued `--attr`, watch records (`--blocked-by`), confirm records,
  retract (kill a claim, keep the finding), withdraw, clusters, tally `--json`,
  cross-run `diff`, `checklist`.
- `statebox.py` — checkpoint / restore / list / promote (`--with-checkpoints`).
- `lastmsg.py` — mtime-discovered subagent transcript reader; `--grep` flags
  hits that came from `Read`/`Glob`/`Grep` (the agent quoting docs at itself).

### The rules that earned their place in run 3

Each has a worked failure attached in `finding-discipline.md`:

- State the rival before you file (strong inference) — **A6b**
- For *"the agent cannot learn X"*, the agent is the instrument — **but only
  for reachability, never for causal mechanism**
- Do not manufacture the condition that makes your finding true
- When two defects cancel, the outcome is evidence for neither
- The strongest evidence is one artifact containing its own contradiction
- Ask which *direction* a fix was applied in (mirror-direction; a **generator**,
  not a predictor — 6 hits, 2 misses)
- Know which rules are generators and which are predictors
- A third defect on the same lines is structural, not a third bug
- Prove a signal has a delivery path — and enumerate that path from the docs
- Before tuning a check's precision, prove its signal has a delivery path
- P2 assumes the reader can *see* the mismatch; **when the failure mode is
  invisibility, it's P1**
- Instruments ≠ probes (§6b), including **the cold look** and **the outside
  questioner**

---

## 3. Run 3's state (owned by the other session)

- Notes: `capability-assessment-notes-optimizer_R3.md` (repo root, untracked).
- Ledger: `.ledger/r2.jsonl` and `.ledger/r3.jsonl` in this worktree. I
  back-filled r2 from the R2 notes; r3 is shared and append-only.
- T1–T10 complete. Stopped before T11/close. Cold look was running.

### Ledger truth (take numbers from here, not from prose)

```
run r3  vs v0.6
  turns 11   findings 14 (13 distinct causes)   withdrawn 1
  severity     P1 6  P2 2  P3 6
  attribution  tool-silent-shipped 10, tool-false-alarm 2, tool-told 1,
               tool-told-ignored 1, capability-undocumented 1
  found by     assessor-query 9  assessor-look 4  user-look 1
  → 5 found only by LOOKING
```

**The other session's prose says "P2 ×3, P3 ×5" — that predates the R3-14
downgrade and is wrong.** It also believed the severity row includes the
withdrawn P1; it does not (`by_sev` is computed over `live()`). Live P1s = 6,
plus withdrawn `r3-5` = 7 P1s ever.

### Findings

`r3-2` pulled-whose-move (P1) · `r3-4` stored-vs-rendered (P1) · `r3-6`
pulled-blindness (P1) · `r3-10` mixed-batch-write-order (P1) · `r3-12`
dry-run-side-effect (P1) · `r3-13` binding-to-nothing-unchecked (P1) · `r3-1`
bbox-anchor (P2) · `r3-7` many-to-one-as-pairs (P2) · `r3-3`, `r3-8`, `r3-9`,
`r3-11`, `r3-14`, `r3-15` (P3) · **`r3-5` withdrawn**.

**Headline (agreed):** *The model and the artifact can disagree in either
direction, and nothing checks.* Spans check-vs-render (×3 cross-run),
record-vs-file (r3-10), file-vs-nothing (r3-12), agent-narration-vs-queue
(r3-15).

**R3-10 + R3-12 are a compensating pair** — a dry run writing to disk
unversioned, and a mixed batch clobbering it back from a stale snapshot. They
**ship as one work package**; fixing either alone reports a confusing result.

### Verdict constraint that must survive

**The instrument changed mid-run.** Run 2 found 2 P1s, run 3 found 6, and A6b /
attribution / found-by / half the rules did not exist for run 2. The delta is
**not a trend**. The re-test checklist is the only like-for-like comparison.

### Open at the close

Item 2 (does the Deep-Dive price chart mint a `kind: image`? queued in #17),
item 11 (final lint count as distinct causes), the handover
`export --with-footnotes` beat, cold-look results, and the three carry-forward
artifacts (method doc `wysiwyg-grilling_v0.7.md`, notes file, run-4 checklist).

---

## 4. Known gaps in my own tooling

- **`ledger.py clusters` is per-run.** Cross-run cause recurrence
  (`stored-vs-rendered` = r2-8, r2-10, r3-4; `bbox-anchor` = r2-11, r3-1) has
  to be spotted by hand. Worth adding.
- **`SKILL.md` is ~900 bytes over its baseline.**
- **No confirm/watch records exist in the r2 back-fill** — only r3 has them, so
  `diff r2 r3` understates run 2's exercised surface.
- The **waiting tiers remain untested** and make push-vs-pull findings
  *undiscriminable* under the end-your-turn spawn prompt.

## 5. Key paths

```
skill:      /home/cognizac/.claude/skills/skill-optimizer/   (git repo)
target:     <worktree>/skills/wysiwyg-grilling/              (symlinked into ~/.claude/skills/)
worktree:   /home/cognizac/Projects/wysiwyg_grilling_skill.worktrees/capability_assessment
ledger:     <worktree>/.ledger/{r2,r3}.jsonl
run3 notes: <worktree>/capability-assessment-notes-optimizer_R3.md
brief sent: <worktree>/RUN3-CARRY-FORWARD-BRIEF.md
method docs:~/docs/optimization/wysiwyg-grilling_v0.{5,6}.md
fixtures:   <worktree>/.scratch/{argus-v05,argus-v06-prefork,argus-v06-checkpoints}/
```

Symlink check before any run: `ls -l ~/.claude/skills/wysiwyg-grilling` must
point at the intended worktree (currently `capability_assessment`, HEAD
`10a28bd` = v0.6).

## 6. Standing user preferences

- Questions to the user go through `AskUserQuestion` with detailed previews,
  batched. Each response item's preview should throughly explain the what and why of the potential
  response and included detailed examples where applicable.
