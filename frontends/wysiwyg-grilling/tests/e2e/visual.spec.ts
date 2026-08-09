import { testMini as test, expect } from "./harness";

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
