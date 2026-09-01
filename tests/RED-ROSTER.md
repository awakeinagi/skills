# Non-catalogue red classes

The hand-typed half of the sixth census guard. Its other half is
`HAND_AUTHORED_RED_CLASSES` in `tests/test_mutants.py`, and
`TestCoverage.test_the_handover_transcribes_the_hand_authored_red_classes`
compares this file against the live `@unittest.expectedFailure` decorators in
that module — in both directions and with counts. Land a red without adding
its line here and the default suite fails; name a class here that the
decorators do not carry and it fails the same way.

**Curators own this file, alongside `tests/test_mutants.py`, and own nothing
else.** It exists so that landing a hand-authored red does not mean editing
`SESSION-HANDOVER.md`, which curators must not touch. AGENTS.md's *Curation*
section is where that rule is written; this paragraph is the copy you meet at
the moment you arrive to edit. The roster lived in that handover's census
paragraph until 2026-09-01, and it moved because three curators in one
session each landed a red, each was stopped here, and each edited the
handover under protest. A roster line at the old address is now itself a
failure: this fact has exactly one home, and a correct copy beside a stale
original is the silent direction every guard in this family was built about.

**It is typed by a hand and it is never generated.** No `<!-- live: -->`
marker belongs in this file. `tests/livedoc.py`'s boundary rule is that a
marker may carry a TOTAL and may never carry a ROSTER, because `refresh`
silently repairs and a class arriving *by name* is precisely the event a
human is supposed to stop and read. A drift-repair tool cannot hold this file
as a floor either: its repair is to agree with whatever it finds, and what it
finds when the roster has been truncated is nothing.

## The grammar, which is checked and not merely described

Everything below the label at the foot of this file is the roster. Every line
down there is one entry, written as a `-` bullet: the class name in
backticks, then that class's red count in parentheses. Order does not matter,
which is the point of the move — two curators land on two lines instead of
serialising on one sentence.

- **An empty roster is written `- *none*`,** never as a blank. The positive
  spelling is what makes "there are no non-catalogue reds" mean it, rather
  than meaning "this file was truncated and nobody noticed": at zero reds
  those two look identical to any reader that does not demand the word.
- **A class whose LAST red flips leaves the roster entirely** rather than
  dropping to `(0)`. The derivation side never emits a zero, so a `(0)` entry
  is refused here with that rule named, instead of failing later as two
  dicts that differ.
- **Every class name in this file is backticked, above the label as well as
  below it.** Every check reads what is inside backticks, so a name that lost
  its ticks is read by nothing at all — which is the hole a review found in
  the first draft of the reader, and one reflow is all it takes.
- **Nothing but entries goes below the label.** Prose belongs above it. A
  line the grammar cannot read is refused rather than skipped, because a
  skipped line reads exactly like a roster that never had it.

Non-catalog red classes:

- `TestALandedLabelPositionNarratesThroughEitherDoor` (2)
- `TestAClosedPinSaysWhyItClosed` (2)
