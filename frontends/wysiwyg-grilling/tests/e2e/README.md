# E2E regression suite — right-home triage

Every finding from assessment runs 1–4 (`capability-assessment-notes-
optimizer_R{1,2,3,4}*.md`, worktree root) maps to exactly **one** test
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
  402 tests.
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
| Stale-save 409 banner | e2e-dom (planned, run 5) | needs a second browser context |
| Branch fork/archive chips | e2e-dom (planned, run 5) | checkout → draw → Fork & Save |
| Mermaid seed (WP9) | e2e-flow | `mermaid.spec` — CLI seed through the LIVE tab handshake (`--no-headless` so a fallback can't mask it), semantic slugs + self-loop on disk; import dialog → user Save. Skeleton→op mapping itself is unittest (`TestMermaidSeeding`, captured fixture) |

Rows marked *planned* are the run-5 requirements list — add the spec
when the surface ships or the beat is scripted, and extend this table;
an untested row silently promoted to "covered" is how the next run
inherits false confidence.
