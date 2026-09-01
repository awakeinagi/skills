import { testMini as test, expect } from "./harness";
import * as fs from "node:fs";
import * as path from "node:path";

/** The 🐞 bug-hunter, end to end (Task 37 step 4).
 *
 * The backend suite owns the bundle's shape; this file owns the only
 * thing it cannot see — that a person clicking the ladybug ends up with
 * one. It runs both poles: filing writes a bundle and appends the event,
 * and CANCELLING writes nothing. The second is not decoration. A
 * reporter that files on cancel is a reporter that fills the evidence
 * directory with abandoned drafts, and nothing in the backend tests can
 * tell the two apart because both arrive at the endpoint the same way.
 */

/** Every bundle directory currently in the project.
 *
 * @param project - The canvas project root.
 * @returns Directory names under `.bugreports/`, sorted, `[]` when the
 *   directory has not been created yet.
 */
function bundles(project: string): string[] {
  const dir = path.join(project, "project_knowledge", ".bugreports");
  try { return fs.readdirSync(dir).sort(); } catch { return []; }
}

/** Read a filed bundle's `report.json`.
 *
 * @param project - The canvas project root.
 * @param name - The bundle directory name.
 * @returns The parsed report.
 */
function report(project: string, name: string): any {
  return JSON.parse(fs.readFileSync(
    path.join(project, "project_knowledge", ".bugreports", name,
      "report.json"), "utf-8"));
}

/** Wait for exactly one new bundle and return its directory name.
 *
 * The `canvas` fixture is WORKER-scoped, so every test in this file
 * shares one project and bundle ids ascend across the whole run.
 * Asserting `BUGREPORTS=1` therefore encodes the test ORDER, and the
 * first version of this file did: it passed for the test that happened
 * to run early and failed for the one that did not. Every count here is
 * a delta against what the test itself observed.
 *
 * @param project - The canvas project root.
 * @param before - Bundle names seen before the action under test.
 * @returns The one directory name that appeared.
 */
async function newBundle(project: string, before: string[]): Promise<string> {
  await expect.poll(() => bundles(project).length, { timeout: 10_000 })
    .toBe(before.length + 1);
  const added = bundles(project).filter((d) => !before.includes(d));
  expect(added).toHaveLength(1);
  return added[0];
}

/** Live element count, once the artifact has actually loaded.
 *
 * `.excalidraw` is visible before the scene is in it, so a count taken
 * on visibility alone reads 0 — which silently turned this file's
 * "the ring adds no element" assertion into `0 === 0` and passed while
 * the scene loaded 18 elements underneath it. Caught on the first run;
 * kept as a helper so no later test can make the same assertion vacuous.
 *
 * @param page - The page under test.
 * @returns The number of live elements, guaranteed non-zero.
 */
async function liveElementCount(
  page: import("@playwright/test").Page,
): Promise<number> {
  const count = () => page.evaluate(() =>
    ((window as any).excalidrawAPI?.getSceneElements() ?? [])
      .filter((e: any) => !e.isDeleted).length);
  await expect.poll(count, { timeout: 15_000 }).toBeGreaterThan(0);
  return count();
}

test("the ladybug opens straight onto the description, naming the selection",
  async ({ page, canvas }) => {
    // THE RULING, AS A TEST. Variant B won because a ring-first flow
    // taxes every report with a drawing step to capture what the
    // selection already states, so the dialog appearing on the first
    // click — with the selected element named in it — IS the feature.
    await page.goto(canvas.url);
    await expect(page.locator(".excalidraw")).toBeVisible();
    await page.evaluate(() => {
      const api = (window as any).excalidrawAPI;
      api.updateScene({ appState: { selectedElementIds: { kpi: true } } });
    });
    await page.getByTestId("bug-hunter-open").click();

    const dialog = page.getByTestId("bug-dialog");
    await expect(dialog).toBeVisible();
    // no ring was drawn and none was asked for — the ring overlay must
    // not be the thing standing between the click and the dialog
    await expect(page.getByTestId("bug-ring-overlay")).toHaveCount(0);
    await expect(page.getByTestId("bug-targets"))
      .toContainText("Reporting on");
    await expect(page.getByTestId("bug-targets"))
      .toContainText("Sentiment index");
    await expect(page.getByTestId("bug-desc")).toBeFocused();
  });

test("with nothing selected it says so and offers the whole view",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await expect(page.locator(".excalidraw")).toBeVisible();
    await page.evaluate(() => {
      (window as any).excalidrawAPI.updateScene(
        { appState: { selectedElementIds: {} } });
    });
    await page.getByTestId("bug-hunter-open").click();
    await expect(page.getByTestId("bug-targets"))
      .toContainText("Nothing selected");
    await expect(page.getByTestId("bug-targets"))
      .toContainText("reports the whole view");
    // and it is still fileable — the spike proved a no-selection report
    // works and the requirement is to keep it
    await expect(page.getByTestId("bug-submit")).toBeDisabled();
    await page.getByTestId("bug-desc").fill("The rail is empty on load.");
    await expect(page.getByTestId("bug-submit")).toBeEnabled();
  });

test("the submit button is legible, not the spike's white-on-white",
  async ({ page, canvas }) => {
    // The spike's FIRST catch was its own Submit button, invisible
    // because its injected CSS lost the specificity war with its own
    // container rule. This asserts the rendered colours differ — a
    // computed-style check, so a future stylesheet that wins the war
    // fails here rather than in a user's screenshot.
    await page.goto(canvas.url);
    await page.getByTestId("bug-hunter-open").click();
    await page.getByTestId("bug-desc").fill("x");
    const btn = page.getByTestId("bug-submit");
    const [fg, bg] = await btn.evaluate((el) => {
      const s = getComputedStyle(el);
      return [s.color, s.backgroundColor];
    });
    expect(fg).not.toBe(bg);
    expect(bg).not.toBe("rgba(0, 0, 0, 0)");
  });

test("filing writes a bundle and appends a bug_report event",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await expect(page.locator(".excalidraw")).toBeVisible();
    const before = bundles(canvas.project);
    const eventsBefore = canvas.events().length;

    await page.evaluate(() => {
      (window as any).excalidrawAPI.updateScene(
        { appState: { selectedElementIds: { kpi: true } } });
    });
    await page.getByTestId("bug-hunter-open").click();
    await page.getByTestId("bug-desc")
      .fill("The KPI value is cut off on the right.");
    await page.getByTestId("bug-submit").click();
    await expect(page.getByTestId("bug-dialog")).toHaveCount(0);

    const dir = await newBundle(canvas.project, before);
    // the slug is the first ~48 characters kebab-cased; the id ahead of
    // it is whatever this shared project has reached
    expect(dir).toMatch(/^\d+-the-kpi-value-is-cut-off-on-the-right$/);

    const r = report(canvas.project, dir);
    expect(r.description).toBe("The KPI value is cut off on the right.");
    expect(r.artifact).toBe("screen");
    expect(r.selection.map((t: any) => t.id)).toEqual(["kpi"]);
    // the selection's geometry is the SERVER's reading of the scene
    expect(r.selection[0].present).toBe(true);
    expect(r.selection[0].bbox.w).toBeGreaterThan(0);
    expect(r.snapshot_suspect).toBe(false);
    expect(r.capture.tier).toBe("server-svg");
    // the client half only a browser knows
    expect(r.client.user_agent).toContain("Mozilla");
    expect(r.client.viewport.innerWidth).toBe(1440);
    expect(r.client.panels.theme).toBeTruthy();
    // requirement 3: the tab agrees with the build it is talking to
    expect(r.version.client_is_stale).toBe(false);

    // the rendering exists and came from the server, not the browser
    const svg = fs.readFileSync(path.join(
      canvas.project, "project_knowledge", ".bugreports", dir, "scene.svg"),
      "utf-8");
    expect(svg).toContain("<svg");
    expect(svg).not.toContain("data:image");

    // the event a watching agent reacts to, in the log the CLI reads
    const evs = canvas.events();
    expect(evs.length).toBeGreaterThan(eventsBefore);
    const filed = evs.map((l) => JSON.parse(l))
      .filter((e) => e.type === "bug_report" && e.dir === dir);
    expect(filed).toHaveLength(1);
    expect(filed[0].headline).toBe("The KPI value is cut off on the right.");
    expect(filed[0].id).toBe(r.id);

    // and the CLI the agent runs finds it
    const listed = canvas.cli("bugreports");
    expect(listed.rc).toBe(0);
    expect(listed.stdout).toContain(`BUGREPORT_ID=${r.id}`);
    expect(listed.stdout).toContain(`BUGREPORTS=${before.length + 1}`);
  });

test("cancelling the dialog writes nothing at all",
  async ({ page, canvas }) => {
    // THE BOTH-DIRECTION CONTROL. Every assertion above is satisfied by
    // a component that files on any dismissal, so the negative pole is
    // asserted on all three surfaces the positive one touched:
    // directory, event log, CLI count.
    await page.goto(canvas.url);
    await expect(page.locator(".excalidraw")).toBeVisible();
    const before = bundles(canvas.project);
    const eventsBefore = canvas.events()
      .filter((l) => l.includes("bug_report")).length;

    await page.getByTestId("bug-hunter-open").click();
    await page.getByTestId("bug-desc")
      .fill("This one is never going to be filed.");
    await page.getByTestId("bug-cancel").click();
    await expect(page.getByTestId("bug-dialog")).toHaveCount(0);

    // Escape is the other way out and it must behave identically
    await page.getByTestId("bug-hunter-open").click();
    await page.getByTestId("bug-desc").fill("Nor is this one.");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("bug-dialog")).toHaveCount(0);

    // give a stray POST time to land before claiming none happened —
    // asserting immediately would pass against a request still in flight
    await page.waitForTimeout(750);
    expect(bundles(canvas.project)).toEqual(before);
    expect(canvas.events().filter((l) => l.includes("bug_report")).length)
      .toBe(eventsBefore);
    expect(canvas.cli("bugreports").stdout)
      .toContain(`BUGREPORTS=${before.length}`);

    // the control's control: the same page CAN still file, so the
    // assertions above are about cancelling and not about a dead button
    await page.getByTestId("bug-hunter-open").click();
    await page.getByTestId("bug-desc").fill("This one is real.");
    await page.getByTestId("bug-submit").click();
    const dir = await newBundle(canvas.project, before);
    expect(report(canvas.project, dir).description).toBe("This one is real.");
  });

test("the ring is a second step, and never lands in the drawing",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await expect(page.locator(".excalidraw")).toBeVisible();
    const countBefore = await liveElementCount(page);
    const before = bundles(canvas.project);

    await page.getByTestId("bug-hunter-open").click();
    await page.getByTestId("bug-desc")
      .fill("These two boxes overlap in the gap between them.");
    await page.getByTestId("bug-ring").click();

    const overlay = page.getByTestId("bug-ring-overlay");
    await expect(overlay).toBeVisible();
    // requirement 6: the guidance names the way out
    await expect(overlay).toContainText("Drag a circle");
    await expect(page.getByTestId("bug-ring-skip")).toBeVisible();

    await page.mouse.move(500, 400);
    await page.mouse.down();
    for (const [x, y] of [[620, 400], [620, 500], [500, 500], [500, 400]])
      await page.mouse.move(x, y, { steps: 4 });
    await page.mouse.up();

    // back to the description, with the text still there
    await expect(page.getByTestId("bug-dialog")).toBeVisible();
    await expect(page.getByTestId("bug-desc"))
      .toHaveValue("These two boxes overlap in the gap between them.");
    await expect(page.getByTestId("bug-ring")).toContainText("redraw");

    // THE OVERLAY RULE: nothing was added to the artifact
    const countAfter = await liveElementCount(page);
    expect(countAfter).toBe(countBefore);

    await page.getByTestId("bug-submit").click();
    const dir = await newBundle(canvas.project, before);
    const r = report(canvas.project, dir);
    expect(r.ring_points).toBeGreaterThan(3);
    expect(r.ring_bbox.w).toBeGreaterThan(50);
    // burned into the server's own rendering, in scene coordinates
    const svg = fs.readFileSync(path.join(
      canvas.project, "project_knowledge", ".bugreports", dir, "scene.svg"),
      "utf-8");
    expect(svg).toContain("<polyline");
    expect(svg).toContain("#cf222e");
    // and the drawing on disk is untouched — the ring is not an element
    expect(r.server.artifact_element_counts.screen).toBe(countAfter);
  });

test("escape from the ring keeps the half-written report",
  async ({ page, canvas }) => {
    // Losing a written report to a mis-aimed Escape is data loss in the
    // one tool whose job is not losing what the user saw: they cannot
    // re-observe it.
    await page.goto(canvas.url);
    await expect(page.locator(".excalidraw")).toBeVisible();
    await page.getByTestId("bug-hunter-open").click();
    await page.getByTestId("bug-desc").fill("Half a thought so far");
    await page.getByTestId("bug-ring").click();
    await expect(page.getByTestId("bug-ring-overlay")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("bug-dialog")).toBeVisible();
    await expect(page.getByTestId("bug-desc"))
      .toHaveValue("Half a thought so far");
    // a SECOND escape leaves, which is what makes the first one a
    // ring-only exit rather than a broken close
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("bug-dialog")).toHaveCount(0);
  });

test("the dialog states the limitation it cannot fix",
  async ({ page, canvas }) => {
    // Requirement 8. The capture is the drawing plus app state, never a
    // screenshot of browser chrome — so a report about a panel or a menu
    // has to carry the detail in words, and the person writing it has to
    // be told that WHERE THEY ARE WRITING.
    await page.goto(canvas.url);
    await page.getByTestId("bug-hunter-open").click();
    const dialog = page.getByTestId("bug-dialog");
    await expect(dialog).toContainText("not a screenshot");
    await expect(dialog).toContainText("not");
    await expect(dialog).toContainText("picture of the browser");
  });
