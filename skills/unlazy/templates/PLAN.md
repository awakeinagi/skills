# Plan: <task>

Scope: <validated pipeline id; store this file at .unlazy/<scope>/PLAN.md>
Depth: tree <N>
Mode: orchestrated

## Contract

Decide before fan-out:

- Interfaces: <signatures, schemas, formats, integration points>
- Ownership: <one complete set of repository-relative paths per leaf; no absolute paths, traversal, or concurrent overlap>
- Dependencies: <leaf ids that must be VERIFIED first>
- Host launch mode: <Codex native subagents | Claude background Agents | Claude Dynamic Workflow | sequential fallback>
- Wave policy: <which independent READY leaves launch together and the maximum host concurrency>
- Toolchain: <runtime versions, shell, working-directory rules, test commands>
- Conventions: <naming, errors, compatibility, formatting>
- Manual review: <owner and evidence standard for consequential manual gates>

## Current contract inventory

Contract revision: 1. Before fan-out, reread the original request and current amendments. Record every independently omittable required outcome and every constraint that changes acceptance; do not copy credentials, private text, or unrelated context.

| ID | Required outcome or constraint | Owner | Observing gate or manual review | Disposition | Revision |
|---|---|---|---|---|---|
| C1 | <concise paraphrase> | <leaf/node> | <qualified gate/reviewer> | ACTIVE | 1 |

Use stable ids. On an amendment, increment the revision and reconcile every affected row before dispatch or completion credit. `ACTIVE` is complete only with a current owner and observation. `ABANDONED`, `DEFERRED`, and `OWNER_DECISION` are honest non-completion; only explicit user authority may use `REMOVED_BY_USER`.

## State vocabulary

Leaf state is exactly one of:

- WAITING: at least one id in Needs is not VERIFIED
- READY: dependencies are VERIFIED and ownership can be claimed
- IN-FLIGHT: dispatched, not yet parent-verified
- VERIFIED: parent --reverify passed and manual gates were reviewed
- ABANDONED: a required gate has a visible handoff

Branch state is exactly one of OPEN, VERIFIED, or ABANDONED.

## Tree

Use `leaf-` paths for work leaves and `node-` paths for branch integration. A row
id is digits and dots only, and the ledger it names must end in that exact id:
row `1.1.2` owns `gates/leaf-1.1.2.md`, never `gates/leaf-parser.md`. The id is
how a dispatched leaf is matched to its row, so a descriptive filename leaves the
leaf undescribed by the tree, and `open` refuses it rather than dispatching work
whose declared dependencies it cannot read. Name the work in the row's title,
which is free text, and keep the filename numeric.

- 1 <task> .............. GATES.md ..................... State: OPEN
  - 1.1 <branch> ........ gates/node-1.1.md ............ State: OPEN
    - 1.1.1 <leaf> ...... gates/leaf-1.1.1.md .......... Needs: - ...... State: READY
    - 1.1.2 <leaf> ...... gates/leaf-1.1.2.md .......... Needs: - ...... State: READY
  - 1.2 <branch> ........ gates/node-1.2.md ............ State: OPEN
    - 1.2.1 <leaf> ...... gates/leaf-1.2.1.md .......... Needs: - ...... State: READY
    - 1.2.2 <leaf> ...... gates/leaf-1.2.2.md .......... Needs: 1.2.1 .. State: WAITING

This tree is read, not just written. Lint it before dispatching, because a tree
that cannot be parsed means planning is not finished:

```text
node <skill-dir>/scripts/gate-lint.mjs .unlazy/<scope>/PLAN.md
```

`open` refuses a leaf whose `Needs` are unsatisfied, and satisfaction is read
from the dependency's own ledger rather than from the `State` field below, so
keep both current: a ledger that is complete while this tree still calls it
`WAITING` stops the next wave until the two are reconciled.

Every leaf repeats its complete ownership as an `OWNS:` header in its ledger. Claim each concurrently dispatched leaf with `--claim`, then open and seal a native launch wave as described in `references/dispatch.md` before changing its state to IN-FLIGHT.

## Status log

Append events to `.unlazy/<scope>/status.log`; do not copy the event history into this file:

```text
node <skill-dir>/scripts/gate-check.mjs --scope <scope> --log "leaf-1.1.1 dispatched"
node <skill-dir>/scripts/gate-check.mjs --scope <scope> --log "leaf-1.1.1 verified"
```

Record contract amendments, plan changes, dispatch, parent verification, abandonment, branch integration, and lease release. Update the live State and Needs fields above when state changes; keep the log append-only. Before root completion, reread the current request and review every current inventory row against its owner and observing gate or manual review.
