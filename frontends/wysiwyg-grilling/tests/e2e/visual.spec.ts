import { testMini as test, testTextClip, expect } from "./harness";

/** Visual regressions only pixels can judge (r4-12's class): the KPI
 * tile must render its value on ONE line in the real editor, and the
 * body block must show wavy stand-in lines. Linux-only baselines. */

test("kpi and body-text render as designed in the live editor",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "⤢ fit" }).click();
    // let fonts + rough shapes settle
    await page.waitForTimeout(1500);
    const canvasEl = page.locator("canvas.interactive, canvas.excalidraw__canvas").last();
    await expect(canvasEl).toHaveScreenshot("mini-screen.png", {
      maxDiffPixelRatio: 0.02,
    });
  });

testTextClip("bound text waits for its webfont before the live restore",
  async ({ page, canvas }) => {
    await page.route(/\.(woff2?|ttf)(\?.*)?$/, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 750));
      await route.continue();
    });
    await page.goto(canvas.url);
    await expect.poll(async () => page.evaluate(() =>
      Array.from(document.fonts as unknown as Iterable<FontFace>).some(
        (face) => face.family.replace(/["']/g, "") === "Nunito" &&
          face.status === "loaded" && face.unicodeRange.includes("U+0-FF"))))
      .toBe(true);
    await expect.poll(async () => page.evaluate(() => {
      const api = (window as unknown as { excalidrawAPI?: {
        getSceneElements: () => Array<Record<string, unknown>>;
      } }).excalidrawAPI;
      const label = api?.getSceneElements().find(
        (el) => el.containerId === "reported-row");
      return typeof label?.width === "number" ? label.width : null;
    })).not.toBeNull();
    const widths = await page.evaluate(() => {
      const api = (window as unknown as { excalidrawAPI: {
        getSceneElements: () => Array<Record<string, unknown>>;
        updateScene: (scene: Record<string, unknown>) => void;
      } }).excalidrawAPI;
      api.updateScene({ appState: { zoom: { value: 1.3 } } });
      const live = api.getSceneElements().find(
        (el) => el.containerId === "reported-row");
      const probe = document.createElement("canvas").getContext("2d");
      if (!probe) throw new Error("2D canvas context unavailable");
      probe.font = "16px Nunito";
      return {
        expected: Math.ceil(
          probe.measureText("Email landlord about the repair").width),
        live: live?.width as number,
      };
    });
    expect(widths.live).toBe(widths.expected);
  });
