# Native agent dispatch

Use this contract whenever orchestrated mode has two or more independent `READY` leaves. Unlazy records native launches; the host creates the agent sessions.

For one wave, every native launch call and every `start` record must finish before the first wait, join, result read, or return record.

## Open and seal a launch wave

Claim every leaf first. This is enforced, not advisory: `open` refuses a wave naming any leaf that holds no registration in that scope and exits `3`. A leaf that declares no `OWNS:` paths still registers, and its registration says it may change nothing. Partition more `READY` leaves into later waves when they exceed the host's current concurrency limit. Then open one wave with the exact ids:

```text
node <skill-dir>/scripts/dispatch-check.mjs open --scope <scope> --wave ready-1 --leaf leaf-1.1.1 --leaf leaf-1.1.2
```

For each leaf, call the host's native nonblocking launch tool and record the opaque, nonsecret handle it returns:

```text
node <skill-dir>/scripts/dispatch-check.mjs start --scope <scope> --wave ready-1 --leaf leaf-1.1.1 --handle <host-agent-id-1>
node <skill-dir>/scripts/dispatch-check.mjs start --scope <scope> --wave ready-1 --leaf leaf-1.1.2 --handle <host-agent-id-2>
```

When a leaf was given its own worktree, add `--workspace <path>` to its `start` so the isolation claim is verified rather than recorded. `start` then refuses, exit `3`, a path that does not exist, a main checkout, or a worktree of a different repository, which is the exact shape a misdirected launch-tool isolation produces: a real worktree of the wrong tree, passing every test except whose history it advances. One wave watched a leaf's isolation fall through silently and commit straight onto the shared checkout, caught only by the driver auditing git afterwards; this makes that discovery happen before anything is waited on. The flag is optional, and a `start` that claims nothing behaves as before.

Seal the wave before waiting for any result:

```text
node <skill-dir>/scripts/dispatch-check.mjs seal --scope <scope> --wave ready-1
```

Seal fails until every declared leaf has a distinct start handle. `return` fails before seal. These refusals catch the serial pattern where a driver launches one leaf, waits for it, and only then launches the next.

When a native agent finishes, record the return before parent re-verification:

```text
node <skill-dir>/scripts/dispatch-check.mjs return --scope <scope> --wave ready-1 --leaf leaf-1.1.1
```

A return records scheduler completion, including a failed worker result. It does not mark the leaf `VERIFIED`; the parent still runs the leaf gates and reviews manual evidence.

## Durability before teardown

A return is not a durability signal. When a leaf ran in its own worktree, sandbox, or container, that workspace must survive until the parent has verified the leaf against a durable artifact. Tearing it down at return time destroys the work of any leaf that finished without committing, and the wave records that leaf as returned either way.

Require each dispatched writing leaf to commit to its own branch and record the commit id as ledger evidence. Verify in this order:

```text
dispatch-check return   ->   gate-check --reverify   ->   release lease   ->   remove workspace
```

Reverify a leaf that ran in its own worktree against that worktree, not against the driver's checkout. Name the ledger file: `--leaf` selects a lease, not a ledger, so it cannot target one leaf for reverification. An explicit ledger file and `--scope` are mutually exclusive, so pass `--root` and `--cwd` instead of the scope when naming a file.

```text
node <skill-dir>/scripts/gate-check.mjs --reverify .unlazy/<scope>/gates/leaf-1.1.1.md --root <repository-root> --cwd <leaf-worktree>
```

Without an explicit `--cwd`, the check resolves its working directory from the ledger's own location, which is the driver's `.unlazy/<scope>/gates` directory rather than the worktree holding the leaf's work. A scope-wide `--reverify` from the main checkout has the same trap while leaf branches are unmerged: it measures the main tree, where the work does not yet exist, and the failures it prints are real commands genuinely failing there, indistinguishable from a regression. Nothing can tell you the tree was the wrong one, so treat "everything failed at once after isolated leaves passed" as a which-tree question before a what-broke question.

Reverifying the same leaf from the merged root after it verified in its worktree needs its own approval pass first, for the same reason the worktree needed one: the resolved working directory is a bound input, and the merged root is a third one. A branch gate that nests `gate-check --reverify` cannot grant that approval itself, so run `--reverify --approve` on each child ledger at the root `--cwd` as a reviewed driver action before the branch ledger can pass. Expect one approval per leaf per direction, not one per pipeline.

Pass absolute paths once verification leaves the driver's own root. A relative `--cwd` and a relative ledger path both resolve against `--root` rather than against the invocation directory, so relative forms that look correct compose into paths that do not exist.

Reverification also audits ownership. It compares the leaf's real changes against the commit pinned when the leaf registered, and refuses to verify a leaf that changed paths registered to another leaf from a worktree of its own, or one whose audit could not run at all. Straying outside its own registration into paths nobody holds is a warning rather than a refusal, and so is the same finding raised from a working tree several leaves share, where the comparison cannot say which of them wrote the path. Read [parallel.md](parallel.md) for the full table; nothing is reverted, so an unverified leaf keeps its work and owes a decision.

A first-time oracle prints `APPROVAL REQUIRED` and `NOT RUN` instead of executing. That is neither a verification nor a failure. Inspect the oracle and approve it in the same run: `--reverify --approve`. Reverifying against a worktree reaches this even for a gate the leaf already met, because the resolved working directory is a bound input and a worktree is a different one, so expect it once per leaf rather than once per pipeline. A plain `--approve` will not do here. It does not re-execute a gate already recorded as met, so it never reaches that oracle, approves nothing, and still reports `ALL MET`, leaving the next reverification stopped on the same line. Keep the workspace and the lease until a run actually executes and passes: a `NOT RUN` result has measured nothing, so tearing down after one destroys unverified work exactly as a premature return would.

Do not release a lease or delete a worktree between the return and the reverification. If a returned leaf has no durable artifact, treat it as unverified work rather than a failed check: reverification against a destroyed workspace measures nothing, so record the loss and redispatch the leaf. Read [parallel.md](parallel.md) for the isolation levels and the shared surfaces a worktree leaves exposed.

Check the finished wave with:

```text
node <skill-dir>/scripts/dispatch-check.mjs status --scope <scope> --wave ready-1
```

`status` exits `0` only after every declared leaf returned. Dispatch state and timestamps live in `.unlazy/<scope>/dispatch.json`; lifecycle events also append to the scope status log.

The state loader requires string ids, handles, and abandonment reasons plus a possible transition history: returns require an all-started sealed wave, terminal timestamps must exist and follow prior transitions, and a fully returned wave must be complete. Hand-editing an impossible terminal state fails closed. The primary `gate-check.mjs --scope <scope>` reduction includes this aggregate state and cannot print `ALL MET` while a wave is open, sealed, abandoned, or invalid.

## Codex adapter

[Current Codex releases support parallel subagents](https://developers.openai.com/codex/agent-configuration/subagents). Use the native subagent tools available in the host. When the tools are named `spawn_agent` and `wait_agent`, follow this exact order:

1. call `spawn_agent` once for each leaf in the open wave
2. record each returned agent id with `dispatch-check start`
3. seal the wave
4. call `wait_agent` only after seal
5. record each completion with `dispatch-check return`, then reverify it

Do not use `codex exec` as a substitute. It creates a separate CLI process rather than a native subagent owned and visible through the current host.

## Claude Code adapter

[Claude Code background subagents run concurrently](https://code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background). Launch every leaf as a background `Agent` task, record every returned task or agent id, and seal before reading any result. Do not issue foreground Agent calls one after another.

For a large regular fan-out, prefer a [Dynamic Workflow](https://code.claude.com/docs/en/workflows). Its `pipeline()` primitive runs agent work across a list under the runtime's concurrency limit. The workflow must still preserve the same semantic barrier: schedule the whole fan-out before collecting its first result. Open a CLI dispatch wave only when the workflow surface exposes a distinct native handle for each agent. Otherwise retain the generated workflow script and runtime progress as branch evidence without claiming a CLI-verified wave.

Do not use `claude -p` as a substitute for an available native background Agent or workflow. A shell process farm loses the current session's native scheduling and observability.

Some Claude Code contexts expose only a synchronous `Agent`, with no background flag and no id returned before the call completes. There is then no handle to record, because launching and waiting are the same act, and the wave protocol as written cannot be satisfied literally. Do not fabricate a handle to satisfy the shape. Open the wave, then issue every launch for it in one message, which is what actually produces concurrency here. Record `start` for each leaf with the id its call returned, then `seal`, then `return` each leaf. Note in the status log that the handles were recorded after return rather than before.

Do not try to seal before the launches: `seal` requires a `start` for every leaf in the wave and refuses with `cannot seal <wave>: missing starts for ...`, so on this host the seal necessarily lands after the batched message rather than before it. The barrier the protocol exists to enforce is that no result is read before the whole fan-out is scheduled, and issuing the launches in one message is what preserves it. Sealing early is how a host that returns handles up front evidences that barrier; it is not the barrier itself, and it is unavailable here.

## Failure and fallback

If a native launch fails before returning a handle, leave the wave open, fix the launch problem, and retry that leaf. Do not seal a partial wave. If recovery is impossible, preserve the audit trail instead of inventing a handle or deleting state:

```text
node <skill-dir>/scripts/dispatch-check.mjs abandon --scope <scope> --wave ready-1 --reason "<bounded nonblank reason>"
```

An abandoned wave is terminal and `status` exits `1`. The Stop hook does not block on it, but emits a bounded `HANDOFF REQUIRED` message naming the wave without copying its free-form reason into the privileged host message. Surface the reason from dispatch state in the final handoff. If the host has no nonblocking launch capability, record the limitation in `PLAN.md`, execute a declared sequential fallback, and do not open or describe a parallel wave.

Opening a wave is an execution claim. Do not invent handles, record a foreground result as a start, or call simultaneous work proved merely because commands ran quickly.

## Evidence boundary

The launch barrier proves that the host accepted every native start before the driver accepted a return. It does not prove worker honesty, exact CPU overlap, filesystem isolation, or successful integration. Leases, parent re-verification, and branch gates remain separate requirements.
