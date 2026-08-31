// Read-only git access for ownership auditing. Node 16+, no dependencies.
//
// Every other record unlazy keeps is written by the agent, or by a tool the
// agent chose to run, so it states an intention. Version-control history states
// what happened. It is the only evidence here that the thing being checked did
// not author, which is why the ownership audit reads it rather than trusting a
// leaf's own account of what it touched.
//
// The surface stays deliberately narrow: two questions, no mutation, and no
// assumption that git exists or that the workspace is a repository. A caller
// that cannot get an answer is told so explicitly, because an audit that could
// not run has measured nothing and must never read as one that passed.

import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const TIMEOUT_MS = 30000;
const STATE_PREFIX = ".unlazy/";

// A reader who cannot run the audit has to act on this sentence, so it says
// which of the two situations they are in. `spawnSync git ENOENT` is the runtime
// describing its own call rather than the problem, and it reads as a bug in
// unlazy instead of a missing program.
function describe(error) {
  return error && error.code === "ENOENT"
    ? "git was not found on PATH"
    : "git could not be started: " + (error ? error.message : "unknown error");
}

function run(dir, args) {
  let result;
  try {
    result = spawnSync("git", args, { cwd: dir, encoding: "utf8", timeout: TIMEOUT_MS, windowsHide: true });
  } catch (error) {
    return { ok: false, error: describe(error) };
  }
  if (result.error) return { ok: false, error: describe(result.error) };
  if (result.status !== 0) {
    const detail = String(result.stderr || "").trim().split("\n")[0] || ("git exited " + result.status);
    return { ok: false, error: detail };
  }
  return { ok: true, out: String(result.stdout || "") };
}

/**
 * Resolve the commit a workspace currently sits on.
 *
 * Returns `{ ok: true, commit }`, or `{ ok: false, error }` when the directory
 * is not a repository, git is absent, or the repository has no commits yet.
 */
export function headCommit(dir) {
  const result = run(dir, ["rev-parse", "HEAD"]);
  if (!result.ok) return result;
  const commit = result.out.trim();
  if (!/^[0-9a-f]{7,64}$/i.test(commit)) return { ok: false, error: "unreadable HEAD" };
  return { ok: true, commit };
}

/**
 * Report whether `dir` is a working tree of its own rather than a shared one.
 *
 * `changedSince` ends its range at the working tree's `HEAD`, so the range
 * describes one leaf only when that `HEAD` advances for one leaf. A linked
 * worktree carries its own `HEAD`; the main checkout's is shared with whatever
 * else is worked there. Callers need this before reading a change set as a
 * statement about who wrote it.
 *
 * Returns `{ ok: true, exclusive }`, or `{ ok: false, error }` when git cannot
 * answer, which callers must treat as "not established" rather than as a no.
 */
export function exclusiveWorktree(dir) {
  const own = run(dir, ["rev-parse", "--absolute-git-dir"]);
  if (!own.ok) return own;
  const common = run(dir, ["rev-parse", "--git-common-dir"]);
  if (!common.ok) return common;
  const here = own.out.trim();
  const shared = resolve(dir, common.out.trim());
  if (!here || !shared) return { ok: false, error: "unreadable git directory" };
  // `common` names the repository the tree belongs to, so two answers can be
  // compared: same common directory, same repository. A dispatch that records a
  // workspace needs that comparison, or a worktree of the wrong repository
  // would pass the exclusivity test while isolating nothing relevant.
  return { ok: true, exclusive: resolve(here) !== shared, common: shared };
}

/**
 * List every repository-relative path that changed in `dir` since `base`.
 *
 * Committed and uncommitted changes both count, and so do untracked files: a
 * leaf that never commits would otherwise present an empty change set, which
 * reads as "touched nothing" rather than "produced nothing". Both sides of a
 * rename are reported, because a rename moves a path out of one declaration and
 * into another. unlazy's own state directory is excluded; it is bookkeeping
 * every leaf writes, not work any leaf owns.
 */
export function changedSince(dir, base) {
  if (!/^[0-9a-f]{7,64}$/i.test(String(base || ""))) return { ok: false, error: "no pinned starting commit" };
  const committed = run(dir, ["diff", "--name-only", base, "HEAD"]);
  if (!committed.ok) return committed;
  const working = run(dir, ["status", "--porcelain", "--untracked-files=all"]);
  if (!working.ok) return working;

  const files = new Set();
  const add = (value) => {
    const path = String(value || "").trim().replace(/^"(.*)"$/, "$1");
    if (path && !path.startsWith(STATE_PREFIX)) files.add(path);
  };
  for (const line of committed.out.split("\n")) add(line);
  for (const line of working.out.split("\n")) {
    if (!line.trim()) continue;
    const entry = line.slice(3);
    const arrow = entry.indexOf(" -> ");
    if (arrow >= 0) { add(entry.slice(0, arrow)); add(entry.slice(arrow + 4)); }
    else add(entry);
  }
  return { ok: true, files: [...files].sort() };
}
