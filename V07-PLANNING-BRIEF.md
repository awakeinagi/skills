# v0.7 planning brief — pick up here after compaction

Round 3 of the capability assessment is **closed**. Next task: **a
comprehensive plan of updates to `skills/wysiwyg-grilling` based on the
findings**, then implement it.

## Where everything is

| What | Where |
|---|---|
| Findings ledger (authoritative) | `.ledger/r3.jsonl` — read with `ledger.py`, never by hand |
| Full report (evidence, rivals, discriminators) | `ledger.py --dir .ledger --run r3 report` |
| Round-3 notes (narrative, verdict, headline) | `capability-assessment-notes-optimizer_R3.md` |
| Method doc for round 4 + re-test checklist | `~/docs/optimization/wysiwyg-grilling_v0.7.md` |
| Regression fixture, 56 files | `.scratch/argus-v06/` (gitignored) |
| Rollback tarballs | `.scratch/argus-v06-checkpoints/` |
| Round-2 notes (prior round) | `capability-assessment-notes-optimizer_R2.md` |
| Generic protocol | `~/.claude/skills/skill-optimizer/references/` |

```bash
L="python3 /home/cognizac/.claude/skills/skill-optimizer/scripts/ledger.py --dir .ledger"
$L --run r3 report      # everything, including rivals + discriminators
$L --run r3 clusters    # cause groups → these ARE the work-package cut
$L --run r3 tally       # every number that goes in a commit message
```

**Numbers rule:** every figure quoted anywhere must be pasted from `tally` or
`report` **run at the moment of writing**. I got this wrong twice in round 3.

---

## The 16 findings

`canvas.py` = `skills/wysiwyg-grilling/scripts/canvas.py`.
`App.tsx` = `frontends/wysiwyg-grilling/src/App.tsx`.

| id | sev | cause | where | one line |
|---|---|---|---|---|
| r3-10 | P1 | mixed-batch-write-order | `canvas.py:5681` | `rename_artifact` reverted when the batch also draws; the save record stores the stale name so the revert is durable |
| r3-12 | P1 | dry-run-side-effect | `canvas.py:6347` | `apply --check` persists `rename_artifact` to disk — unversioned |
| r3-13 | P1 | binding-to-nothing-unchecked | `canvas.py:4098` | deleting a node orphans its arrows; the endpoint lint `continue`s on a missing target |
| r3-2 | P1 | pulled-whose-move | `canvas.py:5692` | under `pulled` nothing commits → header, round counter and pin aging all freeze |
| r3-4 | P1 | stored-vs-rendered | `App.tsx:534` | `snapshot`/PNG renders unwrapped label text; disagrees with canvas **and** SVG |
| r3-6 | P1 | pulled-blindness | `canvas.py:8036` | queued apply branch `return 0`s before `_print_tripwires` / `_print_debt` |
| r3-1 | P2 | bbox-anchor | `canvas.py:2177` | pin ❓ at bbox corner → 79px off a diamond (22px on a rect) |
| r3-16 | P2 | endpoint-check-one-sided | `canvas.py:4101` | an endpoint 56px **inside** its box passes the check that fires at 26px outside |
| r3-17 | P2 | registry-is-branch-blind | `canvas.py:5408` | registry is global, scenes are per-branch → asserts views a branch lacks, `VIEW_DEBT=none` |
| r3-7 | P2 | many-to-one-as-pairs | `canvas.py:5834` | tripwire fans out arity−1 times; emission iterates pairs, suppression reasons per mapping |
| r3-11 | P3 | branch-origin-unjoined | `canvas.py:5685` | branch has no rationale and no fork pointer; `head` advances past it |
| r3-14 | P3 | apply-response-omits-branch | `canvas.py:8044` | apply names no branch; the UI save toast does (`App.tsx:424`) |
| r3-3 | P3 | apply-feedback-invisible | `App.tsx:577` | applied artifact outside the current concept never joins the filmstrip |
| r3-8 | P3 | many-to-one-as-pairs | `canvas.py:5141` | 3.2.4 cartesian product → "pick one" on a legitimate compression |
| r3-9 | P3 | standing-nag-asymmetry | `canvas.py:8085` | user-armed tripwires are the only nag you must *pull* |
| r3-15 | P3 | state-claim-from-memory | — | **behavioural, n=1** — agent summarised the queue from memory. Do not write code for this |

---

## Proposed work-package cut (validate against `clusters` before committing)

**WP1 · The versioning boundary — r3-12 + r3-10, and they MUST ship together.**
This is the run's headline and a *compensating pair*: `--check` wrote the name
to disk, the real apply reverted it, and "the name never changed" was evidence
for neither. Fixing either alone reports something confusing — fix the clobber
alone and the name sticks via an unversioned write; fix the dry run alone and
the clobber becomes visible for the first time. Each differential control must
hold the other constant. Merge as one commit.
*Mechanism:* `check_batch` deep-copies `self.registry` but `artifact_meta` and
the artifact **file** are outside that guard; and `artifacts[aid]["meta"]` is
snapshotted at `5543` *before* `_apply_registry_ops` at `5606`, then written at
`5679-5681`.

**WP2 · `pulled` cadence — r3-6 + r3-2 (+ r3-9, r3-14).**
r3-6's fix **must be structural, not another patch**: three defects across
three assessments on the same ~8 lines, and the code comment records v0.4
patching one of them. The shape is *an early return skipping a shared
epilogue* — single exit, `finally`, or hoist the epilogue into the caller.
r3-2: derive the displayed state from the pending queue, and advance `round` on
a queued revision so pins age under `pulled`. Also document `set_round`
(15 registry actions implemented, 11 documented).

**WP3 · The endpoint loop — r3-13 + r3-16.**
Both live in the same `for a in arrows` loop at `4091-4110`. `if tgt is None:
continue` skips exactly the broken ones; the bbox test can't see an endpoint
*inside* the box. Decide separately whether deletion should cascade to bound
arrows (arguable — Excalidraw keeps them); the silence is not arguable.

**WP4 · Mappings are relations, not bags of pairs — r3-7 + r3-8.**
Emission at `5834` and the 3.2.4 join at `5141` both explode an N-element
mapping into a cartesian product, while the *suppression* path already reasons
per-mapping. Ask the question once, per mapping.

**WP5 · The export path — r3-4.**
`App.tsx:534` feeds `exportToBlob` from the server's stored elements (text
deliberately unwrapped) instead of the editor scene Excalidraw has re-wrapped.
Highest user-visible payoff: a cold observer given these PNGs reported broken
text in 4 of 6 artifacts, **none of it in the drawings**.

**WP6 · Small, independent — r3-1, r3-3, r3-11, r3-17.**
r3-1: reuse the `App.tsx:1464` per-type inset in the pin seeder (this is the
*third* payout of the shape-grep rule on this codebase). r3-17 needs a decision
first: is the registry project-level or branch-level? The finding is narrow —
`VIEW_DEBT` is an *actionable prompt* computed from a global registry while the
agent draws onto a branch.

---

## Do NOT fix

- **R2-2** (concept-reattach heuristic). Withdrawn in v0.6 because the fix broke
  two tests encoding the opposite behaviour. It fires at close on purpose.
- **No merge/rejoin between branches.** Read the code before touching: for a
  design tool a branch is an alternative to show, not work to integrate.
  Round 3 declined to file it.
- **r3-15** — behavioural, n=1.
- The admin-console sibling-screen question — held as a watch, no discriminator.

---

## Verification (non-negotiable)

```bash
python3 -m unittest discover -s tests -q
uvx ruff check . && uvx mypy skills/wysiwyg-grilling/scripts/canvas.py
npm --prefix frontends/wysiwyg-grilling run lint && … run typecheck && … run build
uvx pre-commit run --all-files          # before EVERY commit
```

- **Every fix needs a differential control in BOTH directions.** "It fires on
  the defect" is worth nothing without "and it stays quiet on the legitimate
  case" — both of v0.6's wrong first attempts were caught by the silent half.
- **Replay every new check against `.scratch/argus-v06/`** before believing it.
  v0.5's text-fit lint produced 16 warnings there, 13 false, on first attempt.
- **After each fix, grep for the same SHAPE elsewhere.** r3-1 exists *because*
  WP5 of v0.6 fixed the identical bbox-corner bug in `App.tsx` and nobody
  grepped.
- Repo constraints (`AGENTS.md`): `canvas.py` stdlib-only and single-file,
  Python floor 3.9 with `from __future__ import annotations`, Google docstrings
  on everything, **no autoformatter**, match the packed ~79-col style by hand.
- Commit convention `v0.7 WPn: …`. The thematic cluster (WP1) ships alone and
  first.

## Also owed

- `capability-assessment-notes-optimizer_R3.md` needs a **"Fixed — v0.7"**
  section mapping each finding to its resolution, including anything withdrawn.
- The v0.7 doc's round-4 re-test checklist (§5) is the acceptance test for this
  work — each item already names *who* must catch it.
