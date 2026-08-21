# Curator batch 36 restore — report (v2, re-derived on `88e04e6`)

**Branch `curator-36-restore-v2`**, one commit off the room-rule
candidate `88e04e6`. Worktree `/tmp/curator36-v2` (isolated).

**1771 passed / 46 skipped / 2 xfailed / 2 failed = 1821.** Base is
1767/46/2/2 = 1817; the +4 is this change's four test methods. The two
failures are the **inherited** `label_adrift` pair
(`test_mutant_diamond_label_overflows_shape`,
`test_neighbour_label_dragged_clear_of_its_owner`) — reproduced on a bare
`git archive` of `88e04e6` with byte-identical messages before any of this
landed. `mutants run --all` fails those two and nothing else.

## Why v1 was sent back, confirmed by measurement

Not a tolerance problem. `client_wrap_width` — the client's real cap,
`js_round(w/2) - 10`, which is **90px** on a 200px rhombus — now wraps a
bound label *before* the overhang check measures it. The v1 discriminator
stood on a centred 172px label; at a 90px cap that label wraps to **three
lines**, its band (60px) exceeds the rhombus's whole height, the span is
empty, and the finding becomes `label_adrift` at 62px. The scene stopped
being the scene. The same thing eats the base's own
`diamond_label_overflows_shape`, which is why that test is red on the base.

## What I carried, and what I did not

| item | v1 (on `d39b43d`) | v2 (re-derived on `88e04e6`) |
| --- | --- | --- |
| `entity_name_dragged_onto_its_row` | 9px overlap, `(1080.0, 0.15)`, neighbour at 5px | **unchanged — re-measured, not assumed** |
| `diamond_label_overflows_shape` band ±0.30 → ±0.05 | tightened | **DROPPED.** Derived from a reading of 12 at w=200 that this tree no longer emits (it is SILENT there). Carrying it would be asserting a number the check has stopped saying. |
| its "6/6 split" comment fix | corrected 6.5/5.5 → 6/6 | **DROPPED.** `drawn_w` is now the *wrapped* ink (62, not 172), so the ink no longer spans [14, 186] and my correction is as stale as what it replaced. |
| `_labelled_shape` `family`/`line_height` params | added | **DROPPED.** The two scenes no longer share a base in any useful way. |
| `diamond_label_measured_at_its_own_spacing` | centred label, 20 vs 12 | **REPLACED** by `dragged_label_measured_at_its_own_spacing`, 22 vs 14, on its own builder. |

`diamond_label_overflows_shape` is now byte-identical to the base. It is
an inherited red owned by `impl-band-apex`; its magnitude cannot be
re-derived until that lands, and touching it would collide with the owner.
The ±0.05 tightening and the split correction are **owed to whoever fixes
it** — both were right about `d39b43d` and neither survives the room rule.

## The discriminator survives, and here is the scene that opens the gap

The lead's hypothesis was that 1.35 becoming the default may have closed
the gap. Measured: it has not, but the gap moved. The load-bearing
mechanism:

```
cy   = t.y + max(box_h, drawn_h)/2
band = [cy - drawn_h/2, cy + drawn_h/2]
```

When `drawn_h >= box_h` the **top edge collapses to `t.y` exactly** and
only the bottom edge carries `drawn_h`. On a centred label the top edge is
the far one from the node's waist, so it decides the chord and the spacing
never reaches the answer — *no centred scene in this catalogue can witness
this fix.* Drop the label below the waist and the moving edge becomes the
deciding one.

`_dropped_label()` — a 54px "Review" (one line at any cap this family
uses) on a 200x100 rhombus at `y=70`:

| rule | band | chord | over |
| --- | --- | --- | --- |
| post-fix, `line_height_of` → 1.35 | `[70, 92]`, edges 20 and 42 below the waist | `200 x 0.16` = **32** | **22** (11 + 11) |
| pre-fix, written-in 1.25 | `[70, 90]`, edges 20 and 40 | `200 x 0.20` = **40** | **14** (7 + 7) |

Every term an integer. 22 is the drawn truth — the client paints a 22px
line box, and a reader sees 11px of "Review" hanging over empty canvas on
each side. Band ±0.15 → [18.7, 25.3].

A sweep of the space (4 strings x 3 widths x 2 heights x 23 drops) found
**62** scenes where the reading still moves; this is the one with the
roundest arithmetic and a silent neighbour.

## Proof each red is red — re-run on `88e04e6`

```
entity_name_dragged_onto_its_row              baseline: green / green
  MUTANT   under the predecessor bar (half a line box, 10px)
      RED BY ASSERTION: no finding of check='text_over_text' element='E1-label'
  MUTANT   under a dead check (bar -> 1e9)
      RED BY ASSERTION: no finding of check='text_over_text' element='E1-label'
  NEIGHBOUR under the draft's flat 4px bar
      RED BY ASSERTION: expected silence ... but it fired: 120x5px (600px²)
  NEIGHBOUR under label_label_overlap's 6px bar        green  (bracket is [5,9))

dragged_label_measured_at_its_own_spacing     baseline: green / green
  MUTANT   under the PRE-FIX rule (written-in 1.25)
      RED BY ASSERTION: fired but said 14px; expected ~22px
  NEIGHBOUR under the PRE-FIX rule                     green (cannot see it)
```

All red **by assertion**; none error-red. Bands run through the shipped
`FindingSpec.matches`: 22 ±15% rejects 14, 11, 0 and 54 while admitting
the defensible 20; 1080 ±15% rejects 120, 9, 129, 3200, 2400, 4960, and
rejects the true area when it names the covered text.

## Coverage

`text_over_text`: **UNCOVERED → proven**. Marker
`35 detectors, 31 proven, 3 render-tier, 1 UNCOVERED` →
`32 proven, 0 UNCOVERED`, moved by `livedoc.py refresh` after the guard
test failed on it, not by hand.

## Handed back

1. **The `label_overflows_shape` emitter's comment is now wrong in a new
   way**, on top of the three numbers already routed: it still says "160px
   at the edges of a 20px label, so a 171px label overhangs by 11px". On
   this tree the check wraps to `client_wrap_width` first, so on its own
   catalogue scene it measures a 3-line 62px block and emits nothing at
   all. `canvas.py` is not the curator's.
2. **No centred bound-label scene can witness the font-aware fix**, by the
   `cy - drawn_h/2` collapse above. If the owner of `impl-band-apex`
   wants a second witness, it has to be an off-centre label — worth
   knowing before another one is written.
