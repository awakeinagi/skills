import { testMini as test, expect, clickScene } from "./harness";

/** ▶ walk stepping and the reader modal (v0.2 gap #4: Esc must close;
 * ✕ must work without force-click). */

test("walk steps screens and Esc exits", async ({ page, canvas }) => {
  await page.goto(canvas.url);
  const walk = page.locator("button", { hasText: "▶ walk" });
  await expect(walk).toBeEnabled();
  await walk.click();
  await expect(page.locator(".walk-bar"))
    .toContainText(/1\s*\/\s*2/);
  await page.keyboard.press("ArrowRight");
  await expect(page.locator(".walk-bar"))
    .toContainText(/2\s*\/\s*2/);
  await page.keyboard.press("Escape");
  await expect(page.locator("text=/1\\s*\\/\\s*2|2\\s*\\/\\s*2/"))
    .toHaveCount(0);
});

test("document reader opens on selection and Esc closes it",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    // select the document-backed card via the rail lint-free path: click
    // its canvas position after fit — the card is at a known spot
    await page.locator("button", { hasText: "⤢ fit" }).click();
    // find it through the reader-agnostic route: the doc opens when the
    // element is selected; drive selection by clicking the canvas where
    // the card renders. Fit view puts the full scene in frame.
    await clickScene(page, 190, 385);
    const reader = page.locator(".doc-reader");
    await expect(reader.first()).toBeVisible({ timeout: 5000 });
    await expect(reader.first()).toContainText("Weekly Brief");
    await page.keyboard.press("Escape");
    await expect(reader.first()).not.toBeVisible();
  });
