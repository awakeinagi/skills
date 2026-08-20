# Sequence — first-class type reference

*The question a sequence answers:* **"Is THIS who talks to whom, in THIS
order?"** Use it when a flow step is labelled with an actor ("Vendor API",
"Agent: …") — the flow is smuggling a conversation into a box — and for
integration/service work (archetype E), where it is usually the *first*
view. Subject: who, and in what order. Flow carries order only; domain
carries structure only; neither carries who.

## Primitives

| Element | Spec | Meaning |
|---|---|---|
| Actor header | `rectangle`, `kind: actor`, 160×60, label = actor name (≤10 chars reads best) | a party |
| Lifeline | `line`, vertical, `kind: lifeline`, under its header | that party's timeline |
| Message | `arrow`, horizontal, `from`/`to` lifelines, label = the message | one interaction |
| Activation | thin `rectangle` (8px wide) on a lifeline, `kind: activation` | processing span |
| Guard | `text`, italic, `[if …]` near the message start | conditionality |
| Note | `text`, `role: annotation` | side note |

## Geometry (seeder)

`headerX = 100 + i·250` (actor i, left to right by first appearance) ·
headers at y=50 · lifeline at `headerX + 76`, from y=110 · message
`y = 180 + j·80` (message j, top to bottom = time) · message label 20px
above its arrow · self-call: bump out 50–60px, return 20–40px lower ·
activation bar spans its request→response messages.

Time flows down, only down: an upward message (dy < 0) is a
`LAYOUT_ERROR` (time reversal). An activation bar that never closes is a
`LAYOUT_WARNING` — "closes" is read off the bar's own edges, so a bar
whose foot no message leaves is reported with the distance from the last
message on its lifeline. A bar that neither opens nor closes on a message
is not this finding; it is furniture in the wrong place, and no check
owns it yet.

**Parties are not linted as flow nodes.** An actor header carries no
binding — messages bind lifeline-to-lifeline — and a lifeline is a
timeline rather than a route, so neither the unconnected-node note nor
the passes-through-a-foreign-node warning applies to them. Both used to
(r5-16), and between them they were everything lint had to say about a
correct sequence.

## Stroke grammar (seeder)

Request: solid, width 2. Response: dashed, width 1.5, lighter — "this is a
return". Async / fire-and-forget: dotted. Error response: dashed red.
Timeout: dotted orange with the duration as a separate small annotation.
The grammar is load-bearing: `sync_changed` fires off exactly these style
transitions.

## Semantic facts

| Fact | Fires when | Narrate as |
|---|---|---|
| `actor_added / actor_deleted` | lifeline add/del | a party entered/left the protocol |
| `message_added / message_deleted {from, to, label}` | arrow between lifelines | an interaction appeared/vanished |
| **`message_reordered {message, before, after}`** | vertical reorder | **flagship** — the protocol changed; sequence's `rewired` |
| **`actor_reassigned {message, from_actor, to_actor}`** | endpoint moved to another lifeline | **flagship-2** — "who does this" changed |
| `sync_changed {message, from, to}` | solid↔dashed↔dotted transition | a call became fire-and-forget (or the reverse) |
| `activation_changed` | activation bar resize | a duration/blocking claim moved |

Plus universal facts. **Every `agent`-kind actor owes the same question as
an `agent` flow node: what does it do when it's unsure?**

## DMF mode (the 1000ft party map)

A sequence whose subject is *organisational* — who exchanges what, at
message granularity — rather than protocol mechanics. Same type, three
extensions:

- Actor headers carry `kind: actor | system | context` (actors plain,
  systems shaded, contexts double-border). At round 1 nearly everything
  is an actor or system; a kind flipping to `context` later is a
  **crystallization event** — narrate it as the design decision it is.
- Messages may carry a **contents** annotation ("what data rides this
  message") and an order number. `contents` changes narrate via the
  ordinary label facts; no new fact table — `message_reordered` and
  `actor_reassigned` already carry the load.
- Skip lifelines/activations at this altitude — plain boxes and numbered
  arrows; the mechanics arrive if and when the diagram descends to
  protocol level.

Use it as the **first seed for party-shaped openings** (see
`references/view-progression.md`) and for stakeholder-facing scenario
walks. One scenario per diagram, always.

## Fragments (open design — draw generically for now)

Standard UML alt/opt/loop fragments have no primitive here yet: draw a
conditional branch as two messages with italic `[guard]` texts, and a loop
as one message with a `loop: <condition>` annotation. A `fragment_added`
fact is planned but needs original design (no source to absorb) — until it
lands, narrate guards from the annotation texts.

## Mapping rules

- sequence↔flow: message ↔ transition, actor ↔ the steps it owns. Link
  eagerly as you draw. Flow stays the hub; sequence is a spoke. The link
  earns narration and tripwires and **no strict lint** — 3.2.4 and 3.3.7
  join wireframe × flow only, and a sequence member is skipped in silence
  (3.3.7 reads `frameId` and `kind: input`, which a sequence has no
  analogue for).
- sequence↔wireframe/domain: **inference only by convention, not by
  enforcement.** `add_mapping` type-checks nothing and tripwires are
  type-blind by ruling (user, 2026-08-20), so a declared mapping is
  accepted and nags on one-sided edits — it just never reaches a strict
  check.

## Seed archetypes

- **Request/response**: caller, service, one request + one response, one
  activation. The minimal honest protocol sketch.
- **Mediated call**: client → gateway → two services; fan the gateway's
  outbound messages with distinct attach offsets.
- **Timeout path**: request, activation, dotted-orange timeout return +
  error response — seed this pair whenever a deadline exists (archetype E
  always owes it).

Complexity budget: **5 lifelines** (sub-limit of the 9-node budget);
messages count against the 12-arrow budget — and 5–9 messages per
scenario is the readable range (domain-message-flow practice; a working
convention, not a cognitive law). Over either → split by scenario ("happy path" /
"failure path"), one scenario per diagram.

---
*Geometry constants, stroke grammar, and self-call/activation conventions
from excalimate's sequence-diagrams (MIT). One-scenario-per-diagram from
design-doc-mermaid's sequence guidance (re-expressed). Lifeline sub-limit
from diagram-design §7 (MIT). Fact table original to this skill (v0
refinement audit §6.1).*
