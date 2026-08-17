import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { replayChanges } from "../../src/api";

/** THE TWO REPLAYS ARE ONE ALGORITHM AND NOTHING SAID SO.
 *
 * `canvas.py`'s `replay_changes` rebuilds a scene from a save record on
 * the server; `replayChanges` (src/api.ts) does it in the tab, for
 * apply-now on a dirty canvas. They are supposed to agree — a replay is
 * how the tab and the file stay one scene, and element order is paint
 * order, so a disagreement is a differently stacked drawing with nothing
 * saying so.
 *
 * They were wrong TOGETHER on a corrupt `add.index` (both read a
 * negative one as `splice`/`insert` do, an offset from the end), which
 * is bad and quiet. Task 36 fixed the server, which made them wrong
 * DIFFERENTLY — each confident, on the same record. That is the state
 * this spec exists to make impossible to reach again: it runs one
 * crafted record through both implementations and compares the answer,
 * so neither side can be repaired alone.
 *
 * The client function is imported from source rather than driven through
 * the browser on purpose. `replayChanges` takes no DOM and touches no
 * React; a page would add a bundle-freshness variable to a test whose
 * subject is the algorithm. The bundle is built from this file and the
 * suite's other specs exercise it in the tab.
 *
 * THE CASES ARE THE FAULT CLASSES `_add_index` ENUMERATES, not a sample:
 * a legal index, past the end, negative, absent, a string, a boolean and
 * a float. Six of the seven were unreachable through the UI and every
 * one of them is reachable from a hand-edited or partially-written
 * record, which is the population the server-side clamp was written
 * for.
 */

const __dirname2 = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname2, "..", "..", "..", "..");
const CANVAS = path.join(REPO, "skills", "wysiwyg-grilling", "scripts",
  "canvas.py");

/** A scene of four bare elements, in paint order. */
const scene = () => ["a", "b", "c", "d"].map((id) => ({ id, type: "rectangle" }));

/** One `add` op carrying whatever a record might hold in `index`. */
const addOp = (index: unknown) =>
  ({ op: "add", index, element: { id: "new", type: "rectangle" } });

/** Replay a record through `canvas.py` and hand back the id order.
 *
 * Runs the real module rather than a transcription of it — the whole
 * point is that the two sides cannot drift, and a Python copy living in
 * this file would be a third implementation to keep honest.
 */
function serverOrder(changes: unknown[]): string[] {
  const prog = [
    "import json, sys",
    `sys.path.insert(0, ${JSON.stringify(path.dirname(CANVAS))})`,
    "import canvas",
    "els, changes = json.load(sys.stdin)",
    "print(json.dumps([e['id'] for e in "
      + "canvas.replay_changes(els, changes)]))",
  ].join("\n");
  const r = spawnSync("python3", ["-c", prog], {
    input: JSON.stringify([scene(), changes]),
    encoding: "utf-8", timeout: 60_000,
  });
  if (r.status !== 0)
    throw new Error("server replay failed: " + r.stdout + r.stderr);
  return JSON.parse(r.stdout);
}

const CASES: Array<{ name: string; index: unknown; expect: string[] }> = [
  { name: "a legal index", index: 2,
    expect: ["a", "b", "new", "c", "d"] },
  { name: "the end", index: 4, expect: ["a", "b", "c", "d", "new"] },
  { name: "past the end", index: 99, expect: ["a", "b", "c", "d", "new"] },
  { name: "the front", index: 0, expect: ["new", "a", "b", "c", "d"] },
  // the one that made the two sides disagree: `splice(-1)` and
  // `insert(-1)` both mean "second to last", and the server now refuses
  // that reading. -5 is here because the old behaviour answered it
  // DIFFERENTLY again (near the front), so one wrong rule produced two
  // wrong answers depending on the magnitude of a number nobody wrote.
  { name: "negative", index: -1, expect: ["new", "a", "b", "c", "d"] },
  { name: "very negative", index: -5, expect: ["new", "a", "b", "c", "d"] },
  { name: "absent", index: undefined,
    expect: ["a", "b", "c", "d", "new"] },
  { name: "a string", index: "2", expect: ["a", "b", "c", "d", "new"] },
  { name: "a boolean", index: true, expect: ["a", "b", "c", "d", "new"] },
  { name: "a float", index: 2.5, expect: ["a", "b", "c", "d", "new"] },
];

test("both replays rebuild the same scene from the same record", () => {
  for (const c of CASES) {
    const changes = [addOp(c.index)];
    const client = replayChanges(scene(), changes).map((e) => e.id);
    const server = serverOrder(changes);
    expect(server, `${c.name}: the server's own answer moved`)
      .toEqual(c.expect);
    expect(client, `${c.name}: the tab rebuilds a different scene than the `
      + `file — element order is paint order, so this is a differently `
      + `stacked drawing with nothing saying so`).toEqual(server);
  }
});

test("an add above a del lands where it was recorded, on both sides", () => {
  // THE SECOND DIVERGENCE, and this spec found it — the ordering half.
  // `diff_scenes` emits adds ahead of dels and indexes them against the
  // FINISHED list, so replaying in record order inserts into a list that
  // still holds the doomed element and lands one slot short for every
  // deletion below. The server hoists the dels; the client did not, and
  // answered `a,c,x,d` where the file says `a,c,d,x`.
  //
  // This is the smallest record that shows it — one add, one del — and
  // the case is worth naming: it is what an ordinary edit produces, not
  // a corrupt record. The clamp cases above need a hand-edited file to
  // reach; this one needed a user deleting a box and drawing another.
  const changes = [
    { op: "add", index: 3, element: { id: "x", type: "rectangle" } },
    { op: "del", element: { id: "b" } },
  ];
  expect(serverOrder(changes)).toEqual(["a", "c", "d", "x"]);
  expect(replayChanges(scene(), changes).map((e) => e.id),
    "the tab replayed in record order and put the new element one slot "
    + "short — element order is paint order").toEqual(["a", "c", "d", "x"]);
});

test("a RUN of adds keeps its recorded order on both sides", () => {
  // The ascending sort, which the hoist alone does not buy, pinned on a
  // case that can tell them apart: two adds arriving DESCENDING, with no
  // del in the record at all, so the hoist is inert and only the sort
  // decides. Inserting the lower index last shifts the higher one that
  // was already placed, and each lands in the other's neighbourhood —
  // `a,y,b,x,c,d` against `a,y,b,c,x,d`. `replay_changes` needs this
  // more for the INVERSE list than the forward one, since inverses are
  // built by reversing the change list and hand back re-insertions in
  // descending order, and the inverse list is the revert path.
  const changes = [
    { op: "add", index: 3, element: { id: "x", type: "rectangle" } },
    { op: "add", index: 1, element: { id: "y", type: "rectangle" } },
  ];
  expect(serverOrder(changes)).toEqual(["a", "y", "b", "x", "c", "d"]);
  expect(replayChanges(scene(), changes).map((e) => e.id))
    .toEqual(serverOrder(changes));
});
