# Domain diagram — first-class type reference

*The question a domain diagram answers:* **"Are THESE the concepts, related
THIS way?"**

## Primitives

| Element | Spec | Meaning |
|---|---|---|
| Entity | `rectangle`, `role: node`, `kind: entity`, label = the term; optional `attributes: ["cash, mandate", …]` renders rows beneath the term (facts: `attribute_added/removed`) | a domain concept |
| Relationship | `arrow` with `from`/`to` + a short label ("owns", "emits") | how they relate |
| Cluster | `frame` around related entities (sparingly) | bounded context |
| Note | `text`, `role: annotation`, `annotates: <id>` | caveat/example |

Entity labels are singular nouns matching the glossary term exactly —
that identity is what wires the glossary discipline.

Keep visible `attributes` to ~2 rows; verbose per-entity detail (field
lists, invariants, examples) belongs in the element's `tooltip`
(markdown, shown on hover) where it can't collide with the drawing.

## Cardinality

Relationship labels may carry plain cardinality tokens — `1`, `0..1`,
`1..*`, `N`, `many`, `per-X` — parsed forgivingly, deliberately not a
schema language ("holds 1..*", "one per PM"). Encode redundantly with
arrowheads: 1:1 no arrowheads · 1:N arrowhead on the many end only · M:N
arrowheads both ends (or a junction entity). Fact
**`cardinality_changed {relationship, from, to}`** — narrate it as "this is
now one-per-X", because a single-token edit here is often the largest
structural consequence available on the canvas (one report vs one per PM
changes half the pipeline). Inconsistent tokens between the two ends of one
relationship is a lint WARNING. A relationship is fully stated when it
carries cardinality + owning side + the delete rule; the last two live as
an annotation when they matter ("Order owns; delete cascades") — ask for
them when a deletion conversation touches the relationship.

## Semantic facts

| Fact | Fires when | Narrate as |
|---|---|---|
| `entity_added/deleted` | entity add/del | vocabulary change |
| `relationship_added/deleted {between}` | arrow add/del | structural claim |
| `relationship_rewired {from, to}` | binding change | "X relates to Z now, not Y" |
| **`entity_renamed {from, to}`** | label edit | **glossary-challenge trigger** |
| `relationship_relabeled {from, to}` | arrow label edit | semantics shift |
| `possible_merge {labels}` | near-duplicate labels | "are these one concept?" |

## The glossary discipline (one discipline, two surfaces)

CONTEXT.md is canonical. The domain diagram is a **declared mapping** onto
it (concept `glossary` field links term names). `entity_renamed` IS the
glossary challenge — run it as an ordinary tripwire conversation: "the
diagram now says *Provider*, the glossary says *Vendor* — which wins?" On
agreement, update CONTEXT.md in your next move's doc flush (narrated,
veto-able). Never let the two drift silently.

## Mapping rules

- domain↔flow (default pair): entity ↔ the steps that act on it — link
  eagerly.
- domain↔wireframe: inference only.

## Seed archetypes

- **Core triangle**: the 3 load-bearing entities + labeled relationships —
  the best first domain sketch; grow from there.
- **Hub**: one central entity with 3–5 spokes placed radially
  (`angle = 2π·i/N`, r ≥ 250 — see `references/layout.md`); watch for the
  hub doing too much — a frontier question.
- **Lifecycle pair**: an entity and its state-carrying companion
  (Order/OrderStatus) — often exposes a hidden concept.

## Reading entity language

**Nouns fool you; verbs provide consistency.** People agree on a noun
("a Talk has a title") while holding different models — the same entity
doing structurally different things in different parts of the canvas is
THE boundary/ambiguity signal, and near-synonyms are its cousin.
So when `possible_merge` fires, resist auto-merging: **divergence is
signal, not noise** — two wordings may be two perspectives on one moment,
hinting at two contexts. Surface the disagreement (a ⚑ hotspot annotation
on the disputed spot is cheaper than a premature resolution); merge only
when the user rules.

Two more duties from the same school: **postpone naming** — when the user
drops a vaguely-named box, ask what it's *responsible for* before
accepting the label (responsibility first, information needed second,
name last; and naming *difficulty* is itself a design signal — a thing
that resists naming is usually a wrong thing); and **interrogate
relationships with the triplet** — what must be true *before* this can
happen, what is true *after*, what must stay true *always*? The "always"
answers are the invariants the design actually rests on. And don't start
from data: what's *displayed* in some screen is not what's structurally
invariant — resist superimposing the wireframe's blocks onto the domain's
entities.

Cheap identity tests when an entity's nature is disputed: "is it still
the same thing if all its attributes change?" → entity with identity;
"is it defined *only* by its attributes?" → a value, not an entity. And
when the user groups entities: **a grouping whose only rule is
structural ("an Order has line items") is a reference, not a boundary**
— if deleting the grouping and pointing at things directly would change
nothing about correctness, it was never protecting an invariant. The
birth event usually finds the true root ("what event brings this into
existence?").

## Bounded contexts (when clusters become frames)

A cluster frame earns bounded-context treatment when the same term means
different things inside and outside it. Discovery tells, beyond language:
**business phases** (a pivotal event — "Order Placed", "Sheet Sent" —
usually marks a boundary and belongs to the shared language between
contexts); **personas** (different people needing different flows through
the "same" process); lanes — with the caveat that *not every lane is a
context; sometimes it's just an `if` statement*. And a structural signal:
when grouping by responsibility starts fighting the left-right timeline,
the domain has outgrown flow-shaped layout — stacks, not sequence.

Rules for drawing them:
- **The arrow between two context frames is itself a model** — when two
  models interact there are usually three (each side's internals plus the
  translation between them). Label it with the relationship: shared
  kernel, anticorruption layer, conformist, customer/supplier, open-host,
  published language, partnership, separate ways. "Big ball of mud" is
  drawable too — as a demarcation you're choosing not to model.
- **Small maps for explicit questions**: one context map per question,
  never one wall-map of everything — the same view-per-question rule the
  rest of the skill already follows.
- Multi-context projects keep a root `CONTEXT-MAP.md` (each context, where
  it lives, how they relate) with per-context `CONTEXT.md` files beneath —
  the same convention the glossary discipline already builds on.
- **Strategic marking** (optional `strategic: core|supporting|generic` on
  a frame or entity, rendered as fill emphasis): classify by three
  questions — would a competitor with the same implementation erase our
  advantage? could we outsource it without damage? does it need deep
  business expertise? The classic failure is "everything is core"; asking
  the three tests per frame is the cure.

Two boundary questions that earn their pins: **"what invariant does this
grouping protect?"** (too many state transitions → the process boundary is
wrong; trivially simple ones → the logic leaked out to services; many
*corrective* policies elsewhere → the boundary excluded its own rules;
bigger groupings trade fewer corrections for more contention — and
sometimes the right scope is a time period, like a billing period). And
**"how would you know this boundary is a bad fit?"** — asking the user to
name their own falsification condition is the sharpest question in this
file. Assumptions are pin-shaped: no boundary decision is made with full
knowledge, so make the assumption explicit where it can be deleted.

The glossary's `_Avoid_` lists are live ammunition: relabeling an entity
TO a term some other entry rejects ("_Avoid_: Vendor") is always a
glossary challenge, never a silent accept. The server lints this
(`LAYOUT_WARNING` naming the canonical term) by parsing
`project_knowledge/CONTEXT.md`; the warning is your cue to run the
challenge in chat, and an accepted rejection makes `_Avoid_` the
resolution sink for `possible_merge` questions — the losing synonym
lands there, so it can never quietly return.

## Two anti-patterns in YOUR questioning

- **Relational reflex**: never demand normalization, foreign keys, or
  junction tables of a model that isn't relational — a document store is
  shaped by its access patterns, a graph by its edges. Ask which paradigm
  the design assumes before asking paradigm-shaped questions.
- **Greenfield clause**: never flag inconsistency against shipped state
  that doesn't exist. A proposed model has nothing to drift from.

And one question that reliably earns its pin: when a structural choice
lands ("Issuer is separate from Instrument"), ask **what did you give up
to get this?** — the answer is usually the trade-off line of an ADR.

Complexity budget: **max 8 entities** (domain's sub-limit of the two-limit
budget in `references/layout.md`); more → cluster into bounded contexts as
separate views. Sizing sanity at the context level: 3–5 concepts that
always change together is usually a fragment, not a context (merge it);
16+ needs demonstrated cohesion. The budget applies to the domain *view*,
never to the concept set in the registry — settled glossary terms all
become concepts, and that list may be long.

---
*Template structure from diagram-design (MIT — see NOTICE.md); entity
sub-limit from diagram-design §7 with utility-mermaid-diagrams convergence
(Apache-2.0). Cardinality tokens and arrowhead encoding from excalimate
er-diagrams (MIT) + diagram-design type-er. Relational-reflex, greenfield
clause, four-part relationship rule, and tradeoff question from
bm629/agent-skills (MIT). Radial formula from excalimate (MIT).
Nouns-fool-you, divergence-as-signal, postpone-naming, BC-discovery tells,
and the invariant triplet adapted from EventStorming (Brandolini, via
melodic-software, MIT). Strategic three-test classification and context-map
relationship vocabulary from wondelai/skills DDD (MIT); identity/value
tests idem. Aggregate-boundary reasoning (structural-grouping smell,
birth-event heuristic) from citypaul/.dotfiles DDD (MIT); context sizing
from tech-leads-club domain-analysis (MIT). Boundary smell tests,
verification-metric and assumptions questions re-expressed from the
ddd-crew canvases (CC BY-SA — ideas only, no wording copied); CONTEXT-MAP
convention from the local domain-modeling skill.*
