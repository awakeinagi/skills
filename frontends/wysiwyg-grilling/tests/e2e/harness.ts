import { test as base } from "@playwright/test";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Per-worker canvas server (WP8): a shared globalSetup server would
 * cross-contaminate the state-mutating flows (saves, pending resolves,
 * tripwire answers), so each worker gets its own fixture copy + server,
 * torn down via `canvas.py stop`. */

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const CANVAS = path.join(
  REPO, "skills", "wysiwyg-grilling", "scripts", "canvas.py");
const FIXTURES = path.join(REPO, "tests", "fixtures");

export type Canvas = {
  url: string;
  project: string;
  /** Run a canvas.py subcommand against this project. */
  cli: (...argv: string[]) => { stdout: string; stderr: string; rc: number };
  /** Read the events log lines. */
  events: () => string[];
};

function startServer(project: string): { url: string; eventsLog: string } {
  const out = spawnSync(
    "python3", [CANVAS, "--project", project, "start", "--no-browser"],
    { encoding: "utf-8", timeout: 60_000 });
  const m = /URL=(\S+)/.exec(out.stdout || "");
  if (!m) throw new Error("server did not start: " + (out.stderr || out.stdout));
  const ev = /EVENTS_LOG=(\S+)/.exec(out.stdout || "");
  return { url: m[1], eventsLog: ev ? ev[1] : "" };
}

function makeCanvas(fixture: string | null, workerIndex: number): Canvas {
  const dir = fs.mkdtempSync(
    path.join(os.tmpdir(), `wysiwyg-e2e-w${workerIndex}-`));
  if (fixture) {
    fs.cpSync(path.join(FIXTURES, fixture),
      path.join(dir, "project_knowledge"), { recursive: true });
  } else {
    fs.mkdirSync(path.join(dir, "project_knowledge"), { recursive: true });
  }
  const { url, eventsLog } = startServer(dir);
  const cli = (...argv: string[]) => {
    const r = spawnSync("python3", [CANVAS, "--project", dir, ...argv],
      { encoding: "utf-8", timeout: 60_000 });
    return { stdout: r.stdout || "", stderr: r.stderr || "",
             rc: r.status ?? -1 };
  };
  return {
    url, project: dir, cli,
    events: () => {
      try {
        return fs.readFileSync(eventsLog, "utf-8").trim().split("\n");
      } catch { return []; }
    },
  };
}

function stop(c: Canvas) {
  spawnSync("python3", [CANVAS, "--project", c.project, "stop"],
    { encoding: "utf-8", timeout: 30_000 });
  fs.rmSync(c.project, { recursive: true, force: true });
}

/** The rich real-session fixture (assessment run 4, arm 3): 7
 * artifacts, 28 concepts, open pins, resolved tripwires. Read-mostly. */
export const testRich = base.extend<object, { canvas: Canvas }>({
  canvas: [async ({}, use, workerInfo) => {
    const c = makeCanvas("argus-r4-arm3", workerInfo.workerIndex);
    await use(c);
    stop(c);
  }, { scope: "worker" }],
});

/** A purpose-built minimal project, seeded through the real write path
 * at setup: one flow, one wireframe with a checkbox + KPI + body text,
 * a document-backed card, and one open pin. Mutating flows live here. */
export const testMini = base.extend<object, { canvas: Canvas }>({
  canvas: [async ({}, use, workerInfo) => {
    const c = makeCanvas(null, workerInfo.workerIndex);
    fs.mkdirSync(path.join(c.project, "project_knowledge", "docs"),
      { recursive: true });
    fs.writeFileSync(
      path.join(c.project, "project_knowledge", "docs", "brief.md"),
      "# Weekly Brief\n\nA short readable document.\n");
    const batch = {
      base_revn: 0,
      create: { id: "screen", type: "wireframe", concept: "screen",
                name: "Screen" },
      ops: [
        { op: "add", element: { id: "frame1", type: "frame",
          x: 40, y: 40, width: 720, height: 480, label: "Main" } },
        { op: "add", element: { id: "cb", type: "rectangle", x: 80,
          y: 100, width: 220, height: 28, label: "Macro calendar",
          kind: "checkbox", checked: true, frameId: "frame1" } },
        { op: "add", element: { id: "kpi", type: "rectangle", x: 80,
          y: 180, width: 292, height: 120, label: "Sentiment index",
          kind: "kpi", value: "62", frameId: "frame1" } },
        { op: "add", element: { id: "body", type: "rectangle", x: 420,
          y: 180, width: 240, height: 80, kind: "body",
          frameId: "frame1" } },
        { op: "add", element: { id: "card", type: "rectangle", x: 80,
          y: 340, width: 220, height: 90, label: "Weekly Brief",
          document: "docs/brief.md", frameId: "frame1" } },
        { op: "pin", target: "kpi",
          question: "is 62 good, and out of what?" },
      ],
    };
    const bpath = path.join(c.project, "batch.json");
    fs.writeFileSync(bpath, JSON.stringify(batch));
    const r = c.cli("apply", "--file", bpath);
    if (!/REVN=1/.test(r.stdout))
      throw new Error("seed failed: " + r.stdout + r.stderr);
    // a second screen so ▶ walk has something to step to
    const batch2 = {
      base_revn: 1, artifact: "screen",
      ops: [{ op: "add", element: { id: "frame2", type: "frame",
        x: 820, y: 40, width: 720, height: 480, label: "Detail" } }],
    };
    fs.writeFileSync(bpath, JSON.stringify(batch2));
    c.cli("apply", "--file", bpath);
    await use(c);
    stop(c);
  }, { scope: "worker" }],
});

export { expect } from "@playwright/test";

/** Click a scene coordinate through the app's own camera hook. */
export async function clickScene(
  page: import("@playwright/test").Page, x: number, y: number,
): Promise<void> {
  const p = await page.evaluate(
    ([sx, sy]) => (window as unknown as {
      __sceneToScreen?: (a: number, b: number) =>
        { x: number; y: number } | null;
    }).__sceneToScreen?.(sx, sy) ?? null,
    [x, y]);
  if (!p) throw new Error("scene hook unavailable");
  await page.mouse.click(p.x, p.y);
}
