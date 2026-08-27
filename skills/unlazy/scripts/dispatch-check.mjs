#!/usr/bin/env node
// Records and checks the all-starts-before-wait dispatch contract. Node 16+.

import { existsSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { getDispatchWave, updateDispatch } from "./lib/dispatch.mjs";
import { exclusiveWorktree } from "./lib/git.mjs";
import { UNLAZY_DIR, ledgerCompletion, readLeases, validateScopeId } from "./lib/gates.mjs";
import { readPlan, rowForLedger } from "./lib/plan.mjs";

const COMMANDS = new Set(["open", "start", "seal", "return", "abandon", "status"]);
const args = process.argv.slice(2);

function usage() {
  return [
    "Usage:",
    "  dispatch-check.mjs open --scope ID --wave ID --leaf ID [--leaf ID ...] [--root PATH]",
    "  dispatch-check.mjs start --scope ID --wave ID --leaf ID --handle OPAQUE_ID [--workspace PATH] [--root PATH]",
    "  dispatch-check.mjs seal --scope ID --wave ID [--root PATH]",
    "  dispatch-check.mjs return --scope ID --wave ID --leaf ID [--root PATH]",
    "  dispatch-check.mjs abandon --scope ID --wave ID --reason TEXT [--root PATH]",
    "  dispatch-check.mjs status --scope ID --wave ID [--root PATH]",
    "",
    "Every leaf must hold an ownership registration before its wave opens.",
    "Register one with: gate-check.mjs --scope ID --leaf ID --claim",
    "",
    "exit codes: 0 action succeeded, or status found a complete wave;",
    "            1 status found an incomplete wave;",
    "            2 usage, parse, or state error;",
    "            3 an unregistered leaf was refused.",
  ].join("\n");
}

function die(message, code = 2) {
  const safe = String(message).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 500);
  console.error("unlazy dispatch: " + safe);
  process.exit(code);
}

// Exit 3 carries gate-check's lease-conflict meaning, so a single code means
// "the ownership protocol refused this" across both tools.
const refuse = (message) => die(message, 3);

if (!args.length || args[0] === "--help" || args[0] === "-h") {
  console.log(usage());
  process.exit(args.length ? 0 : 2);
}

const command = args.shift();
if (!COMMANDS.has(command)) die("unknown command " + command + "\n" + usage());

const options = { root: process.cwd(), scope: null, wave: null, leaves: [], handle: null, reason: null, workspace: null };
const single = new Set();
while (args.length) {
  const option = args.shift();
  if (!["--root", "--scope", "--wave", "--leaf", "--handle", "--reason", "--workspace"].includes(option)) die("unknown option " + option);
  if (!args.length || args[0].startsWith("--")) die(option + " requires a value");
  const value = args.shift();
  if (option === "--leaf") options.leaves.push(value);
  else {
    if (single.has(option)) die(option + " may be provided only once");
    single.add(option);
    options[option.slice(2)] = value;
  }
}

if (!options.scope) die("--scope is required");
if (!options.wave) die("--wave is required");
options.root = resolve(options.root);

if (command === "open") {
  if (!options.leaves.length) die("open requires at least one --leaf");
  if (options.handle !== null || options.reason !== null) die("open does not accept --handle or --reason");
  // Participation, checked before anything launches. The question is answered
  // from lease records the tool itself wrote, so no ledger is read and there is
  // nothing a leaf can omit from its own paperwork to escape the requirement.
  // Refusing here rather than at seal means a collision is prevented instead of
  // reported: by seal the leaves have already run and already written.
  //
  // Shape errors still win. Reading leases for a traversal scope would report a
  // missing registration for an id that could never have held one, hiding the
  // real fault behind a downstream symptom.
  const shapeError = validateScopeId(options.scope) ||
    options.leaves.map((leaf) => validateScopeId(leaf, "leaf")).find(Boolean);
  if (shapeError) die(shapeError);
  const registered = new Set(
    readLeases(options.root).filter((lease) => lease.scope === options.scope).map((lease) => lease.leaf),
  );
  const missing = options.leaves.filter((leaf) => !registered.has(leaf));
  if (missing.length) {
    refuse("not registered in scope " + options.scope + ": " + missing.join(", ") +
      "; run gate-check.mjs --scope " + options.scope + " --leaf <id> --claim for each before opening a wave");
  }

  // Declared dependencies, checked before anything launches. A plan is only
  // required once work is split, so a scope without one is left alone; where one
  // exists it is read as written. Completion comes from the dependency's own
  // ledger, because the plan's State field is authored by the same agent that
  // wants to proceed, and the two are compared so a plan that has drifted out of
  // step with reality is caught rather than believed.
  const planFile = join(options.root, UNLAZY_DIR, options.scope, "PLAN.md");
  if (existsSync(planFile)) {
    let plan;
    try { plan = readPlan(readFileSync(planFile, "utf8")); }
    catch (error) { die("cannot read " + planFile + ": " + error.message); }
    if (plan.errors.length) {
      const first = plan.errors[0];
      refuse("the plan for scope " + options.scope + " is not readable (line " + first.line + ": " + first.detail +
        "); planning is not complete, so run gate-lint.mjs on " + planFile + " before dispatching");
    }
    const blocked = [];
    // A leaf the tree does not carry cannot be dependency checked at all: there
    // is no row to read Needs from. Passing it through in silence made the whole
    // check opt-out by naming, because a ledger called for what it builds rather
    // than for its id matches no row, and the identical work that refuses under
    // `leaf-1.1.2.md` opened cleanly under `leaf-cli.md`.
    const unplanned = [];
    for (const leaf of options.leaves) {
      const row = rowForLedger(plan, leaf);
      if (!row) { unplanned.push(leaf); continue; }
      for (const need of row.needs) {
        const needRow = plan.byId.get(need);
        const ledgerFile = join(options.root, UNLAZY_DIR, options.scope, needRow.path);
        if (!existsSync(ledgerFile)) {
          blocked.push(leaf + " needs " + need + ", whose ledger " + needRow.path + " is missing");
          continue;
        }
        const done = ledgerCompletion(readFileSync(ledgerFile, "utf8"));
        if (!done.ok) { blocked.push(leaf + " needs " + need + ", whose ledger does not parse"); continue; }
        if (!done.complete) {
          blocked.push(leaf + " needs " + need + ", which is not complete (" + done.unmet + " unmet" +
            (done.abandoned ? ", " + done.abandoned + " abandoned" : "") + ")");
        } else if (needRow.state !== "VERIFIED") {
          blocked.push(leaf + " needs " + need + ", whose ledger is complete but the plan still calls it " +
            needRow.state + "; reconcile the plan before dispatching");
        }
      }
    }
    if (unplanned.length) {
      refuse("not described by the plan for scope " + options.scope + ": " + unplanned.join(", ") +
        "; the tree in " + planFile + " needs a row for every leaf dispatched from it, naming that leaf's ledger, " +
        "so add the row or rename the ledger to match its id before opening a wave");
    }
    if (blocked.length) refuse("unsatisfied dependencies in scope " + options.scope + ": " + blocked.join("; "));
  }
} else if (command === "start") {
  if (options.leaves.length !== 1) die("start requires exactly one --leaf");
  if (options.handle === null) die("start requires --handle");
  if (options.reason !== null) die("start does not accept --reason");
  // An isolation claim is verified, not recorded. A leaf whose isolation
  // silently fell through committed straight to the shared checkout, and the
  // only thing that caught it was the driver auditing git afterwards; naming a
  // workspace here makes that claim checkable at the cheapest moment, before
  // anything is waited on. Verification is opt-in but never soft: a workspace
  // that cannot be proven an exclusive worktree of this repository is refused,
  // because an unverifiable isolation claim recorded as fact is exactly the
  // false paperwork this tool exists to keep out.
  if (options.workspace !== null) {
    const workspace = resolve(options.root, options.workspace);
    let missing = true;
    try { missing = !statSync(workspace).isDirectory(); } catch { missing = true; }
    if (missing) {
      refuse("workspace " + workspace + " does not exist, so " + options.leaves[0] +
        " is not isolated there; create the worktree before recording its start");
    }
    const claimed = exclusiveWorktree(workspace);
    const own = exclusiveWorktree(options.root);
    if (!claimed.ok || !own.ok) {
      refuse("workspace " + workspace + " cannot be verified as a worktree (" +
        (claimed.ok ? own.error : claimed.error) + "); an unverifiable isolation claim is not recorded");
    }
    if (!claimed.exclusive) {
      refuse("workspace " + workspace + " is a repository's main checkout, not an exclusive worktree, so " +
        options.leaves[0] + " would share it with whatever else is worked there");
    }
    if (claimed.common !== own.common) {
      refuse("workspace " + workspace + " belongs to a different repository than " + options.root +
        "; the leaf would be isolated in the wrong tree, which looks like success and protects nothing here");
    }
  }
} else if (command === "return") {
  if (options.leaves.length !== 1) die("return requires exactly one --leaf");
  if (options.handle !== null || options.reason !== null) die("return does not accept --handle or --reason");
} else if (command === "abandon") {
  if (options.leaves.length || options.handle !== null) die("abandon does not accept --leaf or --handle");
  if (options.reason === null || !options.reason.trim()) die("abandon requires --reason");
} else if (options.leaves.length || options.handle !== null || options.reason !== null) {
  die(command + " does not accept --leaf, --handle, or --reason");
}
if (options.workspace !== null && command !== "start") die(command + " does not accept --workspace");

const summary = (wave, id) => {
  const started = Object.keys(wave.started).length;
  const returned = Object.keys(wave.returned).length;
  if (wave.state === "complete") return "COMPLETE " + id + " (" + returned + "/" + wave.leaves.length + " returned)";
  if (wave.state === "abandoned") return "ABANDONED " + id + " (" + started + "/" + wave.leaves.length +
    " started, " + returned + "/" + wave.leaves.length + " returned): " + wave.reason;
  return wave.state.toUpperCase() + " " + id + " (" + started + "/" + wave.leaves.length +
    " started, " + returned + "/" + wave.leaves.length + " returned)";
};

try {
  if (command === "status") {
    const wave = getDispatchWave(options.root, options.scope, options.wave);
    console.log(summary(wave, options.wave));
    process.exit(wave.state === "complete" ? 0 : 1);
  }

  const wave = await updateDispatch(options.root, {
    action: command,
    scope: options.scope,
    wave: options.wave,
    leaves: options.leaves,
    leaf: options.leaves[0],
    handle: options.handle,
    reason: options.reason,
  });
  const started = Object.keys(wave.started).length;
  const returned = Object.keys(wave.returned).length;
  if (command === "open") console.log("OPEN " + options.wave + " (0/" + wave.leaves.length + " started, 0/" + wave.leaves.length + " returned)");
  else if (command === "start") console.log("STARTED " + options.wave + " " + options.leaves[0] + " (" + started + "/" + wave.leaves.length + " started)");
  else if (command === "seal") console.log("SEALED " + options.wave + " (" + started + "/" + wave.leaves.length + " started)");
  else if (command === "abandon") console.log(summary(wave, options.wave));
  else if (wave.state === "complete") console.log("COMPLETE " + options.wave + " (" + returned + "/" + wave.leaves.length + " returned)");
  else console.log("RETURNED " + options.wave + " " + options.leaves[0] + " (" + returned + "/" + wave.leaves.length + " returned)");
} catch (error) {
  die(error.message);
}
