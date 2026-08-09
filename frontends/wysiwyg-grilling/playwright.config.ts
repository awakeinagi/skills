import { defineConfig } from "@playwright/test";

/** E2E suite for the canvas app (v0.8, WP8).
 *
 * Every UI-only defect in four assessment runs (wrapped KPI, buried
 * rail, floating pin glyphs) was invisible to the backend suite and
 * found only by a human looking — this suite pins the fixes. Visual
 * baselines are LINUX-ONLY, taken with the pinned Playwright chromium
 * at deviceScaleFactor 1; regenerate with --update-snapshots on this
 * platform only. */
export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false,
  workers: 2,
  timeout: 45_000,
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.01 },
  },
  use: {
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});
