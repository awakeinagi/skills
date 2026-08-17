import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

/** r5b-10: ONE TRIPWIRE MUST NOT FLAG EVERY MAPPING THE CONCEPT HAS.
 *
 * Filed by arm B of run 5 and never independently verified, so this spec
 * is the verification as much as it is the regression pin: fifteen
 * identical `⚠ mapped — divergence flagged below` rows under one
 * concept, burying the concept list, where the OK variant prints the
 * element pair. It reproduces, and the cause is one line — the row asked
 * whether ANY open tripwire's key started with the concept's id, which
 * is true of every mapping the concept has the moment one of them
 * diverges.
 *
 * So the row was making a claim about fourteen mappings that had not
 * diverged, in identical words, with nothing to tell them apart. Both
 * halves are fixed and both are asserted here, because either alone
 * leaves the finding half-standing: the flag is matched on the WHOLE
 * mapping key now, and the flagged row prints its element pair like its
 * quiet neighbours do.
 *
 * FIFTEEN IS THE FILED NUMBER, kept deliberately rather than reduced to
 * the two the logic needs. The finding is about burial, and burial is a
 * property of the count.
 */

const __dirname2 = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname2, "..", "..", "..", "..");
const CANVAS = path.join(REPO, "skills", "wysiwyg-grilling", "scripts",
  "canvas.py");
const N = 15;

/** Seed a project: one concept, two views, N mappings, one divergence.
 *
 * Written through `Store` rather than the HTTP surface so the scene is
 * on disk before the server starts and the page has nothing to race.
 */
const BUILD = `
import json, sys
sys.path.insert(0, ${JSON.stringify(path.dirname(CANVAS))})
import canvas
root = sys.argv[1]
p = canvas.Project(root); p.ensure_tree()
st = canvas.Store(p)
N = ${N}
for aid, nm, ty in (("dash", "Dashboard", "wireframe"), ("flow1", "Flow", "flow")):
    st.apply_batch({"base_revn": st.head_revn(), "artifact": aid,
        "create": {"id": aid, "name": nm, "type": ty,
                   "concept": "trade", "concept_name": "Trade"},
        "ops": [{"op": "add", "element": {
            "type": "rectangle", "id": "%s-%d" % (aid, i),
            "label": "Field %d" % i, "x": 100, "y": 100 + i * 80,
            "width": 160, "height": 48, "role": "node"}}
            for i in range(N)]})
st.apply_batch({"base_revn": st.head_revn(), "artifact": "dash",
    "ops": [{"op": "registry", "action": "add_mapping", "concept": "trade",
             "elements": ["dash#dash-%d" % i, "flow1#flow1-%d" % i]}
            for i in range(N)]})
rec, _ = st.apply_batch({"base_revn": st.head_revn(), "artifact": "dash",
    "ops": [{"op": "mod", "id": "dash-7", "attrs": {"label": "Renamed Field"}}]})
print(json.dumps({"mappings": len(st.registry["mappings"]),
                  "fired": [t["mapping"] for t in rec["tripwires"]]}))
`;

test("one diverged mapping flags one row, and that row says which", async ({ page }) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "railmap-"));
  fs.mkdirSync(path.join(dir, "project_knowledge"), { recursive: true });
  const b = spawnSync("python3", ["-c", BUILD, dir],
    { encoding: "utf-8", timeout: 60_000 });
  if (b.status !== 0)
    throw new Error("seed failed: " + b.stdout + b.stderr);
  const seeded = JSON.parse(b.stdout);
  // the fixture's own preconditions: N mappings on one concept and
  // EXACTLY ONE of them diverged. A seed that fired fifteen tripwires
  // would make fifteen warning rows correct and this test meaningless.
  expect(seeded.mappings).toBe(N);
  expect(seeded.fired).toEqual(["trade:dash#dash-7+flow1#flow1-7"]);

  const s = spawnSync("python3",
    [CANVAS, "--project", dir, "start", "--no-browser"],
    { encoding: "utf-8", timeout: 60_000 });
  const m = /URL=(\S+)/.exec(s.stdout || "");
  if (!m) throw new Error("server did not start: " + s.stderr + s.stdout);
  try {
    await page.goto(m[1]);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 20_000 });
    const rows = page.locator(".registry-concept .map-status");
    await expect.poll(async () => rows.count(), { timeout: 20_000 }).toBe(N);

    const texts = await rows.allInnerTexts();
    const flagged = texts.filter((t) => t.includes("divergence flagged"));
    expect(flagged.length,
      `${flagged.length} of ${N} mapping rows claim a divergence, and one `
      + `mapping diverged. The other rows are a warning about elements `
      + `nobody touched, in words identical to the real one`).toBe(1);

    // the flagged row names its own mapping — the burial was that the
    // warning rows were indistinguishable from each other, so a reader
    // could not tell which pair the divergence was about without
    // scrolling to the tripwire panel and matching ids by hand
    expect(flagged[0]).toContain("dash-7");
    expect(flagged[0]).toContain("flow1-7");

    // and the quiet rows still print their pairs, unchanged
    const quiet = texts.filter((t) => !t.includes("divergence flagged"));
    expect(quiet.length).toBe(N - 1);
    expect(quiet.every((t) => t.includes("↔"))).toBe(true);

    // NO ROW READS THE SAME AS ANOTHER. This is the finding stated
    // directly rather than through its cause: fifteen identical lines
    // are the burial, whatever produced them.
    expect(new Set(texts).size,
      "two mapping rows render identical text").toBe(N);
  } finally {
    spawnSync("python3", [CANVAS, "--project", dir, "stop"],
      { encoding: "utf-8", timeout: 30_000 });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
