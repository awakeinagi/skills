# WYSIWYG Grilling — Specification

**Status: LOCKED** (user-approved 2026-08-07; amended same day at user
direction: Appendix A feature-parity checklist) · Assembled from the
[wayfinder map](.scratch/wysiwyg-grilling/map.md) (17 tickets, all resolved).
Glossary: [CONTEXT.md](CONTEXT.md) — its terms are used capitalized throughout.
ADRs: [docs/adr/](docs/adr/). A fresh session handed this document should be able
to build milestone 1 with no open design questions.

---

## 1. What this is

A Claude Code skill + bundled local web app that extends grilling-with-docs:
during design conversations the agent externalizes its understanding as
**visual artifacts** (wireframes, flowcharts, diagrams) on an editable canvas,
the user edits them directly, and the agent narrates its reading of every
change — closing the alignment loop faster than prose alone.

The core cycle is the **Refinement Loop**: the agent revises the Canvas and asks
for feedback → the user edits and **Saves** (an explicit button; edits are
invisible until Save) → the agent narrates its understanding, hypothesizing
reasoning and implications → the user corrects → repeat. Validated live end-to-end
by the [feel prototype](.scratch/wysiwyg-grilling/issues/12-refinement-loop-feel-prototype.md)
(verdict: positive).

**Runs while developing any target project.** All outputs land in that project's
`project_knowledge/` directory (§4). The skill is **shippable to strangers from
day one** — installation, portability, and zero-assumption setup are requirements
(§10, §11).

### Out of scope (map rulings — not deferred, ruled out)

- **Multi-user / realtime collaboration** — single user + one agent.
- **High-fidelity design tooling** — artifacts stay low-fi sketches whose job is
  alignment, not design deliverables.
- **Code generation implementation** — named future direction only (§13).
- **Second agent-runtime adapters** — boundary documented (§6.6), none built.

---

## 2. Domain model

Full definitions in [CONTEXT.md](CONTEXT.md); the load-bearing structure:

- **Concepts** are identity anchors in the per-project **Registry**
  (`model.json`). They hold no drawing content; their **Views** (artifacts) do.
  A solo artifact is a concept with one view.
- **Mappings** connect views of the same concept and are **tripwires, never
  syncs**: checked at Save, divergence is named in narration with an offer to
  propagate; nothing changes in a view the user didn't edit without an explicit
  yes; accepted divergence is annotated `intentionally-divergent`.
- Mapping granularity is **two-tier**: concept membership is free and coarse;
  element-level links are added selectively where load-bearing, referencing
  stable **semantic slug ids** (`login-form`, never a nanoid). Unlinked elements
  are still checked by LLM inference at narration time; the agent proposes
  promoting an inferred link when it fires repeatedly.
- The Registry is **co-authored**: the agent maintains it via narrated,
  veto-able revisions (lint rule: no silent registry edits — every `model.json`
  change appears in that round's narration); user edits to it are interpreted
  and narrated like canvas Saves.
- **Deletion is honored + tidied + conversed**: view leaves the concept, element
  links tombstoned with labels into the save record, concept survives unviewed.
  The conversation default and its two-layer opt-out are in §9.4.
- **View suggestions are question-driven and seeded**: proposed only when the
  conversation hits a question current views can't make tangible AND the agent
  has material to seed a real first draft. Max one per round; declines recorded
  and not re-proposed without new cause.

---

## 3. Artifact vocabulary

Type depth is **config-driven per project** (§4.2). Shipped defaults:

| Type | Tier | The question it answers |
|---|---|---|
| Wireframe | first-class | "Is THIS the screen you mean?" |
| Flow | first-class | "Is THIS the order things happen in?" |
| Domain diagram | first-class | "Are THESE the concepts, related THIS way?" |
| sequence, ER, class, swimlane, DFD, mind map, architecture | extended | drawable on demand |

- **First-class** = typed edit semantics, semantic narration facts (§5.3),
  mapping rules. **Extended** = generic add/del/rename/move narration,
  inference-only mappings. Users reorder/demote/disable via config; *promotion*
  to first-class is bounded by shipped semantics (a skill version matter).
- **Wireframe ceiling: low-fi + annotations.** Gray/black/white, X-box images,
  wavy text lines — but **real labels on nav/buttons** (labels are load-bearing
  for mappings and narration). Mid-fi realism deferred; a state variant is a
  second wireframe view of the same screen.
- **Flow uses Excalidraw arrow `startBinding`/`endBinding`** — the `rewired`
  narration fact (§5.3) depends on it. Flow is the **hub type**: default-mapped
  pairs are **wireframe ↔ flow** (screen↔step, button↔transition) and
  **domain ↔ flow** (entity↔steps acting on it), element links created eagerly
  as the agent draws these pairs. Domain↔wireframe stays inference-only.
- **Annotations are universal** across first-class views: interaction notes,
  priority numbers, sticky comments — user- or agent-authored, ambient in the
  artifact (never a separate mode), the reply channel of the loop. Annotations
  MUST stay machine-readable elements (`customData` roles), never flattened
  into pixels — the codegen non-preclusion clause.
- **Complexity budget: 8–12 nodes per artifact** (three converging sources).
  Exceeding it is a signal to split into another view, not to shrink the font.
- **Typography: no cursive anywhere.** Sketchy *shapes* carry the low-fi signal;
  canvas text defaults to Excalidraw's legible font family (hand-drawn face
  ships but is non-default). ADR 0001.
- **Draw gate** (choreography side: §9.2): draw only when structure,
  relationship, or flow carries the point better than words.

---

## 4. project_knowledge/ — layout, formats, durability

### 4.1 Layout (shallow per-kind, lazily created)

```
project_knowledge/
├── CONTEXT.md          # glossary — canonical; format inherited verbatim
├── model.json          # Registry (§4.3)
├── config.json         # behavior knobs (§4.2)
├── artifacts/          # normalized .excalidraw files, one per view
├── saves/              # save records, NNNN-<slug>.json — kept forever
├── specs/              # free-form markdown
├── adr/                # ADR format inherited verbatim (+ may cite save short-ids)
└── .backups/           # pre-migration snapshots (gitignored)
```

Concept grouping lives in the Registry only — **filesystem paths never carry
identities** that can rename or merge. Git guidance: commit everything by
default; `.backups/` gitignored; `saves/` the user's call.

**Artifact format: normalized native `.excalidraw`.** The file the user edits is
the file of record — no neutral schema, no sidecar. Every write passes one
normalizer: sorted keys; volatile attrs (`version`, `seed`, `versionNonce`,
`updated`) stripped/pinned; geometry rounded to 1px at rest; agent-minted
semantic slug ids (`regenerateIds: false`); annotations/roles + an
intent/meaning field in `customData`; a single `make_element()` construction
funnel. Semantics live in the Registry, pointing at elements by id.

**Doc formats inherited verbatim** from the domain-modeling skill
(CONTEXT-FORMAT, ADR-FORMAT), plus: registry glossary links reference terms by
name; ADRs may cite save records by short-id. **Coexistence:** one-time
migration offer on first run in a project with root `CONTEXT.md`/`docs/adr`
(move + pointer files); declining = read-both-write-one, stated once.

### 4.2 config.json

```jsonc
{ "migrations": [],
  "artifact_types": {
    "wireframe": {"tier": "first-class", "priority": 1},
    "flow":      {"tier": "first-class", "priority": 2},
    "domain":    {"tier": "first-class", "priority": 3},
    "sequence":  {"tier": "extended",    "priority": 10}
    // …remaining extended types; "disabled": true removes from play
  },
  "narration_altitude": "clusters",   // clusters | exhaustive | headline
  "canvas_updates": "per-round",      // per-round | pulled
  "deletion_conversation": true,
  "nudge_after_minutes": 10 }
```

### 4.3 model.json (Registry)

```jsonc
{ "migrations": [], "revn": 12,
  "head": "main",
  "branches": [ {"name": "main", "head": 12, "archived": false} ],
  "round": 9, "whose_move": "user",          // session-resume fields
  "concepts": [
    {"id": "checkout", "name": "Checkout",
     "views": ["checkout-wireframe", "checkout-flow"],
     "glossary": "Checkout", "unviewed": false} ],
  "mappings": [
    {"concept": "checkout",
     "elements": ["checkout-wireframe#pay-block", "checkout-flow#pay-step"],
     "note": null} ],                        // or "intentionally-divergent: …"
  "declined": [
    {"concept": "checkout", "view_type": "domain",
     "kind": "suggestion",                   // suggestion | prune
     "reason": null, "when": "2026-08-07"} ] }
```

### 4.4 saves/NNNN-\<slug\>.json (save record)

Global project revn; one record per Save covering all changed artifacts; the
same record shape for agent revisions (symmetric authorship).

```jsonc
{ "migrations": [], "revn": 7, "base_revn": 6,   // base_revn IS the DAG parent pointer
  "branch": "main",
  "author": "user",                              // user | agent | out-of-session
  "saved_at": "…", "short_id": "17bc430",
  "selection_at_save": ["checkout-wireframe#pay-block"],
  "user_note": null,
  "artifacts": {
    "checkout-wireframe": {
      "changes": [ /* add/del/mod/move/reorder; mod ops carry {attr,from,to} */ ],
      "inverse": [ /* pre-built, reverse order */ ],
      "by_element": [ {"id": "…", "kind": "…", "label": "…", "verb": "…",
                       "attrs_changed": ["…"], "ops": [], "semantic": {},
                       "consequence_of": null} ] } },
  "summary": {"verb_counts": {}, "headline": "…", "suppressed": 0},
  "tripwires": [ {"mapping": "…", "changed": "…", "sibling": "…", "kind": "…"} ] }
```

### 4.5 Durability package (all in v1, ~120 lines stdlib)

Named-migration sets on every JSON file; snapshot to `.backups/` before
migrating; validate+repair tables (error codes with hints, code→repair
dispatch, logging no-op default); optimistic `base_revn` check on Save
(stale-tab protection); deterministic ids under a test flag ("no semantic
change → no git diff" assertable in CI); saves kept forever.

---

## 5. Save-narration contract

### 5.1 Pipeline (ratified)

**diff → mechanical summarize → semanticize → narrate.** The server diffs old
vs new scene (bucketed add/del/mod/move/reorder; config-listed significant
attrs only; 1px rounding; deletions inline the tombstone label), mechanically
summarizes (verb counts, salience pick, `consequence_of` suppression,
selection-at-save), derives typed semantic facts + registry tripwire hits, and
the LLM narrates from that structure. The canvas screenshot is visual context
only, **never** source of truth.

Differ requirements confirmed by the live feel test:

- Suppress Excalidraw's **rebind sentinel coordinates** (±2^56 on arrows
  mid-rebind) — the REWIRED fact is the signal, the coordinate churn is noise.
- **Consequence suppression**: one structural insertion producing N layout
  moves must collapse to the insertion + "layout adjusted".
- Fact types the first differ missed, now required: **element TYPE change**
  (e.g. rectangle→diamond) and **LABEL-ADDED** to a previously unlabeled
  element (both directions of label lifecycle, not just renames).

### 5.2 Narration rules

- **Altitude**: default = 2–4 interpreted intent clusters + named suppressed
  items ("and 3 nudges I read as incidental"); full ledger in the save record,
  quotable on demand. Configurable (`narration_altitude`).
- **Hypotheses**: deliberate internally (≥2 candidate readings scored against
  registry, glossary, conversation, selection-at-save), **commit externally**
  to one reading stated as a position, with the point of uncertainty named and
  a next move offered. Near-ties surface as an explicit fork question.
- **Doc timing**: mechanics immediate (save records, registry tidy-ups);
  meaning one round later (glossary/spec/ADR updates ride the agent's next
  revision, narrated and veto-able); session-end flush (§9.5).
- **Unreadable edits**: pinned question + one honest narration line — never
  guessed, never dropped, never blocking. Pin lifecycle: answered in chat →
  normal round; elements edited → next diff reads them, pin removed; pin
  deleted → "not worth explaining", untracked drawing, never re-raised;
  elements deleted → prune rules.
- **Batching** (from §9.3): multiple Saves per user move stay separate commits
  but narrate as one cumulative diff, with per-save attribution only where the
  sequence itself is signal ("the wobble across 0014–0015").
- **Exemplars from the live run** (encode in SKILL.md): committed reading +
  named uncertainty; a legible deletion gets a one-line acknowledgment, not an
  interrogation; a rename phrased as an instruction ("we should rename…") is
  executed immediately, not re-asked; semantic ids stay stable under label
  changes (ids are identity anchors — that split is what makes RENAMED
  detectable).

### 5.3 Semantic-fact tables

**Universal (all types):** `added`, `deleted{was: kind,label}` (tombstone),
`renamed{from,to}`, `label_added{text}`, `type_changed{from,to}`,
`moved{dx,dy,spatial}` (pre-translated to regions/relations), `resized`,
`annotated{text,target}` / `annotation_deleted`, `reordered`, `restyled`
(low-signal, config-filtered by default), `consequence_of: <id>`, and the
explicit empty save → "you saved without changing anything."

**Wireframe:** `screen_added/deleted` (frame-level); `block_moved_within_screen`
vs `regrouped{from_screen,to_screen}` (different sentences); `label_renamed` on
nav/buttons (fires wireframe↔flow tripwires); `priority_changed{from,to}`.

**Flow:** `step_added/deleted`; `transition_added/deleted{between}`;
**`rewired{arrow, from: A→B, to: A→C}`** via binding change — the
highest-signal fact in the product; `branch_added`; `sequence_reordered`;
`step_orphaned` (lint-style observation).

**Domain:** `entity_added/deleted`; `relationship_added/deleted/rewired`;
`entity_renamed{from,to}` → **glossary-challenge trigger** (§9.4);
`relationship_relabeled`; `possible_merge`.

**Extended tier:** universal facts only; mappings by inference only.

---

## 6. Agent ↔ app protocol

### 6.1 Helper surface

One stdlib-only `scripts/canvas.py {start, status, wait, stop, apply, screenshot}`,
invoked as `python3 ${CLAUDE_SKILL_DIR}/scripts/canvas.py …` (canonically under
`uv run` — ADR 0001) with a matching `allowed-tools: Bash(...)` rule. `start`
spawns a **detached** server child on `127.0.0.1:0` (ephemeral, collision-free)
and prints `KEY=VALUE` lines. `status` stamps the **protocol version**;
bundle/server check compatibility at startup; mismatches surface as
LLM-addressed errors ("tell the user to update the skill / restart the server").

### 6.2 Write path

`canvas.py apply` takes `{base_revn, ops: [add/mod/del/reorder]}` — the op
vocabulary mirrors the save-record change vocabulary (one grammar, both
directions). The server validates the **whole batch before applying anything**,
fills boring fields via `make_element()`, normalizes, bumps revn, writes one
atomic file update, and writes a save record for the agent's revision
(symmetric authorship). Direct file edits (any tool) are tolerated: the watcher
validates + repairs whatever appears. **Op-schema teaching layer**: a short
primer rides SKILL.md; the full reference is fetched on demand.

### 6.3 Waiting (never block)

Tier 1: `Monitor` on the one-line-per-Save events log (`persistent: true`).
Tier 2: `AskUserQuestion` (no timeout, works everywhere). Tier 3: bounded
long-poll that self-terminates strictly below Bash's 600s ceiling (an overrun
is backgrounded, not killed — a blocking wait fails silently). **Waiting is
working time**: doc queue, next-round prep, registry hygiene — but never a
second canvas revision (one unreviewed revision out at a time). Silence earns
exactly ONE nudge (config: `nudge_after_minutes`), then indefinite patience —
**silence is never consent**. Chat is always a legitimate reply channel.

### 6.4 Conflicts

No locks, ever. Agent revisions auto-apply only to a clean canvas; with unsaved
edits present they hold behind the **pending-revision banner** ("apply now /
after I save" — apply-now is an op replay). Stale tabs are caught by the
`base_revn` check → gentle refresh prompt. **Unsaved user work is inviolable —
no path destroys it.** Pulled-updates mode = agent revisions ALWAYS land behind
the banner control; one mechanism serves both cadences.

### 6.5 Session lifecycle

Start: `status` → reuse a healthy lingering server else start fresh → load
registry/config + validation pass → diff artifacts against last-save-record
state → out-of-session changes narrate through the normal pipeline as
**Catch-up Narration** ("since we last spoke…"), with a reconciliation save
record (`author: out-of-session`) re-anchoring revn. Git reverts are the same
case ("the canvas rolled back past 0007 — intentional?"). Shutdown: SessionEnd
hook (Claude Code adapter) + server-side idle watchdog (a detached process must
die on its own).

### 6.6 Adapter boundary (scope ruling — core is hook-free)

Everything under `scripts/` (server, canvas.py, file protocol, save-events log)
is **runtime-agnostic**; SKILL.md + hooks + Monitor are the *Claude Code
adapter*. **The core may never require hooks or push mechanisms.** Degradation
path lives in the web app: when the runtime can't be signaled, the UI shows a
**copyable context update** after Saves/branch switches — a mechanical pointer
(save short-id, branch, artifact, "please review") the user pastes into chat;
the agent reads the document from disk and narrates normally. The idle watchdog
covers runtimes with no session-end signal. No second adapter is designed.

---

## 7. The web app — Mission Control

The canvas IS Excalidraw (survey-verified offline bundle; zero remote
requests). Chrome (validated by both prototypes; the feel test confirmed the
chrome is part of the feel — "the rail is the state machine made visible"):

- **Round header** (dark strip): "Round N — your move / agent reading",
  project + current-artifact name (plain text), agent status chip, cadence
  toggle, **Save** button with dirty dot.
- **Right rail** (read-only state display — no navigation): Registry panel
  (current concept's views, mapping status, glossary link, tripwires spelled
  out in words) · **Pinned questions — interactive**: open pins render with an
  inline answer box; answers append to the save-events log as first-class user
  events that wake the agent exactly like a Save · Branch chips (switch /
  archive 🗃 / unarchive; archived hidden not deleted) · **Save-history
  timeline**: short-id + one-line gist + author-coded dots, current-branch
  lineage by default, "all branches" toggle, ⑂ fork glyphs; click → view mode.
- **History graph panel** (between canvas and filmstrip, collapsible,
  h-scrollable): horizontal git-graph — main on center lane, branches on
  alternating lanes, bezier fork edges, commit dots with revn captions,
  callout head-labels (click node = view commit; click label = switch branch);
  halo = current head; amber ring = viewed commit; dashed ghost = unsaved fork.
  Graph for navigation, timeline list for reading.
- **Filmstrip** (bottom — the single navigation surface): all-artifacts
  dropdown grouped by concept (leftmost) + current concept's view thumbs
  (dirty/tripwire badge dots) + dashed "+ suggest a view…" (opens the
  question-driven suggestion flow).
- **Banners**: pending-revision (apply now / after I save); stale-tab refresh
  prompt (tripwire coloring); **pin-only / no-op revisions get distinct
  styling** ("agent asked a question" — no Apply) — feel-test finding.
- **View mode**: clicking a commit renders all artifacts at that save,
  read-only via Excalidraw `viewModeEnabled`, persistent bar ("nothing is
  lost"), edits/Save/pins/reruns gated with a gentle toast; **unsaved edits
  block time-travel** (inviolable).
- **Editability contract**: everything on canvas is natively editable in live
  mode — every element, either author (correcting the agent's drawing IS the
  input channel). Gating is by **mode and surface only**: chrome is app UI,
  not canvas; view mode is read-only; a checkout is fully editable. Tier
  affects narration semantics, never editability.
- Annotation creation uses native Excalidraw text/sticky tools with
  server-side kind-detection (no custom tool in v1). "Agent is revising" =
  status chip + in-progress timeline event; canvas untouched until the atomic
  apply. Deferred: pending-revision diff-preview peek (v1 ships
  apply-now/after-I-save only).

---

## 8. History model — git-shaped, append-only

The save history is a **commit DAG**: `base_revn` is the parent pointer;
commits are never altered. **Checkout** (clicking a commit, then choosing to
work there) records nothing; the first Save on top of a checkout **forks a
branch** (parent = checkout point; name auto-suggested `alt-NNNN`,
user-editable at fork time). Branch metadata is minimal, in the Registry:
`branches: [{name, head, archived}]` + `head`, plus a `branch` tag per save
record. Switching branches = loading that head + catch-up narration.
**Archive** hides a branch from picker/timeline without deleting; unarchive
anytime. **No merge machinery — deliberately**: reconciling branches is a
conversation ("bring the sidebar idea from alt-0009 into main"), executed as
ordinary narrated edits. Fork narration is a grilling hook: "exploring an
alternative, or replacing the old direction? (If replacing, we can archive
'main' later.)" — ADR 0004.

---

## 9. Skill workflow (SKILL.md choreography)

### 9.1 Session start — eager launch, quiet bootstrap

1. `canvas.py start` — **failure never aborts**: degrade to verbal grilling
   with a one-line notice, retry next round.
2. Browser open best-effort (SSH/containers/WSL break `xdg-open`); URL printed
   either way.
3. `project_knowledge/`: load registry + config, or create (empty dir + fresh
   registry); the one-time migration offer fires here if root-level docs exist.
4. Out-of-session changes → the first agent move IS the catch-up narration.
5. Otherwise: state the destination-so-far from existing artifacts, or open
   grilling on a blank slate; empty canvas shows a seeded empty state, never a
   void.
6. Round counter + whose-move resume from the registry.
7. Op-schema primer rides SKILL.md; full reference on demand.

### 9.2 The round — unified move

**Round N = agent move N → user move N**; the counter advances when the agent
posts its next move. Agent move, in order:

1. **Process what accumulated** (chat answers, pin answers, every Save since
   last move — natural batching per §5.2).
2. **Remove test** as the pre-narration step (doubles as narration content);
   **never narrate an unvalidated artifact**.
3. **Narrate** (§5.2).
4. **Draw Gate**: draw only when structure/relationship/flow carries the point
   better than words. If drawing: typed op batch, validate-all-then-apply,
   atomic; the revision is announced by the narration itself. Purely verbal
   rounds touch nothing.
5. **Ask the frontier**: questions in chat; element-anchored questions ALSO
   pinned as ❓ (pin and chat question are the same object, synced). Max one
   view suggestion per round, question-driven and seeded; declines remembered.

User move: any mix of chat, canvas edits + Save, inline pin answers — no
channel is ever required. When a wireframe is the round's subject, the
interface-state checklist (empty/loading/error/disabled/no-results) feeds
frontier questions — a source of questions, never a gate.

### 9.3 Batching & waiting

Natural batching (§5.2); a Save landing while the agent composes waits for the
next move. Waiting rules per §6.3.

### 9.4 Disciplines — one discipline, two surfaces

- **Glossary ↔ domain diagram is a declared mapping.** CONTEXT.md stays
  canonical; a rename on either side trips the ordinary mapping tripwire, and
  that flag-and-offer conversation IS the glossary challenge.
- **ADR offers stay chat-only**, gated by the three-part test (hard to
  reverse / surprising / real trade-off), citing save short-ids as evidence.
- **The design tree stays implicit**; visualizing it is an ordinary view
  suggestion, never automatic.
- **Deletion conversations**: default on. Two-layer opt-out —
  `deletion_conversation: false` flips the global lean; with it on, a
  per-deletion signal ("pruning X, no need to discuss" in chat, or
  delete-with-a-note on canvas) suppresses just that one (a stated reason IS
  the conversation, pre-answered → one-line acknowledgment + tombstone). With
  it off, deleting a mapped element with live links still gets one flag line —
  never a silent structural loss.

### 9.5 Session end — flush docs, summarize in chat

Graceful: flush deferred semantic doc updates → **no parting canvas revision**
→ parting summary in chat (decisions settled with short-ids, open pins,
outstanding tripwires, where-we-left-off, and the dangling-thread check:
"the Excess-Return tripwire is still open — resolve before we stop?") →
persist round counter + whose-move → server stops via hook or watchdog.
Abrupt: mechanical records are immediate; next session's catch-up narration
reconstructs from state. **No session-journal file** — project_knowledge/
state is the single durable record.

---

## 10. Stack, packaging, distribution

**Stack** (ADR 0001): single stdlib-only Python server file, 3.9 syntax floor
(RHEL9); `uv run` canonical with **uv-on-PATH an assumed prerequisite**; bare
`python3` is the free degradation path. Frontend: React + Excalidraw,
Vite-built on the maintainer's machine, **committed prebuilt with fonts**
(~21 MB; npm/CI at build time only — strangers never build). A build-stamp
(source hash) ships beside `dist/` so a stale bundle is detectable. Windows
entrypoints via PowerShell/`.cmd`, never `.sh`.

**Distribution** (research-settled): one GitHub repo that is simultaneously
marketplace and plugin (`.claude-plugin/marketplace.json` with
`"source": "./"` + `plugin.json` + `skills/wysiwyg-grilling/{SKILL.md,scripts/}`
— the `anthropics/skills` layout). Serves both `/plugin install` (versioned,
auto-updatable; omit `version` so every commit counts) and plain
`git clone → ~/.claude/skills/` (zero-infrastructure fallback, free with this
layout). SKILL.md stays under the 10,000-token Read limit.

**Provenance & attribution** (packaging requirement):

- Copyable **with attribution**: `diagram-design` (MIT — complexity budget,
  remove test, connector rules, per-type reference structure),
  `utility-mermaid-diagrams` (Apache-2.0 — draw/don't-draw gate, node limits).
- **Re-expressed only** (no license grant): wireframe-spec conventions,
  wireframing's archetypes + annotation taxonomy, user-flow-diagram's
  shape→semantic map, ui-ux-pro-max's interface-state list — short factual
  material, rewritten in our own words with a provenance note.
- **Never redistribute**: `diagramming-code` (Trail of Bits trademark assets);
  `excalidraw-diagram-generator` content (wrong text model; idea-mine only —
  except its ID-remap-and-rebind algorithm, re-implemented, as idea).

---

## 11. First-run experience

- **Install**: `/plugin install` or git clone (§10). Prerequisite stated
  plainly: uv on PATH (or any python3 ≥3.9). No Node, no build, no network
  after install.
- **First invocation in a project**: bootstrap per §9.1 — project_knowledge/
  created silently, migration offer if root docs exist, canvas opens to a
  seeded empty state with the agent's opening grilling move. No tutorial;
  the loop teaches itself by doing one round.
- **Error copy is LLM-addressed** where the agent is the reader (protocol
  mismatch, validation repairs) and plain-language where the user is
  ("this canvas moved on — refresh to continue"). Launch failure degrades to
  verbal grilling (§9.1.1) — the skill NEVER hard-fails on app trouble.
- **Cross-platform floor**: Windows (PowerShell entrypoints, Store-stub-aware
  python detection) and RHEL9 (3.9 syntax floor) are the tested minimum;
  browser-open is best-effort everywhere.

---

## 12. Named implementation deliverables

Beyond the server/app themselves, milestone 1–2 must produce:

1. **Our own Excalidraw schema reference** (the mined generator's is wrong).
   MUST document: the bound-text model (`type:"text"` + `containerId` +
   container's `boundElements` — never an inline `"text"` prop on shapes);
   **`convertToExcalidrawElements` attaches bindings but does NOT route
   arrows** — geometry (x, y, width/height span) must be computed explicitly;
   arrow `startBinding`/`endBinding` and the rebind sentinel; `customData`
   conventions; font-family ids (legible default); the five reference kinds an
   ID remap must rebind (`id`, `groupIds`, `startBinding`, `endBinding`,
   `containerId`, `boundElements`).
2. **Per-type reference files** (diagram-design's template structure, MIT):
   one per first-class type — primitives, semantic-fact table, mapping rules,
   seed archetypes (wireframing's ASCII page archetypes, re-expressed).
3. **`make_element()` funnel + normalizer + differ** (§4.1, §5.1) with the
   validation/repair tables and migration registry (§4.5).
4. **SKILL.md** encoding §9 with the narration exemplars and op-schema primer.

---

## 13. Future directions (deliberately not built)

- **Code generation from artifacts** (wireframe→code, flow→scaffold): the
  design already preserves what it needs — stable semantic ids, normalized
  machine-readable format, typed fact tables, mappings as cross-view joins.
  Kept open by the annotations-stay-machine-readable clause (§3). A future
  effort charts its own map.
- **Other agent-runtime adapters**: the §6.6 boundary makes them a
  choreography-prompt + wait-mechanism exercise; none specced.
- **Autolayout**: bundle dagre (MIT) directly if ever wanted (D2 evaluation's
  conclusion; D2 itself: nowhere).
- **ASCII rendering as an agent read-back channel** (D2's one steal).
- **Pending-revision diff-preview peek**; **mid-fi wireframes**; **narration
  altitude per-artifact overrides** — all deferred, none precluded.

---

## 14. Milestones

**M1 — the loop, for real** (feel-prototype parity, productized): server +
`canvas.py {start,status,wait,stop,apply}` + committed bundle + normalized
artifact format + save records with inverses + differ (incl. §5.1
requirements) + flow type end-to-end + minimal chrome (round header, Save,
banners) + SKILL.md choreography + schema reference (deliverable 1).
*Exit test: the feel-prototype session, replayed through the real op path.*

**M2 — the model**: registry + mappings/tripwires + config + all three
first-class types with semantic-fact tables + interactive pins + full rail +
filmstrip + catch-up narration + per-type reference files (deliverable 2).

**M3 — history + shipping**: commit DAG UI (graph panel, branches, archive,
view mode) + durability package complete + marketplace packaging + Windows
validation + first-run polish.

Each milestone is independently usable; nothing in a later milestone is
load-bearing for an earlier one's exit test. **A milestone's exit additionally
requires its rows in Appendix A** — the prototype feature-parity checklist —
to be checked off; prose recall is not a tracking mechanism.

---

## Appendix A — Prototype feature-parity checklist

Every agreed feature demonstrated in the prototypes
([UI prototype](docs/prototypes/webapp-ui-prototype.html) variant C ·
[capability demo](docs/prototypes/capability-demo.html) ·
[feel prototype](docs/prototypes/feel-prototype/)), mapped to its milestone.
Fictional *content* of the demos (Argus dashboards, reports, pipelines) is not
product surface and is deliberately absent. If a build session finds a
demonstrated behavior missing from this table, that is a spec bug — fix the
table, don't drop the feature.

| # | Feature (as demonstrated) | Source | Milestone |
|---|---|---|---|
| 1 | Round header: round counter + whose-move, project · artifact as plain text, agent status chip | C / demo / feel | M1 |
| 2 | Save button with dirty dot; disabled when clean or in view mode | C / feel | M1 |
| 3 | Cadence toggle (per-round / pulled); pulled = revisions always behind the update control | C / feel | M1 |
| 4 | Pending-revision banner: apply now / after I save (op replay) | demo / feel | M1 |
| 5 | Stale-tab handling: base_revn 409 → gentle refresh prompt (tripwire coloring) | feel | M1 |
| 6 | Paper ground; sketchy shapes; legible text everywhere (no cursive) | all three | M1 |
| 7 | Editability contract: everything on canvas natively editable in live mode; chrome ≠ canvas | demo addendum 4 | M1 |
| 8 | "Agent is revising" = status chip change + in-progress timeline event; canvas untouched until atomic apply | C addendum | M2 |
| 9 | Right rail, read-only: registry panel — concept's views, mapping status, glossary link, tripwires in words | C / demo | M2 |
| 10 | Tripwire rendering: rail tripline + on-canvas trip-tag near the diverged element | C / demo | M2 |
| 11 | Pinned questions: interactive rail entries (inline answer box); answers are first-class wake events; resolved pins shown with their answers | feel (user-requested) | M2 |
| 12 | ❓ pins on canvas, synced with rail entries; answerable in place | demo | M2 |
| 13 | Pin-only / no-op revisions styled distinctly ("agent asked a question" — no Apply) | feel finding | M2 |
| 14 | Save-history timeline: short-id + gist + author-coded dots, pending event for held revisions | C / demo / feel | M2 |
| 15 | Timeline click → view mode: all artifacts at that save, viewModeEnabled read-only, persistent "nothing is lost" bar, gated actions with gentle toasts | demo addendum 2 / feel | M2 |
| 16 | Unsaved edits block time-travel (inviolable work) | feel | M2 |
| 17 | Filmstrip: all-artifacts dropdown grouped by concept (leftmost) + view thumbs with dirty/tripwire badge dots + "+ suggest a view…" | demo addendum 5 | M2 |
| 18 | Annotations: user/agent sticky notes ambient on canvas; native tools + server kind-detection | demo | M2 |
| 19 | Empty states seeded, never a void; toasts for gated/declined affordances | C / demo | M2 |
| 20 | Checkout an old commit → edit → Save forks a branch; fork-name prompt with `alt-NNNN` default | demo addendum 3 | M3 |
| 21 | Branch chips in rail: switch, archive 🗃 / unarchive, detached indicator; archived hidden never deleted | demo addendum 3 | M3 |
| 22 | Per-branch timeline filtering; "all branches" toggle with branch tags; ⑂ fork glyphs | demo addendum 3 | M3 |
| 23 | **Collapsible history-graph panel**: horizontal lanes (main centered, branches alternating), bezier fork edges, commit dots + revn captions, callout head-labels (node = view, label = switch), halo = head, amber = viewed, dashed ghost = unsaved fork; h-scrollable | demo addendum 4 | M3 |
| 24 | Fork narration hook ("exploring or replacing?") + branch-switch catch-up narration | demo addendum 3 | M3 |
| 25 | Copyable context update after Save/branch-switch for unsignalable runtimes | scope ruling | M3 |
