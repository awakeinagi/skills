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
      // Free text: the browser re-measures height always, and width
      // while autoResize is on — that churn is measurement, not intent
      // (the server marks it derived; the hash must agree or the
      // artifact reads dirty on open). A deliberate width-drag flips
      // autoResize off and keeps hashing.
      const freeText = e.type === "text" && !e.containerId;
      const geom = bothBound
        ? ["~", "~", "~", "~",
           (e.points?.length ?? 0) + ":" + rpts((e.points ?? []).slice(1, -1))]
        : boundLabel
          ? ["~", "~", "~", "~", "~"]
          : freeText
            ? [Math.round(e.x), Math.round(e.y),
               e.autoResize !== false ? "~" : Math.round(e.width || 0),
               "~", "null"]
            : [Math.round(e.x), Math.round(e.y),
               Math.round(e.width || 0), Math.round(e.height || 0),
               e.points ? rpts(e.points) : "null"];
      // whitespace-collapsed text: bound labels re-wrap with literal
      // newlines on load (the server de-wraps on save — same rule)
      const text = String(e.text ?? "").split(/\s+/).join(" ");
      return [
        e.id, e.type, ...geom,
        e.angle?.toFixed?.(2) ?? 0, text, e.containerId ?? "",
        e.frameId ?? "", e.strokeColor, e.backgroundColor,
        e.startBinding?.elementId ?? "", e.endBinding?.elementId ?? "",
        // link + locked: the v0.3 inspector edits these — leaving them
        // out of the hash made those edits invisible to dirty detection
        e.link ?? "", e.locked ? "L" : "",
        JSON.stringify(e.customData ?? null),
      ].join("|");
    });
  // sorted: element ORDER churn (frame drags re-sort the scene array;
  // undo restores state, not array position) must not read as dirty.
  // Server-side reorder recording is unaffected — POSTs ship the real
  // array order.
  return parts.sort().join("\n");
}

/** Replay save-record change ops onto a local element list (apply-now on a
 * dirty canvas — the op replay the spec names).
 *
 * THE SERVER'S `replay_changes`, op for op. The two are one algorithm
 * and `tests/e2e/replay-parity.spec.ts` runs a crafted record through
 * both, because a replay is how the tab and the file stay one scene:
 * where they differ the user is looking at a drawing the file does not
 * contain, and element order is paint order, so "differ" can mean a
 * differently stacked picture with nothing saying so.
 *
 * Ops apply in DEPENDENCY order, not record order — every `del` first,
 * then every `add` in ascending recorded index, then the rest as
 * recorded. `diff_scenes` indexes an `add` against the FINISHED list but
 * emits it ahead of the `del`s, so replaying in record order inserts it
 * into a list that still holds the doomed elements and it lands one slot
 * short for every deletion below it. That is the r5b-2 storm's own
 * arithmetic; the server was repaired for it in v0.9 and this side was
 * not, so a record as ordinary as "add one element, delete another"
 * rebuilt two different scenes. Read `replay_changes` for why the
 * ascending sort matters more to the inverse list than to this one. */
export function replayChanges(elements: any[], changes: any[]): any[] {
  let els = elements.map((e) => ({ ...e }));
  const idxOf = (ch: any) =>
    typeof ch.index === "number" && Number.isInteger(ch.index)
      ? ch.index : null;
  const ordered = [
    ...changes.filter((ch) => ch.op === "del"),
    ...changes.filter((ch) => ch.op === "add").sort(
      (p, q) => (idxOf(p) === null ? 1 : 0) - (idxOf(q) === null ? 1 : 0)
        || (idxOf(p) ?? 0) - (idxOf(q) ?? 0)),
    ...changes.filter((ch) => ch.op !== "add" && ch.op !== "del"),
  ];
  for (const ch of ordered) {
    if (ch.op === "add") {
      // `canvas.py` `_add_index` + the clamp under it, mirrored: a value
      // that is not a whole number (a hand-edited `"2"`, a `null`, a
      // `true`) means "no index given" and lands at the end, and the
      // result is clamped at BOTH ends. `??` and a top-only `Math.min`
      // were neither: `splice(-1)` reads a negative index as an offset
      // from the end, so a record carrying -1 rebuilt the scene with the
      // element second to last while -5 put it near the front, and
      // element order is paint order. The server refused that clamp-side
      // ambiguity and the client kept it, so as of the server-side fix
      // the two disagreed about the same corrupt record and each was
      // confident — worse than the shared bug they replaced, because a
      // replay is supposed to be how the tab and the file stay one
      // scene. `typeof true === "boolean"` falls to the end here for the
      // same reason `_add_index` refuses `bool`: `min(True, 3)` is
      // position 1, an answer nobody wrote.
      const asked = idxOf(ch) ?? els.length;
      els.splice(Math.max(0, Math.min(asked, els.length)), 0,
                 { ...ch.element });
    } else if (ch.op === "del") {
      els = els.filter((e) => e.id !== ch.element.id);
    } else if (ch.op === "mod") {
      for (const e of els)
        if (e.id === ch.id)
          for (const a of ch.attrs) {
            let v = a.to;
            // records store bindings normalized to id strings; the scene
            // needs the full object (server replay does the same)
            if ((a.attr === "startBinding" || a.attr === "endBinding") &&
                typeof v === "string")
              v = { elementId: v, focus: 0, gap: 6 };
            e[a.attr] = v;
            if (a.attr === "text" && e.type === "text") e.originalText = v;
          }
    } else if (ch.op === "move") {
      for (const e of els)
        if (e.id === ch.id) {
          e.x = ch.to[0];
          e.y = ch.to[1];
        }
    } else if (ch.op === "reorder") {
      if (Array.isArray(ch.order)) {
        // whole-order form (the only form the server emits now)
        // Annotate the tuple: without it TS widens the entries to
        // (string | number)[] and the subtraction below stops type-checking.
        const rank = new Map<string, number>(
          ch.order.map((id: string, i: number): [string, number] => [id, i]));
        els.sort((a, b) =>
          (rank.get(a.id) ?? rank.size) - (rank.get(b.id) ?? rank.size));
      } else {
        const m = els.find((e) => e.id === ch.id);
        if (m) {
          els = els.filter((e) => e.id !== ch.id);
          els.splice(Math.min(ch.to_index, els.length), 0, m);
        }
      }
    }
  }
  return els;
}
