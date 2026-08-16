import { testMini as test, expect } from "./harness";

/** Where a user's own insert lands (curator batch 15, item 2).
 *
 * r5-10 fixed one half of this for the ⌗ dialog only: an import used to
 * centre on the viewport, so a paste landed 36 shapes on top of the
 * artifact you had open, and it now goes clear of everything live. The
 * other four insert sites — `addStickyNote`, `insertFrame`, `askUserPin`
 * and `insertTemplate` in `App.tsx` — still place at `sceneCenter()`.
 *
 * A note dropped squarely on a node is the r5-13 shape (a note that then
 * trips ART-011 on every load) arriving by the user's own hand, and the
 * viewport centre is over a node exactly when the user has zoomed in on
 * one to comment about it — the moment they reach for 🗒. Only a browser
 * can see this: the placement is computed from the live camera, which
 * exists nowhere in the backend suite.
 *
 * The sticky note stands for its three siblings. It is the cheapest to
 * drive and the only one of the four whose overlap has a named
 * downstream consequence; a fix that clears the drop belongs in a helper
 * all four call, and this spec is what proves the first of them.
 *
 * WHO FLIPS THIS: the e2e placement task, which owns all four insert
 * sites. Written in by curator batch 26 (2026-08-16) from the red-owner
 * map: this was one of five reds whose owner existed at plan level and
 * in no file, and it is the only one of the five outside the Python
 * suite — so it is also the only one no census guard would ever have
 * counted as missing. The header note is the routing for this red by
 * the convention batch 26 recorded; `test.fail()` below carries the
 * redness, and the two must be removed in the same change as the fix.
 */

type Box = { id: string; type: string; x: number; y: number;
             w: number; h: number };

/** Read the live scene as plain boxes, through the app's own API handle. */
const boxes = (page: import("@playwright/test").Page): Promise<Box[]> =>
  page.evaluate(() =>
    (window as unknown as { excalidrawAPI: {
      getSceneElements: () => Array<Record<string, unknown>> } })
      .excalidrawAPI.getSceneElements()
      .filter((e) => !e.isDeleted)
      .map((e) => ({ id: e.id as string, type: e.type as string,
                     x: e.x as number, y: e.y as number,
                     w: (e.width as number) || 0,
                     h: (e.height as number) || 0 })));

/** Click 🗒 and answer its prompt, returning the elements it added.
 *
 * `window.prompt` is auto-dismissed by Playwright unless a handler
 * accepts it, and a dismissed prompt returns null — which `addStickyNote`
 * reads as "cancelled" and inserts nothing at all.
 */
async function addNote(page: import("@playwright/test").Page, text: string,
                       before: Box[]): Promise<Box[]> {
  page.once("dialog", (d) => d.accept(text));
  await page.locator("button[title^='add a sticky note']").click();
  await expect(page.locator(".toasts")).toContainText("Note added",
    { timeout: 10_000 });
  const known = new Set(before.map((e) => e.id));
  return (await boxes(page)).filter((e) => !known.has(e.id));
}

/** Ids of the boxes a rectangle covers any part of.
 *
 * Frames are excluded deliberately: a frame is a container the user
 * draws things INSIDE, so landing within one is not the harm — landing
 * on a node, a label or another note is. That also keeps the assertion
 * true under either shape of fix (drop clear of the whole drawing, as
 * the ⌗ dialog does, or nudge clear of the content within a frame).
 */
const covered = (note: Box, live: Box[]): string[] =>
  live.filter((e) => e.type !== "frame" &&
    note.x < e.x + e.w && e.x < note.x + note.w &&
    note.y < e.y + e.h && e.y < note.y + note.h).map((e) => e.id);

test("a sticky note lands clear of the tile the user is looking at",
  async ({ page, canvas }) => {
    test.fail();   // RED BY INTENT — `addStickyNote` uses sceneCenter()
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    // the user zooms in on the KPI tile to ask about it, which is what
    // puts the viewport centre over a node
    await page.evaluate(() => {
      const api = (window as unknown as { excalidrawAPI: {
        getSceneElements: () => Array<{ id: string }>;
        scrollToContent: (e: unknown, o: unknown) => void } }).excalidrawAPI;
      const kpi = api.getSceneElements().find((e) => e.id === "kpi");
      api.scrollToContent([kpi], { fitToViewport: true,
                                   viewportZoomFactor: 0.5 });
    });
    await expect.poll(() => page.evaluate(() =>
      Math.round((window as unknown as { __sceneToScreen?:
        (a: number, b: number) => { x: number } | null })
        .__sceneToScreen?.(226, 240)?.x ?? -1)),
      { timeout: 10_000 }).toBeGreaterThan(0);
    const before = await boxes(page);
    const fresh = await addNote(page, "62 out of what?", before);
    const note = fresh.find((e) => e.type === "rectangle");
    expect(note, "no note rectangle was inserted").toBeTruthy();
    // the whole claim: the note buried nothing. It lands at (136,195)
    // 180x90 today, wholly inside the 292x120 KPI tile at (80,180).
    expect(covered(note as Box, before)).toEqual([]);
  });

test("a sticky note dropped on clear canvas stays where the user looks",
  async ({ page, canvas }) => {
    // The live half, and the opposite pole: when the viewport centre IS
    // clear, a centred drop is the right answer and must survive the
    // fix. It also proves the red above is about placement — the button,
    // the prompt, the insert and the geometry read are all exercised
    // here without the expected-failure mask.
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    await page.evaluate(() => {
      (window as unknown as { excalidrawAPI: {
        updateScene: (s: unknown) => void } }).excalidrawAPI
        .updateScene({ appState: { scrollX: -3000, scrollY: -3000 } });
    });
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __sceneToScreen?:
        (a: number, b: number) => { x: number } | null })
        .__sceneToScreen?.(3000, 3000)?.x ?? -1),
      { timeout: 10_000 }).toBeLessThan(900);
    const before = await boxes(page);
    const fresh = await addNote(page, "room to think", before);
    const note = fresh.find((e) => e.type === "rectangle") as Box;
    expect(note, "no note rectangle was inserted").toBeTruthy();
    expect(covered(note, before)).toEqual([]);
    // and it went where the user was looking, not off to the side
    expect(note.x).toBeGreaterThan(3000);
    expect(note.y).toBeGreaterThan(3000);
  });
