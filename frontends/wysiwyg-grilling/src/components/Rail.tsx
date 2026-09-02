import React, { useMemo, useState } from "react";
import { TripwireCard, gotoRefOf } from "./QuestionUI";

/** Right rail — read-only state display, no navigation (spec §7): registry
 * panel, interactive tripwires and pins (answer in place, goto, detail
 * modal), branch chips, save timeline. */
export function Rail({
  state, currentArtifact, viewingRevn, onAnswerPin, onAnswerTripwire,
  onGoto, onOpenDetail, onSwitchBranch, onArchive, onViewCommit,
  onDismissPin, onLabelSave, onPromotePin, onDiscardProposal, inspector,
}: {
  state: any; currentArtifact: string | null; viewingRevn: number | null;
  onAnswerPin: (id: string, answer: string) => void;
  /**
   * Accept an agent's draft as the answer — blank `answer` takes it as
   * written. The agent has no route to this: it may only propose.
   */
  onPromotePin: (id: string, answer: string) => void;
  onDiscardProposal: (pin: { id: string }) => void;
  onAnswerTripwire: (id: string, answer: string) => void;
  onGoto: (ref: { aid: string; el: string }) => void;
  onOpenDetail: (kind: "pin" | "tripwire", data: any) => void;
  onSwitchBranch: (name: string) => void;
  onArchive: (name: string, archived: boolean) => void;
  onViewCommit: (revn: number) => void;
  onDismissPin?: (pin: any) => void;
  onLabelSave?: (revn: number, current: string) => void;
  inspector?: React.ReactNode;
}) {
  const [showArchivedChips, setShowArchivedChips] = useState(false);
  const [showPinArchive, setShowPinArchive] = useState(false);
  const [showTwArchive, setShowTwArchive] = useState(false);
  const [showVocab, setShowVocab] = useState(false);
  const [allBranches, setAllBranches] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // Which waiting drafts the user has opened for editing. Absent means
  // "accept as written"; present means the textarea below is showing and
  // its content is what acceptance will store.
  const [edits, setEdits] = useState<Record<string, string>>({});

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

  // Concepts split the way the artifact dropdown already splits them
  // (v0.8, assessment r4-13): a 12-round session mints ~20 settled
  // terms and ~7 views, and a flat list buried the seven real
  // artifacts under twenty-one "0 views" rows. View-bearing concepts
  // are navigation; term-only concepts are VOCABULARY, collapsed.
  const viewBearing = useMemo(
    () => concepts.filter((c: any) =>
      c !== currentConcept && (c.views || []).length > 0),
    [concepts, currentConcept]);
  const vocabOnly = useMemo(
    () => concepts.filter((c: any) =>
      c !== currentConcept && !(c.views || []).length),
    [concepts, currentConcept]);

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

  // layout lint for the current artifact (v0.4): server-computed lines
  // (incl. cross-artifact findings), rendered read-only — waives are
  // agent ops that ride the next batch
  const lintRows = useMemo(() => {
    const li = (state?.lint || {})[currentArtifact || ""];
    if (!li) return [] as { tier: string; msg: string; el?: string }[];
    const ids = new Set(
      ((artifacts[currentArtifact || ""] || {}).elements || [])
        .map((e: any) => e.id));
    const refOf = (msg: string): string | undefined => {
      for (const tok of msg.match(/[A-Za-z0-9][\w-]*/g) || [])
        if (ids.has(tok)) return tok;
      return undefined;
    };
    const rows: { tier: string; msg: string; el?: string }[] = [];
    for (const [tier, msgs] of [["error", li.errors],
      ["warning", li.warnings], ["note", li.notes]] as const)
      for (const msg of msgs || [])
        rows.push({ tier, msg, el: refOf(msg) });
    return rows;
  }, [state?.lint, artifacts, currentArtifact]);
  const [showLintNotes, setShowLintNotes] = useState(false);
  const lintHard = lintRows.filter((r) => r.tier !== "note");
  const lintNotes = lintRows.filter((r) => r.tier === "note");

  const openPins = pins.filter((p: any) => p.status === "open");
  // pin_debt carries age_rounds + target_edits — always on the wire,
  // never rendered until v0.8: the agent read "age 5r" while the user's
  // modal said only "asked at round 1" and had to subtract (r4/B1)
  const debtById = useMemo(() => {
    const m: Record<string, any> = {};
    for (const d of state?.pin_debt || []) m[d.id] = d;
    return m;
  }, [state?.pin_debt]);
  const resolvedPins = pins.filter((p: any) => p.status !== "open").slice().reverse();
  const resolvedTripwires = (state?.tripwires || [])
    .filter((t: any) => t.status !== "open").slice().reverse();
  const suppressedMappings = mappings.filter((m: any) =>
    (m.note || "").startsWith("intentionally-divergent"));
  const twArchiveCount = resolvedTripwires.length +
    new Set(suppressedMappings.map((m: any) => m.note)).size;
  const archivedCount = branches.filter((b: any) => b.archived).length;

  return (
    <div className="rail">
      {/* -------- element inspector (v0.3, only while selected) -------- */}
      {inspector}
      {/* -------- registry panel -------- */}
      <div className="rail-section">
        <h3>Registry {concepts.length > 0 && <span className="count">
          {concepts.length - vocabOnly.length} concept{concepts.length - vocabOnly.length === 1 ? "" : "s"}
          {vocabOnly.length > 0 && <> · {vocabOnly.length} term{vocabOnly.length === 1 ? "" : "s"}</>}
        </span>}</h3>
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
            {(currentConcept.owed || []).map((t: string) => (
              <div key={`owed-${t}`} className="view-row owed"
                title="view debt — a view this concept's archetype still owes; drawn only when a question needs it">
                <span>owed: not drawn yet</span>
                <span className="tag owed">{t}</span>
              </div>
            ))}
            {conceptMappings.length > 0 ? (() => {
              // one class-level ruling can cover many mappings — collapse
              // identical divergence notes into a single counted row instead
              // of drowning the concept in near-identical lines
              const live = conceptMappings.filter((m: any) => !(m.note || "").startsWith("intentionally-divergent"));
              const byNote: Record<string, number> = {};
              for (const m of conceptMappings)
                if ((m.note || "").startsWith("intentionally-divergent"))
                  byNote[m.note] = (byNote[m.note] || 0) + 1;
              return (
                <>
                  {live.map((m: any, i: number) => {
                    // WHOLE key, not the concept prefix (r5b-10). A
                    // tripwire's `mapping` is `_mapping_key` — concept,
                    // colon, elements joined by `+` — so matching on
                    // `${concept}:` flagged every mapping the concept
                    // had the moment any one of them diverged: fifteen
                    // identical warning rows about fourteen untouched
                    // pairs, burying the concept list under a claim that
                    // was false of all but one of them. `elements` is
                    // immutable after `add_mapping` (only `note` and
                    // `kinds` can be annotated), so the key is stable to
                    // match on.
                    const key = `${m.concept}:${(m.elements || []).join("+")}`;
                    const hasTrip = tripwires.some((t: any) => t.mapping === key);
                    return (
                      <div key={i} className="map-status">
                        {hasTrip ? (
                          // says WHICH pair, like its quiet neighbours:
                          // a warning that names nothing sends the
                          // reader to the tripwire panel to match ids by
                          // hand, and reads the same as every other
                          // warning on the way
                          <span>⚠ mapped — {mappingWords(m)} — <span className="div">divergence flagged below</span></span>
                        ) : (
                          <span><span className="ok">✓</span> mapped — {mappingWords(m)}</span>
                        )}
                      </div>
                    );
                  })}
                  {Object.entries(byNote).map(([note, n], i) => (
                    <div key={`sg${i}`} className="map-status">
                      <span>⛭ {n > 1 ? `${n} mappings` : "mapped"} — <span className="div">intentionally divergent</span>: {note.split(":").slice(1).join(":").trim() || "accepted"}</span>
                    </div>
                  ))}
                </>
              );
            })() : (
              currentConcept.views?.length > 1 && <div className="map-status">views unmapped (inference only)</div>
            )}
          </div>
        )}
        {viewBearing.map((c: any) => (
          <div key={c.id} className="registry-concept">
            <div className="cname" style={{ color: "var(--muted)", fontWeight: 500 }}>
              {c.name} <span className="gloss">{(c.views || []).length} view{(c.views || []).length === 1 ? "" : "s"}</span>
            </div>
          </div>
        ))}
        {vocabOnly.length > 0 && (
          <button className="show-archived" onClick={() => setShowVocab(!showVocab)}
            title="settled glossary terms with no view yet — vocabulary, not navigation">
            {showVocab ? "hide" : "show"} vocabulary ({vocabOnly.length})
          </button>
        )}
        {showVocab && vocabOnly.map((c: any) => (
          <div key={c.id} className="registry-concept">
            <div className="cname" style={{ color: "var(--faint)", fontWeight: 400 }}>
              📖 {c.name}
            </div>
          </div>
        ))}
        {/* open tripwires — answerable in place, like pins */}
        {tripwires.map((t: any) => (
          <TripwireCard
            key={t.id}
            t={t}
            disabled={viewingRevn != null}
            onAnswer={(a) => onAnswerTripwire(t.id, a)}
            onGoto={gotoRefOf(t, "tripwire")
              ? () => onGoto(gotoRefOf(t, "tripwire")!)
              : null}
            onOpen={() => onOpenDetail("tripwire", t)}
          />
        ))}
        {/* archive: resolved tripwires + settled divergence rulings, out of
            live space (expands inline — never an absolutely-positioned popup
            inside this scroll container) */}
        {twArchiveCount > 0 && (
          <button className="show-archived" onClick={() => setShowTwArchive(!showTwArchive)}>
            {showTwArchive ? "hide" : "show"} archive ({twArchiveCount})
          </button>
        )}
        {showTwArchive && resolvedTripwires.map((t: any) => (
          <div key={t.id} className="tripline suppressed">
            <div className="tw-title">✓ Tripwire resolved</div>
            <b>{(t.changed || "").replace("#", " › ")}</b> vs{" "}
            <b>{(t.sibling || "").replace("#", " › ")}</b> — raised at save {t.save}
            {t.resolved_by != null ? `, settled at save ${t.resolved_by}` : ", settled by ruling"}.
          </div>
        ))}
        {showTwArchive && (() => {
          const byNote: Record<string, any[]> = {};
          for (const m of suppressedMappings) (byNote[m.note] = byNote[m.note] || []).push(m);
          return Object.entries(byNote).map(([note, ms], i) => (
            <div key={`sup${i}`} className="tripline suppressed">
              <div className="tw-title">⛭ Intentionally divergent{ms.length > 1 ? ` — ${ms.length} mappings` : ""}</div>
              {ms.length > 1
                ? (note.split(":").slice(1).join(":").trim() || "accepted divergence")
                : <>{mappingWords(ms[0])} — {note.split(":").slice(1).join(":").trim() || "accepted divergence"}</>}
            </div>
          ));
        })()}
      </div>

      {/* -------- layout lint (v0.4) -------- */}
      {lintRows.length > 0 && currentArtifact && (
        <div className="rail-section">
          <h3>Layout <span className="count">{lintRows.length}</span></h3>
          {lintHard.map((r, i) => (
            <div key={`lh${i}`} className={`tripline lint-row ${r.tier}`}>
              <span className="lint-tier">{r.tier === "error" ? "▲" : "⚠"}</span>
              {" "}{r.msg}
              {r.el && (
                <button className="linkish" title="show on canvas"
                  onClick={() => onGoto({ aid: currentArtifact, el: r.el! })}>⌖</button>
              )}
            </div>
          ))}
          {lintNotes.length > 0 && (
            <button className="show-archived"
              onClick={() => setShowLintNotes(!showLintNotes)}>
              {showLintNotes ? "hide" : "show"} {lintNotes.length} note{lintNotes.length > 1 ? "s" : ""}
            </button>
          )}
          {showLintNotes && lintNotes.map((r, i) => (
            <div key={`ln${i}`} className="tripline lint-row note">
              <span className="lint-tier">·</span>
              {" "}{r.msg}
              {r.el && (
                <button className="linkish" title="show on canvas"
                  onClick={() => onGoto({ aid: currentArtifact, el: r.el! })}>⌖</button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* -------- pinned questions -------- */}
      <div className="rail-section">
        <h3>Pinned questions {openPins.length > 0 && <span className="count">{openPins.length} open</span>}</h3>
        {!openPins.length && (
          <div className="map-status">No open questions. Element-anchored questions land here — answer in place, or in chat.</div>
        )}
        {openPins.map((p: any) => (
          <div key={p.id} className="pin-card"
            onClick={(e) => {
              if ((e.target as HTMLElement).closest("button,input")) return;
              onOpenDetail("pin", p);
            }}
            title="click for the full story"
          >
            <div className="q">❓ {p.question}
              <span className={`who-chip ${p.direction === "user" ? "user" : "agent"}`}>
                {p.direction === "user" ? "yours" : "agent"}
              </span>
            </div>
            {p.element && <div className="anchor">anchored to {p.artifact} › {p.element}</div>}
            {debtById[p.id] && (debtById[p.id].age_rounds > 0 ||
              debtById[p.id].target_edits > 0) && (
              <div className="anchor">
                open {debtById[p.id].age_rounds} round{debtById[p.id].age_rounds === 1 ? "" : "s"}
                {debtById[p.id].target_edits > 0 &&
                  <> · target edited {debtById[p.id].target_edits}×</>}
              </div>
            )}
            {/* A draft the agent wrote, waiting on the user. It is NOT
                an answer and the card must not read like one: the agent
                cannot answer a pin, so nothing here is testimony until
                this person accepts it. Shown above the answer box so
                accepting and answering are visibly two different acts. */}
            {p.proposed_answer && (
              <div className="proposal">
                <div className="proposal-head">
                  <span className="who-chip agent">agent draft</span>
                  the agent wrote this for you — it is not your answer
                  until you accept it
                </div>
                {p.id in edits ? (
                  <textarea
                    className="proposal-edit"
                    autoFocus
                    value={edits[p.id]}
                    disabled={viewingRevn != null}
                    onChange={(e) => setEdits({ ...edits, [p.id]: e.target.value })}
                  />
                ) : (
                  <div className="proposal-text">“{p.proposed_answer}”</div>
                )}
                <div className="proposal-row">
                  <button
                    disabled={viewingRevn != null ||
                      (p.id in edits && !edits[p.id].trim())}
                    onClick={() => onPromotePin(p.id, p.id in edits
                      ? edits[p.id].trim() : "")}>
                    {p.id in edits ? "Accept my wording" : "Accept as written"}
                  </button>
                  {!(p.id in edits) && (
                    <button className="linkish"
                      disabled={viewingRevn != null}
                      onClick={() => setEdits({ ...edits, [p.id]: p.proposed_answer })}>
                      ✎ edit first
                    </button>
                  )}
                  {p.id in edits && (
                    <button className="linkish"
                      onClick={() => {
                        const next = { ...edits };
                        delete next[p.id];
                        setEdits(next);
                      }}>
                      cancel edit
                    </button>
                  )}
                  <button className="linkish dismiss"
                    disabled={viewingRevn != null}
                    title="throw the draft away — the question stays open"
                    onClick={() => onDiscardProposal(p)}>
                    ✕ discard draft
                  </button>
                </div>
              </div>
            )}
            <div className="answer-row">
              <input
                placeholder={p.proposed_answer
                  ? "…or answer in your own words…" : "Answer here…"}
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
            <div className="card-foot">
              {gotoRefOf(p, "pin") && (
                <button className="linkish"
                  onClick={() => onGoto(gotoRefOf(p, "pin")!)}
                  title="reveal this element on the canvas">
                  ⌖ show on canvas
                </button>
              )}
              <button className="linkish"
                onClick={() => onOpenDetail("pin", p)}>ⓘ details</button>
              {onDismissPin && (
                <button className="linkish dismiss"
                  disabled={viewingRevn != null}
                  title="delete the ❓ — reads as “not worth explaining”, never re-raised"
                  onClick={() => onDismissPin(p)}>✕ dismiss</button>
              )}
            </div>
          </div>
        ))}
        {resolvedPins.length > 0 && (
          <button className="show-archived" onClick={() => setShowPinArchive(!showPinArchive)}>
            {showPinArchive ? "hide" : "show"} archive ({resolvedPins.length})
          </button>
        )}
        {/* THE ARCHIVE CARD CARRIES THE PROVENANCE, and until v0.9 it
            carried none: the `yours`/`agent` chip renders inside
            `openPins.map` above, so the moment a pin was answered it
            moved here and its authorship mark vanished — authorship
            rendered on the half that is almost always the agent, and
            absent on the half that varies. `answer_source` is what says
            it, and an accepted draft the user rewrote says both halves:
            the ruling is theirs, the phrasing was the agent's. */}
        {showPinArchive && resolvedPins.map((p: any) => (
          <div key={p.id} className="pin-card resolved">
            <div className="q">{p.question}</div>
            <div className="a">↳ {p.answer || (
              p.status === "dismissed" ? "(dismissed — not worth explaining)" :
              p.status === "pruned" ? "(elements deleted)" : "(resolved on canvas)"
            )}</div>
            {/* GATED ON `answer_source` AND NOT ON `p.answer`, because a
                record written before this field existed cannot say whose
                words it holds — and back then the agent could answer a
                pin outright. Rendering "you wrote this answer yourself"
                over one of those would be a positive claim about a fact
                nobody recorded, which is the same defect one direction
                over. Silence for the unknowable, a mark for everything
                written since. */}
            {p.answer && p.answer_source && (
              <div className="answer-source">
                <span className={`who-chip ${p.answer_source === "agent-proposal" ? "agent" : "user"}`}>
                  {p.answer_source === "agent-proposal" ? "agent draft" : "your words"}
                </span>
                {p.answer_source === "agent-proposal"
                  ? (p.answer !== p.proposed_answer
                    ? <>you accepted the agent's draft after rewriting it — it wrote
                      “{p.proposed_answer}”</>
                    : <>you accepted the agent's draft as written</>)
                  : (p.proposed_answer
                    ? <>you answered in your own words; the agent had offered
                      “{p.proposed_answer}”</>
                    : <>you wrote this answer yourself</>)}
              </div>
            )}
            {p.element && <div className="anchor">was anchored to {p.artifact} › {p.element}</div>}
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
              <div className="tl-body">
                <div className="gist">
                  {s.headline || "(no summary)"}
                  {s.label && <span className="save-label">🔖 {s.label}</span>}
                </div>
                <div className="meta">
                  <span className="short">{s.short_id}</span>
                  <span>#{s.revn}</span>
                  {forkPoints.has(s.revn) && <span className="fork-glyph" title="fork point">⑂</span>}
                  {allBranches && <span className="branch-tag">{s.branch}</span>}
                  {s.reconciliation && <span title="out-of-session reconciliation">↺</span>}
                  {s.tripwires > 0 && <span className="tw-badge">⚠ {s.tripwires}</span>}
                </div>
              </div>
              {onLabelSave && (
                <button
                  className="bookmark-btn"
                  title={s.label ? `bookmarked “${s.label}” — click to rename or clear` : "bookmark this save with a short name"}
                  onClick={(e) => { e.stopPropagation(); onLabelSave(s.revn, s.label || ""); }}
                >🔖</button>
              )}
            </div>
          ))}
          {!shownSaves.length && <div className="map-status">No saves yet — the first Save starts the history.</div>}
        </div>
      </div>
    </div>
  );
}
