import { testRich as test, expect } from "./harness";

/* R5 §6 left a cross-arm contradiction UNRESOLVED and named the test that
 * settles it. Arm B saw a user rename mint phantom `reordered` facts for every
 * other element; arm A's saves were clean. The suspected axis: arm A edited
 * through `x-as-user`, which rewrites the element array IN PLACE, while a
 * browser save ships `apiRef.current.getSceneElements()` — Excalidraw's own
 * order (App.tsx:990).
 *
 * R5's words: "Discriminating test for run 6 (not run here): rename a single
 * label in the browser tab and Save, with no other change, then read
 * `verb_counts`."
 *
 * This is that test. It asserts nothing about the count on purpose — it
 * MEASURES, and prints both the wire response and a before/after order diff,
 * because the question is which of two isolated assessors was right. The only
 * hard assertions are the ones that make the reading trustworthy: exactly one
 * label changed, and no geometry moved. If those fail the measurement is void
 * and says so, rather than reporting a number from a scene that drifted. */

type El = { id: string; type: string; x: number; y: number;
            w: number; h: number; text?: string; containerId?: string | null };

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
        text: e.text as string | undefined,
        containerId: (e.containerId as string | null) ?? null,
      })));

/** The next `/api/save` response body, as the server actually sent it. */
const saveResponse = (page: import("@playwright/test").Page): Promise<any> =>
  page.waitForResponse((r) => r.url().includes("/api/save") &&
    r.request().method() === "POST", { timeout: 20_000 }).then((r) => r.json());

test("R5 §6 discriminator: one browser rename, nothing else", async ({ page, canvas }) => {
  await page.goto(canvas.url);
  await page.waitForFunction(
    () => (window as any).excalidrawAPI?.getSceneElements?.().length > 0,
    null, { timeout: 30_000 });

  const before = await scene(page);
  // the ORDER the server has on disk, which is what the save is diffed against
  const orderBefore = before.map((e) => e.id);

  // a bound label: a text element that lives inside a container. Renaming one
  // of these is the single most ordinary thing a user does to a diagram.
  // A BOUND label, not a standalone decoration. The first attempt took "the
  // first text longer than 3 chars", which was a `role: decoration` value text
  // with no container; no editor ever opened for it. A bound label is both the
  // thing a user actually renames and the thing the app's own gesture targets.
  const target = before
    .filter((e) => e.type === "text" && (e.text || "").trim().length > 3 && e.containerId)
    .sort((a, b) => {
      const ca = before.find((x) => x.id === a.containerId);
      const cb = before.find((x) => x.id === b.containerId);
      return ((cb?.w ?? 0) * (cb?.h ?? 0)) - ((ca?.w ?? 0) * (ca?.h ?? 0));
    })[0];
  expect(target, "no bound label to rename — fixture changed").toBeTruthy();

  const before_text = target!.text!;
  const NEW = "Renamed by R6";

  // Edit through the app's own path: double-click to enter edit mode, select
  // all, retype. This is a user gesture, not a synthesised payload —
  // hand-rolling the client is exactly what the protocol forbids, because a
  // synthesised payload cannot drift-detect against the real one.
  //
  // Double-click the CONTAINER when the text is bound, and the text's own
  // CENTRE otherwise. The first attempt aimed at a bound label's top-left
  // corner, entered no editor at all, and the guard below correctly voided the
  // measurement rather than reporting verb_counts from a save containing no
  // rename.
  const container = target!.containerId
    ? before.find((e) => e.id === target!.containerId) : undefined;
  const hit = container ?? target!;
  const vp = page.viewportSize();
  const pt = await page.evaluate(([x, y]) => (window as any)
    .__sceneToScreen?.(x, y) ?? null,
    [hit.x + hit.w / 2, hit.y + hit.h / 2]);
  expect(pt, "scene hook unavailable").toBeTruthy();
  const onScreen = !!vp && pt.x > 0 && pt.y > 0 && pt.x < vp.width && pt.y < vp.height;
  console.log("hit element :", hit.id, `box=(${hit.x},${hit.y},${hit.w}x${hit.h})`);
  console.log("screen point:", JSON.stringify(pt), "viewport:", JSON.stringify(vp),
    "on-screen:", onScreen);
  expect(onScreen, `target maps off-screen at ${JSON.stringify(pt)} — click would miss`)
    .toBe(true);

  // Click to select, then double-click to open the label editor. Report what
  // actually got selected: a gesture that selects nothing and a gesture that
  // selects the wrong thing fail identically from outside, and they need
  // different fixes.
  await page.mouse.click(pt.x, pt.y);
  await page.waitForTimeout(250);
  const selected = await page.evaluate(() => Object.keys(
    (window as any).excalidrawAPI?.getAppState?.()?.selectedElementIds || {})
    .filter((k) => (window as any).excalidrawAPI.getAppState().selectedElementIds[k]));
  console.log("selected after click:", JSON.stringify(selected));

  await page.mouse.dblclick(pt.x, pt.y);
  const editor = page.locator("textarea.excalidraw-wysiwyg");
  await expect(editor,
    `no text editor opened — selection was ${JSON.stringify(selected)}`)
    .toBeVisible({ timeout: 5000 });

  await page.keyboard.press("Control+a");
  await page.keyboard.type(NEW);
  await page.keyboard.press("Escape");
  await expect(editor).toBeHidden({ timeout: 5000 });
  await page.waitForTimeout(600);

  const after = await scene(page);

  // ---- the guards that make the measurement worth reading -----------------
  const renamed = after.filter((e) => e.text === NEW);
  const textChanges = after.filter((a) => {
    const b = before.find((x) => x.id === a.id);
    return b && b.text !== undefined && b.text !== a.text;
  });
  const moved = after.filter((a) => {
    const b = before.find((x) => x.id === a.id);
    return b && (Math.abs(b.x - a.x) > 0.5 || Math.abs(b.y - a.y) > 0.5);
  });

  console.log("=== R6 / R5§6 DISCRIMINATOR ===");
  console.log("scene size            :", before.length);
  console.log("renamed target        :", target!.id, JSON.stringify(before_text), "->", JSON.stringify(NEW));
  console.log("elements whose text changed:", textChanges.length,
    JSON.stringify(textChanges.map((e) => e.id)));
  console.log("elements that MOVED   :", moved.length, JSON.stringify(moved.map((e) => e.id)));

  expect(renamed.length, "the rename did not land — measurement void").toBe(1);
  expect(textChanges.length, "more than one label changed — measurement void").toBe(1);
  expect(moved.length, "geometry moved during a rename — measurement void").toBe(0);

  // ---- did Excalidraw reorder the array under us? -------------------------
  const orderAfter = after.map((e) => e.id);
  const sameSet = orderBefore.length === orderAfter.length &&
    orderBefore.every((id) => orderAfter.includes(id));
  const positionsChanged = sameSet
    ? orderBefore.filter((id, i) => orderAfter[i] !== id).length : -1;
  console.log("array order positions changed in the CLIENT:", positionsChanged,
    "(of", orderBefore.length, ")");

  // ---- the actual measurement --------------------------------------------
  const pending = saveResponse(page);
  await page.locator(".save-btn").click();
  const body = await pending;

  console.log("headline    :", body?.summary?.headline);
  console.log("verb_counts :", JSON.stringify(body?.summary?.verb_counts));
  console.log("suppressed  :", body?.summary?.suppressed);
  console.log("=== END DISCRIMINATOR ===");

  // One rename must narrate as a rename. Everything else is what R6 is here to
  // find out, and is reported rather than asserted.
  expect(body?.summary?.verb_counts, "no verb_counts on the wire").toBeTruthy();
});
