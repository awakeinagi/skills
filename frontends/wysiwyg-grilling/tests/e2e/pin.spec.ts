import * as fs from "node:fs";
import * as path from "node:path";
import { testMini as test, expect } from "./harness";

/** "Pin to Canvas" — the client surface (v0.9).
 *
 * A pin is Excalidraw's native `locked`, and Excalidraw renders a locked
 * element NO DIFFERENTLY AT ALL: no badge, no border, no handles, no
 * cursor change. So every claim this feature makes is a claim about
 * pixels and pointer routing that the backend suite cannot see — the
 * flag round-trips perfectly today and always has, and the whole defect
 * was that nothing showed it and nothing could take it back.
 *
 * The unpin path is the one that matters. A locked element cannot be
 * click-selected, box-selected or Ctrl+A'd, so an affordance that appears
 * on selection can pin and can never unpin; the tack is drawn from SCENE
 * state (hover, selection, or the standing preference) precisely so it
 * survives its own effect. `hitAtClient` does not filter `locked`, which
 * is why hover still reaches an element nothing else in the app can.
 *
 * Two poles wherever a pole exists: a pinned element must resist a drag
 * AND the same drag must move an unpinned one, or the test proves only
 * that the gesture was never delivered.
 */

type El = { id: string; type: string; x: number; y: number;
            w: number; h: number; locked: boolean; role: string;
            containerId: string };

/** Read the live scene through the app's own API handle. */
const scene = (page: import("@playwright/test").Page): Promise<El[]> =>
  page.evaluate(() =>
    (window as unknown as { excalidrawAPI: {
      getSceneElements: () => Array<Record<string, unknown>> } })
      .excalidrawAPI.getSceneElements()
      .filter((e) => !e.isDeleted)
      .map((e) => ({
        id: e.id as string, type: e.type as string,
        x: e.x as number, y: e.y as number,
        w: (e.width as number) || 0, h: (e.height as number) || 0,
        locked: !!e.locked,
        role: String((e.customData as { role?: string } | null)?.role || ""),
        containerId: String(e.containerId || ""),
      })));

/** One element's live record, by id. */
async function one(page: import("@playwright/test").Page,
                   id: string): Promise<El> {
  const e = (await scene(page)).find((x) => x.id === id);
  if (!e) throw new Error(`no such element: ${id}`);
  return e;
}

/** An element's centre in scene coordinates, read live.
 *
 * Never a hardcoded pair: the server places what it is sent (grid snap,
 * collision nudging, frame refit), so a literal from the fixture's batch
 * is a guess about where an element ended up, and a click that misses
 * lands on empty canvas — where this feature has a DIFFERENT menu, so the
 * failure reads as "the item never rendered" rather than "you missed".
 * @param page The page under test.
 * @param id The element id.
 * @returns The element's centre, in scene coordinates.
 */
async function centre(page: import("@playwright/test").Page, id: string):
  Promise<{ x: number; y: number }> {
  const e = await one(page, id);
  return { x: e.x + e.w / 2, y: e.y + e.h / 2 };
}

/** A scene point in screen pixels, through the app's own camera. */
async function at(page: import("@playwright/test").Page, x: number, y: number):
  Promise<{ x: number; y: number }> {
  const p = await page.evaluate(([sx, sy]) => (window as unknown as {
    __sceneToScreen?: (a: number, b: number) => { x: number; y: number } | null;
  }).__sceneToScreen?.(sx, sy) ?? null, [x, y]);
  if (!p) throw new Error("scene hook unavailable");
  // A click outside the viewport is delivered to nothing and reports
  // nothing, so the symptom surfaces three steps later as "the menu item
  // never rendered". Fail here instead, where the cause is.
  const vp = page.viewportSize();
  expect(p.x, `scene (${x},${y}) is off screen in x`)
    .toBeGreaterThan(0);
  expect(p.y, `scene (${x},${y}) is off screen in y`)
    .toBeGreaterThan(0);
  if (vp) {
    expect(p.x, `scene (${x},${y}) is off screen in x`).toBeLessThan(vp.width);
    expect(p.y, `scene (${x},${y}) is off screen in y`).toBeLessThan(vp.height);
  }
  return p;
}

/** Bring a set of elements into view together.
 *
 * The header's "fit" fits the artifact, and the artifact grows as tests
 * seed into it, so an element that was comfortably on screen in one test
 * is at the edge in the next.
 * @param page The page under test.
 * @param ids The elements that must all be visible at once.
 */
async function reveal(page: import("@playwright/test").Page, ids: string[]) {
  await page.evaluate((want) => {
    const api = (window as unknown as { excalidrawAPI: {
      getSceneElements: () => Array<{ id: string }>;
      scrollToContent: (e: unknown, o: unknown) => void } }).excalidrawAPI;
    const els = api.getSceneElements().filter((e) => want.includes(e.id));
    api.scrollToContent(els, { fitToViewport: true, viewportZoomFactor: 0.5 });
  }, ids);
}

/** Clear any "Got it" banner sitting over the canvas.
 *
 * A save opens the narration banner, which both covers canvas and moves
 * the wrap — and every scene→screen coordinate is measured off the wrap.
 * @param page The page under test.
 */
async function dismissBanners(page: import("@playwright/test").Page) {
  for (const b of await page.locator(".banner button",
    { hasText: "Got it" }).all())
    await b.click().catch(() => { /* raced with its own dismissal */ });
}

/** Right-click a scene coordinate — the gesture the menu hangs off. */
async function rightClickScene(page: import("@playwright/test").Page,
                               x: number, y: number): Promise<void> {
  const p = await at(page, x, y);
  await page.mouse.click(p.x, p.y, { button: "right" });
}

/** Open the EMPTY-canvas menu at a clear point.
 *
 * The left click first is load-bearing: a right-click on blank canvas
 * does not drop an existing selection, so the menu is still about the
 * selected element — correctly, and the same way Excalidraw's own menu
 * behaves — and the all-elements arm is not what appears.
 * @param page The page under test.
 * @param spot A scene point clear of every element.
 */
async function openCanvasMenu(page: import("@playwright/test").Page,
                              spot: { x: number; y: number }) {
  const p = await at(page, spot.x, spot.y);
  await page.mouse.click(p.x, p.y);
  await rightClickScene(page, spot.x, spot.y);
}

/** Left-click an element by id. */
async function clickEl(page: import("@playwright/test").Page, id: string) {
  const c = await centre(page, id);
  const p = await at(page, c.x, c.y);
  await page.mouse.click(p.x, p.y);
}

/** Right-click an element by id. */
async function rightClickEl(page: import("@playwright/test").Page, id: string) {
  const c = await centre(page, id);
  await rightClickScene(page, c.x, c.y);
}

/** Hover a scene coordinate. Two moves, deliberately: one pointermove
 * from a standing start is not always delivered, and the tack's
 * visibility is driven by that event. */
async function hoverEl(page: import("@playwright/test").Page,
                       id: string): Promise<void> {
  const c = await centre(page, id);
  const p = await at(page, c.x, c.y);
  await page.mouse.move(p.x - 4, p.y - 4);
  await page.mouse.move(p.x, p.y);
}

/** Open the element menu's "Pin to Canvas" flyout and return its items. */
async function pinFlyout(page: import("@playwright/test").Page) {
  const group = page.locator("li.wg-submenu-parent.wg-pin-item");
  await expect(group).toBeVisible({ timeout: 5000 });
  await group.hover();
  return group.locator("ul.wg-submenu li.wg-pin-item");
}

/** Zoom out until there is canvas nobody has drawn on, and return a
 * scene point clear of every element — the empty-canvas menu's arm only
 * exists where nothing is hit AND nothing is selected. */
async function emptySpot(page: import("@playwright/test").Page):
  Promise<{ x: number; y: number }> {
  await page.evaluate(() => {
    const api = (window as unknown as { excalidrawAPI: {
      getSceneElements: () => unknown[];
      scrollToContent: (e: unknown, o: unknown) => void } }).excalidrawAPI;
    api.scrollToContent(api.getSceneElements(),
      { fitToViewport: true, viewportZoomFactor: 0.35 });
  });
  const live = await scene(page);
  const spot = { x: Math.min(...live.map((e) => e.x)) - 130,
                 y: Math.min(...live.map((e) => e.y)) - 100 };
  const covered = live.some((e) => spot.x >= e.x && spot.x <= e.x + e.w &&
    spot.y >= e.y && spot.y <= e.y + e.h);
  expect(covered, "the chosen empty spot is not empty").toBe(false);
  const p = await at(page, spot.x, spot.y);
  expect(p.x, "the empty spot is off screen").toBeGreaterThan(0);
  expect(p.y, "the empty spot is off screen").toBeGreaterThan(0);
  return spot;
}

/** Select an element and pin it through the context menu.
 *
 * The item's wording is asserted where it is the point (the first test);
 * here the pattern is loose on purpose, because the count in the label is
 * a fact about what the click selected and not about pinning. */
async function pinViaMenu(page: import("@playwright/test").Page,
                          id: string): Promise<void> {
  const c = await centre(page, id);
  const p = await at(page, c.x, c.y);
  await page.mouse.click(p.x, p.y);
  await rightClickScene(page, c.x, c.y);
  const items = await pinFlyout(page);
  await items.filter({ hasText: /^Pin .* to canvas$/ }).click();
}

/** Commit whatever is unsaved, so the next save carries only what the
 * test does next. These tests share a server per worker, and a save is
 * only "one revision of pins" if the buffer was clean going in.
 * @param page The page under test.
 */
async function commitBaseline(page: import("@playwright/test").Page) {
  const btn = page.locator(".save-btn");
  if (await btn.isEnabled()) await btn.click();
  await expect(btn).toBeDisabled({ timeout: 10_000 });
}

/** Seed plain rectangles through the real agent write path, and make sure
 * the open tab is showing them.
 *
 * Plain on purpose: every owner the mini fixture ships hijacks a bare
 * click — the KPI has an open ❓ that pops a card, the report card opens
 * a document reader, the body block's rectangle is transparent so the hit
 * lands on its decoration strokes. A test about pinning should not be
 * fighting any of that.
 *
 * The Apply-now step is not optional. These tests share one server per
 * worker, so an earlier test can leave the tab with unsaved work, and an
 * agent revision arriving then is HELD behind the pending banner rather
 * than applied — which reads exactly like a seed that silently failed.
 * @param canvas The per-worker canvas fixture.
 * @param page The page under test, already loaded.
 * @param specs Element specs to add (id, x, y, width, height, label).
 */
async function seedRects(
  canvas: { cli: (...a: string[]) => { stdout: string }; project: string },
  page: import("@playwright/test").Page,
  specs: Array<Record<string, unknown>>): Promise<void> {
  const bpath = path.join(canvas.project, `seed-${specs[0].id}.json`);
  fs.writeFileSync(bpath, JSON.stringify({
    base_revn: revn(canvas), artifact: "screen",
    ops: specs.map((s) => ({ op: "add",
      element: { type: "rectangle", ...s } })),
  }));
  const out = canvas.cli("apply", "--file", bpath);
  const banner = page.locator(".banner.pending");
  const last = String(specs[specs.length - 1].id);
  await expect.poll(async () => {
    if (await banner.isVisible().catch(() => false))
      await banner.locator("button", { hasText: "Apply now" })
        .click().catch(() => { /* raced with its own apply */ });
    return (await scene(page)).some((e) => e.id === last);
  }, { timeout: 20_000,
       message: `seed never reached the tab. apply said: ${out.stdout}${out.stderr}` })
    .toBe(true);
}

/** Leave nothing pinned, whatever an earlier test in this worker did.
 *
 * The canvas fixture is worker-scoped, so pins SAVED by one test are
 * still there for the next one, and the empty-canvas item flips to
 * "Unpin ALL" when they are — a test that assumes it reads "Pin ALL"
 * silently measures the opposite gesture.
 * @param page The page under test.
 * @param spot A scene point clear of every element.
 */
async function ensureNothingPinned(page: import("@playwright/test").Page,
                                   spot: { x: number; y: number }) {
  const item = page.locator("li.wg-pin-all");
  // Two passes, because the item only reads "Unpin ALL" when EVERYTHING
  // in scope is pinned. A partly-pinned artifact — the ordinary state
  // after any earlier test saved a pin — reads "Pin ALL", so the way to
  // "nothing pinned" from there is through "everything pinned".
  for (let pass = 0; pass < 2; pass++) {
    if (!(await scene(page)).some((e) => e.locked)) return;
    await openCanvasMenu(page, spot);
    await expect(item).toBeVisible({ timeout: 5000 });
    await item.click();
    await page.waitForTimeout(150);
  }
  await expect.poll(async () => (await scene(page)).some((e) => e.locked),
    { timeout: 5000 }).toBe(false);
}

/** The current head revision number, from the server. */
const revn = (canvas: { cli: (...a: string[]) => { stdout: string } }): number =>
  Number(/(?:^|\n)REVN=(\d+)/.exec(canvas.cli("status").stdout)?.[1] ?? -1);

/** The saved artifact as it sits on disk. */
const onDisk = (project: string): Array<Record<string, unknown>> =>
  JSON.parse(fs.readFileSync(path.join(project, "project_knowledge",
    "artifacts", "screen.excalidraw"), "utf-8")).elements;

test("the context menu pins an element, and the tack shows it",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "fit" }).click();
    expect((await one(page, "kpi")).locked).toBe(false);
    // the label names ONE element even though clicking the tile selects
    // it and its value text: a pin resolves a selection to its owners
    await clickEl(page, "kpi");
    await rightClickEl(page, "kpi");
    await expect((await pinFlyout(page)).first())
      .toContainText("Pin this element to canvas");
    await (await pinFlyout(page))
      .filter({ hasText: /^Pin .* to canvas$/ }).click();
    await expect.poll(async () => (await one(page, "kpi")).locked,
      { timeout: 5000 }).toBe(true);
    expect((await one(page, "kpi-value")).locked,
      "the value text is the tile's, not a thing to pin separately")
      .toBe(false);
    await expect(page.locator(".toasts")).toContainText("Pinned this element");
    // and the pin is now VISIBLE, which native Excalidraw never makes it
    await hoverEl(page, "kpi");
    await expect(page.locator(".pin-tack")).toBeVisible({ timeout: 5000 });
    expect(canvas.cli("status").stdout).toContain("PENDING=0");
  });

test("a multi-selection gets one tack on its union box, and it pins",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    await seedRects(canvas, page, [
      { id: "pa", x: 120, y: 620, width: 140, height: 70, label: "A" },
      { id: "pb", x: 360, y: 620, width: 140, height: 70, label: "B" },
    ]);
    await reveal(page, ["pa", "pb"]);
    // two or more selected elements drove no UI at all before the tack:
    // every anchored surface in this app is `selIds.length === 1 ? … : null`
    await clickEl(page, "pa");
    await page.keyboard.down("Shift");
    await clickEl(page, "pb");
    await page.keyboard.up("Shift");
    const tack = page.locator(".pin-tack.group");
    await expect(tack).toBeVisible({ timeout: 5000 });
    await tack.click();
    await expect.poll(async () => (await one(page, "pa")).locked,
      { timeout: 5000 }).toBe(true);
    expect((await one(page, "pb")).locked).toBe(true);
    await expect(page.locator(".toasts")).toContainText("Pinned 2 elements");
    // one badge for the pair, not one each — the group tack is the union
    await expect(page.locator(".pin-tack.group")).toHaveCount(1);
  });

test("clicking the tack unpins — the path a locked element cannot reach itself",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "fit" }).click();
    await pinViaMenu(page, "kpi");
    await expect.poll(async () => (await one(page, "kpi")).locked,
      { timeout: 5000 }).toBe(true);
    // nothing may select it now, so the tack has to come from scene state
    await clickEl(page, "kpi");
    await hoverEl(page, "kpi");
    const tack = page.locator(".pin-tack").first();
    await expect(tack).toBeVisible({ timeout: 5000 });
    await tack.click();
    await expect.poll(async () => (await one(page, "kpi")).locked,
      { timeout: 5000 }).toBe(false);
    await expect(page.locator(".toasts")).toContainText("Unpinned this element");
    await expect(page.locator(".pin-tack")).toHaveCount(0);
  });

test("Pin ALL takes the owners and leaves the derived pieces alone; " +
  "the same item then reads Unpin ALL",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "fit" }).click();
    const spot = await emptySpot(page);
    await ensureNothingPinned(page, spot);
    await openCanvasMenu(page, spot);
    const item = page.locator("li.wg-pin-all");
    await expect(item).toBeVisible({ timeout: 5000 });
    await expect(item).toContainText("Pin ALL Elements to Canvas");
    await item.click();
    await expect.poll(async () => (await one(page, "kpi")).locked,
      { timeout: 5000 }).toBe(true);
    const live = await scene(page);
    // every OWNER the fixture seeded
    for (const id of ["frame1", "frame2", "cb", "kpi", "body", "card"])
      expect(live.find((e) => e.id === id)?.locked,
        `${id} should be pinned`).toBe(true);
    // and nothing whose position is somebody else's to decide
    for (const e of live)
      if (e.role === "label" || e.role === "pin" || e.role === "decoration" ||
          e.containerId)
        expect(e.locked, `${e.id} (${e.role || "bound"}) must not be pinned`)
          .toBe(false);
    expect(live.some((e) => !e.locked),
      "the fixture must contain derived pieces for this test to mean anything")
      .toBe(true);
    // the item flips once everything in scope is pinned
    await openCanvasMenu(page, spot);
    const back = page.locator("li.wg-pin-all");
    await expect(back).toContainText("Unpin ALL Elements", { timeout: 5000 });
    await back.click();
    await expect.poll(async () => (await scene(page)).some((e) => e.locked),
      { timeout: 5000 }).toBe(false);
  });

test("the “always show pin icon” preference persists across a reload",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "fit" }).click();
    await pinViaMenu(page, "kpi");
    await expect.poll(async () => (await one(page, "kpi")).locked,
      { timeout: 5000 }).toBe(true);
    // off by default: pointer away and nothing selected means no tack
    await clickEl(page, "kpi");
    await page.mouse.move(5, 5);
    await expect(page.locator(".pin-tack")).toHaveCount(0);
    await rightClickEl(page, "kpi");
    await (await pinFlyout(page))
      .filter({ hasText: "Always show pin icon" }).click();
    await page.mouse.move(5, 5);
    await expect(page.locator(".pin-tack")).toHaveCount(1, { timeout: 5000 });
    // the pin itself has to survive the reload too, so record it
    await page.locator(".save-btn").click();
    await expect.poll(() => onDisk(canvas.project)
      .some((e) => e.id === "kpi" && e.locked), { timeout: 10_000 }).toBe(true);
    await page.reload();
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    await page.locator("button", { hasText: "fit" }).click();
    await page.mouse.move(5, 5);
    await expect(page.locator(".pin-tack")).toHaveCount(1, { timeout: 10_000 });
  });

test("a pinned element resists a drag that moves an unpinned one",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    await seedRects(canvas, page, [
      { id: "da", x: 120, y: 780, width: 140, height: 70, label: "pinned" },
      { id: "db", x: 360, y: 780, width: 140, height: 70, label: "free" },
    ]);
    await reveal(page, ["da", "db"]);
    await pinViaMenu(page, "da");
    await expect.poll(async () => (await one(page, "da")).locked,
      { timeout: 5000 }).toBe(true);
    /** Drag an element by a screen-space delta. */
    const drag = async (id: string) => {
      const c = await centre(page, id);
      const p = await at(page, c.x, c.y);
      await page.mouse.move(p.x, p.y);
      await page.mouse.down();
      await page.mouse.move(p.x + 60, p.y + 40, { steps: 8 });
      await page.mouse.up();
    };
    const pinnedBefore = await one(page, "da");
    await drag("da");
    const pinnedAfter = await one(page, "da");
    expect(pinnedAfter.x, "a pinned element moved").toBe(pinnedBefore.x);
    expect(pinnedAfter.y, "a pinned element moved").toBe(pinnedBefore.y);
    // the other pole: the identical gesture on its unpinned twin must
    // move it, or the test above proved only that no drag was delivered
    const freeBefore = await one(page, "db");
    await drag("db");
    await expect.poll(async () => (await one(page, "db")).x,
      { timeout: 5000 }).not.toBe(freeBefore.x);
  });

test("pinning N elements is ONE save and ONE revision",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "fit" }).click();
    // start from nothing pinned AND nothing unsaved, so the save under
    // test carries the pins and only the pins
    await ensureNothingPinned(page, await emptySpot(page));
    await commitBaseline(page);
    await dismissBanners(page);
    const spot = await emptySpot(page);
    const before = revn(canvas);
    const saves = canvas.events().filter((l) => l.includes('"save"')).length;
    await openCanvasMenu(page, spot);
    await page.locator("li.wg-pin-all").click();
    await expect.poll(async () => (await scene(page)).filter((e) => e.locked).length,
      { timeout: 5000 }).toBeGreaterThan(1);
    const pinned = (await scene(page)).filter((e) => e.locked).map((e) => e.id);
    await page.locator(".save-btn").click();
    await expect.poll(() => revn(canvas), { timeout: 10_000 }).toBe(before + 1);
    // N pins, one POST, one revision — never one revision per element
    expect(canvas.events().filter((l) => l.includes('"save"')).length)
      .toBe(saves + 1);
    const disk = onDisk(canvas.project);
    for (const id of pinned)
      expect(disk.find((e) => e.id === id)?.locked, `${id} on disk`).toBe(true);
    // …and there is nothing left over to save. A second revision would
    // mean the pin gesture left the buffer disagreeing with what it sent.
    await expect(page.locator(".save-btn")).toBeDisabled({ timeout: 10_000 });
    expect(revn(canvas)).toBe(before + 1);
  });

test("a bulk pin narrates as a pin, not as a no-op",
  async ({ page, canvas }) => {
    // RED BY INTENT until the server half lands the `locked` fact arm.
    // Flipping `locked` emits no fact today, so a save that does nothing
    // but pin reports "saved without changing anything" while N pins land
    // — the third instance of a class this repo has already fixed twice
    // (`link`, and the tidy pass). Verbatim contract from the server half:
    // fact names `pinned`/`unpinned`, headline "pinned <label> to the
    // canvas", `(+N more)` supplied by `mechanical_summary`.
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "fit" }).click();
    await ensureNothingPinned(page, await emptySpot(page));
    // Burn one save first. Restoring a scene re-measures bound text and
    // re-sorts the element array, so the FIRST save a page makes always
    // carries that drift — on this fixture "resized cb-chk … 1× resized,
    // 5× reordered" — no matter what the user did. It is not dirty (the
    // baseline fingerprint is taken from whatever the canvas settled on),
    // so it cannot be committed until something else is. Pinning one
    // element is that something; the save under test comes after.
    await pinViaMenu(page, "kpi");
    await commitBaseline(page);
    await dismissBanners(page);
    const spot = await emptySpot(page);
    await openCanvasMenu(page, spot);
    await page.locator("li.wg-pin-all").click();
    await expect.poll(async () => (await scene(page)).filter((e) => e.locked).length,
      { timeout: 5000 }).toBeGreaterThan(1);
    const n = (await scene(page)).filter((e) => e.locked).length;
    // this save now contains NOTHING but lock flips, which is what makes
    // the headline a statement about pinning rather than about drift
    await page.locator(".save-btn").click();
    const banner = page.locator(".banner.narration");
    await expect(banner).toBeVisible({ timeout: 10_000 });
    await expect(banner).not.toContainText("saved without changing anything");
    await expect(banner).toContainText(/pinned .* to the canvas/);
    const line = canvas.events().filter((l) => l.includes('"save"')).pop() || "";
    expect(line).toMatch(/pinned .* to the canvas/);
    expect(n).toBeGreaterThan(1);
  });
