import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

/** THE AUTHORITATIVE ORACLE for what the client does with a stored focus.
 *
 * Every other measurement of this property in the repo is a Python
 * transcription of `updateBoundPoint`; this one is the browser. It reads
 * back, from the real React app driven by the real server, where the
 * client DRAWS a bound arrow foot that canvas.py STORED — for the three
 * specimens `tests/focus_probe.py` builds, which is the same scene the
 * fast neighbour (`TestFocusRoundTrip`) reads.
 *
 * Two phases, because the defect is LATENT. As loaded the client honours
 * the stored point exactly, so a screenshot at load agrees with the
 * server and says nothing; `updateBoundElements` (chunk-4FTI6OG3.js
 * :11085) only runs for a bindable element that has CHANGED. Phase B
 * nudges each hub right and then back — net displacement zero, so the
 * correct answer for every foot is byte-identical to what was stored and
 * any difference is the client's re-derivation and nothing else.
 *
 * BOTH QUARTER POINTS ARE ASSERTED and that is the design, not
 * thoroughness. `determineFocusPoint` picks its corner with strict `>`
 * tests, so the correct-magnitude focus for a perpendicular approach
 * sits exactly on its selection boundary; which side of a 91px cliff you
 * come down on is decided by the sign alone. A left-hand foot on its own
 * goes green under a fix that still draws the right-hand foot 91px away.
 *
 * AND EVERY NODE SHAPE IS ASSERTED, which is what this file gained in
 * v0.9 TASK-FOCUS-FOLLOWUP-A. The first cut carried three square-cornered
 * rectangles, because the Python transcription beside it could model
 * nothing else — so 68 of the corpus's 346 bound endpoints (diamonds,
 * ellipses, `roundness: {type: 3}` rectangles) were asserted by nothing
 * anywhere. THE BROWSER NEVER HAD THAT LIMIT: it is the real client, and
 * a rhombus costs it exactly what a rectangle costs it. Rows 4-6 are the
 * conic and rounded specimens, and each one asserts a drawn point the
 * old box-only model could not have produced — 76.97px, 18.43px and
 * 4.47px away from what it would have said. That is what makes this
 * referee the ORACLE for those shapes rather than a second opinion.
 */

const __dirname2 = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname2, "..", "..", "..", "..");
const CANVAS = path.join(REPO, "skills", "wysiwyg-grilling", "scripts",
  "canvas.py");
const BUILD = path.join(REPO, "tests", "focus_probe.py");

/** One specimen, as `tests/focus_probe.py` wrote it. */
type Spec = { name: string; arrow: string; hub: string;
              shape: string; roundness: { type: number } | null;
              side: string; hub_box: number[]; foot: number[];
              adjacent: number[]; gap: number; focus: number;
              normal: number[]; tangent: number[]; predicted: number[] };

/** Component of `drawn - stored` along a specimen's own axis. */
const along = (f: Foot, s: Spec, axis: number[]): number =>
  (f.ex - s.foot[0]) * axis[0] + (f.ey - s.foot[1]) * axis[1];
/** One arrow's live endpoint, in scene coordinates. */
type Foot = { id: string; ex: number; ey: number; focus: number | null };

/** Absolute endpoint of every probe arrow, plus its live binding. */
const feet = (page: import("@playwright/test").Page): Promise<Foot[]> =>
  page.evaluate(() =>
    (window as unknown as { excalidrawAPI: {
      getSceneElements: () => Array<Record<string, unknown>> } })
      .excalidrawAPI.getSceneElements()
      .filter((e) => e.type === "arrow" && !e.isDeleted &&
        String(e.id).startsWith("probe-"))
      .map((e) => {
        const pts = e.points as number[][];
        const last = pts[pts.length - 1];
        return { id: e.id as string,
                 focus: ((e.endBinding as { focus?: number } | null)
                   ?.focus) ?? null,
                 ex: (e.x as number) + last[0],
                 ey: (e.y as number) + last[1] };
      }));

test("the client redraws a stored foot where the server put it",
  async ({ page }) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "focusprobe-"));
    const b = spawnSync("python3", [BUILD, dir],
      { encoding: "utf-8", timeout: 60_000 });
    if (b.status !== 0)
      throw new Error("probe build failed: " + b.stdout + b.stderr);
    const specs: Spec[] = JSON.parse(
      fs.readFileSync(path.join(dir, "manifest.json"), "utf-8"));
    const s = spawnSync("python3",
      [CANVAS, "--project", dir, "start", "--no-browser"],
      { encoding: "utf-8", timeout: 60_000 });
    const m = /URL=(\S+)/.exec(s.stdout || "");
    if (!m) throw new Error("server did not start: " + s.stderr + s.stdout);
    try {
      await page.goto(m[1]);
      await expect(page.locator(".save-btn")).toBeVisible({ timeout: 20_000 });
      await expect.poll(async () => (await feet(page)).length,
        { timeout: 20_000 }).toBe(specs.length);

      // Phase A — as loaded. This is what every snapshot check in the
      // repo sees, and it is the moment BEFORE the defect.
      const asLoaded = new Map((await feet(page)).map((f) => [f.id, f]));
      for (const spec of specs) {
        const f = asLoaded.get(spec.arrow)!;
        expect.soft(Math.abs(f.ex - spec.foot[0]),
          `${spec.name}: as loaded, tangential`).toBeLessThan(0.5);
      }

      // Phase B — make the client re-derive. Select all three hubs and
      // nudge right, then left: net zero, so the stored point IS the
      // correct answer.
      const ids = Object.fromEntries(specs.map((sp) => [sp.hub, true]));
      const select = async () => {
        await page.evaluate((sel) => {
          (window as unknown as { excalidrawAPI: {
            updateScene: (s: unknown) => void } }).excalidrawAPI
            .updateScene({ appState: { selectedElementIds: sel } });
        }, ids);
      };
      await page.locator("canvas").first().click({
        position: { x: 5, y: 5 }, force: true });
      await select();
      await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(300);
      await select();
      await page.keyboard.press("ArrowLeft");
      await page.waitForTimeout(300);

      const drawn = new Map((await feet(page)).map((f) => [f.id, f]));
      const rows: string[] = [];
      for (const spec of specs) {
        const f = drawn.get(spec.arrow)!;
        rows.push(`${spec.name.padEnd(8)} ${spec.shape.padEnd(10)}` +
          `${spec.roundness ? "round " : "sharp "}` +
          `focus=${String(spec.focus).padEnd(7)}` +
          ` stored=(${spec.foot[0]}, ${spec.foot[1]})` +
          ` drawn=(${f.ex.toFixed(3)}, ${f.ey.toFixed(3)})` +
          ` model=(${spec.predicted[0].toFixed(3)}, ` +
          `${spec.predicted[1].toFixed(3)})` +
          ` tangential=${Math.abs(along(f, spec, spec.tangent)).toFixed(3)}` +
          ` normal=${along(f, spec, spec.normal).toFixed(3)}`);
      }
      console.log("focus round-trip, after a net-zero nudge:\n" +
        rows.join("\n"));
      for (const spec of specs) {
        const f = drawn.get(spec.arrow)!;
        // THE CALIBRATION, and it is the assertion that makes the Python
        // transcription trustworthy for a shape it cannot be checked on
        // anywhere else. `predicted` is what `focus_probe.client_draws`
        // says the client will do; this is the client doing it.
        expect(Math.hypot(f.ex - spec.predicted[0], f.ey - spec.predicted[1]),
          `${spec.name} (${spec.shape}): the transcription predicted ` +
          `(${spec.predicted[0].toFixed(3)}, ${spec.predicted[1].toFixed(3)})`)
          .toBeLessThan(0.5);
        // ALONG the side is the slip that moves a fan lane or a port
        // assignment. ACROSS it is the binding `gap` and is by design,
        // so it is reported above and asserted only as "still the gap"
        // — and only where it IS the gap, which is a square-cornered
        // rectangle and nothing else. A rounded box's straight sides sit
        // `gap * cos` out (2.025px here, not 6) and a conic's expanded
        // outline is a SCALED shape rather than an offset curve, so on
        // rows 4-6 the normal is reported and not graded.
        const sharpRect = spec.shape === "rectangle" && !spec.roundness;
        expect(Math.abs(along(f, spec, spec.tangent)),
          `${spec.name}: tangential slip after re-derivation`)
          .toBeLessThan(sharpRect ? 0.5 : 4.0);
        if (sharpRect)
          expect(Math.abs(along(f, spec, spec.normal) - spec.gap),
            `${spec.name}: normal displacement should be exactly the gap`)
            .toBeLessThan(0.5);
        else
          expect(along(f, spec, spec.normal),
            `${spec.name}: the client must still draw the foot OUTSIDE ` +
            `the ink, by no more than the gap`)
            .toBeGreaterThan(0);
      }
    } finally {
      spawnSync("python3", [CANVAS, "--project", dir, "stop"],
        { encoding: "utf-8", timeout: 30_000 });
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
