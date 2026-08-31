import { testTextClip as test, expect } from "./harness";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

/* THE AXIS NO INSTRUMENT IN THIS TREE HAS (mutant-curator, 2026-08-31).
 *
 * `pin-untouched-captions.spec.ts` is the browser referee for what the
 * client does to a caption's WIDTH. Every caption it records has the same
 * height change — `dh = -0.4`, nine of nine — because every caption in its
 * seed is one line in both wraps. So a rule keyed on the caption's own
 * HEIGHT is invisible to it, to the 735-cell grid (whose client door never
 * changes a caption's height at all) and to the 101,310-cell sweep (whose
 * axes are container width, caption width, re-measure and drag, and which
 * has no `dh` axis).
 *
 * THIS SPEC EXISTS TO PUT A `dh` IN A CAPTURE. It seeds a box narrow
 * enough that the server's own wrap and the client's disagree about the
 * LINE COUNT — the disagreement `test_the_two_wraps_disagree_about_this
 * _label` already pins and `choppedAWord` in `App.tsx` exists for — so the
 * tab posts a caption tens of pixels shorter than the one on disk.
 *
 * AND IT WITNESSES THE TWO HALVES TOGETHER, which is the whole finding: in
 * the same caption, in the same load, `dh != 0` and `dy == 0`. A tab
 * re-measures a caption's height and does NOT move it vertically. Any rule
 * that predicts a vertical displacement from `dh` is therefore describing
 * something no client does, and it will absorb a real user drag of exactly
 * that size.
 *
 * WHAT THIS SPEC DOES NOT DO, said rather than left as an omission: it
 * does not perform the drag. The absorption bites at exactly one
 * magnitude, `round(-dh/2)`, and a hand drag landing on exactly that pixel
 * is luck rather than evidence. The model tier applies the shove to the
 * scene the tab POSTED, which is the same protocol the sibling class's
 * positioned-caption pole has used since it was written. What needs a
 * browser is the geometry — that a real client really does re-measure a
 * caption's height by 21 pixels while leaving its `y` alone — and that is
 * what is recorded here.
 *
 * VOIDS ITSELF rather than writing a capture nobody can trust: gated on
 * the seed, on the tab having loaded the boxes, on at least one caption
 * whose re-measure is big enough to absorb a whole pixel, and on the POST
 * having been observed. A failed gate writes NO capture, which the model
 * tier reports as "the browser never spoke" rather than as a pass. */

type El = { id: string; type: string; x: number; y: number;
            w: number; h: number; containerId?: string | null };

/* TWO BOXES AND THE FIXTURE'S OWN THIRD, which is the smallest population
 * that has both poles in it.
 *
 *  - `w0` is 120 wide and its caption wraps to THREE lines on the server
 *    and two in the tab, so its `dh` is about -22 and the drag it absorbs
 *    is 11 whole pixels. It is the red's subject.
 *  - `w1` is 160 wide and its caption wraps to two lines in both, so its
 *    `dh` is the ordinary -0.4 that every caption in the sibling capture
 *    has. It is the control: the same shove, a caption the client did not
 *    re-wrap, and the record must name it.
 *
 * The fixture seeds `reported-row` at 640 wide with the same text on one
 * line, so a third `dh = -0.4` caption is present without costing an op. */
const BOXES = [
  { id: "w0", width: 120, height: 120,
    label: "Email landlord about the repair" },
  { id: "w1", width: 160, height: 90, label: "Reconcile the statement" },
];
/* The gesture goes on the CONTROL box, so the caption the reds are about
 * belongs to a box nobody touched in the tab either. */
const DRAGGED = "w1";
const REWRAP_UNTOUCHED = ["w0"];
const ARTIFACT = "text-clip";
const REPO = path.resolve(
  path.dirname(new URL(import.meta.url).pathname), "..", "..", "..", "..");
const CAPTURE = path.join(REPO, "tests", "browser_rewrap_capture.json");
const BUILD_STAMP = path.join(
  REPO, "skills", "wysiwyg-grilling", "scripts", "web", "build-stamp.json");

/* Excalidraw stamps a fresh `versionNonce` and `updated` on every element
 * on every load, so two byte-identical runs write captures differing in
 * lines of pure churn — and a tracked capture whose re-record diffs to
 * noise is one nobody reads. Neither field is in
 * `DEFAULT_SIGNIFICANT_ATTRS`. Same treatment as the sibling spec. */
const CHURN = ["versionNonce", "updated"] as const;

/** Strip the two fields Excalidraw re-stamps on every load.
 *
 * @param els - Elements as they came off the wire or off disk.
 * @returns The same elements without the churn fields, so a re-record
 *   diffs to nothing unless the client's behaviour actually moved.
 */
function stable(els: Array<Record<string, unknown>>
): Array<Record<string, unknown>> {
  return els.map((e) => {
    const out = { ...e };
    for (const f of CHURN) delete out[f];
    return out;
  });
}

/** Digest the built app bundle, so a capture cannot outlive the client
 * whose behaviour it is testimony about.
 *
 * @returns The build stamp's `source_hash`, or `"unstamped"` when the
 *   tracked build carries no stamp — reported rather than thrown, since
 *   the model tier is the layer that decides what that is worth.
 */
function appBuild(): string {
  try {
    return String(JSON.parse(fs.readFileSync(BUILD_STAMP, "utf-8"))
      .source_hash || "unstamped");
  } catch { return "unstamped"; }
}

const scene = (page: import("@playwright/test").Page): Promise<El[]> =>
  page.evaluate(() =>
    (window as unknown as { excalidrawAPI: {
      getSceneElements: () => Array<Record<string, unknown>> } })
      .excalidrawAPI.getSceneElements()
      .filter((e) => !(e as { isDeleted?: boolean }).isDeleted)
      .map((e) => {
        const r = e as Record<string, number | string | null | undefined>;
        return {
          id: String(r.id), type: String(r.type),
          x: Number(r.x), y: Number(r.y),
          w: Number(r.width) || 0, h: Number(r.height) || 0,
          containerId: (r.containerId as string | null) ?? null,
        };
      }));

test("does the tab move a caption whose height it changed?",
  async ({ page, canvas }) => {
    const bpath = path.join(canvas.project, "pin-rewrap.json");
    fs.writeFileSync(bpath, JSON.stringify({
      base_revn: 1, artifact: ARTIFACT,
      ops: BOXES.map((b, i) => ({
        op: "add", element: { id: b.id, type: "rectangle",
          x: 200, y: 300 + i * 200, width: b.width, height: b.height,
          label: b.label },
      })),
    }));
    const seeded = canvas.cli("apply", "--file", bpath);
    expect(seeded.stdout + seeded.stderr, "seed failed — capture void")
      .toMatch(/REVN=2/);

    const pk = path.join(canvas.project, "project_knowledge");
    const artifactPath = path.join(pk, "artifacts", ARTIFACT + ".excalidraw");
    const storedEls = (): Array<Record<string, unknown>> =>
      JSON.parse(fs.readFileSync(artifactPath, "utf-8")).elements;
    // THE SCENE THE TAB WAS HANDED, verbatim. The model tier installs
    // exactly this as the base revision, so its replay starts from the
    // picture the browser started from rather than from one rebuilt by
    // whichever `canvas.py` is in the tree.
    const storedBefore = storedEls();

    // The POST is captured off the wire and not rebuilt from
    // `getSceneElements`: what the model tier replays has to be what the
    // client SENT, and a reconstruction would be a second model of the
    // client — the failure this whole lineage is about.
    let posted: string | null = null;
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().endsWith("/api/save"))
        posted = req.postData();
    });

    await page.goto(canvas.url);
    await page.waitForFunction(
      () => (window as unknown as { excalidrawAPI?: { getSceneElements?:
        () => Array<{ id: string }> } }).excalidrawAPI?.getSceneElements?.()
        .some((e) => e.id === "w1"), null, { timeout: 30_000 });
    await page.waitForTimeout(1200);
    const atLoad = await scene(page);

    // THE MEASUREMENT THIS SPEC EXISTS FOR. For every bound caption: how
    // much the client changed its HEIGHT, how far the client moved it
    // vertically, and the whole-pixel drag that a rule of the form
    // `y' = y - dh/2` would absorb. The model tier reads `absorbs` as its
    // shove magnitude rather than writing a literal of its own, so an
    // honest re-record against a different font does not have to come
    // back and edit a test.
    const captions = storedBefore
      .filter((e) => e.type === "text" && e.containerId)
      .map((stored) => {
        const client = atLoad.find((e) => e.id === String(stored.id))!;
        const dh = client.h - Number(stored.height);
        return {
          id: String(stored.id), container: String(stored.containerId),
          stored_h: Number(stored.height), client_h: client.h, dh,
          stored_y: Number(stored.y), client_y: client.y,
          dy: client.y - Number(stored.y),
          stored_w: Number(stored.width), client_w: client.w,
          dw: client.w - Number(stored.width),
          absorbs: Math.round(-dh / 2),
        };
      });
    const rewrapped = captions.filter((c) => c.absorbs !== 0);
    expect(rewrapped.length,
      "no caption's height changed by enough to absorb a whole pixel, so "
      + "this capture has no `dh` axis and is exactly the population every "
      + "instrument in the tree already has")
      .toBeGreaterThan(0);
    expect(captions.filter((c) => c.absorbs === 0).length,
      "every caption in this capture was re-wrapped, so there is no "
      + "control — the pole that proves the record still speaks about an "
      + "ORDINARY caption would have nothing to speak about")
      .toBeGreaterThan(0);

    // THE FINDING, ASSERTED IN THE TAB. Both halves in the same caption:
    // the client changed its height and did NOT move it. A rule that
    // predicts a vertical displacement from `dh` is describing a client
    // that does not exist.
    const strayed = captions.filter((c) => Math.abs(c.dy) > 1e-9);
    expect(strayed,
      "the tab moved a re-measured caption vertically, which contradicts "
      + "the premise this capture is recorded to establish. If this fires, "
      + "the reds that read this capture are measuring the wrong thing and "
      + "the client model has to be re-derived, not the tests re-run")
      .toEqual([]);

    // ONE DRAG, AND IT IS NOT THE RED'S SUBJECT. The save button is
    // DISABLED until the tab believes something changed, and the
    // load-time re-measure does not count as a change to it — so a
    // capture of a pure load cannot exist and there has to be a gesture.
    // It is applied to `w1`, the control box, so the caption the reds are
    // about is one nobody went near in the tab either. It also gives the
    // record a sentence that must survive every repair to this class,
    // which the model tier asserts as its own ungated pole.
    const box = atLoad.find((e) => e.id === DRAGGED)!;
    const hook = await page.evaluate(([a, b]) => (window as unknown as {
      __sceneToScreen?: (x: number, y: number) =>
        { x: number; y: number } | null }).__sceneToScreen?.(a, b) ?? null,
      [box.x + box.w / 2, box.y + box.h / 2]);
    expect(hook, "scene hook unavailable — capture void").toBeTruthy();
    const from = hook as { x: number; y: number };
    await page.mouse.click(from.x, from.y);
    await page.waitForTimeout(250);
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(from.x + 15, from.y, { steps: 6 });
    await page.mouse.move(from.x + 30, from.y, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(250);
    const after = await scene(page);
    const dragDx = after.find((e) => e.id === DRAGGED)!.x - box.x;
    expect(Math.abs(dragDx) > 4, "the one drag did not land — capture void")
      .toBe(true);
    const strays = REWRAP_UNTOUCHED.filter((id) => {
      const a = atLoad.find((e) => e.id === id)!;
      const b = after.find((e) => e.id === id)!;
      return Math.abs(a.x - b.x) > 1e-9 || Math.abs(a.y - b.y) > 1e-9;
    });
    expect(strays, "the red's own box moved too — capture void").toEqual([]);

    await page.locator(".save-btn").click();
    await page.waitForTimeout(2500);
    expect(posted, "no /api/save POST was observed — capture void")
      .toBeTruthy();
    const body = JSON.parse(String(posted));
    expect(Object.keys(body.scenes || {}), "the POST carried no scene")
      .toContain(ARTIFACT);

    const saves = fs.readdirSync(path.join(pk, "saves")).sort();
    const record = JSON.parse(fs.readFileSync(
      path.join(pk, "saves", saves[saves.length - 1]), "utf-8"));
    expect(record.author, "the save did not happen — capture void")
      .toBe("user");
    const facts = ((record.artifacts || {})[ARTIFACT] || {}).facts || [];

    fs.mkdirSync(path.dirname(CAPTURE), { recursive: true });
    fs.writeFileSync(CAPTURE, JSON.stringify({
      recorded: new Date().toISOString().slice(0, 10),
      spec: "pin-rewrapped-caption-drag.spec.ts",
      app_build: appBuild(),
      canvas_sha256: crypto.createHash("sha256").update(fs.readFileSync(
        path.join(REPO, "skills", "wysiwyg-grilling", "scripts", "canvas.py")
      )).digest("hex"),
      artifact: ARTIFACT,
      population: {
        captions,
        rewrapped: rewrapped.map((c) => c.id),
        ordinary: captions.filter((c) => c.absorbs === 0).map((c) => c.id),
        dragged: DRAGGED, drag_dx: dragDx, untouched: REWRAP_UNTOUCHED,
      },
      stored_before: stable(storedBefore),
      // WHAT THE SAVE PUT ON DISK, the one thing about this save that a
      // repair to the RECORD layer may never change. The model tier
      // asserts its replay reproduces this geometry exactly, and that
      // assertion is live on every head.
      stored_after: Object.fromEntries(storedEls().map(
        (e: Record<string, unknown>) => [e.id, [e.x, e.y]])),
      posted: { ...body, scenes: Object.fromEntries(
        Object.entries(body.scenes as Record<string,
          Array<Record<string, unknown>>>)
          .map(([k, v]) => [k, stable(v)])) },
      observed: {
        headline: (record.summary || {}).headline,
        verb_counts: (record.summary || {}).verb_counts,
        facts: facts.map((f: { fact?: string; element?: string;
          dx?: number; dy?: number }) => [f.fact, f.element, f.dx, f.dy]),
      },
    }, null, 1) + "\n");
  });
