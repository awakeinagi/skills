// Reads the dependency tree out of a scope's PLAN.md. Zero dependencies. Node 16+.
//
// The tree is hand-written markdown whose dot leaders are decoration, so this
// does not parse the layout. It extracts the tokens that carry meaning, an id,
// a ledger path, the id-shaped tokens listed under Needs, and a state word, and
// ignores everything between them. Arbitrary runs of dots are then irrelevant
// and a plan stays readable to a human without becoming a fragile format.
//
// Parsing is deliberately confined to the `## Tree` section. Other sections
// carry ordinary prose bullets, and a parser loose enough to read those would
// invent dependencies out of sentences.
//
// Nothing here decides completion. It reports what the plan declares, plus the
// faults a plan can carry on its own terms: an unreadable row, an unknown
// dependency, a duplicate id, a cycle, or a state outside the vocabulary. A
// plan carrying any of those is not finished being planned.

const ROW = /^(\s*)-\s+(\d+(?:\.\d+)*)\s+(.*)$/;
const LEDGER = /(?:gates\/)?(?:leaf|node)-[A-Za-z0-9._-]+\.md|GATES\.md/;
const ID_TOKEN = /\d+(?:\.\d+)*/g;
const LEAF_STATES = new Set(["WAITING", "READY", "IN-FLIGHT", "VERIFIED", "ABANDONED"]);
const BRANCH_STATES = new Set(["OPEN", "VERIFIED", "ABANDONED"]);

function section(text, heading) {
  const lines = String(text).split(/\r?\n/);
  const start = lines.findIndex((line) => new RegExp("^##\\s+" + heading + "\\s*$", "i").test(line));
  if (start === -1) return null;
  const rest = lines.slice(start + 1);
  const end = rest.findIndex((line) => /^##\s/.test(line));
  return { lines: end === -1 ? rest : rest.slice(0, end), offset: start + 2 };
}

/**
 * Read a PLAN.md into declared rows plus the faults it carries.
 *
 * Returns `{ rows, byId, errors, warnings }`. Every error means planning is not
 * complete: the tree cannot be trusted to describe what depends on what. A
 * warning marks a plan that parses but contradicts itself, such as a row called
 * READY whose own dependency is not verified.
 */
export function readPlan(text) {
  const errors = [];
  const warnings = [];
  const rows = [];
  const tree = section(text, "Tree");
  if (!tree) {
    return { rows, byId: new Map(), errors: [{ line: 0, detail: "no '## Tree' section" }], warnings };
  }

  let fenced = false;
  tree.lines.forEach((raw, index) => {
    const line = tree.offset + index;
    if (/^\s*```/.test(raw)) { fenced = !fenced; return; }
    if (fenced || !raw.trim()) return;
    if (!/^\s*-\s/.test(raw)) return;
    const match = ROW.exec(raw);
    if (!match) {
      // Name the rule, not the symptom. "no leading id" reads as a spacing or
      // formatting complaint, so a reader who named a row for what it builds
      // has no way to learn from it that ids are a character class, and goes
      // looking through documentation for a rule the message could have stated.
      const token = /^\s*-\s+(\S+)/.exec(raw);
      errors.push({ line, detail: "tree row id must be digits and dots only, like 1.1.2" +
        (token ? ", got " + JSON.stringify(token[1]) : "") +
        "; put the descriptive name in the row's title, which is free text, and match the ledger filename to the id" });
      return;
    }
    const [, indent, id, rest] = match;
    const ledger = LEDGER.exec(rest);
    if (!ledger) {
      errors.push({ line, detail: id + ": no ledger path on the row" });
      return;
    }
    const path = ledger[0];
    const stateMatch = /State:\s*([A-Za-z][A-Za-z-]*)/.exec(rest);
    if (!stateMatch) {
      errors.push({ line, detail: id + ": no State field" });
      return;
    }
    const state = stateMatch[1].toUpperCase();
    const needsMatch = /Needs:\s*([\s\S]*?)(?=State:|$)/.exec(rest);
    const needs = needsMatch ? (String(needsMatch[1]).match(ID_TOKEN) || []) : [];
    const branch = path === "GATES.md" || /(?:^|\/)node-/.test(path);
    rows.push({ id, path, needs, state, branch, line, depth: indent.length });
  });

  if (!rows.length && !errors.length) errors.push({ line: tree.offset, detail: "the tree lists no rows" });

  const byId = new Map();
  for (const row of rows) {
    if (byId.has(row.id)) errors.push({ line: row.line, detail: row.id + ": duplicate id" });
    else byId.set(row.id, row);
    const vocabulary = row.branch ? BRANCH_STATES : LEAF_STATES;
    if (!vocabulary.has(row.state)) {
      errors.push({ line: row.line, detail: row.id + ": state " + row.state + " is not one of " + [...vocabulary].join(", ") });
    }
    // The row already names its ledger, so the id is checked against it rather
    // than used to guess it. Picking one silently would let a plan point a
    // dependency at a file that belongs to a different piece of work.
    const named = /(?:^|\/)(?:leaf|node)-(.+)\.md$/.exec(row.path);
    if (named && named[1] !== row.id) {
      errors.push({ line: row.line, detail: row.id + ": ledger " + row.path + " names a different id" });
    }
    if (row.needs.includes(row.id)) errors.push({ line: row.line, detail: row.id + ": depends on itself" });
  }
  for (const row of rows) {
    for (const need of row.needs) {
      if (!byId.has(need)) errors.push({ line: row.line, detail: row.id + ": Needs " + need + ", which the tree does not list" });
    }
  }
  for (const cycle of findCycles(rows, byId)) {
    errors.push({ line: byId.get(cycle[0]).line, detail: "dependency cycle: " + cycle.join(" -> ") });
  }
  for (const row of rows) {
    if (row.branch || row.state === "WAITING" || row.state === "ABANDONED") continue;
    const blocking = row.needs.filter((need) => byId.get(need) && byId.get(need).state !== "VERIFIED");
    if (blocking.length) {
      warnings.push({ line: row.line, detail: row.id + " is " + row.state + " while it Needs " + blocking.join(", ") + ", not yet VERIFIED" });
    }
  }
  return { rows, byId, errors, warnings };
}

function findCycles(rows, byId) {
  const found = [];
  const seen = new Set();
  const stack = [];
  const onStack = new Set();
  const walk = (id) => {
    if (onStack.has(id)) {
      found.push([...stack.slice(stack.indexOf(id)), id]);
      return;
    }
    if (seen.has(id)) return;
    seen.add(id);
    stack.push(id);
    onStack.add(id);
    for (const need of (byId.get(id) || { needs: [] }).needs) {
      if (byId.has(need)) walk(need);
    }
    stack.pop();
    onStack.delete(id);
  };
  for (const row of rows) walk(row.id);
  return found;
}

/**
 * Find the row that owns a leaf ledger, by the path the plan wrote down.
 *
 * Falls back to the `leaf-<id>.md` convention only when no row names the file,
 * so a plan that spells out an unusual path still resolves.
 */
export function rowForLedger(plan, ledgerName) {
  const bare = String(ledgerName).replace(/\.md$/i, "");
  const byPath = plan.rows.find((row) => row.path.replace(/^gates\//, "").replace(/\.md$/i, "") === bare);
  if (byPath) return byPath;
  const suffix = /^(?:leaf|node)-(.+)$/.exec(bare);
  return suffix ? plan.byId.get(suffix[1]) || null : null;
}
