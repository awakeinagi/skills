import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Excalidraw, exportToBlob, restoreElements, CaptureUpdateAction,
} from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { apiGet, apiPost, fingerprint, replayChanges } from "./api";
import { Rail } from "./components/Rail";
import { HistoryGraph } from "./components/HistoryGraph";
import { SceneThumb } from "./components/Thumb";
import { QuestionModal, gotoRefOf } from "./components/QuestionUI";
import { DocReader } from "./components/DocReader";
import { AnchoredPopover, AnchoredQuestion, anchorStyle } from "./components/AnchoredPopover";
import { TooltipCard, TooltipEditor } from "./components/Tooltip";
import { Inspector } from "./components/Inspector";
import { libraryItems, TEMPLATES, templateElements } from "./library";

const POLL_MS = 2500;

let toastSeq = 0;

/** One element of the onion skin. Deliberately schematic: the point is
 * "something used to be here, this big" — a faithful re-render would
 * compete with the live drawing instead of annotating it. */
function ghostShape(e: any) {
  const w = e.width || 0, h = e.height || 0;
  if (e.type === "ellipse")
    return <ellipse key={e.id} cx={e.x + w / 2} cy={e.y + h / 2} rx={w / 2} ry={h / 2} />;
  if (e.type === "diamond") {
    const cx = e.x + w / 2, cy = e.y + h / 2;
    return <polygon key={e.id}
      points={`${cx},${e.y} ${e.x + w},${cy} ${cx},${e.y + h} ${e.x},${cy}`} />;
  }
  if ((e.type === "arrow" || e.type === "line" || e.type === "freedraw") &&
      e.points?.length >= 2)
    return <polyline key={e.id}
      points={e.points.map((p: number[]) => `${e.x + p[0]},${e.y + p[1]}`).join(" ")} />;
  if (e.type === "text")
    return <text key={e.id} x={e.x} y={e.y + (e.fontSize || 14) * 0.95}
      fontSize={e.fontSize || 14}>{String(e.text || "").split("\n")[0]}</text>;
  return <rect key={e.id} x={e.x} y={e.y} width={w} height={h} />;
}

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
  const [narrOpen, setNarrOpen] = useState(false);
  const [camera, setCamera] = useState({ scrollX: 0, scrollY: 0, zoom: 1 });
  const [detailItem, setDetailItem] = useState<{ kind: "pin" | "tripwire"; data: any } | null>(null);
  const [docView, setDocView] = useState<{ path: string; content: string } | null>(null);
  // v0.3 — anchored popovers, tooltips, inspector, revert
  const [anchored, setAnchored] = useState<{
    kind: "pin" | "tripwire"; data: any;
    el: { x: number; y: number; width?: number; height?: number };
  } | null>(null);
  const [tooltipEdit, setTooltipEdit] = useState<{
    elId: string; initial: string;
    el: { x: number; y: number; width?: number; height?: number };
  } | null>(null);
  const [hoverTip, setHoverTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [revertPrompt, setRevertPrompt] = useState(false);
  const [selElId, setSelElId] = useState<string | null>(null);
  const [docsList, setDocsList] = useState<string[]>([]);
  const [insertOpen, setInsertOpen] = useState(false);
  const [walkIdx, setWalkIdx] = useState<number | null>(null);
  // the onion skin: an old revision kept as a DOM overlay only. It never
  // enters the Excalidraw scene, so it can never reach a save buffer.
  const [ghost, setGhost] = useState<{ revn: number; scenes: Record<string, any[]> } | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    (typeof localStorage !== "undefined" && localStorage.getItem("wysiwyg-theme") === "dark")
      ? "dark" : "light");

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
  const prevSelRef = useRef<string>("");
  const suppressPinOpenRef = useRef<number>(0);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const cameraRef = useRef({ scrollX: 0, scrollY: 0, zoom: 1 });
  const hoverTimerRef = useRef<any>(null);

  currentRef.current = currentArtifact;
  dirtyRef.current = dirtyMap;
  viewingRef.current = viewingRevn;
  cameraRef.current = camera;

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

  /** Re-hydrate Excalidraw's file store from the artifact's persisted
   * images. Without this an imported image round-trips as an empty box:
   * the element survives the save, its bytes live in `files`, and the
   * canvas has no idea where to find them. */
  const addStoredFiles = useCallback((aid: string, st?: any) => {
    const api = apiRef.current;
    const s = st || stateRef.current;
    if (!api?.addFiles) return;
    const stored = s?.artifacts?.[aid]?.files;
    if (!stored) return;
    const vals = Object.values<any>(stored).filter((f) => f && f.id && f.dataURL);
    if (vals.length) api.addFiles(vals as any);
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
      addStoredFiles(aid, s);
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
    [loadScene, addStoredFiles]
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
            addStoredFiles(cur, s);
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
  }, [showArtifact, loadScene, mergePinOnlyGap, addStoredFiles]);

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
    // pin ❓ glyph click → open its detail card. The glyph is a plain
    // Excalidraw text element, so pointer selection is the only signal —
    // before this, clicking the glyph did nothing (capability assessment).
    const selIds = Object.keys(appState.selectedElementIds || {}).filter(
      (k) => appState.selectedElementIds[k]);
    const selKey = selIds.join(",");
    if (selKey !== prevSelRef.current) {
      prevSelRef.current = selKey;
      setSelElId(selIds.length === 1 ? selIds[0] : null);
      if (selIds.length === 1 && Date.now() > suppressPinOpenRef.current) {
        const sel = els.find((e) => e.id === selIds[0]);
        if (sel?.customData?.role === "pin") {
          const pin = stateRef.current?.pins?.find((p: any) => p.id === sel.id);
          if (pin && pin.status !== "resolved")
            // anchored next to the ❓ (v0.3) — the centered modal lost
            // the spatial context the pin exists to give
            setAnchored({ kind: "pin", data: pin,
              el: { x: sel.x, y: sel.y, width: sel.width, height: sel.height } });
        } else if (sel?.customData?.document) {
          // report reader: a document-backed element opens readable in
          // place, never download-to-read (demo parity)
          apiGet(`/api/doc/${encodeURI(sel.customData.document)}`)
            .then((r) => setDocView({ path: r.path, content: r.content }))
            .catch((e) => toast(e.message));
        }
      }
    }
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
      // ship the bytes behind any image element in the artifacts we're
      // saving. Excalidraw keeps files in one flat store keyed by fileId;
      // the server keeps them per artifact, so split by what each scene
      // actually references. The server MERGES what arrives, so sending a
      // subset is safe — it never drops files we didn't mention.
      const allFiles: Record<string, any> =
        (apiRef.current?.getFiles ? apiRef.current.getFiles() : null) || {};
      const files: Record<string, Record<string, any>> = {};
      for (const [aid, els] of Object.entries(scenes)) {
        const mine: Record<string, any> = {};
        for (const e of els as any[])
          if (e.fileId && allFiles[e.fileId]) mine[e.fileId] = allFiles[e.fileId];
        if (Object.keys(mine).length) files[aid] = mine;
      }
      try {
        const base = state.checkout_revn != null ? state.checkout_revn
          : (loadedRevnRef.current || state.head_revn);
        const r = await apiPost("/api/save", {
          base_revn: base, scenes, selection, fork_name: forkName || undefined,
          files: Object.keys(files).length ? files : undefined,
        });
        setDirtyMap({});
        buffersRef.current = {};
        setLastSave(r);
        setNarrOpen(true);
        const s = await refresh();
        if (s && cur) showArtifact(cur, s, true);
        toast(`Saved ✓ #${r.revn} (${r.short_id})${r.branch !== "main" ? ` on ⎇ ${r.branch}` : ""}`);
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
            appState: {
              viewBackgroundColor: "#faf8f2", exportWithDarkMode: false,
              frameRendering: { enabled: true, name: true, outline: true, clip: false },
            },
            files: null,
            mimeType: "image/png",
            exportPadding: 40,
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

  /* ---------------- goto: reveal an anchored element on canvas -------- */
  const revealElement = useCallback((elId: string) => {
    const api = apiRef.current;
    if (!api) return;
    const el = api.getSceneElements().find((e: any) => e.id === elId);
    if (!el) { toast("That element is no longer on the canvas."); return; }
    try {
      // programmatic selection must not re-open the pin card the user is
      // already reading ("show on canvas" would boomerang)
      suppressPinOpenRef.current = Date.now() + 1200;
      api.scrollToContent([el], { fitToViewport: false, animate: true });
      api.updateScene({ appState: { selectedElementIds: { [el.id]: true } } });
    } catch {
      /* selection API differences are cosmetic — scroll is the point */
    }
  }, []);

  const gotoElement = useCallback((ref: { aid: string; el: string }) => {
    if (!ref) return;
    if (ref.aid !== currentRef.current) {
      showArtifact(ref.aid);
      // let the scene land before revealing
      setTimeout(() => revealElement(ref.el), 350);
    } else {
      revealElement(ref.el);
    }
  }, [revealElement, showArtifact]);

  const answerTripwire = useCallback((id: string, answer: string) => {
    apiPost("/api/tripwires/answer", { id, answer })
      .then(() => { toast("Answer sent — the agent picks it up on its next move."); refresh(); })
      .catch((e) => toast(e.message));
  }, [refresh]);

  /* annotation → target leader lines (layout.md promises them; the
     renderer never drew them) */
  const leaderLines = useMemo(() => {
    if (!currentArtifact) return [];
    const els = (viewingRevn != null
      ? viewScenes[currentArtifact]
      : buffersRef.current[currentArtifact] ||
        artifacts[currentArtifact]?.elements) || [];
    const ix: Record<string, any> = {};
    for (const e of els) ix[e.id] = e;
    const out: { a: any; b: any }[] = [];
    for (const e of els) {
      const tgt = e.customData?.annotates && ix[e.customData.annotates];
      if (e.customData?.role === "annotation" && tgt)
        out.push({ a: e, b: tgt });
    }
    return out;
  }, [currentArtifact, state, viewingRevn, viewScenes]);

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

  /* Esc closes whichever lightweight modal is open — before this, the
     suggest-view scrim could only be closed via Cancel and silently
     swallowed every click including Save (capability assessment) */
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (suggestOpen) { setSuggestOpen(false); setSuggestText(""); }
      else if (forkPrompt?.open) setForkPrompt(null);
      else if (revertPrompt) setRevertPrompt(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [suggestOpen, forkPrompt, revertPrompt]);

  /* ---------------- user-authored elements (Phase 5) ---------------- */
  const insertElements = useCallback((newEls: any[]) => {
    const api = apiRef.current;
    if (!api) return;
    const live = api.getSceneElements().filter((e: any) => !e.isDeleted);
    const restored = restoreElements([...live, ...newEls], null, {
      repairBindings: true,
    } as any);
    api.updateScene({ elements: restored });
  }, []);

  const sceneCenter = useCallback(() => {
    const a = appStateRef.current || {};
    const z = camera.zoom || 1;
    return {
      x: (a.width || 800) / 2 / z - camera.scrollX,
      y: (a.height || 600) / 2 / z - camera.scrollY,
    };
  }, [camera]);

  const addStickyNote = useCallback(() => {
    const text = window.prompt("Sticky note (yours — the agent reads it as a requirement):");
    if (!text) return;
    const { x, y } = sceneCenter();
    const id = `note-user-${Date.now().toString(36)}`;
    insertElements([
      {
        id, type: "rectangle", x: x - 90, y: y - 45, width: 180, height: 90,
        backgroundColor: "#fbf3c9", strokeColor: "#c9b961",
        fillStyle: "solid", strokeWidth: 1, roughness: 1, opacity: 100,
        angle: 0, roundness: null, groupIds: [], frameId: null,
        boundElements: [{ id: `${id}-label`, type: "text" }],
        customData: { role: "annotation", author: "user" },
      },
      {
        id: `${id}-label`, type: "text", x: x - 82, y: y - 37,
        width: 164, height: 74, text, originalText: text, fontSize: 14,
        fontFamily: 6, textAlign: "left", verticalAlign: "top",
        lineHeight: 1.25, containerId: id, autoResize: false,
        strokeColor: "#4a4433", customData: { role: "label" },
      },
    ]);
    toast("Note added — Save to record it.");
  }, [insertElements, sceneCenter, toast]);

  const askUserPin = useCallback(() => {
    const api = apiRef.current;
    if (!api) return;
    const selIds = Object.keys(appStateRef.current?.selectedElementIds || {})
      .filter((k) => appStateRef.current.selectedElementIds[k]);
    const target = api.getSceneElements().find(
      (e: any) => selIds.includes(e.id) && !e.isDeleted);
    if (!target) {
      toast("Select an element first — questions anchor to elements (cross-cutting questions go in chat).");
      return;
    }
    const q = window.prompt("Your question about this element (the agent answers on its move):");
    if (!q) return;
    const id = `pin-user-${Date.now().toString(36)}`;
    insertElements([{
      id, type: "text", x: target.x + (target.width || 0) + 8,
      y: target.y - 8, width: 26, height: 26, text: "❓",
      originalText: "❓", fontSize: 20, fontFamily: 6,
      textAlign: "center", verticalAlign: "top", lineHeight: 1.25,
      containerId: null, autoResize: true, strokeColor: "#5b9dff",
      strokeWidth: 1, roughness: 1, opacity: 100, angle: 0,
      roundness: null, groupIds: [], frameId: null, boundElements: [],
      backgroundColor: "transparent", fillStyle: "solid",
      customData: { role: "pin", question: q, target: target.id,
                    status: "open", author: "user", direction: "user" },
    }]);
    toast("Question pinned — Save to send it to the agent.");
  }, [insertElements, toast]);

  const dismissPin = useCallback((p: any) => {
    // the user's "not worth explaining": delete the ❓ element; the next
    // Save records the deletion and the server marks the pin dismissed
    if (p.artifact && p.artifact !== currentRef.current) {
      showArtifact(p.artifact);
      toast("Switched to the pin's artifact — dismiss it there.");
      return;
    }
    const api = apiRef.current;
    if (!api) return;
    const live = api.getSceneElements().filter(
      (e: any) => !e.isDeleted && e.id !== p.id);
    api.updateScene({ elements: live });
    toast("Pin dismissed — Save to record it (reads as “not worth explaining”).");
  }, [showArtifact, toast]);

  /* ---------------- v0.3: tooltips, inspector, revert ---------------- */

  /** Client coords → scene coords via the live camera. */
  const sceneAt = useCallback((clientX: number, clientY: number) => {
    const wrap = wrapRef.current;
    if (!wrap) return null;
    const r = wrap.getBoundingClientRect();
    const cam = cameraRef.current;
    const z = cam.zoom || 1;
    return {
      sx: (clientX - r.left) / z - cam.scrollX,
      sy: (clientY - r.top) / z - cam.scrollY,
      wx: clientX - r.left, wy: clientY - r.top,
    };
  }, []);

  /** Smallest non-label element whose bbox contains the scene point. */
  const hitAtClient = useCallback((clientX: number, clientY: number) => {
    const api = apiRef.current;
    const p = sceneAt(clientX, clientY);
    if (!api || !p) return null;
    let best: any = null;
    let bestArea = Infinity;
    for (const e of api.getSceneElements()) {
      if (e.isDeleted || e.type === "frame") continue;
      if (e.customData?.role === "label") continue;
      const w = e.width || 0, h = e.height || 0;
      if (p.sx >= e.x && p.sx <= e.x + w && p.sy >= e.y && p.sy <= e.y + h) {
        const area = (w * h) || 1;
        if (area < bestArea) { best = e; bestArea = area; }
      }
    }
    return best;
  }, [sceneAt]);

  /** Patch a live element (fields + customData; null cd value deletes the
   * key). Version bump makes the change reach the dirty fingerprint, so
   * it lands in the next Save and narrates like any user edit. */
  const patchElement = useCallback((elId: string, patch: any, cdPatch?: any) => {
    const api = apiRef.current;
    if (!api) return;
    const els = api.getSceneElements().map((e: any) => {
      if (e.id !== elId || e.isDeleted) return e;
      const cd: any = { ...(e.customData || {}) };
      for (const [k, v] of Object.entries(cdPatch || {})) {
        if (v === null) delete cd[k];
        else cd[k] = v;
      }
      return { ...e, ...patch, customData: cd, version: (e.version || 0) + 1 };
    });
    api.updateScene({ elements: restoreElements(els, null) as any });
  }, []);

  const setElementTooltip = useCallback((elId: string, text: string) => {
    patchElement(elId, {}, { tooltip: text || null });
    toast(text ? "Tooltip set — Save to record it."
      : "Tooltip removed — Save to record it.");
  }, [patchElement, toast]);

  const openTooltipEditor = useCallback((el: any) => {
    setTooltipEdit({
      elId: el.id, initial: el.customData?.tooltip || "",
      el: { x: el.x, y: el.y, width: el.width, height: el.height },
    });
  }, []);

  /* right-click on an element: Excalidraw's own menu opens untouched
     (copy, delete, z-order…) and the tooltip actions are APPENDED to it.
     There is no public menu-extension API, so we inject styled items
     into the rendered menu — replacing the native menu was a v0.3
     regression (user report). */
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const closeNativeMenu = () => {
      // Excalidraw closes its menu on Escape / outside pointerdown
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    };
    const h = (e: MouseEvent) => {
      if (viewingRef.current != null) return;
      const hit = hitAtClient(e.clientX, e.clientY);
      if (!hit) return;
      let tries = 0;
      const inject = () => {
        const menu = wrap.querySelector<HTMLElement>("ul.context-menu") ||
          document.querySelector<HTMLElement>("ul.context-menu");
        if (!menu) {
          if (++tries < 10) setTimeout(inject, 30);
          return;
        }
        if (menu.querySelector(".wg-tooltip-item")) return;
        // collapse the bulkiest native groups into hover flyouts (user
        // request: the flat menu ate too much vertical space). Moving
        // the original <li> nodes keeps their React handlers and
        // shortcut hints intact.
        const labelOf = (li: Element) =>
          li.querySelector(".context-menu-item__label")?.textContent || "";
        const collapse = (test: (l: string) => boolean, title: string) => {
          const lis = Array.from(menu.children).filter(
            (li) => li.tagName === "LI" && test(labelOf(li)));
          if (lis.length < 2) return;
          const parent = document.createElement("li");
          parent.className = "wg-submenu-parent";
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "context-menu-item";
          const span = document.createElement("span");
          span.className = "context-menu-item__label";
          span.textContent = title;
          const arrow = document.createElement("span");
          arrow.className = "context-menu-item__shortcut";
          arrow.textContent = "▸";
          btn.append(span, arrow);
          btn.addEventListener("click", (ev) => {
            ev.stopPropagation();
            ev.preventDefault();
          });
          const sub = document.createElement("ul");
          sub.className = "context-menu wg-submenu";
          // position:fixed escapes the popover's overflow clipping (a
          // left/right flyout otherwise gets cut at the menu's box);
          // :hover still reaches it because it stays a DOM child
          parent.addEventListener("mouseenter", () => {
            const r = parent.getBoundingClientRect();
            const fitsRight = r.right + 200 <= window.innerWidth;
            sub.style.left = (fitsRight ? r.right : r.left - 180) + "px";
            sub.style.top = Math.max(8, Math.min(
              r.top - 8, window.innerHeight - 40 * lis.length - 24)) + "px";
          });
          parent.append(btn, sub);
          menu.insertBefore(parent, lis[0]);
          lis.forEach((li) => sub.appendChild(li));
        };
        if (!menu.querySelector(".wg-submenu-parent")) {
          collapse((l) => l.startsWith("Copy to clipboard as"), "Copy as…");
          collapse((l) => ["Send backward", "Bring forward",
                           "Send to back", "Bring to front"].includes(l),
                   "Arrange");
        }
        const live = apiRef.current?.getSceneElements()
          .find((x: any) => x.id === hit.id && !x.isDeleted) || hit;
        const has = !!live.customData?.tooltip;
        const mk = (label: string, fn: () => void) => {
          const li = document.createElement("li");
          li.className = "wg-tooltip-item";
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "context-menu-item";
          const span = document.createElement("span");
          span.className = "context-menu-item__label";
          span.textContent = label;
          btn.appendChild(span);
          btn.addEventListener("click", (ev) => {
            ev.stopPropagation();
            closeNativeMenu();
            fn();
          });
          li.appendChild(btn);
          menu.appendChild(li);
        };
        const sep = document.createElement("li");
        sep.className = "context-menu-item-separator wg-tooltip-item";
        menu.appendChild(sep);
        mk(has ? "✎ Edit tooltip…" : "🛈 Add tooltip…",
          () => openTooltipEditor(live));
        if (has) mk("Remove tooltip", () => setElementTooltip(live.id, ""));
      };
      setTimeout(inject, 0);
    };
    wrap.addEventListener("contextmenu", h);
    return () => wrap.removeEventListener("contextmenu", h);
  }, [hitAtClient, openTooltipEditor, setElementTooltip]);

  /* hover → tooltip card after a beat; any movement re-arms */
  const onWrapPointerMove = useCallback((e: React.PointerEvent) => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    if (hoverTip) setHoverTip(null);
    if (e.buttons !== 0 || walkIdx != null) return;
    const { clientX, clientY } = e;
    hoverTimerRef.current = setTimeout(() => {
      const el = hitAtClient(clientX, clientY);
      const text = el?.customData?.tooltip;
      const p = sceneAt(clientX, clientY);
      if (text && p) setHoverTip({ x: p.wx + 14, y: p.wy + 12, text });
    }, 450);
  }, [hitAtClient, sceneAt, hoverTip, walkIdx]);

  const onWrapPointerLeave = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHoverTip(null);
  }, []);

  /** Revert all: drop every unsaved buffer and restore the currently
   * selected history node (live head, or the checkout point). */
  const revertAll = useCallback(async () => {
    setRevertPrompt(false);
    buffersRef.current = {};
    setDirtyMap({});
    const cur = currentRef.current;
    const co = stateRef.current?.checkout_revn;
    if (co != null && cur) {
      try {
        const r = await apiGet(`/api/artifact/${cur}?at=${co}`);
        const els = r.elements || [];
        loadedHashRef.current[cur] = JSON.stringify(els);
        const restored = loadScene(els, false);
        baselineFpRef.current[cur] = fingerprint(restored);
        pendingBaselineRef.current = cur;
        toast(`Restored to save #${co} — nothing was recorded.`);
      } catch (e: any) {
        toast(e.message);
      }
    } else {
      const s = await refresh();
      if (cur && s) showArtifact(cur, s, true);
      toast(`Restored to save #${stateRef.current?.head_revn ?? "?"} — nothing was recorded.`);
    }
  }, [refresh, showArtifact, loadScene, toast]);

  /* attachable documents for the inspector */
  useEffect(() => {
    apiGet("/api/docs").then((r) => setDocsList(r.docs || [])).catch(() => {});
  }, [state?.revn]);

  /* tooltip presence dots — discoverability for hover-only content */
  const tooltipDots = useMemo(() => {
    if (!currentArtifact) return [];
    const els = (viewingRevn != null
      ? viewScenes[currentArtifact]
      : buffersRef.current[currentArtifact] ||
        artifacts[currentArtifact]?.elements) || [];
    return els.filter((e: any) => !e.isDeleted && e.customData?.tooltip);
  }, [currentArtifact, state, viewingRevn, viewScenes, dirtyMap]);

  const selEl = useMemo(() => {
    if (!selElId || !apiRef.current) return null;
    return apiRef.current.getSceneElements()
      .find((e: any) => e.id === selElId && !e.isDeleted) || null;
  }, [selElId, dirtyMap, state, camera]);

  /* ---------------- Phase 6: wireframing power tools ---------------- */

  useEffect(() => {
    try { localStorage.setItem("wysiwyg-theme", theme); } catch { /* private mode */ }
  }, [theme]);

  /** PNG of the artifact as it stands. Exports always render light: the
   * canvas theme is a reading preference, the artifact is paper. */
  const exportPng = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const els = api.getSceneElements().filter((e: any) => !e.isDeleted);
    if (!els.length) { toast("Nothing to export — this artifact is empty."); return; }
    try {
      const blob = await exportToBlob({
        elements: restoreElements(els, null) as any,
        // clip:false — frame membership must not crop annotations that
        // spill outside their screen frame (v0.3 assessment); padding
        // keeps estimated text extents from being cut at the edge
        appState: {
          viewBackgroundColor: "#faf8f2", exportWithDarkMode: false,
          frameRendering: { enabled: true, name: true, outline: true, clip: false },
        },
        files: api.getFiles ? api.getFiles() : null,
        mimeType: "image/png",
        exportPadding: 40,
      } as any);
      const name = String(artifacts[currentArtifact!]?.name || currentArtifact || "artifact")
        .replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "artifact";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${name}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast(`Exported ${name}.png`);
    } catch (e: any) {
      toast(`Export failed: ${e.message}`);
    }
  }, [artifacts, currentArtifact, toast]);

  const doTidy = useCallback(async () => {
    if (Object.values(dirtyRef.current).some(Boolean)) {
      toast("Save first — tidy works on the committed state.");
      return;
    }
    const aid = currentRef.current;
    if (!aid) return;
    try {
      const r = await apiPost("/api/tidy", { artifact: aid });
      toast(`✨ ${r.headline} (save #${r.revn} — revertable like any other)`);
      await refresh();
    } catch (e: any) {
      toast(`Tidy failed: ${e.message}`);
    }
  }, [refresh, toast]);

  const zoomFit = useCallback(() => {
    const api = apiRef.current;
    if (!api) return;
    const els = api.getSceneElements().filter((e: any) => !e.isDeleted);
    if (!els.length) { toast("Nothing to fit — this artifact is empty."); return; }
    api.scrollToContent(els, { fitToViewport: true, viewportZoomFactor: 0.85 });
  }, [toast]);

  const insertFrame = useCallback((w: number, h: number, label: string) => {
    const { x, y } = sceneCenter();
    const id = `frame-user-${Date.now().toString(36)}`;
    insertElements([{
      id, type: "frame", x: Math.round(x - w / 2), y: Math.round(y - h / 2),
      width: w, height: h, name: `SCREEN — ${label}`,
      angle: 0, strokeColor: "#bbb", backgroundColor: "transparent",
      fillStyle: "solid", strokeWidth: 1, strokeStyle: "solid",
      roughness: 0, opacity: 100, groupIds: [], frameId: null,
      roundness: null, boundElements: [], locked: false,
    }]);
    setInsertOpen(false);
    toast(`${label} frame added — rename it on canvas, Save to record it.`);
  }, [insertElements, sceneCenter, toast]);

  const insertTemplate = useCallback((kind: string, name: string) => {
    const { x, y } = sceneCenter();
    const els = templateElements(kind, Math.round(x), Math.round(y));
    if (!els.length) return;
    insertElements(els);
    setInsertOpen(false);
    toast(`${name} added — edit the labels, Save, and the agent reads it back.`);
  }, [insertElements, sceneCenter, toast]);

  const labelSave = useCallback((revn: number, current: string) => {
    const v = window.prompt("Bookmark this save:", current || "");
    if (v == null) return;
    const label = v.trim();
    apiPost("/api/save-label", { revn, label })
      .then(() => {
        toast(label ? `Bookmarked save #${revn} — 🔖 ${label}` : `Bookmark cleared on save #${revn}`);
        refresh();
      })
      .catch((e) => toast(e.message));
  }, [refresh, toast]);

  /* -------- onion skin: keep the old revision as an overlay -------- */
  const compareWithLive = useCallback(() => {
    const revn = viewingRef.current;
    if (revn == null) return;
    setGhost({ revn, scenes: { ...viewScenesRef.current } });
    backToLive();
  }, [backToLive]);

  const ghostEls = useMemo(() => {
    if (!ghost || !currentArtifact) return [];
    return (ghost.scenes[currentArtifact] || []).filter((e: any) => !e.isDeleted);
  }, [ghost, currentArtifact]);

  /* -------- prototype walk -------- */
  const framesOf = useCallback(() => {
    const api = apiRef.current;
    if (!api) return [] as any[];
    return api.getSceneElements()
      .filter((e: any) => e.type === "frame" && !e.isDeleted)
      .slice()
      .sort((a: any, b: any) => (a.x - b.x) || (a.y - b.y));
  }, []);

  const walkTo = useCallback((idx: number) => {
    const api = apiRef.current;
    const fs = framesOf();
    if (!api || !fs.length) return;
    const i = ((idx % fs.length) + fs.length) % fs.length;
    const f = fs[i];
    const members = api.getSceneElements()
      .filter((e: any) => e.frameId === f.id && !e.isDeleted);
    api.scrollToContent([f, ...members], { fitToViewport: true, viewportZoomFactor: 0.85 });
    setWalkIdx(i);
  }, [framesOf]);

  const hasFrames = useMemo(() => {
    if (!currentArtifact) return false;
    const els = (viewingRevn != null
      ? viewScenes[currentArtifact]
      : buffersRef.current[currentArtifact] || artifacts[currentArtifact]?.elements) || [];
    return els.some((e: any) => e.type === "frame" && !e.isDeleted);
  }, [currentArtifact, state, viewingRevn, viewScenes, dirtyMap]);

  // walking is per-artifact — switching views ends the walk rather than
  // silently pointing the index at a frame that isn't there
  useEffect(() => { setWalkIdx(null); }, [currentArtifact]);

  useEffect(() => {
    if (walkIdx == null) return;
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (/^(INPUT|TEXTAREA)$/.test(t.tagName) || t.isContentEditable)) return;
      if (e.key === "Escape") setWalkIdx(null);
      else if (e.key === "ArrowRight") { e.preventDefault(); walkTo(walkIdx + 1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); walkTo(walkIdx - 1); }
      else return;
      e.stopPropagation();
    };
    // capture: Excalidraw binds arrow keys for nudging a selection, and it
    // would eat them before the walk ever saw them
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  }, [walkIdx, walkTo]);

  const walkFrames = walkIdx != null ? framesOf() : [];

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
        <button className="icon-btn" title="add a sticky note (yours — reads as a requirement)" disabled={viewingRevn != null} onClick={addStickyNote}>🗒 note</button>
        <button className="icon-btn" title="pin a question on the selected element — the agent answers on its move" disabled={viewingRevn != null} onClick={askUserPin}>❓ ask</button>
        <div className="insert-wrap">
          <button className="icon-btn" title="drop a screen frame or an archetype template at the centre of the view"
            disabled={viewingRevn != null}
            onClick={() => setInsertOpen((o) => !o)}>+ insert ▾</button>
          {insertOpen && (
            <div className="insert-menu" onMouseLeave={() => setInsertOpen(false)}>
              <div className="group">Screen frames</div>
              <div className="item" onClick={() => insertFrame(360, 640, "Phone")}>
                Phone frame <span className="dim">360×640</span></div>
              <div className="item" onClick={() => insertFrame(768, 1024, "Tablet")}>
                Tablet frame <span className="dim">768×1024</span></div>
              <div className="item" onClick={() => insertFrame(1280, 800, "Desktop")}>
                Desktop frame <span className="dim">1280×800</span></div>
              <div className="group">Archetypes</div>
              {TEMPLATES.map((t) => (
                <div key={t.id} className="item" onClick={() => insertTemplate(t.id, t.name)}>
                  {t.name} <span className="dim">{t.hint}</span></div>
              ))}
            </div>
          )}
        </div>
        <button className="icon-btn" title="snap to the grid, re-route arrows, normalize z-order — lands as an ordinary revision you can revert"
          disabled={viewingRevn != null} onClick={doTidy}>✨ tidy</button>
        <button className="icon-btn" title="copy a context update to paste to the agent" onClick={copyContextUpdate}>⧉ context</button>
        <button className="icon-btn" title="download this artifact as a PNG" onClick={exportPng}>⇓ png</button>
        <button className="icon-btn" title="zoom to fit everything on this artifact" onClick={zoomFit}>⤢ fit</button>
        <button className="icon-btn" title={hasFrames ? "walk the screens like a prototype — ←/→ to move, Esc to exit" : "no screen frames on this artifact yet — insert one first"}
          disabled={!hasFrames} onClick={() => (walkIdx == null ? walkTo(0) : setWalkIdx(null))}>▶ walk</button>
        <button className="icon-btn" title={theme === "dark" ? "switch the canvas back to paper" : "dark canvas (the chrome stays dark either way)"}
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "☀" : "🌙"}</button>
        <button className="icon-btn revert-btn"
          disabled={!anyDirty || viewingRevn != null}
          onClick={() => setRevertPrompt(true)}
          title={anyDirty ? "discard ALL unsaved changes and restore the current save" : "no unsaved changes to revert"}
        >↺ revert</button>
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
          <button className="icon-btn" onClick={compareWithLive}
            title="return to the present, keeping this save as a translucent outline over the live canvas">👻 Compare with live</button>
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
      {narrOpen && lastSave?.summary && (
        <div className="banner narration">
          <span>
            📖 Save #{lastSave.revn} recorded: <b>{lastSave.summary.headline}</b>
            {Object.keys(lastSave.summary.verb_counts || {}).length > 1 &&
              ` — ${Object.entries(lastSave.summary.verb_counts)
                .map(([k, v]) => `${v}× ${k.replace(/_/g, " ")}`)
                .join(", ")}`}
            {lastSave.summary.suppressed
              ? ` (${lastSave.summary.suppressed} low-signal suppressed)` : ""}
            {lastSave.tripwires?.length
              ? ` · ⚠ ${lastSave.tripwires.length} tripwire${lastSave.tripwires.length > 1 ? "s" : ""} fired — see the rail`
              : ""}
            {" — the agent narrates from this on its move."}
          </span>
          <div className="grow" />
          <button onClick={() => setNarrOpen(false)}>Got it</button>
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
          <div className={`canvas-wrap ${theme === "dark" ? "dark" : ""}`}
            ref={wrapRef}
            onPointerMove={onWrapPointerMove}
            onPointerLeave={onWrapPointerLeave}>
            <Excalidraw
              theme={theme}
              excalidrawAPI={(api) => {
                apiRef.current = api;
                // debug affordance for a local tool: lets a headless
                // agent (or a human devtools session) interrogate the
                // client-side scene when it diverges from server state
                (window as any).excalidrawAPI = api;
              }}
              onChange={onChange}
              onLinkOpen={(el: any, event: any) => {
                const link = el?.link || "";
                if (link.startsWith("artifact:")) {
                  event?.preventDefault?.();
                  showArtifact(link.slice("artifact:".length));
                }
              }}
              viewModeEnabled={viewingRevn != null}
              initialData={{
                appState: {
                  viewBackgroundColor: "#faf8f2",
                  currentItemFontFamily: 6,
                  currentItemRoughness: 1,
                  currentItemStrokeWidth: 1,
                },
                libraryItems,
              }}
              UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false } }}
            />
            {leaderLines.length > 0 && (
              <svg className="leader-lines">
                {leaderLines.map(({ a, b }, i) => (
                  <line key={i}
                    x1={(a.x + (a.width || 0) / 2 + camera.scrollX) * camera.zoom}
                    y1={(a.y + (a.height || 0) / 2 + camera.scrollY) * camera.zoom}
                    x2={(b.x + (b.width || 0) / 2 + camera.scrollX) * camera.zoom}
                    y2={(b.y + (b.height || 0) / 2 + camera.scrollY) * camera.zoom}
                  />
                ))}
              </svg>
            )}
            {/* onion skin. A DOM sibling of the canvas, never a scene
                element: an old revision drawn into the scene would land in
                the dirty buffer and get saved as if the user drew it. */}
            {ghostEls.length > 0 && (
              <svg className="ghost-layer">
                <g transform={`scale(${camera.zoom}) translate(${camera.scrollX} ${camera.scrollY})`}>
                  {ghostEls.map(ghostShape)}
                </g>
              </svg>
            )}
            {ghost && (
              <div className={`ghost-chip ${walkIdx != null ? "below-walk" : ""}`}>
                👻 comparing with save #{ghost.revn}
                {ghostEls.length === 0 && <em> — this artifact was empty then</em>}
                <button onClick={() => setGhost(null)}>clear</button>
              </div>
            )}
            {walkIdx != null && walkFrames.length > 0 && (
              <div className="walk-bar">
                <b>▶ {walkFrames[walkIdx]?.name || `Screen ${walkIdx + 1}`}</b>
                <span className="pos">{walkIdx + 1} / {walkFrames.length}</span>
                <span className="hint">←/→ screens · Esc exits · links jump between artifacts</span>
                <div style={{ flex: 1 }} />
                <button onClick={() => walkTo(walkIdx - 1)}>←</button>
                <button onClick={() => walkTo(walkIdx + 1)}>→</button>
                <button onClick={() => setWalkIdx(null)}>✕ exit</button>
              </div>
            )}
            {/* click opens the full-stack modal — the old hover card sat
                in the canvas's stacking context with pointer-events:none,
                so its buttons never received a click (capability
                assessment: 'Propagate' clicks fell through to the canvas) */}
            {tripTagsForCurrent.map(({ el, t }: any) => (
              <div
                key={t.id}
                className="trip-mark"
                style={{
                  left: (el.x + (el.width || 0) + camera.scrollX) * camera.zoom,
                  top: (el.y + camera.scrollY) * camera.zoom - 10,
                }}
                onClick={() => setAnchored({ kind: "tripwire", data: t,
                  el: { x: el.x, y: el.y, width: el.width, height: el.height } })}
                title="mapping tripwire — click to read and answer"
              >
                <span className="trip-q">?</span>
              </div>
            ))}
            {/* tooltip presence dots (v0.3) — hover-only content needs a
                discoverable tell */}
            {tooltipDots.map((e: any) => (
              <div key={`tip-${e.id}`} className="tip-dot"
                style={{
                  left: (e.x + (e.width || 0) + camera.scrollX) * camera.zoom - 4,
                  top: (e.y + (e.height || 0) + camera.scrollY) * camera.zoom - 4,
                }}
                title="has a tooltip — hover the element" />
            ))}
            {hoverTip && <TooltipCard x={hoverTip.x} y={hoverTip.y} text={hoverTip.text} />}
            {anchored && (
              <AnchoredPopover
                style={anchorStyle(anchored.el, camera,
                  appStateRef.current?.width || 800)}
                onClose={() => setAnchored(null)}>
                <AnchoredQuestion
                  kind={anchored.kind}
                  data={anchored.data}
                  onAnswer={viewingRevn != null ||
                    ["answered", "resolved", "dismissed", "pruned"]
                      .includes(anchored.data?.status)
                    ? null
                    : (a: string) => {
                        const path = anchored.kind === "pin"
                          ? "/api/pins/answer" : "/api/tripwires/answer";
                        apiPost(path, { id: anchored.data.id, answer: a })
                          .then(() => {
                            toast("Answer sent — the agent picks it up on its next move.");
                            refresh();
                          })
                          .catch((e) => toast(e.message));
                      }}
                  onFullStory={() => {
                    setDetailItem({ kind: anchored.kind, data: anchored.data });
                    setAnchored(null);
                  }}
                  onClose={() => setAnchored(null)}
                />
              </AnchoredPopover>
            )}
            {tooltipEdit && (
              <AnchoredPopover
                style={anchorStyle(tooltipEdit.el, camera,
                  appStateRef.current?.width || 800)}
                onClose={() => setTooltipEdit(null)}>
                <TooltipEditor
                  initial={tooltipEdit.initial}
                  onSave={(text) => setElementTooltip(tooltipEdit.elId, text)}
                  onClose={() => setTooltipEdit(null)}
                />
              </AnchoredPopover>
            )}
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
          onAnswerTripwire={answerTripwire}
          onGoto={gotoElement}
          onDismissPin={dismissPin}
          onOpenDetail={(kind, data) => setDetailItem({ kind, data })}
          onLabelSave={labelSave}
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
          inspector={selEl && currentArtifact ? (
            <Inspector
              el={selEl}
              artifactType={artifacts[currentArtifact]?.artifact_type || "flow"}
              artifacts={artifacts}
              docs={docsList}
              disabled={viewingRevn != null}
              onPatch={(patch, cdPatch) => patchElement(selEl.id, patch, cdPatch)}
              onEditTooltip={() => openTooltipEditor(selEl)}
            />
          ) : null}
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
                      <span className="tag" style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>
                        {artifacts[aid]?.artifact_type}
                        {artifacts[aid]?.tier === "extended" && <span className="tier-chip"> ext</span>}
                      </span>
                    </div>
                  ))}
                </React.Fragment>
              ))}
            </div>
          )}
        </div>
        <div className="thumbs">
          {currentConceptViews.map((aid: string) => {
            const hasTrip = openTripwires.some((t: any) => (t.changed || "").startsWith(aid + "#") || (t.sibling || "").startsWith(aid + "#"));
            const ld = (state?.lint_debt || {})[aid];
            const lintN = ld ? (ld.errors || 0) + (ld.warnings || 0) + (ld.notes || 0) : 0;
            const lintTier = ld?.errors ? "error" : ld?.warnings ? "warning" : "note";
            return (
              <div key={aid} className={`thumb ${aid === currentArtifact ? "current" : ""}`} onClick={() => showArtifact(aid)}
                title={`${artifacts[aid]?.name || aid} (${artifacts[aid]?.artifact_type})`}>
                <SceneThumb elements={(viewingRevn != null ? viewScenes[aid] : buffersRef.current[aid] || artifacts[aid]?.elements) || []} />
                <div className="tname">
                  {artifacts[aid]?.name || aid}
                  <span className="tkind">
                    {artifacts[aid]?.artifact_type}
                    {artifacts[aid]?.tier === "extended" ? " · ext" : ""}
                  </span>
                </div>
                <div className="badges">
                  {dirtyMap[aid] && <span className="badge dirty" title="unsaved edits" />}
                  {hasTrip && <span className="badge trip" title="open mapping tripwire" />}
                  {lintN > 0 && <span className={`badge lint ${lintTier}`} title={`${lintN} layout finding${lintN > 1 ? "s" : ""} — see the Layout rail section`}>{lintN}</span>}
                </div>
              </div>
            );
          })}
          <div className="thumb suggest" onClick={() => setSuggestOpen(true)}>+ suggest<br />a view…</div>
        </div>
      </div>

      {/* ============ modals ============ */}
      {forkPrompt?.open && (
        <div className="modal-scrim"
          onClick={(e) => { if (e.target === e.currentTarget) setForkPrompt(null); }}>
          <div className="modal small">
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
      {revertPrompt && (
        <div className="modal-scrim"
          onClick={(e) => { if (e.target === e.currentTarget) setRevertPrompt(false); }}>
          <div className="modal small">
            <h2>Revert all changes?</h2>
            <p>
              Discards every unsaved edit
              ({Object.values(dirtyMap).filter(Boolean).length} artifact{Object.values(dirtyMap).filter(Boolean).length === 1 ? "" : "s"})
              and restores {state?.checkout_revn != null
                ? <>the checked-out save <b>#{state.checkout_revn}</b></>
                : <>save <b>#{state?.head_revn}</b></>}.
              Nothing already saved is touched — but the unsaved edits are gone for good.
            </p>
            <div className="actions">
              <button className="ghost" autoFocus onClick={() => setRevertPrompt(false)}>Keep editing</button>
              <button className="danger" onClick={revertAll}>↺ Revert all changes</button>
            </div>
          </div>
        </div>
      )}
      {suggestOpen && (
        <div className="modal-scrim"
          onClick={(e) => { if (e.target === e.currentTarget) { setSuggestOpen(false); setSuggestText(""); } }}>
          <div className="modal small">
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
      {docView && (
        <DocReader path={docView.path} content={docView.content}
          onClose={() => setDocView(null)} />
      )}
      {detailItem && (
        <QuestionModal
          kind={detailItem.kind}
          data={detailItem.data}
          onClose={() => setDetailItem(null)}
          onGoto={gotoRefOf(detailItem.data, detailItem.kind)
            ? () => gotoElement(gotoRefOf(detailItem.data, detailItem.kind)!)
            : null}
          onAnswer={viewingRevn != null ||
            ["answered", "resolved", "dismissed", "pruned"]
              .includes(detailItem.data?.status)
            ? null
            : (a: string) => {
                const path = detailItem.kind === "pin"
                  ? "/api/pins/answer" : "/api/tripwires/answer";
                apiPost(path, { id: detailItem.data.id, answer: a })
                  .then(() => {
                    toast("Answer sent — the agent picks it up on its next move.");
                    setDetailItem(null);
                    refresh();
                  })
                  .catch((e) => toast(e.message));
              }}
        />
      )}
    </div>
  );
}
