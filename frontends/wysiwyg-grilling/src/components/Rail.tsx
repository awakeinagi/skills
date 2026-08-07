import React, { useMemo, useState } from "react";

/** Right rail — read-only state display, no navigation (spec §7): registry
 * panel, tripwires in words, interactive pins, branch chips, save timeline. */
export function Rail({
  state, currentArtifact, viewingRevn, onAnswerPin, onSwitchBranch,
  onArchive, onViewCommit,
}: {
  state: any; currentArtifact: string | null; viewingRevn: number | null;
  onAnswerPin: (id: string, answer: string) => void;
  onSwitchBranch: (name: string) => void;
  onArchive: (name: string, archived: boolean) => void;
  onViewCommit: (revn: number) => void;
}) {
  const [showArchivedChips, setShowArchivedChips] = useState(false);
  const [allBranches, setAllBranches] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const concepts = state?.concepts || [];
  const mappings = state?.mappings || [];
  const pins = state?.pins || [];
  const tripwires = (state?.tripwires || []).filter((t: any) => t.status === "open");
  const branches = state?.branches || [];
  const saves = state?.saves || [];
  const pending = state?.pending || [];
  const artifacts = state?.artifacts || {};

  const currentConcept = useMemo(() => {
    if (!currentArtifact) return concepts[0];
    return concepts.find((c: any) => (c.views || []).includes(currentArtifact)) || null;
  }, [concepts, currentArtifact]);

  const savesByRevn = useMemo(() => {
    const m: Record<number, any> = {};
    for (const s of saves) m[s.revn] = s;
    return m;
  }, [saves]);

  // current-branch lineage (walk parents from the branch head)
  const lineage = useMemo(() => {
    const head = branches.find((b: any) => b.name === state?.head);
    const seen = new Set<number>();
    let cur = head?.head;
    while (cur && savesByRevn[cur] && !seen.has(cur)) {
      seen.add(cur);
      cur = savesByRevn[cur].base_revn;
    }
    return seen;
  }, [branches, savesByRevn, state?.head]);

  const forkPoints = useMemo(() => {
    const kids: Record<number, number> = {};
    for (const s of saves) kids[s.base_revn] = (kids[s.base_revn] || 0) + 1;
    return new Set(Object.entries(kids).filter(([, n]) => n > 1).map(([r]) => +r));
  }, [saves]);

  const archivedNames = useMemo(
    () => new Set(branches.filter((b: any) => b.archived).map((b: any) => b.name)),
    [branches]
  );

  const shownSaves = useMemo(() => {
    let list = [...saves].sort((a, b) => b.revn - a.revn);
    if (!allBranches) list = list.filter((s) => lineage.has(s.revn));
    else list = list.filter((s) => showArchivedChips || !archivedNames.has(s.branch));
    return list;
  }, [saves, allBranches, lineage, archivedNames, showArchivedChips]);

  const mappingWords = (m: any) => {
    const els = (m.elements || []).map((e: string) => e.replace("#", " › "));
    return els.join("  ↔  ");
  };

  const conceptMappings = currentConcept
    ? mappings.filter((m: any) => m.concept === currentConcept.id)
    : mappings;

  const openPins = pins.filter((p: any) => p.status === "open");
  const resolvedPins = pins.filter((p: any) => p.status !== "open").slice(-4);
  const archivedCount = branches.filter((b: any) => b.archived).length;

  return (
    <div className="rail">
      {/* -------- registry panel -------- */}
      <div className="rail-section">
        <h3>Registry {concepts.length > 0 && <span className="count">{concepts.length} concept{concepts.length > 1 ? "s" : ""}</span>}</h3>
        {!concepts.length && (
          <div className="map-status">No concepts yet — the registry fills in as the conversation draws.</div>
        )}
        {currentConcept && (
          <div className="registry-concept">
            <div className="cname">
              {currentConcept.name}
              {currentConcept.glossary && <span className="gloss">📖 {currentConcept.glossary}</span>}
              {currentConcept.unviewed && <span className="gloss">(unviewed)</span>}
            </div>
            {(currentConcept.views || []).map((v: string) => (
              <div key={v} className={`view-row ${v === currentArtifact ? "current" : ""}`}>
                <span>{artifacts[v]?.name || v}</span>
                <span className={`tag ${artifacts[v]?.tier === "first-class" ? "fc" : ""}`}>
                  {artifacts[v]?.artifact_type || "?"}
                </span>
              </div>
            ))}
            {conceptMappings.length > 0 ? (
              conceptMappings.map((m: any, i: number) => {
                const suppressed = (m.note || "").startsWith("intentionally-divergent");
                const hasTrip = tripwires.some((t: any) => t.mapping?.startsWith(`${m.concept}:`));
                return (
                  <div key={i} className="map-status">
                    {suppressed ? (
                      <span>⛭ mapped — <span className="div">intentionally divergent</span>: {(m.note || "").split(":").slice(1).join(":").trim() || "accepted"}</span>
                    ) : hasTrip ? (
                      <span>⚠ mapped — <span className="div">divergence flagged below</span></span>
                    ) : (
                      <span><span className="ok">✓</span> mapped — {mappingWords(m)}</span>
                    )}
                  </div>
                );
              })
            ) : (
              currentConcept.views?.length > 1 && <div className="map-status">views unmapped (inference only)</div>
            )}
          </div>
        )}
        {concepts.filter((c: any) => c !== currentConcept).map((c: any) => (
          <div key={c.id} className="registry-concept">
            <div className="cname" style={{ color: "var(--muted)", fontWeight: 500 }}>
              {c.name} <span className="gloss">{(c.views || []).length} view{(c.views || []).length === 1 ? "" : "s"}</span>
            </div>
          </div>
        ))}
        {/* tripwires in words */}
        {tripwires.map((t: any) => (
          <div key={t.id} className="tripline">
            <div className="tw-title">⚠ Mapping tripwire</div>
            <b>{t.changed.replace("#", " › ")}</b> changed at save {t.save}, but its
            mapped sibling <b>{t.sibling.replace("#", " › ")}</b> didn't move.
            Divergence, or should it propagate? The agent will ask — nothing
            syncs without your yes.
          </div>
        ))}
        {mappings
          .filter((m: any) => (m.note || "").startsWith("intentionally-divergent"))
          .map((m: any, i: number) => (
            <div key={`sup${i}`} className="tripline suppressed">
              <div className="tw-title">⛭ Intentionally divergent</div>
              {mappingWords(m)} — {(m.note || "").split(":").slice(1).join(":").trim() || "accepted divergence"}
            </div>
          ))}
      </div>

      {/* -------- pinned questions -------- */}
      <div className="rail-section">
        <h3>Pinned questions {openPins.length > 0 && <span className="count">{openPins.length} open</span>}</h3>
        {!openPins.length && !resolvedPins.length && (
          <div className="map-status">No open questions. Element-anchored questions land here — answer in place, or in chat.</div>
        )}
        {openPins.map((p: any) => (
          <div key={p.id} className="pin-card">
            <div className="q">❓ {p.question}</div>
            {p.element && <div className="anchor">anchored to {p.artifact} › {p.element}</div>}
            <div className="answer-row">
              <input
                placeholder="Answer here…"
                value={answers[p.id] || ""}
                disabled={viewingRevn != null}
                onChange={(e) => setAnswers({ ...answers, [p.id]: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (answers[p.id] || "").trim())
                    onAnswerPin(p.id, answers[p.id].trim());
                }}
              />
              <button
                disabled={!(answers[p.id] || "").trim() || viewingRevn != null}
                onClick={() => onAnswerPin(p.id, answers[p.id].trim())}
              >
                Answer
              </button>
            </div>
          </div>
        ))}
        {resolvedPins.map((p: any) => (
          <div key={p.id} className="pin-card resolved">
            <div className="q">{p.question}</div>
            <div className="a">↳ {p.answer || "(resolved on canvas)"}</div>
          </div>
        ))}
      </div>

      {/* -------- branches -------- */}
      <div className="rail-section">
        <h3>Branches</h3>
        <div className="branch-chips">
          {state?.checkout_revn != null && (
            <span className="branch-chip detached" title="viewing an old save — the first Save from here forks a branch">
              ⌖ detached @ {state.checkout_revn}
            </span>
          )}
          {branches
            .filter((b: any) => !b.archived)
            .map((b: any) => (
              <span
                key={b.name}
                className={`branch-chip ${b.name === state?.head && state?.checkout_revn == null ? "current" : ""}`}
                onClick={() => b.name !== state?.head && onSwitchBranch(b.name)}
                title={b.name === state?.head ? "current branch" : `switch to ${b.name} (head: revn ${b.head})`}
              >
                ⎇ {b.name}
                {b.name !== state?.head && (
                  <span
                    className="arch"
                    title="archive (hidden, never deleted)"
                    onClick={(e) => { e.stopPropagation(); onArchive(b.name, true); }}
                  >
                    🗃
                  </span>
                )}
              </span>
            ))}
        </div>
        {archivedCount > 0 && (
          <button className="show-archived" onClick={() => setShowArchivedChips(!showArchivedChips)}>
            {showArchivedChips ? "hide" : "show"} {archivedCount} archived
          </button>
        )}
        {showArchivedChips && (
          <div className="branch-chips" style={{ marginTop: 6 }}>
            {branches.filter((b: any) => b.archived).map((b: any) => (
              <span key={b.name} className="branch-chip" style={{ opacity: 0.65 }}
                onClick={() => onArchive(b.name, false)} title="click to unarchive">
                🗃 {b.name} ↩
              </span>
            ))}
          </div>
        )}
      </div>

      {/* -------- save-history timeline -------- */}
      <div className="rail-section" style={{ flex: 1 }}>
        <h3>Save history</h3>
        <div className="tl-toggle">
          <button className={!allBranches ? "active" : ""} onClick={() => setAllBranches(false)}>current branch</button>
          <button className={allBranches ? "active" : ""} onClick={() => setAllBranches(true)}>all branches</button>
        </div>
        <div className="timeline">
          {pending.map((p: any) => (
            <div key={`p${p.id}`} className="tl-entry pending-entry" title="a held agent revision — see the banner">
              <div className="dot-col"><span className="adot pending-ev" /></div>
              <div>
                <div className="gist">{p.pin_only ? "agent asked a question" : "agent revision waiting"}{p.note ? ` — ${p.note}` : ""}</div>
                <div className="meta">pending · {p.deferred ? "lands after your Save" : "apply from the banner"}</div>
              </div>
            </div>
          ))}
          {shownSaves.map((s: any) => (
            <div
              key={s.revn}
              className={`tl-entry ${viewingRevn === s.revn ? "viewed" : ""}`}
              onClick={() => onViewCommit(s.revn)}
              title={`view the project at save ${s.revn}`}
            >
              <div className="dot-col"><span className={`adot ${s.author}`} /></div>
              <div>
                <div className="gist">{s.headline || "(no summary)"}</div>
                <div className="meta">
                  <span className="short">{s.short_id}</span>
                  <span>#{s.revn}</span>
                  {forkPoints.has(s.revn) && <span className="fork-glyph" title="fork point">⑂</span>}
                  {allBranches && <span className="branch-tag">{s.branch}</span>}
                  {s.reconciliation && <span title="out-of-session reconciliation">↺</span>}
                  {s.tripwires > 0 && <span className="tw-badge">⚠ {s.tripwires}</span>}
                </div>
              </div>
            </div>
          ))}
          {!shownSaves.length && <div className="map-status">No saves yet — the first Save starts the history.</div>}
        </div>
      </div>
    </div>
  );
}
