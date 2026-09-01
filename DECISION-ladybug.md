# Decision — the 🐞 bug-hunter's shape (Task 37, Step 1)

**Ruled by the user, 2026-08-31. Variant B — selection first.**

This file exists because the last ruling about this feature was lost.
`.gitignore:10` ignores all of `docs/`, so
`docs/superpowers/plans/2026-08-13-v0.9-work-packages.md` — where Task 37
and its rulings live — **cannot be committed**. On 2026-08-13 the user
ruled the button should be a ladybug rather than a worm; six rounds of
spike play followed; and the only survivors today are that plan (untracked,
on one machine) and a single bug-report bundle. The client and server code
are gone from every branch. A ruling that lives only in an ignored file is
one `rm -rf` from never having happened.

## The question

What should the bug-hunter *feel* like? Three shapes were built on the real
canvas page and switched with `?variant=A|B|C` (commit `afdf4f1`, this
branch):

| | shape | primary affordance |
|---|---|---|
| A | ring-first — overlay, draw a ring, then describe | point at it |
| B | selection-first — dialog opens naming the selection; ring optional | describe it |
| C | triage panel — stays open, collects elements, file a batch | collect them |

## The verdict: B

**Why.** It cashes in the deleted spike's own finding (h): *"If the user has
something selected when hitting the ladybug, the bundle names it — that is
often the whole bug report."* Ring-first taxes every report with a drawing
step to capture something the selection usually already states.

**The ring stays, as a secondary affordance** (`◯ circle it instead`). It is
the only way to point at a problem that is not an element — text clipping,
overlap, something between two boxes. It must not be the entry gate.

**C is deferred, not rejected.** A persistent list is the right shape for a
review pass (many problems, one sitting) rather than a single report, and it
costs the registry rail its screen space. Revisit if batch filing is asked for.

## Carry into the implementation

- Do **not** promote the prototype code. It was written under prototype
  constraints — no tests, no error handling, stubbed submit.
- `/api/bugreport` exists on **no branch**. The server half was never
  committed either.
- **Capture server-side, never browser pixels.** Measured in spike round 6:
  a privacy browser poisons `toDataURL` *and* `exportToBlob` identically.
  Render from scene data; burn the ring from `ring_bbox`.
- Capture `selectedElementIds` with types and bboxes, the active artifact
  and concept, and app view state — available because the real
  implementation lives inside the React app, which the injected-JS spike
  architecturally could not reach.
- Stamp an app/version field so a stale-server bundle is identifiable.
- Show a guidance toast when ring mode is entered, naming the skip.
- Overlay styles must be specificity-proof against the host page — the
  spike's first catch was its own white-on-white Submit button.
- Bundles must self-validate their capture (`snapshot_suspect`). A bug
  reporter whose own evidence is silently garbage is the
  silence-reads-as-health failure in miniature.

## Shipped — v0.9.1, Task 37

This file arrived here by copy from `prototype-ladybug` `4a0bc60`, onto
the branch that implements it, for the same reason it was written: a
throwaway branch is deleted and a ruling that lives on one is gone. It is
now beside the code it rules.

Where each half landed:

| | |
|---|---|
| toolbar button, dialog, ring | `frontends/wysiwyg-grilling/src/components/BugHunter.tsx` |
| endpoint + bundle writer | `ServerApp.file_bugreport` in `canvas.py` |
| ring burn-in, slug, id allocation, quarantine, capture assessment | the `bug-report bundles` section of `canvas.py` |
| `canvas.py bugreports` | `cmd_bugreports` |
| what the agent is told | `references/ops-reference.md`, `references/canvas-app.md` |
| tests | `TestBugHunterBundles` in `tests/test_backend.py`; `tests/e2e/bug-hunter.spec.ts` |

**Two things were decided during the build that this file did not say.**

*The plan's Step 2 says the CLIENT assembles the rendered snapshot, via
the same export machinery the screenshot flow uses. That is incompatible
with the spike evidence in the bullet above it, and the evidence wins:*
`exportToBlob` **is one of the two calls measured to return noise.** So
the client assembles the CAPTURE REQUEST — description, selection,
camera, panels, ring in scene coordinates — and the server makes the
picture with `render_svg`, which reads the scene it already holds and
owes a browser nothing. The split moved; the field list did not.

*The bundle carries no PNG.* Rasterizing needs a headless browser
(`rasterize_svg`, snapshot tier 2), which takes seconds and can fail — a
POST handler is the wrong place for either. `scene.svg` is deterministic,
stdlib-only, and always available, so it is the evidence. A consequence
worth stating plainly: the spike's periodicity-not-flatness noise
detector has no subject here and is not implemented. If pixels are ever
re-admitted to a bundle, that detector is owed with them, and
`assess_capture` is where it goes.
