import { testRich as test, expect } from "./harness";

/** B3/r4-13 (rail burial) + B1/D25 (pin age) — DOM assertions against
 * the rich arm-3 fixture: 7 artifacts, 28 concepts, 24 terms. */

test("rail splits concepts from vocabulary instead of burying views",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    const registry = page.locator(".rail-section", {
      has: page.locator("h3", { hasText: "Registry" }) });
    await expect(registry.locator("h3 .count")).toContainText("terms");
    // the old failure: twenty-one "0 views" rows in the live list
    await expect(registry.locator(".cname", { hasText: "0 views" }))
      .toHaveCount(0);
    const vocab = registry.locator("button.show-archived",
      { hasText: "vocabulary" });
    await expect(vocab).toBeVisible();
    await vocab.click();
    await expect(registry.locator(".cname", { hasText: "📖" }).first())
      .toBeVisible();
  });

test("open pin cards carry their age, not just their origin",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    const pinCard = page.locator(".pin-card").first();
    await expect(pinCard).toBeVisible();
    // arm 3 closed with pins aged 5+ rounds — the age line must render
    await expect(
      page.locator(".pin-card .anchor", { hasText: /open \d+ rounds?/ })
        .first()).toBeVisible();
  });

test("pin detail modal states the age and the asker",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator(".pin-card .linkish", { hasText: "details" })
      .first().click();
    const modal = page.locator(".modal");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText(/Open for \d+ round/);
    await page.keyboard.press("Escape");
    await expect(modal).not.toBeVisible();
  });
