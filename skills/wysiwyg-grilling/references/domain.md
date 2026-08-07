# Domain diagram — first-class type reference

*The question a domain diagram answers:* **"Are THESE the concepts, related
THIS way?"**

## Primitives

| Element | Spec | Meaning |
|---|---|---|
| Entity | `rectangle`, `role: node`, `kind: entity`, label = the term | a domain concept |
| Relationship | `arrow` with `from`/`to` + a short label ("owns", "emits") | how they relate |
| Cluster | `frame` around related entities (sparingly) | bounded context |
| Note | `text`, `role: annotation` | caveat/example |

Entity labels are singular nouns matching the glossary term exactly —
that identity is what wires the glossary discipline.

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
- **Hub**: one central entity with 3–5 spokes (watch for the hub doing too
  much — a frontier question).
- **Lifecycle pair**: an entity and its state-carrying companion
  (Order/OrderStatus) — often exposes a hidden concept.

Complexity budget: **8–12 entities**; more → cluster into bounded contexts
as separate views.

---
*Template structure from diagram-design (MIT — see NOTICE.md); complexity
budget converges from three sources (diagram-design's 8–12, mermaid guides'
node limits — Apache-2.0 utility-mermaid-diagrams — and the feel-prototype
finding).*
