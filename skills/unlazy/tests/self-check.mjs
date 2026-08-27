#!/usr/bin/env node
// self-check.mjs : structural checks on the skill's own scripts.
//
//   node tests/self-check.mjs
//
// Lives in a file rather than as an inline `node -e` in GATES.md on purpose: a
// one-liner containing quotes, "^" or "!" is parsed differently by cmd.exe and
// by sh, so an inline check can pass on one platform and report a phantom
// failure on the other. A CHECK line should name a script, not embed one.
//
// Prints "self-check ok (N/N)" on success, which is what the gate matches.

import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPTS = [
  "scripts/gate-check.mjs",
  "scripts/gate-lint.mjs",
  "scripts/dispatch-check.mjs",
  "scripts/stop-hook.mjs",
  "scripts/install-hooks.mjs",
  "scripts/lib/gates.mjs",
  "scripts/lib/dispatch.mjs",
  "scripts/lib/check-supervisor.mjs",
  "scripts/lib/git.mjs",
  "scripts/lib/plan.mjs",
  "scripts/lib/process-tree.mjs",
  "scripts/lib/regex-worker.mjs",
  "tests/run-tests.mjs",
  "tests/dispatch-tests.mjs",
  "tests/hardening-tests.mjs",
  "tests/stress-tests.mjs",
  "tests/lint-tests.mjs",
  "tests/contract-tests.mjs",
  "tests/self-check.mjs",
];

const read = (p) => readFileSync(join(ROOT, p), "utf8");
const checks = [];
const check = (name, fn) => checks.push({ name, fn });

check("zero non-stdlib imports", () => {
  const bad = [];
  for (const p of SCRIPTS) {
    for (const m of read(p).matchAll(/^\s*import\s[^;]*?from\s+["']([^"']+)["']/gm)) {
      const spec = m[1];
      if (!spec.startsWith("node:") && !spec.startsWith(".")) bad.push(p + " -> " + spec);
    }
  }
  return bad.length ? "non-stdlib import: " + bad.join(", ") : null;
});

check("one shared gate parser", () => {
  // The v2.0 checker and hook each had their own GATE_RE and disagreed about a
  // gate's id when it carried no "Gn:" prefix. Parsing now lives only in the lib.
  // This file names GATE_RE to describe the rule, so it scans only scripts/.
  const owners = SCRIPTS.filter(p => p.startsWith("scripts/") && read(p).includes("GATE_RE"));
  return owners.length === 1 && owners[0] === "scripts/lib/gates.mjs"
    ? null
    : "GATE_RE should exist only in scripts/lib/gates.mjs, found in: " + owners.join(", ");
});

check("no index-arithmetic argument filtering", () => {
  // The v2.0 arg filter dropped index tIdx+1, which is 0 when --timeout is
  // absent, silently discarding the first file argument.
  return read("scripts/gate-check.mjs").includes("i !== tIdx + 1")
    ? "gate-check.mjs still filters arguments by index arithmetic"
    : null;
});

check("gate files are written atomically", () => {
  const src = read("scripts/gate-check.mjs");
  if (!src.includes("writeAtomic")) return "gate-check.mjs does not write via writeAtomic";
  if (!src.includes("withFileLock")) return "gate-check.mjs does not take a lock before writing";
  return null;
});

check("checks wait for close and cap output", () => {
  const src = read("scripts/gate-check.mjs");
  if (!src.includes('child.once("close"')) return "gate runner does not settle on stdio close";
  if (src.includes('child.once("exit"')) return "gate runner settles on exit before stdio close";
  if (!src.includes("MAX_OUTPUT_BYTES")) return "gate runner has no explicit output cap";
  return null;
});

check("approval identity binds execution semantics", () => {
  const src = read("scripts/gate-check.mjs");
  const required = [
    "check:", "expect:", "cwd", "shell", "timeoutMs", "maxOutputBytes",
    "regexTimeoutMs", "regexStartupTimeoutMs", "maxRegexWorkers", "platform", "path:",
  ];
  const missing = required.filter(token => !src.includes(token));
  return missing.length ? "approval oracle missing source tokens: " + missing.join(", ") : null;
});

check("the hook resolves a scope rather than globbing the tree", () => {
  const src = read("scripts/stop-hook.mjs");
  if (!src.includes("resolveTarget")) return "stop-hook.mjs does not resolve a scope";
  if (!src.includes("hookStatePath")) return "stop-hook.mjs does not use a per-scope state path";
  return null;
});

check("every local resource the skill names exists", () => {
  const skill = read("SKILL.md");
  const missing = [];
  const named = new Set();
  for (const m of skill.matchAll(/`((?:references|templates|scripts)\/[^`\s]+|SECURITY\.md)`/g)) named.add(m[1]);
  for (const path of named) {
    try { read(path); } catch { missing.push(path); }
  }
  return missing.length ? "SKILL.md names missing local resources: " + missing.join(", ") : null;
});

check("all executable sources retain the Node 16 floor", () => {
  const bad = SCRIPTS.filter(p => /Node 1[89]\+/.test(read(p)));
  return bad.length ? "newer runtime claim in: " + bad.join(", ") : null;
});

check("the PLAN template carries a revisioned contract denominator", () => {
  const plan = read("templates/PLAN.md");
  const required = [
    "Current contract inventory", "Contract revision", "Required outcome or constraint",
    "Owner", "Observing gate or manual review", "Disposition", "REMOVED_BY_USER",
  ];
  const missing = required.filter((token) => !plan.includes(token));
  return missing.length ? "PLAN contract inventory missing: " + missing.join(", ") : null;
});

check("request reconciliation keeps the focused solo cheap path", () => {
  const skill = read("SKILL.md");
  if (!skill.toLowerCase().includes("re-read the current request")) return "SKILL.md lacks final request reread";
  if (!skill.includes("a PLAN table is not required")) return "focused solo path was made needlessly orchestrated";
  if (!read("references/orchestration.md").includes("review every current contract row")) {
    return "orchestration guide lacks final contract reconciliation";
  }
  return null;
});

check("the headline examples say which layout they belong to", () => {
  // SKILL.md's --status/--approve examples name a root GATES.md, which does not
  // exist once Parallel mode's scoped layout is in use. It was the first
  // command the docs handed a parallel-mode reader, and it failed immediately.
  const skill = read("SKILL.md");
  if (!skill.includes("--status GATES.md")) return null;
  return /solo ledger at the repository root/.test(skill)
    ? null
    : "SKILL.md shows a root-ledger example without saying a scoped pipeline needs --scope";
});

check("participation in the ownership protocol is enforced, not asked for", () => {
  // "Claim every leaf first" was a flat imperative with no consequence, so
  // skipping it was strictly cheaper than doing it: fewer calls, and it could
  // never refuse. The check must read lease records rather than a ledger, or a
  // leaf could omit its own OWNS line to escape the requirement.
  const dispatch = read("scripts/dispatch-check.mjs");
  if (!dispatch.includes("readLeases")) return "dispatch-check.mjs cannot see registrations";
  if (!/not registered in scope/.test(dispatch)) return "dispatch-check.mjs does not refuse an unregistered leaf";
  if (!dispatch.includes("validateScopeId")) {
    return "dispatch-check.mjs reads leases before validating ids, so a traversal id reports a missing registration";
  }
  if (!read("references/dispatch.md").includes("This is enforced, not advisory")) {
    return "dispatch.md still presents claiming as an unenforced instruction";
  }
  return null;
});

check("dependency checking cannot be switched off by naming a ledger", () => {
  // A ledger named for what it builds matches no row, and the leaf's Needs were
  // then never looked up: identical work refused as leaf-1.1.2 and opened as
  // leaf-cli. An enforcement that any ordinary naming choice silently disables
  // is worse than none, because the pipeline still reads as protected.
  const dispatch = read("scripts/dispatch-check.mjs");
  if (!/not described by the plan/.test(dispatch)) {
    return "dispatch-check.mjs still passes a leaf with no plan row through in silence";
  }
  // Refusing an unmatched leaf without stating how ids match turns a silent
  // no-op into a hard stop whose rule is only discoverable by reading plan.mjs,
  // which is where a tester actually had to go to find it.
  if (!/digits and dots only/.test(read("templates/PLAN.md"))) {
    return "the PLAN template does not state the id rule that decides the match";
  }
  return null;
});

check("a met gate is re-approved through reverification, and the docs say so", () => {
  // A met gate is not re-executed by a plain run, so `--approve` never reaches
  // its oracle: it approves nothing and still prints ALL MET. Naming the bare
  // flag as the remedy sent the reader around a loop whose middle step reported
  // success. Every place that tells a reader how to re-approve must name the
  // form that works.
  const checker = read("scripts/gate-check.mjs");
  if (!/opt\.reverify \? "--reverify --approve"/.test(checker)) {
    return "the NOT RUN hint does not name --reverify --approve when reverifying";
  }
  if (!checker.includes("APPROVAL PENDING")) {
    return "an approving run still drops a met gate's outstanding approval in silence";
  }
  for (const doc of ["SKILL.md", "SECURITY.md", "references/dispatch.md"]) {
    if (!read(doc).includes("--reverify --approve")) return doc + " does not name --reverify --approve";
  }
  return null;
});

check("the met count is not reduced by findings that were never gates", () => {
  // The summary reached the met count by subtracting the size of the map that
  // collects extra unmet items. Ownership and plan findings are entries in that
  // map and were never gates, so each one silently took a passing gate off the
  // count the skill tells the agent to publish.
  const checker = read("scripts/gate-check.mjs");
  if (/totalMet - extraUnmet\.size/.test(checker)) {
    return "the met count still subtracts every extra unmet entry, including non-gate findings";
  }
  if (!/finalStates\.get\(key\) === "met"/.test(checker)) {
    return "nothing restricts the met-count subtraction to gates that were counted as met";
  }
  return null;
});

check("no sequence of edits can wedge a blocked session", () => {
  // The consecutive counter is keyed to the resolved state, so it rearms on any
  // change to it, including one that accomplishes nothing. Renaming an unmet
  // gate each round blocked eight times out of eight under test and never
  // released. A guard whose only release condition can be rearmed by the thing
  // it is guarding against does not bound anything.
  const hook = read("scripts/stop-hook.mjs");
  if (!/MAX_SESSION_BLOCKS/.test(hook)) return "the hook has no ceiling that survives a change of resolved state";
  if (!/sessionState\.total > MAX_SESSION_BLOCKS/.test(hook)) return "the lifetime ceiling is never tested against";
  if (!/current\.total = total/.test(hook)) return "the lifetime count is reset along with the consecutive one";
  if (!/twenty-four blocks per session/.test(read("SKILL.md"))) return "SKILL.md still promises only the consecutive release";
  return null;
});

check("the branch template and the dispatch guide agree about worktree children", () => {
  // The template shipped one shared --cwd naming every child, which is the wrong
  // tree for all of them once each child worked in its own worktree, while
  // dispatch.md said to reverify each against the worktree it worked in. A
  // reader following the template got a finding about work none of the children
  // did, and two shipped documents pointed opposite ways.
  const node = read("templates/gates-node.md");
  if (!/\.worktrees\/leaf-/.test(node)) return "gates-node.md offers no per-worktree form of the integration gate";
  if (!/wrong tree/.test(node)) return "gates-node.md does not say when its shared-checkout form stops being correct";
  return null;
});

check("an isolation claim at start is verified, not recorded", () => {
  // A leaf whose isolation silently fell through committed straight to the
  // shared checkout, caught only by the driver auditing git afterwards. A
  // recorded-but-unchecked workspace would be the same false paperwork with a
  // field name, so the claim is proven at the cheapest moment or refused.
  const dispatch = read("scripts/dispatch-check.mjs");
  if (!dispatch.includes("--workspace")) return "start accepts no isolation claim to verify";
  if (!/exclusiveWorktree/.test(dispatch)) return "the workspace claim is recorded without being proven";
  if (!/different repository/.test(dispatch)) return "a worktree of the wrong repository is not distinguished from isolation";
  if (!/--workspace <path>/.test(read("references/dispatch.md"))) return "dispatch.md does not show the workspace form";
  return null;
});

check("the synchronous adapter describes a sequence the tool accepts", () => {
  // The first attempt at this told the reader to seal before launching, which
  // `seal` refuses outright because it requires a start per leaf. A tester ran
  // it verbatim and got `cannot seal ready-1: missing starts`. Guidance that the
  // tool rejects is worse than none: it spends the reader's trust before they
  // reach anything true.
  const dispatch = read("references/dispatch.md");
  if (/seal the wave before that message/.test(dispatch)) {
    return "dispatch.md still tells a synchronous host to seal before launching, which seal refuses";
  }
  if (!/missing starts/.test(dispatch)) return "dispatch.md does not warn that seal requires a start per leaf";
  return null;
});

check("the ownership verdict reports what the audit could not establish", () => {
  // The warning went to stderr above a verdict that read ALL MET and exited 0,
  // so a reader who checked the verdict, the exit status, or asked again later
  // never saw it. It is the only signal separating a reverification that proved
  // provenance from one that gave up on it.
  const checker = read("scripts/gate-check.mjs");
  if (!/ownershipNote/.test(checker)) return "the verdict line carries no ownership warning count";
  if (!/ownership: clean for/.test(checker)) return "a clean audit is still indistinguishable from one that never ran";
  return null;
});

check("the dispatch adapter covers a host with no background launch", () => {
  // Three testers across three rounds met a synchronous-only Agent, where
  // launching and waiting are one act and no handle exists to record before
  // sealing. Describing only the nonblocking case left each of them to invent a
  // different workaround, and one to record a handle it had made up.
  const dispatch = read("references/dispatch.md");
  if (!/synchronous/.test(dispatch)) return "dispatch.md does not address a synchronous-only launch surface";
  if (!/Do not fabricate a handle/.test(dispatch)) return "dispatch.md does not rule out inventing a handle to fit the shape";
  return null;
});

check("the ownership audit measures before its own checks can change the tree", () => {
  // Pin the ordering rather than the text, because the ordering is the
  // invariant: a check that writes while it verifies puts its own output into
  // the leaf's change set, and a leaf that had committed one correct file was
  // refused for bytecode its own approved oracle generated. Verifying something
  // must not change what is being measured about it.
  const checker = read("scripts/gate-check.mjs");
  const snapshot = checker.indexOf("const ownershipSnapshot");
  const execute = checker.indexOf("await runRolling(runnable, jobs)");
  if (snapshot === -1) return "the audit still measures the tree after its own checks have run";
  if (execute === -1) return "the check execution site moved; re-establish that the snapshot precedes it";
  if (snapshot > execute) return "the change set is captured after the oracles run, so their output is charged to the leaf";
  // Capturing it early is worth nothing if the audit then measures again. A
  // differential control caught this pin passing while the lookup had been
  // reverted, so the read is asserted too, not just the capture.
  if (!/ownershipSnapshot\.get\(/.test(checker)) return "the audit does not read the snapshot it captured";
  if (/const changes = changedSince\(/.test(checker)) return "the audit re-measures the tree instead of reading the snapshot";
  return null;
});

check("an accusation of collision states the evidence it rests on", () => {
  // The comparison ends at the working tree's HEAD, so it describes one leaf
  // only where one leaf advances that HEAD. Refusing without establishing this
  // accused correctly registered, finished work of a collision the evidence
  // could not show. Behaviour changed, so every document that stated the old
  // flat rule has to state the new one, or the skill ships a contract it does
  // not keep.
  if (!/exclusiveWorktree/.test(read("scripts/gate-check.mjs"))) {
    return "the audit still accuses without establishing that the range describes one leaf";
  }
  if (!/exclusiveWorktree/.test(read("scripts/lib/git.mjs"))) return "git.mjs cannot answer whether a tree is exclusive";
  for (const doc of ["SKILL.md", "references/parallel.md", "references/dispatch.md"]) {
    // Match the claim, not one phrasing of it: these three sentences are worded
    // differently on purpose, and pinning a literal would fail the next honest
    // rewrite rather than the next real drift.
    if (!/(shar\w*[^.]{0,60}working tree)|(working tree[^.]{0,60}shar\w*)/i.test(read(doc))) {
      return doc + " still states the flat rule that any path registered to another leaf leaves it unverified";
    }
  }
  return null;
});

check("concurrent dispatch states that it needs a repository", () => {
  // The audit is the only evidence a leaf did not author, so without history
  // every registered leaf stays unverified for a reason no document named. The
  // way out has to be written down, or the reader reads a permanent refusal as
  // a fault in the work.
  const parallel = read("references/parallel.md");
  if (!/needs a git repository/.test(parallel)) {
    return "parallel.md does not say concurrent dispatch requires a repository";
  }
  if (!/Initialise a repository/.test(parallel)) return "parallel.md names no route for a project without one";
  return null;
});

check("a plan that cannot be read is unfinished planning, not a dispatch nuisance", () => {
  // Parsability belongs to the planning stage. Tolerating an unreadable tree at
  // dispatch would make a messy plan the cheapest way to switch dependency
  // checking off, and blocking only there would leave a pipeline able to report
  // completion on a plan nobody can read.
  const lint = read("scripts/gate-lint.mjs");
  const dispatch = read("scripts/dispatch-check.mjs");
  const checker = read("scripts/gate-check.mjs");
  if (!lint.includes("readPlan")) return "gate-lint.mjs does not lint a PLAN, so a broken tree is caught too late";
  if (!dispatch.includes("planning is not complete")) return "dispatch-check.mjs does not refuse an unreadable plan";
  if (!checker.includes("UNMET plan ")) return "an unreadable plan can still reduce to ALL MET";
  return null;
});

check("declared dependencies are enforced against the child's own ledger", () => {
  // The plan's State field is authored by the agent that wants to proceed, so
  // on its own it is the paperwork problem again. Completion is read from the
  // dependency's ledger and the two are compared, so a drifted plan is caught
  // rather than believed.
  const dispatch = read("scripts/dispatch-check.mjs");
  if (!dispatch.includes("ledgerCompletion")) return "dispatch-check.mjs trusts the plan's own State field";
  if (!/unsatisfied dependencies/.test(dispatch)) return "dispatch-check.mjs does not refuse an unsatisfied dependency";
  if (!/still calls it/.test(dispatch)) return "a plan that disagrees with its ledger is not reported";
  if (!read("references/orchestration.md").includes("enforced, not descriptive")) {
    return "orchestration.md still presents the leaf states as description rather than enforcement";
  }
  return null;
});

check("ownership is audited against history, not against a second claim", () => {
  // A declaration has never restricted a write, so two declarations agreeing is
  // not evidence two leaves stayed apart. History is the only record here the
  // checked leaf did not author, and an audit that could not run must not read
  // as one that passed.
  const checker = read("scripts/gate-check.mjs");
  const gates = read("scripts/lib/gates.mjs");
  if (!checker.includes("auditOwnership")) return "gate-check.mjs does not audit ownership";
  if (!checker.includes("changedSince")) return "the ownership audit does not read version control";
  if (!/no starting commit was pinned|audit could not run/.test(checker)) {
    return "an unauditable leaf can pass silently instead of becoming a handoff";
  }
  if (!gates.includes("pathMatchesGlob")) return "no exact path matcher; globsOverlap would misjudge real paths";
  if (!read("references/parallel.md").includes("Audit what happened, not what was claimed")) {
    return "parallel.md does not document the ownership audit";
  }
  return null;
});

check("concurrent writing leaves are isolated by a worktree", () => {
  // Disjoint OWNS: paths are a coordination record. Two leaves writing tracked
  // files in one checkout still share one index, so a leaf that stages broadly
  // commits its sibling's work under its own contract.
  const skill = read("SKILL.md");
  const parallel = read("references/parallel.md");
  if (!/own worktree/.test(skill)) return "SKILL.md does not require a worktree for concurrently writing leaves";
  if (!parallel.includes("share one index")) return "parallel.md does not say why disjoint ownership is insufficient";
  if (!parallel.includes("--worktree")) return "parallel.md does not route per-leaf git config to worktree scope";
  if (!parallel.includes("additive to scope and claim discipline")) {
    return "parallel.md lets worktree isolation read as a replacement for claim discipline";
  }
  // The fallback must be runnable as written. Naming "git worktree add" in
  // passing left a reader to supply the branch flag and placement themselves.
  if (!/git worktree add \S+ -b /.test(parallel)) {
    return "parallel.md names the git fallback without a runnable create command";
  }
  // A directory-only ignore rule does not match the unslashed path until the
  // directory exists, so `check-ignore -q .worktrees` reports a wrong "not
  // ignored" in exactly the pre-creation state this check runs in.
  if (/check-ignore[^\n]*\s\.?[\w.-]*worktrees(?!\/)/.test(parallel)) {
    return "parallel.md tests an ignore rule without the trailing slash";
  }
  // A launch-tool isolation option is often scoped to the driver's repository.
  // Isolating the wrong tree looks like success and leaves the leaves sharing
  // one checkout, so the native-first rule needs its verification caveat.
  if (!parallel.includes("which repository the facility isolates")) {
    return "parallel.md prefers a native worktree facility without saying to confirm its target repository";
  }
  // The lock error is intermittent; the certain failure is that one shared
  // config holds one slot per key, so an agent waiting for a loud error gets
  // silently wrong values in every worktree instead.
  if (!/last writer wins/.test(parallel)) {
    return "parallel.md describes shared-config collision as loud without its silent last-writer-wins outcome";
  }
  const surfaces = ["extensions.worktreeConfig", "caches", "ports"];
  const missing = surfaces.filter((token) => !parallel.includes(token));
  return missing.length ? "parallel.md omits shared surfaces a worktree leaves exposed: " + missing.join(", ") : null;
});

check("workspace teardown waits for reverification", () => {
  // A scheduler return says the host finished the agent, not that the agent's
  // work survived. Removing the workspace at return time destroys any leaf that
  // returned without committing, and the wave records both cases identically.
  const dispatch = read("references/dispatch.md");
  const parallel = read("references/parallel.md");
  if (!dispatch.includes("Durability before teardown")) return "dispatch.md has no durability rule for returned leaves";
  if (!/return[\s\S]{0,120}reverify[\s\S]{0,120}release[\s\S]{0,120}remove/i.test(dispatch)) {
    return "dispatch.md does not order return, reverify, release, and workspace removal";
  }
  if (!parallel.includes("Never release a lease or tear down a worktree")) {
    return "parallel.md does not forbid teardown on a scheduler return";
  }
  // Reverifying a leaf that ran in a worktree needs an explicit --cwd, or the
  // check resolves against the driver's ledger directory instead.
  if (!/--reverify[^\n]*--cwd|--cwd[^\n]*--reverify/.test(dispatch)) {
    return "dispatch.md does not show a worktree-aware reverify invocation";
  }
  if (!dispatch.includes("NOT RUN")) {
    return "dispatch.md does not warn that an unapproved oracle measures nothing before teardown";
  }
  return null;
});

check("abandonment is terminal handoff rather than ALL MET", () => {
  const checker = read("scripts/gate-check.mjs");
  const hook = read("scripts/stop-hook.mjs");
  if (!checker.includes("HANDOFF REQUIRED") || !checker.includes("totalAbandoned === 0")) {
    return "gate checker can still classify abandonment as completion";
  }
  if (!hook.includes("HANDOFF REQUIRED") || !hook.includes("handoffs")) {
    return "Stop hook does not surface bounded abandonment handoff";
  }
  return null;
});

let passed = 0;
const failures = [];
for (const c of checks) {
  let problem;
  try { problem = c.fn(); } catch (e) { problem = e.message; }
  if (problem) { failures.push(c.name + ": " + problem); console.log("FAIL " + c.name + "\n     " + problem); }
  else { passed++; console.log("ok   " + c.name); }
}

console.log("");
if (failures.length) {
  console.log("self-check FAILED (" + passed + "/" + checks.length + ")");
  process.exit(1);
}
console.log("self-check ok (" + passed + "/" + checks.length + ")");
