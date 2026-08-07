import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Excalidraw, exportToBlob, restoreElements, CaptureUpdateAction,
} from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { apiGet, apiPost, fingerprint, replayChanges } from "./api";
import { Rail } from "./components/Rail";
import { HistoryGraph } from "./components/HistoryGraph";
import { SceneThumb } from "./components/Thumb";

const POLL_MS = 2500;

let toastSeq = 0;

export default function App() {
  const [state, setState] = useState<any>(null);
  const [currentArtifact, setCurrentArtifact] = useState<string | null>(null);
  const [dirtyMap, setDirtyMap] = useState<Record<string, boolean>>({});
  const [viewingRevn, setViewingRevn] = useState<number | null>(null);
  const [viewScenes, setViewScenes] = useState<Record<string, any[]>>({});
  const [toasts, setToasts] = useState<{ id: number; text: string }[]>([]);
  const [staleInfo, setStaleInfo] = useState<any>(null);
  const [forkPrompt, setForkPrompt] = useState<{ open: boolean; name: string } | null>(null);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [suggestText, setSuggestText] = useState("");
  const [dropOpen, setDropOpen] = useState(false);
  const [dismissedPending, setDismissedPending] = useState<number[]>([]);
  const [seenPins, setSeenPins] = useState<string[]>([]);
  const [lastSave, setLastSave] = useState<any>(null);
  const [camera, setCamera] = useState({ scrollX: 0, scrollY: 0, zoom: 1 });

  const apiRef = useRef<any>(null);
  const stateRef = useRef<any>(null);
  const viewScenesRef = useRef<Record<string, any[]>>({});
  const firstStateRef = useRef(true);
  const loadedRevnRef = useRef<number>(0);
  const buffersRef = useRef<Record<string, any[]>>({});
  const baselineFpRef = useRef<Record<string, string>>({});
  const loadedHashRef = useRef<Record<string, string>>({});
  const appStateRef = useRef<any>({});
  const currentRef = useRef<string | null>(null);
  const dirtyRef = useRef<Record<string, boolean>>({});
  const viewingRef = useRef<number | null>(null);
  const changeTimer = useRef<any>(null);
  const servicingShot = useRef<Set<number>>(new Set());

  currentRef.current = currentArtifact;
  dirtyRef.current = dirtyMap;
  viewingRef.current = viewingRevn;

  const toast = useCallback((text: string) => {
    const id = ++toastSeq;
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const anyDirty = Object.values(dirtyMap).some(Boolean);

  useEffect(() => {
    (window as any).__wysiwyg = {
      dirtyMap, currentArtifact,
      baseline: baselineFpRef.current,
      buffers: buffersRef.current,
      state,
    };
  });

  /* ---------------- scene loading ---------------- */
  const loadScene = useCallback((els: any[], viewMode: boolean) => {
    if (!apiRef.current) return [];
    const restored = restoreElements(els || [], null, {
      refreshDimensions: true, repairBindings: true,
    } as any);
    apiRef.current.updateScene({
      elements: restored,
      captureUpdate: CaptureUpdateAction.NEVER,
    });
    if (!viewMode) apiRef.current.scrollToContent(restored, { fitToViewport: true, viewportZoomFactor: 0.85 });
    return restored;
  }, []);

  const showArtifact = useCallback(
    (aid: string, st?: any, forceReload = false) => {
      const s = st || stateRef.current;
      if (!s) return;
      // stash the outgoing buffer
      const prev = currentRef.current;
      if (prev && dirtyRef.current[prev] && apiRef.current)
        buffersRef.current[prev] = apiRef.current.getSceneElements();
      setCurrentArtifact(aid);
      currentRef.current = aid;
      if (viewingRef.current != null) {
        loadScene(viewScenesRef.current[aid] || [], true);
        return;
      }
      if (!Object.values(dirtyRef.current).some(Boolean))
        loadedRevnRef.current = s.head_revn ?? loadedRevnRef.current;
      const committed = s.artifacts?.[aid]?.elements || [];
      if (dirtyRef.current[aid] && buffersRef.current[aid] && !forceReload) {
        loadScene(buffersRef.current[aid], false);
      } else {
        loadedHashRef.current[aid] = JSON.stringify(committed);
        const restored = loadScene(committed, false);
        baselineFpRef.current[aid] = fingerprint(restored);
        pendingBaselineRef.current = aid;
        setDirtyMap((d) => ({ ...d, [aid]: false }));
      }
    },
    [loadScene]
  );

  /* ---------------- pin-only gap merge ---------------- */
  const mergePinOnlyGap = useCallback(async (s: any) => {
    const from = loadedRevnRef.current;
    const gap: any[] = [];
    for (const sv of s.saves || [])
      if (sv.revn > from && sv.revn <= s.head_revn) gap.push(sv);
    gap.sort((a, b) => a.revn - b.revn);
    const records: any[] = [];
    for (const sv of gap) {
      try {
        records.push((await apiGet(`/api/save-record/${sv.revn}`)).record);
      } catch {
        return; // can't read the gap — let the base_revn check handle it
      }
    }
    const pinOnly = records.every((r) =>
      Object.values(r.artifacts || {}).every((part: any) =>
        (part.changes || []).every(
          (c: any) =>
            (c.op === "add" || c.op === "del")
              ? (c.element?.customData?.role === "pin")
              : c.op === "mod"
                ? (c.attrs || []).every((a: any) => a.derived)
                : false
        )
      )
    );
    if (!pinOnly) return; // genuine stale tab — leave for the 409 path
    for (const r of records) {
      for (const [aid, part] of Object.entries<any>(r.artifacts || {})) {
        if (buffersRef.current[aid])
          buffersRef.current[aid] = replayChanges(buffersRef.current[aid], part.changes || []);
        if (aid === currentRef.current && apiRef.current) {
          const replayed = replayChanges(
            apiRef.current.getSceneElements().filter((e: any) => !e.isDeleted),
            part.changes || []
          );
          loadScene(replayed, false);
          buffersRef.current[aid] = replayed;
        }
      }
    }
    loadedRevnRef.current = s.head_revn;
  }, [loadScene]);

  /* ---------------- polling ---------------- */
  const refresh = useCallback(async () => {
    try {
      const s = await apiGet("/api/state");
      if (firstStateRef.current) {
        // pins that were already open before this tab loaded don't deserve a
        // "new question" banner — the rail shows them
        firstStateRef.current = false;
        setSeenPins((s.pins || []).filter((p: any) => p.status === "open").map((p: any) => p.id));
      }
      stateRef.current = s;
      setState(s);
      const aids = Object.keys(s.artifacts || {});
      let cur = currentRef.current;
      if (!cur || (!s.artifacts?.[cur] && aids.length)) {
        cur = aids[0] || null;
        if (cur) showArtifact(cur, s);
        else setCurrentArtifact(null);
      } else if (cur && s.artifacts?.[cur] && viewingRef.current == null &&
                 s.checkout_revn == null) {
        const anyDirtyNow = Object.values(dirtyRef.current).some(Boolean);
        if (!anyDirtyNow) {
          loadedRevnRef.current = s.head_revn;
          // reload if the committed scene changed under a clean canvas
          const committed = JSON.stringify(s.artifacts[cur].elements);
          if (!dirtyRef.current[cur] && committed !== loadedHashRef.current[cur]) {
            loadedHashRef.current[cur] = committed;
            const restored = loadScene(s.artifacts[cur].elements, false);
            baselineFpRef.current[cur] = fingerprint(restored);
            pendingBaselineRef.current = cur;
          }
        } else if (s.head_revn > loadedRevnRef.current) {
          // head advanced under unsaved edits. Pin-only revisions commit
          // without holding — merge those into the dirty buffers so the
          // eventual Save diffs cleanly. Anything else is a genuine stale
          // tab: leave it; the Save's base_revn check catches it gently.
          await mergePinOnlyGap(s);
        }
      }
      return s;
    } catch {
      return null;
    }
  }, [showArtifact, loadScene, mergePinOnlyGap]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
    // mount-only: refresh is ref-based and stable in practice
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // heartbeat: the server needs to know whether the canvas is dirty to decide
  // auto-apply vs pending-banner
  useEffect(() => {
    const send = () => apiPost("/api/heartbeat", { dirty: Object.values(dirtyRef.current).some(Boolean) }).catch(() => {});
    send();
    const t = setInterval(send, 3000);
    return () => clearInterval(t);
  }, [anyDirty]);

  /* ---------------- Excalidraw change tracking ---------------- */
  const verSumRef = useRef<number>(-1);
  const pendingBaselineRef = useRef<string | null>(null);

  const onChange = useCallback((els: readonly any[], appState: any) => {
    appStateRef.current = appState;
    setCamera((c) =>
      c.scrollX !== appState.scrollX || c.scrollY !== appState.scrollY || c.zoom !== appState.zoom.value
        ? { scrollX: appState.scrollX, scrollY: appState.scrollY, zoom: appState.zoom.value }
        : c
    );
    if (viewingRef.current != null) return;
    const aid = currentRef.current;
    if (!aid) return;
    // Excalidraw calls onChange continuously; element versions only move on
    // real mutations, so gate the (heavier) fingerprint work on them.
    let v = els.length * 7919;
    for (const e of els) v += (e.version || 0) + (e.isDeleted ? 13 : 0);
    if (v === verSumRef.current && pendingBaselineRef.current !== aid) return;
    verSumRef.current = v;
    const live = els.filter((e) => !e.isDeleted).map((e) => e);
    const fp = fingerprint(live);
    if (pendingBaselineRef.current === aid) {
      // first onChange after a scene load: whatever the canvas settled on IS
      // the clean state
      pendingBaselineRef.current = null;
      baselineFpRef.current[aid] = fp;
      setDirtyMap((d) => (d[aid] === false ? d : { ...d, [aid]: false }));
      return;
    }
    buffersRef.current[aid] = live as any[];
    const isDirty = fp !== baselineFpRef.current[aid];
    setDirtyMap((d) => (d[aid] === isDirty ? d : { ...d, [aid]: isDirty }));
  }, []);

  /* ---------------- Save ---------------- */
  const doSave = useCallback(
    async (forkName?: string) => {
      if (!state) return;
      const scenes: Record<string, any[]> = {};
      for (const [aid, dirty] of Object.entries(dirtyRef.current))
        if (dirty && buffersRef.current[aid]) scenes[aid] = buffersRef.current[aid];
      const cur = currentRef.current;
      if (cur && apiRef.current && dirtyRef.current[cur])
        scenes[cur] = apiRef.current.getSceneElements().filter((e: any) => !e.isDeleted);
      if (!Object.keys(scenes).length) return;
      if (state.checkout_revn != null && !forkName) {
        setForkPrompt({ open: true, name: `alt-${String((state.revn || 0) + 1).padStart(4, "0")}` });
        return;
      }
      const selection = Object.keys(appStateRef.current?.selectedElementIds || {})
        .filter((k) => appStateRef.current.selectedElementIds[k])
        .map((k) => `${cur}#${k}`);
      try {
        const base = state.checkout_revn != null ? state.checkout_revn
          : (loadedRevnRef.current || state.head_revn);
        const r = await apiPost("/api/save", {
          base_revn: base, scenes, selection, fork_name: forkName || undefined,
        });
        setDirtyMap({});
        buffersRef.current = {};
        setLastSave(r);
        const s = await refresh();
        if (s && cur) showArtifact(cur, s, true);
        toast(`Saved ✓ #${r.revn} (${r.short_id})${r.branch !== "main" ? ` on ⎇ ${r.branch}` : ""}${r.tripwires?.length ? ` — ⚠ ${r.tripwires.length} tripwire${r.tripwires.length > 1 ? "s" : ""}` : ""}`);
      } catch (e: any) {
        if (e.status === 409) setStaleInfo(e.payload);
        else toast(`Save failed: ${e.message}`);
      }
    },
    [state, refresh, showArtifact, toast]
  );

  /* ---------------- view mode (time travel) ---------------- */
  const viewCommit = useCallback(
    async (revn: number) => {
      if (Object.values(dirtyRef.current).some(Boolean)) {
        toast("Unsaved edits block time-travel — Save (or undo) first. Nothing is ever lost.");
        return;
      }
      const aids = Object.keys(state?.artifacts || {});
      const scenes: Record<string, any[]> = {};
      await Promise.all(
        aids.map(async (aid) => {
          try {
            const r = await apiGet(`/api/artifact/${aid}?at=${revn}`);
            scenes[aid] = r.elements || [];
          } catch {
            scenes[aid] = [];
          }
        })
      );
      setViewScenes(scenes);
      viewScenesRef.current = scenes;
      setViewingRevn(revn);
      viewingRef.current = revn;
      const cur = currentRef.current || aids[0];
      if (cur) loadScene(scenes[cur] || [], true);
    },
    [state, loadScene, toast]
  );

  const backToLive = useCallback(async () => {
    setViewingRevn(null);
    viewingRef.current = null;
    setViewScenes({});
    viewScenesRef.current = {};
    const s = await refresh();
    const cur = currentRef.current;
    if (cur && s) showArtifact(cur, s, true);
  }, [refresh, showArtifact]);

  const workFromHere = useCallback(async () => {
    const revn = viewingRef.current;
    if (revn == null) return;
    try {
      await apiPost("/api/checkout", { revn });
      const scenes = viewScenes;
      setViewingRevn(null);
      viewingRef.current = null;
      const s = await refresh();
      const cur = currentRef.current;
      if (cur && s) {
        // load the checked-out state as the editable baseline
        const els = scenes[cur] || [];
        loadedHashRef.current[cur] = JSON.stringify(els);
        const restored = loadScene(els, false);
        baselineFpRef.current[cur] = fingerprint(restored);
        pendingBaselineRef.current = cur;
        setDirtyMap((d) => ({ ...d, [cur]: false }));
      }
      toast(`Working from save #${revn} — your first Save here will fork a branch.`);
    } catch (e: any) {
      toast(e.message);
    }
  }, [viewScenes, refresh, loadScene, toast]);

  /* ---------------- pending revisions ---------------- */
  const resolvePending = useCallback(
    async (id: number, action: "apply_now" | "after_save") => {
      try {
        const r = await apiPost("/api/pending/resolve", { id, action });
        if (action === "apply_now") {
          const cur = currentRef.current;
          if (cur && dirtyRef.current[cur] && r.changes?.[cur] && apiRef.current) {
            // op replay onto the unsaved canvas — user work is inviolable
            const replayed = replayChanges(
              apiRef.current.getSceneElements().filter((e: any) => !e.isDeleted),
              r.changes[cur]
            );
            buffersRef.current[cur] = replayed;
            loadScene(replayed, false);
          }
          toast(`Applied agent revision #${r.revn}`);
        } else toast("Revision will land after your next Save.");
        refresh();
      } catch (e: any) {
        toast(e.message);
      }
    },
    [refresh, loadScene, toast]
  );

  /* ---------------- screenshot servicing ---------------- */
  useEffect(() => {
    const reqs = state?.screenshot_requests || [];
    for (const req of reqs) {
      if (servicingShot.current.has(req.id)) continue;
      servicingShot.current.add(req.id);
      const aid = req.artifact || currentRef.current;
      const els =
        (viewingRef.current != null ? viewScenes[aid!] : null) ||
        buffersRef.current[aid!] ||
        state?.artifacts?.[aid!]?.elements ||
        (apiRef.current ? apiRef.current.getSceneElements() : []);
      (async () => {
        try {
          const blob = await exportToBlob({
            elements: restoreElements(els, null) as any,
            appState: { viewBackgroundColor: "#faf8f2", exportWithDarkMode: false },
            files: null,
            mimeType: "image/png",
          } as any);
          const dataUrl: string = await new Promise((res) => {
            const fr = new FileReader();
            fr.onload = () => res(fr.result as string);
            fr.readAsDataURL(blob);
          });
          await apiPost("/api/screenshot/complete", { id: req.id, data_url: dataUrl });
        } catch (e: any) {
          console.error("screenshot failed", e);
        }
      })();
    }
  }, [state, viewScenes]);

  /* ---------------- derived ---------------- */
  const artifacts = state?.artifacts || {};
  const artifactIds = Object.keys(artifacts);
  const concepts = state?.concepts || [];
  const cadence = state?.config?.canvas_updates || "per-round";
  const pending = (state?.pending || []).filter((p: any) => !dismissedPending.includes(p.id));
  const openTripwires = (state?.tripwires || []).filter((t: any) => t.status === "open");
  const freshPins = (state?.pins || []).filter(
    (p: any) => p.status === "open" && !seenPins.includes(p.id)
  );

  const currentConceptViews = useMemo(() => {
    const c = concepts.find((c: any) => (c.views || []).includes(currentArtifact));
    const views = c ? c.views.filter((v: string) => artifacts[v]) : artifactIds;
    return views;
  }, [concepts, currentArtifact, artifactIds.join(",")]);

  const groupedArtifacts = useMemo(() => {
    const groups: { name: string; ids: string[] }[] = [];
    const claimed = new Set<string>();
    for (const c of concepts) {
      const ids = (c.views || []).filter((v: string) => artifacts[v]);
      if (ids.length) {
        groups.push({ name: c.name, ids });
        ids.forEach((i: string) => claimed.add(i));
      }
    }
    const solo = artifactIds.filter((a) => !claimed.has(a));
    if (solo.length) groups.push({ name: "Solo artifacts", ids: solo });
    return groups;
  }, [concepts, artifactIds.join(",")]);

  const tripTagsForCurrent = useMemo(
    () =>
      openTripwires
        .map((t: any) => {
          const [aid, elId] = (t.changed || "").split("#");
          if (aid !== currentArtifact) return null;
          const els = viewingRevn != null ? viewScenes[aid] : buffersRef.current[aid] || artifacts[aid]?.elements;
          const el = (els || []).find((e: any) => e.id === elId);
          return el ? { el, t } : null;
        })
        .filter(Boolean),
    [openTripwires, currentArtifact, state, viewingRevn, viewScenes]
  );

  const copyContextUpdate = useCallback(() => {
    const s = lastSave;
    const parts = [
      "WYSIWYG Grilling context update:",
      s ? `please review save #${s.revn} (${s.short_id}) on branch ${s.branch}.` : `project is at revn ${state?.revn} on branch ${state?.head}.`,
      currentArtifact ? `Current artifact: ${artifacts[currentArtifact]?.name || currentArtifact}.` : "",
      openTripwires.length ? `${openTripwires.length} open mapping tripwire(s).` : "",
      "Read project_knowledge/ and narrate.",
    ].filter(Boolean);
    navigator.clipboard?.writeText(parts.join(" "));
    toast("Context update copied — paste it to the agent in chat.");
  }, [lastSave, state, currentArtifact, openTripwires.length, toast]);

  const emptyCanvas =
    !viewingRevn && currentArtifact != null &&
    !(dirtyMap[currentArtifact]) &&
    !(artifacts[currentArtifact]?.elements || []).length;

  const whoseMove = state?.whose_move || "agent";
  const viewingSave = viewingRevn != null ? state?.saves?.find((s: any) => s.revn === viewingRevn) : null;

  /* ---------------- render ---------------- */
  return (
    <div className="app">
      {/* ============ round header ============ */}
      <header className="header">
        <div className="brand"><span className="logo" /> WYSIWYG&nbsp;Grilling</div>
        <div className="round">
          Round {state?.round ?? "…"} — <span className="whose">{whoseMove === "user" ? "your move" : "agent reading"}</span>
        </div>
        <div className="crumbs">
          {state?.project || "…"} · <b>{currentArtifact ? artifacts[currentArtifact]?.name || currentArtifact : "no artifact yet"}</b>
        </div>
        <div className="spacer" />
        <span className={`chip ${whoseMove === "agent" ? "agent-busy" : "your-move"}`}>
          <span className="dot" />
          {viewingRevn != null ? "viewing history" : whoseMove === "agent" ? "agent is reading…" : pending.length ? "revision waiting" : "your move"}
        </span>
        <div className="cadence" title="canvas update cadence: per-round = agent revisions land automatically on a clean canvas; pulled = they always wait behind the banner">
          <button className={cadence === "per-round" ? "active" : ""} onClick={() => apiPost("/api/config", { patch: { canvas_updates: "per-round" } }).then(refresh)}>per-round</button>
          <button className={cadence === "pulled" ? "active" : ""} onClick={() => apiPost("/api/config", { patch: { canvas_updates: "pulled" } }).then(refresh)}>pulled</button>
        </div>
        <button className="icon-btn" title="copy a context update to paste to the agent" onClick={copyContextUpdate}>⧉ context</button>
        <button
          className="save-btn"
          disabled={!anyDirty || viewingRevn != null}
          onClick={() => doSave()}
          title={viewingRevn != null ? "read-only while viewing history" : anyDirty ? "checkpoint your edits — the agent reads them at Save" : "no unsaved changes"}
        >
          Save
          {anyDirty && viewingRevn == null && <span className="dirty-dot" />}
        </button>
      </header>

      {/* ============ banners ============ */}
      {viewingRevn != null && (
        <div className="view-bar">
          <span>👁 Viewing save <b>#{viewingRevn}{viewingSave ? ` · ${viewingSave.short_id}` : ""}</b> ({viewingSave?.headline || "…"}) — read-only. Nothing is lost; “Back to live” returns to the present.</span>
          <div className="grow" style={{ flex: 1 }} />
          <button className="icon-btn" onClick={workFromHere}>✎ Work from here</button>
          <button className="icon-btn" onClick={backToLive}>↩ Back to live</button>
        </div>
      )}
      {staleInfo && (
        <div className="banner stale">
          <span>⚠ This canvas moved on (now at revn {staleInfo.head_revn}) — another tab or the agent saved first. Refresh to continue; your edits here stay until you choose.</span>
          <div className="grow" />
          <button onClick={async () => { setStaleInfo(null); setDirtyMap({}); buffersRef.current = {}; const s = await refresh(); if (currentRef.current && s) showArtifact(currentRef.current, s, true); }}>Load latest (discard this tab's edits)</button>
          <button onClick={() => setStaleInfo(null)}>Keep editing</button>
        </div>
      )}
      {state?.rollback && (
        <div className="banner rollback">
          <span>↺ The canvas rolled back past save #{state.rollback.matches_revn} (history head is #{state.rollback.head_revn}) — a git revert? The agent will ask; or re-anchor now.</span>
          <div className="grow" />
          <button onClick={() => apiPost("/api/rollback/accept", {}).then(() => { toast("Re-anchored."); refresh(); })}>Re-anchor here</button>
        </div>
      )}
      {freshPins.length > 0 && (
        <div className="banner pin-only">
          <span>❓ The agent asked {freshPins.length === 1 ? "a question" : `${freshPins.length} questions`} — no canvas changes. Answer in the rail, or in chat.</span>
          <div className="grow" />
          <button onClick={() => setSeenPins((d) => [...d, ...freshPins.map((p: any) => p.id)])}>Got it</button>
        </div>
      )}
      {pending.map((p: any) => (
        <div key={p.id} className="banner pending">
          <span>✎ Agent revision waiting{p.note ? `: ${p.note}` : ""} — your unsaved work is safe either way.</span>
          <div className="grow" />
          {!p.deferred && <>
            <button onClick={() => resolvePending(p.id, "apply_now")}>Apply now</button>
            <button onClick={() => resolvePending(p.id, "after_save")}>After I save</button>
          </>}
          {p.deferred && <span style={{ opacity: 0.8 }}>lands after your next Save</span>}
        </div>
      ))}

      {/* ============ main ============ */}
      <div className="main">
        <div className="center">
          <div className="canvas-wrap">
            <Excalidraw
              excalidrawAPI={(api) => (apiRef.current = api)}
              onChange={onChange}
              viewModeEnabled={viewingRevn != null}
              initialData={{
                appState: {
                  viewBackgroundColor: "#faf8f2",
                  currentItemFontFamily: 6,
                  currentItemRoughness: 1,
                  currentItemStrokeWidth: 1,
                },
              }}
              UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false } }}
            />
            {tripTagsForCurrent.map(({ el, t }: any) => (
              <div
                key={t.id}
                className="trip-tag"
                style={{
                  left: (el.x + (el.width || 0) / 2 + camera.scrollX) * camera.zoom,
                  top: (el.y + camera.scrollY) * camera.zoom - 6,
                }}
              >
                ⚠ diverged from {String(t.sibling).split("#")[0]}
              </div>
            ))}
            {emptyCanvas && (
              <div className="empty-state">
                <div className="empty-card">
                  <h2>A blank canvas, on purpose</h2>
                  Draw anywhere and hit <b>Save</b> — the agent narrates what it
                  reads. Or answer in chat and let the agent seed the first
                  sketch. Nothing here is precious; everything is editable.
                </div>
              </div>
            )}
            {!artifactIds.length && !currentArtifact && (
              <div className="empty-state">
                <div className="empty-card">
                  <h2>No artifacts yet</h2>
                  The agent seeds the first wireframe, flow, or domain sketch
                  when the conversation needs one. You can also just draw —
                  your first Save creates an artifact.
                </div>
              </div>
            )}
          </div>
          <HistoryGraph
            saves={state?.saves || []}
            branches={state?.branches || []}
            head={state?.head || "main"}
            headRevn={state?.head_revn || 0}
            viewingRevn={viewingRevn}
            checkoutRevn={state?.checkout_revn ?? null}
            dirty={anyDirty}
            showArchived={false}
            onViewCommit={viewCommit}
            onSwitchBranch={(name) => {
              if (Object.values(dirtyRef.current).some(Boolean)) {
                toast("Unsaved edits — Save before switching branches.");
                return;
              }
              apiPost("/api/branch/switch", { name })
                .then(async () => {
                  setViewingRevn(null); viewingRef.current = null;
                  const s = await refresh();
                  if (currentRef.current && s) showArtifact(currentRef.current, s, true);
                  toast(`Switched to ⎇ ${name} — the agent will catch up on what changed.`);
                })
                .catch((e) => toast(e.message));
            }}
          />
        </div>

        <Rail
          state={state}
          currentArtifact={currentArtifact}
          viewingRevn={viewingRevn}
          onAnswerPin={(id, answer) => {
            apiPost("/api/pins/answer", { id, answer })
              .then(() => { toast("Answer sent — the agent picks it up on its next move."); refresh(); })
              .catch((e) => toast(e.message));
          }}
          onSwitchBranch={(name) => {
            if (Object.values(dirtyRef.current).some(Boolean)) {
              toast("Unsaved edits — Save before switching branches.");
              return;
            }
            apiPost("/api/branch/switch", { name })
              .then(async () => {
                setViewingRevn(null); viewingRef.current = null;
                const s = await refresh();
                if (currentRef.current && s) showArtifact(currentRef.current, s, true);
                toast(`Switched to ⎇ ${name}`);
              })
              .catch((e) => toast(e.message));
          }}
          onArchive={(name, archived) => {
            apiPost("/api/branch/archive", { name, archived })
              .then(() => { toast(archived ? `Archived ⎇ ${name} — hidden, never deleted.` : `Restored ⎇ ${name}`); refresh(); })
              .catch((e) => toast(e.message));
          }}
          onViewCommit={viewCommit}
        />
      </div>

      {/* ============ filmstrip ============ */}
      <div className="filmstrip">
        <div className="artifact-dropdown">
          <button onClick={() => setDropOpen(!dropOpen)}>
            <span className="lbl">All artifacts ▴</span>
            <span>{currentArtifact ? artifacts[currentArtifact]?.name || currentArtifact : "—"}</span>
          </button>
          {dropOpen && (
            <div className="dropdown-menu" onMouseLeave={() => setDropOpen(false)}>
              {groupedArtifacts.length === 0 && <div className="group">nothing here yet</div>}
              {groupedArtifacts.map((g) => (
                <React.Fragment key={g.name}>
                  <div className="group">{g.name}</div>
                  {g.ids.map((aid) => (
                    <div key={aid} className={`item ${aid === currentArtifact ? "current" : ""}`}
                      onClick={() => { setDropOpen(false); showArtifact(aid); }}>
                      <span>{artifacts[aid]?.name || aid}</span>
                      <span className="tag" style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>{artifacts[aid]?.artifact_type}</span>
                    </div>
                  ))}
                </React.Fragment>
              ))}
            </div>
          )}
        </div>
        {currentConceptViews.map((aid: string) => {
          const hasTrip = openTripwires.some((t: any) => (t.changed || "").startsWith(aid + "#") || (t.sibling || "").startsWith(aid + "#"));
          return (
            <div key={aid} className={`thumb ${aid === currentArtifact ? "current" : ""}`} onClick={() => showArtifact(aid)}
              title={`${artifacts[aid]?.name || aid} (${artifacts[aid]?.artifact_type})`}>
              <SceneThumb elements={(viewingRevn != null ? viewScenes[aid] : buffersRef.current[aid] || artifacts[aid]?.elements) || []} />
              <div className="tname">{artifacts[aid]?.name || aid}</div>
              <div className="badges">
                {dirtyMap[aid] && <span className="badge dirty" title="unsaved edits" />}
                {hasTrip && <span className="badge trip" title="open mapping tripwire" />}
              </div>
            </div>
          );
        })}
        <div className="thumb suggest" onClick={() => setSuggestOpen(true)}>+ suggest<br />a view…</div>
      </div>

      {/* ============ modals ============ */}
      {forkPrompt?.open && (
        <div className="modal-scrim">
          <div className="modal">
            <h2>Name this branch</h2>
            <p>You're saving on top of save #{state?.checkout_revn} — this forks a new branch (the old line stays intact; nothing is lost).</p>
            <input autoFocus value={forkPrompt.name}
              onChange={(e) => setForkPrompt({ open: true, name: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") { const n = forkPrompt.name.trim() || "alt"; setForkPrompt(null); doSave(n); } }} />
            <div className="actions">
              <button className="ghost" onClick={() => setForkPrompt(null)}>Cancel</button>
              <button className="primary" onClick={() => { const n = forkPrompt.name.trim() || "alt"; setForkPrompt(null); doSave(n); }}>Fork &amp; Save</button>
            </div>
          </div>
        </div>
      )}
      {suggestOpen && (
        <div className="modal-scrim">
          <div className="modal">
            <h2>Suggest a view</h2>
            <p>What question should a new view make tangible? The agent proposes views when there's a real seed — your ask lands in its next move.</p>
            <textarea rows={3} autoFocus value={suggestText} placeholder="e.g. What does checkout look like on mobile? A state diagram for orders?"
              onChange={(e) => setSuggestText(e.target.value)} />
            <div className="actions">
              <button className="ghost" onClick={() => { setSuggestOpen(false); setSuggestText(""); }}>Cancel</button>
              <button className="primary" disabled={!suggestText.trim()}
                onClick={() => {
                  apiPost("/api/suggest-view", { text: suggestText.trim(), concept: concepts.find((c: any) => (c.views || []).includes(currentArtifact))?.id })
                    .then(() => { toast("Suggestion recorded — the agent will pick it up."); setSuggestOpen(false); setSuggestText(""); })
                    .catch((e) => toast(e.message));
                }}>Send to agent</button>
            </div>
          </div>
        </div>
      )}

      {/* ============ toasts ============ */}
      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className="toast">{t.text}</div>)}
      </div>
    </div>
  );
}
