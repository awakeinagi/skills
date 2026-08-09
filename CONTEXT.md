# WYSIWYG Grilling

An agent ability (a Claude Code skill + local web app) that extends grilling-with-docs: alongside text docs, the agent externalizes its understanding as visual artifacts the user can edit, closing the alignment loop faster.

## Language

**Artifact**:
A single visual representation on the canvas — a wireframe or a diagram. Persists in the target project's `project_knowledge/` directory.
_Avoid_: drawing, doc, visual

**Canvas**:
The editable surface in the web app where artifacts are rendered and manipulated.
_Avoid_: board, whiteboard

**Concept**:
An aspect of the project's design that artifacts represent, recorded as an identity anchor in the project's Registry. A concept holds no drawing content of its own; its views do. One concept may have several views.
_Avoid_: entity, element

**Registry**:
The per-project `model.json` in Project Knowledge listing every concept, which artifacts view it, and the mappings between views. Co-authored: the agent maintains it through narrated, veto-able revisions; the user may edit it directly, and user edits are interpreted and narrated back like canvas Saves. The single place to read a project's shape; views stay plain artifact files.
_Avoid_: manifest, index

**View**:
An artifact considered as one perspective on a concept, with membership recorded in the Registry. Some artifacts are solo representations; others are views of the same concept, connected by mappings. Views are proposed question-driven — only when the conversation hits a question no current view can make tangible and there is material to seed a real first draft — and pruned at will. The Archetype named at round 1 fixes the set of views a project is expected to eventually owe; owed-but-undrawn views are View Debt.
_Avoid_: projection, facet

**View Debt**:
The condition of a concept whose settled understanding has outgrown its views: a view the project's Archetype says is owed, recorded in the Registry but not yet drawn. Debt is carried openly and paid at most one view per round, only when a question triggers it — never drawn speculatively.
_Avoid_: backlog, missing diagram

**Archetype**:
A named structural shape — defined by what the project produces (document generator, data pipeline, interactive application, multi-actor workflow, integration/service, decision support) — that the agent commits to in one correctable line at round 1, together with the parties involved and what passes between them. Naming it fixes which views the project owes and the order they become decidable in; it is a claim the user can correct, never a template that constrains drawing.
_Avoid_: template, project type

**Mapping**:
A recorded correspondence between views of the same concept, checked at Save: the agent flags divergence and offers to propagate, never syncing without confirmation. A mapping can be annotated as intentionally divergent. Which artifacts are mapped vs. solo varies per project. A mapping over several elements and one is a *compression* — several real controls at one resolution, one box at a lower one — not a naming mistake, and it asks one question, not one per pair.
_Avoid_: link, sync

**Artifact Type**:
The kind of representation an artifact is (wireframe, flow, domain diagram, sequence, …). Support depth is per-project configurable: first-class types carry typed edit semantics, narration facts, and mapping rules; extended types draw with generic narration only. Defaults: wireframe, flow, domain diagram, and sequence first-class.
_Avoid_: diagram kind, template

**Annotation**:
A note element pinned to a location in any first-class view — an interaction note, priority number, or comment — authored by either the user or the agent. Ambient in the artifact (never a separate mode) and a first-class reply channel in the refinement loop.
_Avoid_: comment thread, markup

**Refinement Loop**:
The core interaction cycle: the agent revises the canvas and asks for feedback → the user edits and Saves → the agent narrates its understanding of the changes, hypothesizing reasoning and implications → the user corrects in chat → repeat until the user is satisfied.
_Avoid_: feedback cycle, iteration

**Save**:
The explicit user action (a web-app button) that checkpoints their canvas edits and hands the turn to the agent. The sync point of the refinement loop — user edits are invisible to the agent until Save.
_Avoid_: commit, submit

**Round**:
One full pass of the refinement loop: one agent Move followed by one user Move. The counter advances when the agent posts its next move. By default the agent updates the canvas once per round (per-round cadence); the user may switch to pulled updates, where the canvas refreshes only on request.

**Move**:
One side's turn within a Round. The agent's move is a single composite turn: narrate what accumulated, pass the Draw Gate before any canvas revision, then ask frontier questions (element-anchored ones also pinned). The user's move is any mix of chat replies, canvas edits + Save, and pin answers — no channel is ever required. Multiple Saves within one user move stay separate commits but are narrated together.
_Avoid_: turn, step

**Draw Gate**:
The check inside every agent move before touching the canvas: draw only when structure, relationship, or flow would carry the point better than words. Purely verbal matters never force a drawing.
_Avoid_: draw check, canvas gate

**Catch-up Narration**:
The session-opening reconciliation: artifacts are diffed against the last save record's state, and any out-of-session changes flow through the normal narration pipeline framed as "since we last spoke," with a reconciliation save record re-anchoring history. Edits from any tool are legitimate input.
_Avoid_: sync check, startup scan

**Config**:
The per-project `config.json` in Project Knowledge holding the behavior knobs: artifact-type tiers and priorities, narration altitude, canvas update cadence, and the deletion-conversation opt-out. The user's levers over the loop, distinct from the Registry (which records understanding, not preference).
_Avoid_: settings, preferences file

**Narration**:
The agent's post-Save turn: a committed reading of the changes (chosen after internal multi-hypothesis deliberation), the named point of uncertainty, any mapping tripwires, and offered next moves. Never silent about a change; never a guess about an unreadable one.
_Avoid_: summary, changelog

**Intent Echo**:
The statement, returned with every agent canvas revision, of the observable consequence of what actually landed — what each change now looks like on the canvas, as opposed to what was asked for. It exists so the agent verifies its drawing said what it meant without needing to see pixels.
_Avoid_: confirmation, receipt

**Snapshot**:
A rendered image of the canvas, taken to check that a drawing is legible — and only for that. Never a source of truth: what changed and what it means always come from save records and their semantic facts, not from pixels.
_Avoid_: screenshot-as-evidence

**Channel Contract**:
The rule assigning each surface of the conversation its job: element-anchored matter belongs on the canvas (pins, annotations), discursive matter belongs in chat, and settled matter belongs in docs (glossary, specs, ADRs). A question about a specific element rides the canvas; a question about direction rides chat; nothing settled lives only in scrollback.
_Avoid_: communication rules

**Save Record**:
The per-Save JSON written to Project Knowledge: bucketed changes with inverses, the mechanical summary, and semantic facts. Derived and disposable — artifact snapshots are the source of truth; the record exists for narration, revert, and history.
_Avoid_: diff file, event log

**Project Knowledge**:
The `project_knowledge/` directory in the target project where the ability stores its outputs — text docs, diagrams, and wireframes.
_Avoid_: docs dir, knowledge base

**Tripwire**:
The question raised when one member of a mapping changes in a meaning-changing way and its siblings do not. One question per mapping per Save, answerable in place like a pin, and standing until resolved. Presentation-only edits — a nudge, a re-worded tooltip — never arm one.
_Avoid_: alert, warning

**Divergence Policy**:
A standing ruling that a class of change is expected to differ across a mapping's members, so it stops asking. Broader than annotating a single mapping: the policy applies wherever the same kind of change recurs.
_Avoid_: rule, exception

**Waive**:
A permanent answer to a one-time lint question, keyed on the artifact it is about (`q25:<artifact>`, `324:<artifact>:<step>`, `var:<artifact>:<block>`). The `reason` **is** the recorded answer, not a silencer — the op refuses without one. A waived question is an answered question.
_Avoid_: suppression, ignore, mute

**Budget**:
The complexity ceiling for one artifact — by default 9 nodes and 12 arrows, 8 entities on a domain view, 9 blocks per screen. `set_budget` overrides it for a view whose density is the point, with a required reason the lint restates every run. Legibility physics, not taste: over budget, split the view, never shrink the font.
_Avoid_: limit, quota

**Declined**:
The record that the user was offered a view and said no, kept so the offer is not re-made and so View Debt does not nag about something already settled.
_Avoid_: rejected, skipped

**Branch**:
An alternative version of the project's drawings, forked from a Save the user checked out. A branch is something to *show* — this way or that way — not a line of work to integrate, so there is no merge. Scenes and the whole branch-scoped Registry (concepts, mappings, pins, tripwires, waives, budgets) follow the branch you are standing on; only the shape of history itself is shared.
_Avoid_: version, fork, variant
