import { testRich as test, expect } from "./harness";

/* DISCRIMINATOR for R6's user-path finding.
 *
 * Measured via `x-as-user move`: an identical 40x24px move of the same
 * unpinned node, from an identical 0-error baseline, leaves
 *
 *     user path (x-as-user move)   0 -> 2 LAYOUT_ERRORs
 *     agent path (apply mod x/y)   0 -> 0
 *
 * with a byte-identical headline ("Market Feed moved left-up") on both.
 *
 * Two explanations predict that identically, and they need opposite fixes:
 *
 *   (A) THE PRODUCT: a user save genuinely does not re-route bound arrows,
 *       so a real person dragging a node in the tab strands its arrows.
 *   (B) THE INSTRUMENT: `x-as-user move` mutates the element list
 *       server-side and never runs Excalidraw's native binding update, so it
 *       manufactures a state a browser drag would not produce. `cmd_x_as_user`
 *       warns about precisely this drift in its own docstring.
 *
 * This drags a bound node in a REAL tab and reads whether its arrows follow.
 * If they follow, (B) is the answer and the finding is about the assessment
 * adapter -- which would also mean every behavioural finding any run derived
 * from `x-as-user move` on a bound element is suspect. */

type El = {
  id: string; type: string; x: number; y: number; w: number; h: number;
  points?: number[][]; startId?: string | null; endId?: string | null;
};

const scene = (page: import("@playwright/test").Page): Promise<El[]> =>
  page.evaluate(() =>
    (window as unknown as { excalidrawAPI: {
      getSceneElements: () => Array<Record<string, any>> } })
      .excalidrawAPI.getSceneElements()
      .filter((e) => !e.isDeleted)
      .map((e) => ({
        id: e.id, type: e.type, x: e.x, y: e.y,
        w: e.width || 0, h: e.height || 0,
        points: e.points, startId: e.startBinding?.elementId ?? null,
        endId: e.endBinding?.elementId ?? null,
      })));

test("does a real browser drag carry a node's bound arrows with it?", async ({ page, canvas }) => {
  await page.goto(canvas.url);
  await page.waitForFunction(
    () => (window as any).excalidrawAPI?.getSceneElements?.().length > 0,
    null, { timeout: 30_000 });

  // The app opens on whichever artifact is current, which in this fixture is a
  // wireframe with no arrows at all. Walk the thumbnails until we land on one
  // that actually has bound arrows -- otherwise the test "passes" by measuring
  // a scene where the question cannot arise.
  const pickBound = (els: El[]) => {
    const counts = new Map<string, number>();
    for (const e of els)
      for (const id of [e.startId, e.endId])
        if (id) counts.set(id, (counts.get(id) || 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
      .find(([id]) => {
        const n = els.find((e) => e.id === id);
        return n && n.type !== "arrow" && n.w > 40 && n.h > 20;
      })?.[0];
  };

  let before = await scene(page);
  let nodeId = pickBound(before);
  if (!nodeId) {
    const thumbs = page.locator("div.thumb");
    const n = await thumbs.count();
    console.log("current artifact has no bound arrows; walking", n, "thumbnails");
    for (let i = 0; i < n; i++) {
      await thumbs.nth(i).click();
      await page.waitForTimeout(700);
      before = await scene(page);
      nodeId = pickBound(before);
      if (nodeId) { console.log("switched to thumbnail", i); break; }
    }
  }
  expect(nodeId, "no multiply-bound node on any artifact in the fixture").toBeTruthy();

  const node = before.find((e) => e.id === nodeId)!;
  const bound = before.filter((e) => e.startId === nodeId || e.endId === nodeId);
  console.log("=== R6 USER-DRAG BINDING DISCRIMINATOR ===");
  console.log("node        :", nodeId, `(${node.x},${node.y}) ${node.w}x${node.h}`);
  console.log("arrows bound:", bound.length, JSON.stringify(bound.map((a) => a.id)));

  const geom = (els: El[]) => JSON.stringify(
    bound.map((a) => {
      const cur = els.find((e) => e.id === a.id)!;
      return [cur.id, cur.x, cur.y, JSON.stringify(cur.points)];
    }));
  const geomBefore = geom(before);

  const toScreen = async (x: number, y: number) => {
    const p = await page.evaluate(([a, b]) => (window as any)
      .__sceneToScreen?.(a, b) ?? null, [x, y]);
    expect(p, "scene hook unavailable").toBeTruthy();
    return p as { x: number; y: number };
  };
  const from = await toScreen(node.x + node.w / 2, node.y + node.h / 2);

  // select, then drag. Assert the selection landed before dragging: a drag
  // that starts on empty canvas draws a selection box and moves nothing, which
  // would look exactly like "the arrows did not follow".
  await page.mouse.click(from.x, from.y);
  await page.waitForTimeout(200);
  const sel = await page.evaluate(() => Object.entries(
    (window as any).excalidrawAPI.getAppState().selectedElementIds)
    .filter(([, v]) => v).map(([k]) => k));
  console.log("selected    :", JSON.stringify(sel));
  expect(sel, `drag would start on empty canvas; selection was ${JSON.stringify(sel)}`)
    .toContain(nodeId);

  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x - 40, from.y - 24, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(500);

  const after = await scene(page);
  const movedNode = after.find((e) => e.id === nodeId)!;
  const nodeMoved = Math.abs(movedNode.x - node.x) > 4 || Math.abs(movedNode.y - node.y) > 4;
  console.log("node after  :", `(${movedNode.x},${movedNode.y})  moved=${nodeMoved}`);
  expect(nodeMoved, "the drag did not move the node — measurement void").toBe(true);

  const arrowsFollowed = geom(after) !== geomBefore;
  console.log("ARROWS FOLLOWED THE NODE IN THE CLIENT:", arrowsFollowed);

  // now save through the real client and ask the product's own linter
  await page.locator(".save-btn").click();
  await page.waitForTimeout(1500);
  const lint = canvas.cli("lint").stdout;
  const errs = lint.split("\n").filter((l) => l.startsWith("LAYOUT_ERROR=")).length;
  const dangling = lint.split("\n")
    .filter((l) => /claims to bind/.test(l)).length;
  console.log("LAYOUT_ERRORs after a real browser drag + save:", errs);
  console.log("  of which 'claims to bind ... away':", dangling);
  console.log("=== END ===");

  // Reported, not asserted: which way this lands is the finding.
  expect(typeof errs).toBe("number");
});
