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
 * dirty detection (mirrors the server's significant-attr thinking). */
export function fingerprint(elements: readonly any[]): string {
  const parts = elements
    .filter((e) => !e.isDeleted)
    .map((e) =>
      [
        e.id, e.type, Math.round(e.x), Math.round(e.y),
        Math.round(e.width || 0), Math.round(e.height || 0),
        e.angle?.toFixed?.(2) ?? 0, e.text ?? "", e.containerId ?? "",
        e.frameId ?? "", e.strokeColor, e.backgroundColor,
        JSON.stringify(e.points ?? null),
        e.startBinding?.elementId ?? "", e.endBinding?.elementId ?? "",
        JSON.stringify(e.customData ?? null),
      ].join("|")
    );
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
