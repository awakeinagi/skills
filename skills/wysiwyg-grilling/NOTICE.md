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

## Deliberately absent

- No Trail of Bits / diagramming-code assets (trademark).
- No excalidraw-diagram-generator content (its text model is wrong; its
  ID-remap-and-rebind idea was re-implemented from scratch — see
  references/excalidraw-schema.md).
