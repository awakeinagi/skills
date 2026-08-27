# Parallel pipelines and leaves

Scopes and ownership leases coordinate cooperating unlazy processes. They prevent accidental cross-certification and refuse declared ownership overlap. They do not sandbox shell commands, enforce operating-system permissions, or stop a process that ignores the protocol from writing any file.

## Layout

```text
.unlazy/
  <scope>/
    PLAN.md
    GATES.md
    gates/
      leaf-*.md
      node-*.md
    status.log
    session
    hook-state.json
  locks/
```

Keep `.unlazy/` untracked. The legacy single-pipeline layout, `GATES.md` plus `gates/*.md` at the project root, remains available for solo work.

## Scope resolution

A scoped checker invocation selects one pipeline in this order:

1. `--scope <id>`
2. `UNLAZY_SCOPE`
3. the only scope present
4. the legacy layout when no scoped pipeline exists

The Stop hook can additionally use the current Claude Code `session_id` binding written by `--bind`. A binding associates a session with a scope; it is not authentication.

When several scopes exist and none resolves, the checker refuses instead of running every ledger. The Stop hook allows the stop with a diagnostic instead of blocking a session on an unknown pipeline.

Use scope ids that match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` and are not `.` or `..`. Do not use separators, traversal, or absolute paths.

## What a scope isolates

A scope limits unlazy's own:

- default gate discovery
- status log target
- Stop-hook resolution and progress state
- lease owner label

A scope does not limit a `CHECK:` process. Checks inherit ambient operating-system access and can read or write outside the scope. Use separate worktrees or stronger process isolation when commands themselves must be isolated.

## Ownership declarations

Declare repository-relative paths before the first gate:

```markdown
OWNS: src/api/**, tests/api/**
```

Reject absolute paths and any path containing a `..` traversal segment. Every leaf dispatched concurrently must declare all paths it may modify and claim them before work:

```text
node <skill-dir>/scripts/gate-check.mjs --scope api --leaf leaf-1.2.1 --claim
```

Claiming checks all existing leases and writes the new lease while holding one global lease lock. Two simultaneous conflicting claims cannot both succeed. A claim is all-or-nothing.

**Every dispatched leaf must register, including one that declares no paths.** Registration is a roll call, not a permission grant: nothing in unlazy has ever restricted what a process writes, so a pathless registration records that an identity exists and that the leaf may change nothing. `dispatch-check.mjs open` refuses any leaf in the wave that holds no registration for that scope, and exits `3`. It answers that question from lease records the checker itself wrote, so it reads no ledger and there is nothing a leaf can omit from its own paperwork to escape the requirement.

Refusal happens at `open` rather than at `seal` because by seal the leaves have already launched and already written. A check there would report a collision instead of preventing one.

A scope/leaf label is itself exclusive while its registration exists. Repeating the same claim is refused; release that exact leaf before claiming it again. This prevents two workers dispatched with the same logical identity from both believing they own one lease, and it holds for a pathless registration too, which has no globs of its own to collide with.

## Audit what happened, not what was claimed

Registration records nothing about what a leaf may write. Nothing in unlazy ever has: a declaration is compared against other declarations and never against an actual write, and a leaf holding `src/api/**` can still write anywhere on the disk. Two declarations agreeing is therefore not evidence that two leaves stayed apart.

Registration pins the repository's current commit, and `--reverify` compares the leaf's real changes against it. That comparison is the only check here that observes what happened rather than comparing two claims, because version-control history is the one record the leaf being checked did not write.

| What the audit finds | Result |
|---|---|
| Only paths inside its own registration | verified |
| Paths outside its own registration, held by nobody | warning, still verifiable |
| Paths registered to another leaf, from a worktree of its own | not verified, handoff |
| Paths registered to another leaf, from a shared working tree | warning naming what could not be decided |
| No pinned commit, or git cannot answer | not verified, reason recorded |

The last two rows are the same observation read against different evidence. The comparison starts at the leaf's own pinned commit but ends at its working tree's `HEAD`, and that end describes one leaf only where `HEAD` advances for one leaf. A worktree carries its own; the main checkout's is shared with whatever else is worked there. So in a shared tree a sibling's own committed work lands inside this leaf's range and matches that sibling's registration, which is indistinguishable from this leaf having written it. Refusing there accuses correctly registered, finished work of a collision the evidence cannot show, and a channel that does that is one whose real findings get discounted. The audit says instead which question it could not answer. Give each leaf its own worktree to make the question decidable; note that this is a property of one leaf per working tree rather than of worktrees as such, so a single leaf left in the main checkout while its siblings are isolated is decidable too.

Committed changes, uncommitted changes, and untracked files all count. A leaf that never commits would otherwise present an empty change set, which reads as "touched nothing" rather than "produced nothing". Newly created files are covered by the declaration rather than exempt from it: exempting creation would let a leaf reach into another's territory by adding a file instead of editing one, which is cheaper than registering the extra work and would become the ordinary route around the rule. Unlazy's own `.unlazy/` state is excluded, being bookkeeping every leaf writes.

Nothing is ever reverted. An unverified leaf keeps its work and owes a decision, because a mechanism that discards an agent's output on a path-matching judgement destroys good work along with the stray edit.

Concurrent dispatch therefore needs a git repository. History is the whole basis of the audit, so where none exists there is nothing to compare a leaf against: `--claim` warns that no starting commit could be pinned, and reverification then leaves every registered leaf unverified rather than passing it on a check that never ran. That is the intended answer to an audit that cannot run, not a fault in the leaf's work, and no amount of rerunning will clear it. A project that keeps no version control has two honest routes. Initialise a repository and make one commit, because an empty repository has no `HEAD` to pin and reports the same missing starting commit that no repository does. Or register nothing and work the leaves one at a time against solo ledgers, where the audit does not apply and the protection it provides is genuinely absent rather than merely unproven.

An audit only applies to a leaf that registered, so a solo ledger is untouched. It sees only registrations that are still held: a leaf that finishes late and overwrites a sibling whose registration was already released gets a warning rather than a refusal, because by then the tool can no longer prove whose paths those were. Release late for that reason.

Lock directories contain JSON owner metadata. Unlazy deliberately does not auto-break an apparently stale lock, because deleting a live owner's path can let two successors enter at once. If a process dies while holding a lock, first verify that its recorded process is no longer running and that no unlazy operation could still own the lock, then remove only that specific abandoned lock manually. Never clear the whole lock directory while work is active. Approval-record locks use the same recovery rule.

Overlap detection is deliberately conservative. It may reject two globs that a full intersection engine could prove disjoint, especially globs with mid-segment wildcards. It must not clear an uncertain pair as safe. Treat over-conflict as a prompt to use simpler disjoint paths or sequential dispatch.

Examples:

| First declaration | Second declaration | Result |
|---|---|---|
| `src/api/**` | `src/web/**` | disjoint |
| `src/shared/**` | `src/shared/util.mjs` | conflict |
| `src/a*.mjs` | `src/ab*.mjs` | conflict because intersection is possible |
| `**` | `docs/**` | conflict |

Leases cover only declared paths and only participants that honor them. They are coordination records, not write isolation.

Release a leaf or whole scope after parent verification:

```text
node <skill-dir>/scripts/gate-check.mjs --scope api --release --leaf leaf-1.2.1
node <skill-dir>/scripts/gate-check.mjs --scope api --release
```

An unknown `--leaf` is an error. The checker never silently falls back to the first ledger.

## Concurrent ledger updates

The checker serializes each gate-file update and commits it atomically. Before applying a completed check, it re-reads the ledger and confirms that the gate id and oracle fields still match what ran. If the command, expectation, working directory, or other bound oracle field changed in flight, the stale result is discarded.

Preserve the ledger's original LF or CRLF style. Insert a missing evidence line without changing unrelated content. Keep result output deterministic in gate order even when `--jobs <N>` executes checks concurrently.

The status log is append-only:

```text
node <skill-dir>/scripts/gate-check.mjs --scope api --log "leaf-1.2.1 verified"
```

Append-only logging reduces lost updates; it does not replace the live state fields in `PLAN.md`.

## Session-keyed hook state

The Stop hook keys progress state to the resolved scope and current session. Concurrent hook calls serialize their state update. Completion or disappearance of the ledger clears obsolete state. One session cannot consume another session's six no-progress blocks.

The hook may be pinned with installer `--scope` or resolve a session binding written by:

```text
node <skill-dir>/scripts/gate-check.mjs --scope api --bind <session-id>
```

Do not treat a stored session id as a secret or identity proof.

## Choose the right isolation level

Leases coordinate cooperating writers. They do not isolate them. Pick the weakest level that covers what the concurrent leaves actually write.

| Level | Use when | Cost |
|---|---|---|
| One working tree, several scopes | Leaves read widely and write disjoint declared paths | none |
| One git worktree per writing leaf | Two or more concurrent leaves write tracked files, or worktree-local build output collides | one checkout and setup per leaf |
| Operating-system, container, or microVM isolation | A leaf or a `CHECK:` command is untrusted | image and orchestration overhead |

Escalate one level as soon as two concurrently dispatched leaves both write tracked files. A refused claim tells you the split is unsafe; a worktree is what makes a safe split runnable.

Worktree isolation is additive to scope and claim discipline, not a substitute for it. Isolation answers where a leaf's writes land. A lease answers a question the filesystem cannot see: whether two leaves were declared to own the same work. Separate checkouts make that duplicate-ownership mistake invisible rather than impossible, so claim every concurrent leaf even when each one has its own worktree.

## Isolate writing leaves with a worktree

Two writers in one checkout share one index. A leaf that stages broadly then commits its sibling's in-progress files under its own contract, and both ledgers record evidence for work neither leaf owns. Disjoint `OWNS:` paths do not prevent this, because the index is shared even when the declared paths are not.

Prefer the host's native worktree facility. Hosts expose one as a tool named like `EnterWorktree`, a `/worktree` command, an isolation option on the agent launch tool, or a worktree skill installed alongside this one. A native facility owns placement, branch creation, and teardown, so a manual `git worktree add` beside it leaves state the host cannot see or clean up.

Establish which repository the facility isolates before relying on it. A launch-tool isolation option is commonly scoped to the driver's own repository, which is the wrong tree whenever the driver and the dispatched work live in different repositories, and a tool schema rarely states the target. Where the target is not verifiable, use the git fallback below rather than guessing: isolating the wrong repository looks like success and leaves every leaf sharing one checkout.

Fall back to git only when the host offers nothing. Detect existing isolation before creating anything:

```text
git rev-parse --git-dir
git rev-parse --git-common-dir
git rev-parse --show-superproject-working-tree
```

Differing first two values mean the current directory is already a linked worktree or a submodule, and the third distinguishes them: a submodule prints a path, a worktree prints nothing. Do not nest a worktree inside a worktree.

Verify that the worktree parent directory is ignored rather than assuming it, then create one worktree per leaf, each on its own branch:

```text
git check-ignore -q .worktrees/
git worktree add .worktrees/<leaf-id> -b <leaf-id>
```

Record each dispatched leaf's worktree on its `start` with `--workspace <path>`, so the isolation is verified at dispatch time rather than assumed until an audit contradicts it; `references/dispatch.md` shows the form and what a false claim is refused for.

An unignored parent directory commits every worktree's contents back into the repository. Add it to the ignore rules before the first `add` if `check-ignore` exits nonzero. Keep the trailing slash on the path being tested. A directory-only ignore rule such as `.worktrees/` does not match the path `.worktrees` until that directory exists, so the unslashed form reports a confident and wrong "not ignored" in exactly the state this check runs in. Remove a worktree only after its leaf is verified:

```text
git worktree remove .worktrees/<leaf-id>
```

## What a worktree does and does not isolate

A linked worktree owns its working directory, `HEAD`, and index at `<git-common-dir>/worktrees/<name>/index`. Concurrent `git add` and `git commit` from separate worktrees on separate branches therefore do not contend for a single index lock.

A worktree shares the object database, the ref store, and `.git/config`. Shared configuration is the surface that collides first, and it fails quietly. One shared config holds one value per key, so per-leaf settings cannot coexist in it at all: the last writer wins, and every worktree then reads that one value, including worktrees that never wrote it and the main checkout. Simultaneous writes may additionally fail to take the config lock, but that error is intermittent. An agent waiting to see `could not lock config file` before changing approach will usually get silently wrong values instead of an error.

Give each leaf its own commit identity and any other per-leaf settings in worktree scope ([git-worktree](https://git-scm.com/docs/git-worktree)):

```text
git config extensions.worktreeConfig true
git config --worktree user.email <leaf-identity>
git config --worktree user.name <leaf-name>
```

Scoped writes do not contend and do not leak: each worktree resolves its own identity and the main checkout keeps the value it already had.

A worktree isolates nothing outside the repository. Package caches, build caches, temporary directories, listening ports, databases, and external services stay shared. Assign each leaf its own cache locations and ports in the contract, or make the leaves that need them sequential.

## Make the result durable before teardown

A worktree removed before its leaf commits destroys that leaf's work, and a leaf that returned without committing looks exactly like one that finished. Order the end of every dispatched writing leaf:

1. the leaf commits to its own branch inside its own worktree
2. the leaf records the commit id as ledger evidence
3. the parent records the dispatch return
4. the parent reverifies the leaf's runnable gates against that commit
5. only then release the lease and remove the worktree

Never release a lease or tear down a worktree on the strength of a scheduler return. A return records that the host finished the agent, not that the agent's work survived. Read [dispatch.md](dispatch.md) for the wave transitions this ordering constrains.

Parallelism changes wall-clock time, not the evidence standard. Parent re-verification and branch integration remain required.
