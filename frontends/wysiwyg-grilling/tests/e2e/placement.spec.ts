import { testMini as test, expect } from "./harness";

/** Where a user's own insert lands (curator batch 15, item 2).
 *
 * r5-10 fixed one half of this for the ⌗ dialog only: an import used to
 * centre on the viewport, so a paste landed 36 shapes on top of the
 * artifact you had open, and it now goes clear of everything live. The
 * other insert sites — `addStickyNote`, `insertFrame`, `askUserPin` and
 * `insertTemplate` in `App.tsx` — still placed at `sceneCenter()`.
 *
 * A note dropped squarely on a node is the r5-13 shape (a note that then
 * trips ART-011 on every load) arriving by the user's own hand, and the
 * viewport centre is over a node exactly when the user has zoomed in on
 * one to comment about it — the moment they reach for 🗒. Only a browser
 * can see this: the placement is computed from the live camera, which
 * exists nowhere in the backend suite.
 *
 * FLIPPED by the e2e placement task (v0.9), which owns all four sites.
 * Three of them now take their top-left from `dropClear`, the shared
 * helper the header called for: it returns the viewport centre untouched
 * when that is clear, and otherwise slides the insert out from under the
 * content by the shortest trip that is still on screen. The fourth,
 * `askUserPin`, never used `sceneCenter` at all — it is anchored to the
 * element the question is about, so it took the OTHER half of the fix:
 * `pinSpot`, the client mirror of `canvas.py`'s `pin_spot`, which hugs
 * the target and falls inside its own corner rather than into a
 * neighbour (r4b-3, which the agent's seeder learned and the user's ❓
 * button had not).
 *
 * Three tests, both poles: the buried drop is cleared; a drop that was
 * already clear does not move; and the template site — a sibling of the
 * one the note stands for — clears the same tile with a 420px archetype.
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

/**
 * The live camera, read from the app's own appState.
 *
 * The same four numbers the placement is computed from, so a test can
 * derive where a drop SHOULD have gone instead of bounding it loosely —
 * and can watch whether the insert moved the view.
 * @param page The page under test.
 * @returns Scroll offsets, zoom, and the canvas size in css px.
 */
const camera = (page: import("@playwright/test").Page):
  Promise<{ scrollX: number; scrollY: number; zoom: number;
            width: number; height: number }> =>
  page.evaluate(() => {
    const a = (window as unknown as { excalidrawAPI: {
      getAppState: () => { scrollX: number; scrollY: number;
                           zoom: { value: number };
                           width: number; height: number } } })
      .excalidrawAPI.getAppState();
    return { scrollX: a.scrollX, scrollY: a.scrollY, zoom: a.zoom.value,
             width: a.width, height: a.height };
  });

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
    const note = fresh.find((e) => e.type === "rectangle") as Box;
    expect(note, "no note rectangle was inserted").toBeTruthy();
    // the whole claim: the note buried nothing. Before the fix it landed
    // at (136,195) 180x90, wholly inside the 292x120 KPI tile at (80,180).
    expect(covered(note, before)).toEqual([]);
    // its bound label rode along — a note whose text stayed on the tile
    // would clear the assertion above and still bury the thing
    const label = fresh.find((e) => e.type === "text") as Box;
    expect(label, "the note's label was not inserted").toBeTruthy();
    expect(covered(label, before)).toEqual([]);
    // and the user can SEE it: clearing the tile is worthless if the note
    // goes somewhere off screen, which zoomed this far in it must, so the
    // view follows it
    await expect.poll(() => page.evaluate(([x, y]) => {
      const p = (window as unknown as { __sceneToScreen?:
        (a: number, b: number) => { x: number; y: number } | null })
        .__sceneToScreen?.(x, y);
      return p && p.x > 0 && p.x < 1440 && p.y > 0 && p.y < 900;
    }, [note.x + note.w / 2, note.y + note.h / 2]),
      { timeout: 10_000 }).toBe(true);
    // it went to the nearest clear air, not off to the far side of the
    // drawing: the seeded screen is ~600x400, and a note flung clear of
    // EVERYTHING (the ⌗ import's coarser rule) would be past all of it
    expect(Math.abs(note.x - 136)).toBeLessThan(400);
    expect(Math.abs(note.y - 195)).toBeLessThan(400);
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
    const cam = await camera(page);
    const fresh = await addNote(page, "room to think", before);
    const note = fresh.find((e) => e.type === "rectangle") as Box;
    expect(note, "no note rectangle was inserted").toBeTruthy();
    expect(covered(note, before)).toEqual([]);
    // and it went where the user was looking — the EXACT centre of the
    // view, derived from the same appState the app placed it from, not
    // merely somewhere off in the right half of the canvas. A nudge of a
    // note-width is a regression and has to read as one.
    expect(note.x).toBeCloseTo(
      cam.width / 2 / cam.zoom - cam.scrollX - note.w / 2, 3);
    expect(note.y).toBeCloseTo(
      cam.height / 2 / cam.zoom - cam.scrollY - note.h / 2, 3);
    // ...and the camera did not move to show it.
    const after = await camera(page);
    expect(after.scrollX).toBe(cam.scrollX);
    expect(after.scrollY).toBe(cam.scrollY);
    expect(after.zoom).toBe(cam.zoom);

    // A SECOND note is where the camera's restraint can actually be
    // caught. The assertion above cannot fail on its own: a centred drop
    // sits on the view centre, so "recentre on the insert" is the
    // identity and a `revealInsert` that always scrolled would pass it.
    // The centre is now taken, so this note steps aside — and at this
    // zoom the clear air it steps into is still in view, which is
    // precisely when the view must hold still. Without the in-view early
    // return the camera walks after every insert, one note at a time.
    const known = await boxes(page);
    const second = await addNote(page, "and another", known);
    const note2 = second.find((e) => e.type === "rectangle") as Box;
    expect(note2, "no second note was inserted").toBeTruthy();
    expect(covered(note2, known)).toEqual([]);
    expect(note2.y, "the second note did not step aside")
      .not.toBeCloseTo(note.y, 3);
    const held = await camera(page);
    expect(held.scrollX).toBe(cam.scrollX);
    expect(held.scrollY).toBe(cam.scrollY);
    expect(held.zoom).toBe(cam.zoom);
    // it stepped into air the user could already see — the reason the
    // view had nothing to follow
    const seen = await page.evaluate(([x, y]) => {
      const p = (window as unknown as { __sceneToScreen?:
        (a: number, b: number) => { x: number; y: number } | null })
        .__sceneToScreen?.(x, y);
      return p && p.x > 0 && p.x < 1440 && p.y > 0 && p.y < 900;
    }, [note2.x + note2.w / 2, note2.y + note2.h / 2]);
    expect(seen, "the second note landed off screen").toBe(true);
  });

test("an archetype template clears the tile too, not just the note",
  async ({ page, canvas }) => {
    // The sibling site. The note is the cheap one to drive; the template
    // is the one that would do the most damage, because it arrives as a
    // whole screen — 420x308 of boxes and bound labels — and a fix
    // written for a 180x90 note would leave it burying the tile it was
    // sized against. Same camera, same tile, same claim.
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
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
    await page.locator("button[title^='drop a screen frame']").click();
    await page.locator(".insert-menu .item", { hasText: "Dashboard grid" })
      .click();
    await expect(page.locator(".toasts")).toContainText("Dashboard grid added",
      { timeout: 10_000 });
    const known = new Set(before.map((e) => e.id));
    const fresh = (await boxes(page)).filter((e) => !known.has(e.id));
    expect(fresh.length, "the template inserted nothing").toBeGreaterThan(5);
    // every piece of it, boxes and labels alike, landed on clear canvas
    for (const e of fresh) expect(covered(e, before), e.id).toEqual([]);
  });
