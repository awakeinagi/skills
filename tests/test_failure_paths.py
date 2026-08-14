"""WP1 acceptance: what a failure leaves behind.

Every case here follows one discipline — *cause a failure, then diff the
state*. A test that only asserts the error message passes just as
happily when the rejected batch has already written half of itself into
the live registry, and that is exactly how r5-8 shipped: a batch
rejected for a typo'd `set_round` kept the concept its earlier op had
added, served it on `/api/state`, and the next commit persisted it. So
each case snapshots what a caller can observe — the in-memory registry,
the registry file, the artifact file — provokes the rejection, and
asserts the snapshot is unchanged afterwards.

The second class reads the same discipline through durability rather
than atomicity. r5-18's failure is a *restart*, and the state to diff
is the pending queue across two server lifetimes: the old queue lived
only in `ServerApp.pending`, so the documented shutdown destroyed every
revision waiting behind the banner and said nothing at stop, at start,
or after.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas


def seed_batch() -> dict[str, Any]:
    """A two-node flow with a concept, so mappings have something to name.

    Returns:
        An op-batch envelope creating the `checkout-flow` artifact.
    """
    return {
        "base_revn": 0,
        "artifact": "checkout-flow",
        "create": {"id": "checkout-flow", "name": "Checkout Flow",
                   "type": "flow", "concept": "checkout",
                   "concept_name": "Checkout"},
        "ops": [{"op": "add", "element": {
            "type": "rectangle", "id": "cart", "label": "Cart", "x": 40,
            "y": 120, "width": 140, "height": 60, "role": "node"}},
            {"op": "add", "element": {
                "type": "rectangle", "id": "checkout", "label": "Checkout",
                "x": 260, "y": 120, "width": 140, "height": 60,
                "role": "node"}}],
    }


class TestFailurePathAtomicity(unittest.TestCase):
    """A rejected batch must leave the state exactly as it found it."""

    def setUp(self) -> None:
        """Build a store over a temp project, seed one artifact and one pin.

        The pin is here rather than in `seed_batch`, which two other
        classes share: only the sweep needs it, and it needs it to exist
        BEFORE the batch under test, because `_validate_batch` reads the
        registry once at the top. A shape whose leading legitimate op is
        a `resolve_pin` is the one write path the sweep could not reach
        without it, and it is the widest of them — the resolution rides
        into the registry write-through and into every artifact holding
        that ❓.
        """
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-fail-"))
        self.project = canvas.Project(self.tmp)
        self.project.ensure_tree()
        self.store = canvas.Store(self.project)
        self.store.apply_batch(seed_batch())
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(),
             "artifact": "checkout-flow",
             "ops": [{"op": "pin", "id": "pin-live", "target": "cart",
                      "question": "one cart, or one per seller?"}]})

    def tearDown(self) -> None:
        """Drop the temp project and the process-level side files."""
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p in (self.project.state_path, self.project.events_path,
                  self.project.log_path):
            if p.exists():
                p.unlink()

    def snapshot(self) -> tuple[str, bytes]:
        """Both observable copies of the registry.

        Returns:
            `(the live registry as sorted JSON, the registry file bytes)`.
        """
        return (json.dumps(self.store.registry, sort_keys=True),
                self.project.registry_path.read_bytes())

    def mapping_then_round(self, round_value: Any) -> dict[str, Any]:
        """A registry-only batch: add a mapping, then set the round.

        Args:
            round_value: What `set_round` carries. A non-integer makes
                the batch illegal, and the mapping added by the op
                before it is then the write that must not survive.

        Returns:
            An op-batch envelope on the current head revision.
        """
        return {"base_revn": self.store.head_revn(),
                "artifact": "checkout-flow",
                "ops": [{"op": "registry", "action": "add_mapping",
                         "concept": "checkout",
                         "elements": ["checkout-flow#cart"],
                         "note": "the cart screen"},
                        {"op": "registry", "action": "set_round",
                         "round": round_value}]}

    def test_rejected_batch_leaves_the_registry_byte_identical(self) -> None:
        """One bad op rejects the batch, including its good ops' writes."""
        before_mem, before_disk = self.snapshot()
        with self.assertRaises(canvas.BatchError) as caught:
            self.store.apply_batch(self.mapping_then_round("two"))
        self.assertTrue(any("set_round" in e for e in caught.exception.errors))
        after_mem, after_disk = self.snapshot()
        self.assertEqual(after_mem, before_mem,
                         "the valid op's write leaked into the live registry")
        self.assertEqual(after_disk, before_disk,
                         "the rejected batch reached model.json")

    def test_rejected_batch_is_not_persisted_by_the_next_commit(self) -> None:
        """The measured consequence: the next commit persisted the leak."""
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch(self.mapping_then_round("two"))
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "checkout-flow",
             "ops": [{"op": "registry", "action": "set_round", "round": 3}]})
        on_disk = json.loads(self.project.registry_path.read_text("utf-8"))
        self.assertEqual(on_disk["round"], 3)
        self.assertEqual(on_disk["mappings"], [],
                         "the rejected batch's mapping rode along on the "
                         "next commit")

    def test_rejected_rename_leaves_the_artifact_file_untouched(self) -> None:
        """The name a rejected batch asked for reaches neither meta nor file.

        `rename_artifact` is the one registry op whose new name also
        belongs in the artifact FILE, and it used to write it there as
        it validated — so the rejection below arrived with the rename
        already on disk, and the guard had to write the old name back.
        The write-through now waits for `commit`'s persist block, which
        a rejected batch never reaches; this holds either way, which is
        the point of asserting the bytes rather than the mechanism.
        """
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        before = path.read_bytes()
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch(
                {"base_revn": self.store.head_revn(),
                 "artifact": "checkout-flow",
                 "ops": [{"op": "registry", "action": "rename_artifact",
                          "artifact": "checkout-flow", "name": "Renamed"},
                         {"op": "registry", "action": "set_round",
                          "round": "two"}]})
        self.assertEqual(self.store.artifact_meta["checkout-flow"]["name"],
                         "Checkout Flow")
        self.assertEqual(path.read_bytes(), before,
                         "a rejected rename stayed in the artifact file")

    def test_rejected_batch_leaves_the_pin_lifecycle_untouched(self) -> None:
        """The registry ops are not the only write ahead of the rejection.

        `commit` runs the pin lifecycle — prune on target deletion,
        `target_edits` on target churn — BEFORE it dispatches the
        registry ops, so the guard has to sit ahead of both. With it
        moved down to the ops, this batch still pruned a pin the user
        never lost and aged another one for an edit that never landed.
        """
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "checkout-flow",
             "ops": [{"op": "pin", "target": "cart", "id": "pin-a",
                      "question": "one cart, or one per seller?"},
                     {"op": "pin", "target": "checkout", "id": "pin-b",
                      "question": "guest checkout?"}]})
        before_pins = json.dumps(self.store.registry["pins"], sort_keys=True)
        before_meta = json.dumps(self.store.artifact_meta, sort_keys=True)
        before = self.snapshot()
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch(
                {"base_revn": self.store.head_revn(),
                 "artifact": "checkout-flow",
                 "ops": [{"op": "del", "id": "cart"},
                         {"op": "mod", "id": "checkout",
                          "attrs": {"label": "Checkout & pay"}},
                         {"op": "registry", "action": "set_round",
                          "round": "two"}]})
        self.assertEqual(json.dumps(self.store.registry["pins"],
                                    sort_keys=True), before_pins,
                         "the rejected batch pruned or aged a pin")
        self.assertEqual(json.dumps(self.store.artifact_meta, sort_keys=True),
                         before_meta)
        self.assertEqual(self.snapshot(), before)

    def test_a_crashing_registry_op_leaves_nothing_behind(self) -> None:
        """The promise is "no trace", so the guard cannot ask what raised.

        A guard that catches only `BatchError` never runs when an op
        dies of something else, and the half-written registry escapes
        with the exception. This once rode a real defect as its crash —
        `annotate_mapping` with `index: -1`, which subscripted an empty
        list — and that defect is now fixed, which is precisely why the
        crash here is SYNTHETIC: the subject is the guard's breadth, and
        pinning it to a live bug meant the pin died the day the bug did.
        `_parse_kinds` is raised through because `add_mapping` calls it
        mid-op, after the two ops before it have already written.

        The ops before the crash are chosen to write in both places the
        guard restores: `rename_artifact` mutates `artifact_meta` (and
        is the one op whose new name also belongs in the artifact FILE),
        `set_round` mutates the registry.
        """
        before = self.snapshot()
        before_meta = json.dumps(self.store.artifact_meta, sort_keys=True)
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        before_file = path.read_bytes()
        boom = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.object(self.store, "_parse_kinds", boom), \
                self.assertRaises(RuntimeError):
            self.store.apply_batch(
                {"base_revn": self.store.head_revn(),
                 "artifact": "checkout-flow",
                 "ops": [{"op": "registry", "action": "rename_artifact",
                          "artifact": "checkout-flow", "name": "Renamed"},
                         {"op": "registry", "action": "set_round",
                          "round": 9},
                         {"op": "registry", "action": "add_mapping",
                          "concept": "checkout",
                          "elements": ["checkout-flow#cart"]}]})
        self.assertTrue(boom.called, "the batch never reached the crash — "
                                     "this pins nothing")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(json.dumps(self.store.artifact_meta, sort_keys=True),
                         before_meta)
        self.assertEqual(path.read_bytes(), before_file,
                         "the crash left the rename in the artifact file")

    def test_checking_the_same_batch_leaves_the_registry_byte_identical(
            self) -> None:
        """The dry run reports the same rejection and writes nothing."""
        before = self.snapshot()
        out = self.store.check_batch(self.mapping_then_round("two"))
        self.assertFalse(out["ok"])
        self.assertTrue(any("set_round" in e for e in out["errors"]))
        self.assertEqual(self.snapshot(), before)

    def test_accepted_batch_applies_every_registry_op(self) -> None:
        """Control: staging must not cost the good path its writes."""
        self.store.apply_batch(self.mapping_then_round(7))
        self.assertEqual(self.store.registry["round"], 7)
        self.assertEqual([m["elements"] for m in self.store.registry["mappings"]],
                         [["checkout-flow#cart"]])
        on_disk = json.loads(self.project.registry_path.read_text("utf-8"))
        self.assertEqual(on_disk["round"], 7)
        self.assertEqual(len(on_disk["mappings"]), 1)

    def test_resending_a_fixed_batch_does_not_duplicate_the_mapping(
            self) -> None:
        """Control: the rejected attempt left nothing for the retry to hit."""
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch(self.mapping_then_round("two"))
        self.store.apply_batch(self.mapping_then_round(7))
        self.assertEqual(len(self.store.registry["mappings"]), 1)
        self.assertEqual(self.store.registry["round"], 7)

    # -- the sweep ---------------------------------------------------------
    # Everything above is a case someone remembered to write, and each one
    # watches the surfaces that mattered to the defect it came from. That is
    # how the rejection shapes added since — a negative mapping index, a
    # boolean one, a reused pin id, an op that is not an object — arrived
    # with no atomicity case at all: they were fixed as REFUSALS, and
    # nobody's checklist said "and now diff the state". The sweep closes
    # that by running every known rejection shape through one comparison of
    # everything observable, so the next op to grow a write is caught by a
    # test nobody had to remember to write.

    def full_snapshot(self) -> str:
        """Every observable copy of the state, as one comparable blob.

        The cases above each diff one or two surfaces — the live
        registry, the registry file, the artifact file, the pin list.
        This diffs all of them at once, including the bytes of every
        file under `project_knowledge/`, because a sweep whose reach is
        narrower than the writes it is sweeping for would go quiet
        exactly where a new write appeared.

        Returns:
            JSON holding the in-memory registry, artifact meta, scenes
            and head revision, plus every file under the project root
            keyed by relative path.
        """
        disk = {str(p.relative_to(self.tmp)): p.read_bytes().hex()
                for p in sorted(self.tmp.rglob("*")) if p.is_file()}
        return json.dumps({"registry": self.store.registry,
                           "meta": self.store.artifact_meta,
                           "scenes": self.store.scenes,
                           "head": self.store.head_revn(), "disk": disk},
                          sort_keys=True, default=str)

    def rejection_shapes(self) -> list[tuple[str, dict[str, Any]]]:
        """Every batch shape the tool is known to refuse, one each.

        Every shape carries a legitimate op BEFORE its illegal one, and
        that is the whole discipline: a batch that is rejected on its
        first op has nothing half-written to leave behind, so it would
        sweep clean however leaky the path is. The write that must not
        survive is the one the good op made.

        The list is deliberately shape-by-shape rather than one entry
        per fix, since two refusals from the same predicate can still
        reach different write paths — `add_mapping` writes the registry,
        `add` writes the scene, `pin` writes both.

        Returns:
            `(name, batch)` pairs on the current head revision.
        """
        base = self.store.head_revn()
        mapping = {"op": "registry", "action": "add_mapping",
                   "concept": "checkout",
                   "elements": ["checkout-flow#cart"]}
        node = {"op": "add", "element": {
            "type": "rectangle", "id": "extra", "label": "Extra", "x": 480,
            "y": 120, "width": 140, "height": 60, "role": "node"}}

        def batch(*ops: Any) -> dict[str, Any]:
            """Wrap ops in an envelope on the seeded artifact.

            Args:
                ops: The batch's ops, in order.

            Returns:
                An op-batch envelope.
            """
            return {"base_revn": base, "artifact": "checkout-flow",
                    "ops": list(ops)}

        return [
            ("a non-integer round",
             batch(mapping, {"op": "registry", "action": "set_round",
                             "round": "two"})),
            ("a negative mapping index",
             batch(mapping, {"op": "registry", "action": "annotate_mapping",
                             "index": -1, "note": "boom"})),
            ("a boolean mapping index",
             batch(mapping, {"op": "registry", "action": "remove_mapping",
                             "index": True})),
            ("a mapping index past the end",
             batch(mapping, {"op": "registry", "action": "annotate_mapping",
                             "index": 7, "note": "boom"})),
            ("a negative reorder index",
             batch(node, {"op": "reorder", "id": "cart", "index": -1})),
            ("a non-integer reorder index",
             batch(node, {"op": "reorder", "id": "cart", "index": "1"})),
            ("a reused pin id",
             batch({"op": "pin", "target": "cart", "id": "pin-dup",
                    "question": "one cart, or one per seller?"},
                   {"op": "pin", "target": "checkout", "id": "pin-dup",
                    "question": "guest checkout?"})),
            ("an unknown pin resolve",
             batch(mapping, {"op": "resolve_pin", "id": "pin-nobody",
                             "answer": "sure"})),
            # v0.9 Task 40. The only shape whose leading legitimate ops
            # are a RESOLVE and a `create`, which is what earns it an
            # entry rather than folding into the reused-pin-id one above:
            # a resolution reaches further than any other write here —
            # the registry write-through marks the record, and the
            # cross-artifact scan takes the ❓ off every artifact carrying
            # the id — and a `create` writes a whole artifact file. A
            # coherence check running even slightly later than validation
            # would leave a closed question, a vanished glyph and a new
            # artifact behind while still answering "refused".
            # The ❓ is re-drawn onto the artifact being CREATED and not
            # onto the pin's own, which is the difference between pinning
            # this refusal and pinning an older one: on `checkout-flow`
            # the same batch also trips "id already exists in this
            # artifact", so it would sweep clean with the Task 40 check
            # deleted and quietly stop covering it.
            ("a resolve re-drawn under its own id",
             {"base_revn": base, "artifact": "second-view",
              "create": {"id": "second-view", "name": "Second View",
                         "type": "flow"},
              "ops": [mapping,
                      {"op": "resolve_pin", "id": "pin-live",
                       "answer": "one"},
                      {"op": "add", "element": {
                          "type": "text", "id": "pin-live", "text": "❓",
                          "x": 600, "y": 120, "width": 20, "height": 25,
                          "role": "pin"}}]}),
            ("an unknown tripwire",
             batch(mapping, {"op": "registry", "action": "resolve_tripwire",
                             "id": "tw-nobody"})),
            ("an unknown element to modify",
             batch(node, {"op": "mod", "id": "nobody",
                          "attrs": {"label": "boom"}})),
            # crash-shaped until v0.9 Task 38: `check_batch` and
            # `apply_batch` both raised a bare `AttributeError` on this,
            # and it swept here anyway because the claim is about STATE,
            # not about which way the batch resolved — a crash that
            # leaves half a batch behind is the worse version of the same
            # wound. It is an ordinary refusal now (pinned in
            # tests/test_mutants.py), and the entry stays because the
            # state claim is the one this sweep makes about every shape.
            ("an op that is not an object", batch(mapping, "just a string")),
        ]

    def test_every_rejection_shape_leaves_the_state_byte_identical(
            self) -> None:
        """The sweep: nothing a refused batch touched may survive it.

        `Exception` rather than `BatchError` deliberately — the promise
        is "no trace", which a batch that died of something else breaks
        just as badly, and pinning the type here would have excluded the
        crash-shaped shape that most needs the check.

        The "was ACCEPTED" assertion is what stops this from decaying
        into a test of nothing: a shape the tool stops refusing writes
        legitimately, sweeps clean, and would otherwise read as a pass
        while covering no rejection at all.
        """
        before = self.full_snapshot()
        for name, batch in self.rejection_shapes():
            with self.subTest(shape=name):
                escaped = None
                try:
                    self.store.apply_batch(batch)
                except Exception as exc:
                    escaped = exc
                self.assertIsNotNone(
                    escaped, "%s was ACCEPTED — this shape no longer pins a "
                    "rejection, so fix the batch or drop the entry" % name)
                self.assertEqual(
                    self.full_snapshot(), before,
                    "%s left a write behind (%s: %s)"
                    % (name, type(escaped).__name__, escaped))

    def test_every_rejection_shape_dry_runs_without_writing(self) -> None:
        """The same sweep through `--check`, which may not write either.

        `check_batch` stages the same ops against the same store, so
        every write path the sweep above walks is reachable from the dry
        run too — and an agent that asked "would this land?" and got a
        half-applied registry would have no reason to suspect it.

        A raise is tolerated HERE and only here: whether the dry run is
        allowed to raise at all is a different claim, pinned separately
        in tests/test_mutants.py. This one asks only that it wrote
        nothing on its way out, and that whenever it DID answer, it
        answered no.

        The `suppress` below covers the CALL and nothing else, and no
        assertion may ever move inside it: an `AssertionError` is an
        `Exception`, so a suppress wrapped around the verdict as well
        swallowed the verdict. That was this test's first draft, and a
        `check_batch` returning `ok` for every shape passed it. The
        sentinel is how the answer gets judged out here instead — absent
        because the call raised, which is tolerated, is a different
        thing from present and wrong, which is not.
        """
        missed = object()
        before = self.full_snapshot()
        for name, batch in self.rejection_shapes():
            with self.subTest(shape=name):
                out: Any = missed
                with contextlib.suppress(Exception):
                    out = self.store.check_batch(batch)
                self.assertEqual(self.full_snapshot(), before,
                                 "%s wrote something during a DRY RUN" % name)
                if out is not missed:
                    self.assertFalse(out["ok"],
                                     "%s dry-ran as acceptable: %r"
                                     % (name, out))

    def test_the_sweep_notices_an_accepted_batch(self) -> None:
        """The sweep's live pole: the snapshot really can tell writes apart.

        Without it, a `full_snapshot` that returned a constant would
        pass every shape above forever. Each half is asserted BY NAME
        rather than through the blob, because the halves fail
        independently: a snapshot whose `disk` map went empty still
        differs across an accepted batch on its in-memory half alone,
        and the disk half is the one carrying the files a rejected batch
        must not reach.

        The batch writes in both places on purpose — the `mod` rewrites
        the artifact file, the `set_round` rewrites model.json — so a
        snapshot that stopped reading either one is named here.
        """
        art = str((self.project.artifacts_dir /
                   "checkout-flow.excalidraw").relative_to(self.tmp))
        reg = str(self.project.registry_path.relative_to(self.tmp))
        before = json.loads(self.full_snapshot())
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(),
             "artifact": "checkout-flow",
             "ops": [{"op": "mod", "id": "cart",
                      "attrs": {"label": "Basket"}},
                     {"op": "registry", "action": "set_round", "round": 7}]})
        after = json.loads(self.full_snapshot())
        for path in (art, reg):
            self.assertIn(path, before["disk"],
                          "the snapshot stopped reading %s — every rejection "
                          "shape above now sweeps that file blind" % path)
            self.assertNotEqual(
                after["disk"][path], before["disk"][path],
                "an accepted batch rewrote %s and the snapshot did not "
                "notice" % path)
        self.assertNotEqual(after["registry"], before["registry"])
        self.assertNotEqual(after["scenes"], before["scenes"])


class TestCrossArtifactPinResolution(unittest.TestCase):
    """A pin is resolved where it LIVES, not where the batch is aimed.

    The third failure path of the same shape: `resolve_pin` looked the
    ❓ up in the batch artifact's index, so a pin living anywhere else
    fell through the tolerant branch — the registry recorded the
    resolution and the glyph stayed drawn forever. `status` then said
    `OPEN_PINS=3` with four glyphs on the canvas, and SKILL.md instructs
    exactly the shape that triggers it ("the batch that executes an
    answer also carries its mirrored pin's `resolve_pin`" plus "one
    batch = one artifact"). The state to diff here is the canvas against
    the registry: they must agree about how many questions are open
    (r5-17).
    """

    def setUp(self) -> None:
        """Two artifacts, and one open pin whose ❓ sits on the first."""
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-pin-"))
        self.project = canvas.Project(self.tmp)
        self.project.ensure_tree()
        self.store = canvas.Store(self.project)
        self.store.apply_batch(seed_batch())
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "payments",
             "create": {"id": "payments", "name": "Payments", "type": "flow"},
             "ops": [{"op": "add", "element": {
                 "type": "rectangle", "id": "card", "label": "Card", "x": 40,
                 "y": 40, "width": 140, "height": 60, "role": "node"}}]})
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "checkout-flow",
             "ops": [{"op": "pin", "target": "cart", "id": "pin-a",
                      "question": "one cart, or one per seller?"}]})

    def tearDown(self) -> None:
        """Drop the temp project and the process-level side files."""
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p in (self.project.state_path, self.project.events_path,
                  self.project.log_path):
            if p.exists():
                p.unlink()

    def glyphs(self) -> set[str]:
        """Every ❓ standing on any canvas.

        Returns:
            The ids of the pin elements across all scenes.
        """
        return {e["id"] for els in self.store.scenes.values() for e in els
                if (e.get("customData") or {}).get("role") == "pin"}

    def open_pins(self) -> set[str]:
        """Every question the registry still counts as unanswered.

        Returns:
            The ids of the registry pins in an open or answered state —
            what `OPEN_PINS` reports.
        """
        return {p["id"] for p in self.store.registry["pins"]
                if p.get("status") in ("open", "answered")}

    def drawing(self) -> str:
        """Both scenes minus their pins, as a comparable string.

        Returns:
            Sorted JSON of every non-pin element in every artifact, so a
            case can assert that resolving a pin edited nothing else.
        """
        return json.dumps(
            {aid: [e for e in els
                   if (e.get("customData") or {}).get("role") != "pin"]
             for aid, els in self.store.scenes.items()}, sort_keys=True)

    def resolve_from_payments(self) -> tuple[dict[str, Any], bool]:
        """Resolve `pin-a` from a batch scoped to the OTHER artifact.

        Returns:
            What `apply_batch` returns — `(record, pin_only)`.
        """
        return self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "payments",
             "ops": [{"op": "resolve_pin", "id": "pin-a"}]})

    def test_a_foreign_batch_removes_the_glyph_where_it_lives(self) -> None:
        """The measured failure: registry resolved, ❓ still drawn."""
        self.assertEqual(self.glyphs(), {"pin-a"})
        self.resolve_from_payments()
        self.assertEqual(self.glyphs(), set(),
                         "the ❓ survived a resolve aimed at another "
                         "artifact")
        self.assertEqual(self.glyphs(), self.open_pins(),
                         "the canvas and the registry disagree about how "
                         "many questions are open")

    def test_the_glyph_leaves_the_artifact_file_too(self) -> None:
        """In memory is not enough — the drawing on disk is the drawing."""
        self.resolve_from_payments()
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        on_disk = json.loads(path.read_text("utf-8"))
        self.assertEqual([e["id"] for e in on_disk["elements"]
                          if (e.get("customData") or {}).get("role") == "pin"],
                         [], "the resolved ❓ is still in the artifact file")

    def test_a_foreign_resolve_edits_nothing_else(self) -> None:
        """Reaching into another artifact must touch only that one glyph."""
        before = self.drawing()
        self.resolve_from_payments()
        self.assertEqual(self.drawing(), before,
                         "resolving a pin moved or rewrote real elements")

    def test_the_registry_records_it_as_resolved_not_dismissed(self) -> None:
        """Deleting the glyph IS the resolution, from either artifact.

        `commit`'s pin lifecycle reads a deleted pin element as the
        user's "not worth explaining" dismissal unless the batch also
        resolved it, and the cross-artifact deletion has to arrive
        inside that same window to be told apart.
        """
        self.resolve_from_payments()
        self.assertEqual([p["status"] for p in self.store.registry["pins"]],
                         ["resolved"])

    def test_an_already_deleted_glyph_resolves_quietly(self) -> None:
        """The silent half: the user deleted the ❓ first.

        The tolerant branch is kept for exactly this — a pin whose glyph
        is genuinely gone must still be resolvable, with no error and no
        invented element edit. What changes is that it now SAYS so
        instead of resolving in silence.
        """
        self.store.commit(
            author="user",
            new_scenes={"checkout-flow":
                        [e for e in self.store.scenes["checkout-flow"]
                         if e["id"] != "pin-a"]})
        self.assertEqual(self.glyphs(), set())
        before = self.drawing()
        record, _ = self.resolve_from_payments()
        self.assertEqual(self.open_pins(), set())
        self.assertEqual(self.drawing(), before)
        self.assertEqual(
            canvas.pin_glyph_notes(record,
                                   [{"op": "resolve_pin", "id": "pin-a"}]),
            ["pin pin-a resolved; its ❓ was already gone"])

    def test_a_resolve_that_did_delete_the_glyph_says_nothing(self) -> None:
        """Control: the note belongs to the absent glyph, not to every one."""
        record, _ = self.resolve_from_payments()
        self.assertEqual(
            canvas.pin_glyph_notes(record,
                                   [{"op": "resolve_pin", "id": "pin-a"}]), [])

    def test_two_pins_on_one_foreign_artifact_both_go(self) -> None:
        """The second resolve has to build on the first one's scene.

        Both glyphs live on the same foreign artifact, so a loop that
        re-reads the stored scene each time would put the first ❓ back
        and only the last resolve would stick.
        """
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "checkout-flow",
             "ops": [{"op": "pin", "target": "checkout", "id": "pin-b",
                      "question": "guest checkout?"}]})
        self.assertEqual(self.glyphs(), {"pin-a", "pin-b"})
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "payments",
             "ops": [{"op": "resolve_pin", "id": "pin-a"},
                     {"op": "resolve_pin", "id": "pin-b"}]})
        self.assertEqual(self.glyphs(), set())
        self.assertEqual(self.open_pins(), set())

    def test_an_id_shadowing_add_cannot_hide_the_foreign_pin(self) -> None:
        """The skip has to test what the scan tests, or it reopens r5-17.

        The scan reaches across artifacts for a PIN; the guard that
        skips it asked only whether the batch artifact's post-op scene
        still holds that id. So one batch that resolves a foreign pin
        and then adds any element reusing the pin's id put the id back
        into scope, skipped the scan, and stranded the ❓ exactly as
        before — registry resolved, glyph drawn, the count parity broken
        again. Order matters: the add has to land after the resolve,
        because `apply_ops` would otherwise delete the shadow itself.
        """
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "payments",
             "ops": [{"op": "resolve_pin", "id": "pin-a"},
                     {"op": "add", "element": {
                         "type": "rectangle", "id": "pin-a",
                         "label": "Shadow", "x": 240, "y": 200,
                         "width": 140, "height": 60, "role": "node"}}]})
        self.assertEqual(self.glyphs(), set(),
                         "an add reusing the pin's id hid the foreign ❓")
        self.assertEqual(self.glyphs(), self.open_pins())
        self.assertEqual([e.get("id") for e in self.store.scenes["payments"]
                          if e.get("type") == "rectangle"], ["card", "pin-a"],
                         "the shadowing add was swallowed by the scan")

    def test_a_pin_record_that_lost_its_artifact_still_resolves(self) -> None:
        """Nothing validates that key, so it cannot be the only witness.

        `validate_registry` fills in the registry's top-level lists and
        stops there — a pin record is never checked field by field. A
        home read from the record alone would strand the ❓ of any pin
        whose entry was hand-edited or written by an older version,
        which is the very failure this is here to prevent. The glyph is
        on a canvas; that is where to look for it.
        """
        del self.store.registry["pins"][0]["artifact"]
        self.resolve_from_payments()
        self.assertEqual(self.glyphs(), set())
        self.assertEqual(self.open_pins(), set())

    def test_a_same_id_element_elsewhere_is_not_collateral(self) -> None:
        """Ids are minted per scene, so the same id can mean two things.

        Reaching across artifacts by id has to reach for a PIN. A third
        artifact holding an ordinary node that happens to share the id
        must come through the resolve untouched.
        """
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "shipping",
             "create": {"id": "shipping", "name": "Shipping", "type": "flow"},
             "ops": [{"op": "add", "element": {
                 "type": "rectangle", "id": "pin-a", "label": "Courier",
                 "x": 40, "y": 40, "width": 140, "height": 60,
                 "role": "node"}}]})
        self.resolve_from_payments()
        self.assertEqual([e["id"] for e in self.store.scenes["shipping"]
                          if e.get("type") == "rectangle"], ["pin-a"],
                         "the resolve deleted a node that merely shares "
                         "the pin's id")
        self.assertEqual(self.glyphs(), set())

    def test_the_note_reaches_the_agent_s_screen(self) -> None:
        """A note nobody prints is the silence this fix is about.

        `_print_layout` is the one surface both the server response and
        the offline path print through, so this drives the whole
        degraded-path command rather than the helper: a `notes` key a
        call site forgot to pass would still fail here.
        """
        self.store.commit(
            author="user",
            new_scenes={"checkout-flow":
                        [e for e in self.store.scenes["checkout-flow"]
                         if e["id"] != "pin-a"]})
        path = self.tmp / "resolve.json"
        path.write_text(json.dumps(
            {"base_revn": self.store.head_revn(), "artifact": "payments",
             "ops": [{"op": "resolve_pin", "id": "pin-a"}]}), "utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            canvas.cmd_apply(argparse.Namespace(
                project=self.tmp, file=str(path), check=False, render=False))
        self.assertIn("NOTE=pin pin-a resolved; its ❓ was already gone",
                      buf.getvalue().splitlines())

    def test_a_same_artifact_resolve_still_works(self) -> None:
        """Control: the common path must not pay for the foreign one."""
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": "checkout-flow",
             "ops": [{"op": "resolve_pin", "id": "pin-a"}]})
        self.assertEqual(self.glyphs(), set())
        self.assertEqual(self.open_pins(), set())
        self.assertEqual([p["status"] for p in self.store.registry["pins"]],
                         ["resolved"])


class PendingHarness:
    """A restartable server app over a temp project, plus a queueable batch.

    Two classes read the pending queue — one for what a *restart* does to
    it, one for what an *Apply* does to the round — and both need the same
    fixture, so it lives here rather than twice.
    """

    def setUp(self) -> None:
        """Build a server app over a temp project and seed one artifact."""
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-pending-"))
        self.project = canvas.Project(self.tmp)
        self.project.ensure_tree()
        self.app = self.restart()
        self.app.store.apply_batch(seed_batch())

    def tearDown(self) -> None:
        """Drop the temp project and the process-level side files."""
        self.app.log_file.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p in (self.project.state_path, self.project.events_path,
                  self.project.log_path):
            if p.exists():
                p.unlink()

    def restart(self) -> Any:
        """Stand a fresh server app over the same project directory.

        The old app's log handle is closed first, so a case may restart
        as often as it likes without leaking one per lifetime.

        Returns:
            The new `ServerApp` — what `start` builds after a `stop`.
        """
        old = getattr(self, "app", None)
        if old is not None:
            old.log_file.close()
        self.app = canvas.ServerApp(self.project)
        return self.app

    def a_revision(self, note: str = "the cart needs a total") -> dict[str, Any]:
        """A queueable batch: one modification to the seeded flow.

        Args:
            note: The banner's headline for this revision.

        Returns:
            An op-batch envelope on the current head revision.
        """
        return {"base_revn": self.app.store.head_revn(),
                "artifact": "checkout-flow", "note": note,
                "ops": [{"op": "mod", "id": "cart",
                         "attrs": {"label": "Cart (3 items)"}}]}


class TestPendingQueueDurability(PendingHarness, unittest.TestCase):
    """A revision queued behind the banner must outlive the server."""

    def test_a_queued_revision_survives_a_restart(self) -> None:
        """The measured failure: `PENDING=1` → stop → start → `PENDING=0`.

        `stop` is the documented way to end a session, so the queue
        being memory-only meant the documented shutdown destroyed
        agent-authored work the user had not answered yet — with no
        warning at stop, no note at start, and no record anywhere
        (r5-18).
        """
        entry = self.app.queue_pending(self.a_revision(), pin_only=False)
        self.restart()
        self.assertEqual(len(self.app.pending), 1,
                         "the restart destroyed the queued revision")
        after = self.app.pending[0]
        self.assertEqual(after["id"], entry["id"])
        self.assertEqual(after["batch"]["ops"], entry["batch"]["ops"])
        self.assertEqual(after["batch"].get("note"), "the cart needs a total")
        self.assertEqual(after["pin_only"], False)
        self.assertEqual(after["queued_at"], entry["queued_at"])

    def test_a_restored_revision_still_applies(self) -> None:
        """Surviving is not enough — the banner must still be pullable."""
        self.app.queue_pending(self.a_revision(), pin_only=False)
        self.restart()
        record = self.app.commit_pending(self.app.pending[0])
        self.assertEqual([e.get("text") for e in
                          self.app.store.scenes["checkout-flow"]
                          if e.get("containerId") == "cart"],
                         ["Cart (3 items)"])
        self.assertEqual(self.app.pending, [])
        self.assertGreater(record["revn"], 1)

    def test_the_restored_queue_does_not_reuse_an_id(self) -> None:
        """Two entries, one restart: the next queue must not collide.

        A counter that restarted at zero would hand the new revision
        the id of one still on the banner, and `/api/pending/resolve`
        resolves by id — the user's Apply would hit whichever came
        first in the list.
        """
        first = self.app.queue_pending(self.a_revision("first"), False)
        second = self.app.queue_pending(self.a_revision("second"), False)
        self.restart()
        third = self.app.queue_pending(self.a_revision("third"), False)
        self.assertEqual(len({first["id"], second["id"], third["id"]}), 3)
        self.assertEqual(len({p["id"] for p in self.app.pending}), 3)

    def test_an_empty_queue_restarts_silently(self) -> None:
        """Control (a): durability must not invent a finding of its own."""
        before = list(self.app.store.issues)
        self.restart()
        self.assertEqual(self.app.pending, [])
        self.assertEqual(self.app.store.issues, before)

    def test_a_discarded_revision_is_gone_from_disk_too(self) -> None:
        """Control (b): the user said no, so a restart must not re-offer it.

        Persisting on queue without deleting on drop is the worse bug:
        the discard looks like it worked until the next `start` puts the
        revision back on the banner, and there is no second way to say
        no.
        """
        entry = self.app.queue_pending(self.a_revision(), pin_only=False)
        self.app.drop_pending(entry["id"])
        self.assertEqual(
            sorted(p.name for p in self.project.pending_dir.glob("*.json")),
            [], "the dropped revision is still on disk")
        self.restart()
        self.assertEqual(self.app.pending, [])

    def test_a_committed_revision_is_gone_from_disk_too(self) -> None:
        """Control (b), the other exit: pulling it must clear the file."""
        entry = self.app.queue_pending(self.a_revision(), pin_only=False)
        self.app.commit_pending(entry)
        self.assertEqual(list(self.project.pending_dir.glob("*.json")), [])
        self.restart()
        self.assertEqual(self.app.pending, [])

    def test_a_malformed_pending_file_is_quarantined_not_fatal(self) -> None:
        """Control (c): the new on-disk surface gets Task 2's treatment.

        A record holding `[]` is the shape that bricked the whole store
        through the save loop, and the spec's Risks section names this
        directory as the next thing that drifts. So the load rejects a
        non-object before it can subscript one, quarantines it with an
        issue naming the file, and keeps every well-formed sibling.
        """
        good = self.app.queue_pending(self.a_revision("keep me"), False)
        (self.project.pending_dir / "99.json").write_text("[]", "utf-8")
        self.restart()
        self.assertEqual([p["id"] for p in self.app.pending], [good["id"]],
                         "the malformed file took its sibling down with it")
        self.assertEqual(self.app.pending[0]["batch"].get("note"), "keep me")
        quarantined = [i for i in self.app.store.issues
                       if i.get("code") == "PND-001"]
        self.assertEqual(len(quarantined), 1)
        self.assertIn("99.json", quarantined[0]["msg"])
        self.assertIn("99.json.bad", quarantined[0]["hint"],
                      "the issue does not say where the file went")
        self.assertFalse(quarantined[0]["repaired"],
                         "a skipped revision is not a repair")

    def test_a_quarantined_file_is_kept_as_evidence(self) -> None:
        """Set aside under a new name, not left where it was.

        Leaving it in place loses it: `pending_seq` is seeded from the
        entries that *loaded*, so a corrupt file holding the highest id
        is handed straight back out by the next queue, which overwrites
        the very evidence the quarantine promised to keep.
        """
        self.app.queue_pending(self.a_revision("keep me"), False)   # id 1
        (self.project.pending_dir / "2.json").write_text("[]", "utf-8")
        self.restart()
        self.assertTrue((self.project.pending_dir / "2.json.bad").exists(),
                        "the malformed file was not set aside")
        self.assertFalse((self.project.pending_dir / "2.json").exists())
        second = self.app.queue_pending(self.a_revision("the next one"), False)
        self.assertEqual(second["id"], 2)
        self.assertEqual(
            json.loads((self.project.pending_dir / "2.json").read_text("utf-8"))
            ["batch"]["note"], "the next one")
        self.assertEqual((self.project.pending_dir / "2.json.bad")
                         .read_text("utf-8"), "[]",
                         "the new entry overwrote the quarantined file")

    def test_a_file_whose_name_lies_about_its_id_is_quarantined(self) -> None:
        """Otherwise it is a ghost the user can never get rid of.

        `forget_pending` recomputes the path from the entry's id, so a
        well-formed entry in a mismatched file is dropped from the queue
        and left on disk — the discard looks like it worked, and every
        subsequent start puts the revision back on the banner. The
        writer never produces this shape, so it is quarantine, not
        repair.
        """
        entry = dict(self.app.queue_pending(self.a_revision("ghost"), False))
        self.app.drop_pending(entry["id"])
        entry["id"] = 2
        (self.project.pending_dir / "5.json").write_text(
            json.dumps(entry), "utf-8")
        self.restart()
        self.assertEqual(self.app.pending, [],
                         "a file that can never be deleted reached the queue")
        self.assertIn("5.json", "".join(i["msg"] for i in
                                        self.app.store.issues
                                        if i.get("code") == "PND-001"))
        self.restart()
        self.assertEqual(self.app.pending, [], "the ghost came back")

    def test_a_boolean_id_is_not_an_integer_id(self) -> None:
        """`isinstance(True, int)` is True, and JSON has real booleans.

        The file is named for what `pending_path` would produce from
        that id, so this reaches the type check rather than the
        name check.
        """
        (self.project.pending_dir / "True.json").write_text(
            '{"id": true, "batch": {"ops": []}}', "utf-8")
        self.restart()
        self.assertEqual(self.app.pending, [])
        self.assertIn("True.json", "".join(i["msg"] for i in
                                           self.app.store.issues
                                           if i.get("code") == "PND-001"))

    def test_the_pending_directory_is_gitignored(self) -> None:
        """A queued revision is machinery, and must not reach a commit.

        Tracked, it rides into the user's commits and a later checkout
        re-materializes revisions they may already have answered.
        """
        lines = (self.project.pk / ".gitignore").read_text("utf-8").split()
        self.assertIn(".pending/", lines)
        self.assertIn(".backups/", lines)

    def test_an_existing_gitignore_gains_the_pending_line(self) -> None:
        """Projects that predate `.pending/` need the line just as much.

        The template was written only when absent, so every project
        created before this change would have kept ignoring `.backups/`
        alone and tracking its queue.
        """
        gi = self.project.pk / ".gitignore"
        gi.write_text(".backups/\nnotes.local.md\n", "utf-8")
        self.project.ensure_tree()
        self.assertEqual(gi.read_text("utf-8").split(),
                         [".backups/", "notes.local.md", ".pending/"])
        self.project.ensure_tree()
        self.assertEqual(gi.read_text("utf-8").count(".pending/"), 1,
                         "a second call appended the line again")

    def test_a_non_utf8_gitignore_does_not_take_down_the_project(self) -> None:
        """It is a USER-owned file, so it can hold anything at all.

        `read_text("utf-8")` raises UnicodeDecodeError, which is a
        ValueError and not an OSError — so one stray high byte in a file
        this code merely consults took down every command that builds a
        Store, with a raw traceback. `ensure_tree` runs inside
        `Store.load`, so that is start, status, apply and lint.
        """
        gi = self.project.pk / ".gitignore"
        gi.write_bytes(b".backups/\nca\xe9sar/\n")
        before = gi.read_bytes()
        self.restart()
        self.assertEqual(gi.read_bytes(), before,
                         "an unreadable .gitignore was rewritten")
        self.assertIn(".gitignore left alone",
                      self.project.log_path.read_text("utf-8"))

    def test_an_unreadable_gitignore_is_not_replaced(self) -> None:
        """The read failure must not be read as "the file was empty".

        Treating it that way rewrote the template over the top: a
        `.gitignore` holding user lines came back holding only
        `.backups/` and `.pending/`, silently. A file this code cannot
        read is not this code's to rewrite.
        """
        gi = self.project.pk / ".gitignore"
        gi.write_bytes(b"secrets.env\n\xff\xfe\nnotes/\n")
        before = gi.read_bytes()
        self.restart()
        self.assertEqual(gi.read_bytes(), before)
        self.assertIn(b"secrets.env", gi.read_bytes())
        self.assertIn(b"notes/", gi.read_bytes())

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0,
                     "mode 000 is not honoured for root, or off posix")
    def test_a_mode_000_gitignore_keeps_its_user_lines(self) -> None:
        """The destruction case, on the branch that actually did it.

        The non-UTF-8 cases die in the decode; this one reaches the
        `except OSError` arm that read "could not read" as "was empty"
        and wrote the template over the top. `os.replace` needs write
        permission on the *directory*, not the file, so an unreadable
        file was replaced quite happily — three user lines became two
        machine ones with nothing logged.
        """
        gi = self.project.pk / ".gitignore"
        gi.write_text(".backups/\nsecrets.env\nnotes/\n", "utf-8")
        before = gi.read_bytes()
        gi.chmod(0o000)
        try:
            self.restart()
        finally:
            gi.chmod(0o644)
        self.assertEqual(gi.read_bytes(), before,
                         "the unreadable .gitignore was replaced wholesale")

    def test_a_directory_shaped_gitignore_is_skipped(self) -> None:
        """`exists()` is true for a directory, and the write died on it.

        The old `if not gi.exists()` guard skipped this quietly; reading
        it raises IsADirectoryError, and rewriting from the assumed-empty
        list then crashed inside `atomic_write`'s `os.replace`.
        """
        gi = self.project.pk / ".gitignore"
        gi.unlink()
        gi.mkdir()
        (gi / "inner").write_text("x", "utf-8")
        self.restart()
        self.assertTrue(gi.is_dir())
        self.assertEqual((gi / "inner").read_text("utf-8"), "x")

    def test_an_unparseable_pending_file_is_quarantined_not_fatal(self) -> None:
        """Control (c), the truncated variant: the write was interrupted."""
        good = self.app.queue_pending(self.a_revision("keep me"), False)
        (self.project.pending_dir / "98.json").write_text('{"id": 98,', "utf-8")
        self.restart()
        self.assertEqual([p["id"] for p in self.app.pending], [good["id"]])
        self.assertIn("98.json", "".join(i["msg"] for i in
                                         self.app.store.issues
                                         if i.get("code") == "PND-001"))

    def test_a_pending_file_missing_its_batch_is_quarantined(self) -> None:
        """An object is not enough — the queue entry has to be one.

        `sanitize_pending` reads `p["batch"].get(...)` for every entry
        on every `/api/state`, so an entry restored without a batch
        would not fail at load; it would fail on the next poll, with
        the banner already drawn.
        """
        (self.project.pending_dir / "97.json").write_text(
            '{"id": 97, "pin_only": false}', "utf-8")
        self.restart()
        self.assertEqual(self.app.pending, [])
        self.assertIn("97.json", "".join(i["msg"] for i in
                                         self.app.store.issues
                                         if i.get("code") == "PND-001"))
        self.app.sanitize_pending()

    def test_a_deferred_revision_stays_deferred_across_a_restart(self) -> None:
        """"After my save" is an answer, and answers must not be forgotten.

        The flag is set by `/api/pending/resolve`, long after the queue
        write, so persisting only at queue time would restore the entry
        with `deferred` back to False and hold it on the banner the user
        already dismissed.
        """
        entry = self.app.queue_pending(self.a_revision(), pin_only=False)
        self.app.set_pending_deferred(entry["id"])
        self.restart()
        self.assertEqual([p["deferred"] for p in self.app.pending], [True])
        self.app.flush_deferred()
        self.assertEqual(self.app.pending, [])


class TestRoundSurvivesAnApply(PendingHarness, unittest.TestCase):
    """Answering the banner must not wind the session clock backwards.

    `effective_round` is `committed + (1 if queued)`, so a queued
    revision advertises the round it belongs to before it lands. The
    commit's own auto-bump fires on the first agent commit after a
    NON-agent one — which an apply-from-pending, landing on the agent's
    own previous revision, is not. So the +1 evaporated at the moment
    the user finally answered: the header fell from `Round 3+` back to
    `Round 2` and every open pin got a round younger, which is the
    standing-nag mechanism running in reverse (r5-2).
    """

    def an_open_pin(self) -> None:
        """Ask a question on the seeded flow, so pin ageing has a subject."""
        self.app.store.apply_batch(
            {"base_revn": self.app.store.head_revn(),
             "artifact": "checkout-flow",
             "ops": [{"op": "pin", "id": "pin-total", "target": "cart",
                      "question": "does the cart show a total?"}]})

    def ages(self) -> dict[str, int]:
        """Pin ages exactly as an agent reads them off an apply response.

        Returns:
            Age in rounds by pin id, queue included.
        """
        return {p["id"]: p["age_rounds"]
                for p in self.app.store.pin_debt(self.app.queued_turn())}

    def advertised(self) -> int:
        """The round every agent-facing surface is showing right now.

        Returns:
            `effective_round`, queue included — what the header reads.
        """
        return self.app.store.effective_round(self.app.queued_turn())

    def test_the_round_the_banner_advertised_is_the_round_that_commits(
            self) -> None:
        """The measured failure: Apply is a fall from `N+1` back to `N`."""
        entry = self.app.queue_pending(self.a_revision(), pin_only=False)
        shown = self.advertised()
        self.assertEqual(shown, self.app.store.registry["round"] + 1,
                         "the banner was not advertising a round at all")
        record = self.app.commit_pending(entry)
        self.assertEqual(record["round"], shown,
                         "the save landed in a round the agent had left")
        self.assertEqual(self.app.store.registry["round"], shown)
        self.assertEqual(self.advertised(), shown,
                         "the header fell back a round on Apply")

    def test_no_pin_gets_younger_across_an_apply(self) -> None:
        """Ageing is the mechanism the round carries — and it ran backwards.

        `age_rounds` is `effective_round - pin.round`, so the evaporating
        +1 made a question that had been waiting a round read "age 0r"
        again. A nag that resets itself every time the user answers the
        banner cannot make an ignored question harder to ignore.
        """
        self.an_open_pin()
        entry = self.app.queue_pending(self.a_revision(), pin_only=False)
        before = self.ages()
        self.assertEqual(before, {"pin-total": 1},
                         "the fixture is not ageing a pin to begin with")
        self.app.commit_pending(entry)
        after = self.ages()
        for pid, age in before.items():
            self.assertGreaterEqual(after.get(pid, -1), age,
                                    "pin %s got younger across an Apply" % pid)

    def test_a_discard_gives_the_round_back(self) -> None:
        """Control: the reversal is the point of deriving it (documented).

        Discard is the one exit that SHOULD un-advertise the round —
        nothing committed, so `registry.json` must not record a round in
        which nothing happened.
        """
        committed = self.app.store.registry["round"]
        entry = self.app.queue_pending(self.a_revision(), pin_only=False)
        self.assertEqual(self.advertised(), committed + 1)
        self.app.drop_pending(entry["id"])
        self.assertEqual(self.advertised(), committed,
                         "a discarded revision kept its round")
        self.assertEqual(self.app.store.registry["round"], committed)

    def test_an_explicit_set_round_in_a_queued_batch_lands_exactly(
            self) -> None:
        """Control: the anti-stacking fix, in the direction this change adds.

        An explicit `set_round` is the round, verbatim — the auto-bump
        used to stack on top of it and hand the chat-only escape hatch
        7 when it asked for 6 (v0.8 beats). Carrying the advertised
        round forward is a second thing that could stack: the banner
        shows 6 while the batch behind it says "we are still in 5", and
        5 is the answer.
        """
        self.app.store.apply_batch(
            {"base_revn": self.app.store.head_revn(),
             "ops": [{"op": "registry", "action": "set_round", "round": 5}]})
        batch = self.a_revision("still round five")
        batch["ops"].append({"op": "registry", "action": "set_round",
                             "round": 5})
        entry = self.app.queue_pending(batch, pin_only=False)
        self.assertEqual(self.advertised(), 6)
        record = self.app.commit_pending(entry)
        self.assertEqual(record["round"], 5,
                         "the advertised round stacked on an explicit one")
        self.assertEqual(self.app.store.registry["round"], 5)

    def test_a_chat_only_set_round_still_lands_exactly(self) -> None:
        """Control: the same fix in its original direction, nothing queued.

        This is the shape v0.8 fixed — the first agent commit after a
        USER save, where the auto-bump would otherwise fire on top of
        the explicit round.
        """
        els = list(self.app.store.scenes["checkout-flow"])
        self.app.store.commit(author="user", new_scenes={"checkout-flow": els},
                              base_revn=self.app.store.head_revn())
        record, _ = self.app.store.apply_batch(
            {"base_revn": self.app.store.head_revn(),
             "ops": [{"op": "registry", "action": "set_round", "round": 6}]})
        self.assertEqual(record["round"], 6)
        self.assertEqual(self.app.store.registry["round"], 6)
        self.assertEqual(self.advertised(), 6)

    def test_two_queued_revisions_are_one_agent_move(self) -> None:
        """Control: one move may span several commits, so it is one round.

        Both entries were queued against the same committed round and
        the banner advertised one number for both. Deriving the target
        at Apply time instead would read the second entry's own +1 and
        ratchet — which is per-commit bumping, the thing round stamping
        was introduced to stop (F-A2).
        """
        committed = self.app.store.registry["round"]
        first = self.app.queue_pending(self.a_revision("the cart"), False)
        second = self.a_revision("and the button")
        second["ops"] = [{"op": "mod", "id": "checkout",
                          "attrs": {"label": "Pay now"}}]
        second = self.app.queue_pending(second, False)
        self.assertEqual(self.advertised(), committed + 1)
        self.app.commit_pending(first)
        self.app.commit_pending(second)
        self.assertEqual(self.app.store.registry["round"], committed + 1,
                         "two batches of one move bought two rounds")
        self.assertEqual(self.advertised(), committed + 1)

    def test_the_advertised_round_survives_a_restart(self) -> None:
        """The queue is durable, so what it advertises has to be too.

        Otherwise the restart is a second way to lose the round: the
        entry comes back, the banner redraws, and pulling it lands in
        the round the session had already left.
        """
        self.app.queue_pending(self.a_revision(), pin_only=False)
        shown = self.advertised()
        self.restart()
        self.assertEqual(self.advertised(), shown)
        record = self.app.commit_pending(self.app.pending[0])
        self.assertEqual(record["round"], shown)
        self.assertEqual(self.app.store.registry["round"], shown)


if __name__ == "__main__":
    unittest.main()
