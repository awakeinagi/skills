"""WP1 acceptance: what a rejected batch leaves behind.

Every case here follows one discipline — *cause a failure, then diff the
state*. A test that only asserts the error message passes just as
happily when the rejected batch has already written half of itself into
the live registry, and that is exactly how r5-8 shipped: a batch
rejected for a typo'd `set_round` kept the concept its earlier op had
added, served it on `/api/state`, and the next commit persisted it. So
each case snapshots what a caller can observe — the in-memory registry,
the registry file, the artifact file — provokes the rejection, and
asserts the snapshot is unchanged afterwards.
"""
from __future__ import annotations

import json
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
    """A one-node flow with a concept, so mappings have something to name.

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
            "y": 120, "width": 140, "height": 60, "role": "node"}}],
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


if __name__ == "__main__":
    unittest.main()
