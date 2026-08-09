import { testRich as test, expect } from "./harness";

/** B7/r3-3: every artifact is one click away from the strip — a
 * 5-artifact project used to show one thumbnail. */

test("filmstrip reaches artifacts of other concepts", async ({ page, canvas }) => {
  await page.goto(canvas.url);
  const thumbs = page.locator(".filmstrip .thumb:not(.suggest)");
  // arm 3 has 7 artifacts across 5+ concepts; the strip must show all
  await expect(thumbs).toHaveCount(7);
  // clicking a dimmed (other-concept) thumb navigates
  const last = thumbs.last();
  const name = await last.locator(".tname").innerText();
  await last.click();
  await expect(page.locator(".artifact-dropdown button span").nth(1))
    .toContainText(name.split("\n")[0].trim().slice(0, 8));
});
