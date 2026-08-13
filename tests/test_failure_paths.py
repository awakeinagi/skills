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

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

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
        """Build a store over a temp project and seed one artifact."""
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-fail-"))
        self.project = canvas.Project(self.tmp)
        self.project.ensure_tree()
        self.store = canvas.Store(self.project)
        self.store.apply_batch(seed_batch())

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
        """`rename_artifact` writes through to disk while validating."""
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

        `annotate_mapping` with a negative `index` walks past the bounds
        check (`idx >= len(...)` is false for -1) and subscripts an
        empty list, so the batch dies on a bare IndexError rather than a
        BatchError — and a guard that only catches BatchError never
        runs. The negative index itself is a separate, still-open
        defect; this pins the guard, not the validation.
        """
        before = self.snapshot()
        before_meta = json.dumps(self.store.artifact_meta, sort_keys=True)
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        before_file = path.read_bytes()
        with self.assertRaises(IndexError):
            self.store.apply_batch(
                {"base_revn": self.store.head_revn(),
                 "artifact": "checkout-flow",
                 "ops": [{"op": "registry", "action": "rename_artifact",
                          "artifact": "checkout-flow", "name": "Renamed"},
                         {"op": "registry", "action": "set_round",
                          "round": 9},
                         {"op": "registry", "action": "annotate_mapping",
                          "index": -1, "note": "boom"}]})
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


class TestPendingQueueDurability(unittest.TestCase):
    """A revision queued behind the banner must outlive the server."""

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


if __name__ == "__main__":
    unittest.main()
