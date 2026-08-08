export async function apiGet(path: string) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  const j = await r.json();
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status, payload: j });
  return j;
}

export async function apiPost(path: string, body: any) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status, payload: j });
  return j;
}

/** Fingerprint of the semantically-significant parts of a scene — used for
 * dirty detection (mirrors the server's derived-attr doctrine).
 *
 * Excalidraw RE-DERIVES the endpoint geometry of fully-bound arrows on
 * every load, drag, and undo (focus/gap re-solve — deltas up to ~7px),
 * and re-centers bound labels at fractional x. Hashing that volatile
 * geometry made drag-then-undo read as dirty forever (false pending-
 * banner holds, "saved without changing anything" commits). So:
 *  - both-bound arrows hash their binding topology + INTERIOR points
 *    (the user's shape) — never x/y/w/h or the volatile endpoints; a
 *    user bend adds an interior point and still trips it, a rewire
 *    changes the binding ids and trips it;
 *  - bound labels hash their text, never their re-centered position;
 *  - everything else hashes rounded geometry as before. */
export function fingerprint(elements: readonly any[]): string {
  const rpts = (pts: any[]) =>
    JSON.stringify(pts.map((p) => [Math.round(p[0]), Math.round(p[1])]));
  const parts = elements
    .filter((e) => !e.isDeleted)
    .map((e) => {
      const bothBound =
        (e.type === "arrow" || e.type === "line") &&
        e.startBinding?.elementId && e.endBinding?.elementId;
      const boundLabel = e.type === "text" && e.containerId;
      const geom = bothBound
        ? ["~", "~", "~", "~",
           (e.points?.length ?? 0) + ":" + rpts((e.points ?? []).slice(1, -1))]
        : boundLabel
          ? ["~", "~", "~", "~", "~"]
          : [Math.round(e.x), Math.round(e.y),
             Math.round(e.width || 0), Math.round(e.height || 0),
             e.points ? rpts(e.points) : "null"];
      return [
        e.id, e.type, ...geom,
        e.angle?.toFixed?.(2) ?? 0, e.text ?? "", e.containerId ?? "",
        e.frameId ?? "", e.strokeColor, e.backgroundColor,
        e.startBinding?.elementId ?? "", e.endBinding?.elementId ?? "",
        JSON.stringify(e.customData ?? null),
      ].join("|");
    });
  return parts.join("\n");
}

/** Replay save-record change ops onto a local element list (apply-now on a
 * dirty canvas — the op replay the spec names). */
export function replayChanges(elements: any[], changes: any[]): any[] {
  let els = elements.map((e) => ({ ...e }));
  for (const ch of changes) {
    if (ch.op === "add") {
      const idx = Math.min(ch.index ?? els.length, els.length);
      els.splice(idx, 0, { ...ch.element });
    } else if (ch.op === "del") {
      els = els.filter((e) => e.id !== ch.element.id);
    } else if (ch.op === "mod") {
      for (const e of els)
        if (e.id === ch.id) for (const a of ch.attrs) e[a.attr] = a.to;
    } else if (ch.op === "move") {
      for (const e of els)
        if (e.id === ch.id) {
          e.x = ch.to[0];
          e.y = ch.to[1];
        }
    } else if (ch.op === "reorder") {
      const m = els.find((e) => e.id === ch.id);
      if (m) {
        els = els.filter((e) => e.id !== ch.id);
        els.splice(Math.min(ch.to_index, els.length), 0, m);
      }
    }
  }
  return els;
}
