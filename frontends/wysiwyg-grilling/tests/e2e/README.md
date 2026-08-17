# E2E regression suite — right-home triage

Every finding from assessment runs 1–5 (`capability-assessment-notes-
optimizer_R{1,2,3,4,5}*.md`, worktree root) maps to exactly **one** test
home. The rule: a browser test asserts only what only a browser can see —
backend mechanics stay in the fast in-process unittest suite
(`tests/test_backend.py`), and a Playwright test for them would boot a
server+browser to prove what a unit test proves in milliseconds, then be
maintained twice.

Run: `npm run test:e2e` (starts real `canvas.py` servers per worker).
Visual baselines are **linux-only**, taken at `deviceScaleFactor: 1`
with the pinned `@playwright/test`; regenerate with
`npx playwright test --update-snapshots` on this platform only.

## Homes

- **unittest** — routing, commit/replay, lints, registry, facts,
  reconciliation, composed-part invariants, metrics, mermaid mapping.
  <!-- live:unittest_suite_cases -->1284<!-- /live:unittest_suite_cases -->
  tests, mutation harness and render tier included — a **live value**
  computed by `tests/livedoc.py` and held current by its pre-commit hook,
  because this exact number needed a commit of its own (`cb533ab`) to move
  it by ten and had drifted by nearly eight hundred by v0.9. It was 416 at
  run-5 close; that one is history, so it stays a literal.
  Red-by-intent counts are deliberately **not** live here. They belong to
  the census, are stated once in `SESSION-HANDOVER.md`'s guarded
  durable-count sentence, and are checked by
  `test_the_handover_transcribes_the_durable_red_counts` — a guard whose
  whole value is failing loudly when a red moves, which an auto-repair
  would disarm.
- **e2e-dom** — React chrome: rail, cards, modals, banners, overlays.
- **e2e-flow** — round-trips that cross the browser AND the server.
- **e2e-visual** — canvas-rendered pixels (`toHaveScreenshot`).

## Triage table (findings with a UI face; everything else → unittest)

| Finding | Home | Test |
|---|---|---|
| r4-11 router crash / self-loops | unittest | `TestRouterTotalityAndSelfLoops` |
| r4-7/D21 referential integrity | unittest | `TestReferentialIntegrity` |
| r4-8/r4-10 composed parts | unittest | `TestComposedReconciliation` |
| r4-12/R3-4 KPI wrap (live tab) | e2e-visual | `visual.spec` `mini-screen.png` — the '62' tile renders one line in the REAL editor |
| E-4 wavy body-text | e2e-visual | same snapshot — wavy lines visible |
| B6 toggle travel | e2e-visual | same snapshot (20px travel) + `TestPictureIsTheTruth` |
| r4-1 endpoint crosses through | unittest | `TestPictureIsTheTruth` |
| D15/r4b-3 pin glyph placement | unittest | `TestPictureIsTheTruth` (geometry) |
| B3/r4-13 rail burial | e2e-dom | `rail.spec` — concepts/vocabulary split, no "0 views" rows |
| B1/D25 pin age invisible to user | e2e-dom | `rail.spec` — card age line + modal "Open for N rounds" |
| B2/D26 checkbox not operable | e2e-flow | `flows.spec` — select → flip → Save → on-disk `checked: false` |
| B7/r3-3 filmstrip reachability | e2e-dom | `filmstrip.spec` — all 7 artifacts, other-concept click navigates |
| R1 banner pile-up / pending banner | e2e-flow | `flows.spec` — pulled cadence → banner → Apply now → `PENDING=0` (surface never exercised in any assessment) |
| v0.2 gap #4 reader Esc | e2e-dom | `walk-reader.spec` — opens on selection, Esc closes, no force-click |
| ▶ walk (never agent-driven) | e2e-dom | `walk-reader.spec` — steps 1/2 → 2/2, Esc exits |
| Event-loop substrate (r4-6/r4-3) | e2e-flow | `flows.spec` — rail answer → `pin_answer` in EVENTS_LOG |
| r3-2/r3-6 pulled-cadence freezes | unittest | pulled paths in backend suite |
| R2-4 variant-frame rename blind | unittest | `TestStateVariantFrames` |
| R2-8 arrow-label anchor | unittest | `TestClientRemeasure` + x-geometry |
| R2-10 decoration overshoot | unittest | decoration-spill lint tests |
| R2-11/r3-1 marker on diamond | unittest | `TestMarkerAnchor` |
| Stale-save 409 banner | e2e-dom (planned, run 6) | needs a second browser context — still unexercised after run 5 |
| Branch fork/archive chips | unittest | **exercised in run 5**: user checkout + save forked `alt-0022`, agent restored `main` and archived it. Replay-pinned by `TestArgusR5Fixture.test_forked_branch_survives_replay_archived`; the *chips* still have no DOM test |
| r5-2 round goes backwards on Apply | e2e-flow (planned, run 6) | pulled cadence → queue → Apply now → header round must not decrease and pin ages must not fall |
| r5-6 pulled drops ECHO/lint | unittest | apply the same batch under both cadences; the queued response must carry echo+lint computed from the staged scene |
| r5-11 `--relayout` user-placement guard | unittest | drag a node as the user, relayout, assert the NOTE names it — the check currently filters an empty set |
| r5-8 rejected batch mutates the registry | unittest | reject a batch containing a valid registry op; the registry must be unchanged on the server AND on disk |
| r5-13 sticky note trips ART-011 forever | unittest | `TestArgusR5Fixture.test_sticky_note_forces_a_repair_on_every_load` — flip it to `repairs == []` when fixed |
| r5-10 ⌗ dialog import quality | e2e-flow | extend `mermaid.spec` — paste onto a non-empty artifact, assert no mid-word-broken label |
| r5-10's other four insert sites | e2e-dom | `placement.spec` — 🗒 note onto the tile the user has zoomed to; **red by intent** (`test.fail()`), the note buries `kpi`/`kpi-value`/`kpi-label` today. `addStickyNote`, `insertFrame`, `askUserPin`, `insertTemplate` still place at `sceneCenter()`; the note stands for all four |
| r5-5 border-collinear routing | unittest | an edge leaving a side-edge midpoint must not run along its own box's border |
| Mermaid seed (WP9) | e2e-flow | `mermaid.spec` — CLI seed through the LIVE tab handshake (`--no-headless` so a fallback can't mask it), semantic slugs + self-loop on disk; import dialog → user Save. Skeleton→op mapping itself is unittest (`TestMermaidSeeding`, captured fixture) |

Rows marked *planned* are the run-6 requirements list — add the spec
when the surface ships or the beat is scripted, and extend this table;
an untested row silently promoted to "covered" is how the next run
inherits false confidence.

Run 5 closed three of run 5's own planned rows by *exercising* them
(pending banner incl. `After I save`, cadence flip both directions,
branch fork/switch/archive) and left one open (the 409). The r5-* rows
above are all currently **red-by-intent**: they name the assertion the
v0.9 fix has to satisfy, not coverage that exists.
