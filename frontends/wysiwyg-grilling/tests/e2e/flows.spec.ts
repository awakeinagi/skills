import * as fs from "node:fs";
import * as path from "node:path";
import { testMini as test, expect, clickScene } from "./harness";

/** State-mutating flows: pin answer round-trip (the event-loop
 * substrate), the pending banner (never exercised in any assessment
 * run), and the control flip (the benchmark's uncheck gesture). */

test("answering a pin in the rail resolves it and logs the event",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    const before = canvas.events()
      .filter((l) => l.includes('"pin_answer"')).length;
    const card = page.locator(".pin-card").first();
    await expect(card).toBeVisible();
    await card.locator("input").fill("out of 100, higher is calmer");
    await card.locator("button", { hasText: "Answer" }).click();
    await expect
      .poll(() => canvas.events()
        .filter((l) => l.includes('"pin_answer"')).length,
        { timeout: 8000 })
      .toBeGreaterThan(before);
  });

test("pulled cadence holds an agent revision; Apply now lands it",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    canvas.cli("x-as-user", "config", "--text", "canvas_updates=pulled");
    const st = canvas.cli("status").stdout;
    const revn = /(?:^|\n)REVN=(\d+)/.exec(st);
    const bpath = path.join(canvas.project, "b2.json");
    fs.writeFileSync(bpath, JSON.stringify({
      base_revn: revn ? +revn[1] : 2, artifact: "screen",
      ops: [{ op: "add", element: { id: "late", type: "rectangle",
        x: 460, y: 340, width: 160, height: 60, label: "Held box",
        frameId: "frame1" } }],
    }));
    canvas.cli("apply", "--file", bpath);
    const banner = page.locator(".banner.pending");
    await expect(banner).toBeVisible({ timeout: 10_000 });
    await expect(banner).toContainText("Agent revision waiting");
    await banner.locator("button", { hasText: "Apply now" }).click();
    await expect(banner).not.toBeVisible({ timeout: 10_000 });
    await expect.poll(() =>
      canvas.cli("status").stdout.includes("PENDING=0"),
      { timeout: 10_000 }).toBeTruthy();
  });

test("a drawn checkbox is operable end to end",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "fit" }).click();
    await clickScene(page, 190, 114);
    const flip = page.locator(".ctl-flip");
    await expect(flip).toBeVisible({ timeout: 5000 });
    await expect(flip).toContainText("uncheck");
    await flip.click();
    await page.locator(".save-btn").click();
    await expect.poll(() => {
      const doc = JSON.parse(fs.readFileSync(
        path.join(canvas.project, "project_knowledge",
          "artifacts", "screen.excalidraw"), "utf-8"));
      const cb = doc.elements.find((e: { id: string }) => e.id === "cb");
      return cb?.customData?.checked;
    }, { timeout: 10_000 }).toBe(false);
  });
