#!/usr/bin/env node
// lint-tests.mjs : behavioural tests for scripts/gate-lint.mjs.
// Zero dependencies, cross-platform.
//
//   node tests/lint-tests.mjs            run all
//   node tests/lint-tests.mjs regex      run tests whose name contains "regex"
//
// Prints "N/N passed" on success, which is the string CI matches on.

import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const HERE = dirname(fileURLToPath(import.meta.url));
const LINT = join(HERE, "..", "scripts", "gate-lint.mjs");
const filter = process.argv[2] || "";
const DIR = mkdtempSync(join(tmpdir(), "unlazy-lint-test-"));

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

function write(name, body) {
  const path = join(DIR, name);
  writeFileSync(path, body);
  return path;
}

function lint(...args) {
  const result = spawnSync(process.execPath, [LINT, ...args], { encoding: "utf8" });
  return { out: result.stdout + result.stderr, code: result.status };
}

// ------------------------------------------------------------- fixtures

// Every defect below passes gate-check and the Stop hook today.
const WEAK = write("weak.md", `# Gates: weak ledger

Scope: a ledger that satisfies every enforcement layer and proves nothing

- [ ] G1: the entire feature works perfectly
  CHECK: echo ok
  EXPECT: ok
  EVIDENCE: pending

- [ ] G2: improve the error handling
  CHECK: node scripts/verify.mjs --banner DONE
  EXPECT: DONE
  EVIDENCE: pending

- [ ] G3: renders 34 stat rows
  EVIDENCE: pending

- [ ] G4: config path is reported
  CHECK: node scripts/show-path.mjs
  EXPECT: /etc/app/conf/
  EVIDENCE: pending
`);

const SOUND_BODY = `# Gates: sound ledger

Scope: pricing section renders and behaves

- [ ] G1: three tiers render with real copy
  CHECK: node scripts/check.mjs pricing --tiers
  EXPECT: 3/3 tiers rendered
  EVIDENCE: pending

- [ ] G2: annual toggle changes price and label
  CHECK: node scripts/check.mjs pricing --toggle
  EXPECT: toggle switched both fields
  EVIDENCE: pending

- [ ] G3: typecheck is clean
  CHECK: npx tsc --noEmit
  EXPECT: /Found 0 errors/
  EVIDENCE: pending

- [ ] G4: unit suite green
  CHECK: node --test test/pricing.test.mjs
  EXPECT: /# fail 0/
  EVIDENCE: pending

- [ ] G5: no console errors on load
  CHECK: node scripts/check.mjs pricing --console
  EXPECT: 0 console errors
  EVIDENCE: pending
`;

const SOUND = write("sound.md", SOUND_BODY);
const MANUAL = write("manual.md", SOUND_BODY + `
- [ ] G6: copy reads as written by the brand, not by a model
  EVIDENCE: pending
`);

// ------------------------------------------------------------- tests

test("lint: a fixed-output command is advisory by default", () => {
  const { out, code } = lint(WEAK);
  assert.match(out, /G1: CHECK looks like a fixed-output command/);
  assert.equal(code, 0);
});

test("lint: a chained verifier is not classified as a tautology", () => {
  const chained = write("chained.md", `# Gates: chained

- [ ] G1: verifier succeeds
  CHECK: echo starting && node scripts/verify.mjs
  EXPECT: VERIFY_OK
  EVIDENCE: pending
`);
  assert.doesNotMatch(lint(chained).out, /tautological-check|fixed-output command/);
});

test("lint: EXPECT passed as an argument is not guaranteed output", () => {
  const argument = write("argument.md", `# Gates: argument

- [ ] G1: verifier succeeds
  CHECK: node scripts/verify.mjs --expected VERIFY_OK
  EXPECT: VERIFY_OK
  EVIDENCE: pending
`);
  assert.doesNotMatch(lint(argument).out, /guarantees its own pass|expect-echoes-check/);
});

test("lint: an expectation shared with failure output is warned", () => {
  assert.match(lint(WEAK).out, /G1:.*also appears in failure output/);
});

test("lint: an activity title is warned", () => {
  assert.match(lint(WEAK).out, /G2:.*names an activity, not an outcome/);
});

test("lint: an unmeasured number in a manual gate is warned", () => {
  assert.match(lint(WEAK).out, /G3:.*states a number that nothing measures/);
});

test("lint: a literal path read as a regex is warned", () => {
  assert.match(lint(WEAK).out, /G4:.*looks like a literal path/);
});

test("lint: a deliberate slash wrapped pattern is not warned", () => {
  assert.doesNotMatch(lint(SOUND).out, /looks like a literal path/);
});

test("lint: shipped leaf and node templates satisfy the documented size policy", () => {
  for (const name of ["gates-leaf.md", "gates-node.md"]) {
    const result = lint(join(HERE, "..", "templates", name));
    assert.equal(result.code, 0, result.out);
    assert.doesNotMatch(result.out, /thin-ledger|fat-ledger|under five|over twelve/);
  }
});

test("lint: a sound ledger is clean and exits 0", () => {
  const { out, code } = lint(SOUND);
  assert.match(out, /^LINT OK$/m);
  assert.equal(code, 0);
});

test("lint: default warnings and strict warnings have distinct gate markers and exits", () => {
  const normal = lint(MANUAL);
  assert.match(normal.out, /G6:.*judged by hand/);
  assert.match(normal.out, /^LINT OK \(\d+ warning\(s\)\)$/m);
  assert.equal(normal.code, 0);
  const strict = lint("--strict", MANUAL);
  assert.equal(strict.code, 1);
  assert.match(strict.out, /^LINT FINDINGS:/m);
  assert.doesNotMatch(strict.out, /^LINT OK/m);
});

test("lint: json reports counts and stays parseable", () => {
  const data = JSON.parse(lint("--json", WEAK).out);
  assert.equal(data.ok, true);
  assert.equal(data.errors, 0);
  assert.ok(data.warnings >= 1, "expected warnings, got " + data.warnings);
  assert.ok(data.findings.every((f) => f.rule && f.level));
  const strict = JSON.parse(lint("--strict", "--json", WEAK).out);
  assert.equal(strict.ok, false);
});

test("lint: a ledger the shared parser rejects exits 2, not 1", () => {
  const broken = write("broken.md", "# Gates: broken\n\n- [ ] no explicit id here\n");
  assert.equal(lint(broken).code, 2);
});

test("lint: multiple ledgers in one run are all honored", () => {
  const { out } = lint(SOUND, WEAK);
  assert.match(out, /weak\.md/);
  assert.match(out, /G1: CHECK looks like a fixed-output command/);
});

test("lint: path ambiguity is sourced once from the shared parser", () => {
  const result = lint(WEAK);
  assert.equal((result.out.match(/\[path-read-as-regex\]/g) || []).length, 1, result.out);
});

test("lint: an unknown option exits 2", () => {
  assert.equal(lint("--nope", SOUND).code, 2);
});

test("CLI: an unknown short option exits 2", () => {
  assert.equal(lint("-x", SOUND).code, 2);
});

test("CLI: the positional marker permits literal files named --help and -h", () => {
  write("--help", SOUND_BODY);
  write("-h", SOUND_BODY);
  for (const filename of ["--help", "-h"]) {
    const result = spawnSync(process.execPath, [LINT, "--", filename], { cwd: DIR, encoding: "utf8" });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /^LINT OK$/m);
    assert.doesNotMatch(result.stdout, /^usage:/m);
  }
});

test("lint: no arguments exits 2", () => {
  assert.equal(lint().code, 2);
});

// ------------------------------------------------------------- runner

// ------------------------------------------------------- plan linting
//
// Parsability belongs to the planning stage. A tree that cannot be read is not
// a formatting nuisance to tolerate at dispatch time; it means planning did not
// finish, so it is caught here where fixing it is cheap.

const SHIPPED_PLAN = join(HERE, "..", "templates", "PLAN.md");

test("plan: the shipped template lints clean", () => {
  // The quiet half. A lint that fires on unlazy's own template would be
  // teaching every reader to ignore it from the first run.
  const r = lint(SHIPPED_PLAN);
  assert.equal(r.code, 0, "the shipped PLAN template must lint clean\n" + r.out);
  assert.match(r.out, /LINT OK/);
});

test("plan: an unknown dependency id is an error", () => {
  const file = write("plan-unknown.md", [
    "# Plan: t", "", "## Tree", "",
    "- 1 task ..... GATES.md ................ State: OPEN",
    "  - 1.1.1 a .. gates/leaf-1.1.1.md ..... Needs: 9.9.9 .. State: READY", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 2, r.out);
  assert.match(r.out, /Needs 9\.9\.9, which the tree does not list/);
});

test("plan: a row id that is not digits and dots names the rule it broke", () => {
  // "tree row has no leading id" reads as a spacing complaint, so a reader who
  // named a row for what it builds could not learn the character-class rule
  // from it and went to the documentation instead. The message has to carry the
  // rule and the offending token.
  const file = write("plan-named.md", [
    "# Plan: t", "", "## Tree", "",
    "- 1 task ....... GATES.md ................ State: OPEN",
    "  - parser p ... gates/leaf-parser.md ..... Needs: - ..... State: READY", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 2, r.out);
  assert.match(r.out, /digits and dots only/);
  assert.match(r.out, /"parser"/);
});

test("plan: VERIFIED is checked against the ledger it claims", () => {
  // VERIFIED is the one state other machinery believes: dispatch admits
  // dependents on it, and it is written by hand. A tester set it on a row whose
  // ledger had never come clean and got LINT OK even under --strict, so the one
  // completion claim worth auditing was the one nothing audited.
  write("leaf-1.md", "# Gates\n\n- [ ] G1: never finished\n  EVIDENCE: pending\n");
  const file = write("plan-verified-drift.md", [
    "# Plan: t", "", "## Tree", "",
    "- 1 a ..... leaf-1.md ..... Needs: - ..... State: VERIFIED", "",
  ].join("\n"));
  const r = lint(file);
  // An error, not a warning: the claim and the ledger are both on disk and they
  // certainly contradict, and a warning folded into a green LINT OK was skimmed
  // straight past by the reader it existed for. Exit 1, the findings class, not
  // 2: the row parses, so this is a failed audit rather than an unreadable one.
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /ERROR/);
  assert.match(r.out, /1 is VERIFIED while its ledger leaf-1\.md has 1 unmet/);
  assert.match(r.out, /written by hand/);
  assert.doesNotMatch(r.out, /LINT OK/);
});

test("plan: a VERIFIED row whose ledger is missing is named", () => {
  const file = write("plan-verified-missing.md", [
    "# Plan: t", "", "## Tree", "",
    "- 9 a ..... leaf-9.md ..... Needs: - ..... State: VERIFIED", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 0, r.out);
  assert.match(r.out, /9 is VERIFIED but its ledger leaf-9\.md does not exist/);
});

test("plan: a VERIFIED row its ledger backs is not questioned", () => {
  // The silent half. A warning on every honest VERIFIED row would train the
  // reader to skip the one that names real drift.
  write("leaf-2.md", "# Gates\n\n- [x] G1: finished\n  EVIDENCE: reviewed by hand\n");
  const file = write("plan-verified-clean.md", [
    "# Plan: t", "", "## Tree", "",
    "- 2 a ..... leaf-2.md ..... Needs: - ..... State: VERIFIED", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 0, r.out);
  assert.doesNotMatch(r.out, /plan-verified/);
  assert.match(r.out, /LINT OK/);
});

test("plan: a dependency cycle is an error", () => {
  const file = write("plan-cycle.md", [
    "# Plan: t", "", "## Tree", "",
    "- 1 task ..... GATES.md ................ State: OPEN",
    "  - 1.1.1 a .. gates/leaf-1.1.1.md ..... Needs: 1.1.2 .. State: WAITING",
    "  - 1.1.2 b .. gates/leaf-1.1.2.md ..... Needs: 1.1.1 .. State: WAITING", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 2, r.out);
  assert.match(r.out, /dependency cycle/);
});

test("plan: a row whose ledger names a different id is an error", () => {
  // Guessing the ledger from the id, or the id from the ledger, would let a
  // dependency silently point at a file belonging to different work.
  const file = write("plan-mismatch.md", [
    "# Plan: t", "", "## Tree", "",
    "- 1 task ..... GATES.md ................ State: OPEN",
    "  - 1.1.1 a .. gates/leaf-9.9.9.md ..... Needs: - ...... State: READY", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 2, r.out);
  assert.match(r.out, /names a different id/);
});

test("plan: a state inconsistent with its own dependency warns without failing", () => {
  const file = write("plan-drift.md", [
    "# Plan: t", "", "## Tree", "",
    "- 1 task ..... GATES.md ................ State: OPEN",
    "  - 1.1.1 a .. gates/leaf-1.1.1.md ..... Needs: - ...... State: READY",
    "  - 1.1.2 b .. gates/leaf-1.1.2.md ..... Needs: 1.1.1 .. State: READY", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 0, "an inconsistent state is advisory, not fatal\n" + r.out);
  assert.match(r.out, /1\.1\.2 is READY while it Needs 1\.1\.1/);
});

test("plan: prose bullets outside the tree are not read as dependencies", () => {
  // A parser loose enough to read ordinary prose would invent dependencies out
  // of sentences in the Contract section.
  const file = write("plan-prose.md", [
    "# Plan: t", "",
    "## Contract", "",
    "- Dependencies: 1.1.1 must be VERIFIED before 1.1.2 starts",
    "- Ownership: src/api/** belongs to 1.1.1", "",
    "## Tree", "",
    "- 1 task ..... GATES.md ................ State: OPEN",
    "  - 1.1.1 a .. gates/leaf-1.1.1.md ..... Needs: - ...... State: READY", "",
  ].join("\n"));
  const r = lint(file);
  assert.equal(r.code, 0, "prose bullets must not become tree rows\n" + r.out);
  assert.match(r.out, /LINT OK/);
});

const selected = tests.filter((t) => t.name.includes(filter));
let passed = 0;
const failures = [];

for (const t of selected) {
  try {
    t.fn();
    passed++;
    console.log("ok   " + t.name);
  } catch (e) {
    failures.push(t.name);
    console.log("FAIL " + t.name + "\n     " + String(e.message).split("\n").join("\n     "));
  }
}

console.log("");
console.log(passed + "/" + selected.length + " passed");
try { rmSync(DIR, { recursive: true, force: true }); } catch { /* windows lag */ }
process.exit(failures.length ? 1 : 0);
