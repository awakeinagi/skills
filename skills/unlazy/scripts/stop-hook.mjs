#!/usr/bin/env node
// Claude Code Stop hook for one unlazy pipeline. Zero dependencies. Node 16+.

import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { resolve } from "node:path";
import {
  UNLAZY_DIR, gateState, hookStatePath, parseGates, qualify, resolveTarget,
  sha256, validateScopeId, withFileLock, writeAtomic,
} from "./lib/gates.mjs";
import { dispatchStatus } from "./lib/dispatch.mjs";

const MAX_BLOCKS = 6;
// A hard ceiling on blocks per session regardless of how the resolved state
// moves. MAX_BLOCKS releases an agent that is visibly stuck; this releases one
// whose churn keeps rearming that counter, so being wedged is not reachable by
// either route.
const MAX_SESSION_BLOCKS = 24;
const safeHostText = (value, max = 500) => String(value)
  .replace(/[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, " ")
  .replace(/\s+/g, " ")
  .trim()
  .slice(0, max);

function normalizeHookState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.schema !== 1 ||
      !value.sessions || typeof value.sessions !== "object" || Array.isArray(value.sessions)) {
    return { schema: 1, sessions: {} };
  }
  const sessions = {};
  for (const [key, current] of Object.entries(value.sessions)) {
    if (!/^[a-f0-9]{24}$/.test(key) || !current || typeof current !== "object" || Array.isArray(current) ||
        !/^[a-f0-9]{24}$/.test(String(current.hash || "")) ||
        !Number.isInteger(current.blocks) || current.blocks < 0 ||
        typeof current.updatedAt !== "string" || Number.isNaN(Date.parse(current.updatedAt))) continue;
    // `total` counts every block this session has ever taken, across hash
    // changes. State written before it existed carries none, so seed it from the
    // consecutive count, which is a lower bound on what has already happened.
    sessions[key] = {
      ...current,
      total: Number.isInteger(current.total) && current.total >= 0 ? current.total : current.blocks,
    };
  }
  return { schema: 1, sessions };
}

const args = process.argv.slice(2);
if (args.includes("--help") || args.includes("-h")) {
  // Every other script here answers --help, and three separate readers trying to
  // exercise this one had to guess its input from elsewhere because it answered
  // nothing. The stdin contract is the host's, so name it rather than restate it.
  process.stdout.write([
    "usage: stop-hook.mjs --unlazy-hook-v2 [--scope ID]",
    "",
    "A Claude Code Stop hook. It is invoked by the host, not by hand, and reads",
    "the host's Stop event as JSON on stdin. Only two fields are used:",
    "",
    "  session_id   resolves which pipeline this session is bound to (--bind)",
    "  cwd          repository root to resolve ledgers and dispatch state from",
    "",
    "Both are optional: an empty object still resolves a pipeline from the",
    "current directory. Nothing here executes a CHECK line.",
    "",
    "Writes {\"decision\":\"block\"} while the resolved pipeline has unmet gates or",
    "incomplete waves, prints nothing when everything is met, and always exits 0;",
    "the host reads the JSON body, not the exit status. The block message carries",
    "how many blocks remain before each release ceiling.",
    "",
    "Install and remove it with install-hooks.mjs; see SECURITY.md first.",
  ].join("\n") + "\n");
  process.exit(0);
}
const scopeIndex = args.indexOf("--scope");
const scopeArg = scopeIndex === -1 ? null : args[scopeIndex + 1];

const allow = (message) => {
  if (message) console.log(JSON.stringify({ systemMessage: message }));
  process.exit(0);
};

if (scopeIndex !== -1 && (!scopeArg || validateScopeId(scopeArg))) {
  allow("unlazy: installed hook has an invalid --scope value; not blocking.");
}

let payload = {};
try { payload = JSON.parse(readFileSync(0, "utf8") || "{}"); }
catch { allow(null); }

const root = resolve(typeof payload.cwd === "string" && payload.cwd ? payload.cwd : process.cwd());
const sessionId = payload.session_id || payload.sessionId || "anonymous";
const sessionKey = sha256(String(sessionId)).slice(0, 24);
const target = resolveTarget({ root, scope: scopeArg, sessionId });

if (target.ambiguous) {
  allow("unlazy: " + target.ambiguous.length + " pipelines under " + UNLAZY_DIR +
    "/ (" + target.ambiguous.join(", ") + ") and none bound to this session; not blocking.");
}
if (target.error && !target.ambiguous) allow("unlazy: " + safeHostText(target.error) + "; not blocking.");

const statePath = hookStatePath(root, target.scope);

async function clearSessionState() {
  if (!existsSync(statePath)) return;
  try {
    await withFileLock(root, statePath, () => {
      let state = { schema: 1, sessions: {} };
      try { state = JSON.parse(readFileSync(statePath, "utf8")); } catch { /* replace invalid local state */ }
      state = normalizeHookState(state);
      delete state.sessions[sessionKey];
      if (!Object.keys(state.sessions).length) {
        try { unlinkSync(statePath); } catch { /* already absent */ }
      } else writeAtomic(statePath, JSON.stringify(state, null, 2) + "\n", { root });
    }, { timeoutMs: 10000 });
  } catch {
    // State cleanup must never trap a session after the gates are complete.
  }
}

const dispatch = dispatchStatus(root, target.scope);

if (!target.files.length && !dispatch.blocking.length && !dispatch.abandoned.length) {
  await clearSessionState();
  allow(null);
}

const unmet = [...dispatch.blocking];
const invalid = [];
const handoffs = [...dispatch.abandoned];
const handoffMessage = () => {
  if (!handoffs.length) return "";
  const shown = handoffs.slice(0, 5).join(", ") +
    (handoffs.length > 5 ? ", +" + (handoffs.length - 5) + " more" : "");
  return " HANDOFF REQUIRED: " + handoffs.length + " abandoned item(s): " + safeHostText(shown) + ".";
};
// The loop guard compares resolved gate state between stops, not raw bytes.
// Byte comparison counted any edit as progress: a comment, a reflowed line, or
// the checker rewriting an evidence line with a fresh PATH hash. That rearmed
// the guard indefinitely, so the six-block release could only ever fire for an
// agent doing literally nothing, which is the one case least in need of it.
// Dispatch issue strings encode only canonical state and counts, not raw JSON
// bytes or timestamps, so metadata-only edits do not reset the same guard.
const resolved = [...dispatch.resolved];
for (const file of [...target.files].sort()) {
  let text;
  try { text = readFileSync(file, "utf8"); }
  catch (error) {
    invalid.push(qualify(file, "PARSE") + " unreadable: " + safeHostText(error.message));
    resolved.push(qualify(file, "PARSE") + "=unreadable");
    continue;
  }
  const doc = parseGates(text);
  if (doc.errors.length) {
    invalid.push(qualify(file, "PARSE") + " " + doc.errors.slice(0, 2).map((error) => safeHostText(error)).join("; "));
    // Record only that the ledger is invalid. Diagnostic text carries line
    // numbers, which shift on an unrelated edit and would restore byte coupling.
    resolved.push(qualify(file, "PARSE") + "=invalid");
    continue;
  }
  for (const gate of doc.gates) {
    const state = gateState(gate, doc.abandoned);
    resolved.push(qualify(file, gate.id) + "=" + state);
    if (state === "unmet" || state === "unmet-no-evidence") unmet.push(qualify(file, gate.id));
    else if (state === "abandoned") handoffs.push(qualify(file, gate.id));
  }
}

if (!unmet.length && !invalid.length) {
  await clearSessionState();
  if (!handoffs.length) allow(null);
  const where = target.scope ? " [scope " + target.scope + "]" : "";
  allow("unlazy" + where + ":" + handoffMessage());
}

const progressHash = sha256(resolved.sort().join("\0")).slice(0, 24);
let sessionState;
try {
  sessionState = await withFileLock(root, statePath, () => {
    let state = { schema: 1, sessions: {} };
    try { state = JSON.parse(readFileSync(statePath, "utf8")); } catch { /* new or corrupt local state */ }
    state = normalizeHookState(state);
    let current = state.sessions[sessionKey];
    // Carry the lifetime count across the reset. The consecutive counter is
    // keyed to the resolved state, so any change to it rearms the guard from
    // zero, and a loop that keeps restructuring the ledger without converging
    // never reaches six in a row: renaming an unmet gate each round blocked
    // indefinitely under test. The consecutive rule stays, because it is the one
    // that releases quickly for an agent that is genuinely stuck; the lifetime
    // count is what makes the guard terminate at all.
    const total = (current && Number.isInteger(current.total) ? current.total : 0) + 1;
    if (!current || current.hash !== progressHash) current = { hash: progressHash, blocks: 0 };
    current.total = total;
    current.blocks += 1;
    current.updatedAt = new Date().toISOString();
    state.sessions[sessionKey] = current;
    // Bound abandoned session debris without mixing counters between sessions.
    const entries = Object.entries(state.sessions).sort((a, b) => String(b[1].updatedAt).localeCompare(String(a[1].updatedAt)));
    state.sessions = Object.fromEntries(entries.slice(0, 64));
    writeAtomic(statePath, JSON.stringify(state, null, 2) + "\n", { root });
    return current;
  }, { timeoutMs: 10000 });
} catch (error) {
  allow("unlazy: could not update the serialized hook state (" + safeHostText(error.message) + "); not blocking to avoid a trap.");
}

const where = target.scope ? " [scope " + target.scope + "]" : "";
const outstanding = [...invalid, ...unmet].map((item) => safeHostText(item));
if (sessionState.blocks > MAX_BLOCKS) {
  allow("unlazy: releasing after " + MAX_BLOCKS + " blocks without gate progress" + where +
    "; " + outstanding.length + " item(s) remain (" + outstanding.slice(0, 4).join(", ") + ")." +
    handoffMessage());
}
// The ceiling the consecutive rule cannot provide. It counts every block this
// session has taken however much the resolved state moved, so a session that
// keeps changing shape without finishing still terminates.
if (sessionState.total > MAX_SESSION_BLOCKS) {
  allow("unlazy: releasing after " + MAX_SESSION_BLOCKS + " blocks in this session" + where +
    "; the resolved state kept changing, so the no-progress guard never armed, and " +
    outstanding.length + " item(s) still remain (" + outstanding.slice(0, 4).join(", ") + ")." +
    handoffMessage());
}

const list = outstanding.slice(0, 5).join(", ") + (outstanding.length > 5 ? ", +" + (outstanding.length - 5) + " more" : "");
console.log(JSON.stringify({
  decision: "block",
  // Say how close the release is. Without it the only way to learn how many
  // blocks remain was reading the state file's undocumented shape, which leaves
  // someone who just wants to stop for the day with no idea whether they are one
  // block from release or twenty.
  reason: "unlazy" + where + ": " + outstanding.length + " gate/ledger/dispatch item(s) need work: " + list +
    ". Run gate-check.mjs --status to inspect without execution. To run inherited CHECK lines, inspect them and use --approve. " +
    "Use ABANDON: <id> <non-blank reason> only when a gate is genuinely impossible. (block " +
    sessionState.blocks + " of " + MAX_BLOCKS + " at this state, " + sessionState.total + " of " +
    MAX_SESSION_BLOCKS + " this session; either ceiling releases the stop.)" + handoffMessage(),
}));
process.exit(0);
