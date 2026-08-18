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
 *
 * CURATOR BATCH 31 adds the two client-behaviour families batches 27, 29
 * and 30 each deferred for want of a browser: `elBox`'s occupied box for
 * a point-strung element, and `pinSpot`'s hug. Both were pinned in the
 * model tier as SOURCE READS, which is all that tier can do with a
 * function living in `App.tsx`; these are the same claims driven through
 * the running app, so the drawn extent is measured off the app's own
 * pixels rather than inferred from the model.
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

/* ==================================================================
 * elBox — the occupied box of a point-strung element.
 *
 * Curator batch 31, item 1: the BROWSER arm of batch 30's item 5. That
 * batch measured the sign bug over the frozen corpus — 68 of 194
 * point-strung elements have `elBox` overhang their own point hull, 63
 * of them past `LANE_TOL`, worst 600px — and could only pin it in the
 * model tier as the absence of a source expression, which its own
 * report called the weakest pin in the batch. This is the behavioural
 * half it named as owed.
 *
 * The mechanism: `Math.max(e.width || 0, ...xs)` maximises a stored
 * MAGNITUDE against a point COORDINATE. For a rightward arrow the two
 * coincide; for a leftward or upward one the stored width wins and the
 * box grows a phantom half on the side the arrow never reaches. `elBox`
 * feeds `dropClear`, so every ＋ insert and every 🗒 note is pushed out
 * of canvas that is empty in the picture.
 * ================================================================== */

/** A rectangle in scene coordinates. */
type Rect = { x: number; y: number; w: number; h: number };

/** What the app's own canvas has ink on, in scene coordinates. */
type Ink = { n: number; x0: number; y0: number; x1: number; y1: number };

/** A point-strung arrow, unbound, drawn without roughjs jitter.
 *
 * `roughness: 0` is not cosmetic: the ink reading below is compared to
 * the arrow's own points to within a stroke width, and a seeded sketchy
 * stroke wanders ~1.2px on its own.
 * @param x Stored origin x — where `points[0]` sits.
 * @param y Stored origin y.
 * @param dx Run of the single segment; negative points the arrow left.
 * @param dy Rise of the single segment; negative points it up.
 * @returns One Excalidraw arrow element, ready for `updateScene`.
 */
const strokeArrow = (x: number, y: number, dx: number, dy: number) => ({
  id: "a", type: "arrow", x, y,
  width: Math.abs(dx), height: Math.abs(dy),
  points: [[0, 0], [dx, dy]],
  strokeColor: "#1e1e1e", backgroundColor: "transparent",
  fillStyle: "solid", strokeWidth: 2, strokeStyle: "solid",
  roughness: 0, opacity: 100, angle: 0, seed: 1, version: 1,
  versionNonce: 1, isDeleted: false, groupIds: [], frameId: null,
  roundness: null, boundElements: null, updated: 1, link: null,
  locked: false, startBinding: null, endBinding: null,
  startArrowhead: null, endArrowhead: null,
});

/**
 * Park the camera at a known zoom and report the scene point it centres.
 *
 * The zoom is SET rather than read because every scene below is built
 * around the returned centre and has to fit on screen whole: a clipped
 * arrow would make the ink reading measure the viewport instead of the
 * drawing. At 0.5 the visible half-extents are ~1136x644 scene px
 * against the 450x350 the largest scene here needs.
 * @param page The page under test.
 * @param zoom The zoom to park at.
 * @returns The scene coordinate the viewport centre lands on.
 */
async function centreAt(page: import("@playwright/test").Page,
                        zoom: number): Promise<{ x: number; y: number }> {
  await page.evaluate((z) => {
    (window as unknown as { excalidrawAPI: {
      updateScene: (s: unknown) => void } }).excalidrawAPI
      .updateScene({ appState: { zoom: { value: z } } });
  }, zoom);
  const cam = await camera(page);
  return { x: cam.width / 2 / cam.zoom - cam.scrollX,
           y: cam.height / 2 / cam.zoom - cam.scrollY };
}

/** Replace the live scene with a constructed one and let it settle.
 *
 * Through `updateScene`, not through the server: `dropClear` and
 * `pinSpot` read `excalidrawAPI.getSceneElements()`, so this is the
 * exact input the placement code takes, and nothing is written to the
 * worker's shared project.
 * @param page The page under test.
 * @param els The whole scene.
 * @returns Nothing; resolves once the canvas has repainted.
 */
async function putScene(page: import("@playwright/test").Page,
                        els: unknown[]): Promise<void> {
  await page.evaluate((e) => {
    (window as unknown as { excalidrawAPI: {
      updateScene: (s: unknown) => void } }).excalidrawAPI
      .updateScene({ elements: e });
  }, els);
  await page.waitForTimeout(900);
}

/**
 * Where the app actually put ink, read off its own canvas.
 *
 * THE PIXELS, not the model. The whole point of this arm is that a claim
 * about a drawn extent settled from stored geometry is the same claim
 * `elBox` gets wrong, so it is settled here from `getImageData` on the
 * static Excalidraw canvas and converted back through the live camera.
 * @param page The page under test.
 * @param box The scene rectangle to look inside.
 * @returns The count and bounding box of ink within `box`, in scene px.
 *   The bounds are zero when nothing is inked there.
 */
async function inkIn(page: import("@playwright/test").Page,
                     box: Rect): Promise<Ink> {
  return page.evaluate((r) => {
    const api = (window as unknown as { excalidrawAPI: {
      getAppState: () => { scrollX: number; scrollY: number;
                           zoom: { value: number }; width: number } } })
      .excalidrawAPI;
    const a = api.getAppState();
    const cv = document.querySelector(
      "canvas.excalidraw__canvas.static") as HTMLCanvasElement;
    const ctx = cv.getContext("2d");
    if (!ctx) throw new Error("no 2d context on the static canvas");
    const dpr = cv.width / (a.width || 1), z = a.zoom.value;
    const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
    let x0 = 0, y0 = 0, x1 = 0, y1 = 0, n = 0;
    for (let py = 0; py < cv.height; py++) {
      for (let px = 0; px < cv.width; px++) {
        const i = (py * cv.width + px) * 4;
        if (d[i + 3] <= 40) continue;
        if (d[i] >= 200 && d[i + 1] >= 200 && d[i + 2] >= 200) continue;
        const sx = px / dpr / z - a.scrollX, sy = py / dpr / z - a.scrollY;
        if (sx < r.x || sx > r.x + r.w || sy < r.y || sy > r.y + r.h)
          continue;
        if (!n) { x0 = sx; y0 = sy; x1 = sx; y1 = sy; }
        n++;
        if (sx < x0) x0 = sx; if (sx > x1) x1 = sx;
        if (sy < y0) y0 = sy; if (sy > y1) y1 = sy;
      }
    }
    return { n, x0, y0, x1, y1 };
  }, box);
}

/** The whole visible canvas, as a scene rectangle to read ink inside. */
const EVERYWHERE = (c: { x: number; y: number }): Rect =>
  ({ x: c.x - 4000, y: c.y - 4000, w: 8000, h: 8000 });

/** Assert a measured scene coordinate is `want` within a stroke width.
 * @param got The measured coordinate.
 * @param want The coordinate the arrow's own points put it at.
 * @param what What is being measured, for the failure message.
 * @returns Nothing.
 */
function atPx(got: number, want: number, what: string): void {
  expect(Math.round(got - want),
    `${what}: measured ${Math.round(got)}, points say ${Math.round(want)}`)
    .toBeGreaterThanOrEqual(-3);
  expect(Math.round(got - want),
    `${what}: measured ${Math.round(got)}, points say ${Math.round(want)}`)
    .toBeLessThanOrEqual(3);
}

test("a leftward arrow inks only the half of its box it reaches",
  async ({ page, canvas }) => {
    // THE MAGNITUDE, measured in the client's own pixels, and the live
    // half of the red below: an arrow strung 300px left and 200px up
    // from its stored origin draws a 300x200 stroke ending AT that
    // origin, while `elBox` answers 600x400 spanning 300px past it in x
    // and 200px in y. Nothing here reads the stored width, so it cannot
    // inherit the defect it is measuring.
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    const c = await centreAt(page, 0.5);
    await putScene(page, [strokeArrow(c.x - 150, c.y - 150, -300, -200)]);
    const all = await inkIn(page, EVERYWHERE(c));
    expect(all.n, "the arrow drew nothing at all").toBeGreaterThan(50);
    atPx(all.x0, c.x - 450, "the arrow's left end");
    atPx(all.y0, c.y - 350, "the arrow's top end");
    atPx(all.x1, c.x - 150, "the arrow's right end");
    atPx(all.y1, c.y - 150, "the arrow's bottom end");
    // and the half `elBox` invents is empty canvas, by count. The window
    // starts 10px inside the last point so the stroke's own width and
    // its anti-aliased skirt cannot be mistaken for phantom ink.
    const phantom = await inkIn(page,
      { x: c.x - 140, y: c.y - 140, w: 300, h: 200 });
    expect(phantom.n,
      "the client drew ink in the quadrant elBox invents, so this scene "
      + "no longer separates the stored box from the drawing")
      .toBe(0);
  });

test("a note dropped in that empty quadrant does not move",
  async ({ page, canvas }) => {
    // The consequence, driven end to end. The viewport centre sits in
    // the quadrant the test above measured as blank, so `clearSpot`'s
    // first line — "a drop that is ALREADY clear is returned untouched"
    // — is the whole expected behaviour and the note must land exactly
    // on the centred want.
    //
    // FLIPPED 2026-08-17 by v0.9 TASK-ELBOX, with the assertion
    // untouched as this comment required. It was RED BY INTENT under
    // `test.fail()` while `elBox` unioned the stored width in: the note
    // landed 119px lower, because `elBox` put the arrow's box at
    // y1 = c.y + 50, the down candidate stepped to y1 + DROP_GAP =
    // c.y + 74, and the want was c.y - 45. Point-strung classes are now
    // bound by their points, so the box ends at the ink (y1 = c.y - 150)
    // and the centred want is already clear.
    //
    // WATCHED FLIPPING, not assumed: run against the fixed source before
    // the bundle was rebuilt, this test still failed as declared — the
    // browser was serving the OLD bundle. That is the trap this pair is
    // most exposed to, and it is why the rebuild is its own commit. The
    // flip only appeared once the bundle carried the fix, as Playwright's
    // "Expected to fail, but passed".
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    const c = await centreAt(page, 0.5);
    await putScene(page, [strokeArrow(c.x - 150, c.y - 150, -300, -200)]);
    const before = await boxes(page);
    const fresh = await addNote(page, "on clear canvas", before);
    const note = fresh.find((e) => e.type === "rectangle") as Box;
    expect(note, "no note rectangle was inserted").toBeTruthy();
    expect(note.x, "the note moved sideways").toBeCloseTo(c.x - 90, 3);
    expect(note.y, "the note was pushed out of empty canvas")
      .toBeCloseTo(c.y - 45, 3);
  });

test("the mirrored arrow really does fill that quadrant, and moves it",
  async ({ page, canvas }) => {
    // THE OTHER POLE, and the reason the red above is about the sign and
    // not about note placement in general. One variable changes — the
    // segment runs +300/+200 instead of -300/-200 — and `elBox` returns
    // the SAME rectangle it returned for the leftward arrow. Here that
    // rectangle is honest: the ink is measured inside the drop, the
    // avoidance is correct, and the note steps aside by the same 119px.
    //
    // So the two scenes are mirror images that the client cannot tell
    // apart, and this half is the one where it is right.
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    const c = await centreAt(page, 0.5);
    await putScene(page, [strokeArrow(c.x - 150, c.y - 150, 300, 200)]);
    const want = { x: c.x - 90, y: c.y - 45, w: 180, h: 90 };
    const covering = await inkIn(page, want);
    expect(covering.n,
      "the mirrored arrow draws nothing where the note wants to land, so "
      + "this pole is not the opposite of the red above")
      .toBeGreaterThan(0);
    // and the air it will step into is blank — measured now, while the
    // only thing on the canvas is the arrow. Reading it after the drop
    // would find the note's own stroke and prove nothing.
    const landing = await inkIn(page,
      { x: c.x - 90, y: c.y + 74, w: 180, h: 90 });
    expect(landing.n, "the step-aside lands on ink too").toBe(0);
    const before = await boxes(page);
    const fresh = await addNote(page, "on the arrow", before);
    const note = fresh.find((e) => e.type === "rectangle") as Box;
    expect(note, "no note rectangle was inserted").toBeTruthy();
    expect(note.x, "the note stepped sideways instead of down")
      .toBeCloseTo(c.x - 90, 3);
    expect(note.y, "the note did not clear the arrow it really is on")
      .toBeCloseTo(c.y + 74, 3);
    expect(covered(note, before), "the note buried something").toEqual([]);
  });

/* ==================================================================
 * pinSpot — the ❓ glyph's hug, and its collision fallback.
 *
 * Curator batch 31, item 2: the arm batch 30 could only reach as a
 * constants transcription. `TestThePinSpotMirrorCarriesTheServersConstants`
 * (tests/test_mutants.py) holds the 8 / -8 / 26 / -2 / +2 offsets and
 * both membership filters against `canvas.pin_spot`; what it cannot do
 * is execute the predicate that chooses between the two arms. These
 * two drive it, in the app, through the button a user presses.
 *
 * The glyph is a `text` element with `autoResize: true`, so Excalidraw
 * re-measures the string on load and the stored `x` is no longer the
 * one `pinSpot` returned — a centred 12px glyph inside the 26px box it
 * was minted with sits 7px right of it. The assertions are therefore on
 * the glyph's CENTRE, which is invariant under that re-measure, and on
 * `y`, which `verticalAlign: "top"` leaves alone.
 * ================================================================== */

/** A plain rectangle, for use as a pin target or as its neighbour.
 * @param id The element id.
 * @param x Top-left x.
 * @param y Top-left y.
 * @param w Width.
 * @param h Height.
 * @returns One Excalidraw rectangle, ready for `updateScene`.
 */
const plainRect = (id: string, x: number, y: number, w: number, h: number
) => ({
  id, type: "rectangle", x, y, width: w, height: h,
  strokeColor: "#1e1e1e", backgroundColor: "transparent",
  fillStyle: "solid", strokeWidth: 2, strokeStyle: "solid",
  roughness: 0, opacity: 100, angle: 0, seed: 1, version: 1,
  versionNonce: 1, isDeleted: false, groupIds: [], frameId: null,
  roundness: null, boundElements: null, updated: 1, link: null,
  locked: false,
});

/** Select `t`, press ❓, answer the prompt, return the glyph it minted.
 * @param page The page under test.
 * @param els The scene to install first.
 * @returns The ❓ element as a plain box.
 */
async function askPin(page: import("@playwright/test").Page,
                      els: unknown[]): Promise<Box> {
  await page.evaluate((e) => {
    (window as unknown as { excalidrawAPI: {
      updateScene: (s: unknown) => void } }).excalidrawAPI.updateScene(
      { elements: e, appState: { selectedElementIds: { t: true } } });
  }, els);
  await page.waitForTimeout(600);
  const before = await boxes(page);
  page.once("dialog", (d) => d.accept("why this one?"));
  await page.locator("button[title^='pin a question']").click();
  await expect(page.locator(".toasts")).toContainText("Question pinned",
    { timeout: 10_000 });
  const known = new Set(before.map((e) => e.id));
  const fresh = (await boxes(page)).filter((e) => !known.has(e.id));
  expect(fresh.length, "❓ inserted nothing").toBe(1);
  return fresh[0];
}

test("an unoccupied hug spot keeps the ❓ glyph's +8/-8 offset",
  async ({ page, canvas }) => {
    // The quiet arm, and the one every flow scene takes: with hundreds
    // of px of air around the target the glyph hangs off its top-right
    // corner, 8px out and 8px up, exactly where `marker_anchor` puts it.
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    const g = await askPin(page, [plainRect("t", 0, 0, 200, 100)]);
    expect(g.x + g.w / 2, "the glyph is not hugging the top-right corner")
      .toBeCloseTo(200 + 8 + 13, 3);
    expect(g.y, "the glyph is not lifted clear of the top edge")
      .toBeCloseTo(-8, 3);
  });

test("a buried hug spot falls back inside the target's own corner",
  async ({ page, canvas }) => {
    // THE ARM NOTHING HAS EVER WATCHED RUN. r4b-3 is what it exists for:
    // on a tight grid the constant offset put three glyphs inside the
    // NEXT panel, so they read as questions about that panel. The
    // neighbour here is placed to overlap the 26px hug square and
    // nothing else — (220,-20) 100x80 against a hug square spanning
    // (208,-8)..(234,18) — so the only thing that can move the glyph is
    // the collision test.
    await page.goto(canvas.url);
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    const g = await askPin(page, [plainRect("t", 0, 0, 200, 100),
                                  plainRect("nb", 220, -20, 100, 80)]);
    expect(g.x + g.w / 2, "the glyph did not fall back inside the target")
      .toBeCloseTo(200 - 26 - 2 + 13, 3);
    expect(g.y, "the glyph did not drop to the target's own top edge")
      .toBeCloseTo(2, 3);
    // the harm the fallback exists to prevent, asserted rather than
    // assumed: the 26px glyph box lands wholly inside its target and
    // touches no part of the neighbour it would have been read against.
    // Measured on the box `pinSpot` minted, not on the re-measured
    // string, because it is the mint the placement rule is about.
    const g26 = { x: g.x + g.w / 2 - 13, y: g.y, w: 26, h: 26 };
    expect(g26.x >= 0 && g26.x + g26.w <= 200
           && g26.y >= 0 && g26.y + g26.h <= 100,
      `the glyph box ${JSON.stringify(g26)} is not wholly inside its `
      + "target (0,0) 200x100").toBe(true);
    expect(g26.x < 320 && 220 < g26.x + g26.w
           && g26.y < 60 && -20 < g26.y + g26.h,
      "the glyph still overlaps the neighbour it was moved off")
      .toBe(false);
  });
