# Provenance & attribution

This skill bundles or re-expresses material from the following sources, per
the provenance rules in the project spec (§10):

## Bundled software

- **Excalidraw** (https://github.com/excalidraw/excalidraw) — MIT License,
  © Excalidraw contributors. The committed web bundle embeds the Excalidraw
  editor and its fonts (Virgil, Excalifont, Nunito, Cascadia, Lilita One,
  Comic Shanns, Liberation Sans, Xiaolai, Assistant — each under its own
  open font license, shipped as distributed by the Excalidraw package).
- **React / React DOM** — MIT License, © Meta Platforms.

## Copied-with-attribution (licensed)

- **diagram-design** (cathrynlavery/diagram-design, MIT): the per-type
  reference template structure, the complexity budget — **max 9 nodes AND
  max 12 arrows, two separate limits** (an earlier version of this notice
  collapsed them into "8–12 nodes"; corrected) — plus per-type sub-limits
  (5 lifelines / 5 lanes / 8 entities / 2 callouts), the remove-test, the
  §6 connector rules (orthogonal routing, label offset, attach-point
  fanning, no-crossing, bridge hops), and the §7 grid discipline.
- **utility-mermaid-diagrams** (Apache-2.0): the draw/don't-draw gate
  ("draw only when structure, relationship, or flow carries the point
  better than words") and per-diagram node limits.
- **excalimate** (MIT): sequence-diagram geometry and stroke grammar,
  ER cardinality-token and arrowhead conventions, radial/sibling placement
  formulas, fan-by-focus, waypoint and bundling fallbacks, merge-dot and
  branch-direction flowchart conventions, opacity-is-state.
- **EventStorming skill** (melodic-software, MIT) — the underlying method
  is **EventStorming by Alberto Brandolini** (eventstorming.com; credit to
  the method's author, not only the skill): rush-to-goal sequencing,
  raise-the-bar, reverse narrative, magic keywords, speak-out-loud,
  friction-based splits, incremental notation, "structural holes are
  conversations not yet had", center-start growth.
- **bm629/agent-skills** (MIT): proportionality/stay-in-lane/
  non-duplication/greenfield reviewer discipline, app-shell and the
  amend-ripple, async-three-node rule, flow elaboration ladder,
  irreversibility guard, state-quality rules (skeleton/empty/error),
  12-col/8pt rhythm, internal-≤-external spacing, four-part relationship
  rule, relational-reflex anti-pattern, tradeoff question.
- **softaworks C4 skill** (MIT): audience→level view selection, ≤20
  elements per level, no-bidirectional/no-unlabeled arrow hygiene.
- **owl-listener designer-skills** (MIT): Observation→Problem→Fix critique
  template; entry-point/eye-flow/weight/emphasis question sets.
- **wondelai/skills domain-driven-design** (MIT): strategic three-test
  classification (differentiation/outsourcing/talent), context-mapping
  relationship vocabulary, entity/value identity tests,
  naming-difficulty-as-signal.
- **citypaul/.dotfiles domain-driven-design** (MIT): aggregate-boundary
  reasoning — the structural-grouping ("relationship-driven aggregate")
  smell, the birth-event root heuristic, commands-not-queries framing.
- **tech-leads-club domain-analysis** (MIT): bounded-context sizing
  heuristics.

## Copied-with-attribution — v0.4 U-round additions

- **GOV.UK Design System** (OGL v3, © Crown copyright): form conventions
  (label above input, "(optional)" never asterisks, no
  placeholder-as-label), the input width table, the error-message
  wording rubric, one-thing-per-page guidance with its own
  qualifications, and the error-variant / check-answers / task-list /
  confirmation / start-page / address-lookup / confirm-email patterns.
  The GDS Carer's Allowance progress-indicator finding is quoted in Q25.
  One-thing-per-page is reasoned experience, not a controlled trial —
  transmitted at that strength, not upgraded.
- **W3C WCAG 2.2** (W3C Document License — criteria text quotable with
  notice): SC 1.3.2, 2.4.3, 2.4.11, 2.5.8, 3.2.4, 3.2.6, 3.3.2, 3.3.7
  ground the wireframe lints. **Epistemics rule**: no published source
  treats a wireframe as a checkable artifact — every lint is a
  derivation, so the agent never says a wireframe "fails WCAG"; the
  honest form is "this screen has a reading-order question that 1.3.2
  will ask later."
- **Caroline Jarrett's form-field question protocol** (CC BY 4.0):
  Q30–Q32, copied freely with attribution.

## Refused absorptions (v0.4 ruling — do not add these as lints)

Chased to their primary sources, these popular "laws" do not support the
rules commonly derived from them. An agent tempted to add one should
trip over this section:

- **Miller's 7±2** — about absolute judgment of unidimensional stimuli,
  not UI lists; Miller himself called the recurring sevens "a
  pernicious, Pythagorean coincidence"; working-memory span is ~4 chunks
  (Cowan 2001); menus are recognition, not recall (Nielsen 2009). The
  9-block budget is credited to diagram-design practice, never Miller.
- **Hick's Law** — models a single decision from a known set; does not
  model visual scanning; no "too many nav items" lint. The only salvage
  is the reframe question "is this a decision or a search?" (Q19).
- **Doherty Threshold** — the 400ms figure does not appear in the 1982
  paper (verified by full-text search; the only "400" is a user count);
  the paper argues "sub-second". Response time is out of lane anyway.
- **Choice overload** — meta-analysis (63 conditions, N≈5,036) puts the
  mean effect at virtually zero; no option-count lint.
- **"Reduce cognitive load"** as critique vocabulary — unfalsifiable,
  fits any screen, fails proportionality; absorbed instead as the three
  structural moves (offload / default / pre-fill).

lawsofux.com (CC BY-NC-**ND**) served as the index that led to the
primary sources; ND means even re-worded summaries of its pages are
derivatives, so no text from it is carried anywhere in this skill — the
primaries above are cited directly.

## Re-expressed only (no license grant; rewritten in our own words)

- wireframe-spec: annotation vocabulary and fidelity-level conventions.
- wireframing: page archetypes and iteration methodology.
- user-flow-diagram: shape→semantic mapping conventions.
- ui-ux-pro-max: the interface-state checklist
  (empty/loading/error/disabled/no-results).
- artifact-diagramming (built-in, no license grant): draw-gate sharpeners
  (sentence-faster, mechanism-not-name, draw-the-difference,
  complexity-matches-stakes).
- DFD structural invariants (black-hole/miracle nodes, kind-pairing): the
  notation is a public standard; the source repo carries no license, so
  nothing was copied — rules re-derived from the standard.
- design-doc-mermaid: one-scenario-per-sequence-diagram.
- ddd-crew canvases (Bounded Context / Aggregate Design / Core Domain
  Charts / Context Mapping / Domain Message Flow; licensing ambiguous
  between CC BY and CC BY-SA — treated as BY-SA, so ideas only, no
  wording): boundary smell tests, assumptions/verification-metric
  questions, small-maps-per-question, 5–9 message budget.
- the local domain-modeling skill: the CONTEXT.md / CONTEXT-MAP.md /
  docs-adr conventions this skill's glossary discipline builds on.
- full-stack-skills ddd collection (license file inconsistent with its
  Apache-2.0 frontmatter — treated as unlicensed): nothing copied;
  its event-storming color vocabulary converges with Brandolini's
  standard notation, credited above.
- NN/g (Nielsen Norman Group — no reuse licence): heuristic framings
  (H2/H3/H6 questions), the empty-state pairing, progressive-disclosure
  question, and the aesthetic-usability operational warning —
  re-expressed and cited, nothing copied.
- Kurosu & Kashimura 1995 (academic paper): the apparent-usability
  finding grounding the low-fi ceiling — cited, described in our words.
- Larry Tesler's law of conservation of complexity (nomodes.com):
  Q14/Q15 — short quotes with attribution.

## Deliberately absent

- No Trail of Bits / diagramming-code assets (trademark).
- No excalidraw-diagram-generator content (its text model is wrong; its
  ID-remap-and-rebind idea was re-implemented from scratch — see
  references/excalidraw-schema.md).
