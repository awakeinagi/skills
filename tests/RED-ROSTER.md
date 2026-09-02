# Red roster

Both hand-typed halves of this repo's red census, in a file that holds
nothing else. Each section below is compared against the live
`@unittest.expectedFailure` decorators by a guard in `tests/test_mutants.py`,
in both directions, so a red that lands without its line here fails the
default suite and so does a line naming a red that is not there.

| this file's section | the declaration beside it | the guard |
|---|---|---|
| non-catalogue red classes | `HAND_AUTHORED_RED_CLASSES` | `TestCoverage.test_the_handover_transcribes_the_hand_authored_red_classes` |
| catalogue red ids | `CATALOGUE_RED_IDS` | `TestCoverage.test_the_handover_transcribes_the_reds_it_declares` |

**Curators own this file, alongside `tests/test_mutants.py`, and own nothing
else.** It exists so that landing a red does not mean editing
`SESSION-HANDOVER.md`, which curators must not touch. AGENTS.md's *Curation*
section is where that rule is written; this paragraph is the copy you meet at
the moment you arrive to edit. Both rosters lived in that handover until
2026-09-01. A roster line at either old address is now itself a guard
failure: each fact has exactly one home, and a correct copy beside a stale
original is the silent direction every guard in this family was built about.

**They are typed by a hand and they are never generated.** No `<!-- live: -->`
marker belongs in this file. `tests/livedoc.py`'s boundary rule is that a
marker may carry a TOTAL and may never carry a ROSTER, because `refresh`
silently repairs and a red arriving *by name* is precisely the event a human
is supposed to stop and read. A drift-repair tool cannot hold this file as a
floor either: its repair is to agree with whatever it finds, and what it
finds when a roster has been truncated is nothing.

## The grammar, which is checked and not merely described

Everything below a label, down to the next heading, is that label's roster.
Every line down there is one entry written as a `-` bullet: the subject in
backticks, and for a class its red count in parentheses. Order does not
matter, which is the point of the move — two curators land on two lines
instead of serialising on one sentence.

- **An empty roster is written `- *none*`,** never as a blank. The positive
  spelling is what makes "there are no reds of this kind" mean it, rather
  than meaning "this section was truncated and nobody noticed": at zero those
  two look identical to any reader that does not demand the word.
- **A class whose LAST red flips leaves the roster entirely** rather than
  dropping to `(0)`. The derivation side never emits a zero, so a `(0)` entry
  is refused here with that rule named, instead of failing later as two dicts
  that differ. A catalogue id carries no count at all: it is red or it is not.
- **Every name in this file is backticked, above a label as well as below
  it.** Every check reads what is inside backticks, so a name that lost its
  ticks is read by nothing at all — which is the hole a review found in the
  first draft of this reader, and one reflow is all it takes. Class names are
  caught by their shape; catalogue ids are caught by being looked up in the
  catalogue itself, since no pattern can tell a lowercase id from prose.
- **Nothing but entries goes below a label.** Prose belongs above it, under
  the section's own heading. A line the grammar cannot read is refused rather
  than skipped, because a skipped line reads exactly like a roster that never
  had it.

## Non-catalogue red classes

Reds written as ordinary test classes in `tests/test_mutants.py` rather than
as catalogue entries, because their subject is prose, a document or an
instrument rather than a scene. The count is that class's number of red
methods. A red in `tests/test_backend.py` or `tests/test_mutants_render.py`
is outside this section's reach, which the guard's own docstring says.

Non-catalog red classes:

- `TestALandedLabelPositionNarratesThroughEitherDoor` (2)
- `TestACorrectionWaveHeadlinesWhatSomebodyAuthored` (2)
- `TestAStoredCaptionAnchorTheClientDisagreesWithIsReported` (1)
- `TestOnePairGetsOneVerdictOnWhetherItMayStand` (2)
- `TestAClosedPinSaysWhyItClosed` (2)
- `TestAShadedBoxKeepsTheLineWrittenOnIt` (1)
- `TestAnAnsweredPinIsNotNarratedAsARemoval` (2)
- `TestAResolvedPinSaysWhetherItsAnswerReachedTheDrawing` (2)
- `TestAConfigChangeSaysWhoseHandTurnedIt` (2)
- `TestAStaleBrowserCaptureIsNotConsumedInSilence` (2)

## Catalogue red ids

Reds declared by a mutant in the catalogue, one id per line, matching the
ids `mutants list --red` prints.

These ids were the model row of the handover's catalog-reds table until
2026-09-01, and they left for a reason the classes above did not share: that
row is a long narrative about *why* each entry was red, and the guard
harvested every backticked token in it as a live id. So the narrative could
not spell anything in backticks. The row escaped its own subjects' underscores
and carried the warning "Backtick an id here ONLY as a live red" twice, in a
cell that also quotes source code. A prose author paying a tax to avoid
tripping a census reader is a design defect and not a discipline. Here the
roster is a roster, and the row is prose again.

Catalog red ids:

- `forked_feet_read_through_node`
- `joined_feet_read_through_node`
