#!/usr/bin/env node
// run-tests.mjs : behavioural tests for the unlazy enforcement scripts.
// Zero dependencies, cross-platform (every CHECK command is a `node -e`).
//
//   node tests/run-tests.mjs            run all
//   node tests/run-tests.mjs scope      run tests whose name contains "scope"
//
// Prints "N/N passed" on success, which is the string CI and the repo's own
// gates match on.

import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { execFile, spawnSync } from "node:child_process";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const GATE_CHECK = join(HERE, "..", "scripts", "gate-check.mjs");
const STOP_HOOK = join(HERE, "..", "scripts", "stop-hook.mjs");
const INSTALL = join(HERE, "..", "scripts", "install-hooks.mjs");
const filter = process.argv[2] || "";
const APPROVAL_ROOT = mkdtempSync(join(tmpdir(), "unlazy-test-approvals-"));

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

// ------------------------------------------------------------- helpers

function sandbox() {
  const dir = mkdtempSync(join(tmpdir(), "unlazy-test-"));
  return {
    dir,
    write(rel, text) {
      const p = join(dir, rel);
      mkdirSync(dirname(p), { recursive: true });
      writeFileSync(p, text);
      return p;
    },
    read(rel) { return readFileSync(join(dir, rel), "utf8"); },
    cleanup() { try { rmSync(dir, { recursive: true, force: true }); } catch { /* windows lag */ } },
  };
}

function run(script, args, opts = {}) {
  return new Promise((res) => {
    const actions = new Set(["--status", "--claim", "--release", "--list-scopes", "--log", "--bind", "--help", "-h"]);
    const needsApproval = script === GATE_CHECK && !opts.noApprove && !args.some((arg) => actions.has(arg));
    const actualArgs = needsApproval && !args.includes("--approve") ? ["--approve", ...args] : args;
    const child = execFile(process.execPath, [script, ...actualArgs], {
      cwd: opts.cwd, encoding: "utf8", maxBuffer: 8 * 1024 * 1024,
      env: { ...process.env, UNLAZY_APPROVAL_DIR: APPROVAL_ROOT, ...(opts.env || {}) },
    }, (err, stdout, stderr) => {
      res({ code: err ? (err.code ?? 1) : 0, out: (stdout || "") + (stderr || "") });
    });
    if (opts.stdin !== undefined) { child.stdin.end(opts.stdin); }
  });
}

const gate = (id, title, check, expect) =>
  "- [ ] " + id + ": " + title + "\n" +
  (check ? "  CHECK: " + check + "\n" : "") +
  (expect ? "  EXPECT: " + expect + "\n" : "") +
  "  EVIDENCE: pending\n";

const nodeEval = (js) => 'node -e "' + js.replace(/"/g, '\\"') + '"';
const echoOk = (word) => nodeEval("console.log('" + word + "')");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
function assertHas(hay, needle, label) {
  assert(hay.includes(needle), (label || "output") + " missing " + JSON.stringify(needle) + "\n--- got ---\n" + hay);
}
function assertLacks(hay, needle, label) {
  assert(!hay.includes(needle), (label || "output") + " unexpectedly contains " + JSON.stringify(needle) + "\n--- got ---\n" + hay);
}

// --------------------------------------------------------------- tests

test("args: two explicit files are both processed without --timeout", async () => {
  const s = sandbox();
  try {
    s.write("gates/leaf-a.md", "# Gates: A\n\n" + gate("G1", "A", echoOk("A-OK"), "A-OK"));
    s.write("gates/leaf-b.md", "# Gates: B\n\n" + gate("G1", "B", echoOk("B-OK"), "B-OK"));
    const r = await run(GATE_CHECK, ["gates/leaf-a.md", "gates/leaf-b.md"], { cwd: s.dir });
    assertHas(r.out, "PASS leaf-a:G1");
    assertHas(r.out, "PASS leaf-b:G1");
    assertHas(r.out, "ALL MET (2 met)");
    assertHas(s.read("gates/leaf-a.md"), "- [x] G1", "leaf-a");
    assert(r.code === 0, "expected exit 0, got " + r.code);
  } finally { s.cleanup(); }
});

test("args: one explicit file never widens to the rest of the tree", async () => {
  const s = sandbox();
  try {
    s.write("gates/leaf-a.md", "# Gates: A\n\n" + gate("G1", "A", echoOk("A-OK"), "A-OK"));
    s.write("gates/leaf-b.md", "# Gates: B\n\n" + gate("G1", "B", echoOk("B-OK"), "B-OK"));
    const r = await run(GATE_CHECK, ["gates/leaf-a.md"], { cwd: s.dir });
    assertHas(r.out, "PASS leaf-a:G1");
    assertLacks(r.out, "leaf-b", "run output");
    assertHas(s.read("gates/leaf-b.md"), "- [ ] G1", "leaf-b must be untouched");
    assertHas(s.read("gates/leaf-b.md"), "EVIDENCE: pending", "leaf-b evidence must be untouched");
  } finally { s.cleanup(); }
});

test("args: unknown option is rejected instead of treated as a file", async () => {
  const s = sandbox();
  try {
    s.write("GATES.md", "# Gates\n\n" + gate("G1", "x", echoOk("OK"), "OK"));
    const r = await run(GATE_CHECK, ["--nope"], { cwd: s.dir });
    assert(r.code === 2, "expected exit 2, got " + r.code);
    assertHas(r.out, "unknown option --nope");
  } finally { s.cleanup(); }
});

test("scope: running one pipeline leaves the other pipeline alone", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates: api1\n\n" + gate("G1", "api", echoOk("API-OK"), "API-OK"));
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates: web1\n\n" + gate("G1", "web", echoOk("WEB-OK"), "WEB-OK"));
    const r = await run(GATE_CHECK, ["--scope", "api"], { cwd: s.dir });
    assertHas(r.out, "PASS leaf-1:G1");
    assertHas(r.out, "[scope api]");
    assertHas(s.read(".unlazy/api/gates/leaf-1.md"), "- [x] G1", "api gates");
    assertHas(s.read(".unlazy/web/gates/leaf-1.md"), "- [ ] G1", "web gates must be untouched");
  } finally { s.cleanup(); }
});

test("scope: two pipelines and no scope is refused, not guessed", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates: api\n\n" + gate("G1", "api", echoOk("API-OK"), "API-OK"));
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates: web\n\n" + gate("G1", "web", echoOk("WEB-OK"), "WEB-OK"));
    const r = await run(GATE_CHECK, [], { cwd: s.dir });
    assert(r.code === 2, "expected exit 2, got " + r.code);
    assertHas(r.out, "2 pipelines present");
    assertHas(r.out, "api");
    assertHas(r.out, "web");
    assertHas(s.read(".unlazy/api/gates/leaf-1.md"), "- [ ] G1", "api must be untouched");
    assertHas(s.read(".unlazy/web/gates/leaf-1.md"), "- [ ] G1", "web must be untouched");
  } finally { s.cleanup(); }
});

test("scope: UNLAZY_SCOPE selects the pipeline", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates: api\n\n" + gate("G1", "api", echoOk("API-OK"), "API-OK"));
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates: web\n\n" + gate("G1", "web", echoOk("WEB-OK"), "WEB-OK"));
    const r = await run(GATE_CHECK, [], { cwd: s.dir, env: { UNLAZY_SCOPE: "web" } });
    assertHas(r.out, "[scope web]");
    assertHas(s.read(".unlazy/web/gates/leaf-1.md"), "- [x] G1", "web gates");
    assertHas(s.read(".unlazy/api/gates/leaf-1.md"), "- [ ] G1", "api must be untouched");
  } finally { s.cleanup(); }
});

test("scope: a single pipeline needs no flag", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/solo/GATES.md", "# Gates: solo\n\n" + gate("G1", "solo", echoOk("S-OK"), "S-OK"));
    const r = await run(GATE_CHECK, [], { cwd: s.dir });
    assertHas(r.out, "ALL MET (1 met) [scope solo]");
  } finally { s.cleanup(); }
});

test("scope: legacy GATES.md layout still works", async () => {
  const s = sandbox();
  try {
    s.write("GATES.md", "# Gates\n\n" + gate("G1", "legacy", echoOk("L-OK"), "L-OK"));
    const r = await run(GATE_CHECK, [], { cwd: s.dir });
    assertHas(r.out, "PASS GATES:G1");
    assertHas(r.out, "ALL MET (1 met)");
  } finally { s.cleanup(); }
});

test("state: checked box with pending evidence counts as unmet", async () => {
  const s = sandbox();
  try {
    s.write("GATES.md", "# Gates\n\n- [x] G1: claimed\n  EVIDENCE: pending\n");
    const r = await run(GATE_CHECK, ["--status"], { cwd: s.dir });
    assert(r.code === 1, "expected exit 1, got " + r.code);
    assertHas(r.out, "UNMET GATES:G1 (checked but EVIDENCE pending)");
  } finally { s.cleanup(); }
});

test("state: ABANDON is a non-success handoff, not completion", async () => {
  const s = sandbox();
  try {
    s.write("GATES.md", "# Gates\n\n- [ ] G1: impossible\n  EVIDENCE: pending\n\nABANDON: G1 upstream API removed\n");
    const r = await run(GATE_CHECK, ["--status"], { cwd: s.dir });
    assert(r.code === 1, "expected exit 1, got " + r.code);
    assertHas(r.out, "HANDOFF REQUIRED: 1 abandoned");
    assertLacks(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("state: abandoned and mixed ledgers stay handoffs in every run mode", async () => {
  for (const args of [["--status"], [], ["--reverify"]]) {
    const s = sandbox();
    try {
      s.write("GATES.md", [
        "# Gates",
        "",
        "- [x] G1: measured outcome",
        "  EVIDENCE: checked by test",
        "",
        "- [ ] G2: impossible outcome",
        "  EVIDENCE: pending",
        "",
        "ABANDON: G2 upstream API removed",
        "",
      ].join("\n"));
      const r = await run(GATE_CHECK, args, { cwd: s.dir });
      assert(r.code === 1, "expected exit 1 for " + JSON.stringify(args) + ", got " + r.code + "\n" + r.out);
      assertHas(r.out, "HANDOFF REQUIRED: 1 abandoned");
      assertLacks(r.out, "ALL MET");
    } finally { s.cleanup(); }
  }
});

test("state: multi-file verification cannot hide one abandoned child", async () => {
  const s = sandbox();
  try {
    s.write("met.md", "# Gates\n\n- [x] G1: complete\n  EVIDENCE: checked by test\n");
    s.write("abandoned.md", "# Gates\n\n- [ ] G1: impossible\n  EVIDENCE: pending\n\nABANDON: G1 upstream removed\n");
    const r = await run(GATE_CHECK, ["--status", "met.md", "abandoned.md"], { cwd: s.dir });
    assert(r.code === 1, "expected exit 1, got " + r.code + "\n" + r.out);
    assertHas(r.out, "HANDOFF REQUIRED: 1 abandoned");
    assertHas(r.out, "abandoned:G1");
    assertLacks(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("hierarchy: an abandoned child cannot promote its N1 parent", async () => {
  const s = sandbox();
  try {
    const child = s.write("child.md", "# Gates\n\n- [ ] G1: impossible\n  EVIDENCE: pending\n\nABANDON: G1 upstream removed\n");
    s.write("parent-oracle.mjs", [
      "import { spawnSync } from 'node:child_process';",
      "const result = spawnSync(process.execPath, [" + JSON.stringify(GATE_CHECK) + ", '--reverify', " + JSON.stringify(child) + "], { encoding: 'utf8', env: process.env });",
      "process.stdout.write((result.stdout || '') + (result.stderr || ''));",
      "process.exit(result.status === 0 ? 0 : 1);",
      "",
    ].join("\n"));
    s.write("parent.md", "# Gates: parent\n\n" + gate("N1", "child is complete", "node parent-oracle.mjs", "ALL MET"));
    const r = await run(GATE_CHECK, ["parent.md"], { cwd: s.dir });
    assert(r.code === 1, "parent unexpectedly passed\n" + r.out);
    assertHas(r.out, "FAIL parent:N1");
    assertHas(r.out, "HANDOFF REQUIRED");
    assertLacks(r.out, "PASS parent:N1");
    assertHas(s.read("parent.md"), "- [ ] N1");
  } finally { s.cleanup(); }
});

test("state: unmet gates are reported with file-qualified ids", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates: 1\n\n" + gate("G1", "a", null, null));
    s.write(".unlazy/api/gates/leaf-2.md", "# Gates: 2\n\n" + gate("G1", "b", null, null));
    const r = await run(GATE_CHECK, ["--scope", "api", "--status"], { cwd: s.dir });
    assertHas(r.out, "leaf-1:G1");
    assertHas(r.out, "leaf-2:G1");
  } finally { s.cleanup(); }
});

test("run: a concurrent edit to another gate in the same file survives", async () => {
  const s = sandbox();
  try {
    // G1 is slow and will be flipped by gate-check; G2 is manual and gets its
    // evidence filled in by hand while that check is still running.
    s.write("GATES.md",
      "# Gates\n\n" +
      "- [ ] G1: slow\n  CHECK: " + nodeEval("setTimeout(()=>console.log('SLOW-OK'),2500)") +
      "\n  EXPECT: SLOW-OK\n  EVIDENCE: pending\n\n" +
      "- [ ] G2: manual\n  EVIDENCE: pending\n");
    const running = run(GATE_CHECK, [], { cwd: s.dir });
    await new Promise(r => setTimeout(r, 800));
    const text = s.read("GATES.md")
      .replace("- [ ] G2: manual\n  EVIDENCE: pending", "- [x] G2: manual\n  EVIDENCE: measured 47 rows, threadAnalysis.ts:88");
    s.write("GATES.md", text);
    await running;
    const after = s.read("GATES.md");
    assertHas(after, "- [x] G1", "G1 should be flipped by the checker");
    assertHas(after, "measured 47 rows", "the hand edit to G2 must not be clobbered");
    assertLacks(after, "G2: manual\n  EVIDENCE: pending", "G2 must not be reverted");
  } finally { s.cleanup(); }
});

test("checks: CWD runs a check in the directory it names", async () => {
  const s = sandbox();
  try {
    s.write("sub/marker.txt", "here\n");
    s.write("GATES.md", "# Gates\n\n" +
      "- [ ] G1: in sub\n  CHECK: " + nodeEval("console.log(require('fs').readFileSync('marker.txt','utf8'))") +
      "\n  EXPECT: here\n  CWD: sub\n  EVIDENCE: pending\n");
    const r = await run(GATE_CHECK, [], { cwd: s.dir });
    assertHas(r.out, "ALL MET (1 met)");
  } finally { s.cleanup(); }
});

test("leases: overlapping OWNS across pipelines is refused", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "OWNS: src/shared/**\n\n# Gates\n\n" + gate("G1", "a", null, null));
    s.write(".unlazy/web/gates/leaf-1.md", "OWNS: src/shared/util.ts\n\n# Gates\n\n" + gate("G1", "b", null, null));
    const a = await run(GATE_CHECK, ["--scope", "api", "--leaf", "leaf-1", "--claim"], { cwd: s.dir });
    assertHas(a.out, "CLAIMED 1 path(s) for api/leaf-1");
    const b = await run(GATE_CHECK, ["--scope", "web", "--leaf", "leaf-1", "--claim"], { cwd: s.dir });
    assert(b.code === 3, "expected exit 3 on conflict, got " + b.code);
    assertHas(b.out, "CONFLICT src/shared/util.ts overlaps src/shared/** held by api/leaf-1");
    assertHas(b.out, "CLAIM REFUSED");
  } finally { s.cleanup(); }
});

test("leases: disjoint OWNS both succeed, and release frees them", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "OWNS: src/api/**\n\n# Gates\n\n" + gate("G1", "a", null, null));
    s.write(".unlazy/api/gates/leaf-2.md", "OWNS: src/web/**\n\n# Gates\n\n" + gate("G1", "b", null, null));
    const a = await run(GATE_CHECK, ["--scope", "api", "--leaf", "leaf-1", "--claim"], { cwd: s.dir });
    const b = await run(GATE_CHECK, ["--scope", "api", "--leaf", "leaf-2", "--claim"], { cwd: s.dir });
    assert(a.code === 0 && b.code === 0, "disjoint claims should both succeed");
    assertHas(b.out, "CLAIMED 1 path(s) for api/leaf-2");
    const rel = await run(GATE_CHECK, ["--scope", "api", "--release"], { cwd: s.dir });
    assertHas(rel.out, "released 2 lease(s)");
  } finally { s.cleanup(); }
});

test("leases: a leaf that declares no paths still registers", async () => {
  // Registration is a roll call, not a permission grant: nothing here has ever
  // restricted what a process writes. Refusing a pathless leaf used to leave it
  // unregisterable, so omitting the OWNS line was a way out of the protocol
  // rather than the strictest position inside it.
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-ro.md", "# Gates\n\n" + gate("G1", "a", null, null));
    const r = await run(GATE_CHECK, ["--scope", "api", "--leaf", "leaf-ro", "--claim"], { cwd: s.dir });
    assert(r.code === 0, "a pathless leaf should register, got " + r.code + "\n" + r.out);
    assertHas(r.out, "REGISTERED api/leaf-ro");
    const rel = await run(GATE_CHECK, ["--scope", "api", "--release"], { cwd: s.dir });
    assertHas(rel.out, "released 1 lease(s)");
  } finally { s.cleanup(); }
});

test("leases: an identity cannot be registered twice, with or without paths", async () => {
  // Identity exclusivity used to be emergent, because a repeated claim
  // conflicted with its own held globs. A pathless lease has no globs to
  // overlap, so without an explicit check the guarantee vanishes exactly where
  // two workers sharing one logical identity would both believe they own it.
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-ro.md", "# Gates\n\n" + gate("G1", "a", null, null));
    s.write(".unlazy/api/gates/leaf-1.md", "OWNS: src/api/**\n\n# Gates\n\n" + gate("G1", "b", null, null));
    for (const leaf of ["leaf-ro", "leaf-1"]) {
      const first = await run(GATE_CHECK, ["--scope", "api", "--leaf", leaf, "--claim"], { cwd: s.dir });
      assert(first.code === 0, leaf + " should register once, got " + first.code + "\n" + first.out);
      const again = await run(GATE_CHECK, ["--scope", "api", "--leaf", leaf, "--claim"], { cwd: s.dir });
      assert(again.code === 3, "re-registering " + leaf + " should exit 3, got " + again.code);
      assertHas(again.out, "CONFLICT api/" + leaf + " is already registered");
      assertHas(again.out, "CLAIM REFUSED (1 conflict(s))");
    }
  } finally { s.cleanup(); }
});

// Ownership auditing needs a real repository, because the whole point is to
// read a record the thing being checked did not author. A machine without git
// fails these loudly rather than skipping them: a silently absent audit is the
// exact shape this feature exists to remove.
const git = (dir, ...args) => spawnSync("git", args, { cwd: dir, encoding: "utf8" });
function gitRepo(dir) {
  const init = git(dir, "init", "-q", ".");
  if (init.error || init.status !== 0) {
    throw new Error("ownership-audit tests require git on PATH: " +
      (init.error ? init.error.message : String(init.stderr).trim()));
  }
  git(dir, "config", "user.email", "test@example.invalid");
  git(dir, "config", "user.name", "unlazy test");
  git(dir, "commit", "--allow-empty", "-q", "-m", "root");
}

async function auditFixture(s, { register = ["leaf-1", "leaf-2"], gates = null, worktree = null } = {}) {
  gitRepo(s.dir);
  s.write("src/api/a.txt", "a\n");
  s.write("src/web/w.txt", "w\n");
  s.write("package-lock.json", "lock\n");
  s.write(".gitignore", ".unlazy/\n.worktrees/\n");
  s.write(".unlazy/sc/gates/leaf-1.md", "OWNS: src/api/**\n\n" + (gates || gate("G1", "api", echoOk("DONE"), "DONE")));
  s.write(".unlazy/sc/gates/leaf-2.md", "OWNS: src/web/**\n\n" + gate("G1", "web", echoOk("DONE"), "DONE"));
  git(s.dir, "add", "-A");
  git(s.dir, "commit", "-q", "-m", "base");
  // A worktree is created before any claim, so the pinned commit is the one the
  // branch forks from. Pinning first and committing afterwards is what puts a
  // driver's own commit inside every leaf's range.
  if (worktree) {
    const add = git(s.dir, "worktree", "add", join(s.dir, ".worktrees", worktree), "-b", worktree);
    if (add.status !== 0) throw new Error("git worktree add failed: " + String(add.stderr).trim());
  }
  for (const leaf of register) {
    await run(GATE_CHECK, ["--scope", "sc", "--leaf", leaf, "--claim"], { cwd: s.dir });
  }
  return join(s.dir, ".unlazy", "sc", "gates", "leaf-1.md");
}

const reverifyLeaf = (s, ledger) =>
  run(GATE_CHECK, ["--reverify", ledger, "--root", s.dir, "--cwd", s.dir], { cwd: s.dir });

// Reverify a leaf against the worktree it worked in, which is the only place a
// change set describes that leaf alone.
const reverifyIn = (s, ledger, worktree) =>
  run(GATE_CHECK, ["--reverify", ledger, "--root", s.dir, "--cwd", join(s.dir, ".worktrees", worktree)], { cwd: s.dir });
const inWorktree = (s, worktree, rel, text) => s.write(join(".worktrees", worktree, rel), text);

test("ownership audit: a leaf that stays inside its registration verifies", async () => {
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    s.write("src/api/a.txt", "edited\n");
    s.write("src/api/brand-new.txt", "created inside the registration\n");
    const r = await reverifyLeaf(s, ledger);
    assert(r.code === 0, "own-scope changes should verify, got " + r.code + "\n" + r.out);
    assertHas(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("ownership audit: straying outside every registration warns without blocking", async () => {
  // A dependency lock file, generated output, or a formatter tidying a
  // neighbour are all routine. Blocking these would make the handoff a rubber
  // stamp, and a rubber stamp is how the one that mattered gets waved through.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    s.write("src/api/a.txt", "edited\n");
    s.write("package-lock.json", "regenerated\n");
    const r = await reverifyLeaf(s, ledger);
    assert(r.code === 0, "a stray change should not block, got " + r.code + "\n" + r.out);
    assertHas(r.out, "changed files outside its own registration: package-lock.json");
    assertHas(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("ownership audit: writing into another leaf's registration is not verified", async () => {
  // No innocent version of this exists once the range is this leaf's alone. The
  // worktree is what makes it so: the change set ends at this leaf's own branch
  // tip, so nothing another leaf did can appear in it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { worktree: "leaf-1" });
    inWorktree(s, "leaf-1", "src/web/w.txt", "clobbered by leaf-1\n");
    const r = await reverifyIn(s, ledger, "leaf-1");
    assert(r.code === 1, "a trespass should not verify, got " + r.code + "\n" + r.out);
    assertHas(r.out, "UNMET ownership leaf-1");
    assertHas(r.out, "src/web/w.txt (registered to sc/leaf-2)");
  } finally { s.cleanup(); }
});

test("ownership audit: a check's own side effects are not charged to the leaf", async () => {
  // The audit measured the tree after the checks had run, so anything a check
  // wrote -- a compiler cache, a build artifact, a coverage file -- landed in
  // the leaf's change set and was read as the leaf's doing. A leaf that
  // committed one correct file was refused for bytecode its own approved oracle
  // generated while verifying it. Verifying something must not change what is
  // being measured about it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, {
      worktree: "leaf-1",
      gates: gate("G1", "api",
        nodeEval("require('fs').writeFileSync('src/web/generated.txt','made by the check\\n');console.log('DONE')"),
        "DONE"),
    });
    inWorktree(s, "leaf-1", "src/api/a.txt", "edited by the leaf\n");
    const r = await reverifyIn(s, ledger, "leaf-1");
    assert(r.code === 0, "a leaf must not answer for its own oracle's output, got " + r.code + "\n" + r.out);
    assertLacks(r.out, "UNMET ownership", "a side effect of the leaf's own check");
  } finally { s.cleanup(); }
});

test("ownership audit: a shared working tree warns instead of accusing", async () => {
  // The change set ends at the tree's HEAD, so where leaves share one tree a
  // sibling's own committed work lands in this leaf's range and matches that
  // sibling's registration. Refusing there accused finished, correctly
  // registered work of a collision the evidence cannot show, which is how a
  // real finding in this channel comes to be discounted. The measurement
  // answers "what changed here", and only an exclusive tree makes that the same
  // question as "what did this leaf change".
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    s.write("src/web/w.txt", "written by leaf-2, in the tree they share\n");
    const r = await reverifyLeaf(s, ledger);
    assert(r.code === 0, "an unattributable change must not refuse, got " + r.code + "\n" + r.out);
    assertHas(r.out, "cannot attribute changes to");
    assertHas(r.out, "src/web/w.txt (registered to sc/leaf-2)");
    assertHas(r.out, "own worktree");
    assertLacks(r.out, "UNMET ownership", "a shared working tree");
  } finally { s.cleanup(); }
});

test("ownership audit: an unauditable leaf is not verified rather than silently passed", async () => {
  // An audit that could not run has measured nothing. Reading its absence as a
  // pass would hand back the bypass the audit exists to close.
  const s = sandbox();
  try {
    s.write("src/api/a.txt", "a\n");
    s.write(".unlazy/sc/gates/leaf-1.md", "OWNS: src/api/**\n\n" + gate("G1", "api", echoOk("DONE"), "DONE"));
    const claim = await run(GATE_CHECK, ["--scope", "sc", "--leaf", "leaf-1", "--claim"], { cwd: s.dir });
    assertHas(claim.out, "no starting commit pinned");
    const ledger = join(s.dir, ".unlazy", "sc", "gates", "leaf-1.md");
    const r = await reverifyLeaf(s, ledger);
    assert(r.code === 1, "an unauditable leaf should not verify, got " + r.code + "\n" + r.out);
    assertHas(r.out, "no starting commit was pinned at registration");
  } finally { s.cleanup(); }
});

test("ownership audit: an unregistered ledger is never audited", async () => {
  // The silent half of the control. Registration is what places a leaf inside
  // the protocol, so a solo ledger must pass through untouched: a check that
  // fires on legitimate work teaches the agent to discount the whole channel.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { register: [] });
    s.write("src/web/w.txt", "changed everything\n");
    s.write("package-lock.json", "and this\n");
    const r = await reverifyLeaf(s, ledger);
    assert(r.code === 0, "an unregistered ledger should not be audited, got " + r.code + "\n" + r.out);
    assert(!r.out.includes("ownership"), "an unregistered ledger must produce no ownership finding\n" + r.out);
    assertHas(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("ownership audit: creating a file outside the registration is caught like an edit", async () => {
  // Exempting creation would be the cheapest way around the rule: a leaf that
  // wanted to change another's behaviour would add a file and import it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { worktree: "leaf-1" });
    inWorktree(s, "leaf-1", "src/web/injected.txt", "new file in another leaf's territory\n");
    const r = await reverifyIn(s, ledger, "leaf-1");
    assert(r.code === 1, "a new file in another registration should not verify, got " + r.code + "\n" + r.out);
    assertHas(r.out, "src/web/injected.txt (registered to sc/leaf-2)");
  } finally { s.cleanup(); }
});

test("plan: an unreadable plan keeps a scope from reducing to ALL MET", async () => {
  // Blocking only wave-opening would leave a pipeline able to report completion
  // on a plan nobody can read. Both directions are checked in one test: the
  // scope must block while the tree is broken and clear once it is fixed, or
  // the block proves nothing about the plan.
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.1.1.md", "OWNS: src/a/**\n\n- [x] G1: done\n  EVIDENCE: reviewed by test\n");
    const tree = (needs) => [
      "# Plan: t", "", "## Tree", "",
      "- 1 task ..... GATES.md ................ State: OPEN",
      "  - 1.1.1 a .. gates/leaf-1.1.1.md ..... Needs: " + needs + " .. State: READY", "",
    ].join("\n");

    s.write(".unlazy/api/PLAN.md", tree("4.4.4"));
    const broken = await run(GATE_CHECK, ["--status", "--scope", "api"], { cwd: s.dir });
    assert(broken.code === 1, "a broken plan should block the scope, got " + broken.code + "\n" + broken.out);
    assertHas(broken.out, "UNMET plan api");
    assertHas(broken.out, "Needs 4.4.4, which the tree does not list");

    s.write(".unlazy/api/PLAN.md", tree("-    "));
    const fixed = await run(GATE_CHECK, ["--status", "--scope", "api"], { cwd: s.dir });
    assert(fixed.code === 0, "fixing the plan should clear the block, got " + fixed.code + "\n" + fixed.out);
    assertHas(fixed.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("status log: appends, and appends survive concurrency", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "a", null, null));
    await Promise.all([
      run(GATE_CHECK, ["--scope", "api", "--log", "leaf-1 started"], { cwd: s.dir }),
      run(GATE_CHECK, ["--scope", "api", "--log", "leaf-2 started"], { cwd: s.dir }),
      run(GATE_CHECK, ["--scope", "api", "--log", "leaf-3 started"], { cwd: s.dir }),
    ]);
    const log = s.read(".unlazy/api/status.log");
    for (const n of [1, 2, 3]) assertHas(log, "leaf-" + n + " started", "status log");
  } finally { s.cleanup(); }
});

test("hook: does not block on a pipeline this session does not own", async () => {
  const s = sandbox();
  try {
    // api is finished; web has never been started. Ending the api session must
    // not be blocked by web's gates.
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n- [x] G1: done\n  EVIDENCE: measured, 8/8 passed\n");
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "not started", null, null));
    const r = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin: JSON.stringify({ cwd: s.dir }) });
    assertLacks(r.out, '"decision":"block"', "hook");
    assert(r.code === 0, "hook should exit 0");
  } finally { s.cleanup(); }
});

test("hook: blocks on its own scope, naming qualified ids", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-7.md", "# Gates\n\n" + gate("G3", "unfinished", null, null));
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates\n\n- [x] G1: done\n  EVIDENCE: proven\n");
    const r = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin: JSON.stringify({ cwd: s.dir }) });
    assertHas(r.out, '"decision":"block"');
    assertHas(r.out, "leaf-7:G3");
    assertHas(r.out, "[scope api]");
  } finally { s.cleanup(); }
});

test("hook: abandonment allows Stop but reports an explicit bounded handoff", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-7.md", [
      "# Gates",
      "",
      "- [ ] G3: impossible",
      "  EVIDENCE: pending",
      "",
      "ABANDON: G3 secret-looking reason that must stay ledger-local",
      "",
    ].join("\n"));
    const r = await run(STOP_HOOK, ["--scope", "api"], {
      cwd: s.dir,
      stdin: JSON.stringify({ cwd: s.dir, session_id: "handoff-test" }),
    });
    assertLacks(r.out, '"decision":"block"');
    assertHas(r.out, "HANDOFF REQUIRED");
    assertHas(r.out, "leaf-7:G3");
    assertLacks(r.out, "secret-looking reason");
  } finally { s.cleanup(); }
});

test("hook: unresolvable scope allows the stop instead of blocking blindly", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "a", null, null));
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "b", null, null));
    const r = await run(STOP_HOOK, [], { cwd: s.dir, stdin: JSON.stringify({ cwd: s.dir }) });
    assertLacks(r.out, '"decision":"block"', "hook");
    assertHas(r.out, "2 pipelines");
  } finally { s.cleanup(); }
});

test("hook: session binding resolves the scope among several", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "a", null, null));
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "b", null, null));
    const bind = await run(GATE_CHECK, ["--scope", "web", "--bind", "sess-abc"], { cwd: s.dir });
    assertHas(bind.out, "bound session sess-abc to scope web");
    const r = await run(STOP_HOOK, [], { cwd: s.dir, stdin: JSON.stringify({ cwd: s.dir, session_id: "sess-abc" }) });
    assertHas(r.out, '"decision":"block"');
    assertHas(r.out, "[scope web]");
  } finally { s.cleanup(); }
});

test("hook: each pipeline keeps its own loop-guard counter", async () => {
  const s = sandbox();
  try {
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "a", null, null));
    s.write(".unlazy/web/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "b", null, null));
    const stdin = JSON.stringify({ cwd: s.dir });
    for (let i = 0; i < 7; i++) await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
    const apiState = JSON.parse(s.read(".unlazy/api/hook-state.json"));
    const apiSession = Object.values(apiState.sessions)[0];
    assert(apiSession.blocks === 7, "api counter should be 7, got " + apiSession.blocks);
    // api has exhausted its guard and now releases; web is untouched and still blocks.
    const apiNow = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
    assertHas(apiNow.out, "releasing after 6 blocks");
    const webNow = await run(STOP_HOOK, ["--scope", "web"], { cwd: s.dir, stdin });
    assertHas(webNow.out, '"decision":"block"');
  } finally { s.cleanup(); }
});

test("hook: the loop guard tracks gate state, not file bytes", async () => {
  const s = sandbox();
  try {
    // A cosmetic edit is not progress. Keying the guard to raw bytes let any
    // touch of the ledger reset the counter, so an agent that keeps editing
    // without meeting a gate is never released. Re-running the checker did the
    // same thing by rewriting evidence text.
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "a", null, null) + gate("G2", "b", null, null));
    const stdin = JSON.stringify({ cwd: s.dir });
    for (let i = 0; i < 6; i++) {
      const blocked = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
      assertHas(blocked.out, '"decision":"block"');
    }
    s.write(".unlazy/api/gates/leaf-1.md",
      s.read(".unlazy/api/gates/leaf-1.md") + "\n<!-- still thinking about it -->\n");
    const afterCosmetic = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
    assertHas(afterCosmetic.out, "releasing after 6 blocks");
  } finally { s.cleanup(); }
});

test("hook: churning the ledger's shape cannot block a session forever", async () => {
  // The consecutive counter is keyed to the resolved state, so any change to it
  // rearms from zero, including a change that accomplishes nothing. Renaming an
  // unmet gate every round is not progress by any useful measure but does move
  // the state: under test that blocked eight times out of eight and never
  // released. A ceiling that counts every block this session has taken, whatever
  // the state did, is what makes the guard terminate at all.
  const s = sandbox();
  try {
    const stdin = JSON.stringify({ session_id: "churn", cwd: s.dir });
    let released = null;
    for (let i = 0; i < 40 && released === null; i++) {
      s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + gate("G" + i, "never met", null, null));
      const r = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
      if (!r.out.includes('"decision":"block"')) released = { round: i, out: r.out };
    }
    assert(released, "a session whose ledger keeps changing shape never released, so it is wedged");
    assertHas(released.out, "blocks in this session");
    assertHas(released.out, "still remain");
  } finally { s.cleanup(); }
});

test("hook: a session that is making real progress is not released early", async () => {
  // The silent half. The lifetime ceiling must not fire on a session that keeps
  // meeting gates, or it becomes a timer that stops enforcing after enough
  // stops, which is the opposite of the point.
  const s = sandbox();
  try {
    const stdin = JSON.stringify({ session_id: "working", cwd: s.dir });
    for (let i = 0; i < 8; i++) {
      // One gate met per round, one still outstanding: genuine progress, and the
      // resolved state changes every time.
      const met = Array.from({ length: i + 1 }, (_, n) => "- [x] M" + n + ": done\n  EVIDENCE: reviewed\n").join("\n");
      s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + met + "\n" + gate("G1", "outstanding", null, null));
      const r = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
      assertHas(r.out, '"decision":"block"');
    }
  } finally { s.cleanup(); }
});

test("hook: meeting a gate resets the loop guard", async () => {
  const s = sandbox();
  try {
    // The converse of the test above: real progress must still rearm the guard,
    // or a long run would be released while it is genuinely advancing.
    s.write(".unlazy/api/gates/leaf-1.md", "# Gates\n\n" + gate("G1", "a", null, null) + gate("G2", "b", null, null));
    const stdin = JSON.stringify({ cwd: s.dir });
    for (let i = 0; i < 3; i++) await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
    s.write(".unlazy/api/gates/leaf-1.md",
      "# Gates\n\n- [x] G1: a\n  EVIDENCE: measured 3 of 3\n\n" + gate("G2", "b", null, null));
    for (let i = 0; i < 4; i++) {
      const blocked = await run(STOP_HOOK, ["--scope", "api"], { cwd: s.dir, stdin });
      assertHas(blocked.out, '"decision":"block"');
    }
  } finally { s.cleanup(); }
});

test("hook: no gate files anywhere means silence", async () => {
  const s = sandbox();
  try {
    const r = await run(STOP_HOOK, [], { cwd: s.dir, stdin: JSON.stringify({ cwd: s.dir }) });
    assert(r.out.trim() === "", "expected no output, got: " + r.out);
    assert(r.code === 0, "expected exit 0");
  } finally { s.cleanup(); }
});

test("install: repeated install stays a single Stop entry", async () => {
  const s = sandbox();
  try {
    await run(INSTALL, ["--scope", "api"], { cwd: s.dir });
    await run(INSTALL, ["--scope", "api"], { cwd: s.dir });
    const cfg = JSON.parse(s.read(".claude/settings.local.json"));
    assert(cfg.hooks.Stop.length === 1, "expected 1 Stop entry, got " + cfg.hooks.Stop.length);
    assertHas(cfg.hooks.Stop[0].hooks[0].command, "--scope api", "hook command");
  } finally { s.cleanup(); }
});

test("install: changing the scope replaces the entry instead of stacking", async () => {
  const s = sandbox();
  try {
    await run(INSTALL, ["--scope", "api"], { cwd: s.dir });
    await run(INSTALL, ["--scope", "web"], { cwd: s.dir });
    const cfg = JSON.parse(s.read(".claude/settings.local.json"));
    assert(cfg.hooks.Stop.length === 1, "expected 1 Stop entry, got " + cfg.hooks.Stop.length);
    assertHas(cfg.hooks.Stop[0].hooks[0].command, "--scope web", "hook command");
  } finally { s.cleanup(); }
});

test("install: uninstall removes our entry and leaves others alone", async () => {
  const s = sandbox();
  try {
    s.write(".claude/settings.local.json", JSON.stringify({
      hooks: { Stop: [{ hooks: [{ type: "command", command: "node other-tool.mjs" }] }] },
    }, null, 2));
    await run(INSTALL, ["--scope", "api"], { cwd: s.dir });
    const r = await run(INSTALL, ["--uninstall"], { cwd: s.dir });
    assertHas(r.out, "Removed unlazy Stop hook");
    const cfg = JSON.parse(s.read(".claude/settings.local.json"));
    assert(cfg.hooks.Stop.length === 1, "expected the unrelated hook to remain");
    assertHas(cfg.hooks.Stop[0].hooks[0].command, "other-tool.mjs", "unrelated hook");
  } finally { s.cleanup(); }
});

test("install: an upstream v2.0 entry is still recognised and removable", async () => {
  const s = sandbox();
  try {
    s.write(".claude/settings.local.json", JSON.stringify({
      hooks: {
        Stop: [{
          hooks: [{
            type: "command",
            command: 'node "/home/me/.claude/skills/unlazy/scripts/stop-hook.mjs"',
          }],
        }],
      },
    }, null, 2));
    const r = await run(INSTALL, ["--uninstall"], { cwd: s.dir });
    assertHas(r.out, "Removed unlazy Stop hook");
    const cfg = JSON.parse(s.read(".claude/settings.local.json"));
    assert(!cfg.hooks, "settings should be left clean, got " + JSON.stringify(cfg));
  } finally { s.cleanup(); }
});

// ------------------------------------------------- reported counts

test("counts: an ownership finding does not discount the gates that passed", async () => {
  // The met count is the number the skill tells the agent to publish, and the
  // summary reaches it by subtracting every entry in the map that collects
  // extra unmet items. That subtraction is right only for entries that are
  // themselves gates already counted as met. An ownership finding is not a
  // gate, so counting it as one understates completion by one per finding and
  // reads as work having regressed rather than as ownership having been broken.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, {
      worktree: "leaf-1",
      gates: gate("G1", "one", echoOk("DONE"), "DONE") +
        gate("G2", "two", echoOk("DONE"), "DONE") +
        gate("G3", "three", echoOk("DONE"), "DONE"),
    });
    inWorktree(s, "leaf-1", "src/web/w.txt", "clobbered by leaf-1\n");
    const r = await reverifyIn(s, ledger, "leaf-1");
    assert(r.code === 1, "a trespass should not verify, got " + r.code + "\n" + r.out);
    assertHas(r.out, "UNMET ownership leaf-1");
    assertHas(r.out, "met: 3");
  } finally { s.cleanup(); }
});

test("counts: a gate whose reverification did not run is still discounted", async () => {
  // The silent half. The subtraction exists for exactly this case, so narrowing
  // it must not stop a previously met gate from being taken back off the met
  // count when its oracle was never re-executed.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    const met = await reverifyLeaf(s, ledger);
    assertHas(met.out, "ALL MET");
    // A different timeout is a different bound oracle, so the stored approval
    // no longer covers it and the gate is left unexecuted.
    const r = await run(GATE_CHECK, ["--reverify", ledger, "--root", s.dir, "--cwd", s.dir, "--timeout", "60"],
      { cwd: s.dir, noApprove: true });
    assert(r.code === 1, "an unapproved reverification should not pass, got " + r.code + "\n" + r.out);
    assertHas(r.out, "(reverify not run)");
    assertHas(r.out, "met: 0");
  } finally { s.cleanup(); }
});

test("approval: the pending-approval hint names the command that grants it", async () => {
  // A met gate is only ever re-executed under reverification, so a bare
  // `--approve` cannot reach its oracle. Printing that as the remedy sends the
  // reader around a loop whose middle step reports ALL MET and approves
  // nothing.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    await reverifyLeaf(s, ledger);
    const blocked = await run(GATE_CHECK, ["--reverify", ledger, "--root", s.dir, "--cwd", s.dir, "--timeout", "60"],
      { cwd: s.dir, noApprove: true });
    assertHas(blocked.out, "--reverify --approve");
    const granted = await run(GATE_CHECK, ["--reverify", "--approve", ledger, "--root", s.dir, "--cwd", s.dir, "--timeout", "60"],
      { cwd: s.dir, noApprove: true });
    assert(granted.code === 0, "the named command should grant and pass, got " + granted.code + "\n" + granted.out);
    assertHas(granted.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("approval: --approve reports a met gate whose oracle it could not approve", async () => {
  // `--approve GATES.md` is the documented way to approve, so a run that
  // silently skips the one gate with an outstanding approval and then reports
  // ALL MET is the failure this whole skill exists to catch: a report of
  // completion resting on a check that was never re-established.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    await reverifyLeaf(s, ledger);
    const r = await run(GATE_CHECK, ["--approve", ledger, "--root", s.dir, "--cwd", s.dir, "--timeout", "60"],
      { cwd: s.dir, noApprove: true });
    assertHas(r.out, "APPROVAL PENDING");
    assertHas(r.out, "leaf-1:G1");
  } finally { s.cleanup(); }
});

test("approval: a fully approved run says nothing about pending approvals", async () => {
  // The silent half. The notice must stay quiet when every met gate's oracle is
  // already approved, or it becomes noise on the ordinary path and trains the
  // reader to skip the line that matters.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    await reverifyLeaf(s, ledger);
    const r = await run(GATE_CHECK, ["--approve", ledger, "--root", s.dir, "--cwd", s.dir],
      { cwd: s.dir, noApprove: true });
    assert(r.code === 0, "an approved run should pass, got " + r.code + "\n" + r.out);
    assertLacks(r.out, "APPROVAL PENDING", "a fully approved run");
    assertHas(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

// ------------------------------------------- recorded versus executed

test("status: a recorded ALL MET says that it executed nothing", async () => {
  // --status reaches ALL MET from stored checkboxes alone, having run no oracle
  // and audited no ownership. Printing the sentence an executed run prints let
  // recorded history stand in for a fresh result, which after a merge, a rename,
  // or a deleted worktree can describe a tree that no longer exists.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { register: [] });
    const met = await run(GATE_CHECK, [ledger, "--root", s.dir, "--cwd", s.dir], { cwd: s.dir });
    assertHas(met.out, "ALL MET");
    const r = await run(GATE_CHECK, ["--status", ledger, "--root", s.dir], { cwd: s.dir, noApprove: true });
    assert(r.code === 0, "status on a met ledger should pass, got " + r.code + "\n" + r.out);
    assertHas(r.out, "ALL MET");
    assertHas(r.out, "nothing was executed");
    assertHas(r.out, "--reverify");
  } finally { s.cleanup(); }
});

test("status: an executed ALL MET does not describe itself as recorded", async () => {
  // The silent half. A run that really executed must not carry the caveat, or
  // the caveat stops distinguishing anything and the reader learns to skip it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { register: [] });
    const r = await run(GATE_CHECK, [ledger, "--root", s.dir, "--cwd", s.dir], { cwd: s.dir });
    assert(r.code === 0, "an executed run should pass, got " + r.code + "\n" + r.out);
    assertHas(r.out, "ALL MET");
    assertLacks(r.out, "nothing was executed", "an executed run");
  } finally { s.cleanup(); }
});

test("usage: the scope-with-file refusal states its own remedy", async () => {
  // --help lists --root and --cwd but never connects them to this refusal, so a
  // reader knew they were wrong with no stated way to be right; two testers in
  // one round went to the reference docs for a fact the error could carry.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { register: [] });
    const r = await run(GATE_CHECK, ["--scope", "sc", "--status", ledger], { cwd: s.dir, noApprove: true });
    assert(r.code === 2, "scope plus explicit file should exit 2, got " + r.code + "\n" + r.out);
    assertHas(r.out, "mutually exclusive");
    assertHas(r.out, "drop --scope and pass --root");
  } finally { s.cleanup(); }
});

test("ownership: warning detail survives a stdout-only capture", async () => {
  // The detail naming which files a warning was about went to stderr while the
  // verdict stayed on stdout, so a capture that dropped stderr kept ALL MET and
  // lost the only line saying what to go look at. Audit findings share the
  // verdict's stream; only tool diagnostics stay on stderr.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { worktree: "leaf-1" });
    inWorktree(s, "leaf-1", "src/api/a.txt", "edited\n");
    inWorktree(s, "leaf-1", "package-lock.json", "regenerated\n");
    const r = spawnSync(process.execPath,
      [GATE_CHECK, "--reverify", "--approve", ledger, "--root", s.dir, "--cwd", join(s.dir, ".worktrees", "leaf-1")],
      { cwd: s.dir, encoding: "utf8", env: { ...process.env, UNLAZY_APPROVAL_DIR: APPROVAL_ROOT } });
    const stdoutOnly = String(r.stdout || "");
    assertHas(stdoutOnly, "package-lock.json");
    assertHas(stdoutOnly, "outside its own registration");
    assertHas(stdoutOnly, "ownership warning(s) above");
  } finally { s.cleanup(); }
});

test("status: recorded evidence behind an unchecked box is pointed at", async () => {
  // "(unchecked)" beside a fully written EVIDENCE line reads as a missing step
  // somewhere else, and a tester walked the whole flow again hunting for it.
  // The tick stays manual; the message just says which act is missing.
  const s = sandbox();
  try {
    const ledger = s.write("GATES.md", "# Gates\n\n- [ ] G1: reviewed by hand\n  EVIDENCE: reviewed the diff line by line\n");
    const r = await run(GATE_CHECK, ["--status", ledger, "--root", s.dir], { cwd: s.dir, noApprove: true });
    assert(r.code === 1, "an unchecked gate should be unmet, got " + r.code + "\n" + r.out);
    assertHas(r.out, "though EVIDENCE is recorded");
    assertHas(r.out, "tick the box");
  } finally { s.cleanup(); }
});

test("status: an unchecked box with pending evidence stays plainly unchecked", async () => {
  // The silent half. The hint must mark the one odd state, not decorate every
  // unfinished manual gate.
  const s = sandbox();
  try {
    const ledger = s.write("GATES.md", "# Gates\n\n- [ ] G1: reviewed by hand\n  EVIDENCE: pending\n");
    const r = await run(GATE_CHECK, ["--status", ledger, "--root", s.dir], { cwd: s.dir, noApprove: true });
    assert(r.code === 1, "an unchecked gate should be unmet, got " + r.code + "\n" + r.out);
    assertHas(r.out, "(unchecked)");
    assertLacks(r.out, "though EVIDENCE is recorded", "pending evidence");
  } finally { s.cleanup(); }
});

test("usage: refusing an execution flag names the mode that rejected it", async () => {
  // The documented form for an explicit ledger carries --root and --cwd, so a
  // reader who copies that shape into a status call got four flag names and no
  // statement of which mode objected or which flag to drop. Three separate
  // testers spent a call on it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { register: [] });
    const r = await run(GATE_CHECK, ["--status", ledger, "--root", s.dir, "--cwd", s.dir],
      { cwd: s.dir, noApprove: true });
    assert(r.code === 2, "an execution flag on --status should exit 2, got " + r.code + "\n" + r.out);
    assertHas(r.out, "--status");
    assertHas(r.out, "never executes");
    assertHas(r.out, "--root is still accepted");
  } finally { s.cleanup(); }
});

test("claim: a leaf whose dependencies are outstanding says so at claim time", async () => {
  // Claiming reserves paths; it does not clear a leaf to run. A bare success
  // reads as permission to proceed, and the refusal only arrives later at a
  // wave the reader has by then designed around.
  const s = sandbox();
  try {
    await auditFixture(s, { register: [] });
    s.write(".unlazy/sc/PLAN.md", [
      "# Plan: t", "", "## Tree", "",
      "- 1 a ...... gates/leaf-1.md ..... Needs: - ..... State: READY",
      "- 2 b ...... gates/leaf-2.md ..... Needs: 1 ..... State: WAITING", "",
    ].join("\n"));
    const r = await run(GATE_CHECK, ["--scope", "sc", "--leaf", "leaf-2", "--claim"], { cwd: s.dir });
    assert(r.code === 0, "claiming should still succeed, got " + r.code + "\n" + r.out);
    assertHas(r.out, "CLAIMED");
    assertHas(r.out, "Needs 1");
    assertHas(r.out, "refused until");
  } finally { s.cleanup(); }
});

test("claim: a leaf whose dependencies are satisfied claims quietly", async () => {
  // The silent half. The note must appear only where a wave would actually
  // refuse, or every claim in a healthy pipeline carries a warning about
  // nothing.
  const s = sandbox();
  try {
    await auditFixture(s, { register: [] });
    s.write(".unlazy/sc/PLAN.md", [
      "# Plan: t", "", "## Tree", "",
      "- 1 a ...... gates/leaf-1.md ..... Needs: - ..... State: VERIFIED",
      "- 2 b ...... gates/leaf-2.md ..... Needs: 1 ..... State: READY", "",
    ].join("\n"));
    const r = await run(GATE_CHECK, ["--scope", "sc", "--leaf", "leaf-2", "--claim"], { cwd: s.dir });
    assert(r.code === 0, "claiming should succeed, got " + r.code + "\n" + r.out);
    assertLacks(r.out, "refused until", "a satisfied dependency");
  } finally { s.cleanup(); }
});

test("ownership: a clean audit says so rather than staying silent", async () => {
  // A clean audit printed nothing at all, which in a transcript is identical to
  // an audit that never ran. One tester built a positive control of their own
  // before they would believe the silence meant anything.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { worktree: "leaf-1" });
    inWorktree(s, "leaf-1", "src/api/a.txt", "edited inside its own registration\n");
    const r = await reverifyIn(s, ledger, "leaf-1");
    assert(r.code === 0, "a clean leaf should verify, got " + r.code + "\n" + r.out);
    assertHas(r.out, "ownership: clean for leaf-1");
    assertHas(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("ownership: a warning is carried into the verdict line", async () => {
  // The warning went only to stderr, above a verdict that still said ALL MET and
  // still exited 0. Two testers independently reported that anyone reading the
  // verdict, checking the exit status, or asking again later would never see it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { worktree: "leaf-1" });
    inWorktree(s, "leaf-1", "src/api/a.txt", "edited\n");
    inWorktree(s, "leaf-1", "package-lock.json", "regenerated by a tool\n");
    const r = await reverifyIn(s, ledger, "leaf-1");
    assert(r.code === 0, "a stray change should not block, got " + r.code + "\n" + r.out);
    assertHas(r.out, "ownership warning(s) above");
    assertHas(r.out, "ALL MET");
  } finally { s.cleanup(); }
});

test("ownership: a verdict with nothing to warn about carries no count", async () => {
  // The silent half. A count appended to every verdict would say nothing, and a
  // reader would stop reading it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { worktree: "leaf-1" });
    inWorktree(s, "leaf-1", "src/api/a.txt", "edited inside its own registration\n");
    const r = await reverifyIn(s, ledger, "leaf-1");
    assertLacks(r.out, "ownership warning(s) above", "a clean verdict");
  } finally { s.cleanup(); }
});

test("ownership: an approving run says it did not audit ownership", async () => {
  // The approving run is the one that first turns a leaf green, so it is exactly
  // where a trespass already committed goes unmentioned while ALL MET prints.
  // Three testers across two rounds reported nearly certifying a leaf on it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s);
    s.write("src/web/w.txt", "clobbered before the approving run\n");
    const r = await run(GATE_CHECK, [ledger, "--root", s.dir, "--cwd", s.dir], { cwd: s.dir });
    assert(r.code === 0, "an approving run should still pass its gates, got " + r.code + "\n" + r.out);
    assertHas(r.out, "ALL MET");
    assertHas(r.out, "ownership was not audited");
    assertHas(r.out, "--reverify");
  } finally { s.cleanup(); }
});

test("ownership: an unregistered ledger is not told its ownership went unaudited", async () => {
  // The silent half. A solo ledger is outside the protocol, so a notice there is
  // pure noise about a rule that does not apply to it.
  const s = sandbox();
  try {
    const ledger = await auditFixture(s, { register: [] });
    const r = await run(GATE_CHECK, [ledger, "--root", s.dir, "--cwd", s.dir], { cwd: s.dir });
    assert(r.code === 0, "an unregistered ledger should pass, got " + r.code + "\n" + r.out);
    assertLacks(r.out, "ownership was not audited", "an unregistered ledger");
  } finally { s.cleanup(); }
});

// ---------------------------------------------------------------- driver

const selected = tests.filter(t => t.name.includes(filter));
let passed = 0;
const failures = [];

for (const t of selected) {
  try {
    await t.fn();
    passed++;
    console.log("ok   " + t.name);
  } catch (e) {
    failures.push({ name: t.name, err: e });
    console.log("FAIL " + t.name + "\n     " + String(e.message).split("\n").join("\n     "));
  }
}

console.log("");
console.log(passed + "/" + selected.length + " passed");
try { rmSync(APPROVAL_ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
process.exit(failures.length ? 1 : 0);
