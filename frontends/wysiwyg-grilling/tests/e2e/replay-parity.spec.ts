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
 * a legal index, the end, past the end, the front, negative, absent, a
 * string, a boolean, and floats BOTH WAYS — fractional and integral.
 * Every one is reachable from a hand-edited or partially-written record,
 * which is the population the server-side clamp was written for, and
 * only two are reachable through the UI.
 *
 * EACH CASE IS RAW JSON TEXT AND BOTH SIDES PARSE THE SAME BYTES. That
 * is not fussiness, it is the only way this file can see its own
 * subject: `JSON.stringify({index: 2.0})` emits `{"index":2}`, so a case
 * built as a JavaScript object loses its float-ness before Python ever
 * reads it, and the spec would test a record that cannot exist on disk.
 * The first cut of this file did exactly that and reported parity over
 * the one class where the two sides genuinely disagreed (review
 * MAJOR-1). `2.0` is an integral float to Python and indistinguishable
 * from `2` to JavaScript after `JSON.parse` — which is why the fix for
 * that class had to be server-side, and why the two `*.0` cases below
 * are the load-bearing ones.
 */

const __dirname2 = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname2, "..", "..", "..", "..");
const CANVAS = path.join(REPO, "skills", "wysiwyg-grilling", "scripts",
  "canvas.py");

/** A scene of four bare elements, in paint order. */
const scene = () => ["a", "b", "c", "d"].map((id) => ({ id, type: "rectangle" }));

/** A one-`add` record as RAW JSON TEXT, carrying `index` verbatim.
 *
 * `token` is spliced in unquoted, so a case can say `2.0` and mean it.
 * `null` here means the field is absent entirely, which is a different
 * record from one carrying JSON `null` — both land at the end, and both
 * are cases below.
 */
const record = (token: string | null) =>
  `[{"op": "add"${token === null ? "" : `, "index": ${token}`}, `
  + `"element": {"id": "new", "type": "rectangle"}}]`;

/** Replay a record through `canvas.py` and hand back the id order.
 *
 * Runs the real module rather than a transcription of it — the whole
 * point is that the two sides cannot drift, and a Python copy living in
 * this file would be a third implementation to keep honest. The record
 * arrives as text and `json.load` parses it, exactly as reading one off
 * disk does.
 */
function serverOrder(recordJson: string): string[] {
  const prog = [
    "import json, sys",
    `sys.path.insert(0, ${JSON.stringify(path.dirname(CANVAS))})`,
    "import canvas",
    "els, changes = json.load(sys.stdin)",
    "print(json.dumps([e['id'] for e in "
      + "canvas.replay_changes(els, changes)]))",
  ].join("\n");
  const r = spawnSync("python3", ["-c", prog], {
    input: `[${JSON.stringify(scene())}, ${recordJson}]`,
    encoding: "utf-8", timeout: 60_000,
  });
  if (r.status !== 0)
    throw new Error("server replay failed: " + r.stdout + r.stderr);
  return JSON.parse(r.stdout);
}

const AT_2 = ["a", "b", "new", "c", "d"];
const AT_END = ["a", "b", "c", "d", "new"];
const AT_FRONT = ["new", "a", "b", "c", "d"];

const CASES: Array<{ name: string; token: string | null; expect: string[] }> = [
  { name: "a legal index", token: "2", expect: AT_2 },
  { name: "the end", token: "4", expect: AT_END },
  { name: "past the end", token: "99", expect: AT_END },
  { name: "the front", token: "0", expect: AT_FRONT },
  // THE CHARTERED NEGATIVE CLASS: `splice(-1)` and `insert(-1)` both
  // mean "second to last", and both sides now refuse that reading and
  // clamp to the front. This is the case that discriminates — it is the
  // one that failed against the unfixed client.
  { name: "negative", token: "-1", expect: AT_FRONT },
  // -5 does NOT discriminate and is kept anyway, with the reason said
  // out loud rather than implied: an out-of-range negative reaches the
  // front under `splice` too, so this case passes against the unfixed
  // client as readily as the fixed one. It documents that the old rule
  // gave two different wrong answers by magnitude — which is why the
  // clamp exists — and `negative` above is what actually pins it.
  { name: "very negative", token: "-5", expect: AT_FRONT },
  { name: "absent", token: null, expect: AT_END },
  { name: "JSON null", token: "null", expect: AT_END },
  { name: "a string", token: "\"2\"", expect: AT_END },
  { name: "a boolean", token: "true", expect: AT_END },
  // BOTH DIRECTIONS OF THE FLOAT CLASS (review MAJOR-1). A fractional
  // float is not a position on either side and lands at the end. An
  // INTEGRAL float is the one JavaScript cannot see: `2.0` parses to
  // the same number as `2`, so the client places it at 2 and nothing on
  // that side can know it was written with a decimal point. The server
  // used to refuse the whole float type and append instead — a silent
  // disagreement on an ordinary-looking record — so it now accepts
  // integral floats and the two sides land together.
  { name: "a fractional float", token: "2.5", expect: AT_END },
  { name: "an INTEGRAL float", token: "2.0", expect: AT_2 },
  // the chartered negative class re-entering as a float, which is what
  // made this more than a curiosity: refused-as-float it appended,
  // read-as--1 it clamped to the front, and the record looks innocuous
  { name: "an integral NEGATIVE float", token: "-1.0", expect: AT_FRONT },
  { name: "an integral float past the end", token: "99.0", expect: AT_END },
];

test("both replays rebuild the same scene from the same record", () => {
  for (const c of CASES) {
    const json = record(c.token);
    // the client parses the same bytes the server does — see the header
    const client = replayChanges(scene(), JSON.parse(json)).map((e) => e.id);
    const server = serverOrder(json);
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
  // whole-number indices, so `stringify` is lossless here — the float
  // cases above are the ones that have to be written as text
  const json = JSON.stringify([
    { op: "add", index: 3, element: { id: "x", type: "rectangle" } },
    { op: "del", element: { id: "b" } },
  ]);
  expect(serverOrder(json)).toEqual(["a", "c", "d", "x"]);
  expect(replayChanges(scene(), JSON.parse(json)).map((e) => e.id),
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
  const json = JSON.stringify([
    { op: "add", index: 3, element: { id: "x", type: "rectangle" } },
    { op: "add", index: 1, element: { id: "y", type: "rectangle" } },
  ]);
  expect(serverOrder(json)).toEqual(["a", "y", "b", "x", "c", "d"]);
  expect(replayChanges(scene(), JSON.parse(json)).map((e) => e.id))
    .toEqual(serverOrder(json));
});
