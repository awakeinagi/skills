# Changelog

## v0.9.1 — 2026-08-31

The first release to carry a version number at all. Before this,
`.claude-plugin/plugin.json` had no `version` key, so an installed copy
reported itself as `"unknown"`.

### The empty-save class, closed on the axis it lives on

A change lands on disk and the record says `saved without changing
anything`. The repo had closed this **twelve times** without closing it.
Each fix was small and roughly right; what was wrong every time was the
**instrument**, and each instrument was blind in a way the previous one
could not reveal — a grid built by calling the writer it audits, a client
model with two steps in the wrong order, a parameter sweep with no height
axis, a guard that read its own universe from the data it was auditing,
and a capture whose every caption sat on the one text alignment where the
defect cannot appear.

**What changed structurally:** the referee is now a real browser. A tab
records its testimony in the wire format — the artifact it loaded plus the
verbatim `/api/save` body — and the model tier replays it. Absence is
loud: delete the capture and the suite fails with `NO BROWSER HAS EVER
SPOKEN ABOUT THIS CLASS` rather than skipping green. That guard has been
watched failing, which is more than any of its predecessors could say.

**Fixed:**

- A caption carried by its container is no longer narrated as having moved
  on its own. The prediction is made in the space the store keeps, so the
  half-pixel `_round_geom` residual cancels instead of deciding the answer.
- A caption's own re-measure is no longer treated as a vertical
  displacement. Measured in a live tab: `dy == 0.0000` in 9 of 9 captions
  whose height the client changed. The term described nothing, and it
  swallowed real drags of up to 21px.
- A server-written caption now sits where the client draws it.
  `recenter_label` wrote `container.y + 6` where the shipped bundle uses
  `5`. Every instrument that came before sat on `middle`, the one
  alignment where writer and client happen to agree; on `top` and
  `bottom` it was wrong every time. Rectangles are now exact at all three
  alignments — 43 of 43 corpus captions that disagreed now agree.
- A host element no longer borrows its part's verb. A box whose caption
  moved reported itself as `moved`.
- A bound label is judged by its binding, not by its `customData.role`
  tag, so an untagged label reaching `/api/save` is no longer silent.
- The per-element save receipt no longer under-reports: a coordinate that
  changed is named, and a composed part's host is named for the part.

**Known and deliberately open:** on ellipse and diamond containers a
`middle` caption can still disagree with the client by up to 0.25px. It is
seeded with the *correct* follow-up — port `computeBoundTextPosition`
whole — because the obvious repair (the shape's inset alone) was measured
to make it four times worse.

### Assessment

Round 7 ran 16 turns against this skill and exercised all four first-class
artifact arms for the first time; `domain` and `sequence` had never been
touched by any prior round. It found three defects, corrected two of its
own checklist's baselines, and independently reproduced the caption-anchor
defect through a route the entire fix wave had missed.
