# The canvas app — what the USER can do, and when to say so

Everything else in this skill describes your side of the loop. This file
describes theirs: the affordances in the web app, and the moment each one is
worth mentioning. You cannot see their screen, so an affordance you never name
is one they will not find — and several of these solve problems you would
otherwise solve badly in prose.

Mention at most one per round, when its moment arrives. A tour is noise.

---

## The chrome, left to right

| Control | What it does | Say so when |
|---|---|---|
| **🗒 note** | Drops a sticky note in their own colour. | They are explaining something the drawing can't hold yet. Their notes read as requirements — see SKILL.md. |
| **❓ ask** | Pins a question *at you*, anchored on an element. | They say "I don't know" about something specific. Pins from them arrive as `direction: user` and get answered FIRST. |
| **+ insert ▾** | Screen frames (Phone 360×640, Tablet 768×1024, Desktop 1280×800) and starter templates (list, form, dashboard, fork). | They want to draw a screen themselves, or asked "how big should this be?" |
| **✨ tidy** | Grid-snap, re-route, re-fan, re-stack — as an ordinary revision they can revert. Refuses while the canvas is dirty. | After a messy round, or when they say the drawing looks scruffy. Never as a substitute for fixing a layout you got wrong. |
| **⧉ context** | Copyable context block for unsignalable runtimes. | Your Save events aren't arriving (see SKILL.md § Degraded modes). |
| **⇓ png** | Exports the current artifact as PNG, real Excalidraw rendering. | They want to paste a drawing into a doc or a ticket. **Carries no tooltips** — for handover use `canvas.py export --with-footnotes` instead. |
| **⤢ fit** | Zooms the artifact to fit. | They say they can't see it all. |
| **▶ walk** | **Steps through a wireframe's screen frames like a prototype** — ←/→ to move, Esc to exit. Enabled only on artifacts that have screen frames. | The single most under-used control. Say it when a wireframe grows a second state (normal / stale / error), and at handover: "open the dashboard and press ▶ walk" beats four static frames. |
| **↺ revert** | Undoes the last save. | They regret a save. It is theirs to use; don't pre-empt it. |
| **Save** | Commits their edits. Each Save is a commit; nothing they do is destructive. | Worth saying once, early, to a nervous user. |

**per-round / pulled** is a toggle *they* own. `per-round` lands your revisions
on a clean canvas; `pulled` holds everything behind the banner until they ask.
If they flip to `pulled`, they are telling you to stop moving things under
them — read it as a request for a still canvas, not as disengagement.

## The rail (right-hand column)

**Registry** — concepts, their views, and unpaid view debt. **Pinned
questions** — your ❓ pins and theirs, answerable in place; the card opens a
modal carrying the `detail` and `examples` you wrote, which is the only
out-of-band briefing they get, so write them. **Layout** — the lint findings
for the current artifact. **Branches** and **Save history** — the commit
timeline; entries are clickable (time travel), and the bookmark button labels
a save ("v1 baseline").

## On the canvas itself

- **Right-click an element → tooltip** — add, edit or remove the hover detail.
  A small dot marks elements that carry one. This is where verbose per-element
  detail belongs; never add extra visible rows to say it.
- **Double-click a label** to rename. Ids stay stable, which is what makes a
  rename detectable as a rename rather than a delete-plus-add.
- **Drag anything.** A locked element is a guardrail, not a wall — they can
  right-click to unlock, and that is always legitimate.
- **The pending-revision banner** carries *Apply now*, *After I save* and
  *Discard*. Discard is theirs; if they use it, the revision is gone and you
  are told. Don't re-queue the same thing — ask what was wrong with it.

## Handover

A session usually ends with the drawings going to someone who wasn't in it.
Two things do not survive that: **tooltips** (hover-only) and **the walk**
(interactive). So at handover:

- `canvas.py export --artifact <id> --with-footnotes` writes an SVG with the
  tooltips numbered underneath and the glossary appended — the artifact
  carrying its own detail.
- Point them at **▶ walk** for anything with screen frames.
- The glossary (`project_knowledge/CONTEXT.md`) is the thing to read first;
  say so explicitly rather than assuming.
