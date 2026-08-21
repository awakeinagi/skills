# Task: the drift trio (F1, F2, F3)

Agent `impl-drift-trio`, 2026-08-19. Base `b74ff27` (`v0.9_wps`), worked in
the isolated worktree `.claude/worktrees/agent-a2c77d3eb3ae20048`. Scratch
under `/tmp/impl-drift-trio/`.

> **Where this file is.** Worktree isolation refused a write to the shared
> checkout's `.superpowers/`, so this report lives at that same relative
> path *inside the worktree* and is committed with the work. Copy it across
> when the branch merges.

**Status: all three landed, three commits, suite green, gate green.**

| | commit |
|---|---|
| F1 — bound a point-strung element's box by its points | `2b19739` |
| F2 — size the ❓ pin glyph from its measured ink | `6fc69b5` |
| F3 — stop `text_dims` flooring the line box | `de7125e` |

Test summary: `1575 passed, 41 skipped, 5 xfailed, 761 subtests` — and
one former `expectedFailure` now green (below). `uvx pre-commit
run --all-files`: ruff, mypy, livedoc and backend tests all pass; the three
frontend hooks fail only because `frontends/wysiwyg-grilling/node_modules`
is not installed in this worktree, and no frontend file was touched.

---

## 0. What I re-derived, and the two places the spike was wrong

Everything below is measured on **my** base, not inherited.

Reproduced exactly: the four `max(abs(...))` sites and the fifth in
`tests/test_backend.py`'s fan helper; `{'❓': 41}` as the sole consumer of
the 1.2-em branch across 24 artifacts; 41 of 41 corpus pins at `26x26`;
6 of 194 point-strung boxes wrong; zero lint movement for all three fixes.
The self-loop reproduced as **stored 28x48 against a 52x48 span** (the
spike's `28x54 / 52x54` — the width claim, which is the one that matters,
is identical; the height differs only because my probe node is 60px tall).

**Two corrections.**

1. **The browser measures the glyph at 11.78 px, not 12** — and I measured
   it twice, two ways, agreeing. That mattered: 12 was close enough to
   mislead but is not what the client settles on.
2. **F3 does not do what the spike says it does.** The spike ranks it as
   the fix that "closes the only non-converging drift". It cannot.
   `normalize_element` rounds every stored `width`/`height` to a whole
   pixel, so a client answer of `17.5` has no representation on disk at
   all — 17 before, **18** after. Measured end-to-end through the real
   client: the drift is 0.5px before and 0.5px after. What changes is the
   *sign*. I landed it anyway, on a different and defensible
   justification, and said so in the code and the commit. **The spike's
   §5 mutant spec for F3 — "`text_dims(t, fs, lh)[1]` equals `fs * lh`
   exactly" — is unachievable through the store and must be rewritten.**

Also not in the spike: `_check_stroke`'s `height=8` literal matched
**neither** the reach (6) **nor** the span (10). It was simply a wrong
number, not a consistent application of the wrong formula.

## 1. How the measurements were made

I built a CDP harness (`/tmp/impl-drift-trio/probe_client.py`,
`cdp_ws.py`) that authors a scene through `Store.apply_batch`, serves it
with the product's own `start --no-browser`, drives a **real headless
chromium** at it, and reads the settled scene off the app's own
`window.excalidrawAPI`. That is the instrument behind every "real client"
number here. Two environment notes for whoever reuses it: the snap
chromium at `/snap/bin/chromium` is unusable (it ignores `--user-data-dir`
and aborts on the live session's `SingletonLock`) — use
`~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`; and a naive
websocket reader loses large payloads to frame fragmentation.

## 2. F1 — the point-strung box

Four sites in `canvas.py` (`_snap_geom`, `route_arrow`'s tail,
`fan_attach_points`, `_stamp_contention`) now call one helper,
`points_extent`, and `_check_stroke` derives its box from
`CHECK_STROKE_PTS` instead of writing it down alongside.

*(The spike attributed the four sites to `gate_curvature`'s revert block
and `rebuild_bound_elements`; neither carries one. The count of four is
right, the addresses were not.)*

**Blast radius, measured.** Lint across all five fixture projects: 73 → 73,
output **byte-identical**. SVG export: identical across all 24 artifacts.

**The fix works, in the real client.** Authoring a `run → run` self-loop
and a checked checkbox through `Store.apply_batch` and loading them:

| | disk before | client | disk after |
|---|---|---|---|
| self-loop | 28x48 | 52x48 | **52x48** |
| check stroke | 10x8 | 10x10 | **10x10** |

Both were mismatches; both now agree exactly, so there is no correction
left to travel back as a user edit.

**The user-facing result, through the product's own save path** — this is
the number the whole task exists for, measured on the mutant harness's own
composed screen with the client's correction applied:

```
BASE   verb_counts: {"resized": 1}            headline: resized cb-chk
FIXED  verb_counts: {"saved_no_changes": 1}   headline: saved without changing anything
```

**It flipped a red mutant green.**
`TestALineDecorationRemeasureIsNotAUserEdit::test_the_ticks_remeasured_height_is_not_a_user_resize`
was an `@unittest.expectedFailure` encoding exactly this defect. Its
docstring was deliberately written not to prejudge the repair and named
this shape as the alternative to teaching `_geometry_derived` a new role;
I took it further upstream still, so there is no drift left to suppress.
The `reordered` red beside it is untouched and still red, so the class
drops 2 → 1 in `HAND_AUTHORED_RED_CLASSES` rather than leaving it — which
that dict's own comment explicitly predicted. `livedoc.py refresh` carried
the durable-red count 6 → 5.

I fixed `tests/test_backend.py`'s fan helper as instructed.

## 3. F2 — the ❓ glyph, and why I chose the measured constant

**The choice was not actually open, and the repo already answered it.**
There is a live parity gate (`tests/test_mutants.py`,
`test_the_hug_spot_is_the_servers_hug_spot`) holding `pin_spot`'s `size`
default equal to the client's `pinSpot` default, and its own failure
message calls that number *"the box the two sides test collision
against"*. So `size` is a **placement footprint**, not an ink box. The
spike's suggestion that it should "follow" the ink is wrong: shrinking it
would change which pins fall back to the corner and **move pins on
drawings nobody asked to change** — the one outcome the brief forbids —
and would break the parity gate unless the client bundle were rebuilt too.

So I split the two concepts the shared `26` was conflating:

- `PIN_SLOT_PX = 26` — the square slot a ❓ is *placed* in. Unchanged.
- `pin_glyph_box()` — the *ink*, derived from `text_dims` rather than
  written down, centred in the slot.

The spike's "safer variant" (`autoResize: true`, let the client own the
box) is a no-op: **`autoResize: True` was already set** on both pin
authors, and the client already owns the box — it just doesn't stop the
server writing a false one. The server must write *some* box; the only
question is whether it is defensible.

On the constant's provenance, which the brief rightly insisted on:
`NUNITO_ADVANCE`'s existing note says its entries were measured by
"headless Chromium `canvas.measureText` at 100px, 2dp" against the
vendored face. I measured `❓` **by that same method**, and validated the
harness against seven existing entries first:

| char | measured | in table |
|---|---|---|
| W | 1.1040 | 1.10 |
| 0 | 0.6000 | 0.60 |
| a | 0.5330 | 0.53 |
| M | 0.8580 | 0.86 |
| i | 0.2370 | 0.24 |
| ? | 0.4470 | 0.45 |
| space | 0.2610 | 0.26 |
| **❓** | **0.5890** | *(new: 0.59)* |

Every control rounds to its table value at 2dp, so the ❓ entry is not a
new *kind* of number — it completes a table that already is the repo's
answer to "how wide is this character", and carries the same environment
caveat, stated beside it.

`_display_width` now consults the table **before** the `east_asian_width`
arm. That arm still wins for anything unmeasured, and no CJK codepoint is
in the table, so headroom is intact (`あ`, `中`, `Ａ` still read 1.2 em) —
verified.

**Blast radius, measured the way the spike did it** (corrected geometry
written into copies of all five projects, then re-linted): **73 → 73, same
set, nothing gained or lost.** Five magnitude readings move, all toward
the truth — e.g. `pin-report-page` 16px → 21px (the spike's exact number)
and `pin-rerun-unit` 8px → 14px. The spike found 2; there are 5.

**The picture does not move, and I proved it rather than reasoning about
it.** The exported `<text>` for a composed pin is byte-identical before
and after — `x='381.000000'` in both — while the stored box goes
`26x26 @368` → `14x25 @374`.

**In the real client:** the pin was `26x26 @368` against a settled
`11.78x25 @375.11` — 14.2px of width drift, 7.1px of x drift. It is now
`14x25 @374` against the same settled value: **2.2px and 1.1px, an 84%
reduction on both**, with height now exact.

I also fixed a **third** copy of the `26x26` literals the spike missed, in
`_x_user_pin` (the assessor driver posting what the rail button posts) — a
function whose own docstring warns that a driver posting what the product
does not is a finding built on a scene no user could produce.

## 4. F3 — the line box

`max(int(h), int(fs*lh))` → `max(math.ceil(h), math.ceil(fs*lh))`. Return
type unchanged (`tuple[int, int]`), so no signature churn.

The justification is **not** convergence (see §0). It is that this was the
only place the function rounded down, unexplained, two lines below a
deliberate ceil; and that under-reserving height is the direction that
loses content off the bottom rather than margin — the exact failure the
`line_height` parameter directly above it exists to prevent. Lint: 73 →
73, byte-identical.

One test expectation moved with it and toward the truth:
`TestOneLineHeightHasTwoReaders`' export pole reads **69 rather than 68**,
because a `3 × 16 × 1.35` = 64.8px block was being floored to 64 and the
old 68 had 0.8px of the last line rounded out of sight. **Both reds in
that class are unaffected and still red** — one asserts only that the
check speaks, and the other derives its magnitude from `text_dims`
precisely so that a change to the estimator moves both sides together.

## 5. What I have left for the curator

I wrote no acceptance tests. What the mutant author needs:

1. **F1's pin**, per spike §5 — and note it must build its expectation
   from `points` inline. Beyond the `test_backend.py:8694` helper I fixed,
   **there are four more copies of `max(abs(p[0]))` in `test_mutants.py`
   itself** (near lines 13825, 15058, 16289, 19716), all scene builders.
   The spike found none of them. I deliberately did not touch them —
   editing mutant scene builders is curator territory and could flip
   outcomes — but a pin seeded from any of them would be vacuous.
2. **F2's browser-tier arm.** Still needed and still the one that matters:
   the free arm now proves composer and estimator agree *by construction*
   (the composer calls `text_dims`), which is strictly stronger than
   before but still cannot see that both are wrong together. Tolerance
   should be ~2.5px: the server says 14, the client 11.78, and the gap is
   `text_dims`' deliberate `+2` pad plus rounding.
3. **F3's spec must be rewritten.** "Equals `fs * lh` exactly" is
   unachievable while `normalize_element` rounds stored geometry to whole
   pixels. The honest pin is directional: the reserved line box is never
   *less* than `fs * lh`. Its companion convergence arm ("a second load
   drifts zero rows") is also unachievable and should be dropped or
   restated with a ≤0.5px tolerance.
4. **C4's negative pin** — unchanged, still worth having.

## 6. Concerns, and one thing I could not measure

- **I could not re-derive the fixture-level `{"added": 1, "resized": 7,
  "moved": 3}`.** The real client would not settle on the `argus-r4-arm3`
  project under my harness. I got the equivalent measurement through the
  product's own save path on the mutant harness's scene instead (§2),
  which is decisive for F1's half; the remaining fabricated edits on that
  fixture are the staleness the brief puts out of scope. Flagging it
  rather than implying I checked it.
- **F3 is the weakest of the three** and I would not defend it as a bug
  fix — it is a consistency and direction-of-safety change that also moved
  a test expectation. If v0.9 wants to minimise churn in its last hours,
  F3 is the one to reconsider; F1 and F2 are unambiguous.
- **The client's own `26x26` literal at `App.tsx:1425` is still there.**
  I did not touch it: it is client code, changing it means rebuilding the
  bundle, and Excalidraw re-measures the element on creation anyway
  (`autoResize: true`), so it never persists. But it is now a *fourth*
  copy of a number the server no longer writes, and the divergence is the
  exact shape `_x_user_pin`'s docstring warns about. **Filing it plainly
  as a follow-up rather than absorbing it**, per the standing rule.
- **The 1.2-em `east_asian_width` arm now has a population of zero** in
  this corpus. It is retained for CJK headroom and I verified it still
  fires, but nothing exercises it. Worth a note in whatever inventory
  tracks unexercised branches.
