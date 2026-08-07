// Maintainer-only postbuild: copy Excalidraw fonts into the committed bundle
// (vite build does NOT include them) and stamp the bundle with a source hash
// so a stale committed bundle is detectable (ADR 0001).
import { createHash } from "node:crypto";
import { cpSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..");
const out = join(frontend, "..", "..", "skills", "wysiwyg-grilling", "scripts", "web");

// 1. fonts
cpSync(
  join(frontend, "node_modules", "@excalidraw", "excalidraw", "dist", "prod", "fonts"),
  join(out, "fonts"),
  { recursive: true }
);

// 2. build stamp — sha1 over the frontend source tree (same recipe as
// canvas.py's frontend_stamp_warning)
function hashDir(dir, h) {
  for (const name of readdirSync(dir).sort()) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) hashDir(p, h);
    else {
      h.update(name);
      h.update(readFileSync(p));
    }
  }
}
const h = createHash("sha1");
hashDir(join(frontend, "src"), h);
writeFileSync(
  join(out, "build-stamp.json"),
  JSON.stringify(
    { source_hash: h.digest("hex"), built_at: new Date().toISOString() },
    null,
    2
  ) + "\n"
);
console.log("postbuild: fonts copied, build-stamp.json written");
