# Pin taxonomy — which kind you are writing, and why it matters

(Vocabulary — pin, finding, neighbour, masked, error-red — is defined in
the glossary at the end of `../SKILL.md`.)

Not every pin is a catalogue entry. The kind determines what guards it,
what its docstring must say, and whether its flip will be honest. Decide
before you write it; retrofitting the wrong encoding costs a review
round every time.

## The kinds

### Catalogue pin

The defect is expressible as your standard unit: run the checks over an
artifact and compare findings. These get the four-part record and are
guarded automatically by the harness's own meta-tests (see
`harness-design.md`).

Most pins are this. Prefer it when you can.

### Bespoke class

Some defects the catalogue's unit cannot express, because the thing that
is wrong is not a finding over an artifact:

- Emission *order* — the output is correct element-by-element and wrong
  in sequence (paint order, log ordering, migration ordering).
- Record corruption — a saved session, a checkpoint, a lockfile. It is
  not an element of the artifact; it *is* the artifact's container.
- Packaging and export completeness — what shipped, not what was
  computed.

Each of these forms a class, and **every class must carry its own
red-by-assertion companion**, because the harness's automatic guard
cannot see inside it. The companion is an ungated green test making the
same measurement the pin makes, so "companion green" is equivalent to
"the pin is failing by assertion rather than by crash".

Keep a pointer comment enumerating these classes next to the catalogue,
so a reader who finds a pin outside the catalogue knows it was
deliberate and knows what guards it. Update it in the same commit that
adds a class — a stale pointer costs a review round each time it drifts.

### Awaiting-a-check

The pin is red because the *check does not exist yet*, on purpose. This
is legitimate, and it is also how a typo'd check name sits red forever
looking exactly like deliberate future work.

Register the awaited check's name with a written reason, and add an
anti-rot test that enforces three things: every entry has a reason, no
entry is stale (its check has since landed), and every entry is actually
referenced by a pin.

**Choose this encoding only when the flip lifecycle is correct.** Ask:
when the awaited check lands, does this pin go green — and stay right
afterwards? If the pin would *still* be red after the check exists
because something else must also change, or would go green for a reason
unrelated to the check, the encoding is wrong and it will break the flip
contract. Encode the defect the pin actually describes instead.

## Outcome-pins and mechanism-pins

A pin can assert **what** goes wrong or **how**:

```
outcome  : opening the saved file fails somehow -- the user loses their work
mechanism: a null dereference in migration_list, line 696

outcome  : a request with an expired token is not served
mechanism: the response is 401 with body {"code": "token_expired"}
```

Note the crash case: an outcome-pin can have "it blows up" as its entire
content, which is not in tension with the rule that a crash *in a check*
disqualifies a silence. Different subjects — the system under test may
legitimately be pinned as crashing; the instrument measuring it may not.

Prefer outcome-pins. The fix may repair the mechanism differently than
you guessed, and a mechanism-pin then fails after a correct fix and gets
"fixed" by editing the pin — which is how a pin quietly stops meaning
anything. Wrap the crash into a named assertion so that *any* exception
satisfies the outcome claim.

Mechanism-pins are right when the mechanism is the point: a specific
error code contract, a specific ordering guarantee, a documented
exception type.

Say which kind it is in the docstring. The next reader needs to know
whether a differing failure mode means the pin is wrong or the fix is.

## Pin what is stable, guard what is about to move

Exact assertions belong on values the awaited fix will **not** change.
Liveness or type guards belong on values it **will**.

```python
# stable: the orphan entry is retained before and after the fix that
# merely reports it -- assert it exactly
assert entry.path in store.orphans

# about to move: the fix populates this mapping. Asserting == {} would
# break in the same commit that flips the pin, fighting the fix it is
# waiting for.
assert isinstance(state.referential, dict)
```

A pin that must be edited by the person fixing the bug is a pin that
lost its independence.

## Silence-shaped and firing-shaped

Which pole the pin asserts changes how it behaves under blinding, and it
is worth naming in the docstring:

- **Silence-shaped** — "the check says nothing here, and that is the
  defect." A dead check satisfies this. While red that is harmless (a
  dead check makes it pass, the flip alarm fires, you notice). Once
  flipped green it becomes the classic vacuous pin, so silence-shaped
  pins are the ones that most need re-earning after a flip.
- **Firing-shaped** — "the check should say X and says Y." A dead check
  does not satisfy this in either state.

This distinction also corrects mortality witness counts: flipping a
silence-shaped pin removes a witness with zero change to the check,
which looks exactly like a lost test. `harness-design.md` has the full
correction.

## Red for the right reason

Every pin, whatever its kind, owes three things:

1. **Watched failing before marking.** Run it, read the message, confirm
   it is the predicted mismatch and not an exception. Expected-to-fail
   markers swallow errors identically to assertion failures in most
   runners, so a pin whose fixture crashes prints exactly the same
   healthy character as one catching a real defect.
2. **Covered by a standing guard.** The harness meta-test for catalogue
   pins; a bespoke companion for each other class. The guard's job is to
   fail loudly on both "red via crash" and "secretly green — flip it".
3. **Protected against a quiet-check fix.** If a check simply stopped
   emitting, a silence-shaped pin would flip and read as a fix. The
   companion refuses that reading: an ungated green test in the same
   class asserts the check still fires on a genuine positive. A dead
   check cannot satisfy both.

Whatever CLI or runner wrapper you build, make it print each pin's
*actual* failure message with a classification — red **by assertion**
(the check answered wrongly, healthy) versus **error-red** (something
raised before the check was consulted, the pin is probably broken rather
than the code). Read the message; never accept the colour.

## Duplicates and families

Two pins exercising one defect class are usually not redundant — the
second one's distinct value or direction is the reason it exists. When
you find a real duplicate, point both at a shared base artifact rather
than deleting either, and add a semantic dedupe guard (same defect under
two names) if the catalogue is large enough to hide them.

If you are tempted to add per-author id prefixes to avoid collisions:
don't. A duplicate-id refusal at load time is a loud collision; prefixes
guarantee ids differ and convert it into a silent one. Partition by file
section instead.

## The pin's comment

Record the origin — "found during <context>, <date>" — plus one line on
what goes wrong and what fix will flip it. This is what keeps a
catalogue auditable a year later, when the person who wrote it is gone
and the pin is the only remaining evidence that the defect was real.
