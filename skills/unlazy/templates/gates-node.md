# Gates: <branch name> integration

Scope: integrate children <explicit child ids> into one verified result

- [ ] N1: every named child leaf is reverified from its exact ledger
  CHECK: node <skill-dir>/scripts/gate-check.mjs --root . --cwd . --reverify --jobs 1 .unlazy/<scope>/gates/leaf-<a>.md .unlazy/<scope>/gates/leaf-<b>.md
  EXPECT: ALL MET
  EVIDENCE: pending

<!--
N1 above is the shared-checkout form: one `--cwd`, every child in one call. It
is correct only while the children worked in the tree it names. Where each child
ran in its own worktree, that single `--cwd` is the wrong tree for all of them,
and each child must be reverified against the worktree it worked in, which
cannot be expressed as one invocation. Replace N1 with one gate per child:

- [ ] N1a: child <a> is reverified against its own worktree
  CHECK: node <skill-dir>/scripts/gate-check.mjs --reverify .unlazy/<scope>/gates/leaf-<a>.md --root <repo> --cwd <repo>/.worktrees/leaf-<a>
  EXPECT: ALL MET
  EVIDENCE: pending

Reverifying worktree children through the shared form measures the driver's
checkout instead of theirs, and reports on work none of them did.

Either form's nested reverify runs without --approve and cannot self-escalate,
so each child ledger must already hold an approval at the exact --cwd the gate
names. After worktree children merge, that is a new working directory: run
--reverify --approve on each child at that --cwd as a reviewed driver action
first, or this gate fails with the child's own APPROVAL REQUIRED as its output.
-->


- [ ] N2: child interfaces match the contract in PLAN.md
  CHECK: node scripts/verify-interfaces.mjs
  EXPECT: interface verification passed
  EVIDENCE: pending

- [ ] N3: cross-child behavior works end to end
  CHECK: node scripts/verify-integration.mjs
  EXPECT: integration verification passed
  EVIDENCE: pending

- [ ] N4: affected sibling behavior has not regressed
  CHECK: node scripts/verify-regressions.mjs
  EXPECT: regression verification passed
  EVIDENCE: pending

- [ ] N5: every direct child ownership lease was released after parent verification
  EVIDENCE: pending

- [ ] N6: consequential manual outcomes from the children were reviewed at branch level
  EVIDENCE: pending

<!--
Replace every placeholder before running the checker.

N1 must name every direct child explicitly and use --reverify, not --status.
Status reports old evidence without executing it. Keep --jobs 1 unless the child
checks are independent and deterministic parallel execution is intentional.
If a child reports an abandonment, N1 exits 1 with `HANDOFF REQUIRED`. Mark the
branch ABANDONED and surface the handoff; never rewrite that result as completion.

Branch paths use node-<id>.md. Leaf paths use leaf-<id>.md. Branch completion
requires integration evidence; a set of locally complete leaves is not enough.

For N5, run this once for each direct child after verification and record the
outputs as manual evidence:

node <skill-dir>/scripts/gate-check.mjs --scope <scope> --leaf leaf-<id> --release

Drop N5 only when no child claimed ownership. See references/orchestration.md
and references/parallel.md.
-->
