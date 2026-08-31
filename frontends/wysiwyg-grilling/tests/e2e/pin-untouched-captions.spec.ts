import { testTextClip as test, expect } from "./harness";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

/* THE BROWSER IS THE REFEREE FOR THIS CLASS (mutant-curator, 2026-08-31).
 *
 * Eleven closures of the phantom-caption class were argued in Python
 * against a MODEL of what a browser posts, and eleven times the model was
 * wrong in a way nobody could see. The last one is exact and worth
 * carrying: `label_grid_client_scene` translates the stored x and adds a
 * constant to the width, but a real tab RE-CENTRES the caption on its own
 * re-measure FIRST and translates SECOND. Half a pixel, opposite order,
 * and that half pixel is the entire class.
 *
 * So this spec stops imitating a browser. It drives one, makes the most
 * ordinary gesture in the product — drag one box — and asks what the
 * record says about the captions of the seven boxes nobody went near.
 *
 * TWO OUTPUTS, AND THE SECOND IS WHY THIS IS A PIN AND NOT A PROBE.
 *
 *  1. It ASSERTS the relation here, in the browser: no caption of an
 *     untouched box is narrated as having moved. Red on `fix-tenth` (3)
 *     and on `604a7a0` (2).
 *  2. It WRITES DOWN WHAT THE CLIENT POSTED — the verbatim `/api/save`
 *     request body, plus the artifact as it stood on disk when the tab
 *     loaded it. That capture is the browser's testimony in the wire
 *     format, and `tests/test_mutants.py` replays it through whatever
 *     `canvas.py` is in the tree.
 *
 * THE CAPTURE IS THE ANSWER TO THE DESIGN HAZARD. This repo has 45
 * render-tier tests that skip for want of a browser, and a skip reads as
 * a green — which is the exact shape of the defect being pinned here.
 * The e2e hook is manual, so a pin that lived only in this file would be
 * absent from every ordinary run and nobody would be told. Replaying a
 * recorded capture needs no browser, so the model tier carries the
 * assertion on every commit and FAILS when this file has never run.
 *
 * WHAT THE CAPTURE MAY AND MAY NOT GO STALE AGAINST. It records an
 * INPUT — what the client posts — not a verdict about `canvas.py`, so a
 * `canvas.py` edit does not stale it and no ordinary commit needs a
 * browser. What DOES stale it is the client changing its mind about what
 * to post, so the app bundle's own build stamp is recorded and the model
 * tier refuses a capture taken against a different one.
 *
 * VOIDS ITSELF rather than writing a capture nobody can trust: gated on
 * the seed, the camera hook, the one drag landing, the other seven boxes
 * NOT moving, the save happening, and the POST having been observed. A
 * failed gate writes NO capture, which the model tier reports as "the
 * browser never spoke" rather than as a pass. */

type El = { id: string; type: string; x: number; y: number;
            w: number; h: number; containerId?: string | null };

/* Eight captions of eight different widths, so the client's re-measure
 * lands on both parities. Five of these move on disk from the tab merely
 * OPENING; three of those five are narrated. The widths are what make
 * that population exist — a single repeated caption would put every
 * container on one parity and the class could not appear, which is the
 * hole the ninth closure's 588-cell grid had. */
const CAPTIONS = ["Ship", "Order", "Cart", "Basket", "Checkout",
                  "Pay", "Refund", "Invoice"];
const DRAGGED = "b0";
const UNTOUCHED = [1, 2, 3, 4, 5, 6, 7].map((i) => `b${i}`);
const ARTIFACT = "text-clip";
const REPO = path.resolve(
  path.dirname(new URL(import.meta.url).pathname), "..", "..", "..", "..");
const CAPTURE = path.join(REPO, "tests", "browser_caption_capture.json");
const BUILD_STAMP = path.join(
  REPO, "skills", "wysiwyg-grilling", "scripts", "web", "build-stamp.json");

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

/* Excalidraw stamps a fresh `versionNonce` and `updated` on every element
 * on every load, so two byte-identical runs of this spec wrote captures
 * differing in 40 lines of pure churn. That is not cosmetic: the capture
 * is TRACKED, and a re-record whose diff is 40 meaningless lines is a
 * re-record nobody reads — which is how a real change in what the client
 * posts would go through unnoticed. Neither field is in
 * `DEFAULT_SIGNIFICANT_ATTRS` and dropping both was measured to leave the
 * replayed record identical, fact for fact. */
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
 *   the model tier is the layer that decides what an unstamped capture
 *   is worth.
 */
function appBuild(): string {
  try {
    return String(JSON.parse(fs.readFileSync(BUILD_STAMP, "utf-8"))
      .source_hash || "unstamped");
  } catch { return "unstamped"; }
}

test("is a caption of a box nobody touched narrated as having moved?",
  async ({ page, canvas }) => {
    const bpath = path.join(canvas.project, "pin-untouched.json");
    fs.writeFileSync(bpath, JSON.stringify({
      base_revn: 1, artifact: ARTIFACT,
      ops: CAPTIONS.map((cap, i) => ({
        op: "add", element: { id: `b${i}`, type: "rectangle",
          x: 200, y: 300 + i * 110, width: 90 + i, height: 50, label: cap },
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
    // same picture the browser started from rather than from a scene
    // rebuilt by whichever `canvas.py` is in the tree.
    const storedBefore = storedEls();

    // The POST is captured off the wire and not rebuilt from
    // `getSceneElements`. What the model tier has to replay is what the
    // client SENT — the app is free to reshape a scene on its way into
    // the request, and a reconstruction would be a second model of the
    // client, which is the failure this whole spec exists to end.
    let posted: string | null = null;
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().endsWith("/api/save"))
        posted = req.postData();
    });

    await page.goto(canvas.url);
    await page.waitForFunction(
      () => (window as unknown as { excalidrawAPI?: { getSceneElements?:
        () => Array<{ id: string }> } }).excalidrawAPI?.getSceneElements?.()
        .some((e) => e.id === "b7"), null, { timeout: 30_000 });
    await page.waitForTimeout(1200);
    const atLoad = await scene(page);

    // THE CLIENT'S RE-MEASURE, recorded as a number the model tier can
    // read. This is the geometry the defect lives in: `dx` is exactly
    // `-(clientWidth - storedWidth)/2`, the caption re-centring itself on
    // its own fresh measurement. A capture in which no caption is
    // re-measured cannot contain the class, and the model tier's
    // population guard refuses one.
    const remeasured = CAPTIONS.map((_, i) => {
      const stored = storedBefore.find((e) => e.containerId === `b${i}`)!;
      const client = atLoad.find((e) => e.containerId === `b${i}`)!;
      return { id: `b${i}-label`,
        stored_x: Number(stored.x), stored_w: Number(stored.width),
        client_x: client.x, client_w: client.w,
        dx: client.x - Number(stored.x),
        half_dw: -(client.w - Number(stored.width)) / 2 };
    });
    expect(remeasured.filter((r) => r.client_w !== r.stored_w).length,
      "no caption was re-measured by the client — the half-pixel geometry "
      + "this class lives in is absent and the capture would be vacuous")
      .toBeGreaterThan(0);

    // ONE box moves, and it is the one whose OWN caption the client
    // re-centres by a whole pixel — so a phantom about any other box's
    // caption cannot be blamed on the gesture.
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
    const moved = after.find((e) => e.id === DRAGGED)!;
    const dragDx = moved.x - box.x;
    expect(Math.abs(dragDx) > 4, "the one drag did not land — capture void")
      .toBe(true);
    const strays = UNTOUCHED.filter((id) => {
      const a = atLoad.find((e) => e.id === id)!;
      const b = after.find((e) => e.id === id)!;
      return Math.abs(a.x - b.x) > 1e-9 || Math.abs(a.y - b.y) > 1e-9;
    });
    expect(strays, "another box moved too — capture void").toEqual([]);

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
    const storedAfter = storedEls();

    // Which captions the save actually MOVED ON DISK. A capture in which
    // none did is a capture in which no phantom is possible, and the
    // model tier refuses that too — this is the population, not the
    // verdict.
    const captionsMovedOnDisk = UNTOUCHED.filter((id) => {
      const a = storedBefore.find((e) => e.containerId === id)!;
      const b = storedAfter.find((e) => e.containerId === id)!;
      return a.x !== b.x || a.y !== b.y;
    });
    expect(captionsMovedOnDisk.length,
      "the save moved no untouched caption on disk, so nothing could be "
      + "narrated about one and this capture cannot contain the class")
      .toBeGreaterThan(0);

    const phantoms = facts
      .filter((f: { fact?: string; element?: string }) =>
        f.fact === "moved" && UNTOUCHED.some((id) => f.element === id + "-label"))
      .map((f: { element?: string; dx?: number; dy?: number }) =>
        [f.element, f.dx, f.dy]);

    fs.mkdirSync(path.dirname(CAPTURE), { recursive: true });
    fs.writeFileSync(CAPTURE, JSON.stringify({
      recorded: new Date().toISOString().slice(0, 10),
      spec: "pin-untouched-captions.spec.ts",
      app_build: appBuild(),
      canvas_sha256: crypto.createHash("sha256").update(fs.readFileSync(
        path.join(REPO, "skills", "wysiwyg-grilling", "scripts", "canvas.py")
      )).digest("hex"),
      artifact: ARTIFACT,
      gesture: {
        dragged: DRAGGED, drag_dx: dragDx, untouched: UNTOUCHED,
        captions_remeasured_by_client:
          remeasured.filter((r) => r.client_w !== r.stored_w).map((r) => r.id),
        captions_moved_on_disk: captionsMovedOnDisk.map((id) => id + "-label"),
        remeasure: remeasured,
      },
      stored_before: stable(storedBefore),
      posted: { ...body, scenes: Object.fromEntries(
        Object.entries(body.scenes as Record<string,
          Array<Record<string, unknown>>>)
          .map(([k, v]) => [k, stable(v)])) },
      observed: {
        headline: (record.summary || {}).headline,
        verb_counts: (record.summary || {}).verb_counts,
        facts: facts.map((f: { fact?: string; element?: string;
          dx?: number; dy?: number }) => [f.fact, f.element, f.dx, f.dy]),
        phantom_captions: phantoms.map((p: unknown[]) => p[0]),
      },
    }, null, 1) + "\n");

    // THE NEIGHBOUR, in the same save and asserted first: the record
    // still narrates the gesture the user DID make, with the right
    // magnitude and direction. A repair that reached silence about the
    // captions by breaking narration, suppressing the artifact or
    // emptying the save arrives here before it reaches the red below.
    const boxFact = facts.find((f: { fact?: string; element?: string }) =>
      f.fact === "moved" && f.element === DRAGGED);
    expect(boxFact, "the record does not name the box the user dragged")
      .toBeTruthy();
    expect(boxFact.dx, "the record's dx for the dragged box has the wrong "
      + "sign — it was dragged right").toBeGreaterThan(0);
    expect(boxFact.dy, "the record invented a vertical component for a "
      + "purely horizontal drag").toBe(0);

    // THE RED. The relation, with no coordinate literal in it: a caption
    // of a box nobody touched may not be narrated as having moved.
    expect(phantoms,
      `${phantoms.length} of ${UNTOUCHED.length} captions of boxes NOBODY `
      + `TOUCHED are narrated as having moved. The user dragged ${DRAGGED} `
      + `and nothing else. These captions moved on disk because the CLIENT `
      + `re-centred them on its own re-measure when the tab opened `
      + `(${captionsMovedOnDisk.length} of them did), which is not a `
      + `gesture the user made. Record: ${JSON.stringify(
        (record.summary || {}).verb_counts)}`).toEqual([]);
  });
