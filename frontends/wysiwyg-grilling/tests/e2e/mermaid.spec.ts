import * as fs from "node:fs";
import * as path from "node:path";
import { testMini as test, expect } from "./harness";

/** WP9: the mermaid seeding handshake, exercised for real. The unit
 * suite maps CAPTURED skeletons; only a browser test proves the live
 * tier-1 loop — CLI posts the definition, the open tab converts it
 * with the vendored library and posts skeletons back, the CLI maps
 * them to ops and applies. And only a browser test can drive the
 * user-facing import dialog at all. */

test("the CLI seed converts through the connected tab and lands",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    // the tab must be polling state before the CLI starts waiting on it
    await expect(page.locator(".save-btn")).toBeVisible({ timeout: 10_000 });
    const mmd = path.join(canvas.project, "seed.mmd");
    fs.writeFileSync(mmd, [
      "flowchart TD",
      "  a[Collect input] --> b{Valid?}",
      "  b -->|yes| c[Process]",
      "  b -->|no| a",
      "  c --> c",
    ].join("\n"));
    const r = canvas.cli("mermaid", "--file", mmd,
      "--artifact", "seeded-flow", "--concept", "pipeline",
      "--no-headless");   // tier 1 only: a fallback pass here would
                          // mask a broken tab handshake
    expect(r.stdout).toContain("SEED_KIND=flowchart");
    expect(r.stdout).toMatch(/REVN=\d+/);
    const doc = JSON.parse(fs.readFileSync(
      path.join(canvas.project, "project_knowledge",
        "artifacts", "seeded-flow.excalidraw"), "utf-8"));
    const ids = doc.elements.map((e: { id: string }) => e.id);
    expect(ids).toContain("collect-input");   // semantic slugs
    expect(ids).toContain("valid");
    const loop = doc.elements.find(
      (e: { id: string }) => e.id === "process-process");
    expect(loop.startBinding.elementId).toBe("process");
    expect(loop.endBinding.elementId).toBe("process");
  });

test("the import dialog pastes a diagram as the user's own drawing",
  async ({ page, canvas }) => {
    await page.goto(canvas.url);
    await page.locator("button", { hasText: "mermaid" }).click();
    const input = page.locator(".mermaid-input");
    await expect(input).toBeVisible();
    await input.fill("flowchart LR\n  x[Draft] --> y[Publish]");
    await page.locator(".mermaid-modal button", { hasText: "Import" })
      .click();
    // arrives as user work: dirty canvas, user Save, ordinary record
    await expect(page.locator(".toasts"))
      .toContainText("your drawing now", { timeout: 15_000 });
    const before = canvas.events()
      .filter((l) => l.includes('"save"')).length;
    await page.locator(".save-btn").click();
    await expect
      .poll(() => canvas.events()
        .filter((l) => l.includes('"save"')).length,
        { timeout: 10_000 })
      .toBeGreaterThan(before);
  });
