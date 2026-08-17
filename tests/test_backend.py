"""Backend unit tests for canvas.py — differ, facts, DAG, durability.

Run: python3 -m pytest tests/ -q   (or python3 tests/test_backend.py)
"""
from __future__ import annotations

import argparse
import ast
import base64
import contextlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import canvas  # noqa: E402
import focus_probe  # noqa: E402
import instruments  # noqa: E402

# The instruments are the drawing's own measure, and one test here needs
# them: `TestFanAttachPoints` asserts that the auto-fan's output draws no
# corridor, which is a claim only `shared_corridors` can settle. Both are
# first-party and stdlib-only, so this stays a plain import — the loud-skip
# rule in AGENTS.md §5 is about third-party packages.


def seed_flow_batch(base_revn=0):
    """The feel-prototype checkout flow: cart → checkout → payment → confirm."""
    return {
        "base_revn": base_revn,
        "artifact": "checkout-flow",
        "create": {"id": "checkout-flow", "name": "Checkout Flow",
                   "type": "flow", "concept": "checkout",
                   "concept_name": "Checkout"},
        "ops": [
            {"op": "add", "element": {"type": "rectangle", "id": "cart",
                                      "label": "Cart", "x": 40, "y": 120,
                                      "width": 140, "height": 60,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "checkout",
                                      "label": "Checkout", "x": 260, "y": 120,
                                      "width": 140, "height": 60,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "payment",
                                      "label": "Payment", "x": 480, "y": 120,
                                      "width": 140, "height": 60,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "confirm",
                                      "label": "Confirmation", "x": 700,
                                      "y": 120, "width": 140, "height": 60,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "arrow", "id": "t1"},
             "from": "cart", "to": "checkout"},
            {"op": "add", "element": {"type": "arrow", "id": "t2"},
             "from": "checkout", "to": "payment"},
            {"op": "add", "element": {"type": "arrow", "id": "t3"},
             "from": "payment", "to": "confirm"},
        ],
    }


def crossing_lint_control():
    """The `passes through` warnings a scene that MUST produce one makes.

    The firing pole for this file's clean-routing silences, added by
    curator batch 24 (2026-08-16). Two acceptance assertions — the argus
    ingest fan and gap-list item 1 — say a router produced no crossings,
    and the mortality spike measured that both survive `passes_through_
    foreign` being killed: a check answering nothing reports the same
    empty list a clean route does, so "the fan routes clean" was a
    statement about the lint's silence and not about the drawing.

    Deliberately the crudest possible positive: A and Z on one rank with
    a single straight run between them and F parked in the middle of it.
    Nothing here is routed, fitted or bound to F, so the only way this
    goes quiet is the check itself going quiet — which is exactly the
    event the callers need separated from their own success. It is a
    module-level function rather than a mixin because the two callers
    sit in unrelated classes, one of them a fixture replay.

    Returns:
        The warning lines naming a foreign node on an arrow's path.
        Callers assert the count; one is what this scene draws.
    """
    els = [{"id": "A", "type": "rectangle", "x": 0, "y": 100, "width": 80,
            "height": 40, "customData": {"role": "node"}},
           {"id": "F", "type": "rectangle", "x": 200, "y": 100, "width": 80,
            "height": 40, "customData": {"role": "node"}},
           {"id": "Z", "type": "rectangle", "x": 400, "y": 100, "width": 80,
            "height": 40, "customData": {"role": "node"}},
           {"id": "e1", "type": "arrow", "x": 80, "y": 120,
            "points": [[0, 0], [320, 0]],
            "startBinding": {"elementId": "A", "focus": 0, "gap": 1},
            "endBinding": {"elementId": "Z", "focus": 0, "gap": 1},
            "customData": {"role": "edge", "route": "server"}}]
    lint = canvas.lint_layout(els, artifact_type="flow")
    return [w for w in lint["warnings"] if "passes through" in w]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-test-"))
        self.project = canvas.Project(self.tmp)
        self.project.ensure_tree()
        self.store = canvas.Store(self.project)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p in (self.project.state_path, self.project.events_path,
                  self.project.log_path):
            if p.exists():
                p.unlink()

    def scene(self, aid="checkout-flow"):
        return [dict(e) for e in self.store.scenes[aid]]


class TestWritePath(Base):
    def test_seed_and_labels_bound(self):
        record, pin_only = self.store.apply_batch(seed_flow_batch())
        self.assertFalse(pin_only)
        self.assertEqual(record["revn"], 1)
        self.assertEqual(record["author"], "agent")
        els = self.store.scenes["checkout-flow"]
        by_id = {e["id"]: e for e in els}
        # real text model: bound text with containerId, listed in
        # boundElements — never an inline text prop
        self.assertIn("cart-label", by_id)
        self.assertEqual(by_id["cart-label"]["containerId"], "cart")
        self.assertNotIn("text", by_id["cart"])
        self.assertIn({"id": "cart-label", "type": "text"},
                      by_id["cart"]["boundElements"])
        # arrows routed with explicit geometry AND bindings
        t1 = by_id["t1"]
        self.assertEqual(t1["startBinding"]["elementId"], "cart")
        self.assertEqual(t1["endBinding"]["elementId"], "checkout")
        self.assertTrue(t1["points"][-1][0] > 0)
        # registry got the concept + view
        c = self.store.registry["concepts"][0]
        self.assertEqual(c["id"], "checkout")
        self.assertIn("checkout-flow", c["views"])
        # agent move advances the round and hands the move to the user
        self.assertEqual(self.store.registry["round"], 1)
        self.assertEqual(self.store.registry["whose_move"], "user")

    def test_validate_all_then_apply_rejects_batch(self):
        self.store.apply_batch(seed_flow_batch())
        bad = {"base_revn": 1, "artifact": "checkout-flow", "ops": [
            {"op": "mod", "id": "cart", "attrs": {"x": 500}},
            {"op": "mod", "id": "nope", "attrs": {"x": 1}},
        ]}
        before = canvas.scene_hash(self.store.scenes["checkout-flow"])
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch(bad)
        self.assertIn("nope", str(cm.exception))
        self.assertEqual(before,
                         canvas.scene_hash(self.store.scenes["checkout-flow"]))

    def test_stale_base_revn(self):
        self.store.apply_batch(seed_flow_batch())
        with self.assertRaises(canvas.StaleError):
            self.store.apply_batch({"base_revn": 0, "artifact":
                                    "checkout-flow", "ops": [
                                        {"op": "del", "id": "t3"}]})

    def test_golden_no_semantic_change_no_diff(self):
        self.store.apply_batch(seed_flow_batch())
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        before = path.read_bytes()
        els = self.scene()
        rec = self.store.commit(author="user", new_scenes={
            "checkout-flow": els}, base_revn=1)
        self.assertEqual(rec["summary"]["headline"],
                         "saved without changing anything")
        self.assertEqual(before, path.read_bytes())


class TestDiffer(Base):
    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def user_save(self, els, **kw):
        return self.store.commit(author="user",
                                 new_scenes={"checkout-flow": els},
                                 base_revn=self.store.head_revn(), **kw)

    def facts(self, rec):
        return [f["fact"] for f in
                rec["artifacts"]["checkout-flow"]["facts"]]

    def test_guest_checkout_rewire_and_consequences(self):
        """The feel-test round: insert guest-checkout branch, rewire t1,
        layout shifts collapse to consequences."""
        els = self.scene()
        by_id = {e["id"]: e for e in els}
        # new step + branch diamond
        new_els, errors = els, []
        new_els = canvas.apply_ops(els, [
            {"op": "add", "element": {"type": "diamond", "id": "auth-choice",
                                      "label": "Guest or account?",
                                      "x": 150, "y": 260, "width": 160,
                                      "height": 80, "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "guest-info",
                                      "label": "Guest info", "x": 360,
                                      "y": 280, "width": 140, "height": 60,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "arrow", "id": "t4"},
             "from": "auth-choice", "to": "guest-info"},
            {"op": "add", "element": {"type": "arrow", "id": "t5"},
             "from": "guest-info", "to": "payment"},
            {"op": "mod", "id": "t1", "attrs": {"to": "auth-choice"}},
        ], errors)
        # RULING for the whole `assertEqual(errors, [])` pattern — 26
        # sites as of 2026-08-16, and this is the recorded adjudication
        # for all of them rather than 26 copies of a pole (rule-8
        # census).
        #
        # These are PREMISE GUARDS, not check outputs. The claim is not
        # "the validator found nothing wrong", it is "this fixture built,
        # so everything asserted below it is about the subject and not
        # about a batch that silently half-applied". That is why the
        # right shape is one ruling, not one pole each.
        #
        # And the instrument is not at risk regardless: emptying
        # `apply_ops`' errors list fails 19 tests across 12 classes
        # (measured 2026-08-16), among them
        # `TestHeldRevisions.test_invalid_ops_rejected_at_queue_time` and
        # `TestComposedKindsAndTooltips.test_value_checked_reject_wrong_
        # kind`, which assert it speaks. A validator that had gone quiet
        # never reaches these lines.
        self.assertEqual(errors, [])
        # simulate the layout consequence: downstream nodes shift right
        for e in new_els:
            if e["id"] in ("payment", "confirm", "payment-label",
                           "confirm-label"):
                e["x"] += 60
        rec = self.user_save(new_els)
        facts = rec["artifacts"]["checkout-flow"]["facts"]
        names = [f["fact"] for f in facts]
        self.assertIn("rewired", names)
        rew = next(f for f in facts if f["fact"] == "rewired")
        self.assertIn("Cart", rew["from"])
        self.assertIn("Guest or account?", rew["to"])
        self.assertIn("branch_added", names)
        self.assertIn("step_added", names)
        self.assertIn("transition_added", names)
        # consequence suppression: payment/confirm moves collapse
        moves = [f for f in facts if f["fact"] == "moved"]
        self.assertTrue(all(f.get("consequence_of") for f in moves))
        self.assertGreaterEqual(rec["summary"]["suppressed"], 2)
        # headline leads with the rewire (flagship fact)
        self.assertTrue(rec["summary"]["headline"].startswith("rewired"))

    def test_rename_keeps_id_stable(self):
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "confirm", "attrs":
             {"label": "Order Placed"}}], errors)
        rec = self.user_save(els)
        facts = rec["artifacts"]["checkout-flow"]["facts"]
        ren = next(f for f in facts if f["fact"] == "renamed")
        self.assertEqual(ren["element"], "confirm")   # id is the anchor
        self.assertEqual(ren["from"], "Confirmation")
        self.assertEqual(ren["to"], "Order Placed")

    def test_label_added_and_type_changed(self):
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [
            {"op": "add", "element": {"type": "rectangle", "id": "mystery",
                                      "x": 40, "y": 400, "width": 120,
                                      "height": 50, "role": "node"}}],
            errors)
        self.user_save(els)
        els2 = [dict(e) for e in self.store.scenes["checkout-flow"]]
        els2 = canvas.apply_ops(els2, [
            {"op": "mod", "id": "mystery", "attrs": {"label": "Email"}}],
            errors)
        rec = self.user_save(els2)
        names = self.facts(rec)
        self.assertIn("label_added", names)
        # type change via delete+redraw with the same label nearby
        els3 = [dict(e) for e in self.store.scenes["checkout-flow"]]
        els3 = canvas.apply_ops(els3, [{"op": "del", "id": "mystery"}],
                                errors)
        els3 = canvas.apply_ops(els3, [
            {"op": "add", "element": {"type": "diamond", "id": "mystery2",
                                      "label": "Email", "x": 50, "y": 405,
                                      "width": 120, "height": 50,
                                      "role": "node"}}], errors)
        rec = self.user_save(els3)
        self.assertIn("type_changed", self.facts(rec))

    def test_deletion_tombstone(self):
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "confirm"}], errors)
        rec = self.user_save(els)
        facts = rec["artifacts"]["checkout-flow"]["facts"]
        dele = next(f for f in facts if f["fact"] == "deleted")
        self.assertEqual(dele["was"]["label"], "Confirmation")

    def test_sentinel_suppression(self):
        els = self.scene()
        for e in els:
            if e["id"] == "t2":
                e["x"] = 2 ** 56
                e["y"] = -2 ** 56
        rec = self.user_save(els)
        facts = rec["artifacts"].get("checkout-flow", {}).get("facts", [])
        self.assertFalse([f for f in facts if f["fact"] == "moved"
                          and f["element"] == "t2"])

    def test_empty_save_fact(self):
        rec = self.user_save(self.scene())
        self.assertEqual(rec["summary"]["headline"],
                         "saved without changing anything")

    def test_inverse_replay_roundtrip(self):
        els = self.scene()
        errors = []
        new_els = canvas.apply_ops(els, [
            {"op": "del", "id": "t3"},
            {"op": "mod", "id": "cart", "attrs": {"x": 900}}], errors)
        rec = self.user_save(new_els)
        part = rec["artifacts"]["checkout-flow"]
        back = canvas.rebuild_bound_elements(canvas.replay_changes(
            [canvas.normalize_element(e) for e in new_els], part["inverse"]))
        want = self.store.state_at(1)["checkout-flow"]["elements"]
        self.assertEqual(canvas.scene_hash(back), canvas.scene_hash(want))


class TestDAG(Base):
    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_fork_on_save_after_checkout(self):
        els = self.scene()
        errors = []
        els2 = canvas.apply_ops(els, [
            {"op": "mod", "id": "confirm", "attrs": {"label": "Receipt"}}],
            errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els2},
                          base_revn=1)  # revn 2 on main
        self.store.checkout_revn = 1
        st = self.store.state_at(1)
        els_at_1 = st["checkout-flow"]["elements"]
        alt = canvas.apply_ops([dict(e) for e in els_at_1], [
            {"op": "mod", "id": "confirm", "attrs": {"label": "Summary"}}],
            errors)
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": alt},
                                fork_name="alt-idea")
        self.assertEqual(rec["base_revn"], 1)
        self.assertEqual(rec["branch"], "alt-idea")
        self.assertEqual(self.store.registry["head"], "alt-idea")
        names = [b["name"] for b in self.store.registry["branches"]]
        self.assertIn("alt-idea", names)
        self.assertIn("main", names)
        # fork point identifiable: revn 1 has two children
        children = [r for r in self.store.records.values()
                    if r.get("base_revn") == 1]
        self.assertEqual(len(children), 2)

    def test_commits_never_altered(self):
        rec1 = json.dumps(self.store.records[1], sort_keys=True)
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "t3"}], errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=1)
        self.assertEqual(rec1, json.dumps(self.store.records[1],
                                          sort_keys=True))
        # and the file on disk is still there, unchanged in count
        self.assertEqual(len(list(self.project.saves_dir.glob("*.json"))), 2)

    def test_no_merge_machinery(self):
        self.assertFalse([m for m in dir(self.store)
                          if "merge" in m.lower()])

    def test_branch_switch_and_archive(self):
        els = self.scene()
        errors = []
        els2 = canvas.apply_ops(els, [
            {"op": "mod", "id": "confirm", "attrs": {"label": "Receipt"}}],
            errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els2},
                          base_revn=1)
        self.store.checkout_revn = 1
        alt = canvas.apply_ops([dict(e) for e in
                                self.store.state_at(1)["checkout-flow"]
                                ["elements"]],
                               [{"op": "mod", "id": "confirm",
                                 "attrs": {"label": "Summary"}}], errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": alt},
                          fork_name="alt-idea")
        self.store.switch_branch("main")
        labels = canvas.label_map(self.store.scenes["checkout-flow"])
        self.assertEqual(labels["confirm"], "Receipt")
        b = self.store.set_archived("alt-idea", True)
        self.assertTrue(b["archived"])
        with self.assertRaises(canvas.BatchError):
            self.store.set_archived("main", True)  # current branch
        b = self.store.set_archived("alt-idea", False)
        self.assertFalse(b["archived"])

    def test_revert_is_append_only(self):
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "t3"}], errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=1)
        rec = self.store.revert_to(1)
        self.assertEqual(rec["revn"], 3)  # NEW record, nothing deleted
        self.assertEqual(len(self.store.records), 3)
        ids = {e["id"] for e in self.store.scenes["checkout-flow"]}
        self.assertIn("t3", ids)


class TestRegistryConfigDurability(Base):
    def test_lazy_defaults_created(self):
        self.assertTrue(self.project.config_path.exists())
        self.assertTrue(self.project.registry_path.exists())
        cfg = json.loads(self.project.config_path.read_text())
        self.assertEqual(cfg["canvas_updates"], "per-round")
        self.assertEqual(cfg["artifact_types"]["wireframe"]["tier"],
                         "first-class")

    def test_corrupt_registry_repaired_with_codes(self):
        self.project.registry_path.write_text(
            '{"revn": "nope", "whose_move": "bananas"}')
        store = canvas.Store(self.project)
        self.assertEqual(store.registry["revn"], 0)
        self.assertEqual(store.registry["whose_move"], "agent")
        codes = {i["code"] for i in store.issues}
        self.assertIn("REG-002", codes)
        self.assertIn("REG-006", codes)

    def test_corrupt_artifact_repaired(self):
        self.store.apply_batch(seed_flow_batch())
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        doc = json.loads(path.read_text())
        doc["elements"][0].pop("id")                      # malformed element
        doc["elements"].append({"id": "dangler", "type": "text",
                                "containerId": "ghost", "x": 0, "y": 0})
        path.write_text(json.dumps(doc))
        store = canvas.Store(self.project)
        codes = {i["code"] for i in store.issues}
        self.assertIn("ART-002", codes)
        self.assertIn("ART-004", codes)

    def test_migration_snapshot_to_backups(self):
        canvas.MIGRATIONS["registry"].append(
            ("0002-test", lambda d: dict(d, migrated_marker=True)))
        try:
            store = canvas.Store(self.project)
            self.assertTrue(store.registry.get("migrated_marker"))
            self.assertIn("0002-test", store.registry["migrations"])
            backups = list(self.project.backups_dir.rglob("model.json"))
            self.assertEqual(len(backups), 1)
        finally:
            canvas.MIGRATIONS["registry"].pop()

    def test_registry_coauthored_no_silent_edits(self):
        rec, _ = self.store.apply_batch(seed_flow_batch())
        self.assertTrue(rec["registry_changes"])  # concept rode the record


class TestCatchUp(Base):
    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_out_of_session_edit_reconciles(self):
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        doc = json.loads(path.read_text())
        for e in doc["elements"]:
            if e["id"] == "confirm-label":
                e["text"] = "Done!"
                e["originalText"] = "Done!"
        path.write_text(json.dumps(doc))
        store = canvas.Store(self.project)
        rec = store.catch_up()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["author"], "out-of-session")
        self.assertTrue(rec["reconciliation"])
        names = [f["fact"] for f in
                 rec["artifacts"]["checkout-flow"]["facts"]]
        self.assertIn("renamed", names)

    def test_no_changes_no_reconciliation(self):
        store = canvas.Store(self.project)
        self.assertIsNone(store.catch_up())
        self.assertEqual(len(store.records), 1)

    def test_git_revert_detected_as_question(self):
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "t3"}], errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=1)
        # roll the file back to revn 1's state (as git checkout would)
        state1 = self.store.state_at(1)["checkout-flow"]
        doc = {"elements": state1["elements"],
               "wysiwyg": dict(self.store.artifact_meta["checkout-flow"])}
        canvas.write_json(self.project.artifacts_dir /
                          "checkout-flow.excalidraw",
                          canvas.normalize_scene_doc(doc) | {
                              "wysiwyg": doc["wysiwyg"]})
        store = canvas.Store(self.project)
        rec = store.catch_up()
        self.assertIsNone(rec)                      # NOT silently re-anchored
        self.assertEqual(store.rollback["matches_revn"], 1)
        rec = store.accept_rollback()               # agent confirmed with user
        self.assertEqual(rec["author"], "out-of-session")
        self.assertIsNone(store.rollback)


class TestMappingsTripwires(Base):
    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        # a wireframe view of the same concept, mapped element-to-element
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-wireframe",
            "create": {"id": "checkout-wireframe", "name": "Checkout Screen",
                       "type": "wireframe", "concept": "checkout"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "pay-button",
                                          "label": "Payment", "x": 40,
                                          "y": 40, "width": 120,
                                          "height": 40, "role": "node",
                                          "kind": "button"}},
                {"op": "registry", "action": "add_mapping",
                 "concept": "checkout",
                 "elements": ["checkout-wireframe#pay-button",
                              "checkout-flow#payment"]},
            ]})

    def _user_edit(self, ops):
        """Commit `ops` on the wireframe as the user; return the record."""
        els = [dict(e) for e in self.store.scenes["checkout-wireframe"]]
        errors = []
        els = canvas.apply_ops(els, ops, errors)
        self.assertEqual(errors, [])
        return self.store.commit(author="user",
                                 new_scenes={"checkout-wireframe": els},
                                 base_revn=self.store.head_revn())

    def test_moving_a_mapped_element_fires_nothing(self):
        # v0.6: a 40px nudge is not a disagreement. Every tripwire in the
        # v0.5 assessment was this shape (R2-6).
        rec = self._user_edit([{"op": "mod", "id": "pay-button",
                                "attrs": {"y": 200}}])
        self.assertEqual(rec["tripwires"], [])

    def test_editing_a_tooltip_fires_nothing(self):
        rec = self._user_edit([
            {"op": "mod", "id": "pay-button",
             "attrs": {"customData": {"tooltip": "staged until Rerun"}}}])
        self.assertEqual(rec["tripwires"], [])

    def test_renaming_a_mapped_element_still_fires(self):
        # the differential control: the same mapping, a meaning change
        rec = self._user_edit([{"op": "mod", "id": "pay-button",
                                "attrs": {"label": "Express Pay"}}])
        self.assertTrue(rec["tripwires"])

    def test_divergence_fires_tripwire(self):
        els = [dict(e) for e in self.store.scenes["checkout-wireframe"]]
        errors = []
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "pay-button",
             "attrs": {"label": "Express Pay"}}], errors)
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-wireframe": els},
                                base_revn=self.store.head_revn())
        self.assertTrue(rec["tripwires"])
        tw = rec["tripwires"][0]
        self.assertEqual(tw["changed"], "checkout-wireframe#pay-button")
        self.assertEqual(tw["sibling"], "checkout-flow#payment")
        # fired tripwires are visible at fire time: the record entry
        # mirrors the registry id + question so apply responses can name
        # them (v0.3)
        self.assertIn("id", tw)
        self.assertTrue(tw["id"].startswith("tw-"))
        self.assertIn("changed but its mapped sibling", tw["question"])
        open_tw = [t for t in self.store.registry["tripwires"]
                   if t["status"] == "open"]
        self.assertTrue(open_tw)

    def test_intentionally_divergent_suppresses(self):
        self.store.registry["mappings"][0]["note"] = \
            "intentionally-divergent: wireframe uses marketing copy"
        els = [dict(e) for e in self.store.scenes["checkout-wireframe"]]
        errors = []
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "pay-button", "attrs": {"label": "Buy!"}}],
            errors)
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-wireframe": els},
                                base_revn=self.store.head_revn())
        self.assertFalse(rec["tripwires"])

    def _relabel(self, label):
        els = [dict(e) for e in self.store.scenes["checkout-wireframe"]]
        errors = []
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "pay-button", "attrs": {"label": label}}],
            errors)
        return self.store.commit(author="user",
                                 new_scenes={"checkout-wireframe": els},
                                 base_revn=self.store.head_revn())

    def test_scoped_annotation_still_fires_on_another_kind(self):
        """The demo's flagship tripwire, which v0.4 could not fire.

        An annotation recorded because three KPI tiles fan into one store
        used to mute that mapping forever, so a later rename diverged in
        silence and the dashboard and the pipeline drifted apart.
        """
        self.store.registry["mappings"][0].update({
            "note": "intentionally-divergent: three tiles, one store",
            "kinds": ["cardinality_changed"]})
        rec = self._relabel("Excess Return")
        self.assertTrue(rec["tripwires"])
        self.assertEqual(rec["tripwires"][0]["changed"],
                         "checkout-wireframe#pay-button")

    def test_scoped_annotation_silences_its_own_kind(self):
        self.store.registry["mappings"][0].update({
            "note": "intentionally-divergent: marketing copy",
            "kinds": ["renamed", "label_renamed"]})
        self.assertFalse(self._relabel("Buy!")["tripwires"])

    def test_unscoped_annotation_keeps_blanket_behaviour(self):
        self.store.registry["mappings"][0]["note"] = \
            "intentionally-divergent: marketing copy"
        self.store.registry["mappings"][0]["kinds"] = None
        self.assertFalse(self._relabel("Buy!")["tripwires"])

    def test_scoped_policy_still_fires_on_another_kind(self):
        self.store.registry["mappings"][0]["note"] = None
        self.store.registry.setdefault("divergence_policies", []).append(
            {"types": ["wireframe", "flow"], "concept": None,
             "kinds": ["moved"], "note": "layout only"})
        self.assertTrue(self._relabel("Express Pay")["tripwires"])

    def test_kinds_must_be_a_list_of_fact_names(self):
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch({
                "base_revn": self.store.head_revn(),
                "artifact": "checkout-flow",
                "ops": [{"op": "registry", "action": "annotate_mapping",
                         "index": 0, "note": "intentionally-divergent: x",
                         "kinds": "renamed"}]})
        self.assertIn("list of fact names", "\n".join(cm.exception.errors))


class TestPins(Base):
    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_pin_only_revision_and_answer(self):
        rec, pin_only = self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "pin", "target": "payment", "id": "pin-payment",
                 "question": "Card only, or PayPal too?"}]})
        self.assertTrue(pin_only)
        pins = self.store.registry["pins"]
        self.assertEqual(pins[0]["status"], "open")
        pin = self.store.answer_pin("pin-payment", "Card + PayPal")
        self.assertEqual(pin["status"], "answered")
        self.assertEqual(pin["answer"], "Card + PayPal")
        # pin answer must not pollute the next diff (machinery, not intent)
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        rec2 = self.store.commit(author="user",
                                 new_scenes={"checkout-flow": els},
                                 base_revn=self.store.head_revn())
        self.assertEqual(rec2["summary"]["headline"],
                         "saved without changing anything")


class TestPinLifecycle(Base):
    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "pin", "target": "payment", "id": "pin-a",
                 "question": "a?"},
                {"op": "pin", "target": "confirm", "id": "pin-b",
                 "question": "b?"}]})

    def pin(self, pid):
        return next(p for p in self.store.registry["pins"] if p["id"] == pid)

    def test_resolve_pin_writes_through_to_registry(self):
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "resolve_pin", "id": "pin-a"}]})
        self.assertEqual(self.pin("pin-a")["status"], "resolved")
        open_pins = [p for p in self.store.registry["pins"]
                     if p["status"] == "open"]
        self.assertEqual([p["id"] for p in open_pins], ["pin-b"])

    def test_target_deletion_prunes_pin(self):
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "confirm"}], errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=self.store.head_revn())
        self.assertEqual(self.pin("pin-b")["status"], "pruned")
        self.assertEqual(self.pin("pin-a")["status"], "open")

    def test_pin_element_deletion_dismisses(self):
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "pin-a"}], errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=self.store.head_revn())
        self.assertEqual(self.pin("pin-a")["status"], "dismissed")


class TestZOrderReplay(Base):
    def test_a_recorded_index_is_read_the_way_the_client_reads_it(self):
        """Every `index` fault class, read off JSON TEXT (review MAJOR-1).

        `replay_changes` and `replayChanges` (src/api.ts) have to answer
        one record identically or the tab and the file are two different
        drawings, and element order is paint order so the difference is
        visible. The e2e spec drives both implementations; this is the
        server's half, kept here so it holds when Playwright does not
        run.

        THE CASES ARE PARSED FROM TEXT, not written as Python literals.
        That is the whole point of the float pair: `2.0` and `2` are one
        value in JSON and one value in JavaScript, so a spec that writes
        `2.0` as a Python float and calls it a day is testing something
        the wire cannot carry. Reading it back through `json.loads` is
        what makes `2.0` arrive here as a float and `2` as an int — the
        asymmetry that made these two sides disagree.

        `2.0` is a POSITION and now lands at 2, matching the client,
        which provably cannot tell it from `2`. `2.5` is not a position
        and still falls to the end on both sides. `bool` stays refused —
        `min(True, 3)` is position 1 — and negatives still reach the
        caller's clamp rather than being refused here.
        """
        els = [{"id": i, "type": "rectangle"} for i in ("a", "b", "c", "d")]
        at_2 = ["a", "b", "new", "c", "d"]
        at_end = ["a", "b", "c", "d", "new"]
        at_front = ["new", "a", "b", "c", "d"]
        for token, want, why in (
                ("2", at_2, "a whole number is the position it names"),
                ("2.0", at_2, "an integral float is a position, and the "
                              "client cannot see the decimal point"),
                ("2.5", at_end, "a fractional float is not a position"),
                ("0", at_front, "the front"),
                ("-1", at_front, "negatives clamp, never `insert(-1)`"),
                ("-1.0", at_front, "the negative class re-entering as a "
                                   "float must clamp the same way"),
                ("99", at_end, "past the end clamps to the end"),
                ("99.0", at_end, "and so does an integral float past it"),
                ("true", at_end, "`bool` is not a position"),
                ("\"2\"", at_end, "nor is a string that looks like one"),
                ("null", at_end, "nor is null"),
        ):
            with self.subTest(index=token):
                changes = json.loads(
                    '[{"op": "add", "index": %s, "element": '
                    '{"id": "new", "type": "rectangle"}}]' % token)
                got = [e["id"] for e in canvas.replay_changes(els, changes)]
                self.assertEqual(got, want, "index %s: %s" % (token, why))
        # the absent field is a different record from a null one, and
        # both mean "no position given"
        changes = json.loads('[{"op": "add", "element": '
                             '{"id": "new", "type": "rectangle"}}]')
        self.assertEqual(
            [e["id"] for e in canvas.replay_changes(els, changes)], at_end)

    def test_shuffled_zorder_reconstructs_exactly(self):
        """Per-element reorder replay was lossy — the whole-order op must
        reproduce disk state exactly (no phantom reconciliations)."""
        self.store.apply_batch(seed_flow_batch())
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        import random
        rng = random.Random(42)
        rng.shuffle(els)
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=1)
        # simulate next session: fresh store, catch_up must find nothing
        store2 = canvas.Store(self.project)
        self.assertIsNone(store2.catch_up())
        want = [e["id"] for e in store2.scenes["checkout-flow"]]
        got = [e["id"] for e in
               store2.state_at(2)["checkout-flow"]["elements"]]
        self.assertEqual(want, got)

    def test_resolve_pin_survives_missing_element(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "pin", "target": "cart", "id": "pin-x",
                 "question": "x?"}]})
        # user deletes the ❓ element directly; registry pin gets dismissed,
        # but resolve_pin afterwards must not error out the batch
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "pin-x"}], errors)
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=self.store.head_revn())
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "checkout-flow",
            "ops": [{"op": "resolve_pin", "id": "pin-x"},
                    {"op": "mod", "id": "cart", "attrs": {"x": 500}}]})
        self.assertEqual(rec["author"], "agent")
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({
                "base_revn": self.store.head_revn(),
                "artifact": "checkout-flow",
                "ops": [{"op": "resolve_pin", "id": "totally-bogus"}]})


class TestDeletionConsequences(Base):
    def test_dangling_rewires_marked_consequence(self):
        self.store.apply_batch(seed_flow_batch())
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        errors = []
        els = canvas.apply_ops(els, [{"op": "del", "id": "checkout"}], errors)
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": els},
                                base_revn=1)
        facts = rec["artifacts"]["checkout-flow"]["facts"]
        rewires = [f for f in facts if f["fact"] == "rewired"]
        self.assertTrue(all(f.get("consequence_of") == "checkout"
                            for f in rewires))
        # FIRING POLE: `TestNoOpRewireIsNotASequenceChange.test_two_real_
        # rewires_do_claim_a_re_sequence` (rule-8 census, 2026-08-16).
        # This silence says the fact is suppressed because the rewires
        # are a deletion's CONSEQUENCE — a claim only worth making if
        # the fact can be minted at all, and until that pole landed
        # nothing in the file ever asserted it fires.
        self.assertNotIn("sequence_reordered", [f["fact"] for f in facts])
        # the deletion carries the headline, not the arrow wreckage
        self.assertTrue(rec["summary"]["headline"].startswith("deleted"))

    def test_soft_rewrap_is_not_a_rename(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "confirm",
                 "attrs": {"label": "Order Placed"}}]})
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        for e in els:
            if e.get("containerId") == "confirm":
                e["text"] = "Order\nPlaced"   # soft wrap at the space
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": els},
                                base_revn=2)
        facts = rec["artifacts"].get("checkout-flow", {}).get("facts", [])
        self.assertNotIn("renamed", [f["fact"] for f in facts])


class TestRerouteOnMove(Base):
    """F1 (v0.1): moving a node re-routes its server-routed bound arrows;
    user-shaped multi-point paths are never silently flattened."""

    def test_move_reroutes_bound_arrows(self):
        self.store.apply_batch(seed_flow_batch())
        # the report's minimal repro: move one endpoint node far away
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [{"op": "mod", "id": "checkout",
                                         "attrs": {"y": 600}}]})
        els = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        t1, node = els["t1"], els["checkout"]
        ex = t1["x"] + t1["points"][-1][0]
        ey = t1["y"] + t1["points"][-1][1]
        self.assertTrue(node["x"] - 14 <= ex <= node["x"] +
                        node["width"] + 14)
        self.assertTrue(node["y"] - 14 <= ey <= node["y"] +
                        node["height"] + 14)
        # binding survived the reroute
        self.assertEqual(t1["endBinding"]["elementId"], "checkout")
        # and the lint agrees: no detached-endpoint error
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertFalse([e for e in lint["errors"] if "t1" in e])

    def test_user_shaped_arrow_not_flattened(self):
        self.store.apply_batch(seed_flow_batch())
        els = self.scene()
        for e in els:
            if e["id"] == "t1":  # user hand-bends the arrow: 3-point path
                e["points"] = [[0, 0], [40, -60], [80, 0]]
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els}, base_revn=1)
        self.store.apply_batch({"base_revn": 2, "artifact": "checkout-flow",
                                "ops": [{"op": "mod", "id": "checkout",
                                         "attrs": {"y": 600}}]})
        t1 = next(e for e in self.store.scenes["checkout-flow"]
                  if e["id"] == "t1")
        self.assertEqual(len(t1["points"]), 3)  # geometry untouched
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertTrue(any("user-shaped" in w and "t1" in w
                            for w in lint["warnings"]))


class TestRewireValidation(Base):
    """F2 (v0.1): a rewire that cannot bind rejects the batch with a named
    error instead of silently doing nothing."""

    def test_unbound_rewire_rejects(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [{"op": "add", "element":
                                         {"type": "arrow", "id": "t-free",
                                          "x": 300, "y": 300, "width": 100,
                                          "height": 0}}]})
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch({"base_revn": 2,
                                    "artifact": "checkout-flow",
                                    "ops": [{"op": "mod", "id": "t-free",
                                             "attrs": {"to": "cart"}}]})
        self.assertIn("unbound", str(cm.exception))
        t = next(e for e in self.store.scenes["checkout-flow"]
                 if e["id"] == "t-free")
        self.assertIsNone(t["endBinding"])  # nothing partial landed

    def test_joint_from_to_rewire_succeeds(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [{"op": "add", "element":
                                         {"type": "arrow", "id": "t-free",
                                          "x": 300, "y": 300, "width": 100,
                                          "height": 0}},
                                        {"op": "mod", "id": "t-free",
                                         "attrs": {"from": "cart",
                                                   "to": "payment"}}]})
        t = next(e for e in self.store.scenes["checkout-flow"]
                 if e["id"] == "t-free")
        self.assertEqual(t["startBinding"]["elementId"], "cart")
        self.assertEqual(t["endBinding"]["elementId"], "payment")


class TestIntentEcho(Base):
    def test_echo_reports_bindings_and_deletion(self):
        self.store.apply_batch(seed_flow_batch())
        ops = [{"op": "mod", "id": "t3", "attrs": {"to": "checkout"}},
               {"op": "del", "id": "confirm"}]
        # ops are echoed against the FINAL scene
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [ops[0]]})
        echo = canvas.intent_echo([ops[0]],
                                  self.store.scenes["checkout-flow"])
        self.assertTrue(any("t3 binds payment → checkout" in ln
                            for ln in echo))
        self.store.apply_batch({"base_revn": 2, "artifact": "checkout-flow",
                                "ops": [ops[1]]})
        echo = canvas.intent_echo([ops[1]],
                                  self.store.scenes["checkout-flow"])
        self.assertTrue(any("confirm deleted" in ln for ln in echo))


def port_node(etype="rectangle", x=100, y=100, w=200, h=80):
    """A node to read ports off: centre (200,140), faces at 100/300/100/180.

    Args:
        etype: Element type — `rectangle`, `diamond` or `ellipse`.
        x: Box left.
        y: Box top.
        w: Box width.
        h: Box height.

    Returns:
        The node element.
    """
    return {"id": "n", "type": etype, "x": x, "y": y,
            "width": w, "height": h}


# Where each face's midpoint is on `port_node`, and a point 60px out
# along the inward normal — an approach that is square by construction.
PORT_SPECIMENS = {"left": ((100.0, 140.0), (40.0, 140.0)),
                  "right": ((300.0, 140.0), (360.0, 140.0)),
                  "top": ((200.0, 100.0), (200.0, 40.0)),
                  "bottom": ((200.0, 180.0), (200.0, 240.0))}


class TestPortReading(unittest.TestCase):
    """`endpoint_port` / `port_approach` — the narration's reading layer.

    The vocabulary that lets the echo say WHERE on a node an arrow lands,
    not just which nodes it joins. Measured against the 24 frozen
    artifacts before it landed: 346 bound endpoints read 229 at a port,
    47 on a face off centre, and 70 refused — and the refusal set is what
    keeps the along-face and oblique specimens out of the narration.
    """

    def test_each_face_midpoint_reads_as_that_port(self):
        """A square approach onto a face midpoint names that face."""
        for side, (foot, adj) in PORT_SPECIMENS.items():
            with self.subTest(side=side):
                port, off, at = canvas.endpoint_port(port_node(), adj, foot)
                self.assertEqual(port, side)
                self.assertEqual(off, 0.0)
                self.assertTrue(at)

    def test_both_poles_of_a_face_read_off_port_with_a_signed_offset(self):
        """Off centre is nameable, and the sign is right/down positive.

        The sign is the one thing here a caller could get backwards
        silently, so it is pinned on both poles of all four faces rather
        than inferred from one.
        """
        for side, (foot, adj) in PORT_SPECIMENS.items():
            along = (0.0, 1.0) if side in ("left", "right") else (1.0, 0.0)
            for pole in (-30.0, 30.0):
                with self.subTest(side=side, pole=pole):
                    slid = (foot[0] + along[0] * pole,
                            foot[1] + along[1] * pole)
                    port, off, at = canvas.endpoint_port(
                        port_node(), (adj[0] + along[0] * pole,
                                      adj[1] + along[1] * pole), slid)
                    self.assertEqual(port, side)
                    self.assertEqual(off, pole)
                    self.assertFalse(at)

    def test_a_foot_on_no_face_reads_off_shape(self):
        """Dead centre of a rectangle is on no face and gets no name."""
        self.assertEqual(
            canvas.endpoint_port(port_node(), (40.0, 140.0), (200.0, 140.0)),
            ("off-shape", 0.0, False))

    def test_the_approach_disqualifies_a_foot_that_stands_on_a_port(self):
        """A foot ON the port whose arrival slides ALONG the face.

        The correction the spike's sketched triple could not express: the
        naive foot-only reading calls this a port (it is one, to the
        pixel) and narrates "leaves the left of n" about an arrow that
        never crosses the left face at all. 19 of the corpus's 346
        endpoints are this shape, including both ends of the specimen
        that motivated the whole check.
        """
        node, (foot, _adj) = port_node(), PORT_SPECIMENS["left"]
        # travelling straight DOWN the left face, arriving at its midpoint
        self.assertEqual(canvas.endpoint_port(node, (100.0, 40.0), foot),
                         ("off-approach", 0.0, False))
        # and the naive reading it replaces would have named the face
        self.assertEqual(canvas._edge_side(node, foot[0], foot[1]), "left")

    def test_an_oblique_approach_is_refused_and_a_near_square_one_is_not(self):
        """`NORMAL_COS` is a threshold, not a blanket refusal.

        Both poles of the 25-degree bar, so a classifier that had gone
        blanket-silent — the failure that reads as "nothing to say about
        this drawing" — cannot pass.
        """
        node, (foot, _adj) = port_node(), PORT_SPECIMENS["left"]
        # 30.3 degrees off the inward normal: refused
        self.assertEqual(
            canvas.endpoint_port(node, (foot[0] - 60, foot[1] - 35), foot),
            ("off-approach", 0.0, False))
        # 19.3 degrees off the same normal: named
        self.assertEqual(
            canvas.endpoint_port(node, (foot[0] - 60, foot[1] - 21), foot),
            ("left", 0.0, True))

    def test_a_conic_reads_its_ink_and_not_only_its_box(self):
        """A foot on a rhombus's facet is on a face; a bbox reader sees none.

        The ink is where `edge_anchor` and `_fan_point` both put a foot,
        and it leaves the box everywhere except the four midpoints — so a
        classifier keyed on the box alone goes silent about most of a
        conic's perimeter.
        """
        for etype in ("diamond", "ellipse"):
            with self.subTest(etype=etype):
                node = port_node(etype)
                foot, adj = PORT_SPECIMENS["top"]
                port, off, at = canvas.endpoint_port(node, adj, foot)
                self.assertEqual((port, off, at), ("top", 0.0, True))
        # a point on the rhombus's upper-left facet, off the box entirely
        facet = (125.0, 130.0)
        port, off, at = canvas.endpoint_port(
            port_node("diamond"), (facet[0] - 60, facet[1]), facet)
        self.assertEqual(port, "left")
        self.assertEqual(off, -10.0)
        self.assertFalse(at)

    def test_a_degenerate_leg_is_never_named(self):
        """No leg, no direction, no face — however perfect the foot."""
        foot, _adj = PORT_SPECIMENS["left"]
        self.assertEqual(canvas.endpoint_port(port_node(), foot, foot),
                         ("off-approach", 0.0, False))
        self.assertEqual(canvas.endpoint_port(port_node(), None, foot),
                         ("off-approach", 0.0, False))

    def test_the_reading_does_not_depend_on_stored_focus(self):
        """The version-independence claim, pinned.

        The classifier's inputs are points and boxes; `focus` is not
        among them, so nothing it says can move when the focus work
        rewrites focus values. Measured over the corpus at three arms
        (zeroed, sign-flipped, randomised): 0 of 346 endpoints changed
        their reading. This drives the same claim through a live scene,
        so a future edit that reached for a stored focus fails here.
        """
        node = port_node()
        arrow = {"id": "a", "type": "arrow", "x": 40, "y": 140,
                 "points": [[0, 0], [60, 0]],
                 "endBinding": {"elementId": "n", "focus": 0.0, "gap": 0}}
        want = canvas.port_approach(node, arrow, True)
        self.assertEqual(want, ("left", 0.0, True))
        for focus in (0.0, 0.9, -0.9, 0.333, -0.333):
            with self.subTest(focus=focus):
                arrow["endBinding"]["focus"] = focus
                self.assertEqual(canvas.port_approach(node, arrow, True), want)

    def test_port_approach_reads_the_drawn_leg_of_a_curved_arrow(self):
        """Ink, not the stored chord — and the two disagree here.

        A curved final leg leaves its stored chord by up to 56.8 degrees,
        which is enough to flip the axis and name the wrong face. This
        specimen's chord leans 29.7 degrees off the inward normal, so a
        chord reader REFUSES an arrival the drawing makes square — the
        two readings differ in verdict, not merely in decimals.
        """
        node = port_node(x=300)      # left face at x=300, midpoint y=140
        arrow = {"id": "a", "type": "arrow", "x": 100, "y": 400,
                 "points": [[0, 0], [60, -180], [200, -260]],
                 "roundness": {"type": 2},
                 "endBinding": {"elementId": "n", "focus": 0, "gap": 0}}
        self.assertEqual(canvas.port_approach(node, arrow, True),
                         ("left", 0.0, True))
        seq, _prev = canvas._arrival_path(arrow, True)
        chord_prev = (arrow["x"] + arrow["points"][-2][0],
                      arrow["y"] + arrow["points"][-2][1])
        self.assertEqual(canvas.endpoint_port(node, chord_prev, seq[0]),
                         ("off-approach", 0.0, False))

    def test_an_arrow_with_no_segment_is_never_named(self):
        """A pointless arrow has no arrival to call square."""
        node = port_node()
        self.assertEqual(
            canvas.port_approach(node, {"id": "a", "type": "arrow",
                                        "x": 0, "y": 0, "points": []}, True),
            ("off-approach", 0.0, False))


class TestPortEcho(Base):
    """The echo speaks the port vocabulary — and knows when not to.

    Driven through `check_batch`, which is the `apply --check` surface
    and the same reading an applied batch answers with: what the agent
    receives is the fact, not what a helper computes.
    """

    def setUp(self):
        """Seed the checkout flow, whose arrows all land on ports."""
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_the_echo_names_both_ports_of_a_bound_arrow(self):
        """The FIRING pole for the silence below.

        `intent_echo` named the nodes and said nothing about where on
        them an arrow landed, so an agent reading its own drawing back
        could not tell a connector crossing a node's face from one
        meeting it squarely. The port clause is APPENDED, so `binds A →
        B` stays the head of the sentence.
        """
        out = self.store.check_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "mod", "id": "t1", "attrs": {"strokeWidth": 2}}]})
        self.assertTrue(out["ok"], out["errors"])
        ln = out["intent_echo"][0]
        self.assertIn("t1 binds cart → checkout", ln)
        self.assertIn("leaves the right of cart", ln)
        self.assertIn("arrives at the left of checkout", ln)

    def slide_t1(self, y):
        """Put t1's tail on cart's right face at row `y`, arriving square.

        cart is (40,120) 140x60, so its right face runs x=180 over
        y=120..180 with its midpoint at 150; checkout's left face is
        x=260 over the same rows. A horizontal leg between them is
        square to both, so `y` alone moves the feet off centre and the
        approach never becomes the reason for a refusal.

        Args:
            y: The row both feet sit on.

        Returns:
            The first `intent_echo` line of the dry run.
        """
        out = self.store.check_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "mod", "id": "t1",
                     "attrs": {"x": 180, "y": y,
                               "points": [[0, 0], [80, 0]]}}]})
        self.assertTrue(out["ok"], out["errors"])
        return out["intent_echo"][0]

    def test_the_echo_names_an_off_centre_foot_by_its_edge(self):
        """The second phrasing arm — 47 of the corpus's 346 endpoints.

        A foot square to a face but away from its midpoint is still worth
        naming; it is the difference between "meets Checkout on the left"
        and knowing nothing about the connector at all. The possessive
        form is deliberate and is what distinguishes it from the at-port
        sentence: "cart's right EDGE" is a face, "the right of cart" is
        the port.
        """
        ln = self.slide_t1(130)
        self.assertIn("t1 binds cart → checkout", ln)
        self.assertIn("leaves cart's right edge, off centre", ln)
        self.assertIn("arrives at checkout's left edge, off centre", ln)
        # the at-port wording is NOT what an off-centre foot gets
        self.assertNotIn("the right of cart", ln)

    def test_the_off_centre_phrase_never_says_which_way(self):
        """Two feet off centre in OPPOSITE directions, one sentence.

        The offset's sign is the one thing this vocabulary refuses to
        speak, because the sign is what the client and the server
        currently disagree about on bottom and left faces — narrating a
        direction would be wrong today and right once that settles. The
        property is pinned as an identity rather than as the absence of
        some list of words, so a phrasing that started leaking direction
        by any wording at all fails here.
        """
        low, high = self.slide_t1(130), self.slide_t1(170)
        # -20px and +20px along the same faces
        self.assertEqual(low, high)
        self.assertIn("off centre", low)

    def test_the_echo_is_silent_about_an_end_the_approach_forbids(self):
        """The SILENCE pole, beside the firing above.

        The tail is dragged down the cart's right face so the arrow now
        leaves ALONG that face instead of across it. A foot-only reading
        would still call it the right port — it is one, to the pixel —
        and narrate a face the drawing does not show the arrow crossing.
        The refusal is silence, not a hedge: doctrine tells the agent to
        describe the path there rather than guess a face, and a hedged
        side name is the thing it cannot un-read.
        """
        out = self.store.check_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "mod", "id": "t1",
                     "attrs": {"x": 180, "y": 178,
                               "points": [[0, 0], [0, -28], [80, -28]]}}]})
        self.assertTrue(out["ok"], out["errors"])
        ln = out["intent_echo"][0]
        # the binding sentence is untouched — both nodes still named
        self.assertIn("t1 binds cart → checkout", ln)
        self.assertNotIn("cart's", ln)
        self.assertNotIn("leaves", ln)
        # and the far end, which nothing moved, still speaks
        self.assertIn("arrives at the left of checkout", ln)


class TestLintTiers(Base):
    def test_detached_endpoint_is_error(self):
        self.store.apply_batch(seed_flow_batch())
        els = self.scene()
        for e in els:
            if e["id"] == "checkout":   # user drags the node away; client
                e["x"], e["y"] = 900, 900  # normally re-routes — simulate a
        # broken client that didn't
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els}, base_revn=1)
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertTrue(any("claims to bind" in e for e in lint["errors"]))

    def test_budgets_and_bidirectional(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [{"op": "mod", "id": "t1",
                                         "attrs": {"startArrowhead":
                                                   "arrow"}}]})
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertTrue(any("both ways" in w for w in lint["warnings"]))

    def test_fan_spreads_shared_attach_points(self):
        # three sources converging on one target: anchors must not collide
        ops = []
        for i, nid in enumerate(("a", "b", "c")):
            ops.append({"op": "add", "element":
                        {"type": "rectangle", "id": nid, "label": nid.upper(),
                         "x": 0, "y": i * 160, "width": 160, "height": 64,
                         "role": "node"}})
        ops.append({"op": "add", "element":
                    {"type": "rectangle", "id": "hub", "label": "Hub",
                     "x": 480, "y": 160, "width": 160, "height": 64,
                     "role": "node"}})
        for nid in ("a", "b", "c"):
            ops.append({"op": "add", "element":
                        {"type": "arrow", "id": "t-%s" % nid},
                        "from": nid, "to": "hub"})
        self.store.apply_batch({"base_revn": 0, "artifact": "fan",
                                "create": {"id": "fan", "name": "Fan",
                                           "type": "flow"}, "ops": ops})
        lint = canvas.lint_layout(self.store.scenes["fan"])
        self.assertFalse([w for w in lint["warnings"]
                          if "share an attach point" in w])

    def test_flow_kind_invariants(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "k", "create":
                {"id": "k", "name": "K", "type": "flow"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "s",
                                          "label": "In", "x": 0, "y": 0,
                                          "width": 160, "height": 64,
                                          "role": "node",
                                          "customData": {"kind": "source"}}},
                {"op": "add", "element": {"type": "rectangle", "id": "k2",
                                          "label": "Out", "x": 320, "y": 0,
                                          "width": 160, "height": 64,
                                          "role": "node",
                                          "customData": {"kind": "sink"}}},
                {"op": "add", "element": {"type": "arrow", "id": "t"},
                 "from": "s", "to": "k2"},
            ]})
        lint = canvas.lint_layout(self.store.scenes["k"])
        self.assertTrue(any("source directly to a sink" in e
                            for e in lint["errors"]))


class TestHygiene(Base):
    def test_round_stamped_on_save_records(self):
        rec, _ = self.store.apply_batch(seed_flow_batch())
        self.assertEqual(rec["round"], 1)      # agent move OPENS round 1
        els = self.scene()
        urec = self.store.commit(author="user",
                                 new_scenes={"checkout-flow": els},
                                 base_revn=1)
        self.assertEqual(urec["round"], 1)     # user replies within round 1

    def test_replay_restores_binding_shape(self):
        self.store.apply_batch(seed_flow_batch())
        # rewire produces a mod change whose binding values are normalized
        # id strings in the save record
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [{"op": "mod", "id": "t3",
                                         "attrs": {"to": "checkout"}}]})
        state = self.store.state_at(2)
        t3 = next(e for e in state["checkout-flow"]["elements"]
                  if e["id"] == "t3")
        self.assertIsInstance(t3["endBinding"], dict)
        self.assertEqual(t3["endBinding"]["elementId"], "checkout")

    def test_divergence_policy_silences_class(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-wf",
            "create": {"id": "checkout-wf", "name": "WF",
                       "type": "wireframe"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "blk",
                                          "label": "Pay button", "x": 0,
                                          "y": 0, "width": 160, "height": 64,
                                          "kind": "button", "role": "node"}},
                {"op": "registry", "action": "add_mapping",
                 "concept": "checkout",
                 "elements": ["checkout-wf#blk", "checkout-flow#payment"]},
            ]})
        # without a policy, editing one side fires a tripwire
        els = self.scene()
        for e in els:
            if e["id"] == "payment-label":
                e["text"] = "Pay Now"
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": els},
                                base_revn=2)
        self.assertTrue(rec["tripwires"])
        # class ruling via annotate_mapping pattern: one record
        self.store.apply_batch({
            "base_revn": 3, "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "annotate_mapping",
                     "pattern": {"types": ["wireframe", "flow"]},
                     "note": "intentionally-divergent: blocks name "
                             "sections, steps name work"}]})
        self.assertEqual(
            len(self.store.registry["divergence_policies"]), 1)
        els = self.scene()
        for e in els:
            if e["id"] == "payment-label":
                e["text"] = "Pay Later"
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": els},
                                base_revn=4)
        self.assertFalse(rec["tripwires"])  # the class is ruled, silent


class TestElbowRouter(Base):
    def test_off_axis_pair_gets_orthogonal_elbow(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "e", "create":
                {"id": "e", "name": "E", "type": "flow"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "a",
                                          "label": "A", "x": 100, "y": 100,
                                          "width": 160, "height": 64,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "rectangle", "id": "b",
                                          "label": "B", "x": 500, "y": 600,
                                          "width": 160, "height": 64,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "arrow", "id": "t"},
                 "from": "a", "to": "b"},
            ]})
        t = next(e for e in self.store.scenes["e"] if e["id"] == "t")
        pts = t["points"]
        self.assertEqual(len(pts), 3)
        for i in range(1, len(pts)):
            seg_dx = abs(pts[i][0] - pts[i - 1][0])
            seg_dy = abs(pts[i][1] - pts[i - 1][1])
            self.assertTrue(seg_dx < 0.5 or seg_dy < 0.5,
                            "segment %d not axis-aligned: %r" % (i, pts))
        # aligned pairs stay straight
        t3 = None
        self.store.apply_batch(seed_flow_batch(base_revn=1))
        t3 = next(e for e in self.store.scenes["checkout-flow"]
                  if e["id"] == "t3")
        self.assertEqual(len(t3["points"]), 2)
        # no diagonal warning on the elbowed artifact
        lint = canvas.lint_layout(self.store.scenes["e"])
        self.assertFalse([w for w in lint["warnings"]
                          if "diagonally" in w])


class TestSnapshotPieces(Base):
    def test_render_svg_carries_labels_and_escapes(self):
        self.store.apply_batch(seed_flow_batch())
        svg, w, h = canvas.render_svg(self.store.scenes["checkout-flow"])
        self.assertIn("<svg", svg)
        self.assertIn(">Cart<", svg)
        self.assertGreater(w, 100)
        self.assertGreater(h, 50)
        els = [{"id": "t", "type": "text", "x": 0, "y": 0, "width": 100,
                "height": 20, "text": "a < b & c"}]
        svg2, _, _ = canvas.render_svg(els)
        self.assertIn("a &lt; b &amp; c", svg2)

    def test_render_svg_scales_uniformly_and_bounds_text(self):
        # v0.3: a scene wider than 4000px must scale BOTH dimensions
        # (the old independent clamp squashed the aspect ratio)
        els = [{"id": "a", "type": "rectangle", "x": 0, "y": 0,
                "width": 200, "height": 100},
               {"id": "b", "type": "rectangle", "x": 7800, "y": 0,
                "width": 200, "height": 100}]
        svg, w, h = canvas.render_svg(els)
        import re as _re
        vb = _re.search(r"viewBox='([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)'",
                        svg)
        vw, vh = float(vb.group(3)), float(vb.group(4))
        self.assertLessEqual(w, 4000)
        self.assertAlmostEqual(w / h, vw / vh, delta=0.5)
        # v0.3: text overflowing its stored width expands the bounds
        wide_text = "x" * 120
        els2 = [{"id": "t", "type": "text", "x": 0, "y": 0, "width": 40,
                 "height": 20, "text": wide_text, "fontSize": 16}]
        _, w2, _ = canvas.render_svg(els2)
        self.assertGreater(w2, 40 + 80)  # stored width + 2*pad

    def fake_png(self, w, h):
        """A structurally-valid PNG header of the given IHDR dimensions.

        Args:
            w: IHDR width.
            h: IHDR height.

        Returns:
            Raw bytes — enough of a file for `validate_png` to read.
        """
        import struct
        ihdr = struct.pack(">II5B", w, h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" +
                struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0\0\0\0" +
                b"\0" * 64)

    def test_validate_png(self):
        ok, why = canvas.validate_png(b"definitely not a png")
        self.assertFalse(ok)
        png = self.fake_png(10, 10)
        ok, why = canvas.validate_png(png)
        self.assertTrue(ok, why)
        ok, why = canvas.validate_png(png, want_w=2000)
        self.assertFalse(ok)  # dimension mismatch is the strong signal

    def test_a_raster_short_of_the_drawing_is_refused(self):
        """The truncation half of `validate_png` (v0.9 WP4).

        3000px of a 3640px drawing is 640px of picture missing, and the
        old symmetric tolerance — 20% of the requested width, so 728px —
        let it through as VALID=true with two nodes off the right edge.
        A raster short of the drawing has content cut off it, so it
        fails at 2px, the most an int-rounded scale can account for.
        """
        ok, why = canvas.validate_png(self.fake_png(3000, 200),
                                      3640, 200, min_bpp=0)
        self.assertFalse(ok, why)
        self.assertIn("cuts a 3640px drawing short", why)

    def test_a_raster_a_shade_over_the_drawing_is_fine(self):
        # the other direction, and the reason it is not symmetric: extra
        # pixels are ground, and cost bytes rather than content. The 2px
        # slack under is int-rounding of a fit scale, not tolerance.
        for png_w, want_w in ((3639, 3640), (3700, 3640)):
            ok, why = canvas.validate_png(self.fake_png(png_w, 200),
                                          want_w, 200, min_bpp=0)
            self.assertTrue(ok, (png_w, want_w, why))

    def test_the_window_can_hold_anything_render_svg_makes(self):
        """The snapshot's two ceilings are ONE number now.

        `rasterize_svg` opened a 3000x2000 window while `render_svg`
        only scaled a drawing down past 4000x3000, and every drawing in
        the gap was rendered at full size into a window too small to
        hold it. This asserts `render_svg`'s HALF of the shared-ceiling
        property: its output never exceeds `RASTER_MAX_*`. That
        `rasterize_svg` opens a window that big is pinned by the gated
        snapshot-cap mutant, not here — re-narrowing `rasterize_svg` to
        a literal would slip past this test.
        """
        els = [{"id": "a", "type": "rectangle", "x": 0, "y": 0,
                "width": 200, "height": 100},
               {"id": "b", "type": "rectangle", "x": 20000, "y": 9000,
                "width": 200, "height": 100}]
        _svg, w, h = canvas.render_svg(els)
        self.assertLessEqual(w, canvas.RASTER_MAX_W)
        self.assertLessEqual(h, canvas.RASTER_MAX_H)

    def test_a_frame_titled_wireframe_is_captioned_once(self):
        """r5-15: the export printed the artifact's name twice.

        A frame paints its own `name` just above its top-left corner and
        the caption goes a few pixels away at the drawing's top-left, so
        a wireframe whose one screen frame carries the artifact's name
        read it twice and the second looked like a second screen.
        """
        els = [{"id": "f", "type": "frame", "x": 0, "y": 0, "width": 400,
                "height": 300, "name": "Signals Dashboard"}]
        svg, _, _ = canvas.render_svg(els, title="Signals Dashboard")
        self.assertEqual(svg.count(">Signals Dashboard<"), 1)

    def test_a_frame_named_otherwise_keeps_the_caption(self):
        # the differential: suppression is on the NAME, so a frame named
        # for its screen rather than for the artifact says nothing about
        # which artifact this is, and the caption still has to
        els = [{"id": "f", "type": "frame", "x": 0, "y": 0, "width": 400,
                "height": 300, "name": "SCREEN — Breach detail"}]
        svg, _, _ = canvas.render_svg(els, title="Signals Dashboard")
        self.assertIn(">Signals Dashboard<", svg)
        self.assertIn(">SCREEN — Breach detail<", svg)


# content bbox 3560x100 -> the tab frames 3640x180 at scale 1
TAB_WIDE = [{"type": "rectangle", "id": "l", "x": 0, "y": 0,
             "width": 200, "height": 100, "role": "node"},
            {"type": "rectangle", "id": "r", "x": 3360, "y": 0,
             "width": 200, "height": 100, "role": "node"}]
# content bbox 8000x100 -> the tab frames 8080x180, while render_svg
# scales the same drawing down to fit RASTER_MAX_W
TAB_HUGE = [{"type": "rectangle", "id": "l", "x": 0, "y": 0,
             "width": 200, "height": 100, "role": "node"},
            {"type": "rectangle", "id": "r", "x": 7800, "y": 0,
             "width": 200, "height": 100, "role": "node"}]


class TestSnapshotTierOne(Base):
    """The connected tab's export — the tier the agent gets FIRST.

    v0.9 WP4 taught `validate_png` that a raster short of the drawing has
    content cut off it, and wired the yardstick into tier 2. Tier 1
    called it with `min_bpp` only, so the tier the agent reaches first,
    and quotes as the picture, was checked for health and never for
    completeness. These tests drive `cmd_snapshot` with a fake tab: the
    transport is faked, the decision under test — what tier 1 does with
    the bytes — is the shipped one.

    The yardstick is the EXPORT path's, not `render_svg`'s. The tab
    exports through `exportToBlob` with `exportPadding: 40` and no
    `exportScale` (App.tsx, screenshot servicing), so it frames to the
    content's own bounding box plus 40px each side — independent of the
    caption, footnotes and uniform downscale that set `render_svg`'s
    dimensions. Task 49 wired that box in as `ink_extent` and made it a
    FLOOR rather than a target.

    Task 49 ALSO dropped the ceiling, on the reading that a missing
    `exportScale` leaves the scale to the browser's `devicePixelRatio`,
    so a retina export would be an honest 2-3x. The self-report task
    measured that and it is false — `exportToBlob` overrides the scale
    multiply and the same artifact comes back byte-identical at
    `deviceScaleFactor` 1 and 2 — so the ceiling is back
    (`ceiling_slack`), sized to admit the +20px honest over-margin and
    the 1.41x a rotated drawing could reach, and to refuse a doubled
    raster. `..._refuses_an_export_twice_the_drawing` is that, and
    `..._keeps_an_export_a_little_over_the_floor` is its live half; the
    retina test they replaced asserted the opposite and is written up in
    the second of those.

    The floor is deliberately loose (`floor_slack`), and
    `..._admits_every_measured_real_export` is why: the client rebuilds
    text geometry from real font metrics before it frames, so honest
    exports land inside the saved geometry's box often enough that a
    strict floor refused two of six real corpus artifacts. Read that
    test before touching the slack — and read
    `..._admits_every_measured_real_export_strictly` before concluding
    the 96px is what tier 1 checks to, because on most axes it is not:
    the slack stopped being the sole gate when `exact_ink_extent` and
    the client's byte count joined it.
    Curator batch 15, item 1 (2026-08-14); flipped 2026-08-15.
    """

    def fat_png(self, w, h, bpp=0.06):
        """A PNG header of the given IHDR dims, padded past the bpp floor.

        Tier 1 keeps a 0.05 bytes/px floor against the fonts-race
        corruption, so a header-sized file is refused for a reason that
        has nothing to do with these tests.

        The default sits just over that floor, which is the right shape
        for a test of the GEOMETRY checks and the wrong one for a test of
        truncation: at 0.06 a fifth of the file is also a fifth off the
        density, and the raster gets refused for being thin rather than
        for being short. Real exports are nowhere near that tight — the
        six measured corpus artifacts run 0.07 to 0.11 — so a truncation
        pin passes `bpp` to reproduce a real one, and then the density
        check survives the cut exactly as it does in the field. That is
        the whole reason `bytes` had to be a protocol field.

        Args:
            w: IHDR width.
            h: IHDR height.
            bpp: Bytes per pixel to pad to.

        Returns:
            Raw bytes sitting at approximately `bpp` bytes/px.
        """
        import struct
        ihdr = struct.pack(">II5B", w, h, 8, 2, 0, 0, 0)
        head = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" +
                ihdr + b"\0\0\0\0")
        return head + b"\0" * max(0, int(w * h * bpp) - len(head))

    def snapshot(self, els, png_w, png_h, aid="a", report=None,
                 truncate=0, bpp=0.06):
        """Run `cmd_snapshot` against a tab that answers with one PNG.

        The fake stands in for the transport only: health, the screenshot
        request, and the shot file landing in `shots_dir` are exactly the
        three things the real tab does. Headless is off so a refusal at
        tier 1 falls to the deterministic SVG tier rather than needing a
        browser.

        `report` defaults to None, which makes every caller that omits it
        an OLD-CLIENT case — the pre-self-report bundle posting
        `{id, data_url}` and nothing else. That is deliberate: the
        compatibility pole is the one most easily lost, so the tier's
        whole existing body of tests sits on it by construction, and
        `..._takes_an_export_from_a_client_that_says_nothing` names it.

        Args:
            els: Elements to seed the artifact with.
            png_w: IHDR width of the PNG the tab hands back.
            png_h: IHDR height of the PNG the tab hands back.
            aid: Artifact to create and snapshot. Only a test calling
                this TWICE needs it — the second create would collide on
                the id, and the second batch on `base_revn`.
            report: The client's self-report, written to the sidecar the
                real handler writes. `None` writes no sidecar at all.
                Pass `True` for "whatever an honest client would have
                said about these exact bytes", which is what every test
                of a check OTHER than A1 wants.
            truncate: Bytes to drop off the END of the file after the
                report is computed — the transit-truncation case. The
                report still describes the whole file, which is the
                entire point: the client cannot know what was lost.
            bpp: Density of the PNG the tab hands back; see `fat_png`.
                Any test that truncates should raise it to a real
                export's, or the density floor catches the cut first and
                the pin proves nothing about the check it names.

        Returns:
            Everything `cmd_snapshot` printed.
        """
        self.store.apply_batch(
            {"base_revn": self.store.head_revn(), "artifact": aid,
             "create": {"id": aid, "name": aid.upper(), "type": "flow"},
             "ops": [{"op": "add", "element": dict(e)} for e in els]})
        self.project.shots_dir.mkdir(parents=True, exist_ok=True)
        self.project.state_path.write_text(
            json.dumps({"url": "http://127.0.0.1:9/"}), encoding="utf-8")

        def fake_http(url, payload=None, timeout=10.0):
            if url.endswith("api/health"):
                return {"ok": True}
            if url.endswith("api/screenshot/request"):
                raw = self.fat_png(png_w, png_h, bpp=bpp)
                said = report
                if said is True:
                    said = {"bytes": len(raw), "png_w": png_w,
                            "png_h": png_h, "artifact": aid}
                if said is not None:
                    canvas.write_json(
                        self.project.shots_dir / "shot-7.json", said)
                (self.project.shots_dir / "shot-7.png").write_bytes(
                    raw[:len(raw) - truncate] if truncate else raw)
                return {"id": 7}
            if url.endswith("api/state"):
                return {"screenshot_requests": []}
            raise AssertionError("unexpected call: %s" % url)

        args = argparse.Namespace(
            project=str(self.tmp), artifact=aid,
            out=str(self.tmp / "snap.png"), tab_timeout=8, no_tab=False,
            no_headless=True)
        buf = io.StringIO()
        with mock.patch.object(canvas, "http_json", fake_http), \
                contextlib.redirect_stdout(buf):
            canvas.cmd_snapshot(args)
        return buf.getvalue()

    def test_tier_1_refuses_a_tab_export_short_of_the_drawing(self):
        """FLIPPED (v0.9 task 49): tier 1 measured the export.

        The v0.9 WP4 shape, on the other tier: 3000px of a drawing the
        tab frames at 3640px was 640px of picture missing off the right
        edge, and tier 1 reported `VALID=true` over it. `cmd_snapshot`
        now computes that 3640 with `ink_extent` — the same padded box
        `render_svg` frames with, taken BEFORE the `RASTER_MAX_*` clamp —
        and hands it to `validate_png` as a floor. A LOOSE one — the
        fix round sized `floor_slack` at 182px here, against a defect
        640px deep — because a floor is checked against a client that
        re-measures its own text before framing. One assertion, carrying
        both magnitude and direction: the NOTE can only be printed by a
        refusal, and it names 3000 against 3640, i.e. short rather than
        padded.
        """
        out = self.snapshot(TAB_WIDE, 3000, 180)
        self.assertIn("width 3000 cuts a 3640px drawing short", out, out)
        # ...and the NOTE that carries it. `..._passes_every_check_at_
        # once` asserts this exact prefix is ABSENT from an honest
        # export; the rule-8 census (2026-08-16) found nothing anywhere
        # asserting it present, so that silence could not tell a clean
        # pass from a `cmd_snapshot` that had stopped narrating its
        # refusals. Same entry point, same run — this is where the
        # string is already produced, so the pole costs no second
        # snapshot.
        self.assertIn("NOTE=tab export attempt", out, out)

    def test_tier_1_accepts_a_full_content_tab_export(self):
        """The live half: a complete export is taken, at tier 1.

        Without this the red above passes the day `cmd_snapshot` stops
        reaching tier 1 at all — a dead path and a fixed one look
        identical from a refusal.
        """
        out = self.snapshot(TAB_WIDE, 3640, 180)
        self.assertIn("TIER=1", out)
        self.assertIn("VALID=true", out)

    def test_tier_1_keeps_the_export_of_a_downscaled_drawing(self):
        """The other pole, and the reason the fix is not a dims pass.

        `render_svg` caps a drawing at `RASTER_MAX_*` and hands back
        4000x89 for this one, while the tab — framing to content at
        scale 1 — legitimately returns 8080x180. Handing tier 1
        `render_svg`'s dimensions would refuse that file as "far from
        requested", so this asserts the export the user can see is whole
        stays accepted. On the TIER line, not on `VALID`: every tier
        including the SVG fallback reports `VALID=true`, so a refusal
        here reads as a pass unless the tier is named.
        """
        _svg, w, h = canvas.render_svg([dict(e) for e in TAB_HUGE])
        self.assertEqual((w, h), (4000, 89))  # the mismatch is the point
        out = self.snapshot(TAB_HUGE, 8080, 180)
        self.assertIn("TIER=1", out, out)

    def test_tier_1_refuses_a_short_export_of_a_downscaled_drawing(self):
        """The sibling above keeps the naive fix out; alone it no longer
        can.

        `..._keeps_the_export_of_a_downscaled_drawing` was written to
        refuse `render_svg`'s clamped dimensions as the yardstick, and it
        did that by watching an 8080px export get rejected as "far from
        requested" against a 4000px ask. Task 49 dropped that upper
        branch for tier 1 (retina), which also disarmed the guard: a
        clamped 4000 used as a FLOOR accepts 8080 quite happily, so the
        naive fix passed the whole suite while measuring the wrong
        drawing. What it cannot do is refuse a TRUNCATED export of the
        same drawing — 5000px against 4000 looks fine to it, and against
        the true 8080 floor is 3080px of picture gone. Same defect the
        class was opened for, on the drawing where the clamp bites.
        """
        out = self.snapshot(TAB_HUGE, 5000, 180)
        self.assertIn("width 5000 cuts a 8080px drawing short", out, out)

    def test_the_floor_admits_every_measured_real_export(self):
        """The calibration, pinned to the exports it was measured from.

        `floor_slack` is not a guess and must not be re-tuned by taste.
        Six unedited corpus artifacts were driven through the app's own
        `exportToBlob` at `deviceScaleFactor: 1` and their PNG IHDRs read
        straight off disk, before `validate_png` ever saw them
        (spike-tier1-verify.md, 2026-08-15). Four framed at or above
        their floor; TWO CAME BACK INSIDE IT — 33px and 70px — with
        nothing whatever wrong with the pictures. Those two are why the
        slack exists, and the shipped floor refused them.

        Literals, not a re-derivation: re-running `ink_extent` here would
        only prove it agrees with itself, and the actual widths can only
        come from a browser. Shrinking the slack to the 64px the first
        report proposed puts `tearsheet-domain` back under by 6px, which
        is the failure this test exists to catch.

        This is no longer the whole tier-1 check and must not be read as
        the whole tolerance. `..._strictly` below runs the same six
        exports against `exact_ink_extent`, where the margins are +0 and
        the allowance is 8px rather than 96. The 96 stays because the
        modelling gap it absorbs is real; the precision came from adding
        gates beside it, and re-tuning this one would give back the
        measurement rather than the slack.
        """
        for name, floor_w, floor_h, got_w, got_h in (
                ("tearsheet-demo/tearsheet-wireframe", 1553, 1050,
                 1520, 1070),                    # −33 wide: refused before
                ("tearsheet-demo/tearsheet-domain", 1774, 1138,
                 1704, 1138),                    # −70 wide: refused before
                ("argus-r4-arm3/dashboard", 2700, 880, 2700, 900),
                ("argus-r4-arm4/dashboard-wireframe", 1590, 820,
                 1590, 840),
                ("argus-r5/daily-run", 910, 1344, 910, 1344),
                ("acceptance-tearsheet/tearsheet-flow", 2160, 724,
                 2160, 724)):
            with self.subTest(artifact=name):
                ok, why = canvas.validate_png(
                    self.fat_png(got_w, got_h), want_w=floor_w,
                    want_h=floor_h, min_bpp=0.05, want_is_floor=True)
                self.assertTrue(ok, "%s: %s" % (name, why))

    # the six measured artifacts, each as
    # (name, full floor, text-free floor, real export). The full floors
    # and exports are `..._admits_every_measured_real_export`'s; the
    # text-free column is `exact_ink_extent` over the same revisions,
    # measured by the self-report spike and re-run at this head before
    # `EXACT_SLACK` landed.
    MEASURED = (
        ("tearsheet-demo/tearsheet-wireframe", 1553, 1050, 1520, 940,
         1520, 1070),
        ("tearsheet-demo/tearsheet-domain", 1774, 1138, 1420, 1130,
         1704, 1138),
        ("argus-r4-arm3/dashboard", 2700, 880, 2700, 880, 2700, 900),
        ("argus-r4-arm4/dashboard-wireframe", 1590, 820, 1590, 820,
         1590, 840),
        ("argus-r5/daily-run", 910, 1344, 910, 1344, 910, 1344),
        ("acceptance-tearsheet/tearsheet-flow", 2160, 724, 2160, 664,
         2160, 724))

    def test_the_floor_admits_every_measured_real_export_strictly(self):
        """`EXACT_SLACK`'s calibration — and why 8 is not 96.

        The same six real exports as the test above, against the floor
        built only from ink the client does not re-derive. The margins
        are the whole argument for the number: **+0 on every axis of
        every artifact**, against −70 for the full floor. Not "close to"
        the export — at or under it, exactly, on all twelve axes, which
        is why this can be checked at 8px when the other needs 96.

        The reason is visible one level down and is the load-bearing
        fact: every one of the twenty-four edges of these six text-free
        boxes is owned by a `frame`, `rectangle` or `ellipse`, and no
        arrow owns an edge on any of them. Those are stored-box classes
        the client never re-measures, so the agreement is to the pixel
        rather than to a tolerance. The 8 is not headroom this sample
        needed — it is `api.ts`'s documented ~7px of bound-arrow endpoint
        drift plus rounding, held for the scene where an arrow DOES own
        an edge.

        Without this the 8 is a number someone tidies away, and the
        margins are what stop `exact_ink_extent` being quietly widened
        back toward the full floor — a strict floor that has drifted
        below the export it judges is not strict, it is decorative, and
        nothing else here would notice.
        """
        for name, fw, fh, ew, eh, got_w, got_h in self.MEASURED:
            with self.subTest(artifact=name):
                self.assertLessEqual(
                    (ew, eh), (got_w + 0, got_h + 0),
                    "%s: the text-free floor %dx%d is no longer under the "
                    "real export %dx%d — the +0 margin this constant was "
                    "sized on is gone" % (name, ew, eh, got_w, got_h))
                ok, why = canvas.validate_png(
                    self.fat_png(got_w, got_h), want_w=fw, want_h=fh,
                    exact_w=ew, exact_h=eh, min_bpp=0.05,
                    want_is_floor=True)
                self.assertTrue(ok, "%s: %s" % (name, why))

    def test_the_strict_floor_leaves_out_the_ink_the_client_remeasures(self):
        """What `exact_ink_extent` excludes, and that it excludes only that.

        Found by mutation, not by design: with the six-row table above
        holding its floors as literals, `exact_ink_extent` could be
        changed to return the FULL extent — dropping the text filter
        entirely, which is its whole reason to exist — and the entire
        suite stayed green. The calibration pins prove the constant 8 is
        safe against the numbers; nothing proved the numbers still come
        from the function that computes them.

        So this asserts the rule directly. A text element past the right
        edge of everything else owns the full box's width and must own
        none of the strict one, and the strict box must be exactly what
        the remaining elements give — not merely smaller, which a filter
        that dropped the wrong class would also satisfy.

        Two edges of the rule get their own assertions because both are
        load-bearing and neither is obvious. Bound text is text: a label
        inside a container is re-measured exactly like a free one, and
        the container it belongs to stays. And a drawing made of nothing
        but text has NO strict box — `None`, not a zero-sized one, so the
        caller skips the check rather than measuring against nothing.
        """
        rect = {"type": "rectangle", "id": "r", "x": 100, "y": 100,
                "width": 200, "height": 100}
        text = {"type": "text", "id": "t", "x": 400, "y": 100,
                "width": 300, "height": 40, "fontSize": 20,
                "text": "a label reaching past the box"}
        self.assertGreater(canvas.ink_extent([rect, text])[2],
                           canvas.exact_ink_extent([rect, text])[2])
        self.assertEqual(canvas.exact_ink_extent([rect, text]),
                         canvas.ink_extent([rect]))
        self.assertEqual(
            canvas.exact_ink_extent([rect, dict(text, containerId="r")]),
            canvas.ink_extent([rect]))
        self.assertIsNone(canvas.exact_ink_extent([text]))

    def test_the_measured_table_still_describes_the_corpus(self):
        """The calibration's literals against the fixtures they came from.

        `..._admits_every_measured_real_export` holds its numbers as
        literals for a good reason — the export widths can only come from
        a browser, and re-deriving the floor would only prove `ink_extent`
        agrees with itself. But that reasoning covers the EXPORT column
        and not the two floor columns, which are pure server-side
        derivations of scenes sitting in this repo. Left unchecked they
        are a table that keeps saying what was true in August while the
        code moves underneath it, which is the surviving-guard failure
        this corpus has now recorded twice.

        So both floors are re-derived here from the frozen fixtures and
        compared to the literals. A drift in either direction is a real
        event: it means the floor tier 1 measures against is no longer
        the one the constants were sized on, and the constants must be
        re-measured rather than the table quietly updated.
        """
        root = Path(__file__).resolve().parent / "fixtures"
        for name, fw, fh, ew, eh, _got_w, _got_h in self.MEASURED:
            fixture, aid = name.split("/")
            with self.subTest(artifact=name):
                tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-calib-"))
                try:
                    shutil.copytree(root / fixture, tmp / "project_knowledge")
                    els = canvas.Store(canvas.Project(tmp)).scenes[aid]
                    full = canvas.ink_extent(els)
                    strict = canvas.exact_ink_extent(els)
                    self.assertEqual(
                        (int(full[2]), int(full[3]), int(strict[2]),
                         int(strict[3])), (fw, fh, ew, eh),
                        "%s has drifted off the floors `floor_slack` and "
                        "`EXACT_SLACK` were calibrated against" % name)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_the_band_note_blames_text_only_where_text_is_to_blame(self):
        """Review fix round 1, finding 3 — a confident wrong diagnosis.

        The NOTE fires whenever a band exceeds `EXACT_SLACK` and used to
        attribute it to text owning that edge in every case. A band opens
        for two unrelated reasons, though: text setting the drawing's
        extent on that axis (`exact_ink_extent` short of the full floor),
        or the raster simply sitting ABOVE the floor, which has nothing
        to do with text. On `dashboard` and `dashboard-wireframe` the
        text-free extent equals the full floor on both axes — text owns
        no edge whatever — and the 28px band is the +20px honest
        over-margin plus the slack. A third of the measured corpus got a
        cause that was not there.

        Both poles, because the repair could as easily have deleted the
        explanation as conditioned it, and the explanation is worth
        keeping where it is true: `TAB_WIDE` with a text-free extent
        SHORT of the floor must still say why, and the same drawing with
        the two equal must report the band and stop talking.
        """
        texty = [*TAB_WIDE, {"type": "text", "id": "t", "x": 3560,
                             "y": 0, "width": 200, "height": 40,
                             "fontSize": 20, "text": "a trailing label"}]
        full = int(canvas.ink_extent(texty)[2])
        self.assertGreater(full, int(canvas.exact_ink_extent(texty)[2]),
                           "the fixture no longer has text owning its "
                           "right edge, so the blaming pole tests nothing")
        out = self.snapshot(texty, full, 180, aid="texty")
        self.assertIn("TIER=1", out, out)
        self.assertIn("width checked to within", out, out)
        self.assertIn("text sets the drawing's width", out, out)

        # the same NOTE on a drawing whose extent text has no part in:
        # the band is real (an honest raster 20px over the floor) and the
        # explanation must not be.
        out = self.snapshot(TAB_WIDE, 3660, 180, aid="notexty")
        self.assertIn("TIER=1", out, out)
        self.assertIn("width checked to within 28px only", out, out)
        self.assertNotIn("text sets", out, out)

    def test_a_payload_larger_than_reported_reads_as_what_it_is(self):
        """Review fix round 1, finding 4 — a negative loss.

        A1 compares with `!=`, so the payload can disagree in either
        direction, but the message subtracted unconditionally and printed
        "-4000 lost in transit". A negative quantity of loss is the kind
        of diagnostic that sends a reader hunting the wrong bug, and the
        stale-sidecar defect was one route to it — reachable by any
        report that disagrees the other way, so closing that route does
        not close this one.
        """
        raw = self.fat_png(3640, 180)
        ok, why = canvas.validate_png(
            raw, want_w=3640, want_h=180, min_bpp=0.05, want_is_floor=True,
            report={"bytes": len(raw) - 4000})
        self.assertFalse(ok)
        self.assertIn("4000 more than the tab made", why)
        self.assertNotIn("-4000", why)

    def test_the_blind_band_is_the_one_the_split_was_measured_to_buy(self):
        """What tier 1 actually promises, per axis, on real drawings.

        `VALID=true` does not mean whole; it means nothing more than this
        many pixels is gone off that edge. These twelve numbers are that
        promise on the six measured artifacts, and they are the
        justification for the entire protocol, so they are pinned rather
        than described: eight of twelve axes were 96px or worse before
        and six of twelve are now at `EXACT_SLACK`.

        Read the two columns together. Where the band did NOT move —
        `tearsheet-wireframe`'s height at 116, `tearsheet-domain`'s width
        at 26, `tearsheet-flow`'s height at 68 — a text element owns that
        edge of the drawing, `exact_ink_extent` has nothing to say about
        it, and `floor_slack` is the whole check. That residual does not
        shrink by tuning anything. It shrinks when the server measures
        text the way the browser does, and not before.

        TWO OF THE SPIKE'S SUMMARY FIGURES ARE WRONG and are corrected
        here rather than carried: it reported "median 16px, eight of
        twelve axes at 8px". Every per-axis number in its table is
        right, but the median of these twelve is 12 and the count at 8px
        is six. The aggregates were the part nobody re-derived.
        """
        before, after = [], []
        for name, fw, fh, ew, eh, got_w, got_h in self.MEASURED:
            with self.subTest(artifact=name):
                was = dict(canvas.blind_band(got_w, got_h, fw, fh))
                now = dict(canvas.blind_band(got_w, got_h, fw, fh, ew, eh))
                before += [was["width"], was["height"]]
                after += [now["width"], now["height"]]
        self.assertEqual(
            before, [63, 116, 26, 96, 135, 116, 96, 116, 96, 96, 108, 96],
            "the pre-split bands moved: `floor_slack` is the only input "
            "to these and it was not supposed to change")
        self.assertEqual(
            after, [8, 116, 26, 16, 8, 28, 8, 28, 8, 8, 8, 68],
            "the post-split bands moved off what the split was measured "
            "to buy")
        self.assertEqual(sorted(after)[5:7], [8, 16])   # median 12
        self.assertEqual(after.count(canvas.EXACT_SLACK), 6)

    def test_tier_1_keeps_an_export_a_little_over_the_floor(self):
        """REPLACES `..._keeps_a_retina_tab_export` (self-report task).

        WHAT WAS REPLACED AND WHY, because a green test that vanishes is
        the easiest kind of coverage to lose quietly. The retina test
        asserted that a raster at 2x and 3x the floor is TAKEN, on the
        premise that `exportToBlob` falls back to `devicePixelRatio` and
        an honest export is therefore 2-3x on a HiDPI monitor. That
        premise is measured false: the same artifact through the real
        protocol at `deviceScaleFactor` 1 and 2 comes back
        byte-identical, because `exportToBlob` hands the export path its
        own canvas-maker and overrides the scale multiply
        (spike-selfreport.md §7.1, re-measured 2026-08-15). The scenario
        it guarded cannot occur, and it was the stated reason the tier
        has no ceiling — so it was not merely inert, it was load-bearing
        for a real loss of discrimination. It is REPLACED rather than
        weakened: the sibling below now asserts 2x is refused, which is
        the exact opposite, and both cannot be right.

        What survives the replacement is the QUESTION the retina test was
        really asking — does the tier tolerate an export bigger than the
        floor? It must, and for a reason that outlived the wrong one:
        the client re-measures text before it frames, and that lands
        outside the saved box about as often as inside it. So the band
        is asserted at the sizes really measured rather than at an
        imagined 2x. +20px is the largest over-margin in the six-export
        calibration (`..._admits_every_measured_real_export`, two rows at
        exactly that).

        THE THIRD CASE HAS OUTLIVED ITS REASON and is kept as a bare
        headroom pin. It was the theoretical worst case — a drawing whose
        extent is set end to end by one element rotated 45°, framing
        1.41x the floor because `ink_extent` bounded it unrotated. v0.9
        TASK-MICROFIX taught `ink_extent` to read `angle`, so the floor
        now rotates with the element and that export cannot be 1.41x
        over. The size is retained rather than deleted because
        `ceiling_slack` still carries the 50% that was sized on it and
        this is the only assertion standing between that band and a
        silent narrowing; it now pins the BAND, not the scenario. Re-read
        this together with `ceiling_slack`'s docstring if either moves.
        """
        for over_w, over_h, why in (
                (20, 20, "the measured worst honest over-margin"),
                (0, 20, "dashboard and dashboard-wireframe exactly: the "
                        "width lands on the floor and the height over it"),
                (int(3640 * 0.41), int(180 * 0.41),
                 "41% over: inside `ceiling_slack`'s 50% band")):
            with self.subTest(over=(over_w, over_h)):
                out = self.snapshot(TAB_WIDE, 3640 + over_w, 180 + over_h,
                                    aid="over%dx%d" % (over_w, over_h))
                self.assertIn("TIER=1", out, "%s: %s" % (why, out))
                self.assertIn("VALID=true", out, "%s: %s" % (why, out))

    def test_the_slack_band_is_exactly_as_wide_as_it_was_calibrated(self):
        """Curator batch 23 item 15 (task 49 §9), 2026-08-15.

        The trade `floor_slack` makes, written as an assertion instead of
        as a docstring. Tier 1 takes a raster up to `max(96, 5%)` short
        of the drawing, so on this 3640px fixture the last 182px of the
        right edge can be gone with `VALID=true` over it — a band eleven
        times the 16px the same drawing's own rounding could explain.
        This is NOT a demand that the band shrink: `floor_slack`'s
        docstring records the two real corpus exports (−33 and −70px)
        that a strict floor refused, and spike-selfreport.md §4 re-checked
        the constant against the self-report protocol and kept it — the
        proposed A1/B1/B2 split keeps 96px as the text-inclusive backstop
        and buys its precision by ADDING gates, not by narrowing this one.
        So the number here is stable, and pinning it is safe.

        What the pin is for is the OTHER direction. The band is the tier's
        blind spot, and a blind spot nobody measures grows: any later
        change that widened `floor_slack` would leave every test in this
        class green, because they all sit far outside it. Both poles are
        asserted one pixel apart so the edge itself is the subject.
        """
        for short, tier_1_takes_it in ((100, True), (182, True),
                                       (183, False), (200, False)):
            with self.subTest(px_short=short):
                ok, why = canvas.validate_png(
                    self.fat_png(3640 - short, 180), want_w=3640,
                    want_h=180, min_bpp=0.05, want_is_floor=True)
                self.assertIs(
                    ok, tier_1_takes_it,
                    "a raster %dpx short of a 3640px drawing was %s (%s): "
                    "the slack band's edge has moved off the 182px "
                    "`floor_slack` was calibrated to"
                    % (short, "taken" if ok else "refused", why))

    def test_the_export_path_supplies_nothing_that_would_scale_it(self):
        """Curator batch 23 item 16 (spike-selfreport §7.1), 2026-08-15.

        The executable half of a correction three shipped surfaces have
        not had yet: the tab export is NOT `devicePixelRatio`-scaled. The
        spike drove one artifact through the real protocol at
        `deviceScaleFactor` 1 and 2 and got byte-identical files, then
        read the reason out of the vendored bundle — `exportToBlob` hands
        `pp` its OWN canvas-maker, which overrides the `exportScale`
        multiply, and with neither `maxWidthOrHeight` nor `getDimensions`
        supplied that callback reduces to "same size, scale 1".

        Those three absences are the whole mechanism, so they are what
        this asserts. It goes red the day someone adds `getDimensions` to
        either call site — which is precisely the day the export becomes
        scalable and `ceiling_slack` becomes a false refusal waiting to
        happen, so this is the pin that must be answered BEFORE that
        change lands, not after. The self-report task re-measured the
        mechanism at its own head and then recovered the ceiling on it;
        `..._refuses_an_export_twice_the_drawing` is what now depends on
        these three absences staying absent.
        """
        src = (Path(__file__).resolve().parents[1] / "frontends" /
               "wysiwyg-grilling" / "src" / "App.tsx").read_text(
                   encoding="utf-8")
        calls = src.count("await exportToBlob({")
        self.assertEqual(calls, 2,
                         "App.tsx has %d exportToBlob call sites, not the "
                         "2 this reasoning enumerated (the servicing path "
                         "and the Export PNG button)" % calls)
        for absent in ("exportScale", "maxWidthOrHeight", "getDimensions"):
            with self.subTest(field=absent):
                self.assertNotIn(
                    absent, src,
                    "App.tsx now supplies `%s`: the tab export may be "
                    "scaled after all, so re-measure it at "
                    "deviceScaleFactor 2 before trusting either the "
                    "retina test above or the ceiling red below" % absent)

    def test_the_two_forties_agree(self):
        """Curator batch 23 item 13 (task 49 §7 item 2), 2026-08-15.

        `ink_extent`'s `pad` default is the floor tier 1 measures the
        export against; `exportPadding` is the margin the tab actually
        frames with. They are the same 40 on purpose and by convention
        only — two literals in two languages with no shared code, and
        `ink_extent`'s docstring says so and then relies on the reader.
        Move either one and the floor stops describing the picture it is
        judging: raise the client's and every honest export reads
        oversize, lower it and every one reads short by twice the drift.

        Both call sites are checked rather than the first found, because
        the servicing path and the user's Export PNG button are separate
        literals and only the first is on the tier-1 path — a drift in
        the second is a user exporting a differently-framed picture than
        the agent sees, which is a quieter defect and not a smaller one.
        """
        import inspect
        pad = inspect.signature(canvas.ink_extent).parameters["pad"].default
        src = (Path(__file__).resolve().parents[1] / "frontends" /
               "wysiwyg-grilling" / "src" / "App.tsx").read_text(
                   encoding="utf-8")
        client = [int(m) for m in re.findall(r"exportPadding:\s*(\d+)", src)]
        self.assertEqual(len(client), 2,
                         "App.tsx carries %d exportPadding literals, not "
                         "the 2 this pin enumerated: %s"
                         % (len(client), client))
        self.assertEqual(
            client, [pad, pad],
            "App.tsx frames at %s while ink_extent pads at %s: the tier-1 "
            "floor is now measuring a box the client never draws. These "
            "are unconnected literals held together by convention — "
            "change both or neither" % (client, pad))

    def test_tier_1_refuses_an_export_twice_the_drawing(self):
        """FLIPPED (self-report task): the ceiling is back.

        Curator batch 23 item 16 filed this RED, and the flip needed no
        change to the assertion below — `ceiling_slack` landing is the
        whole difference. The cost of the fiction, and the discrimination
        it talked the tier out of: task 49 dropped tier 1's oversize band
        because `exportToBlob` was believed to scale by
        `devicePixelRatio`, so an export at 2x or 3x the floor had to be
        taken. That was measured false, twice — once by the spike and
        once again at this head before the constant landed — so a raster
        twice the drawing is not a HiDPI user, it is a wrong file, and
        for a while nothing in this tier could say so.

        This was filed as one half of a deliberate contradiction: it and
        `test_tier_1_keeps_a_retina_tab_export` asserted opposite things
        about one input, and the batch declined to pick, leaving the
        choice to whoever measured. The choice went to the ceiling. The
        retina test is gone and
        `..._keeps_an_export_a_little_over_the_floor` stands where it
        stood, holding the same guard — the tier must not refuse an
        honest export that came out bigger than the floor — at the sizes
        that actually occur instead of at an imagined 2x. Read the two
        together; the pair is the whole trade.
        """
        out = self.snapshot(TAB_WIDE, 3640 * 2, 180 * 2, aid="oversize")
        self.assertIn("far from", out, out)

    def test_the_ceiling_sits_where_it_was_sized_to_sit(self):
        """Both poles of `ceiling_slack`, one pixel apart.

        The batch-23 item-15 discipline applied to the new constant: a
        band nobody measures drifts, and every other test of the ceiling
        sits far outside it — 2x on one side, +20px on the other — so
        widening or narrowing it would leave them all green. The edge
        itself is the subject here.

        On a 3640px floor the allowance is 50%, so 5460 is the last
        raster taken and 5461 the first refused. The height axis is where
        the `max(64, ...)` branch shows: 180px of drawing allows 90 and
        not 64, because 50% of 180 is 90.
        """
        for want, over, taken in ((3640, 1820, True), (3640, 1821, False),
                                  (180, 90, True), (180, 91, False),
                                  (100, 64, True), (100, 65, False)):
            with self.subTest(want=want, over=over):
                ok, why = canvas.validate_png(
                    self.fat_png(want + over, want + over), want_w=want,
                    want_h=want, min_bpp=0.05, want_is_floor=True)
                self.assertIs(
                    ok, taken,
                    "a raster %dpx over a %dpx drawing was %s (%s): the "
                    "ceiling has moved off `ceiling_slack`"
                    % (over, want, "taken" if ok else "refused", why))

    def test_tier_1_refuses_an_export_that_lost_bytes_in_transit(self):
        """A1, and the reason the protocol exists at all.

        The case every OTHER check on this tier is blind to, reproduced
        end to end: an honest export of the right drawing at the right
        dimensions, with a fifth of the file gone. The IHDR lives in the
        first 33 bytes and survives any truncation that leaves a file at
        all, so the header still reads 3640x180, the geometry checks all
        pass, and the density stays plausible — the spike measured a real
        20%-cropped corpus artifact at 0.085 bytes/px against a 0.05
        floor. Nothing here could see it before the client started
        saying how many bytes it sent.

        This is the check the task brief assigned to comparing IHDR
        against the self-reported dimensions, which cannot do it: the
        header is intact, so that comparison passes. `bytes` is the field
        that does this job, and if only one field ever ships it is that
        one.
        """
        whole = self.fat_png(3640, 180, bpp=0.106)
        out = self.snapshot(TAB_WIDE, 3640, 180, report=True, bpp=0.106,
                            truncate=len(whole) // 5)
        self.assertIn("lost in transit", out, out)
        self.assertNotIn("TIER=1", out, out)

    def test_the_truncation_that_a1_catches_is_invisible_to_every_other_check(
            self):
        """The distinctness proof for the pin above — the same bytes,
        with the report withheld.

        Without this the A1 pin passes the day the file starts failing
        for some unrelated reason (a density floor that caught up, a
        geometry check that got stricter), and the tier would look
        guarded while the protocol did nothing. Same drawing, same
        truncation, no self-report: `VALID=true` at tier 1, which is
        exactly the defect, and is what shipped until now.

        The density is a real export's (0.106, the spike's measurement of
        `tearsheet-domain`) and not this class's usual 0.06, which is a
        hair over the 0.05 floor. That matters: at 0.06 a fifth of the
        file is also a fifth off the density and the raster is refused
        for being thin, so the pin would report a guarded tier for a
        reason that has nothing to do with truncation. At a real density
        the cut lands at 0.085 and the floor never notices — which is
        precisely what the spike measured on the real artifact, and why
        `min_bpp` could not be the answer here.
        """
        whole = self.fat_png(3640, 180, bpp=0.106)
        out = self.snapshot(TAB_WIDE, 3640, 180, bpp=0.106,
                            truncate=len(whole) // 5)
        self.assertIn("TIER=1", out, out)
        self.assertIn("VALID=true", out, out)
        self.assertIn("0.085 bytes/px", out, out)

    def test_tier_1_refuses_a_crop_inside_the_slack_band(self):
        """A2, and it is a DIFFERENT mutant from A1 by construction.

        The spike's pole 2b on this fixture: 14px of real drawing taken
        off the right edge. That is deep inside `floor_slack`'s 182px
        band on a 3640px drawing, so B2 takes it, and the file is
        internally consistent — a perfectly healthy PNG of a picture with
        a strip missing. What gives it away is that the client said it
        made a 3640px canvas and the server is holding a 3626px one.

        A1 and A2 are distinct and the distinctness is the point: the
        pin above dies to `bytes` alone, and this one dies to EITHER
        field, because a re-encode at different dimensions changes the
        byte count too. Asserting the dimension message specifically is
        what proves A2 is carrying its own weight rather than riding A1.
        """
        out = self.snapshot(TAB_WIDE, 3626, 180,
                            report={"bytes": len(self.fat_png(3626, 180)),
                                    "png_w": 3640, "png_h": 180})
        self.assertIn("the tab framed 3640x180", out, out)
        self.assertNotIn("TIER=1", out, out)

    def test_a_half_arrived_dimension_report_costs_nothing(self):
        """A2 needs both axes, and a partial report is not a refusal.

        Self-review catch, not a design intent discovered late: the first
        cut gated A2 on `png_w` alone and compared the pair, so a report
        carrying a width and no height read as a mismatch against `None`
        and refused a perfectly good export. The fields are filtered
        independently on the way in — a client can lose one to a garbled
        type and keep the other — so that state is reachable, and it
        turned an optional diagnostic into a lost screenshot.

        Both halves are checked, because a fix that skipped A2 whenever
        either field was odd could also have been written to skip it
        whenever either was PRESENT, and the second is a disarmed check
        wearing the first's clothes: the full report on the same bytes
        must still refuse.
        """
        raw = self.fat_png(3640, 180)
        for n, (said, taken) in enumerate((({"png_w": 9999}, True),
                                           ({"png_h": 9999}, True),
                                           ({"png_w": 9999, "png_h": 9999},
                                            False))):
            with self.subTest(report=said):
                out = self.snapshot(TAB_WIDE, 3640, 180, aid="half%d" % n,
                                    report=dict(said, bytes=len(raw)))
                self.assertIs("TIER=1" in out, taken, out)

    def test_tier_1_takes_an_export_from_a_client_that_says_nothing(self):
        """The compatibility pole — the one most likely to be lost.

        Every `project_knowledge/` written before the rebuilt bundle
        holds a client that posts `{id, data_url}` and no more, and every
        user who has not reloaded the tab IS one. Refusing them would
        brick working projects for a check that is insurance, not
        correctness, so the fields are optional on the way in and their
        absence downgrades the verdict to what it was rather than to a
        failure. The NOTE is asserted too: silently checking less is how
        a tier ends up trusted for more than it does.

        The reverse direction needs no test — a new client posting extra
        keys to an old server already works, because unknown keys in the
        POST body are ignored.
        """
        out = self.snapshot(TAB_WIDE, 3640, 180)
        self.assertIn("TIER=1", out, out)
        self.assertIn("VALID=true", out, out)
        self.assertIn("carried no self-report (old client bundle)", out, out)

    def test_a_client_that_says_nothing_still_gets_the_strict_floor(self):
        """...and the compatibility pole is not a bypass.

        The tolerance above is narrow on purpose: only A1 and A2 need the
        protocol. B1 is pure server-side geometry — `exact_ink_extent`
        against the raster — so an old bundle is checked to 8px on every
        axis text does not own, exactly like a new one. Without this pin
        "tolerate old clients" could quietly become "skip the geometry
        for old clients", which is the shape most compatibility hatches
        rot into.

        The same raster as the honest case above, 200px short: inside
        nothing, but this asserts the message names the text-free extent,
        which only B1 can produce.
        """
        out = self.snapshot(TAB_WIDE, 3440, 180)
        self.assertIn("text-free extent alone is 3640", out, out)

    def test_the_strict_floor_refuses_what_the_slack_band_would_take(self):
        """B1 against B2 on one raster — the band the split closes.

        `TAB_WIDE` is a drawing with no text at its edges, so its
        text-free extent IS its extent and the strict floor governs both
        axes. 100px short of 3640 sits comfortably inside `floor_slack`'s
        182px band and was `VALID=true` at tier 1 until now; against a
        floor the server reproduces to the pixel it is 92px past an 8px
        allowance.

        Both poles, so the pin measures the edge rather than the
        direction: 8px short is still taken, because bound arrows can
        move by ~7px on load and `EXACT_SLACK` is that delta plus
        rounding.
        """
        for short, taken in ((8, True), (9, False), (100, False)):
            with self.subTest(px_short=short):
                out = self.snapshot(TAB_WIDE, 3640 - short, 180,
                                    aid="strict%d" % short)
                self.assertIs(
                    "TIER=1" in out, taken,
                    "a raster %dpx short of a 3640px text-free extent was "
                    "%s: %s" % (short, "taken" if taken else "refused", out))

    def test_the_honest_export_passes_every_check_at_once(self):
        """The live pole for the whole protocol.

        Five checks now stand between a tab export and `VALID=true`, and
        each of the pins above proves one of them can REFUSE. None of
        them proves they can all pass together, which is the failure
        mode that matters most: a tier that refuses everything looks
        identical to a tier that works, from any single red.

        So: an honest export, the report an honest client would send
        about those exact bytes, and the dimensions the drawing actually
        has. A1, A2, B1, B2 and the ceiling all satisfied at once.
        """
        out = self.snapshot(TAB_WIDE, 3640, 180, report=True)
        self.assertIn("TIER=1", out, out)
        self.assertIn("VALID=true", out, out)
        # FIRING POLE for the refusal NOTE: `..._refuses_a_tab_export_
        # short_of_the_drawing` asserts this same prefix present on the
        # same entry point (rule-8 census, 2026-08-16).
        self.assertNotIn("NOTE=tab export attempt", out, out)
        self.assertNotIn("carried no self-report", out, out)


class TestScreenshotSelfReport(Base):
    """The wire half of the self-report: what the tab is allowed to say.

    `TestSnapshotTierOne` proves what the CHECKS do with a report; this
    proves the report survives the trip. The two halves are separable and
    were separated on purpose — the server-side geometry check needs no
    protocol at all and landed able to run without one — so the seam
    between them gets its own tests rather than being covered only end to
    end.

    These drive `ServerApp.handle_post` directly. That is the real
    handler with the real body, one layer under the socket: the transport
    is not the subject, and a live server would only add flake.
    """

    def png(self, w=10, h=10):
        """A minimal valid PNG, for a handler that only stores bytes.

        Args:
            w: IHDR width.
            h: IHDR height.

        Returns:
            `(raw_bytes, data_url)`.
        """
        import struct
        raw = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" +
               struct.pack(">II5B", w, h, 8, 2, 0, 0, 0) + b"\0\0\0\0")
        return raw, ("data:image/png;base64,"
                     + base64.b64encode(raw).decode("ascii"))

    def complete(self, extra=None):
        """Run one request/complete round trip through the real handler.

        Each call builds a fresh `ServerApp`, so the request id restarts
        at 1 and a previous call's sidecar would still be sitting at
        `shot-1.json`. Clearing it is what makes "no sidecar" mean this
        call wrote none, rather than the last one having written one —
        without it a subtest that should prove a field was DROPPED reads
        the field its predecessor stored and passes on the wrong
        evidence. Found by mutation.

        Args:
            extra: Fields the client sends beyond `{id, data_url}`.

        Returns:
            `(response, sidecar_or_None, raw_png_bytes)`.
        """
        app = canvas.ServerApp(self.project)
        sid = app.handle_post("/api/screenshot/request",
                              {"artifact": "a"})["id"]
        stale = self.project.shots_dir / ("shot-%d.json" % sid)
        if stale.exists():
            stale.unlink()
        raw, url = self.png()
        body = {"id": sid, "data_url": url}
        body.update(extra or {})
        resp = app.handle_post("/api/screenshot/complete", body)
        side = self.project.shots_dir / ("shot-%d.json" % sid)
        return resp, (canvas.read_json(side) if side.exists() else None), raw

    def test_the_tab_s_account_of_its_export_reaches_the_snapshot(self):
        """The happy path, end to end through the handler.

        The sidecar is the route: `/api/state` lists only WAITING
        requests, so a completed one has nowhere to ride home, and
        growing that payload would tax a hot poll loop for data one
        caller wants once. `shot-<id>.json` beside `shot-<id>.png`
        collides with nothing, because every `shots_dir` consumer globs
        an explicit `shot-N.png`.
        """
        raw, _ = self.png()
        resp, side, _ = self.complete(
            {"bytes": len(raw), "png_w": 10, "png_h": 10, "artifact": "a"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(side, {"bytes": len(raw), "png_w": 10,
                                "png_h": 10, "artifact": "a"})

    def test_an_old_bundle_completes_a_screenshot_and_leaves_no_sidecar(self):
        """The compatibility pole at the wire, not just at the check.

        A tab built before this protocol posts `{id, data_url}`. It must
        get its `{"ok": true}` — an export that succeeds in the browser
        and then 400s on the way home is a broken app, for a check that
        is insurance. No sidecar is written, which is what makes
        `cmd_snapshot` fall back to the pre-protocol verdict rather than
        to a failure.
        """
        resp, side, _ = self.complete()
        self.assertTrue(resp.get("ok"), resp)
        self.assertIsNone(side)

    def test_a_garbled_self_report_is_dropped_and_never_refused(self):
        """Junk fields cost the client its checks, never its export.

        Every field is independently optional, so a client that manages
        `bytes` but garbles `png_w` keeps A1 and loses A2 — the failure
        is graceful per FIELD and not per report. The alternative,
        refusing the POST, turns a mistake in an optional diagnostic into
        a lost screenshot.

        `True` is checked explicitly because `bool` is a subclass of
        `int` in Python: without the guard, `{"bytes": true}` would be
        stored as a byte count of 1 and refuse every real file as
        truncated. That is the one type confusion this protocol can make
        that produces a plausible-looking number rather than a crash.
        """
        raw, _ = self.png()
        for junk in ({"bytes": "205135"}, {"bytes": True}, {"bytes": 0},
                     {"bytes": -1}, {"bytes": 1.5}, {"bytes": None},
                     {"png_w": [10]}, {"artifact": 7}):
            with self.subTest(junk=junk):
                field = next(iter(junk))
                resp, side, _ = self.complete(
                    dict(junk, **({} if field == "bytes"
                                  else {"bytes": len(raw)})))
                self.assertTrue(resp.get("ok"), resp)
                self.assertNotIn(field, side or {},
                                 "%r was stored as a self-report field"
                                 % (junk,))

    def test_a_report_of_nothing_usable_is_no_report(self):
        """...and the all-junk case is the old-client case.

        A client that sends only unusable fields must be indistinguishable
        from one that sends none, or `cmd_snapshot` would find an empty
        sidecar, decide a report exists, and skip the NOTE that says the
        integrity checks did not run. Silently checking less is the
        failure this whole tier keeps being fixed for.
        """
        _, side, _ = self.complete({"bytes": "lots", "png_w": None})
        self.assertIsNone(side)

    def test_a_report_less_post_clears_the_previous_shot_s_sidecar(self):
        """Review fix round 1, finding 1 — the leak that shipped.

        The same defect I found by mutation in this class's own helper,
        living in the product: `complete()` was clearing a stale sidecar
        while the handler was not. `shots_dir` is keyed on the project
        root and outlives the server; `shot_seq` is per-`ServerApp` and
        restarts at 1. So id 1 is reused on every restart, and writing
        the sidecar only when a report existed left the PREVIOUS export's
        numbers sitting there for an old client that sent none.

        End to end that refused an honest export: a 9033-byte file
        checked against a remembered 5033 fails A1, twice, and tier 1
        falls through to the headless render — with a message blaming
        transit for bytes nothing lost. Old clients were the population
        it hit, which is the one the whole protocol promised not to
        break.

        Fixing it in the test and not in the handler is the specific
        trap worth naming: a harness that cleans up hides exactly the
        state the product fails on, so the cleaner the fixture, the
        better the bug is hidden. This pin therefore drives the REAL
        handler twice over one id, which is the only place the leak was
        ever visible.
        """
        raw, _ = self.png()
        _, first, _ = self.complete({"bytes": len(raw), "png_w": 10,
                                     "png_h": 10})
        self.assertEqual(first["bytes"], len(raw))
        app = canvas.ServerApp(self.project)      # a restart: sid back to 1
        sid = app.handle_post("/api/screenshot/request",
                              {"artifact": "a"})["id"]
        self.assertEqual(sid, 1, "the id no longer collides, so this pin "
                                 "is testing nothing — rebuild it")
        _, url = self.png(20, 20)
        app.handle_post("/api/screenshot/complete",
                        {"id": sid, "data_url": url})   # an OLD client
        self.assertFalse(
            (self.project.shots_dir / ("shot-%d.json" % sid)).exists(),
            "a client that sent no self-report inherited the previous "
            "export's — tier 1 will check this file against another "
            "file's byte count")

    def test_the_report_describes_the_file_and_not_the_request(self):
        """The field that does the work is the client's own count.

        `screenshot_self_report` does not compute, infer or sanity-check
        `bytes` against the payload — it records what the tab said. That
        is the entire mechanism: if the server recomputed it from the
        bytes it received, A1 would compare a number with itself and a
        truncated payload would agree with its own length perfectly.

        So the handler must store a byte count that DISAGREES with the
        file on disk, without complaint, and leave the judgement to
        `validate_png`. This pin exists because "validate the input"
        is the obvious instinct here and it would silently disarm the
        check.
        """
        raw, _ = self.png()
        _, side, stored = self.complete({"bytes": len(raw) + 4096})
        self.assertEqual(side["bytes"], len(raw) + 4096)
        self.assertNotEqual(side["bytes"], len(stored))


def seed_sequence_batch(base_revn=0):
    ops = []
    for i, (aid, label, kind) in enumerate(
            (("client", "Client", "actor"), ("api", "API", "system"),
             ("vendor", "Vendor", "system"))):
        ops.append({"op": "add", "element":
                    {"type": "rectangle", "id": aid, "label": label,
                     "x": 100 + i * 250, "y": 50, "width": 160,
                     "height": 60, "kind": kind, "role": "node"}})
    for j, (mid, s, d, label) in enumerate(
            (("m1", "client", "api", "request quote"),
             ("m2", "api", "vendor", "fetch prices"),
             ("m3", "vendor", "api", "prices"))):
        ops.append({"op": "add", "element":
                    {"type": "arrow", "id": mid, "label": label,
                     "x": 100, "y": 180 + j * 80, "width": 250,
                     "height": 0}, "from": s, "to": d})
    return {"base_revn": base_revn, "artifact": "seq",
            "create": {"id": "seq", "name": "Quote Flow",
                       "type": "sequence"}, "ops": ops}


class TestSequenceFacts(Base):
    def _facts(self, rec, aid="seq"):
        return [(f["fact"], f) for f in rec["artifacts"][aid]["facts"]]

    def test_seed_produces_actor_and_message_facts(self):
        rec, _ = self.store.apply_batch(seed_sequence_batch())
        names = [n for n, _ in self._facts(rec)]
        self.assertIn("actor_added", names)
        self.assertIn("message_added", names)

    def test_message_reordered_and_actor_reassigned(self):
        self.store.apply_batch(seed_sequence_batch())
        # user drags m3 above m2 (y swap) and repoints m1 at vendor
        els = self.scene("seq")
        for e in els:
            if e["id"] == "m3":
                e["y"] = 250
            if e["id"] == "m2":
                e["y"] = 340
            if e["id"] == "m1":
                e["endBinding"] = {"elementId": "vendor", "focus": 0,
                                   "gap": 6}
        rec = self.store.commit(author="user", new_scenes={"seq": els},
                                base_revn=1)
        names = [n for n, _ in self._facts(rec)]
        self.assertIn("message_reordered", names)
        self.assertIn("actor_reassigned", names)
        ra = next(f for n, f in self._facts(rec)
                  if n == "actor_reassigned")
        self.assertEqual(ra["to_actor"], "Vendor")

    def test_party_crystallization(self):
        self.store.apply_batch(seed_sequence_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "seq",
            "ops": [{"op": "mod", "id": "vendor",
                     "attrs": {"customData": {"kind": "context"}}}]})
        rec = self.store.records[2]
        f = next(f for f in rec["artifacts"]["seq"]["facts"]
                 if f["fact"] == "party_kind_changed")
        self.assertTrue(f["crystallized"])

    def test_sequence_lint_time_reversal_and_budget(self):
        self.store.apply_batch(seed_sequence_batch())
        els = self.scene("seq")
        for e in els:
            if e["id"] == "m2":
                e["points"] = [[0, 0], [250, -60]]  # upward = back in time
        self.store.commit(author="user", new_scenes={"seq": els},
                          base_revn=1)
        lint = canvas.lint_layout(self.store.scenes["seq"])
        self.assertTrue(any("travels UP" in e for e in lint["errors"]))


def _lifeline_sequence(bar_height=None, reply_dy=0):
    """A sequence drawn the way the reference says to draw one.

    The seeder above binds messages ACTOR to ACTOR, which is why r5-16
    never showed up in it: bound headers are not orphans. A real sequence
    puts the bindings on the LIFELINES (references/sequence.md), and then
    every actor header is unbound by construction — the shape the
    unconnected note fired on for four versions.

    Geometry is the seeder's own: headers at `100 + i*250`, lifelines at
    `headerX + 76`, messages at `180 + j*80`.

    Args:
        bar_height: If set, an activation bar on the second lifeline
            starting at the request. 80 closes it on the reply.
        reply_dy: Vertical delta on the reply message; negative draws it
            back up the page.

    Returns:
        The scene's elements.
    """
    els = []
    for i, nm in enumerate(("Client", "Vendor")):
        hx = 100 + i * 250
        els += [{"id": "act%d" % i, "type": "rectangle", "x": hx, "y": 48,
                 "width": 160, "height": 60,
                 "customData": {"role": "node", "kind": "actor"}},
                {"id": "act%d-l" % i, "type": "text", "x": hx + 10,
                 "y": 68, "width": 100, "height": 20, "text": nm,
                 "containerId": "act%d" % i},
                {"id": "lf%d" % i, "type": "line", "x": hx + 76, "y": 108,
                 "width": 0, "height": 400, "points": [[0, 0], [0, 400]],
                 "customData": {"kind": "lifeline"}}]
    els += [{"id": "m0", "type": "arrow", "x": 176, "y": 180, "width": 252,
             "height": 0, "points": [[0, 0], [252, 0]],
             "startBinding": {"elementId": "lf0", "focus": 0, "gap": 1},
             "endBinding": {"elementId": "lf1", "focus": 0, "gap": 1},
             "customData": {"role": "edge"}},
            {"id": "m1", "type": "arrow", "x": 428, "y": 260, "width": 252,
             "height": abs(reply_dy),
             "points": [[0, 0], [-252, reply_dy]],
             "startBinding": {"elementId": "lf1", "focus": 0, "gap": 1},
             "endBinding": {"elementId": "lf0", "focus": 0, "gap": 1},
             "customData": {"role": "edge"}}]
    if bar_height is not None:
        els.append({"id": "bar1", "type": "rectangle", "x": 424, "y": 180,
                    "width": 8, "height": bar_height,
                    "customData": {"kind": "activation"}})
    return els


def _two_lane_flow(step_y, kind="lane"):
    """Two stacked lanes meeting at y=200, and one 60px-tall step.

    Args:
        step_y: The step's top edge. 180 straddles the boundary,
            20px still in the upper lane and 40px into the lower —
            asymmetric so the reported side is falsifiable.
        kind: The frames' `kind`; anything but `"lane"` makes them
            ordinary frames the lane check must ignore.

    Returns:
        The scene's elements.
    """
    return [{"id": "ln-a", "type": "frame", "x": 0, "y": 0, "width": 800,
             "height": 200, "name": "Vendor", "customData": {"kind": kind}},
            {"id": "ln-b", "type": "frame", "x": 0, "y": 200, "width": 800,
             "height": 200, "name": "Ops", "customData": {"kind": kind}},
            {"id": "s1", "type": "rectangle", "x": 100, "y": step_y,
             "width": 140, "height": 60, "frameId": "ln-a",
             "customData": {"role": "node", "kind": "transform"}}]


class TestSequenceAndLaneLints(unittest.TestCase):
    """r5-16 and the two checks `lint_layout` promised and never grew.

    Every test here is one half of a pair. A lint is a channel, and a
    channel that is wrong in either direction gets discounted wholesale —
    which is what r5-16 cost: the unconnected note was 100% of what lint
    had to say about a CORRECT sequence, and the only repair it left a
    headless agent would have made the drawing wrong. So the silent poles
    are not politeness, they are half the claim.
    """

    def _fired(self, els, needle, artifact_type="sequence"):
        """Lint a scene and return the findings matching a phrase.

        Args:
            els: The scene's elements.
            needle: Substring identifying the check under test.
            artifact_type: Type passed to `lint_layout`.

        Returns:
            Matching lines across all three channels.
        """
        li = canvas.lint_layout(els, artifact_type=artifact_type)
        return [m for t in ("errors", "warnings", "notes")
                for m in li[t] if needle in m]

    def test_a_correct_sequence_reports_no_unconnected_actor(self):
        """r5-16: four versions of a note no correct sequence could clear."""
        self.assertEqual(self._fired(_lifeline_sequence(), "unconnected"), [])

    def test_a_flow_with_a_stranded_node_still_reports_it(self):
        """The exemption's other pole — it is by KIND, not a blanket mute.

        Without this, deleting the unconnected check outright would pass
        the test above. The scene is the same shape one type over: nodes
        carrying no binding and no frame, where the note is exactly right.
        """
        els = [{"id": "n%d" % i, "type": "rectangle", "x": i * 200, "y": 0,
                "width": 140, "height": 60,
                "customData": {"role": "node", "kind": "transform"}}
               for i in range(3)]
        self.assertEqual(len(self._fired(els, "unconnected", "flow")), 1)

    def test_a_lifeline_crossing_its_own_activation_is_not_a_route(self):
        """r5-16's second half: the bar sits ON the lifeline by design.

        A lifeline is a timeline, not a connector, and its activation
        bars ride on top of it — so `passes through` told every correct
        sequence carrying a bar to route around furniture that belongs
        exactly where it is.
        """
        self.assertEqual(
            self._fired(_lifeline_sequence(bar_height=80), "passes through"),
            [])

    def test_an_arrow_through_a_foreign_node_is_still_reported(self):
        """That narrowing's pole: the check still owns real crossings."""
        els = [{"id": "a", "type": "rectangle", "x": 0, "y": 0, "width": 80,
                "height": 60, "customData": {"role": "node"}},
               {"id": "mid", "type": "rectangle", "x": 200, "y": 0,
                "width": 80, "height": 60, "customData": {"role": "node"}},
               {"id": "z", "type": "rectangle", "x": 400, "y": 0,
                "width": 80, "height": 60, "customData": {"role": "node"}},
               {"id": "t1", "type": "arrow", "x": 80, "y": 30, "width": 320,
                "height": 0, "points": [[0, 0], [320, 0]],
                "startBinding": {"elementId": "a", "focus": 0, "gap": 1},
                "endBinding": {"elementId": "z", "focus": 0, "gap": 1},
                "customData": {"role": "edge"}}]
        self.assertEqual(len(self._fired(els, "passes through", "flow")), 1)

    def test_a_reply_drawn_up_the_page_is_a_time_reversal(self):
        """Time flows down; a back-arrow says the protocol went backwards."""
        self.assertEqual(
            len(self._fired(_lifeline_sequence(reply_dy=-60), "travels UP")),
            1)

    def test_a_reply_drawn_level_is_not_a_time_reversal(self):
        """The same reply, drawn the way a reply is drawn: silent."""
        self.assertEqual(self._fired(_lifeline_sequence(), "travels UP"), [])

    def test_an_activation_that_no_message_closes_is_reported(self):
        """The bar outlives the conversation, and says so by how much.

        The magnitude is asserted, not just the firing: it is measured to
        the last message on the lifeline (260) rather than to the bar's
        own head (180), so a check that reported its height would say
        240px — a different number and a different repair.
        """
        said = self._fired(_lifeline_sequence(bar_height=240), "never closes")
        self.assertEqual(len(said), 1)
        self.assertIn("160px past", said[0])

    def test_an_activation_closed_by_its_reply_is_silent(self):
        """The bar ending on the message that answers it: nothing to say."""
        self.assertEqual(
            self._fired(_lifeline_sequence(bar_height=80), "never closes"),
            [])

    def test_an_activation_no_message_opens_is_silent(self):
        """A bar touching neither edge is unpaired, not unclosed.

        The third pole, and the one a naive "does anything end here?"
        implementation fails: this bar has no opening message either, so
        it is furniture in the wrong place — a different finding, owed by
        nobody yet, and not this check's to invent.
        """
        els = _lifeline_sequence(bar_height=80)
        for e in els:
            if e["id"] == "bar1":
                e["y"] = 500
        self.assertEqual(self._fired(els, "never closes"), [])

    def test_a_step_drawn_across_two_lanes_is_reported(self):
        """Two owners for one step is the drawing refusing to answer."""
        said = self._fired(_two_lane_flow(step_y=180), "drawn across lanes",
                           "flow")
        self.assertEqual(len(said), 1)
        self.assertIn("reaching 20px", said[0])
        self.assertIn("filed under Vendor", said[0],
                      "the declared owner is the repair instruction")

    def test_a_step_inside_one_lane_is_silent(self):
        """The same step, same `frameId`, 20px clear of the boundary."""
        self.assertEqual(
            self._fired(_two_lane_flow(step_y=120), "drawn across lanes",
                        "flow"), [])

    def test_ordinary_frames_are_not_lanes(self):
        """The check reads `kind: lane`, not every frame on the canvas.

        A wireframe's screen frames are stacked the same way and a step
        spanning two of them means nothing about ownership — without this
        pole the check would fire across an entire artifact type.
        """
        self.assertEqual(
            self._fired(_two_lane_flow(step_y=180, kind="screen"),
                        "drawn across lanes", "wireframe"), [])


class TestTypeExtensionFacts(Base):
    def test_cardinality_changed(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dom", "create":
                {"id": "dom", "name": "D", "type": "domain"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "team",
                                          "label": "Team", "x": 0, "y": 0,
                                          "width": 180, "height": 64,
                                          "kind": "entity", "role": "node"}},
                {"op": "add", "element": {"type": "rectangle", "id": "sheet",
                                          "label": "Tearsheet", "x": 400,
                                          "y": 0, "width": 180, "height": 64,
                                          "kind": "entity", "role": "node"}},
                {"op": "add", "element": {"type": "arrow", "id": "r1",
                                          "label": "receives 1"},
                 "from": "team", "to": "sheet"},
            ]})
        self.store.apply_batch({
            "base_revn": 1, "artifact": "dom",
            "ops": [{"op": "mod", "id": "r1",
                     "attrs": {"label": "receives per-PM"}}]})
        rec = self.store.records[2]
        f = next(f for f in rec["artifacts"]["dom"]["facts"]
                 if f["fact"] == "cardinality_changed")
        self.assertEqual(f["to"], ["per-PM"])

    def test_lane_ownership_and_handoff(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "lf", "create":
                {"id": "lf", "name": "L", "type": "flow"},
            "ops": [
                {"op": "add", "element": {"type": "frame", "id": "lane-ops",
                                          "name": "Ops", "x": 0, "y": 0,
                                          "width": 600, "height": 200,
                                          "kind": "lane"}},
                {"op": "add", "element": {"type": "frame", "id": "lane-risk",
                                          "name": "Risk", "x": 0, "y": 220,
                                          "width": 600, "height": 200,
                                          "kind": "lane"}},
                {"op": "add", "element": {"type": "rectangle", "id": "s1",
                                          "label": "Build", "x": 40, "y": 60,
                                          "width": 160, "height": 64,
                                          "role": "node",
                                          "frameId": "lane-ops"}},
                {"op": "add", "element": {"type": "rectangle", "id": "s2",
                                          "label": "Check", "x": 40, "y": 280,
                                          "width": 160, "height": 64,
                                          "role": "node",
                                          "frameId": "lane-risk"}},
                {"op": "add", "element": {"type": "arrow", "id": "t"},
                 "from": "s1", "to": "s2"},
            ]})
        rec = self.store.records[1]
        names = [f["fact"] for f in rec["artifacts"]["lf"]["facts"]]
        self.assertIn("lane_added", names)
        self.assertIn("handoff_added", names)
        # move s1 into the Risk lane: ownership changes
        self.store.apply_batch({
            "base_revn": 1, "artifact": "lf",
            "ops": [{"op": "mod", "id": "s1",
                     "attrs": {"frameId": "lane-risk", "y": 350}}]})
        rec = self.store.records[2]
        f = next(f for f in rec["artifacts"]["lf"]["facts"]
                 if f["fact"] == "ownership_changed")
        self.assertEqual(f["to_lane"], "Risk")

    def test_fold_crossed(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "wf", "create":
                {"id": "wf", "name": "W", "type": "wireframe"},
            "ops": [
                {"op": "add", "element": {"type": "frame", "id": "scr",
                                          "name": "Sheet", "x": 0, "y": 0,
                                          "width": 360, "height": 480}},
                {"op": "add", "element": {"type": "text", "id": "fold",
                                          "text": "- - fold - -", "x": 20,
                                          "y": 200, "width": 320,
                                          "height": 16, "kind": "fold",
                                          "role": "annotation",
                                          "frameId": "scr"}},
                {"op": "add", "element": {"type": "rectangle", "id": "blk",
                                          "label": "Macro News", "x": 20,
                                          "y": 300, "width": 320,
                                          "height": 60, "kind": "block",
                                          "role": "node",
                                          "frameId": "scr"}},
            ]})
        els = self.scene("wf")
        for e in els:
            if e["id"] == "blk":
                e["y"] = 40  # user promotes the block above the fold
        rec = self.store.commit(author="user", new_scenes={"wf": els},
                                base_revn=1)
        f = next(f for f in rec["artifacts"]["wf"]["facts"]
                 if f["fact"] == "fold_crossed")
        self.assertEqual(f["to"], "above")


class TestSignatureSurvivesPersistence(Base):
    """v0.1 acceptance finding: fan offsets (L*k/(N+1)) and odd-width
    centers produce fractional routed geometry; at-rest normalization
    rounds to 1px, so a signature stamped on the floats died on the first
    round-trip and the server disowned its own arrows (reroute-on-move
    silently off, 'user-shaped' warnings on agent arrows)."""

    def test_fanned_arrows_stay_owned_after_roundtrip(self):
        # three arrows converging on one node → fan pass fires with
        # fractional offsets (60/4=15 is integral; use width 100 → 25;
        # force fractions with width 130 → 32.5)
        self.store.apply_batch({
            "base_revn": 0, "artifact": "fan-flow",
            "create": {"id": "fan-flow", "name": "Fan", "type": "flow",
                       "concept": "fan", "concept_name": "Fan"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "hub",
                                          "label": "Hub", "x": 600, "y": 300,
                                          "width": 130, "height": 65,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "rectangle", "id": "s1",
                                          "label": "A", "x": 100, "y": 100,
                                          "width": 140, "height": 60,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "rectangle", "id": "s2",
                                          "label": "B", "x": 100, "y": 300,
                                          "width": 140, "height": 60,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "rectangle", "id": "s3",
                                          "label": "C", "x": 100, "y": 500,
                                          "width": 140, "height": 60,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "arrow", "id": "a1"},
                 "from": "s1", "to": "hub"},
                {"op": "add", "element": {"type": "arrow", "id": "a2"},
                 "from": "s2", "to": "hub"},
                {"op": "add", "element": {"type": "arrow", "id": "a3"},
                 "from": "s3", "to": "hub"},
            ]})
        # simulate a fresh session: reload everything from disk
        store2 = canvas.Store(self.project)
        arrows = [e for e in store2.scenes["fan-flow"]
                  if e.get("type") == "arrow"]
        self.assertEqual(len(arrows), 3)
        for a in arrows:
            self.assertTrue(canvas.server_owns_geometry(a),
                            "%s disowned after persist round-trip "
                            "(sig=%r, geom=%r,%r,%r)"
                            % (a["id"], a["customData"].get("routed"),
                               a["x"], a["y"], a["points"]))
        # and the reroute contract holds through the reloaded store:
        # moving the hub re-routes all three, no detachment errors
        store2.apply_batch({"base_revn": 1, "artifact": "fan-flow",
                            "ops": [{"op": "mod", "id": "hub",
                                     "attrs": {"x": 900, "y": 320}}]})
        lint = canvas.lint_layout(store2.scenes["fan-flow"])
        self.assertFalse([e for e in lint["errors"]
                          if "claims to bind" in e])


class TestAcceptanceFixes(Base):
    """Remaining v0.1 acceptance findings: pin deletion writes through to
    the registry; elbow crossing lint tests real segments; wireframe
    budgets are per-screen and blocks are never 'unconnected'."""

    def test_pin_deletion_writes_through_to_registry(self):
        # pin element deleted → "dismissed" (not worth explaining);
        # pin's TARGET deleted → "pruned". Either way a pin never stays
        # open with no element behind it (v0.1 acceptance finding —
        # end-state verified correct, kept under test).
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [
                                    {"op": "pin", "target": "cart",
                                     "id": "pin-cart",
                                     "question": "why a cart?"},
                                    {"op": "pin", "target": "confirm",
                                     "id": "pin-confirm",
                                     "question": "confirm needed?"}]})
        pins = {p["id"]: p for p in self.store.registry["pins"]}
        self.assertEqual(pins["pin-cart"]["status"], "open")
        self.store.apply_batch({"base_revn": 2, "artifact": "checkout-flow",
                                "ops": [{"op": "del", "id": "pin-cart"}]})
        self.store.apply_batch({"base_revn": 3, "artifact": "checkout-flow",
                                "ops": [{"op": "del", "id": "confirm"}]})
        pins = {p["id"]: p for p in self.store.registry["pins"]}
        self.assertEqual(pins["pin-cart"]["status"], "dismissed")
        self.assertNotIn(pins["pin-confirm"]["status"],
                         ("open", "answered"))

    def test_elbow_crossing_uses_real_segments(self):
        # an L-elbow whose chord crosses a box its path misses: path goes
        # right along y=100 then down at x=400; the box sits at the chord's
        # middle (200-260, 200-260) — chord hits it, real path doesn't
        els = [
            {"id": "a", "type": "rectangle", "x": 0, "y": 68, "width": 60,
             "height": 64, "customData": {"role": "node"}},
            {"id": "b", "type": "rectangle", "x": 370, "y": 300,
             "width": 60, "height": 64, "customData": {"role": "node"}},
            {"id": "mid", "type": "rectangle", "x": 200, "y": 200,
             "width": 60, "height": 60, "customData": {"role": "node"}},
            {"id": "t", "type": "arrow", "x": 60, "y": 100,
             "points": [[0, 0], [340, 0], [340, 200]],
             "width": 340, "height": 200,
             "startBinding": {"elementId": "a", "focus": 0, "gap": 6},
             "endBinding": {"elementId": "b", "focus": 0, "gap": 6},
             "customData": {}},
        ]
        lint = canvas.lint_layout(els)
        self.assertFalse([w for w in lint["warnings"]
                          if "passes through" in w and "mid" in w])
        # and a segment that DOES cross still warns: straight arrow
        # through the box
        els[3]["points"] = [[0, 0], [340, 232]]
        lint = canvas.lint_layout(els)
        self.assertTrue([w for w in lint["warnings"]
                         if "passes through" in w])

    def test_wireframe_budget_is_per_screen(self):
        # two screens of 7 blocks each = 14 nodes in one artifact —
        # legal for a state-variant pair; no unconnected-node noise
        els = []
        for si, sx in (("scr-a", 0), ("scr-b", 600)):
            els.append({"id": si, "type": "frame", "x": sx, "y": 0,
                        "width": 400, "height": 800, "customData": {}})
            for k in range(7):
                els.append({"id": "%s-blk%d" % (si, k),
                            "type": "rectangle", "x": sx + 20,
                            "y": 40 + k * 100, "width": 360, "height": 80,
                            "frameId": si, "customData": {"role": "node",
                                                          "kind": "block"}})
        lint = canvas.lint_layout(els)
        self.assertFalse([n for n in lint["notes"] if "budget: 9" in n
                          and "screen" not in n])
        # The firing half, in the same call and one variable away: the
        # note is suppressed because these blocks sit in FRAMES, not
        # because the check is gone. The rule-8 census (2026-08-16)
        # found "unconnected node(s)" asserted absent here and asserted
        # present nowhere in the file, so the framed silence was resting
        # on an instrument nothing had ever heard speak.
        unframed = [dict(e, frameId=None) for e in els
                    if e.get("type") != "frame"]
        self.assertTrue([n for n in canvas.lint_layout(unframed)["notes"]
                         if "unconnected" in n])
        self.assertFalse([n for n in lint["notes"] if "unconnected" in n])
        # but 10+ blocks in ONE screen still notes, per screen
        for k in range(7, 11):
            els.append({"id": "scr-a-blk%d" % k, "type": "rectangle",
                        "x": 20, "y": 40 + k * 68, "width": 360,
                        "height": 60, "frameId": "scr-a",
                        "customData": {"role": "node", "kind": "block"}})
        lint = canvas.lint_layout(els)
        self.assertTrue([n for n in lint["notes"]
                         if "per screen" in n and "scr-a" in n])


class TestDddLints(Base):
    """v0.1 DDD wiring: _Avoid_ relabel warning, context-frame notes,
    strategic marking (references/domain.md)."""

    GLOSSARY = (
        "# Glossary\n\n"
        "**Provider**:\nThe firm supplying market data.\n"
        "_Avoid_: vendor, data supplier\n\n"
        "**Tearsheet**:\nThe morning report.\n_Avoid_: dashboard\n"
    )

    def test_parse_glossary_avoid(self):
        avoid = canvas.parse_glossary_avoid(self.GLOSSARY)
        self.assertEqual(avoid, {"vendor": "Provider",
                                 "data supplier": "Provider",
                                 "dashboard": "Tearsheet"})

    def test_parse_glossary_emdash_format(self):
        # the v0.1 acceptance agent wrote '**Term** — definition' instead
        # of the canonical '**Term**:' — the parser accepts both
        text = ("**The book** — the team's own positions.\n"
                "_Avoid_: portfolio, holdings list\n")
        self.assertEqual(canvas.parse_glossary_avoid(text),
                         {"portfolio": "The book",
                          "holdings list": "The book"})

    def test_avoid_relabel_warns(self):
        avoid = {"vendor": "Provider"}
        els, errors = [], []
        els = canvas.apply_ops(els, [
            {"op": "add", "element": {"type": "rectangle", "id": "prov",
                                      "label": "Vendor", "x": 0, "y": 0,
                                      "kind": "entity"}}], errors)
        self.assertEqual(errors, [])
        lint = canvas.lint_glossary(els, avoid, True)
        self.assertTrue(any("rejected synonym" in w and "'Provider'" in w
                            for w in lint["warnings"]))
        # the canonical label itself never warns
        els2, errors = [], []
        els2 = canvas.apply_ops(els2, [
            {"op": "add", "element": {"type": "rectangle", "id": "prov",
                                      "label": "Provider", "x": 0, "y": 0,
                                      "kind": "entity"}}], errors)
        self.assertEqual(canvas.lint_glossary(els2, avoid, True)["warnings"],
                         [])

    def _domain_with_frames(self, arrow_label=None):
        errors = []
        ops = [
            {"op": "add", "element": {"type": "rectangle", "id": "e1",
                                      "label": "Holding", "x": 40, "y": 40,
                                      "kind": "entity"}},
            {"op": "add", "element": {"type": "frame", "id": "ctx-a",
                                      "label": "Ordering", "x": 0, "y": 0,
                                      "width": 300, "height": 200}},
            {"op": "add", "element": {"type": "frame", "id": "ctx-b",
                                      "label": "Billing", "x": 400, "y": 0,
                                      "width": 300, "height": 200}},
            {"op": "add", "element": {"type": "arrow", "id": "rel",
                                      **({"label": arrow_label}
                                         if arrow_label else {})},
             "from": "ctx-a", "to": "ctx-b"},
        ]
        els = canvas.apply_ops([], ops, errors)
        self.assertEqual(errors, [])
        return els

    def test_two_context_frames_without_map_is_a_note(self):
        els = self._domain_with_frames(arrow_label="customer/supplier")
        lint = canvas.lint_glossary(els, {}, has_context_map=False)
        self.assertTrue(any("CONTEXT-MAP.md" in n for n in lint["notes"]))
        lint = canvas.lint_glossary(els, {}, has_context_map=True)
        self.assertFalse(any("CONTEXT-MAP.md" in n for n in lint["notes"]))

    def test_unlabeled_context_arrow_is_a_note(self):
        els = self._domain_with_frames(arrow_label=None)
        lint = canvas.lint_glossary(els, {}, has_context_map=True)
        self.assertTrue(any("relationship label" in n
                            for n in lint["notes"]))
        els = self._domain_with_frames(arrow_label="conformist")
        lint = canvas.lint_glossary(els, {}, has_context_map=True)
        self.assertFalse(any("relationship label" in n
                             for n in lint["notes"]))

    def test_lane_frames_never_fire_context_lints(self):
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": "e1",
                                      "label": "Holding", "x": 40, "y": 40,
                                      "kind": "entity"}},
            {"op": "add", "element": {"type": "frame", "id": "lane-a",
                                      "label": "Ops", "kind": "lane",
                                      "x": 0, "y": 0, "width": 300,
                                      "height": 200}},
            {"op": "add", "element": {"type": "frame", "id": "lane-b",
                                      "label": "Agent", "kind": "lane",
                                      "x": 0, "y": 220, "width": 300,
                                      "height": 200}}], errors)
        self.assertEqual(errors, [])
        lint = canvas.lint_glossary(els, {}, has_context_map=False)
        self.assertEqual(lint["notes"], [])

    def test_project_lint_reads_glossary_and_map(self):
        (self.project.pk / "CONTEXT.md").write_text(self.GLOSSARY,
                                                    encoding="utf-8")
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": "prov",
                                      "label": "Vendor", "x": 0, "y": 0,
                                      "kind": "entity"}}], errors)
        lint = canvas.project_lint(self.project, els)
        self.assertTrue(any("rejected synonym" in w
                            for w in lint["warnings"]))

    def test_strategic_marking(self):
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": "core-e",
                                      "label": "Tearsheet", "x": 0, "y": 0,
                                      "kind": "entity",
                                      "strategic": "core"}}], errors)
        self.assertEqual(errors, [])
        e = next(x for x in els if x["id"] == "core-e")
        self.assertEqual(e["customData"]["strategic"], "core")
        self.assertEqual(e["backgroundColor"],
                         canvas.STRATEGIC_FILLS["core"])
        # mod path + invalid value rejects
        els = canvas.apply_ops(els, [{"op": "mod", "id": "core-e",
                                      "attrs": {"strategic": "supporting"}}],
                               errors)
        self.assertEqual(errors, [])
        e = next(x for x in els if x["id"] == "core-e")
        self.assertEqual(e["customData"]["strategic"], "supporting")
        bad = []
        canvas.apply_ops(els, [{"op": "mod", "id": "core-e",
                                "attrs": {"strategic": "vital"}}], bad)
        self.assertTrue(any("strategic" in m for m in bad))


class TestTextInTextRepair(Base):
    """Live finding (ESG session): `label` on a type:"text" spec built a
    bound label INSIDE a text element — illegal Excalidraw structure the
    client renders as a giant one-character-wide tower. Three layers:
    fold/reject at add, redirect at mod, heal corrupted files at load."""

    def test_add_label_on_text_folds_to_text(self):
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "text", "id": "note-1",
                                      "label": "typed: factual / omission",
                                      "x": 100, "y": 100,
                                      "role": "annotation"}}], errors)
        self.assertEqual(errors, [])
        self.assertEqual(len(els), 1)  # no separate label element
        e = els[0]
        self.assertEqual(e["text"], "typed: factual / omission")
        self.assertGreater(e["width"], 100)
        self.assertFalse(e.get("boundElements"))

    def test_add_label_and_text_on_text_rejects(self):
        errors = []
        canvas.apply_ops([], [
            {"op": "add", "element": {"type": "text", "id": "note-2",
                                      "text": "a", "label": "b",
                                      "x": 0, "y": 0}}], errors)
        self.assertTrue(any("not `label`" in m for m in errors))

    def test_mod_label_on_text_sets_text(self):
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "text", "id": "note-3",
                                      "text": "old", "x": 0, "y": 0}}],
            errors)
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "note-3",
             "attrs": {"label": "new content"}}], errors)
        self.assertEqual(errors, [])
        self.assertEqual(len(els), 1)
        self.assertEqual(els[0]["text"], "new content")
        self.assertEqual(els[0]["originalText"], "new content")

    def test_long_label_wraps_and_grows_container(self):
        # live finding: 'Labels: sparse, months late' spilled out both
        # sides of its 260px box — the client clips at container bounds
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": "opt-c",
                                      "label": "Labels: sparse, months "
                                               "late, external only",
                                      "x": 0, "y": 0, "width": 260,
                                      "height": 64, "role": "node"}}],
            errors)
        self.assertEqual(errors, [])
        lbl = next(e for e in els if e.get("containerId") == "opt-c")
        box = next(e for e in els if e["id"] == "opt-c")
        # text stays UNWRAPPED (originalText/replay/facts hygiene); the
        # allotted box forces the client's own wrap instead
        self.assertNotIn("\n", lbl["text"])
        self.assertLessEqual(lbl["width"], box["width"] - 20)
        self.assertGreater(lbl["height"], 22)           # multi-line room
        self.assertGreaterEqual(box["height"], lbl["height"] + 16)
        self.assertFalse(lbl["autoResize"])
        # short labels stay single-line and untouched
        els2 = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": "ok",
                                      "label": "Fits fine", "x": 0, "y": 0,
                                      "width": 260, "height": 64,
                                      "role": "node"}}], errors)
        lbl2 = next(e for e in els2 if e.get("containerId") == "ok")
        self.assertLessEqual(lbl2["height"], 22)

    def test_mod_label_rewrap(self):
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": "n1",
                                      "label": "Short", "x": 0, "y": 0,
                                      "width": 180, "height": 64,
                                      "role": "node"}}], errors)
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "n1",
             "attrs": {"label": "A very long relabel that cannot possibly "
                                "fit on one line in this box"}}], errors)
        self.assertEqual(errors, [])
        lbl = next(e for e in els if e.get("containerId") == "n1")
        box = next(e for e in els if e["id"] == "n1")
        self.assertNotIn("\n", lbl["text"])
        self.assertGreater(lbl["height"], 22)
        self.assertLessEqual(lbl["width"], box["width"] - 20)
        self.assertGreaterEqual(box["height"], lbl["height"] + 16)

    def test_validate_scene_merges_text_in_text(self):
        # the exact corrupted shape found on disk: empty container text,
        # content living in a bound label pointing at it
        doc = {"type": "excalidraw", "version": 2, "elements": [
            {"id": "note-x", "type": "text", "x": 1050, "y": 724,
             "width": 10, "height": 20, "text": "", "originalText": "",
             "fontSize": 16,
             "boundElements": [{"id": "note-x-label", "type": "text"}],
             "customData": {"role": "annotation"}},
            {"id": "note-x-label", "type": "text", "x": 1056, "y": 724,
             "width": 758, "height": 20, "fontSize": 16,
             "text": "typed: factual / divergence",
             "originalText": "typed: factual / divergence",
             "containerId": "note-x", "customData": {}},
        ]}
        fixed, issues = canvas.validate_scene(doc, "esg-domain")
        ids = [e["id"] for e in fixed["elements"]]
        self.assertIn("note-x", ids)
        self.assertNotIn("note-x-label", ids)
        note = next(e for e in fixed["elements"] if e["id"] == "note-x")
        self.assertEqual(note["text"], "typed: factual / divergence")
        self.assertGreater(note["width"], 100)
        self.assertFalse(note["boundElements"])
        self.assertTrue(any("ART-010" == i.code for i in issues))


class TestLoadRepairRerouteAndConfess(Base):
    """v0.9 WP4: a load-time repair that resizes a shape owns what it
    moved. The ART-011 refit grows a container to fit a wrapped label,
    which drags the border out from under every arrow endpoint sitting
    on it; `endpoint_gap` then reported the stranded endpoint against
    the USER's arrow. The repair now re-routes what it displaced
    (ART-012) — and the trigger stops firing on the 8px note padding
    that made every note-bearing project repair itself on load."""

    LONG = "Escalate to the compliance review board immediately"

    def oversized(self, arrows):
        """A 120x60 box with a 400px label, plus the given arrows.

        Args:
            arrows: Arrow elements to append to the scene.

        Returns:
            A scene document ready for `validate_scene`.
        """
        return {"type": "excalidraw", "version": 2, "elements": [
            {"id": "n1", "type": "rectangle", "x": 0, "y": 0, "width": 120,
             "height": 60, "customData": {"role": "node"},
             "boundElements": [{"id": "t1", "type": "text"}]},
            {"id": "t1", "type": "text", "x": 4, "y": 20, "width": 400,
             "height": 20, "text": self.LONG, "originalText": self.LONG,
             "fontSize": 16, "containerId": "n1", "textAlign": "center"},
            {"id": "far", "type": "rectangle", "x": 400, "y": 0,
             "width": 120, "height": 60, "customData": {"role": "node"}},
            {"id": "far2", "type": "rectangle", "x": 400, "y": 300,
             "width": 120, "height": 60, "customData": {"role": "node"}},
            *arrows]}

    def test_an_arrow_on_an_untouched_shape_is_left_alone(self):
        # Control for the re-route's blast radius: `far -> far2` is bound
        # to two shapes the refit never touches, so the repair must not
        # so much as re-derive its geometry. Without this a re-route that
        # simply routed every arrow in the scene would pass the flip.
        untouched = {"id": "a2", "type": "arrow", "x": 460, "y": 60,
                     "width": 0, "height": 240, "points": [[0, 0], [0, 240]],
                     "startBinding": {"elementId": "far", "focus": 0,
                                      "gap": 1},
                     "endBinding": {"elementId": "far2", "focus": 0,
                                    "gap": 1}}
        before = json.loads(json.dumps(untouched))
        doc, issues = canvas.validate_scene(
            self.oversized([untouched]), "a")
        # n1 was resized, so the confession fires — but it must say that
        # nothing rode on n1, and a2 must come out byte-identical
        self.assertEqual([i.code for i in issues], ["ART-011", "ART-012"])
        self.assertIn("no arrow was bound to it", issues[1].msg)
        after = next(e for e in doc["elements"] if e["id"] == "a2")
        self.assertEqual(after, before)

    def test_a_hand_authored_arrow_is_named_not_redrawn(self):
        # The other half of the ownership rule. A bent, unmarked path is
        # the user's geometry (`server_owns_geometry`), so the loader may
        # not straighten it to tidy up after itself — it says what it did
        # and hands the arrow back. Silently redrawing someone's line to
        # make a finding go away is the same misattribution wearing the
        # opposite coat.
        drawn = {"id": "a3", "type": "arrow", "x": 60, "y": 300,
                 "width": 80, "height": 240,
                 "points": [[0, 0], [80, -120], [0, -240]],
                 "startBinding": {"elementId": "far2", "focus": 0, "gap": 1},
                 "endBinding": {"elementId": "n1", "focus": 0, "gap": 1}}
        before = json.loads(json.dumps(drawn))
        doc, issues = canvas.validate_scene(self.oversized([drawn]), "a")
        self.assertEqual([i.code for i in issues], ["ART-011", "ART-012"])
        said = issues[1]
        self.assertIn("left a3 as drawn", said.msg)
        self.assertIn("re-routed nothing", said.msg)
        self.assertIn("the user's own geometry", said.hint)
        after = next(e for e in doc["elements"] if e["id"] == "a3")
        self.assertEqual(after["points"], before["points"])
        self.assertEqual((after["x"], after["y"]), (before["x"], before["y"]))

    def test_the_confession_names_the_shape_and_the_size_it_became(self):
        # The misattribution half, asserted on its wording: before this,
        # `issues` said only that a LABEL had been refit, and nothing
        # anywhere said the loader had resized a shape carrying arrows.
        # An agent reading the load had no way to know the drawing had
        # moved, which is what let `endpoint_gap`'s "re-route it" land on
        # the user as an instruction about their own work.
        routed = {"id": "a1", "type": "arrow", "x": 60, "y": 300,
                  "width": 0, "height": 240, "points": [[0, 0], [0, -240]],
                  "startBinding": {"elementId": "far2", "focus": 0,
                                   "gap": 1},
                  "endBinding": {"elementId": "n1", "focus": 0, "gap": 1}}
        _, issues = canvas.validate_scene(self.oversized([routed]), "a")
        self.assertEqual([i.code for i in issues], ["ART-011", "ART-012"])
        self.assertTrue(issues[1].repaired)
        self.assertIn("resized n1 (120x60 to 120x136)", issues[1].msg)
        self.assertIn("re-routed a1", issues[1].msg)

    def test_a_refit_that_resizes_nothing_confesses_nothing(self):
        # The silent pole of the confession rule (fix round 1, F1). The
        # rule is "every resize is confessed", NOT "every refit is" — so
        # a box already tall enough to hold the wrapped label gets its
        # label narrowed and nothing else, and ART-012 must stay quiet.
        # Without this pole, filing the confession unconditionally would
        # pass the resize-with-no-arrows test and tell the agent a shape
        # had moved when it had not.
        doc = self.oversized([])
        tall = next(e for e in doc["elements"] if e["id"] == "n1")
        tall["height"] = 200          # already deeper than the 136 refit
        _, issues = canvas.validate_scene(doc, "a")
        self.assertEqual([i.code for i in issues], ["ART-011"])
        self.assertEqual(tall["height"], 200, "the box was not resized")

    def test_a_declined_refit_leaves_the_label_where_it_was_drawn(self):
        # Both directions of the no-op guard's second effect (fix round
        # 1, F3). Skipping the repair also skips the re-centering under
        # it, deliberately: geometry nobody needed to move is geometry
        # the loader has no business moving.
        #
        # DECLINED — the tearsheet's own shape, a 153px label in a 160px
        # box, which `fit_label_in` refuses to improve. It trips the
        # trigger (153 > 160-16), so the repair path IS entered; the
        # label must come out at the position and size it went in with.
        drawn = {"type": "excalidraw", "version": 2, "elements": [
            {"id": "c", "type": "rectangle", "x": 0, "y": 0, "width": 160,
             "height": 64, "customData": {"role": "node"},
             "boundElements": [{"id": "c-t", "type": "text"}]},
            {"id": "c-t", "type": "text", "x": 10, "y": 30, "width": 153,
             "height": 20, "text": "Pull market data",
             "originalText": "Pull market data", "fontSize": 16,
             "containerId": "c", "textAlign": "center"}]}
        doc, issues = canvas.validate_scene(drawn, "a")
        self.assertEqual([i.code for i in issues], [])
        lbl = next(e for e in doc["elements"] if e["id"] == "c-t")
        # (4, 22) is where the centring under the repair would have put
        # it, so this position is chosen to tell the two apart
        self.assertEqual((lbl["x"], lbl["y"], lbl["width"], lbl["height"]),
                         (10, 30, 153, 20))
        # ACCEPTED — the same path when the fitter does act still centres
        # the label in the box it just grew, which is what makes the skip
        # above a decision rather than a hole.
        doc2, issues2 = canvas.validate_scene(self.oversized([]), "a")
        self.assertEqual([i.code for i in issues2], ["ART-011", "ART-012"])
        t1 = next(e for e in doc2["elements"] if e["id"] == "t1")
        self.assertEqual((t1["x"], t1["y"], t1["width"], t1["height"]),
                         (12, 8, 96, 120))

    def test_the_reroute_persists_instead_of_recurring(self):
        # The re-route is itself a load-time geometry change, which is
        # the r5-13 hazard class — so it has to answer the same question
        # the refit does: does it settle? First load repairs and
        # re-routes, the reconciliation carries both codes and writes the
        # result down, and the second load has nothing left to do. A
        # re-route that fired on every load would mint a revision on
        # every resume and, since Task 42, spend referential standing
        # doing it.
        els = self.oversized([
            {"id": "a1", "type": "arrow", "x": 60, "y": 300, "width": 0,
             "height": 240, "points": [[0, 0], [0, -240]],
             "startBinding": {"elementId": "far2", "focus": 0, "gap": 1},
             "endBinding": {"elementId": "n1", "focus": 0, "gap": 1}}])
        self.store.commit(author="user", new_scenes={"f": els["elements"]},
                          base_revn=0)
        store = canvas.Store(self.project)
        self.assertEqual([i["code"] for i in store.scene_repairs],
                         ["ART-011", "ART-012"])
        rec = store.catch_up()
        self.assertIsNotNone(rec)
        self.assertIn("ART-011 ×1, ART-012 ×1", rec["summary"]["headline"])
        store2 = canvas.Store(self.project)
        self.assertEqual(store2.scene_repairs, [],
                         "the re-route recurs — every resume mints a "
                         "reconciliation for geometry already settled")
        self.assertIsNone(store2.catch_up())
        first = next(e for e in store.scenes["f"] if e["id"] == "a1")
        a1 = next(e for e in store2.scenes["f"] if e["id"] == "a1")
        self.assertEqual((a1["x"], a1["y"], a1["points"]),
                         (first["x"], first["y"], first["points"]),
                         "the second load re-routed the arrow again")
        # and the settled path is a correct one, not merely a stable one
        warned = [w for w in canvas.project_lint(
            self.project, store2.scenes["f"], registry=store2.registry,
            artifact_type=store2.artifact_type("f"), aid="f")["warnings"]
            if "inside the shape" in w]
        self.assertEqual(warned, [])

    def test_a_client_shaped_note_loads_untouched_twice(self):
        # The r5-13 constant, pinned by construction rather than by
        # fixture: this is exactly what `addStickyNote` posts — a 180x90
        # box with a 164px label, w-16 — and the loader must have
        # nothing to say about it, on this load or any later one. If
        # anybody re-tightens the rule to w-24, the message names the
        # arithmetic instead of leaving a fixture count to be re-baselined.
        note = [{"id": "note", "type": "rectangle", "x": 0, "y": 0,
                 "width": 180, "height": 90, "customData": {"role": "note"},
                 "boundElements": [{"id": "note-label", "type": "text"}]},
                {"id": "note-label", "type": "text", "x": 8, "y": 8,
                 "width": 164, "height": 74, "text": "keep this as drawn",
                 "originalText": "keep this as drawn", "fontSize": 14,
                 "containerId": "note", "autoResize": False}]
        self.store.commit(author="user", new_scenes={"f": note},
                          base_revn=0)
        for load in (1, 2):
            store = canvas.Store(self.project)
            self.assertEqual(store.issues, [],
                             "load %d repaired a note the client itself "
                             "posts: 164 = 180 - 16, and the rule triggers "
                             "above 180 - 16" % load)
            self.assertIsNone(store.catch_up(),
                              "load %d minted a reconciliation" % load)
            lbl = next(e for e in store.scenes["f"] if e["id"] == "note-label")
            self.assertEqual((lbl["width"], lbl["height"]), (164, 74))


class TestTermConceptViewDebt(Base):
    """Post-acceptance wiring: settled terms mint concepts (ADR 0007) and
    view debt is a registry mechanism (`owed`, ADR 0006) paid down as
    views of the owed type register."""

    def test_upsert_concept_mints_unviewed_term_concept(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [{"op": "registry",
                                         "action": "upsert_concept",
                                         "name": "The Book",
                                         "glossary": "The book",
                                         "owed": ["domain"]}]})
        c = next(c for c in self.store.registry["concepts"]
                 if c["id"] == "the-book")
        self.assertTrue(c["unviewed"])
        self.assertEqual(c["glossary"], "The book")
        self.assertEqual(c["owed"], ["domain"])

    def test_view_debt_paid_when_view_registers(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [{"op": "registry",
                                         "action": "upsert_concept",
                                         "id": "checkout",
                                         "owed": ["domain", "sequence"]}]})
        # drawing the owed domain view clears exactly that debt
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-domain",
            "create": {"id": "checkout-domain", "name": "Checkout Domain",
                       "type": "domain", "concept": "checkout",
                       "concept_name": "Checkout"},
            "ops": [{"op": "add", "element": {"type": "rectangle",
                                              "id": "order",
                                              "label": "Order", "x": 0,
                                              "y": 0, "kind": "entity"}}]})
        c = next(c for c in self.store.registry["concepts"]
                 if c["id"] == "checkout")
        self.assertIn("checkout-domain", c["views"])
        self.assertEqual(c["owed"], ["sequence"])
        self.assertFalse(c["unviewed"])

    def test_bad_owed_rejects(self):
        self.store.apply_batch(seed_flow_batch())
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({"base_revn": 1,
                                    "artifact": "checkout-flow",
                                    "ops": [{"op": "registry",
                                             "action": "upsert_concept",
                                             "id": "checkout",
                                             "owed": "domain"}]})

    def test_slugify_never_collides_two_names_onto_one_id(self):
        """Ids are identity anchors. The ASCII-only slug mapped every
        non-Latin name onto the bare fallback, and upsert_concept then
        merged them without a word."""
        self.assertEqual(canvas.slugify("Report"), "report")
        self.assertEqual(canvas.slugify("Émissions"), "emissions")
        self.assertEqual(canvas.slugify("CO₂ Intensity"), "co2-intensity")
        self.assertNotEqual(canvas.slugify("報告"), canvas.slugify("分析"))
        # opaque but stable for a given name
        self.assertEqual(canvas.slugify("報告"), canvas.slugify("報告"))
        self.assertTrue(canvas.slugify("報告").startswith("item-"))
        self.assertEqual(canvas.slugify("   "), "item")
        self.assertEqual(canvas.slugify("", fallback="el"), "el")

    def test_upsert_concept_rejects_name_id_collision(self):
        self.store.apply_batch(seed_flow_batch())
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch({
                "base_revn": 1, "artifact": "checkout-flow",
                "ops": [{"op": "registry", "action": "upsert_concept",
                         "name": "Check Out"},
                        {"op": "registry", "action": "upsert_concept",
                         "name": "Check-out"}]})
        self.assertIn("already belongs to", str(cm.exception))
        # an explicit id is still an update, and a rename keeps the id
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "upsert_concept",
                     "name": "Check Out"},
                    {"op": "registry", "action": "upsert_concept",
                     "id": "check-out", "name": "Checkout Redux"}]})
        c = next(c for c in self.store.registry["concepts"]
                 if c["id"] == "check-out")
        self.assertEqual(c["name"], "Checkout Redux")

    def test_label_color_reads_short_and_alpha_hex(self):
        """A dark fill written as '#000' fell through to dark-on-dark."""
        for bg in ("#000", "#000000", "#000000ff"):
            els = canvas.make_element({"type": "rectangle", "id": "n",
                                       "label": "Hi", "x": 0, "y": 0,
                                       "backgroundColor": bg},
                                      set(), [])
            lbl = next(e for e in els if e["type"] == "text")
            self.assertEqual(lbl["strokeColor"], "#ffffff", bg)
        els = canvas.make_element({"type": "rectangle", "id": "n2",
                                   "label": "Hi", "x": 0, "y": 0,
                                   "backgroundColor": "#ffffff"},
                                  set(), [])
        lbl = next(e for e in els if e["type"] == "text")
        self.assertEqual(lbl["strokeColor"], "#1e1e1e")

    def test_text_dims_measures_real_advances(self):
        # v0.8 (r4-12): per-char Nunito advances replace the flat 0.6-em
        # cell model. CJK counts 1.2em; '62' at 24px must come out WIDER
        # than the 28px box the old int() truncation produced (the live
        # editor wrapped it while the snapshot said one line).
        wide, _ = canvas.text_dims("報告", 16)
        self.assertEqual(wide, int(2 * 1.2 * 16 + 0.999) + 2)
        digits, _ = canvas.text_dims("62", 24)
        self.assertGreaterEqual(digits, 29)  # ceil(28.8), plus pad
        w_cap, _ = canvas.text_dims("W", 24)
        i_cap, _ = canvas.text_dims("I", 24)
        self.assertGreater(w_cap, i_cap * 2)  # real metrics, not cells

    def test_glossary_parsers_accept_bullet_and_inline_avoid(self):
        """A glossary written as a Markdown bullet list parsed to ZERO
        terms, which is indistinguishable from having no glossary — every
        downstream lint went silently dark (ADR 0010 investigation)."""
        bullets = (
            "## Glossary\n\n"
            "- **Vendor Score**: A vendor's aggregated opinion. _Avoid_: "
            "Vendor Rating (ambiguous — split in two). (Settled 2026.)\n"
            "- **Claim**: An atomic assertion.\n")
        self.assertEqual(canvas.parse_glossary_terms(bullets),
                         ["Vendor Score", "Claim"])
        self.assertEqual(canvas.parse_glossary_avoid(bullets),
                         {"vendor rating": "Vendor Score"})

    def test_glossary_parsers_unchanged_on_canonical_format(self):
        canon = ("**Order**:\nA request.\n_Avoid_: Purchase, transaction\n\n"
                 "**Invoice** — a bill.\n_Avoid_: Bill, payment request\n")
        self.assertEqual(canvas.parse_glossary_terms(canon),
                         ["Order", "Invoice"])
        self.assertEqual(canvas.parse_glossary_avoid(canon), {
            "purchase": "Order", "transaction": "Order",
            "bill": "Invoice", "payment request": "Invoice"})

    def test_lint_registry_orphan_terms_and_debt(self):
        registry = {"concepts": [
            {"id": "checkout", "name": "Checkout", "views": ["checkout-flow"],
             "glossary": "Checkout", "unviewed": False, "owed": ["domain"]}]}
        notes = canvas.lint_registry(["Checkout", "The book", "Cutoff"],
                                     registry)
        orphan = [n for n in notes if "no registry concept" in n]
        self.assertEqual(len(orphan), 1)
        self.assertIn("'The book'", orphan[0])
        self.assertIn("'Cutoff'", orphan[0])
        self.assertNotIn("'Checkout'", orphan[0])
        debt = [n for n in notes if "view debt" in n]
        self.assertEqual(len(debt), 1)
        self.assertIn("domain", debt[0])

    def test_umbrella_pileup_notes(self):
        # every view under one concept + several empty term-concepts →
        # the reattachment nudge; distributed views → silence
        piled = {"concepts": [
            {"id": "umbrella", "name": "The Project",
             "views": ["a", "b", "c"], "unviewed": False},
            {"id": "report", "name": "Report", "views": [],
             "unviewed": True},
            {"id": "claim", "name": "Claim", "views": [], "unviewed": True},
            {"id": "topic", "name": "Topic", "views": [], "unviewed": True},
        ]}
        notes = canvas.lint_registry([], piled)
        self.assertTrue(any("MOST SPECIFIC" in n for n in notes))
        piled["concepts"][1]["views"] = ["b"]
        notes = canvas.lint_registry([], piled)
        self.assertFalse(any("MOST SPECIFIC" in n for n in notes))

    def test_umbrella_pileup_survives_one_reattachment(self):
        """ADR 0010: the old `exactly one viewful concept` trigger went
        silent the moment a single view was reattached, while the pile
        kept growing on the umbrella. A concept holding 4+ views is a
        pile-up regardless of how many other concepts have one."""
        piled = {"concepts": [
            {"id": "umbrella", "name": "The Project",
             "views": ["a", "b", "c", "d"], "unviewed": False},
            {"id": "report", "name": "Report", "views": ["e"],
             "unviewed": False},
            {"id": "claim", "name": "Claim", "views": [], "unviewed": True},
            {"id": "topic", "name": "Topic", "views": [], "unviewed": True},
        ]}
        notes = canvas.lint_registry([], piled)
        self.assertTrue(any("MOST SPECIFIC" in n for n in notes))

    def test_misfiled_view_named_after_another_concept(self):
        """The live finding, as the lint sees it: an artifact named after
        a settled term, registered under the umbrella instead."""
        reg = {"concepts": [
            {"id": "greenwashing-detection", "name": "Greenwashing Detection",
             "views": ["esg-domain", "report-wireframe"], "unviewed": False},
            {"id": "report", "name": "Report", "views": [], "unviewed": True},
        ]}
        notes = canvas.lint_registry([], reg)
        misfiled = [n for n in notes if "is named after concept" in n]
        self.assertEqual(len(misfiled), 1)
        self.assertIn("'report-wireframe'", misfiled[0])
        self.assertIn("'Report'", misfiled[0])
        # reattached → silence
        reg["concepts"][0]["views"] = ["esg-domain"]
        reg["concepts"][1]["views"] = ["report-wireframe"]
        notes = canvas.lint_registry([], reg)
        self.assertFalse(any("is named after concept" in n for n in notes))

    def test_misfiled_view_needs_full_concept_slug(self):
        """A multi-token concept must not match on one shared word:
        'detection-flow' is not evidence for concept 'greenwashing-detection'
        (and 'vendor-score' matches nothing here)."""
        reg = {"concepts": [
            {"id": "greenwashing-detection", "name": "Greenwashing Detection",
             "views": [], "unviewed": True},
            {"id": "vendor-score", "name": "Vendor Score", "views": [],
             "unviewed": True},
            {"id": "claim", "name": "Claim", "views": ["detection-flow"],
             "unviewed": False},
        ]}
        notes = canvas.lint_registry([], reg)
        self.assertFalse(any("is named after concept" in n for n in notes))

    def test_view_debt_paid_project_wide(self):
        """ADR 0010: `owed` is archetype debt, so a view of an owed type
        pays it wherever it attaches. Before the fix the only way to pay
        the umbrella's wireframe debt was to file the wireframe on the
        umbrella — the incentive that produced the misfiled view."""
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({"base_revn": 1, "artifact": "checkout-flow",
                                "ops": [
                                    {"op": "registry",
                                     "action": "upsert_concept",
                                     "id": "checkout",
                                     "owed": ["wireframe", "sequence"]},
                                    {"op": "registry",
                                     "action": "upsert_concept",
                                     "name": "Receipt",
                                     "glossary": "Receipt"}]})
        # the owed wireframe lands on the SPECIFIC concept, not the umbrella
        self.store.apply_batch({
            "base_revn": 2, "artifact": "receipt-wireframe",
            "create": {"id": "receipt-wireframe", "name": "Receipt",
                       "type": "wireframe", "concept": "receipt"},
            "ops": [{"op": "add", "element": {"type": "rectangle",
                                              "id": "total",
                                              "label": "Total", "x": 0,
                                              "y": 0}}]})
        umbrella = next(c for c in self.store.registry["concepts"]
                        if c["id"] == "checkout")
        receipt = next(c for c in self.store.registry["concepts"]
                       if c["id"] == "receipt")
        self.assertEqual(umbrella["owed"], ["sequence"])
        self.assertEqual(umbrella["views"], ["checkout-flow"])
        self.assertEqual(receipt["views"], ["receipt-wireframe"])

    def test_project_lint_carries_registry_notes(self):
        (self.project.pk / "CONTEXT.md").write_text(
            "**The book** — the team's positions.\n", encoding="utf-8")
        self.store.apply_batch(seed_flow_batch())
        lint = canvas.project_lint(self.project, self.scene(),
                                   self.store.registry)
        self.assertTrue(any("no registry concept" in n and "The book" in n
                            for n in lint["notes"]))
        # registry omitted (default) → no registry-level notes, no crash
        lint = canvas.project_lint(self.project, self.scene())
        self.assertFalse(any("no registry concept" in n
                             for n in lint["notes"]))


class TestQuestionSurfaces(Base):
    """In-place question UI backing (pins with detail/examples, tripwires
    answerable with choices or free text, annotate_tripwire enrichment)."""

    def _fire_tripwire(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "add_mapping",
                     "concept": "checkout",
                     "elements": ["checkout-flow#cart",
                                  "checkout-flow#payment"]}]})
        els = self.scene()
        for e in els:
            if e["id"] == "cart-label":
                e["text"] = "Basket"
                e["originalText"] = "Basket"
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=2)
        open_tw = [t for t in self.store.registry["tripwires"]
                   if t["status"] == "open"]
        self.assertTrue(open_tw)
        return open_tw[0]

    def test_pin_carries_detail_and_examples(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "pin", "target": "cart", "id": "pin-cart",
                     "question": "Is the cart persistent?",
                     "detail": "Carts can be session-scoped or durable.\n\n"
                               "The answer decides whether we need an "
                               "identity before checkout.",
                     "examples": ["Amazon: durable, tied to account",
                                  "Kiosk: session only"]}]})
        p = next(p for p in self.store.registry["pins"]
                 if p["id"] == "pin-cart")
        self.assertIn("session-scoped", p["detail"])
        self.assertEqual(len(p["examples"]), 2)

    def test_pin_bad_detail_rejects(self):
        self.store.apply_batch(seed_flow_batch())
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({
                "base_revn": 1, "artifact": "checkout-flow",
                "ops": [{"op": "pin", "target": "cart", "id": "p1",
                         "question": "q?", "examples": "not-a-list"}]})

    def test_tripwire_defaults_are_answerable(self):
        t = self._fire_tripwire()
        self.assertTrue(t["question"])
        self.assertEqual(len(t["choices"]), 2)
        self.assertIn("Divergence", t["question"])
        self.assertIn("mapping", t["detail"])

    def test_annotate_tripwire(self):
        t = self._fire_tripwire()
        self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "annotate_tripwire",
                     "id": t["id"],
                     "question": "Basket or Cart — which name wins?",
                     "choices": ["Basket everywhere", "Cart everywhere",
                                 "They are different things"],
                     "detail": "The glossary says Cart.",
                     "examples": ["UK sites say basket"]}]})
        t2 = next(x for x in self.store.registry["tripwires"]
                  if x["id"] == t["id"])
        self.assertEqual(len(t2["choices"]), 3)
        self.assertIn("glossary", t2["detail"])
        # unknown id is a named validation error
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({
                "base_revn": self.store.head_revn(),
                "artifact": "checkout-flow",
                "ops": [{"op": "registry", "action": "annotate_tripwire",
                         "id": "tw-nope", "question": "?"}]})

    def test_answer_tripwire(self):
        t = self._fire_tripwire()
        out = self.store.answer_tripwire(t["id"],
                                         "Intentional divergence — keep both")
        self.assertEqual(out["status"], "answered")
        self.assertIn("Intentional", out["answer"])
        with self.assertRaises(canvas.BatchError):
            self.store.answer_tripwire("tw-nope", "x")
        # resolved tripwires are not answerable
        self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "resolve_tripwire",
                     "id": t["id"]}]})
        with self.assertRaises(canvas.BatchError):
            self.store.answer_tripwire(t["id"], "again")


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tearsheet-demo"


class TestTearsheetFixture(unittest.TestCase):
    """Merge-bar regression: the real v0 tearsheet demo session replayed
    through v0.1 code. The fixture is frozen history; the pinned numbers
    come from the v0 audit (docs/refinement/v0_issues_and_improvements.md).
    v0 baseline being regressed against: lint returned ZERO findings on a
    diagram with 20/34 endpoints detached (F3); rewires silently no-opped
    (F2); node moves never rerouted bound arrows (F1)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-fixture-"))
        shutil.copytree(FIXTURE_DIR, self.tmp / "project_knowledge")
        self.project = canvas.Project(self.tmp)
        self.store = canvas.Store(self.project)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p in (self.project.state_path, self.project.events_path,
                  self.project.log_path):
            if p.exists():
                p.unlink()

    def test_replay_reconverges_via_catchup(self):
        # The demo's multi-server history left one known divergence: three
        # pipeline arrows whose disk points drifted from the recorded head.
        # v0.1 must (a) replay all 18 v0-era records (string bindings and
        # all), (b) reconcile the drift as a geometry-only out-of-session
        # record — nothing structural invented or lost — and (c) converge.
        self.assertEqual(self.store.head_revn(), 18)
        rec = self.store.catch_up()
        self.assertIsNotNone(rec)
        self.assertTrue(rec.get("reconciliation"))
        self.assertEqual(rec["author"], "out-of-session")
        changed = {}
        for aid, part in rec["artifacts"].items():
            for c in part.get("changes") or []:
                changed.setdefault(c["id"], []).append(c)
        self.assertEqual(set(changed), {"t-market-compute",
                                        "t-news-summarize",
                                        "t-positions-compute"})
        for cs in changed.values():
            for c in cs:
                self.assertEqual(c["op"], "mod")
                self.assertEqual([a["attr"] for a in c["attrs"]], ["points"])
        # converged: replayed head now matches disk for every artifact
        state = self.store.state_at(self.store.head_revn())
        for aid, part in state.items():
            self.assertEqual(canvas.scene_hash(part["elements"]),
                             canvas.scene_hash(self.store.scenes[aid]),
                             "post-reconciliation divergence in %s" % aid)

    def test_lint_sees_the_audit_wreckage(self):
        # Audit measured, at revn 10 of tearsheet-pipeline: 20/34 arrow
        # endpoints geometrically detached, 14/17 diagonal connectors,
        # through-node crossings — and v0 lint said NOTHING. v0.1 lint must
        # find it all: 22 detachment ERRORs (per-endpoint, lint threshold),
        # diagonal + through-node WARNINGs.
        #
        # v0.7: 26, not 22. The check tested containment in a bbox grown
        # by TOL, which a point INSIDE satisfies trivially, so it only
        # ever saw the outward direction (r3-16). The 4 additions are all
        # inward and all real — e.g. t-macro-cutoff starts at (590,308)
        # inside pull-macro-news y[260..324], 16px past the edge, and
        # t-trigger-macro 32px inside daily-trigger. The original 22
        # outward findings are unchanged.
        els = self.store.state_at(10)["tearsheet-pipeline"]["elements"]
        lint = canvas.lint_layout(els)
        detach = [m for m in lint["errors"] if "claims to bind" in m]
        self.assertEqual(len(detach), 26)
        self.assertEqual(len([m for m in detach if "away" in m]), 22)
        self.assertEqual(len([m for m in detach if "inside the shape" in m]), 4)
        self.assertEqual(len(detach), len(lint["errors"]),
                         "unexpected non-detachment ERRORs: %r"
                         % [m for m in lint["errors"]
                            if "claims to bind" not in m])
        diagonal = [m for m in lint["warnings"] if "diagonally" in m]
        self.assertGreaterEqual(len(diagonal), 10)
        self.assertTrue(any("passes through" in m
                            for m in lint["warnings"]))

    def test_v0_arrows_are_server_owned(self):
        # Migration rule: v0's server only ever drew straight 2-point
        # arrows, so unmarked 2-point geometry is server-owned (reroutable);
        # a user bend adds points and opts the arrow out.
        arrows = [e for els in self.store.scenes.values() for e in els
                  if e.get("type") == "arrow"]
        self.assertEqual(len(arrows), 30)
        self.assertTrue(all(canvas.server_owns_geometry(a) for a in arrows))

    def test_move_heals_audit_detachments(self):
        # F1 on the real wreckage: at revn 10, input-cutoff is the node the
        # detached arrows cite most. Moving it must reroute every bound
        # server-owned arrow — its detachment ERRORs go to zero, and the
        # rerouted arrows come out signature-stamped.
        els = self.store.state_at(10)["tearsheet-pipeline"]["elements"]
        before = [m for m in canvas.lint_layout(els)["errors"]
                  if "claims to bind" in m and "input-cutoff" in m]
        self.assertTrue(before)
        errors = []
        moved = canvas.apply_ops(els, [{"op": "mod", "id": "input-cutoff",
                                        "attrs": {"x": 1340, "y": 420}}],
                                 errors)
        self.assertEqual(errors, [])
        after = [m for m in canvas.lint_layout(moved)["errors"]
                 if "claims to bind" in m and "input-cutoff" in m]
        self.assertEqual(after, [])
        by_id = {e["id"]: e for e in moved}
        for a in by_id.values():
            if a.get("type") != "arrow":
                continue
            binds = {(a.get("startBinding") or {}).get("elementId"),
                     (a.get("endBinding") or {}).get("elementId")}
            if "input-cutoff" in binds:
                self.assertIsInstance(
                    (a.get("customData") or {}).get("routed"), str)

    def test_rewire_lands_on_v0_data(self):
        # F2 on the real artifact: a rewire through the head state must
        # actually change the binding and re-stamp geometry — v0 answered
        # "saved without changing anything".
        base = self.store.head_revn()
        self.store.apply_batch({
            "base_revn": base, "artifact": "tearsheet-pipeline",
            "ops": [{"op": "mod", "id": "t-market-compute",
                     "attrs": {"to": "agent-write-tearsheet"}}]})
        t = next(e for e in self.store.scenes["tearsheet-pipeline"]
                 if e["id"] == "t-market-compute")
        self.assertEqual(t["endBinding"]["elementId"],
                         "agent-write-tearsheet")
        self.assertIsInstance(t["customData"]["routed"], str)
        echo = canvas.intent_echo(
            [{"op": "mod", "id": "t-market-compute",
              "attrs": {"to": "agent-write-tearsheet"}}],
            self.store.scenes["tearsheet-pipeline"])
        self.assertTrue(any("t-market-compute" in ln
                            and "agent-write-tearsheet" in ln
                            for ln in echo))


class FixtureReplayBase(unittest.TestCase):
    """Shared harness for frozen-project replay fixtures (v0.8 corpus).

    Every remediation work package must leave these replays green: a check
    validated only against the case that motivated it is validated against
    one data point, and naive checks over-fire on real data (the run-1
    lesson: 16 warnings, 13 false). Subclasses set ``FIXTURE``.
    """

    FIXTURE = ""

    def setUp(self):
        """Copy the frozen project into a temp dir and load it.

        Skips when ``FIXTURE`` is unset, and THAT GUARD IS LOAD-BEARING:
        this class is abstract, but since it acquired a test method of
        its own (`..._catchup_can_still_speak_...`) unittest collects the
        base itself, where ``FIXTURE`` is "" and the copy below would
        take the whole fixtures TREE before dying on an empty scene list.
        Anyone adding a second base-class control inherits the guard by
        putting it here rather than in the test — do not move it.
        """
        if not self.FIXTURE:
            self.skipTest("abstract base — subclasses set FIXTURE")
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-fixture-"))
        src = Path(__file__).resolve().parent / "fixtures" / self.FIXTURE
        shutil.copytree(src, self.tmp / "project_knowledge")
        self.project = canvas.Project(self.tmp)
        self.store = canvas.Store(self.project)

    def tearDown(self):
        """Remove the temp copy and the shared runtime files."""
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p in (self.project.state_path, self.project.events_path,
                  self.project.log_path):
            if p.exists():
                p.unlink()

    def lint_all(self):
        """Run project_lint over every artifact.

        Returns:
            {artifact_id: lint result dict} for the loaded scenes.
        """
        out = {}
        for aid in sorted(self.store.scenes):
            out[aid] = canvas.project_lint(
                self.project, self.store.scenes[aid],
                registry=self.store.registry,
                artifact_type=self.store.artifact_type(aid), aid=aid)
        return out

    def test_catchup_can_still_speak_about_this_fixture(self):
        """The firing pole every replay class's `catch_up` silence needs.

        The rule-8 census (2026-08-16) called the `catch_up`/
        `scene_repairs` silences the file's largest DEFICIT cluster —
        ~18 assertions over 7 classes that a load minted no
        reconciliation, with nothing beside them proving one can be
        minted. The stub sweep run for this task corrected half of that:
        killing `Store.catch_up` already fails 7 tests in 5 classes, so
        the detector is not dead and a plain cross-reference would have
        been an honest close.

        What those 7 do NOT cover is the half that bites here. Every one
        of them drives a SYNTHETIC scene; these classes replay a frozen
        real project, and `catch_up`'s `repair_only` branch is gated on
        the raw disk hashes matching replayed history — a gate a fixture
        can fail for reasons that have nothing to do with divergence
        (TASK-CENSUSGAPS gap 1 lost half a pole to exactly that). A
        `catch_up` that had gone quiet on THIS corpus, and only this
        corpus, would leave all 7 green and every silence below it
        vacuous.

        So the control lives on the base and runs once per fixture,
        against that fixture's own first artifact: an out-of-session
        rename on disk, the same edit `TestCatchUp.test_out_of_session_
        edit_reconciles` uses, through the same `Store` load the
        silences read. Measured 2026-08-16: all four fixtures narrate
        the rename and repair nothing.
        """
        aid = sorted(self.store.scenes)[0]
        path = self.project.artifacts_dir / (aid + ".excalidraw")
        doc = json.loads(path.read_text())
        edited = next((e for e in doc["elements"]
                       if e.get("type") == "text" and e.get("text")), None)
        self.assertIsNotNone(edited, "fixture %s has no text to edit — the "
                                     "premise of this control is gone"
                             % self.FIXTURE)
        edited["text"] = edited["originalText"] = "OUT OF SESSION"
        path.write_text(json.dumps(doc))
        rec = canvas.Store(self.project).catch_up()
        self.assertIsNotNone(rec, "an out-of-session edit to %s minted no "
                                  "reconciliation — every `catch_up` "
                                  "silence in this class is vacuous" % aid)
        self.assertTrue(rec["reconciliation"])
        self.assertIn("renamed", [f["fact"] for f in
                                  rec["artifacts"][aid]["facts"]])


class TestArgusR4Arm3Fixture(FixtureReplayBase):
    """Assessment run 4, arm 3: 44 saves, 7 artifacts, 28 concepts.

    The richest real session on record (v0.7): resolved tripwires, scoped
    divergence rulings, deliberate standing warnings, one deliberately
    unbound self-loop workaround (r4-11's fallout, D8).
    """

    FIXTURE = "argus-r4-arm3"

    def test_replays_full_history(self):
        self.assertEqual(self.store.head_revn(), 44)
        self.assertEqual(sorted(self.store.scenes), [
            "admin-console", "aggregation-flow", "argus-domain",
            "argus-run-flow", "dashboard", "enrichment-pipeline",
            "publication-flow"])

    def test_the_load_repairs_nothing_and_reports_nothing(self):
        # FLIPPED by v0.9 WP4 (was: five ART-011 label refits, nothing
        # else). All five were sticky notes at the client's own w-16
        # padding, tripping a rule that repaired above w-24 — the r5-13
        # 8px false positive, five times over on one recorded session.
        # With the rule at w-16 this project loads exactly as its author
        # left it.
        #
        # Asserted over `issues`, not `scene_repairs`, so a quarantine
        # would fail it too: the claim is that the richest real session
        # on record gives the loader nothing to say.
        self.assertEqual([i.get("code") for i in self.store.issues], [])

    def test_catchup_mints_nothing_on_either_load(self):
        # FLIPPED TWICE. WP2 turned this fixture's phantom into a named
        # repair-only reconciliation (5 refits, converging on the second
        # load). v0.9 WP4 removed the refits themselves — all five were
        # the r5-13 note-padding false positive — so the honest end
        # state is no reconciliation at all: resuming this project
        # writes nothing, which is what a resume should do.
        #
        # The repair-only headline it used to be the only cover for now
        # lives on a scene that genuinely needs a refit
        # (`TestReferentialIntegrity`), because a fixture that loads
        # clean cannot exercise an attribution path for repairs.
        self.assertIsNone(self.store.catch_up())
        store2 = canvas.Store(self.project)
        self.assertIsNone(store2.catch_up())

    def test_standing_lint_is_the_arms_deliberate_record(self):
        # Arm 3 left the admin-console reading-order warnings standing on
        # purpose ("the answer is in the lint") — they must survive replay
        # exactly, and nothing may fire an ERROR anywhere.
        #
        # ITEMIZED 2026-08-17 (the contrast ruling). The counts moved,
        # admin-console 5 -> 7 and dashboard 2 -> 8, and the movement is
        # entirely the dropped composed-furniture exemption: 2 slider
        # tracks here and 6 chart-placeholder X strokes there, all of
        # them #b8b2a5 at 2.06:1 against 1.4.11's 3:1. They are TRUE
        # findings about a frozen drawing — this arm really did ship
        # furniture a reader cannot pick out, and no tier reported it
        # until the exemption went — so they are expected by SHAPE here
        # rather than absorbed into a bigger number. The fixtures keep
        # the pre-ruling ink by design (they are a record of sessions
        # that happened); TASK-FOCUS-FOLLOWUP's rebase is what clears
        # them, and when it does these two clauses go to zero and the
        # counts return to 5 and 2.
        lint = self.lint_all()
        furniture = {}
        for aid in ("admin-console", "dashboard"):
            furniture[aid] = [w for w in lint[aid]["warnings"]
                              if "1.4.11" in w and "#b8b2a5" in w]
        self.assertEqual(len(furniture["admin-console"]), 2,
                         furniture["admin-console"])
        self.assertEqual(len(furniture["dashboard"]), 6,
                         furniture["dashboard"])
        self.assertEqual(len(lint["admin-console"]["warnings"]), 7)
        self.assertTrue(all("reading order" in w or "precedes" in w
                            for w in lint["admin-console"]["warnings"]
                            if w not in furniture["admin-console"]))
        self.assertEqual(len(lint["dashboard"]["warnings"]), 8)
        for aid, r in lint.items():
            self.assertEqual(r["errors"], [],
                             "unexpected ERROR in %s: %r" % (aid, r["errors"]))

    def test_unbound_relationship_arrow_now_warns(self):
        # FLIPPED BY WP1 (was: pinned the defect). r-run-rerun is the
        # hand-authored self-loop workaround for the router crash — a
        # labeled domain relationship bound to NOTHING that rendered
        # perfectly and followed nothing (D8). The lint must name it,
        # exactly once, and nothing else on the artifact may fire.
        els = self.store.scenes["argus-domain"]
        loop = next(e for e in els if e["id"] == "r-run-rerun")
        self.assertIsNone(loop.get("startBinding"))
        self.assertIsNone(loop.get("endBinding"))
        lint = self.lint_all()["argus-domain"]
        named = [w for w in lint["warnings"] if "binds nothing" in w]
        self.assertEqual(len(named), 1, lint["warnings"])
        self.assertIn("r-run-rerun", named[0])
        # SECOND FINDING, SAME ARROW, DIFFERENT DEFECT (v0.9
        # TASK-LINTPROMOTE). This test used to assert the artifact fired
        # exactly one warning, as the unbound lint's over-fire guard.
        # The promoted bidirectional check now names `r-run-rerun` too,
        # and it is worth reading rather than counting past: the same
        # hand-authored self-loop that binds nothing also brings its
        # return leg up the line `r-run-signal` departs by, so 30px of
        # ink under `pipeline-run` is drawn twice with a head at each
        # end. One workaround, two defects, and the corpus's only live
        # bidirectional finding.
        #
        # Both findings are pinned by shape and the total is pinned
        # beside them, so this is a strictly tighter statement than the
        # bare count it replaces — a third check waking up on this
        # artifact still fails here, which is what the original count
        # was protecting.
        bidi = [w for w in lint["warnings"] if "bidirectional edge" in w]
        self.assertEqual(len(bidi), 1, lint["warnings"])
        self.assertIn("r-run-signal", bidi[0])
        self.assertIn("sharing 30px", bidi[0])
        self.assertEqual(len(lint["warnings"]), 2,
                         "unbound-lint over-fired: %r" % lint["warnings"])


class TestArgusR4Arm4Fixture(FixtureReplayBase):
    """Assessment run 4, arm 4: 28 saves, 5 artifacts, 3 ADRs, handover."""

    FIXTURE = "argus-r4-arm4"

    def test_replays_full_history(self):
        self.assertEqual(self.store.head_revn(), 28)
        self.assertEqual(sorted(self.store.scenes), [
            "admin-console-wireframe", "daily-run-flow",
            "dashboard-wireframe", "enrichment-flow",
            "review-publish-flow"])

    def test_catchup_finds_nothing_to_reconcile(self):
        # FLIPPED BY WP2 (was: pinned the phantom that read "saved
        # without changing anything" while committing — r4b save 0028's
        # live shape). The divergences were all derived machinery
        # (z-order, int/float representation, replayed roundness); with
        # those canonicalized, this project needs no reconciliation.
        #
        # The surviving ART-011 was the last thing the loader still said
        # about this project, and v0.9 WP4 established it was the r5-13
        # note-padding false positive — one sticky note at the client's
        # own w-16 padding. Silence is now the whole claim.
        self.assertIsNone(self.store.catch_up())
        self.assertEqual(self.store.scene_repairs, [])

    def test_no_lint_errors_anywhere(self):
        for aid, r in self.lint_all().items():
            self.assertEqual(r["errors"], [],
                             "unexpected ERROR in %s: %r" % (aid, r["errors"]))

    def test_this_arms_share_of_the_pre_ruling_palette_findings(self):
        """Arm 4's 8 of the 19 standing contrast findings, by shape.

        The user's 2026-08-17 contrast ruling darkened both defaults and
        dropped the composed-furniture exemption; the frozen fixtures
        keep the PRE-RULING ink, because they are records of sessions
        that happened. So 19 findings stand across the corpus — true
        statements about old drawings — and they are executable here
        rather than asserted in prose.

        WHY THIS TEST EXISTS AT ALL (review IMPORTANT-1): the first cut
        itemized only arm 3's 8, while the handover claimed all 19 were
        pinned. A fixture rebase that cleared 8 and left 11 would have
        passed the whole suite under a handover saying otherwise.

        THE EIGHT ASSERTED BELOW, and the count is the sum of the
        assertions rather than a number carried in from anywhere: 6
        chart-placeholder X strokes on `dashboard-wireframe`, plus the
        pre-ruling annotation ink on `dashboard-wireframe` AND on
        `enrichment-flow`, one each. Arm 3's 8 and run 5's 3 are pinned
        in their own classes.

        BY SHAPE, NOT BY TOTAL, deliberately: no warning-count assertion
        is added here. Coupling these to this artifact's total would
        make every unrelated check that ever fires on `dashboard-
        wireframe` fail in a test about the palette. TASK-FOCUS-
        FOLLOWUP's fixture rebase is what takes these to zero, and when
        it does the counts below go to 0 in one edit.
        """
        lint = self.lint_all()
        furniture = [w for w in lint["dashboard-wireframe"]["warnings"]
                     if "1.4.11" in w and "#b8b2a5" in w]
        self.assertEqual(len(furniture), 6, furniture)
        for aid in ("dashboard-wireframe", "enrichment-flow"):
            ink = [w for w in lint[aid]["warnings"]
                   if "1.4.3 asks" in w and "#5c8a5f" in w]
            self.assertEqual(len(ink), 1,
                             "%s's pre-ruling annotation ink: %r"
                             % (aid, ink))

    def test_the_canonical_lane_finding_survives_on_recorded_work(self):
        """The one corpus lane the auto-fan structurally cannot reach.

        `e-in-sentiment` is server-routed and `e-edgar-sentiment` is
        authored, and they arrive at `sentiment-scorer` on finals 80px
        long and EXACTLY 16px apart. That 16 is the whole reason this
        pin exists. `LANE_TOL` is 16 and the comparison is `>`, so this
        pair qualifies on the boundary and nowhere inside it: moving the
        test to `>=`, or retuning the tolerance down by any amount at
        all, drops the most-cited finding on the corpus and leaves every
        other number in the suite standing.

        Fanning cannot reach it either, which is why it is lint signal
        rather than router backlog: the auto-fan needs two fannable feet
        on a side, and one of these two is a 4-point authored path.

        The MAGNITUDE is asserted, not just the fire. A check that found
        this pair while measuring the wrong span would satisfy a
        presence test and hand the agent a number that points at the
        wrong part of the drawing.
        """
        said = [w for w in self.lint_all()["enrichment-flow"]["warnings"]
                if "run together for" in w]
        self.assertEqual(len(said), 1, said)
        self.assertIn("e-in-sentiment", said[0])
        self.assertIn("e-edgar-sentiment", said[0])
        self.assertIn("run together for 80px, 16px apart", said[0])
        self.assertIn("they arrive at sentiment-scorer", said[0])

    def test_no_label_is_accused_of_hanging_over_its_shape(self):
        """v0.9 WP4's shape check must stay quiet on recorded work.

        `to-compose-label` on `enrichment-flow` is the scene that caught
        the first cut measuring a fitted label's wrapping FRAME instead
        of its ink (review F1): a 136px frame holding 85px of centred
        text, 20px clear of the ellipse on both sides, reported as
        hanging 11px over empty canvas. This artifact is a real session
        someone drew and reviewed, so a shape warning here is a false
        positive until proven otherwise — the standing count is zero.

        THE OVERHANGING SCENE RIDES THE SAME LOOP. Curator batch 24
        (2026-08-16), against the mortality spike's §4c: "the standing
        count is zero" is a claim about the DRAWINGS, and a check that
        has stopped answering produces the same zero. This test survived
        every one of the spike's 21 detector kills, which is the
        definition of a pin that proves nothing about its own
        instrument. A diamond carrying one unsplittable word wider than
        its chord is the smallest scene the check must speak on, and it
        goes through `canvas.project_lint` inside this loop — not beside
        it — so a regression that silenced only the fixture arm still
        fails here.
        """
        text = "Unsplittable" * 3
        firing = [{"id": "n1", "type": "diamond", "x": 0, "y": 0,
                   "width": 200, "height": 100,
                   "customData": {"role": "node"},
                   "boundElements": [{"id": "t1", "type": "text"}]},
                  {"id": "t1", "type": "text", "x": 0, "y": 40,
                   "width": canvas.text_dims(text, 16)[0], "height": 20,
                   "text": text, "originalText": text, "fontSize": 16,
                   "textAlign": "center", "verticalAlign": "middle",
                   "containerId": "n1"}]
        results = dict(self.lint_all())
        results[None] = canvas.project_lint(self.project, firing,
                                            registry=self.store.registry,
                                            artifact_type="flow")
        spoke: list[str] = []
        for aid, r in results.items():
            hits = [w for w in r["warnings"] if "overhangs" in w]
            if aid is None:
                spoke = hits
            else:
                self.assertEqual(hits, [],
                                 "shape-overhang warning on %s" % aid)
        self.assertEqual(len(spoke), 1,
                         "a word wider than the rhombus it sits in must "
                         "still be reported, or the zeros above are about "
                         "the check and not about the session; got %s"
                         % spoke)


class TestArgusR5Fixture(FixtureReplayBase):
    """Assessment run 5: 23 saves, 6 artifacts, 22 concepts, one ADR.

    The first fixture on record carrying a **branch pair** — the user
    checked out an old revision, saved on top of it (forking `alt-0022`),
    and the agent restored `main` and archived the fork. Branch handling
    had no coverage at all before this session, in tests or in any of the
    four prior runs.

    It also carries a user sticky note, which is what makes the r5-13
    replay below bite.
    """

    FIXTURE = "argus-r5"

    def test_replays_full_history(self):
        self.assertEqual(self.store.head_revn(), 23)
        self.assertEqual(sorted(self.store.scenes), [
            "admin-console", "argus-domain", "daily-run", "edgar-late",
            "enrichment-flow", "tuesday-triage"])

    def test_no_lint_errors_anywhere(self):
        for aid, r in self.lint_all().items():
            self.assertEqual(r["errors"], [],
                             "unexpected ERROR in %s: %r" % (aid, r["errors"]))

    def test_this_runs_share_of_the_pre_ruling_palette_findings(self):
        """Run 5's 3 of the 19 standing contrast findings, by shape.

        The third and last share (arm 3 has 8, arm 4 has 8 — see
        `TestArgusR4Arm4Fixture` for why these are itemized per fixture
        rather than claimed in prose). All three are on `admin-console`:
        2 slider tracks at the pre-ruling `#b8b2a5`, and 1 sticky-era
        annotation at `#5c8a5f`.

        These stand until TASK-FOCUS-FOLLOWUP rebases the fixtures, and
        the two clauses go to 0 in one edit when it does. No total is
        asserted, on purpose — see the arm 4 twin.
        """
        lint = self.lint_all()["admin-console"]
        furniture = [w for w in lint["warnings"]
                     if "1.4.11" in w and "#b8b2a5" in w]
        self.assertEqual(len(furniture), 2, furniture)
        ink = [w for w in lint["warnings"]
               if "1.4.3 asks" in w and "#5c8a5f" in w]
        self.assertEqual(len(ink), 1, ink)

    def test_forked_branch_survives_replay_archived(self):
        names = {b["name"]: b.get("archived")
                 for b in self.store.registry["branches"]}
        self.assertEqual(names, {"main": False, "alt-0022": True})

    def test_sticky_note_survives_a_load_untouched(self):
        # FLIPPED by v0.9 WP4, exactly as the old body demanded: this
        # used to PIN r5-13, asserting the repair and the reconciliation
        # the defect guaranteed. Both note creators (the client's
        # addStickyNote and `_x_user_note`) pad the label by w-16 while
        # the ART-011 rule repaired anything wider than w-24 — an 8px
        # disagreement that was structural, so a refit was guaranteed on
        # the first load of every note anyone had ever made, and the
        # refit moved geometry, so `catch_up` minted an out-of-session
        # reconciliation the user never caused.
        #
        # The rule now triggers at w-16, the padding the client itself
        # posts, so this note is what the user drew and stays that way.
        # Both loads are asserted, not just the first: a repair that
        # fires once and persists would satisfy the second alone, and
        # the r5-13 harm was the FIRST load minting a revision.
        self.assertEqual(self.store.scene_repairs, [])
        self.assertIsNone(self.store.catch_up())
        note = next(e for e in self.store.scenes["daily-run"]
                    if e["id"] == "usernote-43435b73-t")
        self.assertEqual((note["width"], note["height"]), (214, 35))
        store2 = canvas.Store(self.project)
        self.assertEqual(store2.scene_repairs, [])
        self.assertIsNone(store2.catch_up())


class TestAcceptanceTearsheetFixture(FixtureReplayBase):
    """The formerly-dead fixture, wired in: 15 saves, 3 artifacts."""

    FIXTURE = "acceptance-tearsheet"

    def test_replays_and_converges_without_reconciliation(self):
        # FLIPPED THREE TIMES. WP2 turned this fixture's phantom into a
        # named repair-only reconciliation; WP4's real font metrics made
        # the ART-011 refits CONVERGE memory to the replayed history (the
        # old 0.6-em estimate was what disagreed with the client), so
        # repairs fired, were reported, and nothing diverged.
        #
        # v0.9 WP4 stopped them firing, and for the sharper reason: both
        # refits changed NOTHING. `fit_label_in` declined them (no wrap
        # of a 153px label sits any better in a 160px box than the label
        # as written), and the loader filed a repair anyway. A repair
        # that mutates nothing has nothing to persist, so these two
        # re-reported themselves on every load of this project forever —
        # the recurring half of r5-13, with no geometry damage to make
        # it visible. The remaining honest report is `lint_layout`'s:
        # the remedy for a label that will not wrap is a wider node.
        self.assertEqual(self.store.head_revn(), 15)
        self.assertEqual(sorted(self.store.scenes), [
            "tearsheet-failures", "tearsheet-flow", "tearsheet-sheet"])
        self.assertEqual(self.store.scene_repairs, [])
        self.assertIsNone(self.store.catch_up())
        store2 = canvas.Store(self.project)
        self.assertIsNone(store2.catch_up())
        # the label the loader used to claim it had refit is untouched
        lbl = next(e for e in self.store.scenes["tearsheet-flow"]
                   if e["id"] == "pull-market-data-label")
        self.assertEqual((lbl["width"], lbl["height"]), (153, 20))

    def test_the_roundness_rule_reaches_disk_and_mints_nothing(self):
        # v0.9 WP4 stages 1 and 3. This fixture was frozen while every
        # elbow rendered {"type": 2}, so it is the switch's real
        # subject, in BOTH directions: it went sharp at stage 1 and
        # comes back curved at stage 3, and either flip only reaches an
        # arrow on disk because `rebuild_bound_elements` re-derives
        # roundness at load.
        #
        # The load-time re-derivation is the r5-13 hazard — a load-time
        # repair that moves geometry mints a reconciliation on EVERY
        # resume — so this asserts the whole no-mint chain, not just the
        # shape: the repair set is unchanged (roundness is not a repair),
        # and catch_up finds nothing across TWO loads. It cannot: the
        # derived value is absent from both `content_fingerprint` and
        # DEFAULT_SIGNIFICANT_ATTRS, and disk and replayed history run
        # through the same derivation. Task 42's epoch work sharpens the
        # stake, since a reconciliation would now also spend standing.
        #
        # The repair set was two ART-011s when this was written and is
        # empty since v0.9 WP4 fixed both (see the test above). The
        # claim is unchanged — re-deriving roundness adds no repair —
        # but an empty list is a weaker witness for it, so the roundness
        # assertion above stays the load-bearing one.
        raw = json.loads((self.project.artifacts_dir
                          / "tearsheet-flow.excalidraw").read_text())
        # guard against a vacuous pass if the fixture is ever re-frozen
        self.assertTrue(any(e.get("roundness") for e in raw["elements"]
                            if e.get("type") in ("arrow", "line")),
                        "fixture is no longer curved-era — this test would "
                        "pass without proving anything")
        # This fixture cannot witness the PROPAGATION on its own — it was
        # frozen under a rule that agrees with the current one arrow for
        # arrow — so the re-derivation is proved directly below and this
        # test keeps the half only a real project can show: that the
        # propagation mints nothing.
        both = [{"id": "s", "type": "arrow", "x": 0, "y": 0,
                 "points": [[0, 0], [100, 0]], "roundness": {"type": 2}},
                {"id": "e", "type": "arrow", "x": 0, "y": 0,
                 "points": [[0, 0], [100, 0], [100, 60]], "roundness": None}]
        for a in both:
            a["customData"] = {"routed": canvas._route_sig(a)}
        canvas.rebuild_bound_elements(both)
        self.assertIsNone(both[0]["roundness"], "a straight route kept a "
                                                "curvature it did not earn")
        self.assertEqual(both[1]["roundness"], {"type": 2},
                         "a route with a turn was not re-curved at load")
        curved = 0
        for aid, els in self.store.scenes.items():
            for e in els:
                if e.get("type") not in ("arrow", "line"):
                    continue
                # SERVER-OWNED ONLY (review F7). `rebuild_bound_elements`
                # promises the derived value for arrows the server routed
                # and deliberately leaves a hand-authored path alone, so
                # asserting over every arrow overstated the contract. It
                # passed here only because this fixture happens to be 14
                # arrows / 14 owned; pointed at `argus-r4-arm3` (57
                # arrows, 40 owned) the unguarded form failed on 15
                # arrows that were behaving exactly as designed.
                if not canvas.server_owns_geometry(e):
                    continue
                # The GATE's contract, not the candidate rule's (v0.9
                # Task 57). A turn no longer earns curvature by itself —
                # it earns a CANDIDACY, and `gate_curvature` accepts or
                # reverts it. So the two things that must hold are that
                # a straight route is sharp unconditionally, and that a
                # turned route carries either the candidate or nothing.
                # Asserting the candidate outright would re-pin the
                # ungated switch this task removed.
                got = e.get("roundness")
                if len(e.get("points") or []) < 3:
                    self.assertIsNone(
                        got, "%s/%s: a straight route earned curvature"
                        % (aid, e["id"]))
                else:
                    self.assertIn(
                        got, (None, canvas.derived_roundness(e)),
                        "%s/%s carries %r, which is neither sharp nor the "
                        "candidate" % (aid, e["id"], got))
                    curved += got is not None
        # not vacuous: the gate has to be ACCEPTING somewhere on this
        # fixture, or the two assertions above would both be satisfied by
        # a gate that had jammed shut.
        self.assertTrue(curved, "the gate declined every arrow in the "
                                "project — nothing here proves it accepts")
        self.assertEqual(self.store.scene_repairs, [])
        self.assertIsNone(self.store.catch_up())
        store2 = canvas.Store(self.project)
        self.assertIsNone(store2.catch_up())
        self.assertEqual(store2.scene_repairs, [])


def _gate_scene(pts, roundness=None, obstacle=None, loop=False):
    """A bound elbow the curvature gate will judge, plus optional obstacle.

    Both nodes sit exactly on the route's endpoints so nothing else in
    `lint_layout` has an opinion — the scenes below are about ONE
    verdict, and an endpoint-gap complaint in the baseline would be
    noise on both sides of every comparison.

    Args:
        pts: The arrow's stored points, relative to (100, 100).
        roundness: What the file carries before the gate runs, curved
            when omitted; the gate resets it either way, so this exists
            to prove it does.
        obstacle: Optional `(x, y, w, h)` for a third node named `mid`.
        loop: True to bind both ends to the same node.

    Returns:
        The scene, with the arrow server-owned so the gate judges it.
    """
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    roundness = {"type": 2} if roundness is None else roundness
    dst = "a" if loop else "b"
    arrow = {"id": "e", "type": "arrow", "x": 100, "y": 100, "points": pts,
             "width": max(xs) - min(xs), "height": max(ys) - min(ys),
             "customData": {"role": "edge"}, "roundness": roundness,
             "startBinding": {"elementId": "a", "focus": 0, "gap": 0},
             "endBinding": {"elementId": dst, "focus": 0, "gap": 0}}
    arrow["customData"]["routed"] = canvas._route_sig(arrow)

    def node(i, x, y):
        """A 60x60 node box.

        Args:
            i: Element id.
            x: Box left.
            y: Box top.

        Returns:
            The node element dict.
        """
        return {"id": i, "type": "rectangle", "x": x, "y": y, "width": 60,
                "height": 60, "customData": {"role": "node"}}

    els = [node("a", 70, 70),
           node("b", 100 + pts[-1][0] - 30, 100 + pts[-1][1] - 30), arrow]
    if obstacle:
        m = node("mid", obstacle[0], obstacle[1])
        m["width"], m["height"] = obstacle[2], obstacle[3]
        els.insert(2, m)
    return els


def _verdict(els):
    """Run the load path and report what the gate left on arrow `e`.

    Args:
        els: A scene from `_gate_scene`.

    Returns:
        The arrow's `roundness` after `rebuild_bound_elements`.
    """
    canvas.rebuild_bound_elements(els)
    return next(e for e in els if e["id"] == "e").get("roundness")


class TestCurvatureGate(unittest.TestCase):
    """`gate_curvature`: which arrows earn a curve, and why the rest do not.

    v0.9 Task 57, replacing the unconditional switch review F1 blocked.
    The design of record is `V0.9-PLAN.md:540` — "propose a curvature
    increment on an in-memory candidate, re-run the geometry checks,
    accept if clean, and revert to the last clean state at the first
    violation" — and these are its poles. Both arms are pinned from BOTH
    sides, because a gate that accepted everything and a gate that
    accepted nothing would each satisfy half of this class.
    """

    def test_a_clean_elbow_earns_its_curve(self) -> None:
        """The accepting pole, and the one that stops this class going
        vacuous.

        A balanced 300+300 elbow between two boxes: nothing to cross,
        nothing to hide, and its drawn arrivals lean 7.0 degrees, well
        inside `NEAR_AXIS`. If this ever goes sharp the gate has jammed
        shut and every other test here would still pass.
        """
        self.assertEqual(_verdict(_gate_scene([[0, 0], [300, 0], [300, 300]])),
                         {"type": 2})

    def test_an_arrival_that_stops_reading_as_square_is_reverted(self) -> None:
        """The squareness arm, on the geometry that made it necessary.

        260px east then 37px down is `argus-run-flow`'s `t-agg` shape,
        and curved it arrives 40.6 degrees off the axis the router
        chose — past `NEAR_AXIS`, so the candidate is refused. This is
        the arm that does the visible work: 14 of the corpus's 38
        eligible arrows decline on it.
        """
        self.assertIsNone(_verdict(_gate_scene([[0, 0], [260, 0], [260, 37]])))

    def test_the_same_route_curves_once_the_short_leg_is_long_enough(
            self) -> None:
        """The squareness arm's other side: the shape, not the arrow.

        Same approach, same nodes, a longer final leg — and the verdict
        flips. Without this the test above would also pass on a gate
        that had simply refused every 3-point route, which is the
        `derived_roundness` rule this task replaced, inverted.
        """
        self.assertEqual(
            _verdict(_gate_scene([[0, 0], [260, 0], [260, 260]])),
            {"type": 2})

    def test_a_candidate_that_moves_the_finding_set_is_reverted(self) -> None:
        """The finding-set arm: the bow crosses a box the chords miss.

        A 300+300 elbow bows 18.8px off its stored run, and `mid` sits
        in exactly that gap — invisible to the stored polyline, plainly
        crossed on screen. Curved, `lint_layout` gains "arrow e passes
        through mid"; the gate sees the finding set move and reverts.

        This arm fires nowhere on the 24-fixture corpus, which is why it
        is pinned synthetically here. It is not ornamental: the corpus
        is 38 server-owned arrows out of 61 eligible, and a freshly
        drawn project is entirely server-owned, so this is the arm that
        does the work in the world the gate was actually built for.
        """
        self.assertIsNone(_verdict(_gate_scene(
            [[0, 0], [300, 0], [300, 300]], obstacle=(220, 62, 60, 30))))

    def test_the_same_elbow_without_the_obstacle_curves(self) -> None:
        """The finding-set arm's other pole — one node apart.

        Proves the revert above is the OBSTACLE's doing and not the
        elbow's, which is the only thing that makes it evidence about
        the arm rather than about the shape.
        """
        self.assertEqual(_verdict(_gate_scene(
            [[0, 0], [300, 0], [300, 300]])), {"type": 2})

    def test_a_straight_route_is_never_a_candidate(self) -> None:
        """Two points, frozen curved by the old era, sharp after the load.

        Curvature is a property of a turn. This is also the propagation
        half of the load-time re-derivation: scoping the gate's reset to
        the ELIGIBLE arrows left a curved-era straight arrow carrying a
        curvature nothing would ever take away, which is how it failed
        the first time it was written.
        """
        self.assertIsNone(_verdict(_gate_scene([[0, 0], [300, 0]])))

    def test_a_self_loop_declines(self) -> None:
        """Recorded as a taste decision, and pinned so it stays one.

        A self-loop leaves and re-enters one node, so it is the shape
        most made of corners, and r5-14's cold observer read the curved
        one's erased corner as "a stray L-shaped stub" — a complaint
        that was out-measured rather than answered. The corpus's single
        self-loop clears the squareness arm by 1.3 degrees, so without
        this rule the decision would be made by a threshold that never
        considered it.
        """
        self.assertIsNone(_verdict(_gate_scene(
            [[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]], loop=True)))

    def test_a_hand_authored_path_is_not_judged_at_all(self) -> None:
        """The gate reaches only what the server routed.

        Same guard as the rest of `rebuild_bound_elements`: a path the
        user reshaped keeps the shape they gave it, including a
        curvature this gate would have refused. Dropping the `routed`
        mark is what `mod points` does.
        """
        els = _gate_scene([[0, 0], [260, 0], [260, 37]])
        arrow = next(e for e in els if e["id"] == "e")
        arrow["customData"].pop("routed")
        self.assertEqual(_verdict(els), {"type": 2})

    def test_the_verdict_is_recomputed_from_the_scene_every_load(
            self) -> None:
        """The answer to the flare spike's staleness objection.

        That spike's decisive argument against carrying a curvature
        verdict on the arrow is that it cannot self-invalidate: it is a
        SCENE-level judgement stored per-arrow, so a node moving on the
        far side of the drawing falsifies it while the arrow's own
        `_route_sig` still matches perfectly. Nothing here is stored —
        the gate runs inside `rebuild_bound_elements` — so the verdict
        cannot go stale, and this is that claim made executable: the
        SAME arrow flips from curved to sharp because a box moved into
        its bow, with its own geometry and its routed mark untouched.
        """
        clean = _gate_scene([[0, 0], [300, 0], [300, 300]])
        self.assertEqual(_verdict(clean), {"type": 2})
        sig = next(e for e in clean
                   if e["id"] == "e")["customData"]["routed"]
        moved = _gate_scene([[0, 0], [300, 0], [300, 300]],
                            obstacle=(220, 62, 60, 30))
        self.assertIsNone(_verdict(moved))
        self.assertEqual(
            next(e for e in moved if e["id"] == "e")["customData"]["routed"],
            sig, "the arrow's own signature changed, so this proves "
                 "nothing about a SCENE-level verdict going stale")

    def test_the_same_scene_gives_the_same_verdicts_every_time(self) -> None:
        """Deterministic, and not by accident.

        Eligible arrows are taken in sorted `id` order from an all-sharp
        start, so the result cannot depend on element order in the file
        or on what the file happened to carry. Shuffled input, reversed
        input, and a re-run over an already-gated scene all agree.
        """
        def verdicts(els):
            canvas.rebuild_bound_elements(els)
            return {e["id"]: e.get("roundness") for e in els
                    if e.get("type") == "arrow"}

        base = _gate_scene([[0, 0], [300, 0], [300, 300]],
                           obstacle=(220, 62, 60, 30))
        want = verdicts(json.loads(json.dumps(base)))
        self.assertEqual(verdicts(json.loads(json.dumps(base))[::-1]), want)
        again = json.loads(json.dumps(base))
        verdicts(again)
        self.assertEqual(verdicts(again), want, "gating an already-gated "
                                                "scene changed the verdict")


class TestClientRemeasure(Base):
    """Client font-metric re-measurement must never masquerade as user
    edits. Excalidraw re-measures every text element on load (real font
    metrics vs the server's 0.6em estimate) and re-wraps bound labels
    with literal newlines in `text`; before the fix that produced a
    phantom `resized` fact on every user save, wrap-poisoned facts and
    headlines, and false-dirty artifacts (capability assessment
    2026-08-08)."""

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        # a legacy-shaped free annotation (no explicit width → autoResize
        # stays True, exactly like every pre-fix annotation on disk) and
        # a multi-word label for the wrap simulation
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {
                    "type": "text", "id": "note-legacy",
                    "text": "the review step must block the report",
                    "x": 40, "y": 320, "role": "annotation"}},
                {"op": "mod", "id": "confirm",
                 "attrs": {"label": "Order placed today"}},
            ]})

    def _remeasured_scene(self):
        """The scene as the browser posts it back after a load: label
        re-wrapped (originalText intact), annotation blown out to its
        natural single-line width, container min-height settled."""
        els = self.scene()
        for e in els:
            if e["id"] == "confirm-label":
                e["text"] = "Order placed\ntoday"
                e["originalText"] = "Order placed today"
            if e["id"] == "note-legacy":
                e["width"], e["height"] = 745, 20
            if e["id"] == "cart":
                e["height"] = e["height"] + 2   # 28→30-style settle
        return els

    def test_client_remeasure_is_derived_and_silent(self):
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow":
                                            self._remeasured_scene()},
                                base_revn=2)
        facts = rec["artifacts"].get("checkout-flow", {}).get("facts", [])
        names = {f["fact"] for f in facts}
        self.assertNotIn("resized", names)
        self.assertNotIn("renamed", names)
        for f in facts:
            self.assertNotIn("\n", json.dumps(f))
        for ch in rec["artifacts"].get("checkout-flow", {}).get("changes", []):
            if ch["op"] != "mod":
                continue
            for a in ch["attrs"]:
                if a["attr"] in ("width", "height", "text"):
                    self.assertTrue(
                        a.get("derived"),
                        "%s.%s not derived: %r" % (ch["id"], a["attr"], a))
        # de-wrap: the stored label text carries no wrap newline
        lbl = {e["id"]: e for e in
               self.store.scenes["checkout-flow"]}["confirm-label"]
        self.assertEqual(lbl["text"], "Order placed today")
        self.assertEqual(lbl["originalText"], "Order placed today")

    def test_replay_converges_after_remeasure_save(self):
        self.store.commit(author="user",
                          new_scenes={"checkout-flow":
                                      self._remeasured_scene()},
                          base_revn=2)
        n = len(self.store.records)
        store = canvas.Store(self.project)
        # FIRING POLE: `TestCatchUp.test_out_of_session_edit_reconciles`.
        # Measured for the rule-8 census close (2026-08-16): killing
        # `Store.catch_up` outright already fails 7 tests across 5
        # classes, so the cross-reference is the honest close here — this
        # is a synthetic scene, and the synthetic pole is its match. The
        # per-fixture half the 7 do not cover lives on
        # `FixtureReplayBase.test_catchup_can_still_speak_about_this_
        # fixture`.
        self.assertIsNone(store.catch_up())     # no phantom reconciliation
        self.assertEqual(len(store.records), n)

    def test_deliberate_annotation_resize_still_narrates(self):
        els = self.scene()
        for e in els:
            if e["id"] == "note-legacy":
                e["autoResize"] = False          # the width-drag intent flip
                e["width"] = 300
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": els},
                                base_revn=2)
        names = [f["fact"] for f in
                 rec["artifacts"]["checkout-flow"]["facts"]]
        self.assertIn("resized", names)
        attrs = {a["attr"] for ch in
                 rec["artifacts"]["checkout-flow"]["changes"]
                 if ch["op"] == "mod" and ch["id"] == "note-legacy"
                 for a in ch["attrs"]}
        self.assertIn("autoResize", attrs)       # the flip itself is recorded
        store = canvas.Store(self.project)
        # FIRING POLE: as above — `TestCatchUp`, plus the per-fixture
        # control on `FixtureReplayBase`.
        self.assertIsNone(store.catch_up())     # ...and survives replay

    def test_wrapped_text_never_reaches_facts_or_disk(self):
        els = self.scene()
        for e in els:
            if e["id"] == "cart-label":          # real rename, posted wrapped
                e["text"] = "Guest or\naccount?"
                e["originalText"] = "Guest or account?"
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": els},
                                base_revn=2)
        renames = [f for f in rec["artifacts"]["checkout-flow"]["facts"]
                   if f["fact"] == "renamed"]
        self.assertTrue(renames)
        self.assertEqual(renames[0]["to"], "Guest or account?")
        self.assertNotIn("\n", rec["summary"]["headline"])
        doc = json.loads((self.project.artifacts_dir /
                          "checkout-flow.excalidraw").read_text())
        lbl = {e["id"]: e for e in doc["elements"]}["cart-label"]
        self.assertEqual(lbl["text"], "Guest or account?")

    def test_remeasured_resave_is_explicit_empty(self):
        scene = self._remeasured_scene()
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": scene},
                          base_revn=2)
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": scene},
                                base_revn=3)
        facts = [f["fact"] for a in rec["artifacts"].values()
                 for f in a.get("facts", [])] or ["saved_no_changes"]
        self.assertEqual(set(facts), {"saved_no_changes"})


class TestZOrderNormalization(Base):
    """Arrows must paint UNDER nodes (layout.md doctrine); before the fix
    every apply left arrows z-above all nodes because elements landed in
    batch order."""

    def test_arrows_sort_below_nodes_labels_on_top(self):
        self.store.apply_batch(seed_flow_batch())
        els = self.store.scenes["checkout-flow"]
        bands = []
        for e in els:
            if e["type"] in ("arrow", "line"):
                bands.append(1)
            elif e["type"] == "text" and e.get("containerId"):
                bands.append(3)
            else:
                bands.append(2)
        self.assertEqual(bands, sorted(bands), [e["id"] for e in els])

    def test_zorder_survives_replay(self):
        self.store.apply_batch(seed_flow_batch())
        store = canvas.Store(self.project)
        # FIRING POLE: `TestCatchUp.test_out_of_session_edit_reconciles`
        # (rule-8 census close, 2026-08-16; killing `Store.catch_up`
        # fails 7 tests in 5 classes), plus the per-fixture control on
        # `FixtureReplayBase`.
        self.assertIsNone(store.catch_up())


class TestModPathFixes(Base):
    """The mod-dispatch asymmetry family (capability assessment +
    live_test_2 B1): `mod label` on a frame minted a floating bound text
    instead of renaming; `mod kind` wrote a top-level key nothing reads
    while echoing success; `mod points` worked but disowned the arrow's
    route signature and narrated as an empty save; unknown attrs were
    silently accepted."""

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "frame", "id": "zone-pay",
                                          "label": "Payment zone",
                                          "x": 440, "y": 80,
                                          "width": 420, "height": 160}},
            ]})

    def test_frame_rename_via_label(self):
        rec, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "zone-pay",
                 "attrs": {"label": "Settlement zone"}}]})
        by_id = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        self.assertEqual(by_id["zone-pay"]["name"], "Settlement zone")
        # no bound text minted onto the frame
        self.assertFalse([e for e in by_id.values()
                          if e.get("containerId") == "zone-pay"])
        names = [f["fact"] for f in
                 rec["artifacts"]["checkout-flow"]["facts"]]
        self.assertIn("renamed", names)

    def test_mod_kind_persists_and_records(self):
        rec, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "cart", "attrs": {"kind": "source"}}]})
        by_id = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        self.assertEqual(canvas.kind_of(by_id["cart"]), "source")
        mods = [c for c in rec["artifacts"]["checkout-flow"]["changes"]
                if c["op"] == "mod" and c["id"] == "cart"]
        self.assertTrue(any(a["attr"] == "customData"
                            for c in mods for a in c["attrs"]))

    def test_mod_unknown_attr_rejected(self):
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch({
                "base_revn": 2, "artifact": "checkout-flow", "ops": [
                    {"op": "mod", "id": "cart", "attrs": {"bogus": 1}}]})
        self.assertIn("bogus", str(cm.exception))

    def test_mod_points_stamps_and_narrates(self):
        rec, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "t1",
                 "attrs": {"points": [[0, 0], [40, 0], [40, 60],
                                      [120, 60]]}}]})
        by_id = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        # v0.3: hand-authored waypoints are the author's geometry — marked
        # "authored", never server-owned, so no later pass re-routes them
        self.assertEqual(by_id["t1"]["customData"]["routed"], "authored")
        self.assertFalse(canvas.server_owns_geometry(by_id["t1"]))
        names = [f["fact"] for f in
                 rec["artifacts"]["checkout-flow"]["facts"]]
        self.assertIn("rerouted", names)
        self.assertIn("rerouted", rec["summary"]["headline"])

    def test_echo_covers_every_op_kind(self):
        """No op may be silently skipped in the echo — skipped numbers
        read as dropped ops (capability assessment)."""
        ops = [
            {"op": "pin", "id": "pin-q", "target": "cart",
             "question": "why a cart?"},
            {"op": "registry", "action": "upsert_concept", "name": "Cart"},
        ]
        self.store.apply_batch({"base_revn": 2,
                                "artifact": "checkout-flow", "ops": ops})
        echo = canvas.intent_echo(ops, self.store.scenes["checkout-flow"])
        self.assertEqual(len(echo), 2)
        self.assertIn("pin", echo[0])
        self.assertIn("❓ on canvas", echo[0])
        self.assertIn("registry", echo[1])
        ops2 = [{"op": "resolve_pin", "id": "pin-q"}]
        self.store.apply_batch({"base_revn": 3,
                                "artifact": "checkout-flow", "ops": ops2})
        echo2 = canvas.intent_echo(ops2, self.store.scenes["checkout-flow"])
        self.assertIn("❓ glyph removed from canvas", echo2[0])
        # B3: resolve_pin alone removed the glyph and the registry agrees
        self.assertNotIn("pin-q", {e["id"] for e in
                                   self.store.scenes["checkout-flow"]})
        pin = next(p for p in self.store.registry["pins"]
                   if p["id"] == "pin-q")
        self.assertEqual(pin["status"], "resolved")

    def test_registry_only_batch_headlines_registry(self):
        rec, pin_only = self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "registry", "action": "upsert_concept",
                 "name": "Ledger", "glossary": "Ledger"}]})
        self.assertTrue(pin_only)
        self.assertTrue(rec["summary"]["headline"].startswith("registry:"),
                        rec["summary"]["headline"])


class TestStandingNags(Base):
    """v0.2 standing-nag machinery (live_test_2 B4/B5 + capability
    assessment lints): LINT_DEBT and PIN_DEBT recomputed on every apply,
    reconciliation passing the lint gate, and the new lint checks."""

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_lint_debt_covers_untouched_artifacts(self):
        # second artifact with a deliberate overlap
        self.store.apply_batch({
            "base_revn": 1, "artifact": "messy",
            "create": {"id": "messy", "name": "Messy", "type": "flow",
                       "concept": "mess"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "a",
                                          "label": "A", "x": 40, "y": 40,
                                          "width": 160, "height": 64}},
                {"op": "add", "element": {"type": "rectangle", "id": "b",
                                          "label": "B", "x": 60, "y": 52,
                                          "width": 160, "height": 64}},
            ]})
        # touch ONLY checkout-flow; messy's drift must still be visible
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "cart", "attrs": {"x": 44}}]})
        debt = self.store.lint_debt()
        self.assertIn("messy", debt)
        self.assertGreater(debt["messy"]["warnings"], 0)

    def test_pin_debt_ages_and_counts_target_edits(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "pin", "id": "pin-cart", "target": "cart",
                 "question": "why a cart?"}]})
        # the pin's target changes in a user save
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "cart", "attrs": {"label": "Basket"}}],
            errors)
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els}, base_revn=2)
        debt = self.store.pin_debt()
        entry = next(p for p in debt if p["id"] == "pin-cart")
        self.assertEqual(entry["status"], "open")
        self.assertGreaterEqual(entry["target_edits"], 1)

    def test_annotation_budget_note(self):
        ops = [{"op": "add", "element": {
            "type": "text", "id": "n%d" % i, "text": "note %d" % i,
            "x": 40, "y": 400 + 90 * i, "role": "annotation"}}
            for i in range(3)]
        self.store.apply_batch({"base_revn": 1,
                                "artifact": "checkout-flow", "ops": ops})
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertTrue(any("annotation callouts" in n
                            for n in lint["notes"]))

    def test_label_collision_warning(self):
        # two labeled shapes stacked → their labels overlap too
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "s1",
                                          "label": "disputes 1", "x": 900,
                                          "y": 400, "width": 160,
                                          "height": 64}},
                {"op": "add", "element": {"type": "rectangle", "id": "s2",
                                          "label": "disputes 2", "x": 908,
                                          "y": 408, "width": 160,
                                          "height": 64}},
            ]})
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertTrue(any("labels" in w and "overlap" in w
                            for w in lint["warnings"]), lint["warnings"])

    def test_reverse_glossary_and_zero_terms(self):
        ctx = self.project.pk / "CONTEXT.md"
        ctx.write_text("# Glossary\n\n- **Cart**: the basket.\n",
                       encoding="utf-8")
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "registry", "action": "upsert_concept",
                 "name": "Ghost", "glossary": "Ghost"}]})
        lint = canvas.project_lint(self.project,
                                   self.store.scenes["checkout-flow"],
                                   self.store.registry)
        self.assertTrue(any("CONTEXT.md doesn't define" in n
                            for n in lint["notes"]), lint["notes"])
        ctx.write_text("prose with no term lines\n", encoding="utf-8")
        lint = canvas.project_lint(self.project,
                                   self.store.scenes["checkout-flow"],
                                   self.store.registry)
        self.assertTrue(any("zero glossary terms parsed" in n
                            for n in lint["notes"]), lint["notes"])

    def test_reconciliation_runs_lint(self):
        # out-of-session edit that creates a legibility defect
        path = self.project.artifacts_dir / "checkout-flow.excalidraw"
        doc = json.loads(path.read_text())
        for e in doc["elements"]:
            if e["id"] == "checkout":
                e["x"], e["y"] = 44, 124        # stack onto cart
        path.write_text(json.dumps(doc))
        store = canvas.Store(self.project)
        rec = store.catch_up()
        self.assertIsNotNone(rec)
        self.assertIn("lint", rec)
        self.assertTrue(any(a.get("warnings")
                            for a in rec["lint"].values()))
        # ...and the findings were persisted into the record on disk
        disk_rec = json.loads(next(
            self.project.saves_dir.glob("%04d-*.json" % rec["revn"]))
            .read_text())
        self.assertIn("lint", disk_rec)

    def test_annotate_mapping_string_pattern_errors_not_crashes(self):
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch({
                "base_revn": 1, "artifact": "checkout-flow", "ops": [
                    {"op": "registry", "action": "annotate_mapping",
                     "pattern": "wireframe↔flow",
                     "note": "intentionally-divergent"}]})
        self.assertIn("pattern", str(cm.exception))

    def test_new_mapping_does_not_self_trip(self):
        """A mapping declared in the same batch as edits to its members
        must not fire a divergence tripwire against itself (regression:
        the registry-before-summary reorder made creation batches
        self-trip)."""
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "cart", "attrs": {"label": "Basket"}},
                {"op": "registry", "action": "add_mapping",
                 "concept": "checkout",
                 "elements": ["checkout-flow#cart", "checkout-flow#payment"]},
            ]})
        self.assertEqual(rec["tripwires"], [])
        # ...but a LATER divergent edit still trips
        rec2, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "cart", "attrs": {"label": "Trolley"}}]})
        self.assertTrue(rec2["tripwires"])

    def test_records_carry_origin_stamp(self):
        rec = self.store.records[1]
        self.assertIn("origin", rec)
        self.assertIn("pid", rec["origin"])


class TestDrawingVocabulary(Base):
    """Phase-3 vocabulary: decoration role, X-box image composites,
    declared containment, entity attribute rows, store kind, sharp
    hand-authored elbows (capability assessment gaps)."""

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_decoration_lines_skip_connector_lints(self):
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "line", "id": "wavy-1",
                                          "role": "decoration",
                                          "x": 60, "y": 320, "width": 200,
                                          "points": [[0, 0], [200, 0]]}}]})
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertFalse([w for w in lint["warnings"] if "wavy-1" in w])
        echo = canvas.intent_echo(
            [{"op": "add", "element": {"type": "line", "id": "wavy-1"}}],
            self.store.scenes["checkout-flow"])
        self.assertIn("line wavy-1", echo[0])
        self.assertNotIn("binds", echo[0])
        # decoration adds are recorded but low-signal (never the headline)
        adds = [f for f in rec["artifacts"]["checkout-flow"]["facts"]
                if f["fact"] == "added" and f["element"] == "wavy-1"]
        self.assertTrue(adds and adds[0].get("low_signal"))

    def test_image_kind_composes_xbox(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "hero",
                                          "kind": "image", "label": "hero",
                                          "x": 700, "y": 320, "width": 200,
                                          "height": 100}}]})
        by_id = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        self.assertIn("hero-x1", by_id)
        self.assertIn("hero-x2", by_id)
        self.assertEqual((by_id["hero-x1"]["customData"] or {})["role"],
                         "decoration")
        self.assertIn("hero-grp", by_id["hero"]["groupIds"])
        # moving the rect carries the strokes
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "hero", "attrs": {"x": 740, "y": 360}}]})
        by_id = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        self.assertEqual(by_id["hero-x1"]["x"], 740)
        self.assertEqual(by_id["hero-x2"]["y"],
                         360 + by_id["hero"]["height"])
        # deleting the rect removes the strokes
        self.store.apply_batch({
            "base_revn": 3, "artifact": "checkout-flow", "ops": [
                {"op": "del", "id": "hero"}]})
        ids = {e["id"] for e in self.store.scenes["checkout-flow"]}
        self.assertNotIn("hero-x1", ids)
        self.assertNotIn("hero-x2", ids)

    def test_entity_attributes_compose_and_fact(self):
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "shop-domain",
            "create": {"id": "shop-domain", "name": "Domain",
                       "type": "domain", "concept": "shop-dom"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "ent-portfolio",
                                          "kind": "entity",
                                          "label": "Portfolio",
                                          "attributes": ["cash, mandate",
                                                         "holds Positions"],
                                          "x": 60, "y": 60, "width": 180,
                                          "height": 64}}]})
        by_id = {e["id"]: e for e in self.store.scenes["shop-domain"]}
        self.assertIn("ent-portfolio-attr-1", by_id)
        self.assertEqual(by_id["ent-portfolio-label"]["text"], "Portfolio")
        facts = rec["artifacts"]["shop-domain"]["facts"]
        attr_facts = [f for f in facts if f["fact"] == "attribute_added"]
        self.assertEqual(len(attr_facts), 2)
        self.assertEqual(attr_facts[0]["entity"], "Portfolio")

    def test_parent_containment_suppresses_overlap_lint(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "shelf",
                                          "label": "Shelf", "x": 700,
                                          "y": 320, "width": 300,
                                          "height": 160}},
                {"op": "add", "element": {"type": "rectangle", "id": "card",
                                          "label": "Card", "parent": "shelf",
                                          "x": 720, "y": 360, "width": 120,
                                          "height": 80}}]})
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertFalse([w for w in lint["warnings"]
                          if "shelf" in w and "card" in w and "overlap" in w],
                         lint["warnings"])

    def test_store_kind_is_legal_terminal(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "cart", "attrs": {"kind": "source"}},
                {"op": "mod", "id": "checkout",
                 "attrs": {"kind": "transform"}},
                {"op": "mod", "id": "confirm", "attrs": {"kind": "store"}}]})
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertFalse([e for e in lint["errors"]
                          if "black hole" in e and "confirm" in e],
                         lint["errors"])

    def test_a_non_store_terminal_is_still_a_black_hole(self):
        """The firing pole for the exemption above.

        The rule-8 census (2026-08-16) found this error asserted absent
        once and asserted present nowhere in the file: a check that had
        stopped answering would have read as "store is a legal
        terminal" and nobody would have known the vocabulary rule was
        gone. One variable moves — `confirm` is a transform rather than
        a store — and the same `lint_layout` call the silence reads has
        to name it.
        """
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "cart", "attrs": {"kind": "source"}},
                {"op": "mod", "id": "checkout",
                 "attrs": {"kind": "transform"}},
                {"op": "mod", "id": "confirm",
                 "attrs": {"kind": "transform"}}]})
        lint = canvas.lint_layout(self.store.scenes["checkout-flow"])
        self.assertTrue([e for e in lint["errors"]
                         if "black hole" in e and "confirm" in e],
                        lint["errors"])

    def test_axis_aligned_hand_points_render_sharp(self):
        """A hand-authored path keeps its corners — beside a route that does not.

        The rule-8 census (2026-08-16) tagged this NEEDS-DESIGN: the
        silence says a four-point path the USER drew is left sharp, and
        an assignment that had stopped happening at all would say the
        same. It is not needs-design — the pole is one off-axis node
        away. `t-audit` is server-routed to a target below the rank, so
        the router elbows it to three points and `derived_roundness`
        must stamp it; `t1` carries the same three-segment shape and
        must not, because the author is the difference. Both read off
        the same loaded scene, so a dead assignment fails the pair.
        """
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "t1",
                 "attrs": {"points": [[0, 0], [60, 0], [60, 80],
                                      [120, 80]]}}]})
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "audit",
                                          "label": "Audit", "x": 300,
                                          "y": 420, "width": 140,
                                          "height": 60, "role": "node"}},
                {"op": "add", "element": {"type": "arrow", "id": "t-audit"},
                 "from": "cart", "to": "audit"}]})
        by_id = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        self.assertIsNone(by_id["t1"].get("roundness"))
        self.assertGreaterEqual(len(by_id["t-audit"]["points"]), 3,
                                "the routed arrow no longer elbows — this "
                                "pole's premise, not the rule, has moved")
        self.assertEqual(by_id["t-audit"].get("roundness"), {"type": 2})


class TestDemoParityChrome(Base):
    """Phase-5 server halves: user comment pins register from user
    saves; links_to / document op sugar."""

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_user_drawn_pin_registers_with_direction(self):
        els = self.scene()
        els.append({
            "id": "pin-user-1", "type": "text", "x": 650, "y": 100,
            "width": 26, "height": 26, "text": "❓", "originalText": "❓",
            "fontSize": 20, "fontFamily": 6, "textAlign": "center",
            "verticalAlign": "top", "lineHeight": 1.25, "containerId": None,
            "autoResize": True, "strokeColor": "#5b9dff",
            "customData": {"role": "pin", "question": "why no invoice step?",
                           "target": "payment", "status": "open",
                           "author": "user", "direction": "user"}})
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els}, base_revn=1)
        pin = next(p for p in self.store.registry["pins"]
                   if p["id"] == "pin-user-1")
        self.assertEqual(pin["direction"], "user")
        self.assertEqual(pin["question"], "why no invoice step?")
        debt = self.store.pin_debt()
        self.assertTrue(any(p["id"] == "pin-user-1" and
                            p["direction"] == "user" for p in debt))

    def test_links_to_and_document_sugar(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "nav-x",
                                          "kind": "nav", "label": "Admin",
                                          "links_to": "admin-wire",
                                          "document": "docs/brief.md",
                                          "x": 700, "y": 320, "width": 160,
                                          "height": 48}},
                {"op": "mod", "id": "cart",
                 "attrs": {"links_to": "checkout-wire"}}]})
        by_id = {e["id"]: e for e in self.store.scenes["checkout-flow"]}
        self.assertEqual(by_id["nav-x"]["link"], "artifact:admin-wire")
        self.assertEqual(by_id["nav-x"]["customData"]["document"],
                         "docs/brief.md")
        self.assertEqual(by_id["cart"]["link"], "artifact:checkout-wire")
        # link changes must replay (they're significant now)
        store = canvas.Store(self.project)
        # FIRING POLE: `TestCatchUp.test_out_of_session_edit_reconciles`
        # (rule-8 census close, 2026-08-16; killing `Store.catch_up`
        # fails 7 tests in 5 classes), plus the per-fixture control on
        # `FixtureReplayBase`.
        self.assertIsNone(store.catch_up())


class TestPowerFeatures(Base):
    """Phase-6 server halves: tidy, save bookmarks, image file blobs."""

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_tidy_snaps_and_commits(self):
        els = self.scene()
        errors = []
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "cart", "attrs": {"x": 43, "y": 121}}],
            errors)
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els}, base_revn=1)
        rec = self.store.tidy("checkout-flow")
        self.assertEqual(rec["author"], "agent")
        self.assertIn("tidy", rec["user_note"])
        cart = next(e for e in self.store.scenes["checkout-flow"]
                    if e["id"] == "cart")
        self.assertEqual(cart["x"] % 4, 0)
        self.assertEqual(cart["y"] % 4, 0)
        store = canvas.Store(self.project)
        # FIRING POLE: `TestCatchUp.test_out_of_session_edit_reconciles`
        # (rule-8 census close, 2026-08-16; killing `Store.catch_up`
        # fails 7 tests in 5 classes), plus the per-fixture control on
        # `FixtureReplayBase`.
        self.assertIsNone(store.catch_up())     # tidy replays cleanly

    def test_save_label_bookmark(self):
        rec = self.store.label_save(1, "v1 baseline")
        self.assertEqual(rec["label"], "v1 baseline")
        st = self.store.public_state()
        sv = next(s for s in st["saves"] if s["revn"] == 1)
        self.assertEqual(sv["label"], "v1 baseline")
        disk = json.loads(next(
            self.project.saves_dir.glob("0001-*.json")).read_text())
        self.assertEqual(disk["label"], "v1 baseline")

    def test_image_files_persist_roundtrip(self):
        els = self.scene()
        els.append({
            "id": "shot-1", "type": "image", "x": 700, "y": 300,
            "width": 200, "height": 120, "fileId": "f-abc",
            "status": "saved", "scale": [1, 1],
            "customData": {"role": "annotation", "author": "user"}})
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els}, base_revn=1,
                          new_files={"checkout-flow": {"f-abc": {
                              "mimeType": "image/png", "id": "f-abc",
                              "dataURL": "data:image/png;base64,AAAA"}}})
        store = canvas.Store(self.project)
        self.assertIn("f-abc", store.artifact_files["checkout-flow"])
        self.assertTrue(any(e["id"] == "shot-1"
                            for e in store.scenes["checkout-flow"]))

    def test_op_added_image_rejected(self):
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({
                "base_revn": 1, "artifact": "checkout-flow", "ops": [
                    {"op": "add", "element": {"type": "image",
                                              "id": "img-1"}}]})


class TestObstacleRouter(Base):
    """Phase-4 router: the Argus pipeline's 5-way ingest fan drew
    diagonals through foreign boxes and only the lint noticed
    (capability assessment). The router must now find clean orthogonal
    paths for exactly that shape."""

    def test_argus_ingest_fan_routes_clean(self):
        scorers = [("sentiment", 40), ("fundamentals", 200),
                   ("technical", 360), ("insider", 520),
                   ("contrarian", 680)]
        ops = [{"op": "add", "element": {
            "type": "rectangle", "id": "ingest", "label": "ingest",
            "kind": "transform", "x": 360, "y": 280,
            "width": 160, "height": 64}}]
        for name, y in scorers:
            ops.append({"op": "add", "element": {
                "type": "rectangle", "id": name, "label": name,
                "kind": "transform", "x": 680, "y": y,
                "width": 160, "height": 64}})
        for name, _ in scorers:
            ops.append({"op": "add",
                        "element": {"type": "arrow",
                                    "id": "t-ing-%s" % name},
                        "from": "ingest", "to": name})
        self.store.apply_batch({
            "base_revn": 0, "artifact": "fan",
            "create": {"id": "fan", "name": "Fan", "type": "flow",
                       "concept": "fan"},
            "ops": ops})
        lint = canvas.lint_layout(self.store.scenes["fan"])
        crossing = [w for w in lint["warnings"] if "passes through" in w]
        diagonal = [w for w in lint["warnings"] if "diagonally" in w]
        self.assertEqual(crossing, [])
        self.assertEqual(diagonal, [])
        # The firing pole, added by curator batch 24 (2026-08-16). The two
        # silences above are what the r4 gap list closed on, and the
        # mortality spike measured that they survive `passes_through_
        # foreign` being killed outright — a check that answers nothing
        # reports exactly this clean fan. Same `lint_layout` entry point,
        # one node parked on the straight run between A and Z, so the
        # emptiness above means the ROUTER did its job rather than that
        # nobody was watching.
        self.assertEqual(len(crossing_lint_control()), 1)

    def test_clean_pairs_still_route_straight(self):
        self.store.apply_batch(seed_flow_batch())
        t1 = next(e for e in self.store.scenes["checkout-flow"]
                  if e["id"] == "t1")
        self.assertEqual(len(t1["points"]), 2)   # aligned pair stays a line
        self.assertTrue(canvas.server_owns_geometry(t1))


class TestArgusAcceptance(Base):
    """v0.2 acceptance: re-run the capability assessment's Argus
    recreation with the new vocabulary and assert the original gap list
    is closed — zero phantom facts, no wrap newlines anywhere, clean fan
    routing, working frame rename, full pin/tripwire lifecycle, X-box
    and entity-attribute composites."""

    def _seed_argus(self):
        s = self.store
        s.apply_batch({  # dashboard wireframe with X-box charts + nested cards
            "base_revn": 0, "artifact": "dashboard-wireframe",
            "create": {"id": "dashboard-wireframe", "name": "Argus Dashboard",
                       "type": "wireframe", "concept": "dashboard",
                       "concept_name": "Dashboard"},
            "ops": [
                {"op": "add", "element": {"type": "frame",
                                          "id": "screen-dashboard",
                                          "label": "SCREEN — Argus Dashboard",
                                          "x": 40, "y": 40, "width": 720,
                                          "height": 520}},
                {"op": "add", "element": {"type": "rectangle", "id": "nav",
                                          "kind": "nav",
                                          "label": "ARGUS · Dashboard · Admin",
                                          "links_to": "admin-wireframe",
                                          "x": 60, "y": 60, "width": 680,
                                          "height": 48,
                                          "frameId": "screen-dashboard"}},
                {"op": "add", "element": {"type": "rectangle", "id": "kpi-alpha",
                                          "kind": "block",
                                          "label": "+3.1% — Alpha",
                                          "x": 60, "y": 120, "width": 160,
                                          "height": 64,
                                          "frameId": "screen-dashboard"}},
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "chart-equity",
                                          "kind": "image",
                                          "label": "equity curve",
                                          "x": 240, "y": 120, "width": 216,
                                          "height": 120,
                                          "frameId": "screen-dashboard"}},
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "reports-shelf",
                                          "kind": "block",
                                          "label": "Reports",
                                          "x": 60, "y": 260, "width": 680,
                                          "height": 160,
                                          "frameId": "screen-dashboard"}},
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "card-weekly",
                                          "kind": "button",
                                          "label": "Weekly Brief",
                                          "parent": "reports-shelf",
                                          "document": "docs/weekly.md",
                                          "x": 80, "y": 300, "width": 200,
                                          "height": 100,
                                          "frameId": "screen-dashboard"}},
                {"op": "add", "element": {"type": "text", "id": "note-kpi",
                                          "role": "annotation",
                                          "text": "KPI row first — the number "
                                                  "IS the product",
                                          "x": 800, "y": 120, "width": 200,
                                          "customData": {"annotates": "kpi-alpha"}}},
            ]})
        s.apply_batch({  # admin with a mystery frame + pin
            "base_revn": 1, "artifact": "admin-wireframe",
            "create": {"id": "admin-wireframe", "name": "Argus Admin",
                       "type": "wireframe", "concept": "admin",
                       "concept_name": "Admin Console"},
            "ops": [
                {"op": "add", "element": {"type": "frame", "id": "screen-mystery",
                                          "label": "?", "x": 720, "y": 300,
                                          "width": 180, "height": 140}},
                {"op": "add", "element": {"type": "rectangle", "id": "mystery-1",
                                          "x": 736, "y": 328, "width": 148,
                                          "height": 24,
                                          "frameId": "screen-mystery"}},
                {"op": "pin", "id": "pin-mystery", "target": "mystery-1",
                 "question": "what are these boxes?"},
            ]})
        ops = [{"op": "add", "element": {
            "type": "rectangle", "id": "ingest", "kind": "transform",
            "label": "ingest + normalize", "x": 360, "y": 280,
            "width": 160, "height": 64}}]
        for name, y in (("sentiment", 40), ("fundamentals", 200),
                        ("technical", 360), ("insider", 520),
                        ("contrarian", 680)):
            ops.append({"op": "add", "element": {
                "type": "rectangle", "id": name, "kind": "transform",
                "label": name, "x": 680, "y": y, "width": 160,
                "height": 64}})
            ops.append({"op": "add",
                        "element": {"type": "arrow", "id": "t-%s" % name},
                        "from": "ingest", "to": name})
        ops.append({"op": "add", "element": {
            "type": "rectangle", "id": "kpi-store", "kind": "store",
            "label": "KPI store", "roundness": {"type": 3},
            "x": 1000, "y": 200, "width": 160, "height": 64}})
        ops.append({"op": "add", "element": {"type": "arrow", "id": "t-out"},
                    "from": "sentiment", "to": "kpi-store"})
        ops.append({"op": "registry", "action": "add_mapping",
                    "concept": "dashboard",
                    "elements": ["dashboard-wireframe#kpi-alpha",
                                 "pipeline-flow#kpi-store"]})
        s.apply_batch({"base_revn": 2, "artifact": "pipeline-flow",
                       "create": {"id": "pipeline-flow",
                                  "name": "Data Pipelines", "type": "flow",
                                  "concept": "pipelines",
                                  "concept_name": "Data Pipelines"},
                       "ops": ops})
        s.apply_batch({  # domain with attribute rows
            "base_revn": 3, "artifact": "research-domain",
            "create": {"id": "research-domain", "name": "Research Domain",
                       "type": "domain", "concept": "research"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "ent-signal",
                                          "kind": "entity", "label": "Signal",
                                          "attributes": ["kind, confidence",
                                                         "informs Report"],
                                          "x": 60, "y": 60, "width": 180,
                                          "height": 64}},
            ]})

    def test_gap_list_closed(self):
        self._seed_argus()
        s = self.store
        # 1 — the ingest fan routes clean (was: diagonals through boxes).
        # The control is not optional: curator batch 24 measured that this
        # assertion passes with `passes_through_foreign` dead, so without
        # it gap-list item 1 closes on the lint saying nothing rather than
        # on the router routing.
        lint = canvas.lint_layout(s.scenes["pipeline-flow"])
        self.assertFalse([w for w in lint["warnings"]
                          if "passes through" in w or "diagonally" in w],
                         lint["warnings"])
        self.assertEqual(len(crossing_lint_control()), 1)
        # 2 — X-box composed; nested card lints as nesting, not collision
        dash = {e["id"]: e for e in s.scenes["dashboard-wireframe"]}
        self.assertIn("chart-equity-x1", dash)
        wl = canvas.lint_layout(s.scenes["dashboard-wireframe"])
        self.assertFalse([w for w in wl["warnings"]
                          if "reports-shelf" in w and "card-weekly" in w])
        # 3 — nav link + document sugar landed
        self.assertEqual(dash["nav"]["link"], "artifact:admin-wireframe")
        self.assertEqual(dash["card-weekly"]["customData"]["document"],
                         "docs/weekly.md")
        # 4 — entity attribute rows with term-exact label
        dom = {e["id"]: e for e in s.scenes["research-domain"]}
        self.assertIn("ent-signal-attr-1", dom)
        self.assertEqual(dom["ent-signal-label"]["text"], "Signal")
        # 5 — frame rename (was: floating bound text over the '?' frame)
        s.apply_batch({"base_revn": 4, "artifact": "admin-wireframe",
                       "ops": [{"op": "mod", "id": "screen-mystery",
                                "attrs": {"label": "Notifications"}}]})
        adm = {e["id"]: e for e in s.scenes["admin-wireframe"]}
        self.assertEqual(adm["screen-mystery"]["name"], "Notifications")
        self.assertFalse([e for e in adm.values()
                          if e.get("containerId") == "screen-mystery"])
        # 6 — pin lifecycle: answer → resolve deletes the glyph
        s.answer_pin("pin-mystery", "notification toggles")
        s.apply_batch({"base_revn": 5, "artifact": "admin-wireframe",
                       "ops": [{"op": "resolve_pin", "id": "pin-mystery"}]})
        self.assertNotIn("pin-mystery",
                         {e["id"] for e in s.scenes["admin-wireframe"]})
        self.assertEqual(next(p for p in s.registry["pins"]
                              if p["id"] == "pin-mystery")["status"],
                         "resolved")
        # 7 — tripwire on the mapped rename; propagate; resolve
        rec, _ = s.apply_batch({
            "base_revn": 6, "artifact": "dashboard-wireframe",
            "ops": [{"op": "mod", "id": "kpi-alpha",
                     "attrs": {"label": "+3.1% — Excess Return"}}]})
        self.assertTrue(rec["tripwires"])
        tw = next(t["id"] for t in s.registry["tripwires"]
                  if t["status"] == "open")
        s.answer_tripwire(tw, "Propagate to the sibling")
        s.apply_batch({
            "base_revn": 7, "artifact": "pipeline-flow",
            "ops": [{"op": "mod", "id": "kpi-store",
                     "attrs": {"label": "KPI store — excess return"}},
                    {"op": "registry", "action": "resolve_tripwire",
                     "id": tw}]})
        self.assertFalse([t for t in s.registry["tripwires"]
                          if t["status"] == "open"])
        # 8 — a re-measured user save stays silent and replay-convergent.
        # (New notes ship autoResize:false so the 745px blowout can't
        # happen anymore — TestClientRemeasure covers the legacy shape;
        # here we simulate the remaining churn: height re-measure.)
        els = [dict(e) for e in s.scenes["dashboard-wireframe"]]
        for e in els:
            if e["id"] == "note-kpi":
                e["height"] = e["height"] + 4
        rec3 = s.commit(author="user",
                        new_scenes={"dashboard-wireframe": els},
                        base_revn=8)
        facts = [f["fact"] for a in rec3["artifacts"].values()
                 for f in a["facts"]]
        self.assertNotIn("resized", facts)
        store2 = canvas.Store(self.project)
        self.assertIsNone(store2.catch_up())
        # 9 — no wrap newline ever reaches a fact or headline
        for r in s.records.values():
            self.assertNotIn("\\n", r["summary"]["headline"]
                             .replace("\\\\n", ""))
            for part in (r.get("artifacts") or {}).values():
                for f in part.get("facts", []):
                    for v in f.values():
                        if isinstance(v, str):
                            self.assertNotIn("\n", v)
        # 10 — standing nags exist and the pin debt is empty again
        self.assertEqual(s.pin_debt(), [])
        self.assertIsInstance(s.lint_debt(), dict)

    def test_v03_capability_round(self):
        """The v0.3 additions, end to end on the Argus shape: composed
        kinds, tooltips, budget override, tripwire visibility, fan focus,
        authored waypoints."""
        self._seed_argus()
        s = self.store
        # composed kinds land on the dashboard
        rec, _ = s.apply_batch({
            "base_revn": 4, "artifact": "dashboard-wireframe", "ops": [
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "kpi-var", "kind": "kpi",
                                          "label": "VaR", "value": "2.4%",
                                          "x": 480, "y": 120, "width": 160,
                                          "height": 80,
                                          "frameId": "screen-dashboard"}},
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "cb-macro",
                                          "kind": "checkbox",
                                          "label": "Macro calendar",
                                          "checked": False,
                                          "x": 60, "y": 440, "width": 200,
                                          "height": 28,
                                          "frameId": "screen-dashboard"}},
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "sl-conf", "kind": "slider",
                                          "label": "Signal confidence",
                                          "value": 70,
                                          "x": 280, "y": 432, "width": 240,
                                          "height": 44,
                                          "frameId": "screen-dashboard"}},
            ]})
        dash = {e["id"]: e for e in s.scenes["dashboard-wireframe"]}
        self.assertIn("kpi-var-value", dash)
        self.assertIn("cb-macro-box", dash)
        self.assertIn("sl-conf-thumb", dash)
        # value/checked mods fire typed facts with clean headlines
        rec, _ = s.apply_batch({
            "base_revn": 5, "artifact": "dashboard-wireframe", "ops": [
                {"op": "mod", "id": "kpi-var", "attrs": {"value": "2.1%"}},
                {"op": "mod", "id": "cb-macro", "attrs": {"checked": True}},
            ]})
        facts = [f["fact"] for f in
                 rec["artifacts"]["dashboard-wireframe"]["facts"]]
        self.assertIn("value_changed", facts)
        self.assertIn("state_toggled", facts)
        # tooltip lifecycle narrates
        rec, _ = s.apply_batch({
            "base_revn": 6, "artifact": "dashboard-wireframe", "ops": [
                {"op": "mod", "id": "kpi-var",
                 "attrs": {"tooltip": "95%, 1-day horizon."}}]})
        self.assertIn("tooltip_added",
                      [f["fact"] for f in
                       rec["artifacts"]["dashboard-wireframe"]["facts"]])
        # budget override: recorded intent, restated by the lint
        s.apply_batch({
            "base_revn": 7, "artifact": "pipeline-flow", "ops": [
                {"op": "registry", "action": "set_budget",
                 "artifact": "pipeline-flow", "nodes": 14, "arrows": 18,
                 "reason": "the five-way ingest fan IS this view"}]})
        lint = canvas.project_lint(
            self.project, s.scenes["pipeline-flow"], s.registry,
            artifact_type="flow", aid="pipeline-flow")
        self.assertTrue(any("budget override" in n and "ingest fan" in n
                            for n in lint["notes"]))
        # tripwire fired by a mapped rename is VISIBLE in the record
        rec, _ = s.apply_batch({
            "base_revn": 8, "artifact": "dashboard-wireframe",
            "ops": [{"op": "mod", "id": "kpi-alpha",
                     "attrs": {"label": "+3.1% — Excess Return"}}]})
        self.assertTrue(rec["tripwires"])
        self.assertTrue(rec["tripwires"][0]["id"].startswith("tw-"))
        self.assertIn("changed but its mapped sibling",
                      rec["tripwires"][0]["question"])
        # the 5-way ingest fan carries real focus values
        flow = {e["id"]: e for e in s.scenes["pipeline-flow"]}
        fan_focus = [flow["t-%s" % n]["startBinding"].get("focus", 0)
                     for n in ("sentiment", "fundamentals", "technical",
                               "insider", "contrarian")]
        self.assertTrue(any(abs(f) > 0.05 for f in fan_focus), fan_focus)
        # authored waypoints survive the batch post-passes AND later moves
        s.apply_batch({
            "base_revn": 9, "artifact": "pipeline-flow", "ops": [
                {"op": "mod", "id": "t-contrarian",
                 "attrs": {"points": [[0, 0], [60, 0], [60, 420],
                                      [320, 420]]}}]})
        s.apply_batch({
            "base_revn": 10, "artifact": "pipeline-flow", "ops": [
                {"op": "mod", "id": "ingest", "attrs": {"x": 320}}]})
        t = next(e for e in s.scenes["pipeline-flow"]
                 if e["id"] == "t-contrarian")
        self.assertEqual(t["customData"]["routed"], "authored")
        self.assertEqual(len(t["points"]), 4)


class TestV03ServerFixes(Base):
    """v0.3 capability-assessment bug fixes (WP1)."""

    def seed_entity(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "research-domain",
            "create": {"id": "research-domain", "name": "Research Domain",
                       "type": "domain", "concept": "domain",
                       "concept_name": "Research Domain"},
            "ops": [
                {"op": "add", "element": {"type": "rectangle",
                                          "id": "position",
                                          "label": "Position",
                                          "kind": "entity",
                                          "attributes": ["qty, cost basis",
                                                         "of an Instrument"],
                                          "x": 320, "y": 60, "width": 180,
                                          "height": 64, "role": "node"}},
            ]})

    def entity_and_label(self):
        els = self.store.scenes["research-domain"]
        ent = next(e for e in els if e["id"] == "position")
        lbl = next(e for e in els if e.get("containerId") == "position")
        return ent, lbl

    def test_entity_move_keeps_label_top_aligned(self):
        self.seed_entity()
        ent, lbl = self.entity_and_label()
        self.assertEqual(lbl["verticalAlign"], "top")
        top_offset = lbl["y"] - ent["y"]
        self.store.apply_batch({
            "base_revn": 1, "artifact": "research-domain",
            "ops": [{"op": "mod", "id": "position",
                     "attrs": {"x": 380, "y": 200}}]})
        ent, lbl = self.entity_and_label()
        self.assertEqual(lbl["verticalAlign"], "top")
        self.assertEqual(lbl["y"] - ent["y"], top_offset)

    def test_tidy_preserves_entity_label_alignment(self):
        self.seed_entity()
        self.store.tidy("research-domain")
        ent, lbl = self.entity_and_label()
        self.assertEqual(lbl["y"] - ent["y"], 6)

    def test_tidy_noop_does_not_commit(self):
        self.seed_entity()
        first = self.store.tidy("research-domain")
        head = self.store.head_revn()
        second = self.store.tidy("research-domain")
        self.assertTrue(second.get("noop"))
        self.assertEqual(self.store.head_revn(), head)
        self.assertEqual(second["summary"]["headline"],
                         "already tidy — nothing to change")
        # the first tidy is allowed to commit or noop; whichever it did,
        # a repeat must always be a noop
        self.assertLessEqual(first["revn"], head)

    def test_entity_drag_headlines_the_entity_not_its_rows(self):
        self.seed_entity()
        els = [dict(e) for e in self.store.scenes["research-domain"]]
        for e in els:
            if e["id"] == "position" or \
                    (e.get("customData") or {}).get("attr_of") == \
                    "position" or e.get("containerId") == "position":
                e["x"] = e["x"] + 60
        rec = self.store.commit(author="user",
                                new_scenes={"research-domain": els},
                                base_revn=self.store.head_revn())
        self.assertIn("Position", rec["summary"]["headline"])
        moved = [f for f in rec["artifacts"]["research-domain"]["facts"]
                 if f["fact"] == "moved"]
        row_moves = [f for f in moved if f["element"] != "position"]
        self.assertTrue(all(f.get("low_signal") for f in row_moves))

    def test_autogrow_overlap_warns(self):
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": "top-box",
                                      "label": "Short", "x": 0, "y": 0,
                                      "width": 120, "height": 40,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "low-box",
                                      "label": "Below", "x": 0, "y": 48,
                                      "width": 120, "height": 40,
                                      "role": "node"}},
        ], errors)
        self.assertEqual(errors, [])
        lint = canvas.lint_layout(els)
        self.assertFalse(any("grew to fit" in w for w in lint["warnings"]))
        els = canvas.apply_ops(els, [
            {"op": "mod", "id": "top-box",
             "attrs": {"label": "A very long label that has to wrap into "
                                "several lines to fit this narrow box"}}],
            errors)
        self.assertEqual(errors, [])
        top = next(e for e in els if e["id"] == "top-box")
        self.assertGreater((top.get("customData") or {})
                           .get("auto_grown", 0), 0)
        lint = canvas.lint_layout(els)
        self.assertTrue(any("grew to fit" in w for w in lint["warnings"]))


class TestComposedKindsAndTooltips(Base):
    """v0.3 WP2: kpi/checkbox/toggle/slider composites, verticalAlign,
    tooltips, authorship."""

    def seed_dashboard(self, extra_ops=()):
        ops = [
            {"op": "add", "element": {"type": "rectangle", "id": "kpi-alpha",
                                      "kind": "kpi", "label": "Alpha",
                                      "value": "+3.1%", "x": 40, "y": 40,
                                      "width": 160, "height": 80,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "cb-alerts",
                                      "kind": "checkbox",
                                      "label": "Enable alerts",
                                      "checked": True, "x": 40, "y": 140,
                                      "width": 200, "height": 28,
                                      "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "sl-risk",
                                      "kind": "slider",
                                      "label": "Risk tolerance",
                                      "value": 60, "x": 40, "y": 200,
                                      "width": 240, "height": 44,
                                      "role": "node"}},
        ]
        ops.extend(extra_ops)
        self.store.apply_batch({
            "base_revn": 0, "artifact": "admin-wireframe",
            "create": {"id": "admin-wireframe", "name": "Admin",
                       "type": "wireframe", "concept": "admin",
                       "concept_name": "Admin"},
            "ops": ops})

    def by_id(self):
        return {e["id"]: e for e in self.store.scenes["admin-wireframe"]}

    def test_kpi_composition(self):
        self.seed_dashboard()
        ix = self.by_id()
        kpi, val = ix["kpi-alpha"], ix["kpi-alpha-value"]
        self.assertEqual(kpi["customData"]["value"], "+3.1%")
        self.assertEqual(val["text"], "+3.1%")
        self.assertEqual(val["customData"]["role"], "decoration")
        self.assertEqual(val["customData"]["value_of"], "kpi-alpha")
        self.assertIn("kpi-alpha-grp", val["groupIds"])
        # semantic name is the bound label, pinned to the bottom band
        lbl = ix["kpi-alpha-label"]
        self.assertEqual(lbl["verticalAlign"], "bottom")
        self.assertGreater(lbl["y"], val["y"])

    def test_kpi_value_mod_keeps_id_and_fires_fact(self):
        self.seed_dashboard()
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "kpi-alpha",
                     "attrs": {"value": "+3.4%"}}]})
        ix = self.by_id()
        self.assertEqual(ix["kpi-alpha-value"]["text"], "+3.4%")
        facts = rec["artifacts"]["admin-wireframe"]["facts"]
        vc = next(f for f in facts if f["fact"] == "value_changed")
        self.assertEqual(vc["from"], "+3.1%")
        self.assertEqual(vc["to"], "+3.4%")
        self.assertIn("is now +3.4%", rec["summary"]["headline"])

    def test_kpi_rename_is_clean(self):
        self.seed_dashboard()
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "kpi-alpha",
                     "attrs": {"label": "Excess Return"}}]})
        facts = [f["fact"] for f in
                 rec["artifacts"]["admin-wireframe"]["facts"]]
        self.assertIn("label_renamed", facts)
        # the value never pollutes the rename
        self.assertNotIn("+3.1%", rec["summary"]["headline"])

    def test_checkbox_toggle_state(self):
        self.seed_dashboard()
        ix = self.by_id()
        self.assertTrue(ix["cb-alerts"]["customData"]["checked"])
        self.assertIn("cb-alerts-chk", ix)
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "cb-alerts",
                     "attrs": {"checked": False}}]})
        ix = self.by_id()
        self.assertNotIn("cb-alerts-chk", ix)
        facts = rec["artifacts"]["admin-wireframe"]["facts"]
        st = next(f for f in facts if f["fact"] == "state_toggled")
        self.assertFalse(st["to"])
        self.assertIn("switched off", rec["summary"]["headline"])

    def test_slider_value_moves_thumb(self):
        self.seed_dashboard()
        ix = self.by_id()
        x60 = ix["sl-risk-thumb"]["x"]
        self.store.apply_batch({
            "base_revn": 1, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "sl-risk",
                     "attrs": {"value": 90}}]})
        ix = self.by_id()
        self.assertGreater(ix["sl-risk-thumb"]["x"], x60)
        self.assertEqual(ix["sl-risk"]["customData"]["value"], 90.0)

    def test_value_checked_reject_wrong_kind(self):
        self.seed_dashboard()
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch({
                "base_revn": 1, "artifact": "admin-wireframe",
                "ops": [{"op": "mod", "id": "cb-alerts",
                         "attrs": {"value": "nope"}}]})
        self.assertIn("value", str(cm.exception))
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({
                "base_revn": 1, "artifact": "admin-wireframe",
                "ops": [{"op": "mod", "id": "kpi-alpha",
                         "attrs": {"checked": True}}]})

    def test_composite_moves_and_deletes_whole(self):
        self.seed_dashboard()
        self.store.apply_batch({
            "base_revn": 1, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "sl-risk",
                     "attrs": {"x": 400}}]})
        ix = self.by_id()
        self.assertEqual(ix["sl-risk-track"]["x"], 400 + 10)
        self.store.apply_batch({
            "base_revn": 2, "artifact": "admin-wireframe",
            "ops": [{"op": "del", "id": "kpi-alpha"}]})
        ix = self.by_id()
        self.assertNotIn("kpi-alpha-value", ix)
        self.assertNotIn("kpi-alpha-label", ix)

    def test_tooltip_lifecycle(self):
        self.seed_dashboard()
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "kpi-alpha",
                     "attrs": {"tooltip": "Trailing-quarter **excess "
                                          "return** vs benchmark."}}]})
        ix = self.by_id()
        self.assertIn("excess", ix["kpi-alpha"]["customData"]["tooltip"])
        facts = [f["fact"] for f in
                 rec["artifacts"]["admin-wireframe"]["facts"]]
        self.assertIn("tooltip_added", facts)
        self.assertIn("added a tooltip", rec["summary"]["headline"])
        rec2, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "kpi-alpha",
                     "attrs": {"tooltip": ""}}]})
        ix = self.by_id()
        self.assertNotIn("tooltip", ix["kpi-alpha"]["customData"])
        facts2 = [f["fact"] for f in
                  rec2["artifacts"]["admin-wireframe"]["facts"]]
        self.assertIn("tooltip_removed", facts2)

    def test_tooltip_on_add(self):
        self.seed_dashboard(extra_ops=[
            {"op": "add", "element": {"type": "rectangle", "id": "blk",
                                      "label": "Data sources",
                                      "tooltip": "One row per feed.",
                                      "x": 400, "y": 40, "width": 160,
                                      "height": 60, "role": "node"}}])
        ix = self.by_id()
        self.assertEqual(ix["blk"]["customData"]["tooltip"],
                         "One row per feed.")

    def test_vertical_align_titled_panel(self):
        self.seed_dashboard(extra_ops=[
            {"op": "add", "element": {"type": "rectangle", "id": "shelf",
                                      "label": "Reports",
                                      "verticalAlign": "top",
                                      "x": 400, "y": 200, "width": 300,
                                      "height": 160, "role": "node",
                                      "kind": "block"}}])
        ix = self.by_id()
        lbl = ix["shelf-label"]
        self.assertEqual(lbl["verticalAlign"], "top")
        self.assertEqual(lbl["y"], ix["shelf"]["y"] + 6)
        # mod verticalAlign flips it
        self.store.apply_batch({
            "base_revn": 1, "artifact": "admin-wireframe",
            "ops": [{"op": "mod", "id": "shelf",
                     "attrs": {"verticalAlign": "middle"}}]})
        ix = self.by_id()
        self.assertEqual(ix["shelf-label"]["verticalAlign"], "middle")
        bad = {"base_revn": 2, "artifact": "admin-wireframe",
               "ops": [{"op": "mod", "id": "shelf",
                        "attrs": {"verticalAlign": "sideways"}}]}
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch(bad)

    def test_agent_authorship_stamped_and_narrated(self):
        self.seed_dashboard(extra_ops=[
            {"op": "add", "element": {"type": "text", "id": "note-1",
                                      "text": "contrarian screen was my "
                                              "suggestion",
                                      "x": 400, "y": 400, "width": 200,
                                      "role": "annotation"}}])
        ix = self.by_id()
        self.assertEqual(ix["kpi-alpha"]["customData"]["author"], "agent")
        self.assertEqual(ix["note-1"]["customData"]["author"], "agent")
        # DARKENED by the user's 2026-08-17 contrast ruling (was #5c8a5f
        # at 3.89:1, under 1.4.3's 4.5). Still the agent-green half of
        # the v0.3 authorship language — the ruling required the colour
        # family to survive, which is why this pin reads a green and not
        # merely "something compliant".
        self.assertEqual(ix["note-1"]["strokeColor"], "#47704b")
        rec = self.store.records[1]
        anno = next(f for f in
                    rec["artifacts"]["admin-wireframe"]["facts"]
                    if f["fact"] == "annotated")
        self.assertEqual(anno["author"], "agent")
        self.assertIn("my note", canvas.headline_for(anno))

    def test_the_palette_it_mints_is_quiet_in_its_own_contrast_lint(self):
        """Both poles of the user's 2026-08-17 contrast ruling, minted.

        The ruling has three parts and they only work together, so they
        are pinned together on ONE scene that goes through the real
        paths: ops mint the elements, `project_lint` reads them.

        QUIET POLE. The exemption that used to hide composed furniture
        from `contrast_object` is gone, so a slider's track, a KPI value
        row and an agent annotation are now checked like anything else —
        and the whole scene must still be silent. That silence is now
        earned by the palette (#47704b at 5.55:1, `FURNITURE_INK`
        #8d877a at 3.48:1) instead of by a carve-out, which is the
        ruling's actual content: a REGRESSED DEFAULT would fire here,
        where under the exemption it could not.

        FIRING POLE, on the same furniture. The old grey is kept as a
        test scene and put back on the minted track — the "user's own
        bad recolour" case the ruling names — and it must be asked
        about. Without it the silence above is satisfied by a check that
        has stopped answering, which is exactly how the exemption's
        16-finding blind spot survived a full version.

        THE SUGGESTION IS VERIFIED, not just matched. The message's
        computed shade is parsed back out and its ratio measured with
        canvas's own arithmetic: advice that names a colour still under
        the floor would pass a substring assertion and send the agent
        round the loop twice.
        """
        self.seed_dashboard(extra_ops=[
            {"op": "add", "element": {"type": "text", "id": "note-1",
                                      "text": "minted at the default ink",
                                      "x": 400, "y": 400, "width": 200,
                                      "role": "annotation"}}])
        els = self.store.scenes["admin-wireframe"]
        ix = {e["id"]: e for e in els}
        track = ix["sl-risk-track"]
        self.assertEqual(track["customData"]["track_of"], "sl-risk")
        self.assertEqual(track["strokeColor"], canvas.FURNITURE_INK)

        def contrast_lines(scene):
            out = canvas.project_lint(self.project, scene,
                                      registry=self.store.registry,
                                      artifact_type="wireframe",
                                      aid="admin-wireframe")
            return [w for w in out["warnings"]
                    if "1.4.3 asks" in w or "1.4.11" in w]

        self.assertEqual(contrast_lines(els), [],
                         "the palette canvas.py mints is not quiet in its "
                         "own contrast lint")
        faint = [dict(e) for e in els]
        for e in faint:
            if e["id"] == "sl-risk-track":
                e["strokeColor"] = "#b8b2a5"     # the pre-ruling grey
        asked = [w for w in contrast_lines(faint) if "sl-risk-track" in w]
        self.assertEqual(len(asked), 1,
                         "faint furniture went unasked, so the silence "
                         "above is about a check that stopped answering "
                         "rather than about the palette: %r"
                         % contrast_lines(faint))
        self.assertIn("reads 2.06:1", asked[0])
        shade = re.search(r"nearest compliant shade of the same hue: "
                          r"(#[0-9a-f]{6})", asked[0])
        self.assertIsNotNone(shade, "no computed fix in %r" % asked[0])
        ratio = canvas.contrast_ratio(
            canvas.parse_hex_color(shade.group(1)),
            canvas.parse_hex_color(canvas.SVG_GROUND))
        self.assertGreaterEqual(
            ratio, canvas.CONTRAST_OBJECT,
            "the suggested shade %s reads %.2f:1, still under the floor "
            "the finding quoted" % (shade.group(1), ratio))


class TestDerivedRoundness(unittest.TestCase):
    """v0.9 WP4 stage 1: who the load-time roundness derivation reaches.

    `rebuild_bound_elements` re-derives a server-routed arrow's
    roundness at load, which is the only reason the sharp switch reaches
    artifacts frozen while elbows were curved. The reach is the whole
    question, so it is tested on bare element lists rather than through
    a project: what the derivation must NOT touch is not a property of
    any one fixture.
    """

    def test_a_hand_authored_path_keeps_its_shape_through_a_load(self):
        # The guard on the re-derivation. `mod points` marks a path
        # "authored" and the whole point of that mark is that no later
        # pass reshapes it — a blanket flatten at load would have made
        # the loader the one pass that ignores it.
        els = [{"id": "a1", "type": "arrow", "x": 0, "y": 0,
                "points": [[0, 0], [40, 30], [90, 30]],
                "roundness": {"type": 2},
                "customData": {"routed": "authored"}}]
        back = canvas.rebuild_bound_elements(els)
        self.assertEqual(back[0]["roundness"], {"type": 2})


class TestRouterV03(Base):
    """v0.3 WP3: authored waypoints, real focus, fan slide fallback,
    soft-obstacle routing."""

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_authored_points_survive_node_move_and_reload(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "t1",
                 "attrs": {"points": [[0, 0], [40, 0], [40, 80],
                                      [220, 80], [220, 0], [260, 0]]}}]})
        authored = next(e for e in self.store.scenes["checkout-flow"]
                        if e["id"] == "t1")
        pts_before = [list(p) for p in authored["points"]]
        # moving an endpoint node must NOT flatten the authored path
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "cart", "attrs": {"y": 400}}]})
        t1 = next(e for e in self.store.scenes["checkout-flow"]
                  if e["id"] == "t1")
        self.assertEqual([list(p) for p in t1["points"]], pts_before)
        self.assertEqual(t1["customData"]["routed"], "authored")
        # and the mark survives a persist round-trip
        store2 = canvas.Store(self.project)
        t1b = next(e for e in store2.scenes["checkout-flow"]
                   if e["id"] == "t1")
        self.assertEqual(t1b["customData"]["routed"], "authored")
        self.assertFalse(canvas.server_owns_geometry(t1b))

    def test_rewire_rereoutes_authored_arrow(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "t1",
                 "attrs": {"points": [[0, 0], [40, 0], [40, 80],
                                      [220, 80]]}}]})
        # a rewire is a NEW path request — the router takes back over
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "t1", "attrs": {"to": "payment"}}]})
        t1 = next(e for e in self.store.scenes["checkout-flow"]
                  if e["id"] == "t1")
        self.assertEqual(t1["endBinding"]["elementId"], "payment")
        self.assertNotEqual(t1["customData"]["routed"], "authored")
        self.assertTrue(canvas.server_owns_geometry(t1))

    def test_fanned_arrows_carry_nonzero_focus(self):
        # two more sources into checkout → 3 arrows on one side get fanned
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "s2",
                                          "label": "Saved carts", "x": 40,
                                          "y": 240, "width": 140,
                                          "height": 60, "role": "node"}},
                {"op": "add", "element": {"type": "rectangle", "id": "s3",
                                          "label": "Wishlist", "x": 40,
                                          "y": 360, "width": 140,
                                          "height": 60, "role": "node"}},
                {"op": "add", "element": {"type": "arrow", "id": "t-s2"},
                 "from": "s2", "to": "checkout"},
                {"op": "add", "element": {"type": "arrow", "id": "t-s3"},
                 "from": "s3", "to": "checkout"}]})
        els = self.store.scenes["checkout-flow"]
        into = [e for e in els if e.get("type") == "arrow" and
                (e.get("endBinding") or {}).get("elementId") == "checkout"]
        self.assertGreaterEqual(len(into), 3)
        ends = set()
        for a in into:
            p = a["points"][-1]
            ends.add((round(a["x"] + p[0]), round(a["y"] + p[1])))
        # attach points are actually distinct...
        self.assertEqual(len(ends), len(into))
        # ...and at least the outer fanned arrows carry nonzero focus so
        # the client doesn't snap them back to center
        focuses = [(a.get("endBinding") or {}).get("focus", 0)
                   for a in into]
        self.assertTrue(any(abs(f) > 0.05 for f in focuses))

    def test_router_avoids_label_corridor(self):
        # a wide annotation sits in the straight corridor between two
        # nodes; with a clean detour available, the router must not run
        # the arrow through the text
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "add", "element": {"type": "rectangle", "id": "a1",
                                          "label": "A", "x": 40, "y": 600,
                                          "width": 140, "height": 60,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "rectangle", "id": "b1",
                                          "label": "B", "x": 640, "y": 780,
                                          "width": 140, "height": 60,
                                          "role": "node"}},
                {"op": "add", "element": {"type": "text", "id": "big-note",
                                          "text": "a very wide annotation "
                                                  "sitting in the corridor",
                                          "x": 200, "y": 620, "width": 380,
                                          "role": "annotation"}},
                {"op": "add", "element": {"type": "arrow", "id": "t-ab"},
                 "from": "a1", "to": "b1"}]})
        els = self.store.scenes["checkout-flow"]
        arrow = next(e for e in els if e["id"] == "t-ab")
        note = next(e for e in els if e["id"] == "big-note")
        pts = arrow["points"]
        crossed = any(
            canvas._seg_hits_rect(arrow["x"] + p1[0], arrow["y"] + p1[1],
                                  arrow["x"] + p2[0], arrow["y"] + p2[1],
                                  note)
            for p1, p2 in zip(pts, pts[1:]))
        # The measurement's own liveness half. `crossed` is False either
        # because the router detoured or because `_seg_hits_rect` has
        # stopped seeing anything, and the rule-8 census (2026-08-16)
        # found no positive control for it in this class. A straight run
        # down the middle of the very same note must come back True.
        self.assertTrue(canvas._seg_hits_rect(
            note["x"] - 10, note["y"] + note["height"] / 2,
            note["x"] + note["width"] + 10, note["y"] + note["height"] / 2,
            note), "the corridor check no longer sees a run through it")
        self.assertFalse(crossed)


class TestFanAttachPoints(unittest.TestCase):
    """v0.9 task 51: what the auto-fan writes, on each shape and pitch.

    The writer's own regression for the two failures the fan owned. The
    catalogue pins the READER halves — `fanned_ellipse_foot_floats_in_
    the_void` asserts that an instrument reports a foot in an ellipse's
    void, and `curved_short_finals_escape_the_corridor` that the corridor
    reports two runs one lane apart — and neither can notice the fan
    ceasing to PRODUCE those pictures, because both hand-build their
    scenes. This class is that half, and it calls `fan_attach_points`
    directly rather than through a batch so a failure names the fan.
    """

    def _converging(self, shape: str, n: int = 3, side: float = 300,
                    gap: float = 100) -> list[dict]:
        """A scene of `n` routed arrows arriving on one node's left side.

        Args:
            shape: The target node's type.
            n: How many arrows converge.
            side: The target node's width and height.
            gap: Vertical spacing between the sources, which is what
                orders the fan's slots.

        Returns:
            The scene, sources first, ready for `fan_attach_points`.
        """
        els = [{"id": "N", "type": shape, "x": 200, "y": 100,
                "width": side, "height": side, "customData":
                {"role": "node"}}]
        for i in range(n):
            els.append({"id": "S%d" % i, "type": "rectangle", "x": 0,
                        "y": 100 + i * gap, "width": 60, "height": 40,
                        "customData": {"role": "node"}})
            els.append({"id": "a%d" % i, "type": "arrow", "x": 60,
                        "y": 120 + i * gap, "width": 140, "height": 0,
                        "points": [[0, 0], [140, 0]],
                        "startBinding": {"elementId": "S%d" % i,
                                         "focus": 0, "gap": 1},
                        "endBinding": {"elementId": "N", "focus": 0,
                                       "gap": 1},
                        "customData": {"role": "edge", "routed": True}})
        return els

    def _feet(self, els: list[dict]) -> list[tuple[float, float]]:
        """The absolute end point of every arrow in a fanned scene.

        Args:
            els: The scene, after `fan_attach_points`.

        Returns:
            One `(x, y)` per arrow, in scene order.
        """
        return [(e["x"] + e["points"][-1][0], e["y"] + e["points"][-1][1])
                for e in els if e.get("type") == "arrow"]

    def _elbowed(self, shape: str, n: int, w: float, h: float) -> list[dict]:
        """`n` ELBOWED arrows arriving on one node's left side.

        `_converging`'s straight arrows cannot state the lane claim: the
        fan turns each into a diagonal from its source to its foot, and
        a diagonal has no axis for `shared_corridors` to read — which is
        why that builder's fans looked clean while the M1 defect was
        live. These turn horizontal for their whole approach, so each
        arrow's final run IS its lane and the corridor instrument reads
        exactly the separation the fan produced.

        THE SOURCES ARE STAGGERED IN X on purpose. Stacked in one column
        their outgoing stubs share a lane by construction, and that
        corridor is the SCENE's, not the fan's — it survives any pitch
        and would make this assertion unpassable for a reason that has
        nothing to do with the feet.

        Args:
            shape: The target node's type.
            n: How many arrows converge.
            w: The target node's width.
            h: The target node's height, which is the side being fanned.

        Returns:
            The scene, ready for `fan_attach_points`.
        """
        els = [{"id": "N", "type": shape, "x": 400, "y": 100, "width": w,
                "height": h, "customData": {"role": "node"}}]
        for i in range(n):
            sx0, sy = i * 48, 60 + i * 160
            els.append({"id": "S%d" % i, "type": "rectangle", "x": sx0,
                        "y": 40 + i * 160, "width": 60, "height": 40,
                        "customData": {"role": "node"}})
            arr = {"id": "a%d" % i, "type": "arrow", "x": sx0 + 60,
                   "y": sy, "points": [[0, 0], [0, 100 + h / 2 - sy],
                                       [340 - sx0, 100 + h / 2 - sy]],
                   "startBinding": {"elementId": "S%d" % i, "focus": 0,
                                    "gap": 1},
                   "endBinding": {"elementId": "N", "focus": 0, "gap": 1},
                   "customData": {"role": "edge"}}
            arr["width"] = max(abs(p[0]) for p in arr["points"])
            arr["height"] = max(abs(p[1]) for p in arr["points"])
            arr["customData"]["routed"] = canvas._route_sig(arr)
            els.append(arr)
        return els

    def test_a_rectangle_fan_is_unchanged(self) -> None:
        """The even spread, on the box, exactly where it always was.

        The control for both changes at once: a rectangle's outline IS
        its box, so `shape_clip` returns the slot untouched, and a 300px
        side spreads at 75px, far past `FAN_LANE_PITCH`, so the widening
        arm never fires. If this moves, one of the two arms is firing
        where it was meant to be inert.
        """
        els = self._converging("rectangle")
        canvas.fan_attach_points(els)
        self.assertEqual(self._feet(els), [(200, 175), (200, 250),
                                           (200, 325)])

    def test_curved_shapes_get_their_feet_on_the_ink(self) -> None:
        """No fanned foot stands in a rhombus's or a circle's corner void.

        Measured as the instruments measure it, against the drawn
        outline: `_dist_to_ellipse`'s own scan says the old middle slot
        on this exact node missed by 17.71px and the rhombus's by 53.03.
        Asserted at the endpoint lint's floor rather than at zero
        because `_snap_geom` rounds every stored coordinate to a whole
        pixel, so a foot written onto a conic reads back up to ~1px off
        it.
        """
        for shape in ("ellipse", "diamond"):
            els = self._converging(shape)
            canvas.fan_attach_points(els)
            node = els[0]
            for fx, fy in self._feet(els):
                miss = canvas.shape_clearance(node, fx, fy)
                self.assertLess(
                    abs(miss), 2.0,
                    "%s: the fan left a foot at (%s, %s), %.2fpx off the "
                    "drawn outline" % (shape, fx, fy, miss))

    def test_a_cramped_side_gets_lanes_wider_than_a_corridor(self) -> None:
        """The fan's own output draws no corridor, on all three shapes.

        The corridor collision: the even spread puts three feet on a
        64px side exactly 16px apart, which clears the lint's 12px
        coincidence window and sits ON `instruments.shared_corridors`'
        16px tolerance — so the fan's own output was a merged stroke by
        the drawing's own measure.

        ASSERTED BY RUNNING THE CORRIDOR INSTRUMENT over the fanned
        scene, and across all three shapes, because the first version of
        this test did neither and that is exactly how it missed the M1
        defect. It measured a rectangle — the one shape where
        `shape_clip` is the identity and the projection cannot compress
        anything — while the shape test used a 300px node where the
        widening arm never fires. Two tests, two halves, and the product
        of the halves untested: on real corpus node sizes the projection
        ate the whole widening and the fan reported corridors against
        itself (ellipse 160x64 at 16px lanes, rhombus 200x80 at 13).

        The gap assertion stays beside it as the diagnostic. It is a
        strict inequality against the CORRIDOR's tolerance rather than
        against `FAN_LANE_PITCH`, so this pins the lane a reader sees
        and not the constant's value — and it is what tells you which
        way a failure went when the instrument speaks.
        """
        for shape, w, h in (("rectangle", 160, 64), ("ellipse", 160, 64),
                            ("diamond", 200, 80)):
            els = self._elbowed(shape, 3, w, h)
            canvas.fan_attach_points(els)
            ys = sorted(y for _x, y in self._feet(els))
            self.assertEqual(len(set(ys)), 3, (shape, ys))
            for lo, hi in zip(ys, ys[1:]):
                self.assertGreater(
                    hi - lo, 16,
                    "%s %dx%d: the fan left two feet %spx apart, inside "
                    "the lane the corridor reads as one stroke"
                    % (shape, w, h, hi - lo))
            hits = instruments.shared_corridors(els)
            self.assertEqual(
                hits, [],
                "%s %dx%d: the fan's own output reports %d corridor(s) — "
                "the fan exists to stop these arrows reading as one "
                "stroke and the drawing's own measure disagrees: %s"
                % (shape, w, h, len(hits), hits))
            # AND THE LIVE LINT, added by v0.9 TASK-LINTPROMOTE, which
            # is when the stakes changed. While the corridor reading
            # lived only in `tests/instruments` the cost of the fan
            # tripping it was a wrong number in a score vector. It is
            # now `lint_layout`'s `shared_lane`, so the same trip would
            # have the server hand the AGENT a defect to repair by hand
            # — the geometry the server itself had just produced, one
            # call earlier, on purpose. The instrument assertion above
            # cannot see that: it reads a different function, and the
            # day the two tolerances drift apart it is the lint that
            # speaks to a user.
            said = [w_ for w_ in canvas.lint_layout(els)["warnings"]
                    if "run together for" in w_]
            self.assertEqual(
                said, [],
                "%s %dx%d: the fan's own output is reported to the agent "
                "as a lane to repair — the server would be telling a "
                "user to undo what it just did: %s" % (shape, w, h, said))

    def test_a_side_too_short_for_the_pitch_still_spreads(self) -> None:
        """A 64px side cannot hold four lanes, and says so by trying.

        The widening is bounded by the side, and the bound is the same
        8px margin the slide fallback refuses to step outside. Four feet
        need `3 * FAN_LANE_PITCH` = 54px of clear outline; a 64px side
        offers ~48px of it on a rectangle and ~38 on a conic, so the
        lanes come out under the corridor's tolerance on EVERY shape
        including the one where the projection does nothing. That is
        what makes this the honest-shortfall pole rather than a residue
        of the curve fix — the rectangle is short for the same reason.

        What must NOT happen is the slots going out of order or off the
        end, which is what an unbounded widening does, so that is what
        is asserted. The shortfall's boundary moves with
        `FAN_LANE_PITCH` and `FAN_EDGE_MARGIN`; it is derived here
        rather than written down, so it stays true when they move.
        """
        for shape, w, h in (("rectangle", 160, 64), ("ellipse", 160, 64),
                            ("diamond", 200, 80)):
            els = self._elbowed(shape, 4, w, h)
            node = els[0]
            room = (canvas._fan_cross(node, "left", h - canvas.FAN_EDGE_MARGIN)
                    - canvas._fan_cross(node, "left", canvas.FAN_EDGE_MARGIN))
            self.assertLess(room, 3 * canvas.FAN_LANE_PITCH,
                            "%s: this side is no longer short, so it is no "
                            "longer this test's scene" % shape)
            canvas.fan_attach_points(els)
            ys = [y for _x, y in self._feet(els)]
            self.assertEqual(ys, sorted(ys), (shape, ys))
            lo, hi = node["y"] + canvas.FAN_EDGE_MARGIN, node["y"] + h
            self.assertTrue(all(lo <= y <= hi - canvas.FAN_EDGE_MARGIN
                                for y in ys), (shape, ys))
            # AND THE AGENT IS TOLD, which is what v0.9 TASK-LINTPROMOTE
            # changed about this scene (review M4). The shortfall above
            # was a wrong number in a score vector until the corridor
            # reading became `lint_layout`'s `shared_lane`; now the
            # server hands the agent a warning about geometry it just
            # produced and cannot improve on this side. That is the
            # known over-fire surface of the promoted lint — 53 of 120
            # swept fanned scenes, all cramped sides at >= 4 feet — and
            # it is pinned HERE, as a fact, rather than left as a
            # silence somebody rediscovers. Two directions to fail in:
            # the day the fan learns to spill onto another border this
            # goes quiet and this assertion says so, and the day the
            # lint stops reading the drawing it goes quiet for the wrong
            # reason and the sibling test above catches that instead.
            said = [w for w in canvas.lint_layout(els)["warnings"]
                    if "run together for" in w]
            self.assertTrue(
                said,
                "%s %dx%d: the fan fell short of its pitch here but the "
                "lint said nothing — either the fan improved (retire "
                "this) or the lane check stopped reading the drawing"
                % (shape, w, h))
            # and the advice it gives must be followable on a side that
            # has no room left: telling the agent to re-run the fan is
            # the "unfollowable advice" defect this repo has recorded
            # once already.
            self.assertIn("too short to hold them all", said[0])

    def test_the_fan_survives_a_second_pass(self) -> None:
        """Re-running the post-pass on its own output moves nothing.

        `fan_attach_points` runs over the whole scene after every batch,
        so it meets its own feet constantly. On a curved shape those
        feet are no longer on any bbox edge, and before `_edge_side`
        learned to read an outline they dropped out of the per-side
        ledger — the fan could be formed once and never maintained.
        """
        els = self._converging("ellipse")
        canvas.fan_attach_points(els)
        once = self._feet(els)
        canvas.fan_attach_points(els)
        self.assertEqual(once, self._feet(els))

    def test_fanned_feet_on_a_curved_shape_keep_a_nonzero_focus(self
                                                                ) -> None:
        """The v0.3 collapse, on the shapes it was never checked on.

        Focus 0 aims the client's re-render at the node's centre, so a
        fan whose outer feet carry it is a fan the first re-render
        undoes. `test_fanned_arrows_carry_nonzero_focus` asserts this
        for rectangles; a foot pulled onto an outline is on no bbox edge
        at all, which is the case that used to score 0.
        """
        els = self._converging("ellipse")
        canvas.fan_attach_points(els)
        focuses = [(e.get("endBinding") or {}).get("focus", 0)
                   for e in els if e.get("type") == "arrow"]
        self.assertEqual(len(focuses), 3)
        self.assertTrue(any(abs(f) > 0.05 for f in focuses), focuses)


class TestFocusRoundTrip(unittest.TestCase):
    """Does the focus we STORE redraw the foot where we PUT it?

    The fast neighbour of `frontends/wysiwyg-grilling/tests/e2e/
    focus.spec.ts`, which is the authoritative oracle and needs a
    browser. Both read the same specimen from `tests/focus_probe.py`;
    this one re-derives it through a transcription of the client's own
    `updateBoundPoint`, so the property is guarded on every backend run.

    WHY THIS TIER WAS BLIND. Nothing here moves a stored point: the
    defect lives entirely in what the CLIENT does with a stored value,
    and it is latent until a node changes. spike-focus-verify measured a
    fix that moved 75 focus values across 10 fixtures and changed what
    the browser draws — and the whole 1149-test suite stayed
    byte-identical. Re-derivation is the missing reading, and these are
    the tests that have it.
    """

    def setUp(self):
        self.recs = focus_probe.probe_manifest(focus_probe.probe_elements())
        self.by_name = {r["name"]: r for r in self.recs}

    def test_the_transcription_agrees_with_the_live_browser(self):
        """The calibration, and it is what keeps the rest honest.

        Every other assertion in this class asks a Python model where
        the client would draw a foot. A model that quietly drifted
        toward whatever `canvas.py` computes would agree with it
        forever and pin nothing, so the model is nailed here to three
        numbers READ OUT OF THE REAL BROWSER — pinned Excalidraw 0.18.1,
        the real server, a net-zero nudge — at the commit that landed
        this file:

            left   focus=-0.5  stored x=450   drawn x=541.406
            mid    focus=0     stored x=1000  drawn x=1000.000
            right  focus=+0.5  stored x=1550  drawn x=1458.594

        If this fires, distrust the transcription before the fix.
        """
        for name, focus, drawn_x in (("left", -0.5, 541.406),
                                     ("mid", 0.0, 1000.000),
                                     ("right", 0.5, 1458.594)):
            r = self.by_name[name]
            hx, hy, w, h = r["hub_box"]
            hub = {"type": "rectangle", "x": hx, "y": hy,
                   "width": w, "height": h}
            got = focus_probe.client_draws(hub, focus, r["gap"],
                                           r["adjacent"], r["foot"])
            self.assertAlmostEqual(got[0], drawn_x, places=3,
                                   msg="%s at focus %s" % (name, focus))

    def test_both_quarter_point_feet_redraw_where_they_were_stored(self):
        """The pin. Two feet, and the second one is the whole design.

        `determineFocusPoint` selects its corner with strict `>` tests,
        so a focus point exactly collinear with the adjacent point falls
        through to the WRONG corner — and for a perpendicular approach
        to a rectangle the correct magnitude `|d| / (w/2)` is exactly
        the scale that puts it there. Which side of the resulting 91px
        cliff the foot lands on is then decided by the SIGN alone, so a
        single left-hand specimen goes green under a fix that still
        draws the right-hand one 91px away. Both quarter points, or this
        proves nothing (spike-anchor §6a).
        """
        for r in self.recs:
            self.assertLess(focus_probe.tangential_slip(r), 0.5,
                            "%s: stored focus %r redraws the foot %.2fpx "
                            "along the side" % (r["name"], r["focus"],
                                                focus_probe.tangential_slip(r)))

    def test_the_midpoint_pole_stays_exactly_where_it_is(self):
        """Focus 0 through a side midpoint is exact today; keep it.

        A perpendicular approach through a side midpoint aims at the
        centre, and the centre is on the target line, so 0 is not an
        approximation there — it is the answer. Any inverse solve that
        returns something else for this row has broken the one case the
        wire format was designed for (spike-ports §2).
        """
        mid = self.by_name["mid"]
        self.assertEqual(mid["focus"], 0)
        self.assertAlmostEqual(focus_probe.tangential_slip(mid), 0.0,
                               places=6)

    def test_the_selection_cliff_is_real_and_one_foot_cannot_see_it(self):
        """The neighbour that fires: the WRONG fix, on the same specimen.

        `determineFocusDistance` — the client's own forward function,
        and the obvious thing to reach for — returns exactly the value
        sitting on `determineFocusPoint`'s selection boundary: +0.5 for
        the left foot and -0.5 for the right. Asserted here as a
        measurement rather than described, because it is the reason the
        pin above carries two feet: on the left that value happens to
        land right, on the right it is 91px out. A pin that only ever
        asserts a success cannot tell a fix from a coincidence.
        """
        left, right = self.by_name["left"], self.by_name["right"]
        for r, wrong in ((left, 0.5), (right, -0.5)):
            hx, hy, w, h = r["hub_box"]
            hub = {"type": "rectangle", "x": hx, "y": hy,
                   "width": w, "height": h}
            got = focus_probe.client_draws(hub, wrong, r["gap"],
                                           r["adjacent"], r["foot"])
            slip = abs(got[0] - r["foot"][0])
            if r is left:
                self.assertLess(slip, 0.5, "left foot, client_focus")
            else:
                self.assertGreater(slip, 50.0,
                                   "the right-hand foot is the one that "
                                   "separates an inverse solve from the "
                                   "client's forward formula")

    def test_solve_focus_expresses_a_perpendicular_foot_anywhere(self):
        """The property, swept — not one specimen but a side's worth.

        Four sides, five box shapes, thirteen offsets along each side,
        approached square from 240px out. `solve_focus` has to hand back
        a focus the client redraws within half a pixel of, at every one.
        Rectangles only, and `roundness: null`: those are the shapes the
        transcription models EXACTLY (`deconstructRectanguloidElement`
        offsets a rounded corner diagonally, which this does not), so a
        0.5px assertion is honest on them and would not be on a rounded
        box or a conic. The browser referee carries no such caveat.
        """
        worst, worst_at = 0.0, None
        for w, h in ((200.0, 64.0), (120.0, 120.0), (300.0, 40.0),
                     (64.0, 200.0), (48.0, 48.0)):
            hub = {"type": "rectangle", "x": 400.0, "y": 400.0,
                   "width": w, "height": h}
            for side in ("top", "right", "bottom", "left"):
                horiz = side in ("top", "bottom")
                span = w if horiz else h
                for k in range(1, 14):
                    off = span * k / 14.0
                    if horiz:
                        fx = 400.0 + off
                        fy = 400.0 + (h if side == "bottom" else 0.0)
                        ax, ay = fx, fy + (240.0 if side == "bottom"
                                           else -240.0)
                    else:
                        fy = 400.0 + off
                        fx = 400.0 + (w if side == "right" else 0.0)
                        ax, ay = fx + (240.0 if side == "right"
                                       else -240.0), fy
                    f = canvas.solve_focus(hub, ax, ay, fx, fy, 6)
                    got = focus_probe.client_draws(hub, f, 6, (ax, ay),
                                                   (fx, fy))
                    slip = abs(got[1] - fy) if horiz is False \
                        else abs(got[0] - fx)
                    if slip > worst:
                        worst, worst_at = slip, (w, h, side, off, f)
        self.assertLess(worst, 0.5,
                        "worst tangential slip %.3fpx at %r" % (worst,
                                                                worst_at))

    def test_solve_focus_expresses_an_oblique_approach_too(self):
        """The class `binding_focus` cannot see at all.

        `binding_focus` reads the FOOT and nothing else, so every arrow
        arriving at the same point gets the same focus however it got
        there — but Excalidraw's focus is a property of the whole
        approach ray. Swept over approach angles either side of square,
        the inverse solve has to keep landing the foot; a foot-only
        formula cannot, by construction.
        """
        hub = {"type": "rectangle", "x": 400.0, "y": 400.0,
               "width": 200.0, "height": 64.0}
        worst, worst_at = 0.0, None
        for off in (40.0, 70.0, 100.0, 130.0, 160.0):
            fx, fy = 400.0 + off, 464.0
            for lean in (-160.0, -80.0, -20.0, 20.0, 80.0, 160.0):
                ax, ay = fx + lean, fy + 240.0
                f = canvas.solve_focus(hub, ax, ay, fx, fy, 6)
                got = focus_probe.client_draws(hub, f, 6, (ax, ay), (fx, fy))
                slip = abs(got[0] - fx)
                if slip > worst:
                    worst, worst_at = slip, (off, lean, f)
        self.assertLess(worst, 0.5,
                        "worst tangential slip %.3fpx at %r" % (worst,
                                                                worst_at))

    # -- the residual acceptance check, pinned in BOTH directions --------
    #
    # `solve_focus` is a minimiser, and a minimiser always has a winner.
    # Without a ceiling on how bad the winner may be it degrades to a
    # CONFIDENT wrong answer — a clamped ±0.9 — exactly where it has
    # least right to be confident. The two tests below are a pair on
    # purpose: one that the guard fires, one that it does not. Either
    # alone is satisfiable by a constant.

    def test_an_inexpressible_geometry_gets_0_and_not_a_confident_0_9(self):
        """When no focus can express it, say 0 — don't guess loudly.

        Two shapes of inexpressible, both from the v0.9 TASK-FOCUS
        review. `_edge_side` names a real side for either, so the
        `side is None` guard never sees them and the search runs anyway:

        - **`along`** — the final leg runs edge-on to the side it lands
          on, so no ray from the adjacent point can enter through that
          side at any focus.
        - **a corner foot on a steep lean** — the reviewer's probe, and
          the one that shows the cost: the minimiser answered ±0.9 and
          the client drew the foot 62.5px away.

        The `along` row also pins the property that made this a defect
        rather than a wart: it is NOT slip-invariant. With the adjacent
        point inside the box footprint the clamped answer drew the foot
        90px out where 0 draws it exactly.
        """
        hub = {"type": "rectangle", "x": 400.0, "y": 400.0,
               "width": 200.0, "height": 64.0}
        for foot, adj, why in (
                ((500.0, 464.0), (300.0, 464.0), "along, adjacent outside"),
                ((500.0, 464.0), (450.0, 464.0), "along, adjacent inside"),
                ((440.0, 464.0), (560.0, 464.0), "along, other direction"),
                ((595.0, 464.0), (195.0, 524.0), "corner foot, 400px lean"),
                ((405.0, 464.0), (805.0, 524.0), "corner foot, mirrored")):
            f = canvas.solve_focus(hub, adj[0], adj[1], foot[0], foot[1], 6)
            self.assertEqual(f, 0, "%s: expressible by nothing, so the "
                                   "answer is 0 — got %r" % (why, f))
        # and the case that proves 0 is the SAFE answer, not just the
        # humble one: the clamped guess drew this foot 90px out
        f_wrong = -0.9
        drawn = focus_probe.client_draws(hub, f_wrong, 6, (450.0, 464.0),
                                         (500.0, 464.0))
        self.assertGreater(abs(drawn[0] - 500.0), 50.0)
        drawn0 = focus_probe.client_draws(hub, 0, 6, (450.0, 464.0),
                                          (500.0, 464.0))
        self.assertLess(abs(drawn0[0] - 500.0), 0.5)

    def test_the_guard_does_not_fire_on_a_foot_a_focus_can_reach(self):
        """The other direction — including at the clamp.

        A rejection rule that rejects everything would pass the test
        above and be a total regression, so this pins what must still
        come back NON-zero. The last row is the one that matters: a foot
        5px from the corner approached SQUARE needs |focus| past the
        ±0.9 the wire format allows, so it is legitimately clamped and
        still draws within 4px. Refusing a clamp you can honour is the
        failure mode that cost 76 good answers when this was judged on
        the aim's ANGLE instead of on where the aim LANDS.
        """
        hub = {"type": "rectangle", "x": 400.0, "y": 400.0,
               "width": 200.0, "height": 64.0}
        for foot, adj, why in (
                ((450.0, 464.0), (450.0, 704.0), "left quarter point"),
                ((550.0, 464.0), (550.0, 704.0), "right quarter point"),
                ((470.0, 464.0), (550.0, 704.0), "oblique, 80px lean"),
                ((440.0, 464.0), (600.0, 704.0), "oblique, 160px lean"),
                ((595.0, 464.0), (595.0, 704.0), "near-corner, square on")):
            f = canvas.solve_focus(hub, adj[0], adj[1], foot[0], foot[1], 6)
            self.assertNotEqual(f, 0, "%s: this foot IS expressible and the "
                                      "guard swallowed it" % why)
            drawn = focus_probe.client_draws(hub, f, 6, adj, foot)
            self.assertLess(abs(drawn[0] - foot[0]), 4.0,
                            "%s: accepted focus %r draws %.2fpx out"
                            % (why, f, abs(drawn[0] - foot[0])))
        # the midpoint pole is 0 for the RIGHT reason (it is exact), not
        # because the guard rejected it — asserted next to its neighbours
        # so the two reasons for a 0 cannot be confused
        self.assertEqual(canvas.solve_focus(hub, 500.0, 704.0, 500.0,
                                            464.0, 6), 0)
        mid = focus_probe.client_draws(hub, 0, 6, (500.0, 704.0),
                                       (500.0, 464.0))
        self.assertAlmostEqual(mid[0], 500.0, places=6)

    def test_a_corner_foot_is_solved_on_the_side_it_is_approached_through(
            self) -> None:
        """A foot in a corner stands on two sides. Solve for both.

        `_edge_side` has to pick ONE, and its bbox tests run in a fixed
        order, so a foot 2.4px in from the corner of a 48×48 node —
        sitting exactly ON the top edge — comes back `"left"`. Solving
        for a left side that an arrow arriving from directly above never
        enters through cannot produce a right answer, and the answer it
        did produce was then thrown away for being bad: 76 good answers
        lost across an 18,720-foot sweep, this one among them
        (v0.9 TASK-FOCUS re-review, NEW-2).

        The specimen is PERPENDICULAR, which is what makes it matter —
        that is the geometry `route_arrow` actually emits, not a corner
        case in the informal sense.
        """
        node = {"type": "rectangle", "x": 400.0, "y": 400.0,
                "width": 48.0, "height": 48.0}
        foot = (402.4, 400.0)
        # the mechanism: one side named, two sides available
        self.assertEqual(canvas._edge_side(node, *foot), "left")
        self.assertEqual(canvas._edge_sides(node, *foot), ["left", "top"])
        # the side the arrow cannot enter through scores INFINITE and so
        # loses to focus 0; the side it does enter through scores 0
        self.assertEqual(
            canvas._solve_focus_on(node, "left", 402.4, 160.0, *foot,
                                   gap=6)[1], float("inf"))
        self.assertLess(
            canvas._solve_focus_on(node, "top", 402.4, 160.0, *foot,
                                   gap=6)[1], 0.5)
        for adj, why in (((402.4, 160.0), "perpendicular"),
                         ((492.4, 160.0), "leaning 90px"),
                         ((312.4, 160.0), "leaning -90px")):
            f = canvas.solve_focus(node, adj[0], adj[1], foot[0], foot[1], 6)
            self.assertNotEqual(f, 0, "%s: a reachable corner foot" % why)
            drawn = focus_probe.client_draws(node, f, 6, adj, foot)
            self.assertLess(abs(drawn[0] - foot[0]), 4.0,
                            "%s: focus %r draws %.3fpx out"
                            % (why, f, abs(drawn[0] - foot[0])))
        # and the number that says why this was worth fixing: the same
        # foot, approached square, is drawn 19.145px away by focus 0
        zero = focus_probe.client_draws(node, 0, 6, (402.4, 160.0), foot)
        self.assertAlmostEqual(abs(zero[0] - foot[0]), 19.145, places=3)

    def test_a_conics_box_corner_is_given_a_side_name_on_purpose(
            self) -> None:
        """`_edge_side` names a side 32.75px off an ellipse's ink. ALLOWED.

        THIS PIN RECORDS A JUDGEMENT, not a defect, and it exists so the
        judgement is never made twice. `_edge_side`'s four bbox arms run
        FIRST and unconditionally, so a conic gets a side name for any
        point near its BOX — and the box corner of a 200x100 ellipse is
        32.75px from the drawn outline, out in blank canvas. The conic
        arm underneath (`shape_clip` within `eps`) is only ever reached
        by points the box arms miss. On a rectangle the same corner is
        the ink, 0.00px, which is the comparison that makes the conic
        reading visibly wrong rather than merely loose.

        A GUARD FOR THIS WAS BUILT AND REJECTED ON MEASUREMENT
        (TASK-NARRATION, 2026-08-17). Refusing a side name to points off
        a conic's ink refuses 9 of 346 corpus endpoints, and 5 of those
        are fanned feet standing 4-8px off an ellipse where the side
        sentence is both true and wanted — the narration wants to say
        "arrives on the left" about a foot the fan slid, and eps=2.5 is
        tighter than the fan's own spread. The case the guard defends —
        an endpoint genuinely stranded off the ink — is already reported
        LOUDLY by `endpoint_gap` as an ERROR in the same response, so the
        guard would trade five true sentences for a warning the reader
        already has.

        So this is `allow`, in the sweep's sense: a real inaccuracy, no
        picture consequence, reason written down. What is pinned is the
        MEASUREMENT, so that a future reader who rediscovers the
        wrongness finds the arithmetic that made it tolerable, and so
        that a fix which changes the trade has to change this test and
        say why. Owner if it is ever re-opened: TASK-FOCUS-FOLLOWUP,
        which owns conic calibration.
        """
        ell = {"type": "ellipse", "x": 0.0, "y": 0.0,
               "width": 200.0, "height": 100.0}
        corner = (0.0, 0.0)

        def off_the_ink(node: dict, px: float, py: float) -> float:
            """How far `(px, py)` stands from `node`'s drawn outline."""
            cx = node["x"] + node["width"] / 2.0
            cy = node["y"] + node["height"] / 2.0
            dx, dy = px - cx, py - cy
            span = canvas.shape_clip(node, cx, cy, dx, dy)
            return abs(1.0 - span[1]) * (dx * dx + dy * dy) ** 0.5

        self.assertEqual(canvas._edge_side(ell, *corner), "left")
        self.assertAlmostEqual(off_the_ink(ell, *corner), 32.75, places=2)
        # the same point on a rectangle IS the outline — the bbox arms are
        # exactly right there, which is why they run first at all
        rect = dict(ell, type="rectangle")
        self.assertEqual(canvas._edge_side(rect, *corner), "left")
        self.assertAlmostEqual(off_the_ink(rect, *corner), 0.0, places=6)
        # and the side midpoint, the one place a conic touches its box,
        # is named for the right reason rather than by accident
        self.assertEqual(canvas._edge_side(ell, 0.0, 50.0), "left")
        self.assertAlmostEqual(off_the_ink(ell, 0.0, 50.0), 0.0, places=6)

    def test_an_unreachable_side_is_refused_by_arithmetic_not_by_a_constant(
            self) -> None:
        """WHICH mechanism refuses an impossible foot — pinned on purpose.

        `FOCUS_LAND_TOL` looks like the thing rejecting the `along` and
        corner-lean classes and it is not. `_drawn_offset` scores a side
        the aim cannot reach as INFINITE — parallel to it, aimed away
        from it, or crossing its line past its end — and focus 0 is the
        search's own baseline, so an unreachable geometry loses on the
        arithmetic before any ceiling is consulted.

        That distinction is worth a test because the constant's comment
        was WRONG ONCE ALREADY: it claimed a provenance (the endpoint
        lint's 14px) it never had, and a reader who acted on that claim
        would have raised the ceiling. So the property is asserted at
        the endpoint lint's own number — the rejections must survive it,
        because they were never the ceiling's doing.
        """
        hub = {"type": "rectangle", "x": 400.0, "y": 400.0,
               "width": 200.0, "height": 64.0}
        cases = (((500.0, 464.0), (300.0, 464.0), "along, adjacent outside"),
                 ((500.0, 464.0), (450.0, 464.0), "along, adjacent inside"),
                 ((595.0, 464.0), (195.0, 524.0), "corner foot, 400px lean"))
        for foot, adj, why in cases:
            best = min(canvas._solve_focus_on(hub, side, adj[0], adj[1],
                                              foot[0], foot[1], 6)[1]
                       for side in canvas._edge_sides(hub, *foot))
            self.assertEqual(best, float("inf"),
                             "%s: should be unreachable on every side" % why)
        # raised to the endpoint lint's tolerance — the number the false
        # comment invited — and every one of them still comes back 0
        with mock.patch.object(canvas, "FOCUS_LAND_TOL", 14.0):
            for foot, adj, why in cases:
                self.assertEqual(
                    canvas.solve_focus(hub, adj[0], adj[1], foot[0],
                                       foot[1], 6), 0,
                    "%s: rejection must not depend on the constant" % why)


class TestPairKindAndTheSentencesItProduces(unittest.TestCase):
    """`_pair_kind`'s five kinds, and the reading each one hands the agent.

    v0.9 TASK-LINTPROMOTE fix round 1 (review M2). The classifier shipped
    with three of its five kinds exercised only incidentally — `converge`
    by the canonical corpus pin, `unrelated` by `argus-domain`, `fan` as
    suppression — and NOTHING asserting `swapped` or `chain` in either
    lint. The reviewer replaced all four of those message branches with
    garbage and the full suite stayed green.

    That is worst for `swapped`, which is the bidirectional check's OWN
    STATED PREMISE and the branch a hand probe had already caught being
    wrong once: the first cut ended its ternary chain on the `swapped`
    reading, so every kind the gate let through was told it was a
    2-cycle. That defect was found by hand, fixed by rewrite, and the
    rewrite shipped untested — so it could return in silence. These are
    the tests that would have caught it without the probe.

    The truth table is asserted directly and the sentences through the
    real lint, because the two can rot apart: a classifier that is right
    while the branch selecting on it is wrong is exactly what shipped.
    """

    def _arrow(self, aid: str, pts: list[list[float]], x: float, y: float,
               src: str | None, dst: str | None) -> dict:
        """Build one bound arrow.

        Args:
            aid: Element id.
            pts: Points, relative to `x`/`y`.
            x: Element origin x.
            y: Element origin y.
            src: `startBinding` target id, or None to leave it unbound.
            dst: `endBinding` target id, or None to leave it unbound.

        Returns:
            The arrow element dict.
        """
        return {"id": aid, "type": "arrow", "x": x, "y": y, "points": pts,
                "width": 0, "height": 0, "customData": {"role": "edge"},
                "startBinding": ({"elementId": src, "focus": 0, "gap": 1}
                                 if src else None),
                "endBinding": ({"elementId": dst, "focus": 0, "gap": 1}
                               if dst else None)}

    def _node(self, nid: str, x: float, y: float) -> dict:
        """Build one 80x40 node box.

        Args:
            nid: Element id.
            x: Left edge.
            y: Top edge.

        Returns:
            The node element dict.
        """
        return {"id": nid, "type": "rectangle", "x": x, "y": y,
                "width": 80, "height": 40, "customData": {"role": "node"}}

    def test_the_five_kinds_are_what_the_bindings_say(self) -> None:
        """The truth table, asserted directly rather than through a lint."""
        a = self._arrow("a", [[0, 0], [10, 0]], 0, 0, "A", "B")
        cases = [("swapped", "B", "A"), ("chain", "B", "C"),
                 ("fan", "A", "C"), ("converge", "C", "B"),
                 ("unrelated", "C", "D")]
        for want, src, dst in cases:
            b = self._arrow("b", [[0, 0], [10, 0]], 0, 0, src, dst)
            self.assertEqual(canvas._pair_kind(a, b), want,
                             "A->B against %s->%s" % (src, dst))

    def test_chain_wins_over_fan_when_both_could_be_read(self) -> None:
        """`A->B` and `B->A` share BOTH ends; the reversal is the reading.

        The ordering inside `_pair_kind` is load-bearing and invisible
        from any single case: a 2-cycle satisfies the `chain` test as
        well, and answering `chain` there would hand the agent "they
        continue each other" for a pair that is a reversal.
        """
        a = self._arrow("a", [[0, 0], [10, 0]], 0, 0, "A", "B")
        b = self._arrow("b", [[0, 0], [10, 0]], 0, 0, "B", "A")
        self.assertEqual(canvas._pair_kind(a, b), "swapped")

    def test_a_self_loop_is_never_a_fan_or_a_convergence(self) -> None:
        """The carve-out, from both sides of the pair.

        A loop shares its node at both ends, so "same end" says nothing
        about a common relation set. Reading it as a fan would have
        suppressed the corpus's only live bidirectional finding.
        """
        loop = self._arrow("loop", [[0, 0], [10, 0]], 0, 0, "N", "N")
        out = self._arrow("out", [[0, 0], [10, 0]], 0, 0, "N", "Z")
        into = self._arrow("in", [[0, 0], [10, 0]], 0, 0, "Z", "N")
        self.assertEqual(canvas._pair_kind(loop, out), "unrelated")
        self.assertEqual(canvas._pair_kind(out, loop), "unrelated")
        self.assertEqual(canvas._pair_kind(loop, into), "unrelated")

    def test_unbound_ends_match_nothing(self) -> None:
        """Two arrows binding nothing are unrelated, not a fan of Nones."""
        a = self._arrow("a", [[0, 0], [10, 0]], 0, 0, None, None)
        b = self._arrow("b", [[0, 0], [10, 0]], 0, 0, None, None)
        self.assertEqual(canvas._pair_kind(a, b), "unrelated")
        c = self._arrow("c", [[0, 0], [10, 0]], 0, 0, "A", None)
        d = self._arrow("d", [[0, 0], [10, 0]], 0, 0, "B", None)
        self.assertEqual(canvas._pair_kind(c, d), "unrelated")

    def _swapped_scene(self) -> list[dict]:
        """`A->B` and `B->A` drawn 6px apart on one line, fully overlapping.

        Inside `LANE_TOL` and inside `BIDI_HEAD_TOL`, so ONE scene fires
        both lints and both `swapped` branches are asserted off the same
        geometry.

        Returns:
            The four-element scene.
        """
        return [self._node("A", 100, 100), self._node("B", 600, 100),
                self._arrow("e1", [[0, 0], [420, 0]], 180, 110, "A", "B"),
                self._arrow("e2", [[0, 0], [-420, 0]], 600, 116, "B", "A")]

    def _chain_scene(self) -> list[dict]:
        """`A->N` and `N->Z`, the second doubling back over the first.

        `e1`'s final leg runs east into N at y=110; `e2` leaves N, goes
        over the top and comes back west into Z at y=116, so the two
        finals lie 6px apart, point opposite ways and overlap 100px.

        Returns:
            The five-element scene.
        """
        return [self._node("A", 100, 300), self._node("N", 600, 100),
                self._node("Z", 100, 100),
                self._arrow("e1", [[0, 0], [220, 0], [220, -210], [420, -210]],
                            180, 320, "A", "N"),
                self._arrow("e2", [[0, 0], [0, -40], [-140, -40], [-140, 16],
                                   [-460, 16]], 640, 100, "N", "Z")]

    def test_a_reversal_is_named_as_one_by_both_lints(self) -> None:
        """`swapped`: both messages say each arrow is the other's reverse.

        The bidirectional message is selected on "end on the same" and
        not on "bidirectional edge": the LANE message's own `swapped`
        clause contains that phrase too, so the looser filter matched
        both and this test failed on its own selector before it could
        say anything about the product.
        """
        li = canvas.lint_layout(self._swapped_scene())
        lane = [w for w in li["warnings"] if "run together for" in w]
        bidi = [w for w in li["warnings"] if "end on the same" in w]
        self.assertEqual(len(lane), 1, li["warnings"])
        self.assertEqual(len(bidi), 1, li["warnings"])
        self.assertIn("each is the other's reverse", lane[0])
        self.assertIn("each is the other's reverse", bidi[0])
        # ...and not the sentence belonging to any other kind
        for other in ("they leave", "they arrive at", "continue each other",
                      "share no node"):
            self.assertNotIn(other, lane[0])

    def test_the_waive_keys_name_the_artifact_and_not_an_arrow(self) -> None:
        """Every new lint's waive key carries the `aid` it was given.

        THE REGRESSION PIN FOR A SCOPE BUG, and the scene is chosen to
        reproduce it: these two arrows share an attach point at BOTH
        ends, so `lint_layout`'s shared-attach block runs first and its
        `for aid in (id1, id2)` loop — function-scoped, like every Python
        loop variable — used to overwrite the `aid` PARAMETER for the
        rest of the pass. The three promoted lints are the first code to
        read `aid` after that point, so their keys came out
        `lane:e2:e1:e2` instead of `lane:myflow:e1:e2`.

        That is a live defect and not a cosmetic one: the key is what an
        agent records a waive under, and it depended on which arrow pair
        happened to share an attach point earlier in the same drawing.
        Asserted for all three lints from one scene, because one shared
        cause would break them all and one fix repairs them all.
        """
        els = self._swapped_scene()
        els.append({"id": "A-label", "type": "text", "x": 110, "y": 110,
                    "width": 40, "height": 20, "text": "A",
                    "originalText": "A", "opacity": 100, "containerId": "A"})
        for e in els:
            if e["id"] == "A":
                e["opacity"] = 0
        li = canvas.lint_layout(els, aid="myflow")
        keys = re.findall(r"key: '([^']+)'", " ".join(li["warnings"]))
        self.assertEqual(
            sorted(keys),
            ["bidi:myflow:e1:e2", "ghost:myflow:a-label", "lane:myflow:e1:e2"],
            li["warnings"])
        # the shared-attach block DID run — otherwise this proves nothing
        self.assertTrue([w for w in li["warnings"]
                         if "share an attach point" in w], li["warnings"])

    def test_a_chain_is_named_as_one_by_both_lints(self) -> None:
        """`chain`: both messages name the node the merged stroke deletes.

        The lane sentence has to carry the NODE, because that is the
        whole finding — a step in the flow stops reading as a step — and
        a branch that merely said "they continue each other" would leave
        the agent hunting for which node.
        """
        li = canvas.lint_layout(self._chain_scene())
        lane = [w for w in li["warnings"] if "run together for" in w]
        bidi = [w for w in li["warnings"] if "bidirectional edge" in w]
        self.assertEqual(len(lane), 1, li["warnings"])
        self.assertEqual(len(bidi), 1, li["warnings"])
        self.assertIn("continue each other through N", lane[0])
        self.assertIn("stops being a step", lane[0])
        self.assertIn("continue each other through a shared node", bidi[0])
        for other in ("each is the other's reverse", "they leave",
                      "they arrive at"):
            self.assertNotIn(other, lane[0])


class TestOrphanLabelHonoursIntent(unittest.TestCase):
    """`orphan_label`'s two escape hatches, and that they are needed.

    v0.9 TASK-LINTPROMOTE fix round 1 (review m1). The check shipped with
    NEITHER channel: it walks `els` directly rather than the pre-filtered
    `arrows` list its two siblings share, so it inherited nothing, and it
    offered no waive key. `canvas.py` states that `role: decoration`
    elements are "invisible to every lint" — this was the first
    exception. Of the three promoted checks it is the one with zero
    corpus witnesses, which makes it the one that most needs a way to say
    "I meant that", not the least.
    """

    def _ghost(self, **over: object) -> list[dict]:
        """A dimmed box carrying a fully-inked bound label.

        Args:
            **over: Fields merged onto the LABEL element.

        Returns:
            The two-element scene.
        """
        label = {"id": "g1-label", "type": "text", "x": 10, "y": 20,
                 "width": 100, "height": 20, "text": "GHOST",
                 "originalText": "GHOST", "opacity": 100,
                 "containerId": "g1"}
        label.update(over)
        return [{"id": "g1", "type": "rectangle", "x": 0, "y": 0,
                 "width": 120, "height": 60, "opacity": 0,
                 "customData": {"role": "node"}}, label]

    def _fired(self, els, **kw):
        """The orphan-label warnings for one scene.

        Args:
            els: The scene.
            **kw: Passed through to `lint_layout`.

        Returns:
            Matching warning strings.
        """
        return [w for w in canvas.lint_layout(els, **kw)["warnings"]
                if "does not inherit" in w]

    def test_the_firing_pole_still_fires(self) -> None:
        """Without either channel engaged, the ghost is still reported.

        The pole both tests below need: a filter that silenced everything
        would pass them and mean nothing.
        """
        said = self._fired(self._ghost())
        self.assertEqual(len(said), 1, said)
        self.assertIn("g1-label", said[0])
        self.assertIn("100%", said[0])
        self.assertIn("0%", said[0])

    def test_a_decoration_caption_is_not_this_defect(self) -> None:
        """Furniture is exempt, from either side of the pairing.

        A decoration label over an authored box and an authored label
        over a decoration box are both somebody's deliberate composite.
        The defect is a caption for a THING, left inked after the thing
        was hidden — neither of these is that.
        """
        self.assertEqual(
            self._fired(self._ghost(customData={"role": "decoration"})), [])
        els = self._ghost()
        els[0]["customData"] = {"role": "decoration"}
        self.assertEqual(self._fired(els), [])

    def test_a_recorded_waive_silences_it(self) -> None:
        """The key the message offers is the key that works.

        Asserted by READING THE KEY OUT OF THE MESSAGE rather than by
        rebuilding it here: a check that told the agent one key and
        honoured another would pass a hand-written expectation and fail
        the only user who ever tried it.
        """
        said = self._fired(self._ghost(), aid="flow")
        self.assertEqual(len(said), 1, said)
        key = re.search(r"key: '([^']+)'", said[0]).group(1)
        self.assertEqual(key, "ghost:flow:g1-label")
        self.assertEqual(
            self._fired(self._ghost(), aid="flow", waives={key: {}}), [])

    def test_a_waive_for_another_artifact_does_not_leak(self) -> None:
        """The key is artifact-scoped, so one waive does not silence two."""
        self.assertEqual(
            len(self._fired(self._ghost(), aid="other",
                            waives={"ghost:flow:g1-label": {}})), 1)


class TestBudgetsV03(Base):
    """v0.3 WP4: type-aware budgets, set_budget override, frame-containment
    overlap exemption."""

    def seed_domain(self, n_entities):
        ops = []
        for i in range(n_entities):
            ops.append({"op": "add", "element": {
                "type": "rectangle", "id": "e%d" % i,
                "label": "Entity%d" % i, "kind": "entity",
                "x": 60 + (i % 4) * 320, "y": 60 + (i // 4) * 200,
                "width": 180, "height": 64, "role": "node"}})
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dom",
            "create": {"id": "dom", "name": "Dom", "type": "domain",
                       "concept": "dom", "concept_name": "Dom"},
            "ops": ops})

    def test_domain_entity_budget_is_8(self):
        self.seed_domain(9)
        lint = canvas.project_lint(self.project, self.store.scenes["dom"],
                                   self.store.registry,
                                   artifact_type="domain", aid="dom")
        self.assertTrue(any("9 entities (budget: 8)" in n
                            for n in lint["notes"]))

    def test_set_budget_override_and_clear(self):
        self.seed_domain(9)
        with self.assertRaises(canvas.BatchError):  # reason required
            self.store.apply_batch({
                "base_revn": 1, "artifact": "dom", "ops": [
                    {"op": "registry", "action": "set_budget",
                     "artifact": "dom", "nodes": 12}]})
        with self.assertRaises(canvas.BatchError):  # unknown artifact
            self.store.apply_batch({
                "base_revn": 1, "artifact": "dom", "ops": [
                    {"op": "registry", "action": "set_budget",
                     "artifact": "nope", "nodes": 12, "reason": "x"}]})
        self.store.apply_batch({
            "base_revn": 1, "artifact": "dom", "ops": [
                {"op": "registry", "action": "set_budget",
                 "artifact": "dom", "nodes": 12,
                 "reason": "the nine-entity core set is the point"}]})
        self.assertEqual(self.store.registry["budgets"]["dom"]["nodes"], 12)
        lint = canvas.project_lint(self.project, self.store.scenes["dom"],
                                   self.store.registry,
                                   artifact_type="domain", aid="dom")
        self.assertFalse(any("(budget: 8)" in n for n in lint["notes"]))
        self.assertTrue(any("budget override" in n and "nine-entity" in n
                            for n in lint["notes"]))
        self.store.apply_batch({
            "base_revn": 2, "artifact": "dom", "ops": [
                {"op": "registry", "action": "set_budget",
                 "artifact": "dom", "clear": True}]})
        self.assertNotIn("dom", self.store.registry["budgets"])

    def test_budgets_survive_registry_repair(self):
        self.seed_domain(1)
        reg = dict(self.store.registry)
        reg.pop("budgets", None)
        fixed, _issues = canvas.validate_registry(reg)
        self.assertEqual(fixed["budgets"], {})

    def test_frame_containment_exempts_full_overlap_only(self):
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "frame", "id": "scr",
                                      "label": "Screen", "x": 0, "y": 0,
                                      "width": 600, "height": 400}},
            {"op": "add", "element": {"type": "rectangle", "id": "shelf",
                                      "label": "", "x": 20, "y": 20,
                                      "width": 400, "height": 200,
                                      "frameId": "scr", "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "card",
                                      "label": "Card", "x": 40, "y": 60,
                                      "width": 160, "height": 100,
                                      "frameId": "scr", "role": "node"}},
            {"op": "add", "element": {"type": "rectangle", "id": "half",
                                      "label": "Half", "x": 300, "y": 100,
                                      "width": 200, "height": 160,
                                      "frameId": "scr", "role": "node"}},
        ], errors)
        self.assertEqual(errors, [])
        lint = canvas.lint_layout(els)
        warns = " | ".join(lint["warnings"])
        # card fully inside shelf, same frame → exempt
        self.assertNotIn("'Card'", warns.replace("card", "'Card'")
                         if "card" in warns else warns)
        self.assertFalse(any("card" in w and "shelf" in w
                             for w in lint["warnings"]))
        # half only partially overlaps shelf → still lints
        self.assertTrue(any("half" in w and "shelf" in w
                            for w in lint["warnings"]))


class TestUiUxLintsV04(Base):
    """v0.4 WP1: reading order, form/WCAG-shaped wireframe lints, and the
    waive registry op (rulings Q29+, docs/refinement/v0.4-progress.md)."""

    def wf(self, ops, artifact_type="wireframe"):
        errors = []
        els = canvas.apply_ops([], ops, errors)
        self.assertEqual(errors, [])
        return canvas.lint_layout(els, artifact_type=artifact_type)

    @staticmethod
    def frame(fid="scr", name="Screen", x=0, y=0, w=360, h=480):
        return {"op": "add", "element": {
            "type": "frame", "id": fid, "label": name, "x": x, "y": y,
            "width": w, "height": h}}

    @staticmethod
    def block(bid, label, kind, x, y, w=320, h=40, fid="scr"):
        return {"op": "add", "element": {
            "type": "rectangle", "id": bid, "label": label, "kind": kind,
            "x": x, "y": y, "width": w, "height": h, "frameId": fid,
            "role": "node"}}

    def test_frame_reading_order_rows_merge_at_6px(self):
        errors = []
        els = canvas.apply_ops([], [
            self.frame(),
            self.block("b", "Bravo", "block", 200, 64),
            self.block("a", "Alpha", "block", 20, 60, w=160),
            self.block("c", "Charlie", "block", 20, 120),
        ], errors)
        self.assertEqual(errors, [])
        order = [e["id"] for e in canvas.frame_reading_order(els, "scr")]
        # a and b differ by 4px in y → one row, ordered by x; c below
        self.assertEqual(order, ["a", "b", "c"])

    def test_reading_order_facts_first_draw_and_change(self):
        rec, _ = self.store.apply_batch({
            "base_revn": 0, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "wf", "concept_name": "WF"},
            "ops": [self.frame(),
                    self.block("title", "Title", "block", 20, 40),
                    self.block("cta", "Continue", "button", 20, 120)]})
        facts = rec["artifacts"]["wf"]["facts"]
        ro = [f for f in facts if f["fact"] == "reading_order_set"]
        self.assertEqual(len(ro), 1)
        self.assertEqual(ro[0]["order"], ["Title", "Continue"])
        self.assertIn("reads: Title / Continue",
                      canvas.headline_for(ro[0]))
        # swap: move the button above the title → order change narrates
        rec2, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "wf", "ops": [
                {"op": "mod", "id": "cta", "attrs": {"y": 8}}]})
        facts2 = rec2["artifacts"]["wf"]["facts"]
        ch = [f for f in facts2 if f["fact"] == "reading_order_changed"]
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0]["order"], ["Continue", "Title"])
        self.assertEqual(ch[0]["was"], ["Title", "Continue"])
        # a nudge inside the row tolerance is not an order change
        rec3, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "wf", "ops": [
                {"op": "mod", "id": "cta", "attrs": {"y": 12}}]})
        self.assertFalse([f for f in rec3["artifacts"]["wf"]["facts"]
                          if f["fact"] == "reading_order_changed"])

    def test_submit_before_inputs_warns(self):
        lint = self.wf([
            self.frame(),
            self.block("go", "Continue", "button", 20, 60, w=120),
            self.block("name", "Name", "input", 20, 140),
            self.block("mail", "Email", "input", 20, 200)])
        self.assertTrue(any("precedes 2 of the inputs" in w
                            for w in lint["warnings"]))
        lint2 = self.wf([
            self.frame(),
            self.block("name", "Name", "input", 20, 60),
            self.block("mail", "Email", "input", 20, 120),
            self.block("go", "Continue", "button", 20, 200, w=120)])
        self.assertFalse(any("precedes" in w for w in lint2["warnings"]))

    def test_input_label_lints(self):
        lint = self.wf([
            self.frame(),
            {"op": "add", "element": {
                "type": "rectangle", "id": "bare", "kind": "input",
                "x": 20, "y": 60, "width": 320, "height": 40,
                "frameId": "scr", "role": "node"}},
            self.block("req", "Name *", "input", 20, 120)])
        self.assertTrue(any("has no label" in w for w in lint["warnings"]))
        self.assertTrue(any("(optional)" in w and "asterisk" in w
                            for w in lint["warnings"]))

    def test_uniform_width_note_needs_three(self):
        rows = [self.frame(),
                self.block("a", "Day", "input", 20, 60),
                self.block("b", "Month", "input", 20, 120),
                self.block("c", "Year", "input", 20, 180)]
        lint = self.wf(rows)
        self.assertTrue(any("every answer the same length" in n
                            for n in lint["notes"]))
        rows[3] = self.block("c", "Year", "input", 20, 180, w=96)
        lint2 = self.wf(rows)
        self.assertFalse(any("every answer the same length" in n
                             for n in lint2["notes"]))

    def test_sticky_bar_note_needs_inputs(self):
        base = [self.frame(),
                self.block("bar", "Total £42", "sticky-bar", 0, 440,
                           w=360, h=40)]
        lint = self.wf(base)
        self.assertFalse(any("2.4.11" in n for n in lint["notes"]))
        lint2 = self.wf([*base, self.block("pc", "Postcode", "input",
                                           20, 200)])
        self.assertTrue(any("2.4.11" in n and "under the bar" in n
                            for n in lint2["notes"]))

    def test_help_presence_and_slot_drift(self):
        two = [self.frame("s1", "One", x=0),
               self.frame("s2", "Two", x=400),
               self.block("h1", "Help", "help", 300, 8, w=48, h=24,
                          fid="s1")]
        lint = self.wf(two)
        self.assertTrue(any("help lives on 1 of 2 screens" in n
                            for n in lint["notes"]))
        both = [*two, self.block("h2", "Help", "help", 420, 440, w=48,
                                 h=24, fid="s2")]
        lint2 = self.wf(both)
        self.assertTrue(any("help drifts across screens" in n
                            for n in lint2["notes"]))

    def test_target_size_note(self):
        # undersized checkbox whose 24px spacing circle clips a button
        lint = self.wf([
            self.frame(),
            self.block("chk", "T&C", "checkbox", 20, 200, w=20, h=20),
            self.block("go", "Continue", "button", 40, 200, w=120, h=40)])
        self.assertTrue(any("closer than a thumb" in n
                            for n in lint["notes"]))
        # two undersized targets: circles intersect at <24px centres
        lint2 = self.wf([
            self.frame(),
            self.block("c1", "A", "checkbox", 20, 200, w=20, h=20),
            self.block("c2", "B", "checkbox", 40, 200, w=20, h=20)])
        self.assertTrue(any("closer than a thumb" in n
                            for n in lint2["notes"]))
        # clear spacing → silent
        lint3 = self.wf([
            self.frame(),
            self.block("chk", "T&C", "checkbox", 20, 200, w=20, h=20),
            self.block("go", "Continue", "button", 80, 260, w=120, h=40)])
        self.assertFalse(any("closer than a thumb" in n
                             for n in lint3["notes"]))

    def test_duplicate_frame_titles_note(self):
        lint = self.wf([self.frame("s1", "Details", x=0),
                        self.frame("s2", "Details", x=400)])
        self.assertTrue(any("share the title" in n for n in lint["notes"]))

    def test_progress_indicator_note_and_waive(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "wf", "concept_name": "WF"},
            "ops": [self.frame(),
                    self.block("bar", "Step 2 of 4", "block", 20, 8,
                               h=24)]})
        lint = canvas.project_lint(self.project, self.store.scenes["wf"],
                                   self.store.registry,
                                   artifact_type="wireframe", aid="wf")
        self.assertTrue(any("progress indicator" in n
                            for n in lint["notes"]))
        with self.assertRaises(canvas.BatchError):  # reason required
            self.store.apply_batch({
                "base_revn": 1, "artifact": "wf", "ops": [
                    {"op": "registry", "action": "waive",
                     "key": "q25:wf"}]})
        self.store.apply_batch({
            "base_revn": 1, "artifact": "wf", "ops": [
                {"op": "registry", "action": "waive", "key": "q25:wf",
                 "reason": "the user needs the steps — regulated flow"}]})
        lint2 = canvas.project_lint(self.project, self.store.scenes["wf"],
                                    self.store.registry,
                                    artifact_type="wireframe", aid="wf")
        self.assertFalse(any("progress indicator" in n
                             for n in lint2["notes"]))

    def test_task_list_statuses_exempt_from_q25(self):
        lint = self.wf([
            self.frame(),
            self.block("t1", "Your details", "block", 20, 60),
            {"op": "add", "element": {
                "type": "text", "id": "st1", "text": "In progress",
                "x": 260, "y": 68, "frameId": "scr"}}])
        self.assertFalse(any("progress indicator" in n
                             for n in lint["notes"]))

    def test_waives_survive_registry_repair(self):
        reg = json.loads(json.dumps(canvas.DEFAULT_REGISTRY))
        reg.pop("waives", None)
        fixed, _issues = canvas.validate_registry(reg)
        self.assertEqual(fixed["waives"], {})


class TestCrossLintV04(Base):
    """v0.4 WP2: cross-artifact lints — 3.3.7 redundant entry, 3.2.4
    consistent identification (mapped only), Q12 whose-word check."""

    def seed(self, btn_a="Continue", btn_b="Next", flow_b="n2"):
        """Two-screen wireframe + two-step flow + mappings."""
        self.store.apply_batch({
            "base_revn": 0, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "checkout", "concept_name": "Checkout"},
            "ops": [
                {"op": "add", "element": {
                    "type": "frame", "id": "scr-a", "label": "Address",
                    "x": 0, "y": 0, "width": 360, "height": 480}},
                {"op": "add", "element": {
                    "type": "frame", "id": "scr-b", "label": "Delivery",
                    "x": 400, "y": 0, "width": 360, "height": 480}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "pc-a", "label": "Postcode",
                    "kind": "input", "x": 20, "y": 100, "width": 320,
                    "height": 40, "frameId": "scr-a", "role": "node"}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "pc-b", "label": "Postcode",
                    "kind": "input", "x": 420, "y": 100, "width": 320,
                    "height": 40, "frameId": "scr-b", "role": "node"}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "btn-a", "label": btn_a,
                    "kind": "button", "x": 20, "y": 400, "width": 120,
                    "height": 40, "frameId": "scr-a", "role": "node"}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "btn-b", "label": btn_b,
                    "kind": "button", "x": 420, "y": 400, "width": 120,
                    "height": 40, "frameId": "scr-b", "role": "node"}}]})
        self.store.apply_batch({
            "base_revn": 1, "artifact": "fl",
            "create": {"id": "fl", "name": "FL", "type": "flow",
                       "concept": "checkout"},
            "ops": [
                {"op": "add", "element": {
                    "type": "rectangle", "id": "n1", "label": "Address",
                    "x": 60, "y": 60, "width": 160, "height": 64,
                    "role": "node"}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "n2", "label": "Delivery",
                    "x": 380, "y": 60, "width": 160, "height": 64,
                    "role": "node"}},
                {"op": "add", "element": {"type": "arrow", "id": "t12"},
                 "from": "n1", "to": "n2"},
                {"op": "registry", "action": "add_mapping",
                 "concept": "checkout", "elements": ["wf#scr-a", "fl#n1"]},
                {"op": "registry", "action": "add_mapping",
                 "concept": "checkout", "elements": ["wf#scr-b", "fl#n2"]},
                {"op": "registry", "action": "add_mapping",
                 "concept": "checkout",
                 "elements": ["wf#btn-a", "fl#n2"]},
                {"op": "registry", "action": "add_mapping",
                 "concept": "checkout",
                 "elements": ["wf#btn-b", "fl#" + flow_b]}]})

    def types(self):
        return {aid: self.store.artifact_type(aid)
                for aid in self.store.scenes}

    def test_flow_reachable_cuts_cycles(self):
        els = []
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {"type": "rectangle", "id": i,
                                      "label": i.upper(), "x": x, "y": 60,
                                      "width": 160, "height": 64,
                                      "role": "node"}}
            for i, x in (("a", 0), ("b", 320), ("c", 640))
        ] + [
            {"op": "add", "element": {"type": "arrow", "id": "ab"},
             "from": "a", "to": "b"},
            {"op": "add", "element": {"type": "arrow", "id": "bc"},
             "from": "b", "to": "c"},
            {"op": "add", "element": {"type": "arrow", "id": "ca"},
             "from": "c", "to": "a"},
        ], errors)
        self.assertEqual(errors, [])
        reach = canvas.flow_reachable(els)
        self.assertEqual(reach["a"], {"a", "b", "c"})

    def test_337_redundant_entry_via_mapped_path(self):
        self.seed()
        x = canvas.cross_lint(self.store.scenes, self.types(),
                              self.store.registry)
        notes = (x.get("wf") or {}).get("notes") or []
        self.assertTrue(any("'postcode'" in n.lower() and "why twice" in n
                            for n in notes))

    def test_324_divergence_note_and_mirror_warn(self):
        # btn-a 'Continue' and btn-b 'Next' both map to fl#n2 → NOTE
        self.seed(btn_a="Continue", btn_b="Next", flow_b="n2")
        x = canvas.cross_lint(self.store.scenes, self.types(),
                              self.store.registry)
        notes = (x.get("wf") or {}).get("notes") or []
        self.assertTrue(any("same action" in n and "pick" in n
                            for n in notes))
        # same label 'Save' mapped to n1 and n2 → mirror WARN
        self.tearDown()
        self.setUp()
        self.seed(btn_a="Save", btn_b="Save", flow_b="n1")
        x2 = canvas.cross_lint(self.store.scenes, self.types(),
                               self.store.registry)
        warns = (x2.get("wf") or {}).get("warnings") or []
        self.assertTrue(any("different consequences" in w for w in warns))

    def test_324_goes_quiet_on_an_annotated_mapping(self):
        """Lint used to ignore what the tripwire check honoured.

        A divergence the agent had already explained kept coming back as
        'same action, 2 names; pick one?' every round, with no waive key
        to settle it (v0.4 capability assessment).
        """
        self.seed(btn_a="Continue", btn_b="Next", flow_b="n2")
        for m in self.store.registry["mappings"]:
            m["note"] = "intentionally-divergent: the button is the " \
                        "user's word, the step is ours"
        x = canvas.cross_lint(self.store.scenes, self.types(),
                              self.store.registry)
        notes = (x.get("wf") or {}).get("notes") or []
        self.assertFalse(any("same action" in n for n in notes))

    def test_324_annotation_scoped_elsewhere_still_fires(self):
        self.seed(btn_a="Continue", btn_b="Next", flow_b="n2")
        for m in self.store.registry["mappings"]:
            m["note"] = "intentionally-divergent: layout only"
            m["kinds"] = ["moved"]
        x = canvas.cross_lint(self.store.scenes, self.types(),
                              self.store.registry)
        notes = (x.get("wf") or {}).get("notes") or []
        self.assertTrue(any("same action" in n for n in notes))

    def test_324_offers_a_waive_key_that_works(self):
        self.seed(btn_a="Continue", btn_b="Next", flow_b="n2")
        x = canvas.cross_lint(self.store.scenes, self.types(),
                              self.store.registry)
        note = next(n for n in (x.get("wf") or {}).get("notes") or []
                    if "same action" in n)
        key = re.search(r"key: '([^']+)'", note).group(1)
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "wf",
            "ops": [{"op": "registry", "action": "waive", "key": key,
                     "reason": "the step and the button differ on purpose"}]})
        x2 = canvas.cross_lint(self.store.scenes, self.types(),
                               self.store.registry)
        self.assertFalse(any("same action" in n
                             for n in (x2.get("wf") or {}).get("notes") or []))

    def test_unmapped_elements_never_fire_324(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "wf", "concept_name": "WF"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "b1", "label": "Continue",
                "kind": "button", "x": 20, "y": 60, "width": 120,
                "height": 40, "role": "node"}}]})
        x = canvas.cross_lint(self.store.scenes, self.types(),
                              self.store.registry)
        self.assertEqual(x, {})

    def test_q12_whose_word_fires_once_waived(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dom",
            "create": {"id": "dom", "name": "Dom", "type": "domain",
                       "concept": "dom", "concept_name": "Dom"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "prov", "label": "Provider",
                "kind": "entity", "x": 60, "y": 60, "width": 180,
                "height": 64, "role": "node"}}]})
        self.store.apply_batch({
            "base_revn": 1, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "dom"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "hd", "label": "Provider",
                "x": 20, "y": 60, "width": 320, "height": 40,
                "role": "node"}}]})
        x = canvas.cross_lint(self.store.scenes, self.types(),
                              self.store.registry)
        notes = (x.get("wf") or {}).get("notes") or []
        self.assertTrue(any("whose word" in n for n in notes))
        self.store.apply_batch({
            "base_revn": 2, "artifact": "wf", "ops": [
                {"op": "registry", "action": "waive",
                 "key": "q12:wf:provider",
                 "reason": "users say Provider too — checked in round 3"}]})
        x2 = canvas.cross_lint(self.store.scenes, self.types(),
                               self.store.registry)
        self.assertFalse((x2.get("wf") or {}).get("notes"))

    def test_lint_lines_merges_cross_findings(self):
        self.seed()
        lines = self.store.lint_lines()
        self.assertIn("wf", lines)
        self.assertTrue(any("why twice" in n
                            for n in lines["wf"]["notes"]))
        debt = self.store.lint_debt()
        self.assertGreaterEqual(debt["wf"]["notes"], 1)


class TestUiUxAcceptance(Base):
    """v0.4 WP7 acceptance: one GOV.UK-shaped form project exercises the
    whole U-round surface — reading-order facts, the form/WCAG lint set,
    cross-artifact findings, and waive-driven quieting."""

    def test_v04_uiux_round_end_to_end(self):
        rec, _ = self.store.apply_batch({
            "base_revn": 0, "artifact": "wf",
            "create": {"id": "wf", "name": "Details", "type": "wireframe",
                       "concept": "signup", "concept_name": "Signup"},
            "ops": [
                {"op": "add", "element": {
                    "type": "frame", "id": "scr", "label": "Your details",
                    "x": 0, "y": 0, "width": 360, "height": 480}},
                # submit ABOVE the inputs it submits
                {"op": "add", "element": {
                    "type": "rectangle", "id": "go", "label": "Continue",
                    "kind": "button", "x": 20, "y": 40, "width": 120,
                    "height": 40, "frameId": "scr", "role": "node"}},
                # progress indicator label
                {"op": "add", "element": {
                    "type": "rectangle", "id": "prog",
                    "label": "Step 2 of 4", "kind": "block", "x": 160,
                    "y": 40, "width": 160, "height": 40,
                    "frameId": "scr", "role": "node"}},
                # three uniform-width inputs, one unlabeled, one asterisk
                {"op": "add", "element": {
                    "type": "rectangle", "id": "in-name",
                    "label": "Name *", "kind": "input", "x": 20, "y": 120,
                    "width": 320, "height": 40, "frameId": "scr",
                    "role": "node"}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "in-mail", "label": "Email",
                    "kind": "input", "x": 20, "y": 180, "width": 320,
                    "height": 40, "frameId": "scr", "role": "node"}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "in-bare", "kind": "input",
                    "x": 20, "y": 240, "width": 320, "height": 40,
                    "frameId": "scr", "role": "node"}},
                # undersized checkbox hugging a button (2.5.8)
                {"op": "add", "element": {
                    "type": "rectangle", "id": "tc", "label": "T&C",
                    "kind": "checkbox", "x": 20, "y": 320, "width": 20,
                    "height": 20, "frameId": "scr", "role": "node"}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "send", "label": "Send",
                    "kind": "button", "x": 40, "y": 320, "width": 120,
                    "height": 40, "frameId": "scr", "role": "node"}},
                # declared sticky bar
                {"op": "add", "element": {
                    "type": "rectangle", "id": "bar", "label": "Total",
                    "kind": "sticky-bar", "x": 0, "y": 440, "width": 360,
                    "height": 40, "frameId": "scr",
                    "role": "decoration"}}]})
        facts = rec["artifacts"]["wf"]["facts"]
        ro = [f for f in facts if f["fact"] == "reading_order_set"]
        self.assertEqual(len(ro), 1)
        self.assertEqual(ro[0]["order"][0], "Continue")

        lint = self.store.lint_lines()["wf"]
        joined = " | ".join(lint["warnings"])
        self.assertIn("precedes", joined)              # 1.3.2-shaped
        self.assertIn("has no label", joined)          # 3.3.2
        self.assertIn("(optional)", joined)            # GOV.UK asterisk
        notes = " | ".join(lint["notes"])
        self.assertIn("every answer the same length", notes)   # Q4
        self.assertIn("under the bar", notes)                  # Q7
        self.assertIn("closer than a thumb", notes)            # Q11
        self.assertIn("progress indicator", notes)             # Q25

        # domain term + matching wireframe label → Q12; waives quiet both
        self.store.apply_batch({
            "base_revn": 1, "artifact": "dom",
            "create": {"id": "dom", "name": "Dom", "type": "domain",
                       "concept": "signup"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "acct", "label": "Email",
                "kind": "entity", "x": 60, "y": 60, "width": 180,
                "height": 64, "role": "node"}}]})
        notes2 = " | ".join(self.store.lint_lines()["wf"]["notes"])
        self.assertIn("whose word", notes2)
        self.store.apply_batch({
            "base_revn": 2, "artifact": "wf", "ops": [
                {"op": "registry", "action": "waive", "key": "q25:wf",
                 "reason": "steps stay — regulated journey"},
                {"op": "registry", "action": "waive",
                 "key": "q12:wf:email",
                 "reason": "Email is the users' word too"}]})
        notes3 = " | ".join(self.store.lint_lines()["wf"]["notes"])
        self.assertNotIn("progress indicator", notes3)
        self.assertNotIn("whose word", notes3)

        # reordering the form narrates reading_order_changed
        rec2, _ = self.store.apply_batch({
            "base_revn": 3, "artifact": "wf", "ops": [
                {"op": "mod", "id": "go", "attrs": {"y": 400}}]})
        ch = [f for f in rec2["artifacts"]["wf"]["facts"]
              if f["fact"] == "reading_order_changed"]
        self.assertEqual(len(ch), 1)
        self.assertNotEqual(ch[0]["order"][0], "Continue")
        # and the fixed order clears the submit-before-inputs WARN
        warns = " | ".join(self.store.lint_lines()["wf"]["warnings"])
        self.assertNotIn("precedes", warns)


class TestHandoverExport(Base):
    """Tooltips survive leaving the canvas (v0.5).

    Per-element detail lives in a hover-only tooltip, which survives no
    export at all — so a session that ends "I'm handing these to my
    analyst" hands over drawings with the detail stripped out.
    """

    def seed(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "fl",
            "create": {"id": "fl", "name": "FL", "type": "flow",
                       "concept": "fl", "concept_name": "FL"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "edgar", "label": "SEC EDGAR",
                "x": 100, "y": 100, "width": 160, "height": 60,
                "role": "node",
                "tooltip": "Feeds **three** of the five scorers."}},
                {"op": "add", "element": {
                    "type": "rectangle", "id": "news", "label": "News",
                    "x": 400, "y": 100, "width": 160, "height": 60,
                    "role": "node"}}]})

    def test_footnotes_carry_the_tooltip_text(self):
        self.seed()
        svg, _, _ = canvas.render_svg(self.store.scenes["fl"],
                                      footnotes=True)
        self.assertIn("Feeds three of the five scorers.", svg)
        self.assertIn("SEC EDGAR", svg)

    def test_markdown_emphasis_is_flattened_not_printed(self):
        self.seed()
        svg, _, _ = canvas.render_svg(self.store.scenes["fl"],
                                      footnotes=True)
        self.assertNotIn("**", svg)

    def test_no_footnotes_without_the_flag(self):
        self.seed()
        svg, _, _ = canvas.render_svg(self.store.scenes["fl"])
        self.assertNotIn("Feeds three", svg)

    def test_only_tooltip_bearing_elements_are_numbered(self):
        self.seed()
        notes = canvas.collect_footnotes(self.store.scenes["fl"])
        self.assertEqual([n[1] for n in notes], ["SEC EDGAR"])

    def test_glossary_pairs_carry_definitions(self):
        pairs = dict(canvas.parse_glossary_pairs(
            "# Glossary\n\n**Pull**: one fetch of one Source at one\n"
            "moment.\n\n**Run**: one execution of the pipeline.\n"))
        self.assertEqual(pairs["Pull"],
                         "one fetch of one Source at one moment.")
        self.assertEqual(pairs["Run"], "one execution of the pipeline.")


class TestMermaidExport(Base):
    """`export --format mermaid|er` — the drawing as portable text (v0.9).

    The command exists for two uses and NOT for a third: a diffable text
    form for a PR, and a seed for another tool. Handover stays the SVG's
    job, because mermaid cannot carry a tooltip.

    The measured bar these pin, re-run at this head over the 24-fixture
    corpus through the real converter (server + headless chromium + m2e
    2.2.2 + dagre): 132/132 nodes, 132/132 labels, 132/132 shapes
    (including roundness) and 144/144 edges across the 14 flows; 27/27
    entities and 29/29 relations across the 3 domains. The pre-fix shape
    map scored 120/132 shapes and 104/132 with roundness on the same run,
    which is the defect these tests hold shut.
    """

    def export_args(self, **kw):
        """A Namespace for `cmd_export`, mermaid defaults filled in.

        Args:
            **kw: Overrides — `artifact`, `format`, `out`, `direction`,
                `lanes`, `no_kinds`.

        Returns:
            The argparse Namespace `cmd_export` expects.
        """
        base = {"project": self.tmp, "artifact": None, "out": None,
                "format": "mermaid", "direction": None, "lanes": False,
                "no_kinds": False, "with_footnotes": False,
                "no_glossary": False}
        base.update(kw)
        return argparse.Namespace(**base)

    def run_export(self, **kw):
        """Run `cmd_export` and capture what it printed.

        Args:
            **kw: Passed to `export_args`.

        Returns:
            ``(exit_code, output)`` — stdout and stderr together, because
            a per-relation ER refusal prints to stdout while `die` writes
            to stderr, and a caller reads one terminal.
        """
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(buf):
                code = canvas.cmd_export(self.export_args(**kw))
        except SystemExit as e:
            return e.code, buf.getvalue()
        return code, buf.getvalue()

    def seed(self, atype="flow", ops=None, aid="fl"):
        """Create one artifact of `atype` holding `ops`.

        Args:
            atype: Artifact type (flow/domain/wireframe/sequence).
            ops: The op list; a two-node flow when omitted.
            aid: The artifact id.
        """
        if ops is None:
            ops = [{"op": "add", "element": {
                "type": "rectangle", "id": "cart", "label": "Cart",
                "x": 40, "y": 40, "width": 160, "height": 60,
                "role": "node"}},
                {"op": "add", "element": {
                    "type": "ellipse", "id": "done", "label": "Done",
                    "x": 400, "y": 40, "width": 120, "height": 60,
                    "role": "node"}}]
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": aid,
            "create": {"id": aid, "name": aid.upper(), "type": atype,
                       "concept": aid, "concept_name": aid.upper()},
            "ops": ops})

    def text_of(self, aid="fl"):
        """The exported mermaid text for `aid`.

        Args:
            aid: The artifact id.

        Returns:
            The file's contents.
        """
        out = self.tmp / "x.mmd"
        code, _ = self.run_export(artifact=aid, out=str(out))
        self.assertEqual(code, 0)
        return out.read_text(encoding="utf-8")

    # -- the shape map, both poles ------------------------------------

    def test_ellipse_exports_as_circle_never_stadium(self):
        """`(["x"])` is the STADIUM, and the vendored converter sends
        STADIUM and ROUND to one result — a rounded RECTANGLE. So the
        stadium form silently turns every terminal into a box. Both
        poles are asserted because the wrong one reads as correct."""
        self.seed()
        text = self.text_of()
        self.assertIn('(("Done"))', text)
        self.assertNotIn('(["Done"])', text)

    def test_roundness_exports_round_never_square(self):
        """A rounded rectangle (the `store` kind) exported as `["x"]`
        comes back square — roundness was simply never consulted."""
        self.seed(ops=[{"op": "add", "element": {
            "type": "rectangle", "id": "db", "label": "Ledger",
            "x": 40, "y": 40, "width": 160, "height": 60,
            "role": "node", "roundness": {"type": 3}}}])
        text = self.text_of()
        self.assertIn('("Ledger")', text)
        self.assertNotIn('["Ledger"]', text)

    def test_the_shape_map_has_exactly_one_spelling(self):
        """THE ANTI-DRIFT PIN. `_flow_to_mermaid` (the `--relayout`
        gear) and the export both go through `mermaid_shape`; a second
        map is what let the first one be wrong for as long as only the
        re-layout path read it. Asserted structurally rather than by
        comparing two outputs, because two wrong maps agree too."""
        for el, want in (({"type": "ellipse"}, '(("%s"))'),
                         ({"type": "diamond"}, '{"%s"}'),
                         ({"type": "rectangle"}, '["%s"]'),
                         ({"type": "rectangle",
                           "roundness": {"type": 3}}, '("%s")')):
            self.assertEqual(canvas.mermaid_shape(el), want)
        els = [{"id": "done", "type": "ellipse", "x": 0, "y": 0,
                "width": 100, "height": 60, "customData": {"role": "node"}},
               {"id": "done-l", "type": "text", "containerId": "done",
                "text": "Done"}]
        # the internal gear now produces the same bracket as the export
        self.assertIn('(("Done"))', canvas._flow_to_mermaid(els)[0])

    def test_a_rounded_ellipse_states_its_lossiness(self):
        """A rounded ELLIPSE and a rounded DIAMOND have no mermaid vertex
        syntax at all, so roundness is dropped. That is an honest gap,
        not a defect — and `mermaid_shape`'s docstring must keep saying
        so, because a caller cannot see it from the output."""
        self.assertEqual(
            canvas.mermaid_shape({"type": "ellipse",
                                  "roundness": {"type": 2}}), '(("%s"))')
        doc = canvas.mermaid_shape.__doc__
        self.assertIn("rounded ELLIPSE", doc)
        self.assertIn("no mermaid vertex syntax", doc)

    # -- identifiers ---------------------------------------------------

    def test_end_is_underscore_guarded_not_hyphen_guarded(self):
        """A HYPHEN IS A TOKEN BOUNDARY in the flowchart lexer, so the
        intuitive guard is a no-op: `end-node["end"]` still lexes `end`
        and dies with "got 'end'". `end_node` is one NODE_STRING."""
        self.assertEqual(canvas.mermaid_ident("end", "e1"), "end_node")
        self.assertNotEqual(canvas.mermaid_ident("end", "e1"), "end-node")
        self.seed(ops=[{"op": "add", "element": {
            "type": "rectangle", "id": "e1", "label": "end",
            "x": 40, "y": 40, "width": 160, "height": 60, "role": "node"}}])
        text = self.text_of()
        self.assertIn("end_node", text)
        self.assertNotIn("end-node", text)

    def test_a_leading_digit_is_prefixed(self):
        """`slugify("06:00 weekday")` is a legal element id and an
        illegal mermaid node id."""
        self.assertEqual(canvas.mermaid_ident("06:00 weekday", "x"),
                         "n-06-00-weekday")

    def test_export_ids_are_the_ids_mint_id_would_mint(self):
        """There is no identity-versus-portability trade here: both
        sides call `slugify`, so a node cited back in an op needs no
        translation table."""
        self.assertEqual(canvas.mermaid_ident("Pull Market Data", "x"),
                         canvas.slugify("Pull Market Data"))

    # -- label escaping ------------------------------------------------

    def test_angle_brackets_are_escaped_or_they_disappear(self):
        """Labels are rendered as HTML. Unescaped, `<ok>` is parsed as a
        tag and VANISHES from the picture — the silent direction."""
        body, md = canvas.mermaid_label("50% > 30% <ok>")
        self.assertFalse(md)
        self.assertIn("#lt;ok#gt;", body)
        self.assertNotIn("<ok>", body)

    def test_hash_is_escaped_before_everything_else(self):
        """Mermaid substitutes HTML numeric entities, so escaping `#`
        after anything else eats the entity just written."""
        body, _ = canvas.mermaid_label('#1 "a" <b>')
        self.assertTrue(body.startswith("#35;1"))
        self.assertIn("#quot;", body)
        self.assertNotIn("#35;quot;", body)
        self.assertNotIn("#35;lt;", body)

    def test_a_newline_uses_a_markdown_string_only_when_safe(self):
        """Backticks are the ONLY form that carries a real newline, but
        inside them `*x*` comes back `x` and `#lt;` is dropped — entity
        substitution runs before the HTML parse."""
        self.assertEqual(canvas.mermaid_label("two\nlines"),
                         ("two\nlines", True))
        body, md = canvas.mermaid_label("two\n*lines*")
        self.assertFalse(md)
        self.assertIn("<br/>", body)

    def test_an_empty_label_becomes_one_space(self):
        """`{""}` is a parse error in every bracket form."""
        self.assertEqual(canvas.mermaid_vertex({"type": "diamond"}, ""),
                         '{" "}')

    def test_a_pipe_in_an_edge_label_is_escaped(self):
        """The pipe is the edge-label delimiter."""
        self.seed(ops=[
            {"op": "add", "element": {
                "type": "rectangle", "id": "a", "label": "A", "x": 40,
                "y": 40, "width": 160, "height": 60, "role": "node"}},
            {"op": "add", "element": {
                "type": "rectangle", "id": "b", "label": "B", "x": 400,
                "y": 40, "width": 160, "height": 60, "role": "node"}},
            {"op": "add", "element": {
                "type": "arrow", "id": "t", "label": "a|b"},
             "from": "a", "to": "b"}])
        text = self.text_of()
        self.assertIn("#124;", text)
        self.assertNotIn("|a|b|", text)

    def test_a_dashed_edge_exports_dotted(self):
        """`-.->` is the form that re-imports dashed."""
        self.seed(ops=[
            {"op": "add", "element": {
                "type": "rectangle", "id": "a", "label": "A", "x": 40,
                "y": 40, "width": 160, "height": 60, "role": "node"}},
            {"op": "add", "element": {
                "type": "rectangle", "id": "b", "label": "B", "x": 400,
                "y": 40, "width": 160, "height": 60, "role": "node"}},
            {"op": "add", "element": {
                "type": "arrow", "id": "t", "strokeStyle": "dashed"},
             "from": "a", "to": "b"}])
        self.assertIn("-.->", self.text_of())

    # -- refusals, each beside a firing ---------------------------------

    def test_wireframe_refuses_by_name_and_points_at_the_svg(self):
        """Mermaid has no diagram whose subject is a screen, and a
        wireframe's meaning IS its layout."""
        self.seed(atype="wireframe", aid="wf")
        code, out = self.run_export(artifact="wf")
        self.assertEqual(code, 2)
        self.assertIn("wireframe", out)
        self.assertIn("--with-footnotes", out)
        self.assertFalse((self.tmp / "project_knowledge" / "wf.mmd").exists())

    def test_sequence_refuses_as_deferred_and_says_why(self):
        """DEFERRED, not impossible — the corpus carries zero sequence
        artifacts, so the mapping has never been measured. The word
        matters: a reader who thinks it is impossible stops asking."""
        self.seed(atype="sequence", aid="sq")
        code, out = self.run_export(artifact="sq")
        self.assertEqual(code, 2)
        self.assertIn("DEFERRED", out)
        self.assertIn("sequenceDiagram", out)
        self.assertIn("ZERO sequence", out)

    def test_a_domain_exports_fine_beside_those_refusals(self):
        """Refusals with no firing beside them prove nothing."""
        self.seed(atype="domain", aid="dm")
        code, out = self.run_export(artifact="dm")
        self.assertEqual(code, 0)
        self.assertIn("FORMAT=mermaid", out)
        self.assertTrue((self.tmp / "project_knowledge" / "dm.mmd").exists())

    def test_er_refuses_every_relation_with_no_cardinality(self):
        """Mermaid's ER grammar takes a token on BOTH ends and cannot
        spell "unknown", so exporting an unsettled relation would invent
        the claim. Refused by name, and NO FILE is written."""
        self.seed(atype="domain", aid="dm", ops=[
            {"op": "add", "element": {
                "type": "rectangle", "id": "run", "label": "Run", "x": 40,
                "y": 40, "width": 160, "height": 60, "role": "node",
                "kind": "entity"}},
            {"op": "add", "element": {
                "type": "rectangle", "id": "sig", "label": "Signal",
                "x": 400, "y": 40, "width": 160, "height": 60,
                "role": "node", "kind": "entity"}},
            {"op": "add", "element": {
                "type": "arrow", "id": "r1", "label": "holds"},
             "from": "run", "to": "sig"}])
        code, out = self.run_export(artifact="dm", format="er")
        self.assertEqual(code, 2)
        self.assertIn("no `cardinality:` tooltip", out)
        self.assertIn("Run", out)
        self.assertIn("Signal", out)
        self.assertFalse((self.tmp / "project_knowledge" / "dm.mmd").exists())

    def test_er_fires_when_the_cardinality_was_actually_settled(self):
        """The same command on an erDiagram-seeded artifact: the
        cardinality tooltip reparses into both end tokens."""
        self.seed(atype="domain", aid="dm", ops=[
            {"op": "add", "element": {
                "type": "rectangle", "id": "run", "label": "Run", "x": 40,
                "y": 40, "width": 160, "height": 60, "role": "node",
                "kind": "entity"}},
            {"op": "add", "element": {
                "type": "rectangle", "id": "sig", "label": "Signal",
                "x": 400, "y": 40, "width": 160, "height": 60,
                "role": "node", "kind": "entity"}},
            {"op": "add", "element": {
                "type": "arrow", "id": "r1", "label": "holds 1..*",
                "tooltip": "cardinality: Run 1 — 1..* Signal"},
             "from": "run", "to": "sig"}])
        out_p = self.tmp / "er.mmd"
        code, out = self.run_export(artifact="dm", format="er",
                                    out=str(out_p))
        self.assertEqual(code, 0)
        text = out_p.read_text(encoding="utf-8")
        self.assertIn("erDiagram", text)
        self.assertIn('Run ||--|{ Signal : "holds"', text)

    def test_er_on_a_flow_refuses(self):
        """`--format er` reads entities; a flow has none."""
        self.seed()
        code, out = self.run_export(artifact="fl", format="er")
        self.assertEqual(code, 2)
        self.assertIn("--format er", out)

    # -- the staleness stance -------------------------------------------

    def test_every_file_carries_the_snapshot_stamp(self):
        """`%%` is skipped by mermaid and by `mermaid_kind`, so the
        stance costs the reader nothing and survives a paste into a
        wiki. Without it, "is this still what the drawing says?" is
        answered only in someone's terminal scrollback."""
        self.seed()
        text = self.text_of()
        self.assertTrue(text.startswith("%% wysiwyg-grilling: fl at revn"))
        self.assertIn("SNAPSHOT", text)
        self.assertIn("never read back", text)

    def test_the_stamp_does_not_break_re_seeding(self):
        """A `%%` line must not change what the text IS."""
        self.seed()
        self.assertEqual(canvas.mermaid_kind(self.text_of()), "flowchart")

    def test_the_drop_report_counts_what_was_left_behind(self):
        """An export that announces "I dropped your pins and your notes"
        is a stronger defence against being mistaken for the artifact
        than refusing to export at all."""
        self.seed(ops=[
            {"op": "add", "element": {
                "type": "rectangle", "id": "a", "label": "A", "x": 40,
                "y": 40, "width": 160, "height": 60, "role": "node"}},
            {"op": "add", "element": {
                "type": "ellipse", "id": "p", "label": "why?", "x": 40,
                "y": 300, "width": 40, "height": 40, "role": "pin"}}])
        code, out = self.run_export(artifact="fl")
        self.assertEqual(code, 0)
        self.assertIn("DROPPED=1", out)
        self.assertIn("the drawing is still the truth", out)

    # -- lanes -----------------------------------------------------------

    def test_lanes_are_off_by_default_and_the_drop_is_counted(self):
        """`--lanes` produces a file this repo's own seeder refuses, so
        it is opt-in — but silence about the drop is not the price."""
        self.seed(ops=[
            {"op": "add", "element": {
                "type": "frame", "id": "lane", "label": "Ingest", "x": 0,
                "y": 0, "width": 400, "height": 200}},
            {"op": "add", "element": {
                "type": "rectangle", "id": "a", "label": "A", "x": 40,
                "y": 40, "width": 160, "height": 60, "role": "node",
                "frameId": "lane"}}])
        code, out = self.run_export(artifact="fl")
        self.assertEqual(code, 0)
        self.assertNotIn("subgraph", self.text_of())
        self.assertIn("DROPPED=1", out)

    def test_lanes_on_emits_subgraphs_and_names_the_asymmetry(self):
        """Valid mermaid that renders in a wiki and that this repo
        cannot read back. Stated, not papered over."""
        self.seed(ops=[
            {"op": "add", "element": {
                "type": "frame", "id": "lane", "label": "Ingest", "x": 0,
                "y": 0, "width": 400, "height": 200}},
            {"op": "add", "element": {
                "type": "rectangle", "id": "a", "label": "A", "x": 40,
                "y": 40, "width": 160, "height": 60, "role": "node",
                "frameId": "lane"}}])
        out_p = self.tmp / "l.mmd"
        code, out = self.run_export(artifact="fl", lanes=True,
                                    out=str(out_p))
        self.assertEqual(code, 0)
        self.assertIn("subgraph", out_p.read_text(encoding="utf-8"))
        self.assertIn("refuses them", out)

    # -- kinds ------------------------------------------------------------

    def test_kinds_ride_as_classdefs_and_are_docs_side_only(self):
        """The converter folds a class into container/label STYLES and
        never passes the name on, so 0 of 123 corpus node kinds
        round-trip. The lines are carried for the reader, and the
        docstring says which."""
        self.seed(ops=[{"op": "add", "element": {
            "type": "rectangle", "id": "a", "label": "A", "x": 40,
            "y": 40, "width": 160, "height": 60, "role": "node",
            "kind": "source"}}])
        self.assertIn("classDef source", self.text_of())
        self.assertIn("DROPPED on re-import",
                      canvas.flow_to_mermaid_export.__doc__)

    def test_no_kinds_omits_them(self):
        self.seed(ops=[{"op": "add", "element": {
            "type": "rectangle", "id": "a", "label": "A", "x": 40,
            "y": 40, "width": 160, "height": 60, "role": "node",
            "kind": "source"}}])
        out_p = self.tmp / "n.mmd"
        self.run_export(artifact="fl", no_kinds=True, out=str(out_p))
        self.assertNotIn("classDef", out_p.read_text(encoding="utf-8"))

    # -- direction ---------------------------------------------------------

    def test_direction_is_read_off_the_drawing_when_unset(self):
        """flow.md's own recipe: wider than tall reads left-to-right."""
        self.seed()
        self.assertIn("flowchart LR", self.text_of())

    def test_svg_is_still_the_default_format(self):
        """The new flag must not move the old door."""
        self.seed()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            canvas.cmd_export(self.export_args(artifact="fl", format="svg"))
        self.assertTrue((self.tmp / "project_knowledge" / "fl.svg").exists())


class TestLintHygiene(Base):
    """Lint noise reduction (v0.5).

    The v0.4 assessment ended with 16 standing notes of which ~4 were
    distinct: one registry finding copied into every artifact bucket, one
    question repeated across three frames of the same screen, and nags
    about elements the user drew.
    """

    def test_registry_findings_stay_in_the_registry_bucket(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "upsert_concept",
                     "id": "checkout", "owed": ["domain"]}]})
        lines = self.store.lint_lines()
        reg = " | ".join(lines.get("registry", {}).get("notes") or [])
        art = " | ".join(lines["checkout-flow"]["notes"])
        self.assertNotEqual(reg, "")
        for note in lines.get("registry", {}).get("notes") or []:
            self.assertNotIn(note, art)

    def test_misfile_note_never_pushes_a_view_to_the_umbrella(self):
        reg = {"concepts": [
            {"id": "argus", "name": "Argus", "views": [], "unviewed": True},
            {"id": "dashboard", "name": "Dashboard",
             "views": ["argus-dashboard"], "unviewed": False}],
            "mappings": [], "waives": {}}
        notes = " | ".join(canvas.lint_registry([], reg))
        self.assertNotIn("is named after concept", notes)

    def test_misfile_note_still_catches_a_view_stuck_on_the_umbrella(self):
        reg = {"concepts": [
            {"id": "shop", "name": "Shop",
             "views": ["report-wireframe"], "unviewed": False},
            {"id": "report", "name": "Report", "views": [],
             "unviewed": True}],
            "mappings": [], "waives": {}}
        notes = " | ".join(canvas.lint_registry([], reg))
        self.assertIn("is named after concept", notes)

    def test_q12_asks_once_per_waive_key(self):
        """Three frames of one screen are one question, not three."""
        ops = [{"op": "add", "element": {
            "type": "rectangle", "id": "e-provider", "kind": "entity",
            "label": "Provider", "x": 40, "y": 40, "width": 164,
            "height": 96}}]
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dm",
            "create": {"id": "dm", "name": "DM", "type": "domain",
                       "concept": "dm", "concept_name": "DM"}, "ops": ops})
        self.store.apply_batch({
            "base_revn": 1, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "wf", "concept_name": "WF"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "b%d" % i, "kind": "button",
                "label": "Provider", "x": 40 + i * 200, "y": 40,
                "width": 160, "height": 40}} for i in range(3)]})
        x = canvas.cross_lint(self.store.scenes,
                              {a: self.store.artifact_type(a)
                               for a in self.store.scenes},
                              self.store.registry)
        hits = [n for n in (x.get("wf") or {}).get("notes") or []
                if "whose word" in n]
        self.assertEqual(len(hits), 1)

    def test_offgrid_ignores_user_drawn_elements(self):
        els = [{"id": "note-user-1", "type": "rectangle", "x": 3, "y": 7,
                "width": 101, "height": 53, "isDeleted": False,
                "customData": {"role": "node", "author": "user"}}]
        notes = canvas.lint_layout(els, artifact_type="flow")["notes"]
        self.assertFalse(any("off the 4px grid" in n for n in notes))
        els[0]["customData"]["author"] = "agent"
        notes2 = canvas.lint_layout(els, artifact_type="flow")["notes"]
        self.assertTrue(any("off the 4px grid" in n for n in notes2))


class TestStoredHeightMatchesThePaintedLineHeight(Base):
    """Every write path reserves height at the element's own `lineHeight`.

    `text_dims` has taken a `line_height` parameter since the Task 24
    follow-up, and five sites that hold an element and WRITE its height
    went on calling it at the 1.25 default. So a text the client had set
    to double spacing was stored at `lines * fontSize * 1.25` while the
    paint drew `lines * fontSize * lineHeight` — under by the last line,
    downward, off the bottom.

    THE FIVE, each with the entry path that reaches it, because the
    first draft of this class pinned ONE of them and read as though it
    covered the change (TASK-POLISH review, Minor 1 — reverting the
    other four left all 1320 tests green):

    | site | reached by |
    |---|---|
    | `_set_label` label branch | `mod label` on a host element |
    | `_set_label` text branch | `mod label` on a TEXT element |
    | `apply_ops` mod-text branch | `mod text` on a text element |
    | `validate_scene` text-in-text | loading an artifact with one |
    | `cmd_x_as_user` rename | the adapter verb, over a live server |

    The last is a subprocess test and lives with its server in
    `TestXAsUserFidelity`; the other four are here. Each was proven to
    bite by reverting ITS OWN site alone.

    `route_ctx` IS NOT ONE OF THEM, and this class said it was. That
    name came from curator batch 26's enumeration and survived into the
    fix commit unchecked: `route_ctx` is a routing-error wrapper nested
    inside `apply_ops` and contains no `text_dims` call at all. The site
    is `apply_ops`' own mod-text branch, a few lines below it. Same
    class of error this task's own concern 5 was about — a name in prose
    that no check reads.

    Curator batch 26 annotated all of this and deliberately did not pin
    it, because the export is protected by `ink_extent`'s `max()` and a
    red would have asserted a miss nobody had demonstrated. With the
    sites fixed the ARITHMETIC is demonstrable without demonstrating a
    silent lint, so it is pinned as what the STORE holds — the thing
    every later reader inherits.

    The numbers throughout are batch 26's own, at fontSize 16: three
    lines at `lineHeight: 2.0` is 96px and at the 1.25 default 60px, and
    the 36px gap is exactly the under-measure that was filed.
    """

    TEXT = "aaa\nbbb\nccc"

    def assert_reserved_at_double_spacing(self, el, where):
        """Assert one element's stored height is the painted one.

        Both the correct height and the default it must NOT be are
        asserted, so a `text_dims` that stopped varying with
        `line_height` fails here rather than passing because the two
        readings agree.

        Args:
            el: The element whose height was just written.
            where: The site's name, for the failure message.
        """
        fs = el.get("fontSize", 16)
        self.assertEqual(
            el.get("lineHeight"), 2.0,
            "%s overwrote the client's line height, so this measures the "
            "default twice" % where)
        self.assertEqual(
            (el["height"], canvas.text_dims(self.TEXT, fs)[1]), (96, 60),
            "%s stored %rpx where the paint draws 96 at lineHeight 2.0 "
            "(the default reads 60) — batch 26's 36px under-measure"
            % (where, el["height"]))

    def seed_text(self, tid="t1"):
        """Seed one text element and give it the client's double spacing.

        `lineHeight` is set on the element and COMMITTED rather than
        passed through the seeding op, because that is how it arrives in
        life: the client sets it and the next op has to read it back.

        Args:
            tid: The text element's id.

        Returns:
            The store's element list for artifact `flow`.
        """
        self.store.apply_batch({
            "base_revn": 0, "artifact": "flow",
            "create": {"id": "flow", "name": "F", "type": "flow"},
            "ops": [{"op": "add", "element": {
                "type": "text", "id": tid, "text": "one", "x": 0, "y": 0,
                "fontSize": 16}}]})
        for e in self.store.scenes["flow"]:
            if e["id"] == tid:
                e["lineHeight"] = 2.0
        self.store.commit(author="user", base_revn=self.store.head_revn(),
                          new_scenes={"flow": self.store.scenes["flow"]})
        return self.store.scenes["flow"]

    def test_a_relabel_reserves_the_height_the_client_will_paint(self):
        """`_set_label`'s LABEL branch, through a real `mod label`.

        Driven through the op rather than by calling `_set_label`,
        because the claim is about the path a client reaches.
        """
        self.store.apply_batch({
            "base_revn": 0, "artifact": "flow",
            "create": {"id": "flow", "name": "F", "type": "flow"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n1", "label": "one", "x": 0,
                "y": 0, "width": 400, "height": 300, "role": "node"}}]})
        for e in self.store.scenes["flow"]:
            if e["id"] == "n1-label":
                e["lineHeight"] = 2.0
        self.store.commit(author="user", base_revn=self.store.head_revn(),
                          new_scenes={"flow": self.store.scenes["flow"]})
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "flow",
            "ops": [{"op": "mod", "id": "n1",
                     "attrs": {"label": self.TEXT}}]})
        lbl = next(e for e in self.store.scenes["flow"]
                   if e["id"] == "n1-label")
        self.assert_reserved_at_double_spacing(lbl, "mod label (bound)")

    def test_mod_label_on_a_text_element_reserves_its_own_spacing(self):
        """`_set_label`'s TEXT branch — a different branch, same op.

        `mod label` on a text element means its own text and never a
        bound one (text-in-text renders a character wide; ART-010), so
        this reaches a second `text_dims` call the bound-label test
        above never touches.
        """
        self.seed_text()
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "flow",
            "ops": [{"op": "mod", "id": "t1",
                     "attrs": {"label": self.TEXT}}]})
        el = next(e for e in self.store.scenes["flow"] if e["id"] == "t1")
        self.assert_reserved_at_double_spacing(el, "mod label (text)")

    def test_mod_text_reserves_the_spacing_the_element_carries(self):
        """`apply_ops`' mod-text branch — the site misnamed `route_ctx`.

        A different attribute from the two above and therefore a
        different branch: `mod text` writes the element's text directly
        instead of going through `_set_label`.
        """
        self.seed_text()
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "flow",
            "ops": [{"op": "mod", "id": "t1",
                     "attrs": {"text": self.TEXT}}]})
        el = next(e for e in self.store.scenes["flow"] if e["id"] == "t1")
        self.assert_reserved_at_double_spacing(el, "mod text")

    def test_the_text_in_text_repair_reserves_the_containers_spacing(self):
        """`validate_scene`, reached by LOADING a structure it repairs.

        A text element bound to another TEXT element is illegal
        Excalidraw structure, and the loader merges the child's content
        into the empty container and drops the child. That merge
        re-measures, on a container carrying its own `lineHeight`, so it
        is the same defect on a load-repair path rather than an op path
        — and the only one of the five not reachable through any op,
        which is why it is driven by writing the file and re-opening the
        project.
        """
        self.store.apply_batch({
            "base_revn": 0, "artifact": "flow",
            "create": {"id": "flow", "name": "F", "type": "flow"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n1", "label": "x", "x": 0,
                "y": 0, "width": 100, "height": 60}}]})
        path = self.project.artifacts_dir / "flow.excalidraw"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["elements"] = [
            {"id": "host", "type": "text", "x": 0, "y": 0, "width": 10,
             "height": 10, "text": "", "originalText": "", "fontSize": 16,
             "lineHeight": 2.0, "containerId": None,
             "boundElements": [{"id": "kid", "type": "text"}]},
            {"id": "kid", "type": "text", "x": 0, "y": 0, "width": 10,
             "height": 10, "text": self.TEXT, "originalText": self.TEXT,
             "fontSize": 16, "lineHeight": 1.25, "containerId": "host"}]
        path.write_text(json.dumps(doc), encoding="utf-8")
        reloaded = canvas.Store(self.project)
        host = next(e for e in reloaded.scenes["flow"] if e["id"] == "host")
        self.assertEqual(host["text"], self.TEXT,
                         "the text-in-text repair did not merge, so this "
                         "measures an element nothing re-sized")
        self.assert_reserved_at_double_spacing(host, "text-in-text repair")


class TestTextFit(Base):
    """Text that does not fit the box it is drawn in (v0.5).

    The agent cannot see its own drawing, so an overflow it is never told
    about ships. Three did in the v0.4 assessment. The old check read the
    STORED label width — an estimate the client re-derives — so it stayed
    silent on all three.
    """

    def entity(self, attrs, width=164):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dm",
            "create": {"id": "dm", "name": "DM", "type": "domain",
                       "concept": "dm", "concept_name": "DM"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "report", "kind": "entity",
                "label": "Report", "attributes": attrs,
                "x": 100, "y": 100, "width": width, "height": 96}}]})
        return canvas.lint_layout(self.store.scenes["dm"],
                                  artifact_type="domain")

    def test_attribute_row_wider_than_its_entity_warns(self):
        warns = self.entity(["audience: us | clients | committee"])["warnings"]
        self.assertTrue(any("does not fit" in w and "too wide" in w
                            for w in warns))

    def test_attribute_row_that_fits_is_silent(self):
        warns = self.entity(["sections"])["warnings"]
        self.assertFalse(any("does not fit" in w for w in warns))

    def test_wrapping_label_is_judged_wrapped_not_unwrapped(self):
        """A two-word label the renderer wraps is not an overflow.

        Measuring it unwrapped reported every slightly-long node label in
        the assessment fixture — 13 false positives against 3 real ones.
        """
        self.store.apply_batch({
            "base_revn": 0, "artifact": "fl",
            "create": {"id": "fl", "name": "FL", "type": "flow",
                       "concept": "fl", "concept_name": "FL"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n1", "label": "Technical Signals",
                "x": 100, "y": 100, "width": 152, "height": 60,
                "role": "node"}}]})
        warns = canvas.lint_layout(self.store.scenes["fl"],
                                   artifact_type="flow")["warnings"]
        self.assertFalse(any("does not fit" in w for w in warns))

    def test_unbreakable_word_wider_than_its_box_warns(self):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "fl",
            "create": {"id": "fl", "name": "FL", "type": "flow",
                       "concept": "fl", "concept_name": "FL"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n1",
                "label": "Unsplittablesupercalifragilistic",
                "x": 100, "y": 100, "width": 96, "height": 60,
                "role": "node"}}]})
        warns = canvas.lint_layout(self.store.scenes["fl"],
                                   artifact_type="flow")["warnings"]
        self.assertTrue(any("does not fit" in w for w in warns))

    def test_render_svg_wraps_fixed_width_text(self):
        """The snapshot is the agent's eyes — it must not lie about fit.

        `render_svg` only split on newlines, so a label the live canvas lays
        out over two lines exported as one long line spilling out of its
        box (the Weekly Brief provenance line, v0.4 assessment).
        """
        long_text = ("Issued 08:12 - built from the run of 8 Aug 06:00 - "
                     "thresholds 0.70 / VaR 2.5%")
        els = [{"id": "t1", "type": "text", "x": 0, "y": 0, "width": 200,
                "height": 40, "text": long_text, "originalText": long_text,
                "fontSize": 16, "autoResize": False, "textAlign": "left",
                "isDeleted": False}]
        svg, _, _ = canvas.render_svg(els)
        self.assertGreater(svg.count("<text"), 1)


class TestInputValue(Base):
    """kind:"input" carries its value (v0.5).

    `value` was read only by kpi and slider, so an input handed one had
    it silently dropped — while `mod value` on the same element errored
    loudly. The admin console's schedule field rendered as a box reading
    "Run at" with no time in it and nothing complained.
    """

    def seed(self, **extra):
        el = {"type": "rectangle", "id": "sched", "kind": "input",
              "label": "Run at", "x": 100, "y": 100, "width": 260,
              "height": 40}
        el.update(extra)
        self.store.apply_batch({
            "base_revn": 0, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "wf", "concept_name": "WF"},
            "ops": [{"op": "add", "element": el}]})

    def value_rows(self):
        return [e["text"] for e in self.store.scenes["wf"]
                if (e.get("customData") or {}).get("value_of") == "sched"]

    def test_add_with_value_composes_a_value_row(self):
        self.seed(value="weekdays 06:00")
        self.assertEqual(self.value_rows(), ["weekdays 06:00"])

    def test_add_without_value_stays_a_bare_field(self):
        self.seed()
        self.assertEqual(self.value_rows(), [])

    def test_mod_value_retexts_in_place_and_narrates(self):
        self.seed(value="weekdays 06:00")
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "wf",
            "ops": [{"op": "mod", "id": "sched",
                     "attrs": {"value": "weekdays 05:30"}}]})
        self.assertEqual(self.value_rows(), ["weekdays 05:30"])
        self.assertIn("value_changed",
                      [f["fact"] for f in rec["artifacts"]["wf"]["facts"]])

    def test_mod_value_on_a_plain_block_still_errors(self):
        self.seed()
        self.store.apply_batch({
            "base_revn": 1, "artifact": "wf",
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "blk", "kind": "block",
                "label": "Notes", "x": 400, "y": 100, "width": 160,
                "height": 60}}]})
        with self.assertRaises(canvas.BatchError) as cm:
            self.store.apply_batch({
                "base_revn": 2, "artifact": "wf",
                "ops": [{"op": "mod", "id": "blk", "attrs": {"value": "x"}}]})
        self.assertIn("kind:input", "\n".join(cm.exception.errors))


class TestHeldRevisions(Base):
    """The pending-revision queue (v0.5).

    A held batch used to skip validation entirely: `apply` answered
    `queued: true` for a batch that could never land, the agent narrated
    the drawing as done, and the user met the validator error minutes
    later. A failed pull then left the entry armed, so the same error came
    back on every click with no way to discard it.
    """

    def setUp(self):
        super().setUp()
        self.app = canvas.ServerApp(self.project)
        self.store = self.app.store
        self.store.apply_batch(seed_flow_batch())
        self.app.store.config["canvas_updates"] = "pulled"

    def tearDown(self):
        self.app.log_file.close()
        super().tearDown()

    def post(self, path, body):
        """Call a POST route, turning its _Err into a payload dict."""
        try:
            return self.app.handle_post(path, body)
        except canvas._Err as e:
            return dict(e.payload, status=e.status)

    def apply(self, batch):
        return self.post("/api/apply", batch)

    def batch(self, ops, **kw):
        b = {"base_revn": self.store.head_revn(),
             "artifact": "checkout-flow", "ops": ops}
        b.update(kw)
        return b

    def test_invalid_ops_rejected_at_queue_time(self):
        # the live failure: an attribute that does not exist.
        # This used to use `attributes`, which was genuinely unknown to
        # `mod` — so the test encoded BUG-04 as intended behaviour. It is
        # a real mod attribute since v0.7; use one that is not.
        r = self.apply(self.batch([
            {"op": "mod", "id": "payment",
             "attrs": {"nonesuch": {"x": 1}}}]))
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("status"), 422)
        self.assertIn("unknown attribute", r["error"])
        self.assertEqual(self.app.pending, [])

    def test_a_bad_attributes_value_is_still_rejected_at_queue_time(self):
        r = self.apply(self.batch([
            {"op": "mod", "id": "payment",
             "attrs": {"attributes": {"x": 1}}}]))
        self.assertFalse(r.get("ok"))
        self.assertIn("must be a list of strings", r["error"])
        self.assertEqual(self.app.pending, [])

    def test_invalid_registry_op_rejected_at_queue_time(self):
        # the second live failure — a registry op, not a drawing op
        r = self.apply(self.batch(
            [{"op": "mod", "id": "payment", "attrs": {"x": 500}},
             {"op": "registry", "action": "set_budget",
              "artifact": "checkout-flow", "arrows": -1,
              "reason": "no"}]))
        self.assertFalse(r.get("ok"))
        self.assertIn("positive integer", r["error"])
        self.assertEqual(self.app.pending, [])

    def test_check_batch_does_not_mutate_the_registry(self):
        before = json.dumps(self.store.registry, sort_keys=True)
        self.store.check_batch(self.batch(
            [{"op": "mod", "id": "payment", "attrs": {"x": 500}},
             {"op": "registry", "action": "upsert_concept",
              "name": "Refund"}]))
        self.assertEqual(json.dumps(self.store.registry, sort_keys=True),
                         before)

    def test_queued_response_carries_echo_and_layout(self):
        r = self.apply(self.batch([
            {"op": "mod", "id": "payment", "attrs": {"label": "Pay"}}]))
        self.assertTrue(r["queued"])
        self.assertTrue(r["intent_echo"])
        for k in ("layout_errors", "layout_warnings", "layout_notes"):
            self.assertIn(k, r)

    def test_failed_pull_evicts_instead_of_re_arming(self):
        r = self.apply(self.batch([
            {"op": "mod", "id": "payment", "attrs": {"x": 500}}]))
        pid = r["pending_id"]
        # the user deletes the element the held batch targets
        els = [e for e in self.scene() if e["id"] != "payment"]
        self.store.commit(author="user", new_scenes={"checkout-flow": els},
                          base_revn=self.store.head_revn())
        out = self.post("/api/pending/resolve",
                        {"id": pid, "action": "apply_now"})
        self.assertFalse(out.get("ok"))
        self.assertEqual(self.app.pending, [])

    def test_discard_removes_the_entry(self):
        pid = self.apply(self.batch([
            {"op": "mod", "id": "payment", "attrs": {"x": 500}}]))["pending_id"]
        out = self.post("/api/pending/resolve",
                        {"id": pid, "action": "discard"})
        self.assertTrue(out["discarded"])
        self.assertEqual(self.app.pending, [])

    def test_unknown_action_names_discard(self):
        pid = self.apply(self.batch([
            {"op": "mod", "id": "payment", "attrs": {"x": 500}}]))["pending_id"]
        out = self.post("/api/pending/resolve",
                        {"id": pid, "action": "nope"})
        self.assertIn("discard", out["error"])

    def test_supersedes_replaces_rather_than_stacks(self):
        first = self.apply(self.batch([
            {"op": "mod", "id": "payment", "attrs": {"x": 500}}]))
        second = self.apply(self.batch(
            [{"op": "mod", "id": "payment", "attrs": {"x": 520}}],
            supersedes=first["pending_id"]))
        self.assertEqual([p["id"] for p in self.app.pending],
                         [second["pending_id"]])

    def test_pin_only_revisions_still_bypass_the_queue(self):
        r = self.apply(self.batch([
            {"op": "pin", "target": "payment", "id": "pin-pay",
             "question": "Card only?"}]))
        self.assertTrue(r["ok"])
        self.assertNotIn("queued", r)


class TestPulledCadenceIsNotBlind(Base):
    """Under `pulled`, a queued batch still owes the standing nags, and
    the session clock still runs (v0.7 WP2).

    `cmd_apply`'s queued branch returned before the nag block — the third
    defect on those same eight lines in three assessments, with the
    comment above it recording v0.4 patching one of them (r3-6). The
    server's queued response carried no debt at all, so fixing the early
    return alone would have printed nothing.

    And because `round` advances inside `commit`, a cadence where nothing
    commits froze it, taking pin ageing with it: a question sat open
    across turns still reading "age 0r" (r3-2).
    """

    def setUp(self):
        super().setUp()
        self.app = canvas.ServerApp(self.project)
        self.store = self.app.store
        self.store.apply_batch(seed_flow_batch())
        self.store.config["canvas_updates"] = "pulled"

    def tearDown(self):
        self.app.log_file.close()
        super().tearDown()

    def apply(self, ops, **kw):
        b = {"base_revn": self.store.head_revn(),
             "artifact": "checkout-flow", "ops": ops}
        b.update(kw)
        try:
            return self.app.handle_post("/api/apply", b)
        except canvas._Err as e:
            return dict(e.payload, status=e.status)

    def queue_a_drawing(self):
        return self.apply([{"op": "mod", "id": "payment", "attrs": {"x": 500}}])

    # ---- r3-6: the queued response carries the standing nags ---------

    def test_queued_response_carries_the_debt_keys(self):
        r = self.queue_a_drawing()
        self.assertTrue(r["queued"])
        for k in ("lint_debt", "pin_debt", "open_tripwires", "branch"):
            self.assertIn(k, r)

    def test_the_committed_response_carries_them_too(self):
        self.store.config["canvas_updates"] = "per-round"
        r = self.apply([{"op": "mod", "id": "payment", "attrs": {"x": 500}}])
        for k in ("lint_debt", "pin_debt", "open_tripwires", "branch"):
            self.assertIn(k, r)

    def test_a_queued_pin_still_shows_in_the_debt(self):
        self.apply([{"op": "pin", "target": "payment", "id": "pin-pay",
                     "question": "Card only?"}])
        r = self.queue_a_drawing()
        self.assertIn("pin-pay", [p["id"] for p in r["pin_debt"]])

    # ---- r3-2: the clock runs while the batch waits ------------------

    def user_has_just_saved(self):
        """The precondition: a user save leaves whose_move on the agent.

        The seed batch is an AGENT revision, which already flips it to
        the user, so there is nothing to observe from there.
        """
        self.store.registry["whose_move"] = "agent"

    def test_whose_move_flips_when_the_batch_is_queued(self):
        self.user_has_just_saved()
        self.assertEqual(self.app.api_state()["whose_move"], "agent")
        self.queue_a_drawing()
        self.assertEqual(self.app.api_state()["whose_move"], "user")

    def test_the_round_advances_when_the_batch_is_queued(self):
        before = self.app.api_state()["round"]
        self.queue_a_drawing()
        self.assertEqual(self.app.api_state()["round"], before + 1)

    def test_a_pin_ages_while_it_waits(self):
        self.apply([{"op": "pin", "target": "payment", "id": "pin-pay",
                     "question": "Card only?"}])

        def aged():
            return next(p for p in self.app.api_state()["pin_debt"]
                        if p["id"] == "pin-pay")["age_rounds"]

        self.assertEqual(aged(), 0)
        self.queue_a_drawing()
        self.assertEqual(aged(), 1)

    def test_discarding_the_queue_puts_the_clock_back(self):
        # derived, not written: the whole reason for deriving it
        before = self.app.api_state()
        pid = self.queue_a_drawing()["pending_id"]
        self.app.handle_post("/api/pending/resolve",
                             {"id": pid, "action": "discard"})
        after = self.app.api_state()
        self.assertEqual(after["round"], before["round"])
        self.assertEqual(after["whose_move"], before["whose_move"])

    def test_the_committed_round_is_untouched(self):
        # registry.json must never record a round nothing committed in
        self.queue_a_drawing()
        self.assertEqual(self.store.registry["round"],
                         self.app.api_state()["committed_round"])
        self.assertEqual(self.app.api_state()["round"],
                         self.store.registry["round"] + 1)

    def test_a_pin_only_revision_is_not_an_unanswered_turn(self):
        # the silent half. A pin-only revision never holds behind the
        # banner — it COMMITS — so whose_move moves for the ordinary
        # reason and the derivation must add nothing on top.
        self.user_has_just_saved()
        before = self.app.api_state()["round"]
        r = self.apply([{"op": "pin", "target": "payment", "id": "pin-pay",
                         "question": "Card only?"}])
        self.assertNotIn("queued", r)
        self.assertFalse(self.app.queued_turn())
        self.assertEqual(self.app.api_state()["round"], before)
        self.assertEqual(self.app.api_state()["round"],
                         self.store.registry["round"])


class TestTheQueuedEchoIsTheEchoWithheld(Base):
    """A held batch hands back the whole reading (v0.9 WP3, r5-6).

    A queued batch is fully validated at queue time, so the echo, the
    lint findings and the notes all exist by the time the acknowledgement
    is written. The notes were the half nobody handed over: the
    already-gone-glyph NOTE is produced only by a `resolve_pin`, the
    shape SKILL.md documents rides a drawing op, and a batch carrying a
    drawing op is exactly the batch `pulled` holds — so the one cadence
    where that note matters most was the one cadence that never printed
    it (Task 7 review I1).
    """

    # every key of the reading an apply response owes, so a surface that
    # grows a sixth line has to answer for it on both paths at once
    READING = ("intent_echo", "notes", "layout_errors", "layout_warnings",
               "layout_notes")

    def setUp(self):
        super().setUp()
        self.app = canvas.ServerApp(self.project)
        self.store = self.app.store
        self.store.apply_batch(seed_flow_batch())
        self.store.config["canvas_updates"] = "pulled"

    def tearDown(self):
        self.app.log_file.close()
        super().tearDown()

    def apply(self, ops, **kw):
        b = {"base_revn": self.store.head_revn(),
             "artifact": "checkout-flow", "ops": ops}
        b.update(kw)
        try:
            return self.app.handle_post("/api/apply", b)
        except canvas._Err as e:
            return dict(e.payload, status=e.status)

    def both_ways(self, ops):
        """Queue the batch, throw the queue away, then apply the same one.

        Nothing commits while a batch is held, so the second run reads
        the very head the first was answered against — which is what
        makes the two readings comparable rather than merely similar.

        Args:
            ops: The op list to send twice.

        Returns:
            `(queued_response, applied_response)`.
        """
        queued = self.apply(ops)
        self.app.handle_post("/api/pending/resolve",
                             {"id": queued["pending_id"],
                              "action": "discard"})
        self.store.config["canvas_updates"] = "per-round"
        return queued, self.apply(ops)

    def gone_glyph_batch(self):
        """One ❓ the user has already deleted, and the batch answering it.

        Returns:
            Ops that draw and resolve in one batch — a drawing op is
            what makes it hold behind the banner at all, since a
            pin-only revision commits under either cadence.
        """
        self.apply([{"op": "pin", "target": "payment", "id": "pin-pay",
                     "question": "Card only?"}])
        self.store.commit(
            author="user", base_revn=self.store.head_revn(),
            new_scenes={"checkout-flow": [e for e in self.scene()
                                          if e["id"] != "pin-pay"]})
        return [{"op": "mod", "id": "payment", "attrs": {"label": "Pay"}},
                {"op": "resolve_pin", "id": "pin-pay"}]

    def test_the_queued_reading_equals_the_applied_one(self):
        queued, applied = self.both_ways(
            [{"op": "mod", "id": "payment", "attrs": {"label": "Pay"}}])
        self.assertTrue(queued["queued"])
        self.assertTrue(queued["intent_echo"])
        for k in self.READING:
            self.assertEqual(queued[k], applied[k], k)

    def test_a_held_resolve_of_a_vanished_glyph_says_so_at_queue_time(self):
        r = self.apply(self.gone_glyph_batch())
        self.assertTrue(r["queued"])
        self.assertEqual(r["notes"],
                         ["pin pin-pay resolved; its ❓ was already gone"])

    def test_and_says_it_again_when_the_user_pulls_it(self):
        # the queue-time note is provisional — the head can move under a
        # held batch — so the apply that finally lands owes the same
        # sentence for real, on the response its caller prints
        r = self.apply(self.gone_glyph_batch())
        out = self.app.handle_post("/api/pending/resolve",
                                   {"id": r["pending_id"],
                                    "action": "apply_now"})
        self.assertEqual(out["notes"], r["notes"])
        self.assertTrue(out["intent_echo"])

    def test_the_whole_reading_matches_for_a_resolve_too(self):
        queued, applied = self.both_ways(self.gone_glyph_batch())
        for k in self.READING:
            self.assertEqual(queued[k], applied[k], k)

    def test_a_rejected_batch_returns_errors_and_no_reading(self):
        # the control: an echo of a batch that was refused would be a
        # reading of a revision nobody will ever see
        r = self.apply([{"op": "mod", "id": "payment",
                         "attrs": {"nonesuch": {"x": 1}}}])
        self.assertEqual(r.get("status"), 422)
        self.assertIn("unknown attribute", r["error"])
        for k in self.READING:
            self.assertNotIn(k, r)
        self.assertEqual(self.app.pending, [])


class TestQueuedLinesSayTheyAreProvisional(unittest.TestCase):
    """`ECHO(queued)=` — a reading of a revision that has not happened.

    Both branches of `cmd_apply` print through one helper, so a queued
    reading came out byte-identical to a committed one: an agent
    skimming for `ECHO=` had `QUEUED=true` two lines up and nothing on
    the lines themselves to say the head could still move under them.
    """

    def lines(self, **kw):
        """Print one response carrying every line the helper can emit."""
        resp = {"intent_echo": ["op 0 (mod): payment moved"],
                "consequences": ["arrow t3 lost its start binding"],
                "notes": ["pin pin-a resolved; its ❓ was already gone"],
                "layout_errors": ["e"], "layout_warnings": ["w"],
                "layout_notes": ["n"]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            canvas._print_layout(resp, **kw)
        return buf.getvalue().splitlines()

    def test_every_line_of_a_held_reading_is_marked(self):
        self.assertEqual(self.lines(queued=True), [
            "ECHO(queued)=op 0 (mod): payment moved",
            "CONSEQUENCE(queued)=arrow t3 lost its start binding",
            "NOTE(queued)=pin pin-a resolved; its ❓ was already gone",
            "LAYOUT_ERROR(queued)=e", "LAYOUT_WARNING(queued)=w",
            "LAYOUT_NOTE(queued)=n"])

    def test_a_committed_reading_is_not(self):
        # the silent half: the ordinary keys stay exactly as they were
        self.assertEqual(self.lines(), [
            "ECHO=op 0 (mod): payment moved",
            "CONSEQUENCE=arrow t3 lost its start binding",
            "NOTE=pin pin-a resolved; its ❓ was already gone",
            "LAYOUT_ERROR=e", "LAYOUT_WARNING=w", "LAYOUT_NOTE=n"])


class TestApplyPrintsWhichKindOfReadingItGot(Base):
    """`cmd_apply` picks the marking, so the helper cannot be the only
    thing tested — a call site that forgot the flag would still pass.

    The server is patched rather than run: `server_alive` is an HTTP GET
    and this measures what the command prints, not what a socket does.
    """

    def run_apply(self, resp):
        """Drive `cmd_apply` against a canned server response.

        Args:
            resp: The payload `/api/apply` is pretending to return.

        Returns:
            The command's stdout, split into lines.
        """
        path = self.tmp / "batch.json"
        path.write_text(json.dumps(
            {"artifact": "checkout-flow", "ops": []}), encoding="utf-8")
        self.project.state_path.write_text(json.dumps(
            {"url": "http://127.0.0.1:1/", "port": 1, "pid": 1,
             "protocol_version": canvas.PROTOCOL_VERSION}), encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(canvas, "server_alive", lambda s: True), \
                mock.patch.object(canvas, "http_json",
                                  lambda *a, **kw: resp), \
                contextlib.redirect_stdout(buf):
            canvas.cmd_apply(argparse.Namespace(
                project=self.tmp, file=str(path), check=False, render=False))
        return buf.getvalue().splitlines()

    def test_a_queued_response_prints_a_marked_reading(self):
        out = self.run_apply(
            {"ok": True, "queued": True, "pending_id": 1,
             "intent_echo": ["op 0 (mod): payment moved"],
             "notes": ["pin pin-a resolved; its ❓ was already gone"]})
        self.assertIn("QUEUED=true", out)
        self.assertIn("ECHO(queued)=op 0 (mod): payment moved", out)
        self.assertIn("NOTE(queued)=pin pin-a resolved; its ❓ was already "
                      "gone", out)

    def test_a_committed_response_prints_a_plain_one(self):
        out = self.run_apply(
            {"ok": True, "revn": 2, "short_id": "abc",
             "intent_echo": ["op 0 (mod): payment moved"]})
        self.assertIn("ECHO=op 0 (mod): payment moved", out)


class TestTheCadenceIsOnTheResumeSurfaces(Base):
    """`start` and `status` say which cadence is in force (v0.9 WP3).

    Whether a batch commits or waits behind a banner decides how every
    apply response should be read, and neither resume surface printed
    it: the agent had to GET api/state and dig `config` out of the
    payload, or infer a silently flipped toggle from the shape of the
    answer it got back.
    """

    def with_server(self, fn, st=None):
        """Run a CLI command against a live-looking server.

        Args:
            fn: The command function, called with a project namespace.
            st: The `/api/state` payload to answer with.

        Returns:
            Stdout, split into lines.
        """
        self.project.state_path.write_text(json.dumps(
            {"url": "http://127.0.0.1:1/", "port": 1, "pid": 1,
             "protocol_version": canvas.PROTOCOL_VERSION,
             "catchup_revn": 0}), encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(canvas, "server_alive", lambda s: True), \
                mock.patch.object(canvas, "http_json",
                                  lambda *a, **kw: st or {}), \
                contextlib.redirect_stdout(buf):
            fn()
        return buf.getvalue().splitlines()

    def status_lines(self, cadence):
        return self.with_server(
            lambda: canvas.cmd_status(argparse.Namespace(project=self.tmp)),
            st={"config": {"canvas_updates": cadence}})

    def start_lines(self, cfg):
        self.project.config_path.write_text(json.dumps(cfg),
                                            encoding="utf-8")
        return self.with_server(lambda: canvas.cmd_start(
            argparse.Namespace(project=self.tmp, no_browser=True)))

    def test_status_names_a_pulled_cadence(self):
        self.assertIn("CADENCE=pulled", self.status_lines("pulled"))

    def test_status_names_a_per_round_one(self):
        self.assertIn("CADENCE=per-round", self.status_lines("per-round"))

    def test_start_names_the_cadence_too(self):
        self.assertIn("CADENCE=pulled",
                      self.start_lines({"canvas_updates": "pulled"}))

    def test_a_value_the_loader_would_repair_reads_as_the_default(self):
        # the loader resets an unknown cadence to per-round and rewrites
        # the file (CFG-008), so printing the raw value would announce a
        # cadence no server is running
        self.assertIn("CADENCE=per-round",
                      self.start_lines({"canvas_updates": "whenever"}))


class TestArrowBindingLints(unittest.TestCase):
    """Both directions of "this arrow does not point where it says".

    r3-13: deleting a node orphans its bound arrows, and the loop
    `continue`d past exactly the broken ones while raising an ERROR for
    a binding merely 26px off. r3-16: the guard tested containment in a
    bbox grown by TOL, which a point deep INSIDE satisfies trivially, so
    three arrowheads stopping 56px in passed while one 26px out was the
    artifact's only ERROR.
    """

    def scene(self, ax, ay, ex, ey, drop_target=False):
        node = {"id": "n1", "type": "rectangle", "x": 100, "y": 100,
                "width": 200, "height": 100,
                "customData": {"role": "node"}}
        far = {"id": "n2", "type": "rectangle", "x": 600, "y": 100,
               "width": 200, "height": 100,
               "customData": {"role": "node"}}
        arrow = {"id": "a1", "type": "arrow", "x": ax, "y": ay,
                 "width": ex - ax, "height": ey - ay,
                 "points": [[0, 0], [ex - ax, ey - ay]],
                 "startBinding": {"elementId": "n1", "focus": 0, "gap": 6},
                 "endBinding": {"elementId": "n2", "focus": 0, "gap": 6},
                 "customData": {}}
        arrow["customData"]["routed"] = canvas._route_sig(arrow)
        return [far, arrow] if drop_target else [node, far, arrow]

    def errors(self, els):
        return canvas.lint_layout(els)["errors"]

    # ---- r3-13 -------------------------------------------------------

    def test_a_binding_to_a_deleted_element_is_an_error(self):
        errs = self.errors(self.scene(300, 150, 600, 150, drop_target=True))
        self.assertTrue(any("no longer exists" in m for m in errs), errs)

    def test_it_names_the_arrow_the_side_and_the_missing_element(self):
        m = next(m for m in
                 self.errors(self.scene(300, 150, 600, 150, drop_target=True))
                 if "no longer exists" in m)
        self.assertIn("a1", m)
        self.assertIn("n1", m)
        self.assertIn("start", m)

    def test_a_live_binding_says_nothing(self):
        # the silent half
        self.assertEqual(self.errors(self.scene(300, 150, 600, 150)), [])

    # ---- r3-16 -------------------------------------------------------

    def test_an_endpoint_deep_inside_its_box_is_an_error(self):
        # start at (250,150): 50px inside n1's right edge at x=300
        errs = self.errors(self.scene(250, 150, 600, 150))
        self.assertTrue(any("inside the shape" in m for m in errs), errs)

    def test_an_endpoint_on_the_border_stays_silent(self):
        # the silent half, and the one that matters: a bound endpoint
        # belongs ON the border and must not now be flagged
        self.assertEqual(self.errors(self.scene(300, 150, 600, 150)), [])

    def test_inside_by_less_than_the_tolerance_stays_silent(self):
        self.assertEqual(self.errors(self.scene(290, 150, 600, 150)), [])

    def test_outside_still_fires_and_still_says_away(self):
        errs = self.errors(self.scene(340, 150, 600, 150))
        self.assertTrue(any("away" in m for m in errs), errs)


class TestSharedAttachPointNamesTheRealReason(unittest.TestCase):
    """The warning must not assert a cause it never measured.

    It said "auto-fan couldn't separate them (obstacles in every slot)"
    from a check that only tests whether two attach points are within
    12px — no obstacle set is in scope. On the one case anyone
    reproduced (brownfield algorithm-refinements revns 56-59) the stated
    cause was also wrong: deleting all three decorations left the
    warning standing, and what cleared it at revn 60 was an arrow
    dropping from 4 points to 3, because fan_attach_points only moves
    2- and 3-point server-routed paths (BUG-05).
    """

    def scene(self, pts_b):
        node = {"id": "src", "type": "diamond", "x": 100, "y": 100,
                "width": 160, "height": 80, "customData": {"role": "node"}}
        out = [node]
        for i, (aid, pts) in enumerate((("a1", [[0, 0], [200, 0]]),
                                        ("a2", pts_b))):
            dst = {"id": "d%d" % i, "type": "rectangle", "x": 500,
                   "y": 100 + i * 200, "width": 160, "height": 80,
                   "customData": {"role": "node"}}
            arrow = {"id": aid, "type": "arrow", "x": 260, "y": 140,
                     "width": 200, "height": 0, "points": pts,
                     "startBinding": {"elementId": "src", "focus": 0,
                                      "gap": 6},
                     "endBinding": {"elementId": dst["id"], "focus": 0,
                                    "gap": 6},
                     "customData": {}}
            arrow["customData"]["routed"] = canvas._route_sig(arrow)
            out += [dst, arrow]
        return out

    def warning(self, els):
        return next((w for w in canvas.lint_layout(els)["warnings"]
                     if "share an attach point" in w), None)

    def test_it_no_longer_blames_obstacles(self):
        w = self.warning(self.scene([[0, 0], [60, 40], [140, 60], [200, 80]]))
        self.assertIsNotNone(w)
        self.assertNotIn("obstacles in every slot", w)

    def test_it_names_the_waypoint_count_that_disqualified_the_arrow(self):
        w = self.warning(self.scene([[0, 0], [60, 40], [140, 60], [200, 80]]))
        self.assertIn("a2", w)
        self.assertIn("4 waypoints", w)

    def test_a_user_shaped_arrow_is_named_as_such(self):
        els = self.scene([[0, 0], [200, 80]])
        for e in els:
            if e.get("id") == "a2":
                e["customData"]["routed"] = "stale-signature"
        w = self.warning(els)
        self.assertIn("user-shaped", w)

    def test_arrows_that_do_not_share_a_point_say_nothing(self):
        # the silent half
        els = self.scene([[0, 0], [200, 80]])
        for e in els:
            if e.get("id") == "a2":
                e["y"] = 400
        self.assertIsNone(self.warning(els))


class TestRewireNarratesDiscardingUserGeometry(Base):
    """A rewire replaces a hand-drawn path by design — say so (BUG-06).

    `server_owns_geometry` returns false on a user drag, so `tidy` and
    the move-repair pass already leave it alone; the rewrite on rewire is
    deliberate ("a rewire is a new path request"). What was missing is
    that nothing narrated the discard, so one arrow re-dragged four times
    read to the agent as user indecision.
    """

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def facts(self, rec):
        return [f["fact"] for a in rec["artifacts"].values()
                for f in a["facts"]]

    def user_shapes_t2(self):
        for e in self.store.scenes["checkout-flow"]:
            if e["id"] == "t2":
                e["customData"] = dict(e.get("customData") or {},
                                       routed="a-stale-signature")

    def rewire_t2(self):
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "checkout-flow",
            "ops": [{"op": "mod", "id": "t2",
                     "attrs": {"from": "checkout", "to": "confirm"}}]})
        return rec

    def test_the_discard_is_narrated(self):
        self.user_shapes_t2()
        self.assertIn("user_route_replaced", self.facts(self.rewire_t2()))

    def test_it_reaches_the_headline(self):
        self.user_shapes_t2()
        self.assertIn("replacing the path you drew by hand",
                      self.rewire_t2()["summary"]["headline"])

    def test_a_server_routed_arrow_is_not_narrated(self):
        # the silent half: re-routing our own geometry discards nothing
        self.assertNotIn("user_route_replaced", self.facts(self.rewire_t2()))


class TestPrintStanding(unittest.TestCase):
    """The shared apply epilogue (v0.7 WP2).

    One function on every exit path, so an early return can no longer
    take a subset of the contract with it (r3-6).
    """

    def lines(self, resp):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            canvas._print_standing(resp)
        return buf.getvalue().splitlines()

    def test_a_non_main_branch_is_named(self):
        # a user save toasts its branch; an agent revision named none
        self.assertIn("BRANCH=alt-0019",
                      self.lines({"branch": "alt-0019"}))

    def test_main_is_not_named(self):
        self.assertEqual(self.lines({"branch": "main"}), [])

    def test_standing_tripwires_carry_their_question(self):
        out = self.lines({"open_tripwires": [
            {"id": "tw-20-1", "question": "Divergence, or propagate?"}]})
        self.assertEqual(out, ["OPEN_TRIPWIRE=tw-20-1 "
                               "Divergence, or propagate?"])

    def test_standing_tripwires_are_capped_with_a_tail(self):
        n = canvas.STANDING_TRIPWIRE_CAP + 3
        out = self.lines({"open_tripwires": [
            {"id": "tw-%d" % i, "question": "q"} for i in range(n)]})
        self.assertEqual(len(out), canvas.STANDING_TRIPWIRE_CAP + 1)
        self.assertIn("+3 more", out[-1])

    def test_this_batchs_tripwires_are_separate_from_standing_ones(self):
        out = self.lines({"tripwires": [{"id": "tw-1", "question": "fired"}],
                          "open_tripwires": [{"id": "tw-0",
                                              "question": "standing"}]})
        self.assertEqual(out, ["TRIPWIRE=tw-1 fired",
                               "OPEN_TRIPWIRE=tw-0 standing"])

    def test_an_empty_response_prints_nothing(self):
        self.assertEqual(self.lines({}), [])


class TestArrowLabelAnchor(Base):
    """Where a bound arrow label is drawn, and the lints that read it.

    The client re-centres a text bound to an arrow onto the path's
    arc-length midpoint and discards the stored x/y. Until v0.6 the
    seeder anchored on the longest segment's midpoint with an 8px
    perpendicular lift, so stored position, exported SVG and live canvas
    all disagreed on an elbow — and a label sitting inside a foreign box
    linted clean (v0.5 assessment R2-8).
    """

    def elbow(self, label, pts, ax=0, ay=0):
        """An elbow arrow carrying `label`, plus its bound text."""
        arrow = {"id": "a1", "type": "arrow", "x": ax, "y": ay,
                 "width": 0, "height": 0, "points": pts,
                 "startBinding": {"elementId": "src"},
                 "endBinding": {"elementId": "dst"}}
        text = {"id": "a1-label", "type": "text", "x": -999, "y": -999,
                "width": 60, "height": 20, "text": label,
                "originalText": label, "containerId": "a1"}
        return arrow, text

    def test_anchor_is_the_clients_middle_vertex_not_an_arc_walk(self):
        """The client branches on POINT COUNT, not on arc length.

        Long horizontal run, short vertical drop. An arc-length walk
        puts the label 250px along the horizontal leg; the client puts
        it on `points[1]`, the corner, because the count is odd — and
        the leg lengths never enter. The two answers are 150px apart,
        measured against the running app at 0.0000px for the second
        (v0.9 blind-spot 1 spike).
        """
        arrow, text = self.elbow("x", [[0, 0], [400, 0], [400, 100]])
        x, y = canvas.arrow_label_anchor(arrow, text)
        self.assertAlmostEqual(x + 30, 400, delta=1)
        self.assertAlmostEqual(y + 10, 0, delta=1)

    def test_recenter_label_writes_the_slot(self):
        # the corner IS the anchor here, so the bias fires and what
        # lands in the store is the slid position, 18px down the
        # vertical leg (hh + LABEL_CORNER_PAD, the nearest clearance)
        arrow, text = self.elbow("x", [[0, 0], [400, 0], [400, 100]])
        canvas.recenter_label([arrow, text], arrow)
        self.assertAlmostEqual(text["x"] + 30, 400, delta=1)
        self.assertAlmostEqual(text["y"] + 10, 18, delta=1)

    def test_straight_arrow_anchor_is_its_middle(self):
        arrow, text = self.elbow("x", [[0, 0], [200, 0]])
        x, _ = canvas.arrow_label_anchor(arrow, text)
        self.assertAlmostEqual(x + 30, 100, delta=1)

    def test_label_landing_on_a_foreign_box_warns(self):
        """The R2-8 shape: the DRAWN label falls inside a foreign box.

        Pin A of the blind-spot 1 spike, and it needs no curvature. The
        foreign box sits at `x >= 405`, deliberately clear of the
        stroke's own `x <= 400`, so it touches neither leg and cannot be
        caught by the unrelated "arrow passes through" check — the trap
        that would have made this pin pass for the wrong reason. What it
        does overlap is the label the client draws on the corner, by
        25px in x and 20px in y. On the arc-length anchor this scene was
        silent (observed live in `spike-row26-verify`).
        """
        arrow, text = self.elbow("numbers", [[0, 0], [400, 0], [400, 200]])
        canvas.recenter_label([arrow, text], arrow)
        els = [
            {"id": "src", "type": "rectangle", "x": -200, "y": -30,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            {"id": "dst", "type": "rectangle", "x": 340, "y": 240,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            {"id": "foreign", "type": "rectangle", "x": 405, "y": -40,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            arrow, text]
        warns = canvas.lint_layout(els, artifact_type="flow")["warnings"]
        self.assertTrue(any("arrow label" in w and "neither end" in w
                            for w in warns), warns)

    def test_a_biased_label_still_warns_on_the_foreign_box_it_is_drawn_in(
            self):
        """F1: the corner bias must not buy silence from this check.

        The v0.9 WP4 bias slides the STORED anchor off a turn so the
        export stops painting over the elbow. The client does not follow
        — it re-centres on the arc midpoint and discards our x/y — so on
        a balanced elbow the two positions genuinely differ, and a check
        reading only the stored one goes quiet on a label the user can
        see sitting inside a foreign box. That is R2-8 verbatim, and it
        is what the first round of this fix shipped: measured here, the
        stored box spans x 132..192 (clear of the foreign box) while the
        drawn box spans 170..230 (inside it).

        The check now measures BOTH positions and fires on either. The
        scene is the reviewer's: a 200+200 elbow, balanced, so the
        client's anchor lands exactly on the turn and the bias is
        maximal. Since v0.9 the slide is the SHORTEST one that clears
        (18px down the vertical leg, not 38px back along the
        horizontal), so the two channels now come apart in y rather than
        in x — the foreign box is placed to catch the canvas one alone.
        """
        arrow, text = self.elbow("numbers", [[0, 0], [200, 0], [200, 200]])
        canvas.recenter_label([arrow, text], arrow)
        drawn = canvas.arrow_label_anchor(arrow, text)
        # the premise: the two positions really have come apart here, or
        # this scene would pass for reasons that have nothing to do with
        # the fix
        self.assertNotAlmostEqual(text["y"], drawn[1], delta=1)
        els = [
            {"id": "src", "type": "rectangle", "x": -200, "y": -30,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            {"id": "dst", "type": "rectangle", "x": 140, "y": 240,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            {"id": "foreign", "type": "rectangle", "x": 196, "y": -45,
             "width": 160, "height": 50, "customData": {"role": "node"}},
            arrow, text]
        warns = canvas.lint_layout(els, artifact_type="flow")["warnings"]
        self.assertTrue(any("arrow label" in w and "neither end" in w
                            for w in warns), warns)

    def test_a_balanced_long_elbow_is_biased_too(self):
        """F3: length is not the predicate — leg imbalance is.

        The class above only ever exercised 400+100 and 400+200 elbows,
        which are lopsided enough that the arc midpoint clears the turn
        on its own, so nothing here entered the biasing regime at all.
        The client's anchor on an ODD-point path IS its turn at any
        scale and at any imbalance: this 400+400 path is biased by
        exactly the same 18px as a 200+200 one, which is what makes
        "long elbows are unaffected" false.
        """
        arrow, text = self.elbow("x", [[0, 0], [400, 0], [400, 400]])
        self.assertAlmostEqual(canvas.arrow_label_anchor(arrow, text)[0] + 30,
                               400, delta=1)
        x, y = canvas.arrow_label_slot(arrow, text)
        # the NEAREST clearing position on the drawn path, which is 18px
        # (hh + pad) down the vertical leg — not 38px (hw + pad) back
        # along the horizontal one, which also clears but is further
        self.assertAlmostEqual(x + 30, 400, delta=1)
        self.assertAlmostEqual(y + 10, 18, delta=1)

    def test_the_slide_stays_in_the_offending_turns_neighbourhood(self):
        """Ruling 1, re-derived: the slide is MINIMAL over the path.

        The reviewer's five-point path. Its longest segments are the two
        400px horizontal limbs, and the turn under the label is between
        two shorter ones — so "longest overall" put the anchor on a limb
        with nothing to do with the turn, 203.6px from the anchor against
        a claimed bound of 38. The adjacency rule that replaced it made
        the bound true by construction; the arc-length scan makes it true
        by a stronger property, since it stops at the FIRST clearing
        position walking outward and so cannot reach a far limb before a
        near one.

        THE LAST VERTEX MOVED from (400, 400) to (600, 400) on
        2026-08-16 (v0.9 TASK-MICROFIX), and the reason is this test's
        own subject rather than its arithmetic: every number below is
        unchanged, because the anchor, the host limb and the 18px
        clearance are all decided by the geometry ABOVE the moved point.
        What changed is that the vertex under the label is now a real
        45-degree turn. In the original path it was collinear — (400, 0)
        to (400, 200) to (400, 400) runs dead straight — and once
        `_label_off_corner` learned to test vertices for an actual turn,
        the bias correctly stopped firing on it and this test went on
        passing while measuring a slide that no longer happened. A
        minimality pin whose fixture triggers no slide asserts nothing;
        the blind-spot-4 spike flagged this exact fixture as the one
        anchoring Ruling 1's guarantee, so it is repaired rather than
        retired.
        """
        arrow, text = self.elbow(
            "x", [[0, 0], [400, 0], [400, 200], [600, 400], [200, 400]])
        mx, my = canvas.arrow_label_anchor(arrow, text)
        x, y = canvas.arrow_label_slot(arrow, text)
        self.assertEqual((mx + 30, my + 10), (400, 200))
        self.assertAlmostEqual(x + 30, 400, delta=1)   # same vertical limb
        self.assertAlmostEqual(y + 10, 182, delta=1)   # 18px = hh + pad
        self.assertLessEqual(((x - mx) ** 2 + (y - my) ** 2) ** 0.5, 18.5)

    def test_label_on_its_own_endpoint_is_silent(self):
        # differential control: the same geometry, but the box under the
        # label IS the arrow's destination — that is not a mislabel
        arrow, text = self.elbow("numbers", [[0, 0], [400, 0]])
        canvas.recenter_label([arrow, text], arrow)
        els = [
            {"id": "src", "type": "rectangle", "x": -200, "y": -30,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            {"id": "dst", "type": "rectangle", "x": 140, "y": -30,
             "width": 300, "height": 60, "customData": {"role": "node"}},
            arrow, text]
        warns = canvas.lint_layout(els, artifact_type="flow")["warnings"]
        self.assertFalse(any("arrow label" in w for w in warns), warns)

    def test_label_clear_of_every_box_is_silent(self):
        arrow, text = self.elbow("numbers", [[0, 0], [400, 0]])
        canvas.recenter_label([arrow, text], arrow)
        els = [
            {"id": "src", "type": "rectangle", "x": -200, "y": -30,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            {"id": "dst", "type": "rectangle", "x": 420, "y": -30,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            {"id": "far", "type": "rectangle", "x": 100, "y": 400,
             "width": 160, "height": 60, "customData": {"role": "node"}},
            arrow, text]
        warns = canvas.lint_layout(els, artifact_type="flow")["warnings"]
        self.assertFalse(any("arrow label" in w for w in warns), warns)

    def test_label_collision_measures_drawn_not_stored(self):
        # two labels stored far apart, drawn on top of each other
        a1, t1 = self.elbow("alpha", [[0, 0], [200, 0]])
        a2 = {"id": "a2", "type": "arrow", "x": 0, "y": 4,
              "width": 0, "height": 0, "points": [[0, 0], [200, 0]],
              "startBinding": {}, "endBinding": {}}
        t2 = {"id": "a2-label", "type": "text", "x": 9000, "y": 9000,
              "width": 60, "height": 20, "text": "beta",
              "originalText": "beta", "containerId": "a2"}
        canvas.recenter_label([a1, t1], a1)
        warns = canvas.lint_layout([a1, t1, a2, t2],
                                   artifact_type="flow")["warnings"]
        self.assertTrue(any("overlap" in w and "labels" in w
                            for w in warns), warns)

    def two_labels(self, pts_b, bx=0, by=0):
        """A stub-legged elbow plus a second arrow, both labelled.

        The first arrow's second leg is 10px — too short to host the
        label — so the slide scan has to walk BACK along the horizontal
        one, putting the canvas anchor and the stored slot 38px apart in
        x. That stagger is what every test below discriminates on; a
        balanced elbow would stagger by 18px in y instead, and 18 is
        inside a 20px-tall label's own height, so no arrangement of two
        such labels can separate the channels.

        Args:
            pts_b: The second arrow's points, in its own coordinates.
            bx: The second arrow's x origin.
            by: The second arrow's y origin.

        Returns:
            The element list, labels already re-centred.
        """
        els = [{"id": "a1", "type": "arrow", "x": 0, "y": 0, "width": 0,
                "height": 0, "points": [[0, 0], [200, 0], [200, 10]],
                "startBinding": {}, "endBinding": {}},
               {"id": "a1-label", "type": "text", "x": -999, "y": -999,
                "width": 60, "height": 20, "text": "lblA",
                "originalText": "lblA", "containerId": "a1"},
               {"id": "a2", "type": "arrow", "x": bx, "y": by, "width": 0,
                "height": 0, "points": pts_b,
                "startBinding": {}, "endBinding": {}},
               {"id": "a2-label", "type": "text", "x": -999, "y": -999,
                "width": 60, "height": 20, "text": "lblB",
                "originalText": "lblB", "containerId": "a2"}]
        canvas.recenter_label(els, els[0])
        canvas.recenter_label(els, els[2])
        return els

    def overlaps(self, els):
        """The label↔label warnings a scene produces.

        Args:
            els: The scene.

        Returns:
            The matching warning strings.
        """
        warns = canvas.lint_layout(els, artifact_type="flow")["warnings"]
        return [w for w in warns if "labels" in w and "overlap" in w]

    def test_labels_clear_in_both_channels_are_silent(self):
        """F11: never pair one label's canvas box with another's export.

        Two stub-legged elbows offset in x, each with a 60x20
        label. Both labels are at their anchors on the canvas and both
        at their slots in the export, so those are the only two
        configurations that exist; at these offsets the labels miss each
        other by 2px and 10px in BOTH of them. The cross product the
        two-position rewrite shipped also paired A's canvas box with B's
        export box — a 38px stagger apart — and read 36px and 28px of
        overlap off a screen nobody will ever see, so a drawing that is
        clear everywhere was told to nudge one clear. Silent on the
        corpus, so this was a hole opened rather than a fixture broken
        (task 19 re-review, F11).
        """
        for d in (62, 70):
            els = self.two_labels([[0, 0], [200, 0], [200, 10]], bx=d)
            a_anchor = canvas.arrow_label_anchor(els[0], els[1])[0]
            b_anchor = canvas.arrow_label_anchor(els[2], els[3])[0]
            a_slot = canvas.arrow_label_slot(els[0], els[1])[0]
            b_slot = canvas.arrow_label_slot(els[2], els[3])[0]
            # the premise: clear in each channel on its own, and the two
            # channels really are staggered, or this proves nothing
            self.assertLess(a_anchor + 60, b_anchor, d)
            self.assertLess(a_slot + 60, b_slot, d)
            self.assertGreater(a_anchor - a_slot, 30, d)
            self.assertEqual(self.overlaps(els), [], d)

    def test_a_same_channel_overlap_still_fires(self):
        # control for the above: the same two elbows, close enough that
        # the labels collide in both channels at once
        self.assertTrue(
            self.overlaps(self.two_labels([[0, 0], [200, 0], [200, 10]],
                                          bx=20)))

    def test_an_overlap_on_the_canvas_alone_fires(self):
        # a straight arrow's label sits at one position in both
        # channels, placed over the elbow label's ANCHOR only — visible
        # to the user, invisible in every export
        self.assertTrue(self.overlaps(
            self.two_labels([[0, 0], [200, 0]], bx=130)))

    def test_an_overlap_in_the_export_alone_fires(self):
        # the mirror: over the elbow label's SLOT only, so the collision
        # is in the snapshot the agent takes and not on the user's screen
        self.assertTrue(self.overlaps(
            self.two_labels([[0, 0], [200, 0]], bx=30)))

    def geometry_row(self, pts):
        """The `x-geometry --diff` row for one elbow's bound label.

        Args:
            pts: The arrow's points, in its own coordinates.

        Returns:
            The single printed row.
        """
        self.store.apply_batch({
            "base_revn": 0, "artifact": "f",
            "create": {"id": "f", "name": "F", "type": "flow",
                       "concept": "c", "concept_name": "C"}, "ops": []})
        arrow, text = self.elbow("lbl", pts)
        canvas.recenter_label([arrow, text], arrow)
        self.store.commit(author="agent", new_scenes={"f": [arrow, text]},
                          base_revn=self.store.head_revn())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            canvas.cmd_x_geometry(argparse.Namespace(
                project=self.tmp, artifact="f", diff=True))
        rows = buf.getvalue().splitlines()
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_x_geometry_prints_both_positions_when_they_differ(self):
        """F12: the `drawn=` column named a position no snapshot shows.

        `render_svg` paints text at its stored x/y, so every export and
        every headless look shows the SLOT, while this column is derived
        from the canvas anchor. On a biased label those are up to 49px
        apart, and the agent comparing the number against the picture it
        just took would find them disagreeing with no way to tell why.
        Both, or the column lies by omission (task 19 re-review, F12).
        """
        row = self.geometry_row([[0, 0], [200, 0], [200, 200]])
        self.assertIn("drawn=(170,-10)", row)     # the canvas anchor
        self.assertIn("export=(170,8)", row)      # the stored slot
        self.assertIn("drift=18px", row)

    def test_x_geometry_names_one_position_when_they_agree(self):
        # the silent half: a straight arrow needs no bias, so the two
        # channels coincide and the row must not imply a divergence
        row = self.geometry_row([[0, 0], [200, 0]])
        self.assertIn("drawn=(70,-10)", row)
        self.assertNotIn("export=", row)

    def test_svg_breaks_the_stroke_under_an_arrow_label(self):
        # the gap the client leaves behind a bound label, cut out of the
        # arrow's OWN ink rather than painted over whatever lies beneath
        # (v0.9 task 51 — this used to assert a second SVG_GROUND rect,
        # and that rect erased other elements' ink; canvas.
        # arrow_label_break has the measurement)
        arrow, text = self.elbow("scored by", [[0, 0], [300, 0]])
        canvas.recenter_label([arrow, text], arrow)
        svg, _, _ = canvas.render_svg([arrow, text])
        self.assertIn("<mask id=", svg)
        self.assertIn("mask='url(#", svg)
        # exactly one opaque fill, the paper — nothing is painted over
        self.assertEqual(svg.count("fill='%s'" % canvas.SVG_GROUND), 1)
        bx, by, bw, bh = canvas.arrow_label_break(text)
        self.assertIn("<rect x='%f' y='%f' width='%f' height='%f' "
                      "fill='#000'/>" % (bx, by, bw, bh), svg)


class TestAssessorUserEdits(Base):
    """The user-shaped elements `x-as-user` posts (v0.6).

    The capability assessment rebuilt these by hand every run, and a
    hand-rolled shape that drifts from what the client posts quietly
    invalidates every behavioural finding built on it. Pinning them here
    is also the point: the suite covers agent op batches heavily and
    user-authored edits barely at all.
    """

    def commit(self, extra):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "f",
            "create": {"id": "f", "name": "F", "type": "flow",
                       "concept": "c", "concept_name": "C"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "a", "kind": "step",
                "label": "Ingest", "x": 100, "y": 100}}]})
        els = [dict(e) for e in self.store.scenes["f"]] + extra
        return self.store.commit(author="user", new_scenes={"f": els},
                                 base_revn=self.store.head_revn())

    # A field the client fills in at runtime (`question: q`,
    # `target: target.id`) rather than writing as a literal. The parser
    # reports the KEY and this marker, never a guessed value: what parity
    # can be checked for such a field is that both sides stamp it at all.
    RUNTIME = "<supplied at runtime>"

    @classmethod
    def client_custom_data(cls, builder, next_builder):
        """The `customData` blocks one App.tsx builder inserts.

        Parsed rather than restated: an expectation copied by hand into
        this file agrees with the client exactly once — the day it is
        written — and then drifts in silence, which is the failure r5-7
        IS. The slice is the named builder's body alone, so a `role:`
        belonging to a neighbouring builder cannot wander in.

        EVERY PAIR IS CAPTURED, not only the string-literal ones. The
        first version of this matched `key: "value"` and so read a field
        whose value is an expression as ABSENT — `question: q` and
        `target: target.id` simply vanished, and a field added later as
        `pinned: true` would vanish the same way, silently narrowing the
        parity claim to whatever happened to be quoted. A key with a
        non-literal value is reported with `RUNTIME` as its value, which
        keeps it in the comparison instead of dropping it out of one.

        Args:
            builder: The `const <name>` the block belongs to.
            next_builder: The `const <name>` that follows it, bounding
                the slice.

        Returns:
            One dict per element the builder inserts, in insert order,
            mapping every stamped key to its literal value or `RUNTIME`.

        Raises:
            AssertionError: If either boundary is missing from App.tsx,
                so a renamed builder fails loudly rather than parsing an
                empty slice into a vacuously passing comparison.
        """
        src = (Path(__file__).resolve().parents[1] / "frontends" /
               "wysiwyg-grilling" / "src" / "App.tsx").read_text(
                   encoding="utf-8")
        for anchor in (builder, next_builder):
            if src.count(anchor) != 1:
                # raised, not asserted: `python -O` strips `assert` and
                # this guard is the only thing between a renamed builder
                # and an empty slice that compares equal to nothing
                raise AssertionError(
                    "%r appears %d times in App.tsx; the parity parser "
                    "needs it exactly once to bound a builder's body"
                    % (anchor, src.count(anchor)))
        body = src.split(builder, 1)[1].split(next_builder, 1)[0]
        blocks = []
        for m in re.findall(r"customData:\s*\{([^}]*)\}", body):
            stamped = {}
            for pair in m.split(","):
                if ":" not in pair:
                    continue
                key, _, raw = pair.partition(":")
                lit = re.fullmatch(r'\s*"([^"]*)"\s*', raw)
                stamped[key.strip()] = lit.group(1) if lit else cls.RUNTIME
            blocks.append(stamped)
        return blocks

    @classmethod
    def client_sticky_custom_data(cls):
        """The `customData` the 🗒 note button posts, read off App.tsx.

        Returns:
            One dict per element `addStickyNote` inserts, in order.
        """
        return cls.client_custom_data("const addStickyNote",
                                      "const askUserPin")

    @classmethod
    def client_pin_custom_data(cls):
        """The `customData` the ❓ ask button posts, read off App.tsx.

        Returns:
            One dict per element `askUserPin` inserts, in order.
        """
        return cls.client_custom_data("const askUserPin",
                                      "const dismissPin")

    def assert_stamp_parity(self, ours, client, what):
        """Compare an adapter's stamp against the client's, key by key.

        Split from the note test because the pin needed the same
        comparison and the pin is the half that was actually diverging.
        Keys are compared as SETS first, which is the assertion that
        catches a field one side stamps and the other does not — the
        failure this parity family exists for. Literal values are then
        compared; a `RUNTIME` field is checked for presence only, since
        the client's value for it does not exist until a user types it.

        Args:
            ours: `customData` dicts from the canvas.py adapter, in
                insert order.
            client: The same, parsed out of App.tsx.
            what: The button's name, for the failure message.
        """
        self.assertEqual(
            len(ours), len(client),
            "%s inserts %d stamped element(s) and the adapter posts %d"
            % (what, len(client), len(ours)))
        for i, (mine, theirs) in enumerate(zip(ours, client)):
            self.assertEqual(
                set(mine or {}), set(theirs),
                "element %d: `x-as-user` stamps %s where the %s button "
                "stamps %s — a user edit the product itself never writes, "
                "driving findings that carry the authority of real ones"
                % (i, sorted(mine or {}), what, sorted(theirs)))
            for key, value in theirs.items():
                if value is self.RUNTIME:
                    continue
                self.assertEqual(
                    (mine or {}).get(key), value,
                    "element %d: %s stamps %s=%r where the %s button "
                    "stamps %r" % (i, "x-as-user", key,
                                   (mine or {}).get(key), what, value))

    def test_the_adapter_stamps_the_notes_the_client_stamps(self):
        """r5-7: `x-as-user note` posts the client's roles, not its own.

        The adapter stamped `note`/`note-text`; the client stamps
        `annotation`/`label`. Ten branches in canvas.py consult
        `annotation` and none consult `note`, so every note four
        assessment runs ever posted was a user's sticky in the picture
        and invisible to the annotation path in the code — the drift
        this driver exists to prevent, in the driver itself. Latent when
        filed (verified identical through `lint_layout`) and asymmetric
        by construction: any check added on the annotation path skips
        the whole harness silently.

        The whole `customData` is compared, not just `role`. The stamp
        that mattered was the one nobody was asserting about.
        """
        client = self.client_sticky_custom_data()
        self.assertEqual(len(client), 2,
                         "addStickyNote now inserts %d stamped element(s), "
                         "not the 2 (rect + bound label) this parity test "
                         "was written against: %s" % (len(client), client))
        ours = [e.get("customData")
                for e in canvas._x_user_note("crowded trades", 40, 400)]
        self.assert_stamp_parity(ours, client, "🗒 note")

    def test_the_adapter_stamps_the_pin_the_client_stamps(self):
        """The half the note test's shape left unchecked.

        The note stamp was pinned and the PIN stamp was not, and the pin
        was the one diverging: the client stamps `direction: "user"` and
        the adapter did not, while the adapter seeded an `answer: None`
        the client never writes. Inert only because pin ingestion reads
        the fields it wants by name rather than taking the block whole —
        which is exactly the kind of "harmless today" that the note
        divergence also was, for four assessment runs, until a check
        started consulting the field nobody had aligned.

        `question` and `target` are runtime-supplied on the client side,
        so what is asserted about them is that both sides stamp the KEY.
        That is the assertion that matters here anyway: the divergence
        was a key present on one side and absent on the other.
        """
        client = self.client_pin_custom_data()
        self.assertEqual(len(client), 1,
                         "askUserPin now inserts %d stamped element(s), "
                         "not the 1 this parity test was written against: "
                         "%s" % (len(client), client))
        ours = [canvas._x_user_pin("a", "does this hold?", 3, 5)
                .get("customData")]
        self.assert_stamp_parity(ours, client, "❓ ask")

    def test_the_parity_parser_reads_a_non_string_field(self):
        """A field whose value is not a quoted string must not vanish.

        The parser's first version matched `key: "value"` only, so
        `question: q` and `target: target.id` were invisible to it and a
        field added later as `pinned: true` would be invisible the same
        way — the parity claim narrowing itself in silence, which is the
        failure mode this whole family exists to prevent. Asserted on
        the REAL client rather than on a synthetic string, so it also
        fails if those two fields stop being stamped.
        """
        pin, = self.client_pin_custom_data()
        self.assertEqual(
            {k for k, v in pin.items() if v is self.RUNTIME},
            {"question", "target"},
            "the runtime-valued fields askUserPin stamps have moved: %r"
            % (pin,))
        self.assertEqual(pin.get("direction"), "user",
                         "the literal fields are no longer being read")

    def test_a_user_note_lands_as_an_annotation(self):
        # the role change is only worth having if the pipeline BEHIND it
        # sees the note: role `note` fell through to the generic `added`
        # verb, `annotation` reaches the arm that carries the text and
        # the author to the agent (r5-7)
        rec = self.commit(canvas._x_user_note("crowded trades", 40, 400))
        verbs = rec["summary"]["verb_counts"]
        self.assertIn("annotated", verbs)
        note = next(e for e in self.store.scenes["f"]
                    if (e.get("customData") or {}).get("role") == "annotation")
        self.assertEqual(note["customData"]["author"], "user")
        self.assertEqual(rec["summary"]["headline"],
                         "your note: 'crowded trades'")
        fact = next(f for f in rec["artifacts"]["f"]["facts"]
                    if f["fact"] == "annotated")
        self.assertEqual(fact["text"], "crowded trades")
        self.assertEqual(fact["author"], "user")
        # r4b-9 called the note anchor UNTESTABLE because neither the
        # button nor this verb sets `annotates`. On the annotation arm
        # the fact carries a nearest target regardless, so the anchor
        # exists — it is inferred rather than declared, and it is now
        # reachable at all
        self.assertEqual(fact["target"], "a")

    def test_a_user_pin_registers_as_a_question(self):
        rec = self.commit([canvas._x_user_pin("a", "does this see the "
                                              "book?", 300, 90)])
        self.assertIn("pin_added", rec["summary"]["verb_counts"])
        self.assertTrue(any(p.get("question", "").startswith("does this")
                            for p in self.store.registry.get("pins") or []))

    def test_user_elements_carry_the_author_stamp(self):
        # the off-grid lint skips user elements on this stamp alone, so a
        # missing one turns every simulated user edit into agent
        # sloppiness. Scoped to the elements the CLIENT stamps: it puts
        # no `author` on a sticky's bound label, and that costs nothing
        # here, because the exemption is read off `shapes` — rectangles,
        # diamonds, ellipses — which no bound text has ever been in.
        stamped = [canvas._x_user_note("x", 7, 9)[0],
                   canvas._x_user_pin("a", "q?", 3, 5)]
        for el in stamped:
            self.assertEqual((el.get("customData") or {}).get("author"),
                             "user", el["id"])
        offgrid = canvas.lint_layout(
            [*stamped, *canvas._x_user_note("x", 7, 9)[1:]])["notes"]
        self.assertFalse([n for n in offgrid if "off the 4px grid" in n],
                         "a user's own placement is being nagged about: %s"
                         % offgrid)


class TestGlossaryAlias(Base):
    """One concept, two names, split by audience (v0.6).

    `**Excess Return** / **alpha**: …` was written as a single entry on
    purpose — two entries would read as two metrics. TERM_RE's non-greedy
    group backtracked through it and captured `Excess Return** / **alpha`
    as one term, raw markdown and all, so the registry reported both
    "settled term has no concept" and "concept references an undefined
    term" about the same line (v0.5 assessment R2-7).
    """

    ENTRY = ("**Excess Return** / **alpha**: one number, two names, "
             "split by audience.\n**Run**: one morning's work.\n")

    def test_alias_entry_yields_one_clean_term(self):
        self.assertEqual(canvas.parse_glossary_terms(self.ENTRY),
                         ["Excess Return", "Run"])

    def test_alias_is_recorded(self):
        self.assertEqual(canvas.parse_glossary_aliases(self.ENTRY),
                         {"alpha": "Excess Return"})

    def test_definition_survives(self):
        pairs = dict(canvas.parse_glossary_pairs(self.ENTRY))
        self.assertIn("two names", pairs["Excess Return"])

    def test_a_concept_linked_to_either_name_resolves(self):
        terms = canvas.parse_glossary_terms(self.ENTRY)
        aliases = canvas.parse_glossary_aliases(self.ENTRY)
        for linked in ("Excess Return", "alpha"):
            reg = {"concepts": [{"id": "er", "name": "ER",
                                 "glossary": linked, "views": ["v"]}]}
            notes = canvas.lint_registry(terms, reg, True, aliases)
            self.assertFalse(any("doesn't define" in n for n in notes),
                             (linked, notes))

    def test_a_genuinely_undefined_term_still_reports(self):
        reg = {"concepts": [{"id": "x", "name": "X",
                             "glossary": "Sharpe", "views": ["v"]}]}
        notes = canvas.lint_registry(
            canvas.parse_glossary_terms(self.ENTRY), reg, True,
            canvas.parse_glossary_aliases(self.ENTRY))
        self.assertTrue(any("doesn't define" in n for n in notes), notes)

    def test_plain_entries_are_unaffected(self):
        self.assertEqual(canvas.parse_glossary_terms("**Run**: a day.\n"),
                         ["Run"])
        self.assertEqual(canvas.parse_glossary_aliases("**Run**: a day.\n"),
                         {})


class TestRenameArtifact(Base):
    """A view's scope can narrow, so its name has to be able to (v0.6).

    `name` was writable only inside `create`, so splitting a domain model
    left the rail showing the old title forever and the only workaround
    was re-creating the artifact, which discards its history (R2-5).
    """

    def setUp(self):
        super().setUp()
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dm",
            "create": {"id": "dm", "name": "Argus Domain", "type": "domain",
                       "concept": "d", "concept_name": "D"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "e1", "kind": "entity",
                "label": "Signal", "x": 100, "y": 100}}]})

    def rename(self, **kw):
        return self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [dict({"op": "registry",
                          "action": "rename_artifact"}, **kw)]})

    def test_rename_takes_effect(self):
        self.rename(artifact="dm", name="Signal Formation")
        self.assertEqual(self.store.artifact_meta["dm"]["name"],
                         "Signal Formation")

    def test_rename_persists_to_the_artifact_file(self):
        self.rename(artifact="dm", name="Signal Formation")
        doc = json.loads(
            (self.store.p.artifacts_dir / "dm.excalidraw").read_text())
        self.assertEqual(doc["wysiwyg"]["name"], "Signal Formation")

    def test_rename_keeps_the_elements(self):
        self.rename(artifact="dm", name="Signal Formation")
        self.assertTrue(any(e["id"] == "e1"
                            for e in self.store.scenes["dm"]))

    def test_unknown_artifact_is_rejected(self):
        with self.assertRaises(canvas.BatchError):
            self.rename(artifact="ghost", name="X")

    def test_empty_name_is_rejected(self):
        with self.assertRaises(canvas.BatchError):
            self.rename(artifact="dm", name="   ")


class TestRegistryOpsSeeTheBatchsOwnCreates(Base):
    """A registry op may name the artifact its own batch creates (v0.7).

    `set_budget` was rejected wholesale — "needs an existing artifact" —
    because a created artifact did not reach `artifact_meta` until the
    write at the END of commit, after the ops had run. The documented
    workflow is "over budget → record it with a reason", so every
    deliberate overrun cost an extra revision AND a false LAYOUT_NOTE for
    the overrun it was in the act of justifying (brownfield BUG-03).

    Third instance of one ordering: r3-10 is the same fault running the
    other way, and `upsert_concept` already ships a workaround for this
    shape (`view_types`).
    """

    def batch(self, *ops):
        return {
            "base_revn": self.store.head_revn(), "artifact": "cand",
            "create": {"id": "cand", "name": "Candidates", "type": "flow",
                       "concept": "c", "concept_name": "C"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n1", "kind": "step",
                "label": "Ingest", "x": 100, "y": 100}}, *ops]}

    def test_set_budget_rides_the_creating_batch(self):
        rec, _ = self.store.apply_batch(self.batch(
            {"op": "registry", "action": "set_budget", "artifact": "cand",
             "nodes": 14, "arrows": 18,
             "reason": "the 5-way ingest fan IS this view"}))
        self.assertEqual(self.store.registry["budgets"]["cand"]["nodes"], 14)
        self.assertEqual(self.store.registry["budgets"]["cand"]["reason"],
                         "the 5-way ingest fan IS this view")
        # set_budget records via the generic fall-through, so the entry
        # keeps the op's own action name (unlike budget_cleared)
        self.assertTrue(any(c.get("action") == "set_budget"
                            for c in rec["registry_changes"]))

    def test_rename_rides_the_creating_batch(self):
        self.store.apply_batch(self.batch(
            {"op": "registry", "action": "rename_artifact",
             "artifact": "cand", "name": "Algorithm Candidates"}))
        self.assertEqual(self.store.artifact_meta["cand"]["name"],
                         "Algorithm Candidates")
        doc = json.loads(
            (self.store.p.artifacts_dir / "cand.excalidraw").read_text())
        self.assertEqual(doc["wysiwyg"]["name"], "Algorithm Candidates")

    def test_one_revision_not_two(self):
        before = self.store.head_revn()
        self.store.apply_batch(self.batch(
            {"op": "registry", "action": "set_budget", "artifact": "cand",
             "nodes": 14, "arrows": 18, "reason": "deliberate"}))
        self.assertEqual(self.store.head_revn(), before + 1)

    def test_an_artifact_that_exists_nowhere_is_still_rejected(self):
        # the silent half: seeding the batch's creates must not turn the
        # existence check off
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch(self.batch(
                {"op": "registry", "action": "set_budget",
                 "artifact": "ghost", "nodes": 14, "arrows": 18,
                 "reason": "no such view"}))

    def test_a_failed_registry_op_leaves_no_phantom_artifact(self):
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch(self.batch(
                {"op": "registry", "action": "set_budget",
                 "artifact": "ghost", "nodes": 14, "arrows": 18,
                 "reason": "no such view"}))
        self.assertNotIn("cand", self.store.artifact_meta)
        self.assertNotIn("cand", self.store.scenes)

    def test_check_accepts_it_too(self):
        r = self.store.check_batch(self.batch(
            {"op": "registry", "action": "set_budget", "artifact": "cand",
             "nodes": 14, "arrows": 18, "reason": "deliberate"}))
        self.assertTrue(r["ok"], r.get("errors"))
        self.assertNotIn("cand", self.store.artifact_meta)


class TestVersioningBoundary(Base):
    """A dry run writes nothing; a mixed batch keeps its rename (v0.7 WP1).

    The two are a compensating pair (v0.6 assessment r3-12 + r3-10):
    `--check` wrote the new name to disk with no revn, the real apply
    reverted it from a stale meta snapshot, and "the name never changed"
    was evidence for neither. Each control below has to hold the other
    defect constant, which is why they are tested together.
    """

    ORIGINAL = "Argus Domain"

    def setUp(self):
        super().setUp()
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dm",
            "create": {"id": "dm", "name": self.ORIGINAL, "type": "domain",
                       "concept": "d", "concept_name": "D"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "e1", "kind": "entity",
                "label": "Signal", "x": 100, "y": 100}}]})

    def stored_name(self):
        doc = json.loads(
            (self.store.p.artifacts_dir / "dm.excalidraw").read_text())
        return doc["wysiwyg"]["name"]

    def rename_op(self, name):
        return {"op": "registry", "action": "rename_artifact",
                "artifact": "dm", "name": name}

    # ---- r3-12: the dry run is side-effect free ----------------------

    def test_check_does_not_rename_on_disk(self):
        self.store.check_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [self.rename_op("ZZZ-DRY-RUN-PROBE")]})
        self.assertEqual(self.stored_name(), self.ORIGINAL)

    def test_check_does_not_rename_in_memory(self):
        self.store.check_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [self.rename_op("ZZZ-DRY-RUN-PROBE")]})
        self.assertEqual(self.store.artifact_meta["dm"]["name"],
                         self.ORIGINAL)

    def test_check_commits_no_revision(self):
        before = self.store.head_revn()
        self.store.check_batch({
            "base_revn": before, "artifact": "dm",
            "ops": [self.rename_op("ZZZ-DRY-RUN-PROBE")]})
        self.assertEqual(self.store.head_revn(), before)

    def test_dry_run_flag_does_not_leak_when_the_batch_is_rejected(self):
        # a leaked _dry_run would silently swallow every later real
        # write — the failure mode that makes the chokepoint scary
        self.store.check_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [{"op": "registry", "action": "rename_artifact",
                     "artifact": "ghost", "name": "X"}]})
        self.assertFalse(self.store._dry_run)
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [self.rename_op("After The Rejection")]})
        self.assertEqual(self.stored_name(), "After The Rejection")

    # ---- r3-10: the rename survives a batch that also draws ----------

    def mixed_rename(self, name):
        return self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [self.rename_op(name),
                    {"op": "add", "element": {
                        "type": "rectangle", "id": "e2", "kind": "entity",
                        "label": "Position", "x": 400, "y": 100}}]})

    def test_rename_survives_a_batch_that_also_draws(self):
        self.mixed_rename("Signal Formation")
        self.assertEqual(self.stored_name(), "Signal Formation")

    def test_the_record_does_not_contradict_its_own_registry_changes(self):
        rec, _ = self.mixed_rename("Signal Formation")
        renamed = [c for c in rec["registry_changes"]
                   if c.get("action") == "artifact_renamed"]
        self.assertEqual(renamed[0]["to"], "Signal Formation")
        # one record, one name: it used to report the rename and store
        # the old value, so checking out the renaming revision reverted it
        self.assertEqual(rec["artifacts"]["dm"]["meta"]["name"],
                         "Signal Formation")

    def test_checking_out_the_renaming_revision_keeps_the_new_name(self):
        rec, _ = self.mixed_rename("Signal Formation")
        state = self.store.state_at(rec["revn"])
        self.assertEqual(state["dm"]["meta"]["name"], "Signal Formation")

    def test_a_drawing_batch_with_no_rename_leaves_meta_alone(self):
        # the silent half: the re-read must not invent a meta change
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "e3", "kind": "entity",
                "label": "Book", "x": 700, "y": 100}}]})
        self.assertEqual(rec["artifacts"]["dm"]["meta"]["name"],
                         self.ORIGINAL)
        self.assertEqual(self.stored_name(), self.ORIGINAL)


class TestProgressIndicatorQuestion(Base):
    """Q25 asks about progress indicators, not about percentages (v0.6).

    `VaR alert 2.5%` on a threshold slider drew a GDS citation about
    12-step wizards and cost a waive to silence (R2-1).
    """

    def screen(self, label):
        els = [{"id": "scr", "type": "frame", "x": 0, "y": 0,
                "width": 720, "height": 480, "name": "Console"}]
        els.append({"id": "b", "type": "rectangle", "x": 20, "y": 60,
                    "width": 300, "height": 40, "frameId": "scr",
                    "customData": {"role": "node", "kind": "block"}})
        els.append({"id": "b-l", "type": "text", "x": 24, "y": 70,
                    "width": 200, "height": 20, "text": label,
                    "originalText": label, "containerId": "b"})
        return canvas.lint_layout(els, artifact_type="wireframe",
                                  aid="w")["notes"]

    def test_a_threshold_percentage_is_not_a_progress_indicator(self):
        notes = self.screen("VaR alert  2.5%")
        self.assertFalse(any("progress indicator" in n for n in notes),
                         notes)

    def test_a_kpi_delta_is_not_either(self):
        notes = self.screen("Excess Return +3.1%")
        self.assertFalse(any("progress indicator" in n for n in notes),
                         notes)

    def test_step_n_of_m_still_asks(self):
        notes = self.screen("Step 2 of 5")
        self.assertTrue(any("progress indicator" in n for n in notes),
                        notes)

    def test_percent_complete_still_asks(self):
        notes = self.screen("60% complete")
        self.assertTrue(any("progress indicator" in n for n in notes),
                        notes)


class TestRegistryFollowsTheBranch(Base):
    """The registry is branch-level, like the scenes (r3-17).

    `switch_branch` has always materialised per-branch scenes and deleted
    the artifacts the target lacks, while concepts/mappings/pins/
    tripwires/waives/budgets stayed one global blob — so on a branch the
    registry asserted views that do not exist and printed
    VIEW_DEBT=none: branch-blind in the direction that SUPPRESSES work.

    And it lost data. Reproduced before the fix: switching to a branch
    without the artifact deletes its file, the load-time healer prunes
    the pins on it, and _save_registry makes that permanent — for the
    branch you came from.
    """

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "artifact": "checkout-flow",
            "ops": [{"op": "pin", "target": "payment", "id": "pin-pay",
                     "question": "Card only?"}]})
        self.store.checkout_revn = 0
        self.store.commit(author="user", new_scenes={}, fork_name="alt")
        self.store.registry["head"] = "main"
        self.store._save_registry()

    def reload(self):
        self.store._save_registry()
        self.store = canvas.Store(self.project)
        return self.store

    def pin_status(self):
        return {p["id"]: p["status"] for p in self.store.registry["pins"]}

    def test_mains_pin_survives_a_visit_to_a_branch_without_it(self):
        self.store.switch_branch("alt")
        self.reload()                       # the healer prunes on alt
        self.store.switch_branch("main")
        self.reload()
        self.assertEqual(self.pin_status()["pin-pay"], "open")

    def test_the_branch_carries_its_own_scope(self):
        self.store.switch_branch("alt")
        main = next(b for b in self.store.registry["branches"]
                    if b["name"] == "main")
        self.assertIn("scope", main)
        self.assertIn("pins", main["scope"])

    def test_every_scoped_section_is_stashed(self):
        self.store.switch_branch("alt")
        main = next(b for b in self.store.registry["branches"]
                    if b["name"] == "main")
        self.assertEqual(set(main["scope"]), set(canvas.BRANCH_SCOPED))

    def test_a_fork_records_where_it_began(self):
        alt = next(b for b in self.store.registry["branches"]
                   if b["name"] == "alt")
        self.assertEqual(alt["forked_from"], "main")
        self.assertEqual(alt["forked_at_revn"], 0)
        self.assertIn("origin_revn", alt)

    def test_head_advances_past_the_fork_point(self):
        # the reason the fork point had to be stored: `head` moves
        alt = next(b for b in self.store.registry["branches"]
                   if b["name"] == "alt")
        self.assertNotEqual(alt["head"], alt["forked_at_revn"])

    def test_a_v06_registry_is_migrated_losslessly(self):
        reg = json.loads(json.dumps(canvas.DEFAULT_REGISTRY))
        reg["concepts"] = [{"id": "c", "name": "C", "views": ["a"]}]
        out = canvas._mig_registry_0002(reg)
        main = out["branches"][0]
        self.assertEqual(main["scope"]["concepts"], reg["concepts"])
        # top level stays the working copy — nothing is moved out
        self.assertEqual(out["concepts"], reg["concepts"])


class TestModAttributes(Base):
    """A domain entity's attribute rows are editable in place (BUG-04).

    `attributes` was accepted on `add` and nowhere else, so the only way
    to amend them was delete + re-add — which mints a new element id and
    therefore drops that element's mappings and pins, and breaks the
    rename-keeps-the-id rule `entity_renamed` detection depends on.
    """

    def setUp(self):
        super().setUp()
        self.store.apply_batch({
            "base_revn": 0, "artifact": "dm",
            "create": {"id": "dm", "name": "DM", "type": "domain",
                       "concept": "d", "concept_name": "D"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "e1", "kind": "entity",
                "label": "Concept", "x": 100, "y": 100,
                "attributes": ["name: str", "kind: str"]}}]})

    def set_attrs(self, rows):
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dm",
            "ops": [{"op": "mod", "id": "e1", "attrs": {"attributes": rows}}]})
        return sorted({f["fact"] for a in rec["artifacts"].values()
                       for f in a["facts"]})

    def rows(self):
        return [e["text"] for e in self.store.scenes["dm"]
                if (e.get("customData") or {}).get("attr_of") == "e1"]

    def test_the_entity_id_survives(self):
        self.set_attrs(["name: str", "scale_free: bool"])
        self.assertTrue(any(e["id"] == "e1" for e in self.store.scenes["dm"]))

    def test_the_rows_are_replaced(self):
        self.set_attrs(["name: str", "scale_free: bool"])
        self.assertEqual(self.rows(), ["name: str", "scale_free: bool"])

    def test_adding_a_row_narrates_as_an_attribute(self):
        self.assertIn("attribute_added",
                      self.set_attrs(["name: str", "kind: str", "n: int"]))

    def test_removing_a_row_narrates_as_an_attribute(self):
        self.assertIn("attribute_removed", self.set_attrs(["name: str"]))

    def test_a_non_entity_is_rejected(self):
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({
                "base_revn": self.store.head_revn(), "artifact": "dm",
                "ops": [{"op": "mod", "id": "e1-attr-1",
                         "attrs": {"attributes": ["x"]}}]})

    def test_a_non_list_is_rejected(self):
        with self.assertRaises(canvas.BatchError):
            self.store.apply_batch({
                "base_revn": self.store.head_revn(), "artifact": "dm",
                "ops": [{"op": "mod", "id": "e1",
                         "attrs": {"attributes": {"x": 1}}}]})


class TestMarkerAnchor(unittest.TestCase):
    """Markers hug the shape, not its bounding box (r3-1).

    A constant bbox offset is 22px off a rectangle and 79px off a
    DIAMOND — a clearance varying threefold with shape type, which no
    intentional design would do. v0.6 fixed exactly this for the tooltip
    dot in App.tsx and nobody grepped, so it was still live in the pin
    seeder AND, found while fixing this, in the tripwire mark.
    """

    def shape(self, etype):
        return {"id": "n", "type": etype, "x": 100, "y": 100,
                "width": 200, "height": 100}

    def test_a_rectangle_fills_its_box(self):
        self.assertEqual(canvas.marker_inset("rectangle"), 0.0)
        self.assertEqual(canvas.marker_anchor(self.shape("rectangle")),
                         (300, 200))

    def test_a_diamond_pulls_the_marker_in(self):
        x, y = canvas.marker_anchor(self.shape("diamond"))
        self.assertEqual((x, y), (250, 175))

    def test_an_ellipse_pulls_in_by_the_half_diagonal(self):
        x, _ = canvas.marker_anchor(self.shape("ellipse"))
        self.assertLess(x, 300)
        self.assertGreater(x, 250)

    def test_the_top_right_corner_mirrors_it(self):
        self.assertEqual(canvas.marker_anchor(self.shape("diamond"),
                                              corner="tr"), (250, 125))

    def test_the_pin_glyph_hugs_a_diamond(self):
        els, errors = [], []
        node = self.shape("diamond")
        node["customData"] = {"role": "node"}
        out = canvas.apply_ops(
            [node],
            [{"op": "pin", "id": "pin-1", "target": "n",
              "question": "Which way?"}], errors, [])
        self.assertEqual(errors, [])
        pin = next(e for e in out if e["id"] == "pin-1")
        # 8px clear of the STROKE, not 79px clear of the corner
        self.assertEqual((pin["x"], pin["y"]), (258, 117))

    def test_the_router_still_owns_the_name_edge_anchor(self):
        # this helper was first written AS `edge_anchor`, which already
        # existed with a different meaning (the point on a shape's edge
        # facing a target). The shadow made route_arrow produce no
        # candidates and 156 tests error at once — loudly, which is the
        # only reason it did not ship.
        self.assertEqual(canvas.edge_anchor(self.shape("rectangle"),
                                            1000, 150), (300.0, 150.0))


class TestManyToOneIsADeclaredCompression(unittest.TestCase):
    """3.2.4 asks once per mapping, not per cartesian pair (r3-8).

    Three real toggles the user switches individually, mapped to one
    flow box at a lower resolution, read as "same action, 3 names; pick
    one?" — and both remedies it proposed were destructive: picking one
    name deletes two controls, splitting into three mappings asserts
    three steps that do not exist. When every remedy a check proposes is
    destructive, the check's premise is wrong.
    """

    def labelled(self, eid, lbl, x):
        """A node plus its bound label — label_map reads bound text."""
        return [{"id": eid, "type": "rectangle", "x": x, "y": 100,
                 "width": 160, "height": 60,
                 "customData": {"role": "node"}},
                {"id": eid + "-label", "type": "text", "x": x, "y": 100,
                 "width": 140, "height": 20, "containerId": eid,
                 "text": lbl, "originalText": lbl}]

    def scenes(self):
        wf = []
        for i, (k, lbl) in enumerate((("market", "Market data"),
                                      ("edgar", "EDGAR filings"),
                                      ("news", "News stream"))):
            wf += self.labelled("src-" + k, lbl, 100 + i * 200)
        return {"admin-console": wf,
                "daily-run-flow": self.labelled("ingest", "Ingest", 100)}

    def notes(self, mappings):
        out = canvas.cross_lint(
            self.scenes(),
            {"admin-console": "wireframe", "daily-run-flow": "flow"},
            {"mappings": mappings, "waives": {}}, [])
        return [m for part in out.values() for m in part["notes"]
                if "3.2.4" in m]

    def one_mapping(self):
        return [{"concept": "argus",
                 "elements": ["admin-console#src-market",
                              "admin-console#src-edgar",
                              "admin-console#src-news",
                              "daily-run-flow#ingest"]}]

    def three_mappings(self):
        return [{"concept": "argus",
                 "elements": ["admin-console#src-%s" % k,
                              "daily-run-flow#ingest"]}
                for k in ("market", "edgar", "news")]

    def test_one_mapping_is_a_compression_and_says_nothing(self):
        self.assertEqual(self.notes(self.one_mapping()), [])

    def test_separate_mappings_disagreeing_still_fire(self):
        # the silent half, inverted: two parties naming one step
        # differently is the case worth catching, and it survives
        notes = self.notes(self.three_mappings())
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("3 separate mappings", notes[0])


class TestOneTripwirePerMapping(Base):
    """A mapping asks ONE question per save, whatever its arity (r3-7).

    Emission was a nested loop over (changed × sibling), so renaming one
    element of a four-member mapping asked three questions — while every
    suppression path above it already reasons once per mapping. The
    agent that met it: "fired three tripwires — all one cause, so I've
    answered the cause rather than the count", then wrote ONE annotation
    that resolved all three.
    """

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "add_mapping",
                     "concept": "checkout",
                     "elements": ["checkout-flow#cart",
                                  "checkout-flow#checkout",
                                  "checkout-flow#payment",
                                  "checkout-flow#confirm"]}]})

    def rename(self, eid, label):
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "artifact": "checkout-flow",
            "ops": [{"op": "mod", "id": eid, "attrs": {"label": label}}]})
        return rec

    def test_one_rename_of_four_members_asks_one_question(self):
        self.assertEqual(len(self.rename("cart", "Basket")["tripwires"]), 1)

    def test_the_question_counts_the_siblings(self):
        t = self.rename("cart", "Basket")["tripwires"][0]
        self.assertIn("3 mapped siblings", t["question"])

    def test_it_carries_both_lists(self):
        t = self.rename("cart", "Basket")["tripwires"][0]
        self.assertEqual(t["changed_all"], ["checkout-flow#cart"])
        self.assertEqual(len(t["siblings"]), 3)

    def test_the_singular_fields_survive_for_the_ui_anchor(self):
        t = self.rename("cart", "Basket")["tripwires"][0]
        self.assertEqual(t["changed"], "checkout-flow#cart")
        self.assertIn(t["sibling"], t["siblings"])

    def test_one_resolve_clears_it(self):
        self.rename("cart", "Basket")
        self.assertEqual(len(self.store.open_tripwires()), 1)

    def test_renaming_two_members_still_asks_once(self):
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "artifact": "checkout-flow",
            "ops": [{"op": "mod", "id": "cart", "attrs": {"label": "Basket"}},
                    {"op": "mod", "id": "checkout",
                     "attrs": {"label": "Review"}}]})
        self.assertEqual(len(rec["tripwires"]), 1)
        self.assertEqual(len(rec["tripwires"][0]["changed_all"]), 2)

    def test_renaming_every_member_asks_nothing(self):
        # the silent half: a convergent edit is not a divergence
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "artifact": "checkout-flow",
            "ops": [{"op": "mod", "id": e, "attrs": {"label": "New " + e}}
                    for e in ("cart", "checkout", "payment", "confirm")]})
        self.assertEqual(rec["tripwires"], [])


class TestTidyRunsToAFixedPoint(Base):
    """tidy must settle, or say it cannot (BUG-02).

    Routing reads the other arrows' current paths and the fan then moves
    them, so one pass is not stable: on a real project tidy flip-flopped
    between two states with period 2, and each of five presses wrote a
    revision headlined "saved without changing anything".
    """

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def test_an_already_tidy_artifact_is_a_noop(self):
        self.store.tidy("checkout-flow")
        r = self.store.tidy("checkout-flow")
        self.assertTrue(r.get("noop"))
        self.assertIn("already tidy", r["summary"]["headline"])

    def test_repeated_presses_write_nothing(self):
        self.store.tidy("checkout-flow")
        head = self.store.head_revn()
        for _ in range(4):
            self.store.tidy("checkout-flow")
        self.assertEqual(self.store.head_revn(), head)

    def test_a_genuinely_untidy_artifact_still_commits(self):
        # the silent half: converging to a noop must not disable tidy
        for e in self.store.scenes["checkout-flow"]:
            if e["id"] == "payment":
                e["x"] = e["x"] + 3      # off the 4px grid
        r = self.store.tidy("checkout-flow")
        self.assertFalse(r.get("noop"), r["summary"]["headline"])

    def test_an_unknown_artifact_still_raises(self):
        with self.assertRaises(canvas.BatchError):
            self.store.tidy("ghost")


class TestNoOpRewireIsNotASequenceChange(Base):
    """Dropping an endpoint back on its own node is not a re-sequence.

    Excalidraw rewrites the binding OBJECT (focus, gap) on a drag, so
    the attribute showed in the diff while nothing was re-pointed — and
    two such facts tripped `sequence_reordered`, the one fact the flow
    reference tells the agent to LEAD WITH (brownfield BUG-01).
    """

    def setUp(self):
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def facts_after(self, mutate):
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        mutate({e["id"]: e for e in els})
        rec = self.store.commit(author="user",
                                new_scenes={"checkout-flow": els})
        return [f["fact"] for a in rec["artifacts"].values()
                for f in a["facts"]]

    def jiggle(self, ix, *aids):
        for aid in aids:
            for key in ("startBinding", "endBinding"):
                b = dict(ix[aid].get(key) or {})
                if b:
                    b["focus"] = round(b.get("focus", 0) + 0.11, 3)
                    b["gap"] = b.get("gap", 6) + 1
                    ix[aid][key] = b

    def test_a_no_op_rewire_emits_no_rewired_fact(self):
        self.assertNotIn("rewired", self.facts_after(
            lambda ix: self.jiggle(ix, "t1")))

    def test_two_no_op_rewires_do_not_claim_a_re_sequence(self):
        self.assertNotIn("sequence_reordered", self.facts_after(
            lambda ix: self.jiggle(ix, "t1", "t2")))

    def test_a_real_rewire_still_fires(self):
        # the silent half, and the one that matters
        def repoint(ix):
            ix["t1"]["endBinding"] = dict(ix["t1"]["endBinding"],
                                          elementId="payment")
        self.assertIn("rewired", self.facts_after(repoint))

    def test_two_real_rewires_do_claim_a_re_sequence(self):
        """The firing pole for `sequence_reordered` — the whole file's.

        The rule-8 census (2026-08-16) measured this fact asserted
        SILENT twice — the sibling above, and
        `TestDeletionConsequences.test_dangling_rewires_marked_
        consequence` — and asserted to FIRE nowhere in 12k lines. A
        detector that had stopped emitting it entirely would have passed
        both, and `sequence_reordered` is the one fact the flow
        reference tells the agent to LEAD WITH, so the silence was
        covering the loudest thing the differ can say.

        One variable separates this from the sibling: those bindings are
        jiggled and land back on their own nodes, these are re-pointed
        at different ones. Same fixture, same `facts_after` entry point,
        so the firing runs through the differ the silence reads.
        """
        def repoint(ix):
            ix["t1"]["endBinding"] = dict(ix["t1"]["endBinding"],
                                          elementId="payment")
            ix["t3"]["startBinding"] = dict(ix["t3"]["startBinding"],
                                            elementId="checkout")
        self.assertIn("sequence_reordered", self.facts_after(repoint))


def seed_domain_batch(base_revn=0):
    """Two entities and one relationship — the domain sibling of the flow seed.

    Args:
        base_revn: Head the batch is written against.

    Returns:
        An op batch creating `shop-domain`.
    """
    return {
        "base_revn": base_revn,
        "artifact": "shop-domain",
        "create": {"id": "shop-domain", "name": "Shop Domain",
                   "type": "domain", "concept": "shop",
                   "concept_name": "Shop"},
        "ops": [
            {"op": "add", "element": {"type": "rectangle", "id": "order",
                                      "label": "Order", "x": 40, "y": 40,
                                      "width": 140, "height": 60,
                                      "kind": "entity"}},
            {"op": "add", "element": {"type": "rectangle", "id": "customer",
                                      "label": "Customer", "x": 400,
                                      "y": 40, "width": 140, "height": 60,
                                      "kind": "entity"}},
            {"op": "add", "element": {"type": "rectangle", "id": "invoice",
                                      "label": "Invoice", "x": 400,
                                      "y": 240, "width": 140, "height": 60,
                                      "kind": "entity"}},
            {"op": "add", "element": {"type": "arrow", "id": "r1",
                                      "label": "placed by",
                                      "from": "order", "to": "customer"}},
        ],
    }


class TestNoOpRewireOnADomainArtifactIsNotARelationshipChange(Base):
    """BUG-01's guard, on the arm that never got it.

    The flow arm has compared normalized bindings since the brownfield
    round; the DOMAIN arm built its `relationship_rewired` fact from the
    presence of the attribute alone. So an endpoint dropped back on the
    entity it came from — which rewrites the binding OBJECT and nothing
    else — narrated as "rewired Order→Customer to Order→Customer": a
    change to the model, reported to the user, when the model did not
    move. Found by TASK-REROUTE (a re-route re-solves focus, which is
    the same edit), and verified reachable from a plain user save first.
    """

    def setUp(self):
        """Seed the domain artifact both poles are measured on."""
        super().setUp()
        self.store.apply_batch(seed_domain_batch())

    def facts_after(self, mutate):
        """Commit a mutated scene as the user and return its fact verbs.

        Args:
            mutate: Callback handed `{id: element}` for the scene copy.

        Returns:
            The fact verbs the save recorded.
        """
        els = [dict(e) for e in self.store.scenes["shop-domain"]]
        mutate({e["id"]: e for e in els})
        rec = self.store.commit(author="user",
                                new_scenes={"shop-domain": els})
        return [f["fact"] for a in rec["artifacts"].values()
                for f in a["facts"]]

    def test_a_re_aimed_binding_is_not_a_rewired_relationship(self):
        def jiggle(ix):
            b = dict(ix["r1"]["startBinding"])
            b["focus"] = round(b.get("focus", 0) + 0.11, 3)
            b["gap"] = b.get("gap", 6) + 1
            ix["r1"]["startBinding"] = b
        self.assertNotIn("relationship_rewired", self.facts_after(jiggle))

    def test_a_real_re_pointing_still_fires(self):
        # the firing half — the guard must not silence the fact itself
        def repoint(ix):
            ix["r1"]["endBinding"] = dict(ix["r1"]["endBinding"],
                                          elementId="invoice")
        self.assertIn("relationship_rewired", self.facts_after(repoint))


class TestRerouteScene(Base):
    """The callable surface: what a re-route touches, and what it will not.

    `reroute_scene` is the one mechanism behind the load-time NOTE, the
    `reroute` verb and (by design) TASK-FOCUS-FOLLOWUP's fixture rebase,
    so the authored-arrow boundary is asserted HERE rather than once per
    consumer.
    """

    def setUp(self):
        """Seed a flow drawn by today's router — the silent pole's scene."""
        super().setUp()
        self.store.apply_batch(seed_flow_batch())

    def fossilise(self, arrow_id="t1"):
        """Freeze one arrow the way an older router left it, and reload.

        The geometry is the shape that MATTERS: a foot standing on
        `cart`'s right edge with the line coming in 29° off square, and
        the same at `checkout`'s left. A fossil the user cannot see is
        not one this offers to fix, so a merely-different path would
        prove nothing about the detector.

        Re-stamps the route signature, so the arrow stays the SERVER's —
        which is the whole point: a fossil is geometry nobody hand-drew.

        Args:
            arrow_id: The arrow to overwrite.

        Returns:
            The reloaded store, off disk, through the real load path.
        """
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        for e in els:
            if e["id"] == arrow_id:
                e["x"], e["y"] = 180, 130
                e["points"] = [[0, 0], [80, 45]]
                canvas._stamp_route(e)
        self.store.commit(author="agent",
                          new_scenes={"checkout-flow": els},
                          base_revn=self.store.head_revn())
        return canvas.Store(self.project)

    def test_a_scene_drawn_today_has_nothing_to_re_route(self):
        """The everyday silent pole, through the real load path."""
        store = canvas.Store(self.project)
        self.assertEqual(store.legacy_routing(), {})
        self.assertEqual(store.legacy_routing_notes(), [])

    def test_a_fossil_is_found_and_named(self):
        store = self.fossilise()
        found = store.legacy_routing()
        self.assertEqual(list(found), ["checkout-flow"])
        self.assertIn("t1", [c["id"] for c in found["checkout-flow"]])
        self.assertIn("checkout-flow", store.legacy_routing_notes()[0])
        self.assertIn("reroute --artifact checkout-flow",
                      store.legacy_routing_notes()[0])

    def test_detection_mutates_nothing(self):
        """A load may notice; it may never redraw what the user last saw."""
        store = self.fossilise()
        raw = self.project.artifacts_dir / "checkout-flow.excalidraw"
        on_disk = raw.read_text()
        before = json.dumps(store.scenes["checkout-flow"], sort_keys=True)
        head = store.head_revn()
        self.assertTrue(store.legacy_routing(), "nothing was detected — "
                                                "this proves no silence")
        self.assertEqual(json.dumps(store.scenes["checkout-flow"],
                                    sort_keys=True), before)
        self.assertEqual(store.head_revn(), head)
        self.assertEqual(raw.read_text(), on_disk)

    def test_reroute_scene_does_not_touch_its_input(self):
        els = self.store.scenes["checkout-flow"]
        before = json.dumps(els, sort_keys=True)
        canvas.reroute_scene(els)
        self.assertEqual(json.dumps(els, sort_keys=True), before)

    def test_an_authored_path_is_neither_reported_nor_redrawn(self):
        """The boundary, in all three shapes `server_owns_geometry` knows.

        `routed: "authored"` (a `mod points` path), a signature that no
        longer matches (the user reshaped it on the canvas), and an
        unmarked bent path (a v0 scene). None of the three may appear in
        the report, and none may move when the re-route runs.
        """
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        ix = {e["id"]: e for e in els}
        ix["t1"]["customData"] = dict(ix["t1"].get("customData") or {},
                                      routed="authored")
        ix["t1"]["points"] = [[0, 0], [40, 40], [180, 0]]
        ix["t2"]["points"] = [[0, 0], [40, 40], [180, 0]]   # signature stale
        ix["t3"]["customData"] = {}                          # unmarked, bent
        ix["t3"]["points"] = [[0, 0], [40, 40], [180, 0]]
        out, changes = canvas.reroute_scene(els)
        self.assertEqual([c["id"] for c in changes
                          if c["id"] in ("t1", "t2", "t3")], [])
        after = {e["id"]: e for e in out}
        for aid in ("t1", "t2", "t3"):
            self.assertEqual(after[aid]["points"], ix[aid]["points"], aid)

    def test_a_one_ended_arrow_has_no_pair_to_route_between(self):
        """No pair, no route: its PATH is left exactly as it was drawn.

        It still shows up in the report, and that is not a leak — the
        shipped fan re-solves the binding focus of every server-owned
        arrow on every apply, one-ended ones included, and a focus that
        aims the client somewhere the path does not go is precisely what
        a fossil is. So the assertion is on the geometry, which the
        router may not touch, and not on the arrow's absence.
        """
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        for e in els:
            if e["id"] == "t1":
                e["endBinding"] = None
                e["points"] = [[0, 0], [180, 90]]
                canvas._stamp_route(e)
        was = {e["id"]: e for e in els}["t1"]
        out, changes = canvas.reroute_scene(els)
        now = {e["id"]: e for e in out}["t1"]
        self.assertEqual((now["x"], now["y"], now["points"]),
                         (was["x"], was["y"], was["points"]))
        self.assertEqual([c["moved_px"] for c in changes
                          if c["id"] == "t1"], [0.0])


class TestRerouteIsConsentGated(Base):
    """Report first, act only when told to, and stay revertible.

    The user's ruling (2026-08-17) took `--relayout`'s shape: a dry run
    that says what would change and names the save to revert to, an
    explicit flag that applies, and a narration that goes arrow by arrow
    rather than handing over one anonymous count.
    """

    def setUp(self):
        """Seed a flow and fossilise two of its arrows."""
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        for e in els:
            if e["id"] in ("t1", "t3"):
                e["x"], e["y"] = e.get("x", 0) + 30, e.get("y", 0) + 40
                e["points"] = [[0, 0], [150, -70]]
                canvas._stamp_route(e)
        self.store.commit(author="agent", new_scenes={"checkout-flow": els},
                          base_revn=self.store.head_revn())
        self.store = canvas.Store(self.project)

    def cli(self, **kw):
        """Run `cmd_reroute` and return its stdout lines.

        Args:
            **kw: Overrides for the arg namespace (`artifact`, `apply`).

        Returns:
            Stdout, split into lines.
        """
        ns = argparse.Namespace(project=self.tmp, artifact=None, apply=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            canvas.cmd_reroute(ns)
        return buf.getvalue().splitlines()

    def geometry(self):
        raw = json.loads((self.project.artifacts_dir /
                          "checkout-flow.excalidraw").read_text())
        return {e["id"]: (e.get("x"), e.get("y"), e.get("points"))
                for e in raw["elements"] if e.get("type") == "arrow"}

    def test_the_dry_run_reports_every_arrow_by_name(self):
        out = self.cli()
        named = [ln for ln in out if ln.startswith("REROUTE=")]
        self.assertEqual(len(named), 2, out)
        self.assertTrue(any("t1:" in ln for ln in named), out)
        self.assertTrue(any("t3:" in ln for ln in named), out)
        self.assertIn("APPLIED=false", out)
        self.assertTrue(any(ln.startswith("CHECKPOINT=revert save #")
                            for ln in out), out)

    def test_the_dry_run_changes_nothing(self):
        before, head = self.geometry(), self.store.head_revn()
        self.cli()
        self.assertEqual(self.geometry(), before)
        self.assertEqual(canvas.Store(self.project).head_revn(), head)

    def test_apply_without_an_artifact_refuses(self):
        before = self.geometry()
        with self.assertRaises(SystemExit) as e:
            self.cli(apply=True)
        self.assertEqual(e.exception.code, 2)
        self.assertEqual(self.geometry(), before)

    def test_apply_commits_and_narrates_arrow_by_arrow(self):
        rec = self.store.reroute("checkout-flow")
        facts = rec["artifacts"]["checkout-flow"]["facts"]
        self.assertEqual(sorted(f["element"] for f in facts
                                if f["fact"] == "rerouted"), ["t1", "t3"])
        self.assertIn("rerouted", rec["summary"]["headline"])
        self.assertIn("revert this save", rec.get("user_note") or "")
        self.assertIn("t1:", rec.get("user_note") or "")

    def test_the_save_is_the_checkpoint(self):
        before = self.geometry()
        rec = self.store.reroute("checkout-flow")
        self.assertNotEqual(self.geometry(), before)
        self.store.revert_to(rec["revn"] - 1)
        self.assertEqual(self.geometry(), before)

    def test_a_second_reroute_has_nothing_left_to_do(self):
        self.store.reroute("checkout-flow")
        head = self.store.head_revn()
        again = self.store.reroute("checkout-flow")
        self.assertTrue(again.get("noop"), again["summary"]["headline"])
        self.assertEqual(self.store.head_revn(), head)

    def test_the_note_goes_quiet_once_the_re_route_lands(self):
        self.store.reroute("checkout-flow")
        self.assertEqual(canvas.Store(self.project).legacy_routing_notes(),
                         [])

    def test_an_unknown_artifact_raises(self):
        with self.assertRaises(canvas.BatchError):
            self.store.reroute("ghost")


class TestRerouteReachesTheFossilsOnTheFrozenCorpus(unittest.TestCase):
    """The outcome claim, measured on the drawing rather than on the store.

    A foot whose final leg does not arrive square to the side it stands
    on is what a user SEES when they open a legacy artifact. Measured at
    this head over all 24 frozen artifacts (153 server-owned,
    both-ends-bound arrows): non-normal endpoints 60 before, 2 after,
    and the 2 are the two ends of one 12.6° arrow
    (`tearsheet-domain / r-earnings-issuer`) — the spike's number,
    reproduced.

    ASSERTED AS A RELATIONSHIP, not as 60 and 2. TASK-FOCUS-FOLLOWUP's
    fixture rebase will re-route these files, at which point the BEFORE
    becomes the after and an equality pin would fail for the right
    reason. What must never regress is that a re-route leaves the
    drawing no worse, and leaves at most a couple of oblique arrivals on
    a corpus this size.

    The bound is also what pins the roundness re-derivation:
    `reroute_scene` runs `rebuild_bound_elements` because a re-routed
    path otherwise keeps a curvature it can no longer earn — drop that
    line and this corpus comes out at 24, not 2.
    """

    NORMALS: ClassVar[dict] = {"top": (0, -1), "bottom": (0, 1),
                               "left": (-1, 0), "right": (1, 0)}

    def non_normal(self, els):
        """Endpoints whose drawn final leg leans more than 10° off square.

        Args:
            els: A scene's elements.

        Returns:
            `[(arrow_id, which_end)]` for every bound endpoint over the bar.
        """
        ix = {e["id"]: e for e in els}
        out = []
        for a in els:
            if a.get("type") != "arrow":
                continue
            pts = instruments.rendered_path(a)
            if len(pts) < 2:
                continue
            for which, key in (("start", "startBinding"),
                               ("end", "endBinding")):
                node = ix.get((a.get(key) or {}).get("elementId"))
                if node is None:
                    continue
                foot, prev = ((pts[0], pts[1]) if which == "start"
                              else (pts[-1], pts[-2]))
                side = canvas._edge_side(node, foot[0], foot[1])
                vx, vy = foot[0] - prev[0], foot[1] - prev[1]
                if not side or not (vx or vy):
                    continue
                nx, ny = self.NORMALS[side]
                cos = abs((vx * nx + vy * ny) / math.hypot(vx, vy))
                if math.degrees(math.acos(max(-1.0, min(1.0, cos)))) > 10.0:
                    out.append((a["id"], which))
        return out

    def test_a_re_route_leaves_the_corpus_arriving_square(self):
        root = Path(__file__).resolve().parent / "fixtures"
        before = after = owned = 0
        for path in sorted(root.glob("*/artifacts/*.excalidraw")):
            doc, _ = canvas.validate_scene(json.loads(path.read_text()),
                                           path.stem)
            els = canvas.normalize_scene_doc(doc)["elements"]
            ix = {e["id"]: e for e in els}
            owned += len([a for a in els if a.get("type") == "arrow"
                          and canvas.server_owns_geometry(a)
                          and ix.get((a.get("startBinding") or {})
                                     .get("elementId")) is not None
                          and ix.get((a.get("endBinding") or {})
                                     .get("elementId")) is not None])
            before += len(self.non_normal(els))
            after += len(self.non_normal(canvas.reroute_scene(els)[0]))
        self.assertGreater(owned, 100, "the corpus lost its arrows — this "
                                       "measures nothing")
        self.assertLessEqual(after, before)
        self.assertLessEqual(after, 2, "a re-route left %d oblique arrivals "
                                       "(was %d)" % (after, before))


class TestObliqueArrivals(Base):
    """The measure the offer is gated on, both poles.

    `_arrival_lean` — the curvature gate's arm — reads lean off the
    nearest CARDINAL, so an arrow arriving edge-on to the side it lands
    on scores a perfect 0.0. That is the `along` class, and it is a
    quarter of the endpoint residue, so a detector built on the gate's
    measure would have gone silent on the loudest fossils there are.
    """

    def scene(self, x, y, points):
        """One node and one arrow bound to it at a chosen angle.

        Args:
            x: The arrow's absolute x.
            y: The arrow's absolute y.
            points: The arrow's relative point list.

        Returns:
            The two-element scene.
        """
        return [canvas.normalize_element(e) for e in (
            {"id": "n1", "type": "rectangle", "x": 0, "y": 0,
             "width": 120, "height": 60},
            {"id": "a1", "type": "arrow", "x": x, "y": y, "points": points,
             "startBinding": {"elementId": "n1", "focus": 0, "gap": 6}})]

    def test_a_square_arrival_is_not_counted(self):
        # foot on the right edge, leg leaving straight out along it
        self.assertEqual(canvas.oblique_arrivals(
            self.scene(120, 30, [[0, 0], [90, 0]])), [])

    def test_a_leg_running_along_the_side_it_lands_on_is_counted(self):
        # the `along` class: foot on the TOP edge, leg horizontal — dead
        # cardinal, so `_arrival_lean` scores it 0.0 and sees nothing
        scene = self.scene(60, 0, [[0, 0], [90, 0]])
        self.assertEqual(canvas._arrival_lean(scene[1]), 0.0)
        self.assertEqual(canvas.oblique_arrivals(scene),
                         [("a1", "start")])

    def test_an_oblique_arrival_is_counted(self):
        self.assertEqual(canvas.oblique_arrivals(
            self.scene(120, 30, [[0, 0], [90, 60]])), [("a1", "start")])

    def test_an_unbound_end_has_nothing_to_be_square_to(self):
        scene = self.scene(120, 30, [[0, 0], [90, 60]])
        scene[1]["startBinding"] = None
        self.assertEqual(canvas.oblique_arrivals(scene), [])


class TestTheRerouteOfferWithdrawsItself(unittest.TestCase):
    """The nag, which is the defect this feature nearly shipped.

    The pipeline does not converge, so "one pass would change this
    scene" is true of a legacy artifact AND of its own re-routed
    successor. A detector built on that would have re-offered its own
    output after the user accepted it, and every press would have
    written another revision: BUG-02, with a fresh coat of paint.

    WHAT OSCILLATES IS `route_arrow` AGAINST AN OBSTACLE SET — not the
    fan, which an earlier draft of this docstring blamed. Ablated over
    the corpus: route-only orbits the same nine artifacts as the shipped
    route+fan pass, dropping the crossing term changes nothing, and both
    `fan only` and `route with no obstacles` orbit ZERO of 24. Obstacle-
    free routing is a pure function of the two endpoints and fixes on
    pass one. `TestTheOscillatorIsTheRouter` below holds that
    attribution to the measurement.

    THE FAN IS NEUTRAL HERE, WHICH IS NOT THE SAME AS HELPFUL, and the
    difference cost a correction: an earlier draft called it "a net
    stabiliser", which the ablation does not support in either
    direction. In the shipped configuration adding the fan to the router
    leaves the orbiting set byte-identical — the same nine artifacts by
    name, neither added to nor removed from. The one arm where it moves
    the count at all is obstacle-free routing, and there it moves it the
    WRONG way: 0/24 without the fan, 1/24 with it
    (`argus-r5 / daily-run`). Exonerated as the cause, not credited as
    the cure.

    Driven on the frozen corpus rather than on a synthetic pair, because
    the non-convergence is a property of real scenes: nine of the 24
    artifacts never settle and `argus-r5 / tuesday-triage` cycles with
    period SIX. Two synthetic arrows converge in one pass and prove
    nothing about the case that broke.
    """

    FIXTURE = "argus-r5"

    def setUp(self):
        """Copy the frozen project and open it."""
        self.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-reroute-"))
        src = Path(__file__).resolve().parent / "fixtures" / self.FIXTURE
        shutil.copytree(src, self.tmp / "project_knowledge")
        self.project = canvas.Project(self.tmp)
        self.store = canvas.Store(self.project)

    def tearDown(self):
        """Remove the temp copy and the shared runtime files."""
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p in (self.project.state_path, self.project.events_path,
                  self.project.log_path):
            if p.exists():
                p.unlink()

    def test_the_pipeline_really_does_not_settle(self):
        """The premise, and it is GREEN BY DESIGN — read that carefully.

        This asserts a DEFECT still exists, because `reroute_is_fossil`'s
        whole shape is a workaround for it. It is not a red awaiting a
        fix and must not be counted as one: it passes today and is meant
        to FAIL the day obstacle-aware routing is made idempotent, which
        is the day `reroute_is_fossil` can collapse to a fixed-point
        test and this class can go. Whoever lands that convergence owns
        this flip; do not weaken it quietly to make a suite green.

        A re-route of `tuesday-triage`'s output moves it again, and
        again — which is why the offer cannot be gated on "a pass would
        change something".
        """
        els = self.store.scenes["tuesday-triage"]
        once = canvas.reroute_scene(els)[0]
        self.assertTrue(canvas.reroute_scene(once)[1],
                        "a second pass changed nothing — the pipeline "
                        "converges now and `reroute_is_fossil` can be "
                        "simplified to a fixed-point test")

    def test_the_offer_stands_before_and_falls_after(self):
        flagged = sorted(self.store.legacy_routing())
        self.assertTrue(flagged, "nothing was offered — this fixture no "
                                 "longer carries legacy routing")
        for aid in flagged:
            self.assertFalse(self.store.reroute(aid).get("noop"), aid)
        reopened = canvas.Store(self.project)
        self.assertEqual(reopened.legacy_routing_notes(), [])
        for aid in flagged:
            self.assertTrue(reopened.reroute(aid).get("noop"), aid)

    def test_the_re_route_straightens_what_it_touched(self):
        flagged = sorted(self.store.legacy_routing())
        before = sum(len(canvas.oblique_arrivals(self.store.scenes[aid]))
                     for aid in flagged)
        for aid in flagged:
            self.store.reroute(aid)
        after = sum(len(canvas.oblique_arrivals(self.store.scenes[aid]))
                    for aid in flagged)
        self.assertLess(after, before)

    def test_declining_says_which_kind_of_decline_it_is(self):
        """`daily-run` is the scene the first draft lied about.

        Two crooked arrivals and six arrows a pass would move, and
        `reroute_is_fossil` says no because moving them straightens
        nothing. The decline sentence used to be "nothing arrives
        crooked", which the user can see is false.
        """
        head = self.store.reroute("daily-run")["summary"]["headline"]
        self.assertTrue(canvas.oblique_arrivals(
            self.store.scenes["daily-run"]), "the premise moved: daily-run "
                                             "has nothing crooked any more")
        self.assertNotIn("nothing on daily-run arrives crooked", head)
        self.assertIn("still arrive crooked", head)
        self.assertIn("would not straighten", head)

    def test_the_other_decline_still_says_nothing_is_crooked(self):
        # the silent half: a genuinely square drawing must not be told it
        # has crooked arrivals this cannot reach
        self.assertEqual(canvas.oblique_arrivals(
            self.store.scenes["edgar-late"]), [])
        self.assertIn("nothing on edgar-late arrives crooked",
                      self.store.reroute("edgar-late")["summary"]["headline"])


class TestTheOscillatorIsTheRouter(unittest.TestCase):
    """Which component actually fails to converge — held to the ablation.

    An earlier draft of `reroute_is_fossil` blamed "the router reads the
    other arrows' paths and the fan then moves them", naming two
    innocent parties: the fan and the crossing term. Measuring each arm
    separately over the frozen corpus exonerates both and leaves one
    mechanism standing — obstacle-aware routing, iterated.

    This is an attribution pin. Prose in a docstring rots silently; a
    wrong cause sends the next person to fix the wrong component, which
    is the more expensive failure.
    """

    def scenes(self):
        """Every frozen artifact, loaded the way a project would load it.

        Returns:
            `[(name, elements)]`.
        """
        root = Path(__file__).resolve().parent / "fixtures"
        out = []
        for path in sorted(root.glob("*/artifacts/*.excalidraw")):
            doc, _ = canvas.validate_scene(json.loads(path.read_text()),
                                           path.stem)
            out.append((path.parent.parent.name + "/" + path.stem,
                        canvas.normalize_scene_doc(doc)["elements"]))
        return out

    def one_pass(self, els, route=True, fan=True, obstacles=True):
        """A single pipeline pass with components ablated.

        Args:
            els: Scene elements. Not mutated.
            route: Run the router.
            fan: Run the attach-point fan.
            obstacles: Give the router an obstacle set at all.

        Returns:
            The new elements.
        """
        out = json.loads(json.dumps(els))
        ix = {e["id"]: e for e in out}
        obs = [e for e in out
               if e.get("type") in ("rectangle", "diamond", "ellipse")
               and canvas.role_of(e) not in ("label", "pin", "decoration",
                                             "annotation")] if obstacles \
            else []
        soft = [e for e in out if e.get("type") == "text"
                and (e.get("containerId") or
                     canvas.role_of(e) == "annotation")] if obstacles else []
        if route:
            for a in out:
                if a.get("type") != "arrow":
                    continue
                s = ix.get((a.get("startBinding") or {}).get("elementId"))
                d = ix.get((a.get("endBinding") or {}).get("elementId"))
                if s is None or d is None or \
                        not canvas.server_owns_geometry(a):
                    continue
                canvas.route_arrow(
                    a, s, d, obs, soft_obstacles=soft,
                    other_arrows=[(t["id"], t.get("x", 0), t.get("y", 0),
                                   t.get("points") or [])
                                  for t in out if t.get("type") == "arrow"
                                  and len(t.get("points") or []) >= 2])
                canvas.recenter_label(out, a)
        if fan:
            canvas.fan_attach_points(out)
        canvas.rebuild_bound_elements(out)
        return out

    def orbiters(self, **ablate):
        """Which artifacts never reach a fixed point under this arm.

        Args:
            **ablate: Passed to `one_pass`.

        Returns:
            The sorted names.
        """
        def h(els):
            return canvas.scene_hash(
                canvas.normalize_scene_doc({"elements": els})["elements"])

        bad = []
        for name, els in self.scenes():
            seen, cur = [h(els)], els
            for _ in range(12):
                cur = self.one_pass(cur, **ablate)
                hh = h(cur)
                if hh == seen[-1]:
                    break
                if hh in seen:
                    bad.append(name)
                    break
                seen.append(hh)
            else:
                bad.append(name)
        return sorted(bad)

    def test_the_router_alone_orbits_exactly_what_the_pipeline_does(self):
        self.assertEqual(self.orbiters(fan=False), self.orbiters())

    def test_the_fan_alone_is_a_fixed_point_everywhere(self):
        self.assertEqual(self.orbiters(route=False), [])

    def test_routing_without_obstacles_settles_on_the_first_pass(self):
        # a pure function of the two endpoints — nothing left to re-decide
        self.assertEqual(self.orbiters(fan=False, obstacles=False), [])

    def test_nine_frozen_artifacts_never_settle(self):
        """The count, made mechanical because prose disagreed about it.

        The task report said nine and fix-round review said eight, which
        a sentence in a markdown file cannot settle. Measured five ways
        here — full-scene, drawn-only and geometry-only hashes, and both
        "never settles" and "a second pass still moves it" — this is
        nine, and here it is by name so any future disagreement is a
        failing test naming the artifact rather than two prose numbers.
        """
        self.assertEqual(self.orbiters(), [
            "argus-r4-arm3/aggregation-flow",
            "argus-r4-arm3/argus-run-flow",
            "argus-r4-arm3/publication-flow",
            "argus-r4-arm4/daily-run-flow",
            "argus-r5/edgar-late",
            "argus-r5/enrichment-flow",
            "argus-r5/tuesday-triage",
            "tearsheet-demo/tearsheet-domain",
            "tearsheet-demo/tearsheet-pipeline"])


class TestRerouteSaysTheSameThingOnBothPaths(Base):
    """`--apply` through a server and `--apply` to disk, on one no-op.

    Both branches spelled the outcome by hand and disagreed on the one
    event they can both produce: offline said `APPLIED=false NOOP=true`,
    the server said `APPLIED=true NOOP=true` — one key, two surfaces,
    opposite values — and `CHECKPOINT=` appeared on the offline apply
    and nowhere else. Neither path had a test at all, which is why the
    disagreement could exist.

    The server is patched rather than run, the way `cmd_apply`'s own
    branch tests do it: this measures what the command PRINTS, not what
    a socket does. `/api/reroute`'s handler is exercised directly for
    the half a patched response cannot cover.
    """

    def setUp(self):
        """Seed a flow and fossilise two arrows into crooked arrivals."""
        super().setUp()
        self.store.apply_batch(seed_flow_batch())
        els = [dict(e) for e in self.store.scenes["checkout-flow"]]
        for e in els:
            if e["id"] in ("t1", "t3"):
                e["x"], e["y"] = e.get("x", 0) + 30, e.get("y", 0) + 40
                e["points"] = [[0, 0], [150, -70]]
                canvas._stamp_route(e)
        self.store.commit(author="agent", new_scenes={"checkout-flow": els},
                          base_revn=self.store.head_revn())
        self.store = canvas.Store(self.project)

    def cli(self, resp=None, **kw):
        """Run `cmd_reroute --apply`, optionally against a fake server.

        Args:
            resp: The `/api/reroute` payload to answer with. None runs
                the offline branch.
            **kw: Overrides for the arg namespace.

        Returns:
            Stdout as a `{KEY: value}` dict.
        """
        ns = argparse.Namespace(project=self.tmp, artifact="checkout-flow",
                                apply=True)
        for k, v in kw.items():
            setattr(ns, k, v)
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(buf))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            if resp is not None:
                self.project.state_path.write_text(json.dumps(
                    {"url": "http://127.0.0.1:1/", "port": 1, "pid": 1,
                     "protocol_version": canvas.PROTOCOL_VERSION}),
                    encoding="utf-8")
                stack.enter_context(mock.patch.object(
                    canvas, "server_alive", lambda s: True))
                stack.enter_context(mock.patch.object(
                    canvas, "http_json", lambda *a, **k: resp))
            else:
                stack.enter_context(mock.patch.object(
                    canvas, "server_alive", lambda s: False))
            canvas.cmd_reroute(ns)
        return dict(ln.split("=", 1) for ln in buf.getvalue().splitlines()
                    if "=" in ln and not ln.startswith("REROUTE="))

    def test_a_committed_apply_reads_the_same_either_way(self):
        offline = self.cli()
        server = self.cli(resp={"ok": True, "revn": offline["REVN"],
                                "short_id": offline.get("SHORT_ID"),
                                "noop": False,
                                "headline": offline["HEADLINE"]})
        for key in ("APPLIED", "NOOP", "REVN", "SHORT_ID", "HEADLINE",
                    "CHECKPOINT"):
            self.assertEqual(offline.get(key), server.get(key), key)
        self.assertEqual(offline["APPLIED"], "true")
        self.assertEqual(offline["NOOP"], "false")
        self.assertIn("revert save #", offline["CHECKPOINT"])

    def test_a_no_op_reads_the_same_either_way(self):
        self.store.reroute("checkout-flow")          # spend the offer
        head = canvas.Store(self.project).head_revn()
        offline = self.cli()
        server = self.cli(resp={"ok": True, "revn": head, "noop": True,
                                "headline": offline["HEADLINE"]})
        for key in ("APPLIED", "NOOP", "REVN", "HEADLINE", "CHECKPOINT"):
            self.assertEqual(offline.get(key), server.get(key), key)
        self.assertEqual(offline["APPLIED"], "false")
        self.assertEqual(offline["NOOP"], "true")

    def test_the_checkpoint_is_printed_on_a_no_op_too(self):
        # a key that vanishes when the news is good reads as "nobody
        # said", not as "nothing happened" (the QUARANTINED= rule)
        self.store.reroute("checkout-flow")
        out = self.cli()
        self.assertIn("CHECKPOINT", out)
        self.assertIn("nothing was written", out["CHECKPOINT"])

    def test_the_endpoint_commits_and_reports_its_save(self):
        app = canvas.ServerApp(self.project)
        try:
            resp = app.handle_post("/api/reroute",
                                   {"artifact": "checkout-flow"})
            self.assertTrue(resp["ok"])
            self.assertFalse(resp["noop"])
            self.assertIn("short_id", resp)
            self.assertIn("rerouted", resp["headline"])
            again = app.handle_post("/api/reroute",
                                    {"artifact": "checkout-flow"})
            self.assertTrue(again["noop"])
            self.assertEqual(again["revn"], resp["revn"])
        finally:
            app.log_file.close()

    def test_the_endpoint_refuses_an_unknown_artifact(self):
        app = canvas.ServerApp(self.project)
        try:
            with self.assertRaises(canvas._Err) as e:
                app.handle_post("/api/reroute", {"artifact": "ghost"})
            self.assertEqual(e.exception.status, 400)
        finally:
            app.log_file.close()


class TestDivergenceVerbs(Base):
    """Only meaning-changing facts arm a divergence tripwire (v0.6).

    All 20 tripwires in the v0.5 assessment session were the agent's own
    tooltip and layout edits on mapped elements; it eventually spent two
    `kinds` annotations scoping mappings to `moved` purely to stop the
    recurrence. Moving a box 40px is not a disagreement (R2-6).
    """

    def test_every_listed_verb_is_a_real_fact(self):
        # a typo here silently disarms a tripwire, which is the worst
        # possible failure mode for this constant — so pin it against the
        # verbs the differ actually emits
        src = Path(canvas.__file__).read_text(encoding="utf-8")
        emitted = set(re.findall(r'F\("([a-z_]+)"', src))
        self.assertTrue(emitted, "could not read the fact vocabulary")
        unknown = canvas.DIVERGENCE_VERBS - emitted
        self.assertEqual(unknown, set(),
                         "not emitted by semantic_facts: %s" % unknown)

    def test_presentation_verbs_are_excluded(self):
        for verb in ("moved", "block_moved_within_screen", "reordered",
                     "tooltip_changed", "tooltip_added", "resized",
                     "restyled", "regrouped", "pin_added"):
            self.assertNotIn(verb, canvas.DIVERGENCE_VERBS, verb)

    def test_meaning_verbs_are_included(self):
        for verb in ("renamed", "label_renamed", "entity_renamed",
                     "rewired", "cardinality_changed", "value_changed",
                     "state_toggled", "entity_deleted"):
            self.assertIn(verb, canvas.DIVERGENCE_VERBS, verb)

    def test_the_naming_subset_is_a_subset(self):
        """`NAMING_VERBS` was a literal in two arms and is now one name.

        It is spelled once because three places need exactly it, and the
        risk of the constant is the same as its parent's: a verb that
        drifts out of `DIVERGENCE_VERBS` while staying here would arm a
        glossary question for a fact no tripwire scope watches.
        """
        self.assertTrue(canvas.NAMING_VERBS)
        self.assertLessEqual(canvas.NAMING_VERBS, canvas.DIVERGENCE_VERBS)


class TestGlossaryDivergence(Base):
    """Renaming a drawn element off a settled term (r5-4).

    Divergence detection ran in exactly one scope — a declared `mapping`
    — and the v0.5 assessment found ZERO of eight domain entities mapped
    to their identically-named concepts, so the detector had no domain
    coverage at all: the drawing could rename `Booking` to `Reservation`
    while CONTEXT.md still defined `Booking` and nothing asked. Every
    test here is half a pair; the silent poles are the ones that make
    the firing pole worth having, since a check that questioned every
    rename would be the same channel wrong in the other direction.
    """

    TERMS = ("# Context\n\n## Glossary\n\n"
             "- **Booking**: a reserved slot\n"
             "- **Vendor**: the supplier\n")

    def seed(self, glossary=TERMS, labels=("Booking", "Trolley")):
        """Create a two-entity domain artifact and settle a glossary.

        Args:
            glossary: CONTEXT.md's body, or None to write no file.
            labels: The two entities' labels.

        Returns:
            The store's record for the seeding batch.
        """
        if glossary is not None:
            (self.project.pk / "CONTEXT.md").write_text(glossary,
                                                        encoding="utf-8")
        ops = [{"op": "add", "element": {
            "type": "rectangle", "id": "ent%d" % i, "label": lbl,
            "x": 100 + i * 300, "y": 100, "width": 160, "height": 80,
            "kind": "entity", "role": "node"}}
            for i, lbl in enumerate(labels)]
        rec, _ = self.store.apply_batch({
            "base_revn": 0, "artifact": "dom",
            "create": {"id": "dom", "name": "Shop", "type": "domain"},
            "ops": ops})
        return rec

    def rename(self, eid, to, base_revn=1):
        """Relabel one entity and return the batch's tripwires.

        Args:
            eid: The entity's id; its bound label is `<eid>-label`.
            to: The new label text.
            base_revn: The revision to apply against.

        Returns:
            The record's tripwire list.
        """
        rec, _ = self.store.apply_batch({
            "base_revn": base_revn, "artifact": "dom",
            "ops": [{"op": "mod", "id": eid + "-label",
                     "attrs": {"text": to}}]})
        return rec["tripwires"]

    def test_renaming_a_glossary_term_fires_a_divergence_challenge(self):
        """r5-4: the drawing walks off the term and is asked about it."""
        self.seed()
        tws = self.rename("ent0", "Reservation")
        self.assertEqual(len(tws), 1, tws)
        self.assertEqual(tws[0]["kind"], "glossary")
        self.assertEqual(tws[0]["sibling"], "Booking")
        self.assertIn("Booking", tws[0]["question"])
        self.assertIn("Reservation", tws[0]["question"])

    def test_red_an_answered_glossary_challenge_is_asked_again(self):
        """A settled ruling does not survive the next entity (MAJOR-2).

        FLIPPED by TASK-POLISH: answering a glossary challenge records a
        ruling in `registry["glossary_rulings"]`, keyed on the term by
        `_glossary_key` — the same key the tripwire already carried, so
        the dedupe and the ruling cannot spell it differently. The three
        poles below say what the ruling is NOT: not a reading of the
        answer, not a blanket mute, and not something a dismissal earns.

        The mapping arm has three suppression paths — the
        `intentionally-divergent` note, `_annotation_covers` for `kinds`
        scoping, `_policy_covers` for class-level policies. The glossary
        arm has none. Its only suppression is the open-tripwire dedupe

            seen = {t.get("mapping") for t in self.registry["tripwires"]
                    if t.get("status") == "open"}

        which is a while-you-have-not-answered-yet guard, not a ruling.
        The moment the user answers, the key leaves `seen` and the same
        question is armed again for every remaining entity drawn from
        that term — up to seven more asks on the eight-entity domain
        diagram this scope was built for.

        ONLY THE CONVERGENT ANSWER IS SELF-LIMITING: "the glossary
        follows" rewrites CONTEXT.md and the term stops existing, so the
        question cannot recur. "Intentional divergence" — the answer the
        question is FOR — leaves the term settled and everything else
        re-armed. That is the failure the repo already paid for once
        (v0.6 r3-7, three tripwires for one cause), and the shape
        `_glossary_divergence`'s own docstring rejects auto-mapping to
        avoid.

        The fix is a recorded ruling keyed on the TERM, the glossary
        analogue of `intentionally-divergent`. OWNER: TASK-POLISH.
        Curator batch 27, 2026-08-17, from the Task 28 review.
        """
        self.seed(labels=("Booking", "Booking"))
        first = self.rename("ent0", "Reservation")
        self.assertEqual(len(first), 1, "the first challenge did not fire")
        self.store.answer_tripwire(first[0]["id"], "Intentional divergence")
        again = self.rename("ent1", "Slot", base_revn=2)
        self.assertEqual(
            again, [],
            "'Booking' was ruled on and the ruling did not stick: renaming "
            "a second entity off the same settled term re-asks it — %r"
            % ([t.get("question") for t in again],))

    def test_a_ruling_on_one_term_does_not_mute_another(self):
        """The ruling is keyed on the TERM, so it silences one question.

        The failure mode a suppression path invites is the one SKILL.md
        warns about for unscoped mapping annotations — muting everything
        forever because one thing was excused once. `Booking` is ruled
        on; `Vendor`, a second settled term on the same drawing, walks
        off it in the very next batch and is still asked about. That is
        what makes the flip above a statement about the QUESTION being
        settled rather than about the arm going quiet.
        """
        self.seed(labels=("Booking", "Vendor"))
        first = self.rename("ent0", "Reservation")
        self.store.answer_tripwire(first[0]["id"], "Intentional divergence")
        again = self.rename("ent1", "Supplier", base_revn=2)
        self.assertEqual(len(again), 1,
                         "ruling on 'Booking' silenced 'Vendor' too — the "
                         "ruling is keyed on the term, not on the arm")
        self.assertEqual(again[0]["sibling"], "Vendor")

    def test_the_ruling_does_not_read_which_way_the_user_ruled(self):
        """Free text settles the question; the check issues no verdict.

        The two offered choices point the AGENT in opposite directions —
        "the glossary follows" sends it to CONTEXT.md, "intentional
        divergence" sends it nowhere — but both mean the user has been
        asked and has answered, which is the only thing this check is
        entitled to conclude. The answer here is neither choice and not
        parseable as either; deciding it did not count would be the
        tripwire ruling on an answer instead of asking a question.
        """
        self.seed(labels=("Booking", "Booking"))
        first = self.rename("ent0", "Reservation")
        self.store.answer_tripwire(
            first[0]["id"], "both are wrong, we'll settle it on Thursday")
        self.assertEqual(
            self.rename("ent1", "Slot", base_revn=2), [],
            "an answer the check could not classify was treated as no "
            "answer — the user was asked and replied")

    def test_the_ruling_outlives_the_process_that_recorded_it(self):
        """A ruling held only in memory is a ruling until the next start.

        `glossary_rulings` is registry state and branch-scoped like the
        tripwires it settles, so this reloads the project from disk
        rather than trusting the live object — the defect being fixed was
        a suppression that did not survive an ANSWER, and a fix that did
        not survive a RESTART would be the same bug one lifetime longer.
        """
        self.seed(labels=("Booking", "Booking"))
        first = self.rename("ent0", "Reservation")
        self.store.answer_tripwire(first[0]["id"], "Intentional divergence")
        reloaded = canvas.Store(canvas.Project(self.tmp))
        rec, _ = reloaded.apply_batch({
            "base_revn": 2, "artifact": "dom",
            "ops": [{"op": "mod", "id": "ent1-label",
                     "attrs": {"text": "Slot"}}]})
        self.assertEqual(rec["tripwires"], [],
                         "the ruling did not reach disk — re-asked after a "
                         "reload")

    def test_dismissing_a_challenge_unanswered_does_not_rule_on_it(self):
        """Resolving is the agent closing a card; only an answer rules.

        The boundary is deliberate and is why `_glossary_ruling` hangs
        off `answer_tripwire` and not off the resolve op. `resolve` is
        housekeeping the AGENT does — it can run without the user ever
        seeing the question — so treating it as a ruling would let the
        thing being asked about silence the question about it. An
        unanswered question that gets closed is not settled, and the
        next entity off that term asks again.
        """
        self.seed(labels=("Booking", "Booking"))
        first = self.rename("ent0", "Reservation")
        self.store.apply_batch({
            "base_revn": 2, "artifact": "dom",
            "ops": [{"op": "registry", "action": "resolve_tripwire",
                     "id": first[0]["id"]}]})
        self.assertEqual(
            self.store.registry.get("glossary_rulings") or {}, {},
            "closing an unanswered card recorded a ruling nobody gave")
        again = self.rename("ent1", "Slot", base_revn=3)
        self.assertEqual(len(again), 1,
                         "a dismissed question was treated as a settled one")

    def test_an_open_glossary_challenge_is_not_asked_twice(self):
        """The green pole: the dedupe that DOES exist works.

        While the first challenge is still open, a second entity leaving
        the same term adds no second tripwire. This is what makes the red
        above a statement about RULINGS rather than about the glossary
        arm being unguarded — the guard is real, it is just scoped to the
        window before an answer instead of after one.
        """
        self.seed(labels=("Booking", "Booking"))
        self.assertEqual(len(self.rename("ent0", "Reservation")), 1)
        self.assertEqual(self.rename("ent1", "Slot", base_revn=2), [])

    def test_the_glossary_challenge_flag_is_stamped_and_read_by_nobody(self):
        """`glossary_challenge=True` is inert, and now actively misleading.

        Stamped unconditionally on every `entity_renamed` fact at one
        site and read NOWHERE — not by canvas.py, not by a test, not by
        the frontend, not by a reference doc. It ships: two of the
        `argus-r5` save fixtures carry it.

        WHAT MAKES IT WORSE THAN DEAD is that a real mechanism now
        answers the same question correctly — a glossary tripwire fires
        only when the OLD label was a settled term — so the flag and the
        tripwire list DISAGREE on every rename of a non-term entity, and
        the flag is the one that says "challenge" on all of them.

        Pinned as a TRIPWIRE rather than as a red: the field is
        harmless while nothing reads it, and this fails the day someone
        adds a reader without deciding what it should mean. Either
        outcome is fine and both require this test to be edited, which
        is the whole point. OWNER of the ruling: TASK-POLISH, with
        MAJOR-2 above. Curator batch 27, 2026-08-17.

        THIS FILE IS ON THE EXPECTED LIST because writing the census
        made it a mention — a self-reference, named rather than filtered
        out, since a filter would also hide a real reader added here.
        `canvas.py`'s count is asserted separately at ONE, which is the
        claim that actually matters: one producer.

        CODE ONLY, and that boundary was learned the same hour: the
        first version walked `.md` too and went red the moment the
        handover named the red method above, whose own name contains the
        string. A CONSUMER IS CODE. Prose about a dead field is not a
        reader of it, and a census that counts sentences is an anchor on
        a moving corpus — this file's other subject entirely.

        THE SCAN SURFACE IS `git ls-files`, and that boundary was learned
        the hard way one fold later. The first version walked the tree
        with `rglob` and skipped `node_modules` and `.git` BY NAME, which
        is an exclusion list — it passed in the clean worktree it was
        written in and failed in the shared checkout, which carries seven
        untracked `.scratch/<task>/base/canvas.py` probe workspaces that
        earlier tasks left as cited evidence. Every one of them is a
        `glossary_challenge` site by copy, and none of them is a
        consumer.

        An exclusion list is a hand-maintained enumeration of what to
        ignore, which is the shape this whole batch is about: it is
        correct the day it is written and silently wrong the day someone
        creates a directory nobody listed. Asking git makes "tracked"
        TRUE rather than asserted, and gitignored residue unreachable BY
        CONSTRUCTION. `tests/livedoc.py` had already solved this and its
        comment says so; this is the same fix, and the empty-surface
        refusal below is copied from it for the same reason — a scan that
        found nothing would let this assertion pass by measuring nothing.
        """
        root = Path(canvas.__file__).resolve().parents[3]
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z", "--",
                 "*.py", "*.ts", "*.tsx"],
                capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise AssertionError(
                "git could not list tracked sources under %s (%s). An "
                "empty scan surface would let this census pass by "
                "measuring nothing" % (root, exc)) from exc
        tracked = sorted(n for n in out.stdout.split("\0") if n)
        self.assertGreater(
            len(tracked), 10,
            "the tracked-source scan surface is %d files, which is not a "
            "checkout of this repo — refusing to report a census over it"
            % len(tracked))
        readers = []
        for name in tracked:
            try:
                text = (root / name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "glossary_challenge" in text:
                readers.append(name)
        self.assertEqual(
            readers, ["skills/wysiwyg-grilling/scripts/canvas.py",
                      "tests/test_backend.py"],
            "`glossary_challenge` gained or lost a site. It was one "
            "producer and zero consumers at curator batch 27; if a "
            "consumer arrived, the flag needs a meaning first — it is "
            "stamped on EVERY entity rename while the glossary tripwire "
            "fires only on settled terms, so the two disagree by design")
        self.assertEqual(
            Path(canvas.__file__).read_text(encoding="utf-8").count(
                "glossary_challenge"), 1,
            "the producer is supposed to be a single unconditional stamp")

    def test_renaming_a_genuinely_new_entity_is_silent(self):
        """The control the plan names: `Trolley` is nobody's term.

        Without this, questioning every rename would pass the test above
        — and that is the failure this whole work package exists to stop,
        a channel wrong in the other direction.
        """
        self.seed()
        self.assertEqual(self.rename("ent1", "Basket"), [])

    def test_renaming_toward_a_term_is_silent(self):
        """Convergence is not divergence — the gate is the OLD label.

        `Trolley` becoming `Vendor` moves the drawing INTO the glossary's
        vocabulary. A check gated on the new name would fire here, which
        would be scolding an agent for adopting the settled word.
        """
        self.seed()
        self.assertEqual(self.rename("ent1", "Vendor"), [])

    def test_one_question_for_two_entities_drawn_from_one_term(self):
        """Two views of one term, renamed together, ask once.

        The mapping arm learned this the expensive way: renaming one
        member of a four-member mapping asked three questions, and the
        agent that met it answered the cause and not the count (v0.6
        r3-7). A term is the cause here, so BOTH entities carry the same
        label — an earlier draft renamed one entity twice in a row, which
        passes whether the dedupe exists or not, because after the first
        rename its old label is no longer a term. Measured: deleting the
        dedupe moved nothing against that version.
        """
        self.seed(labels=("Booking", "Booking"))
        rec, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "dom", "ops": [
                {"op": "mod", "id": "ent0-label",
                 "attrs": {"text": "Reservation"}},
                {"op": "mod", "id": "ent1-label",
                 "attrs": {"text": "Slot"}}]})
        self.assertEqual(len(rec["tripwires"]), 1, rec["tripwires"])

    def test_an_open_question_is_not_re_asked_next_revision(self):
        """The same term drifting again while the first ask is open."""
        self.seed(labels=("Booking", "Booking"))
        self.assertEqual(len(self.rename("ent0", "Reservation")), 1)
        self.assertEqual(self.rename("ent1", "Slot", base_revn=2), [])

    def test_a_project_with_no_glossary_asks_nothing(self):
        """No CONTEXT.md is the ordinary state of a first round.

        A missing glossary must read as "no terms settled yet", never as
        an error and never as a reason to go quiet about real mappings —
        so this asserts the rename lands cleanly rather than that some
        exception was swallowed.
        """
        rec = self.seed(glossary=None)
        self.assertEqual(rec["revn"], 1)
        self.assertEqual(self.rename("ent0", "Reservation"), [])

    def test_the_challenge_is_answerable_where_it_fired(self):
        """It carries the same affordances a mapping tripwire does.

        A tripwire the agent cannot resolve is a permanent nag, and this
        one has no mapping to annotate — so the id, the choices and the
        open status are what make it closable at all.
        """
        self.seed()
        self.rename("ent0", "Reservation")
        open_tws = [t for t in self.store.registry["tripwires"]
                    if t.get("status") == "open"]
        self.assertEqual(len(open_tws), 1)
        t = open_tws[0]
        self.assertTrue(t["id"])
        self.assertEqual(len(t["choices"]), 2)
        self.assertIn("glossary follows", t["choices"][0])
        self.assertIn("Booking", t["detail"])


class TestStateVariantFrames(Base):
    """A rename landing on one frame of a screen and not its twin (v0.6).

    The demo's flagship beat. 3.2.4 joins a wireframe element to a FLOW
    element through a mapping and tripwires compare mapped siblings
    ACROSS artifacts, so two frames of one screen inside one artifact
    were compared by nothing — the divergence shipped and the lint said
    nothing about it (v0.5 assessment R2-4).
    """

    def screens(self, normal, degraded, variant_of=False):
        """Two frames, one block each, at matching row baselines."""
        els = []
        for fid, lbl, fx in (("f-normal", normal, 0),
                             ("f-degraded", degraded, 900)):
            frame = {"id": fid, "type": "frame", "x": fx, "y": 100,
                     "width": 720, "height": 480,
                     "name": fid.replace("f-", "")}
            if variant_of and fid == "f-degraded":
                frame["customData"] = {"variant_of": "f-normal"}
            els.append(frame)
            els.append({"id": fid + "-kpi", "type": "rectangle",
                        "x": fx + 20, "y": 212, "width": 160,
                        "height": 60, "frameId": fid,
                        "customData": {"role": "node", "kind": "block"}})
            els.append({"id": fid + "-kpi-l", "type": "text",
                        "x": fx + 24, "y": 228, "width": 100,
                        "height": 20, "text": lbl, "originalText": lbl,
                        "containerId": fid + "-kpi"})
        return els

    def test_divergent_label_across_variant_frames_warns(self):
        warns = canvas.lint_layout(
            self.screens("Excess Return", "Alpha"),
            artifact_type="wireframe", aid="dash")["warnings"]
        self.assertTrue(any("same block" in w for w in warns), warns)

    def test_matching_labels_are_silent(self):
        warns = canvas.lint_layout(
            self.screens("Excess Return", "Excess Return"),
            artifact_type="wireframe", aid="dash")["warnings"]
        self.assertFalse(any("same block" in w for w in warns), warns)

    def test_declared_variant_is_paired(self):
        warns = canvas.lint_layout(
            self.screens("Excess Return", "Alpha", variant_of=True),
            artifact_type="wireframe", aid="dash")["warnings"]
        self.assertTrue(any("same block" in w for w in warns), warns)

    def test_a_waive_silences_it(self):
        els = self.screens("Weekly Brief", "Weekly Brief — HELD")
        warns = canvas.lint_layout(
            els, artifact_type="wireframe", aid="dash")["warnings"]
        key = next(w.split("key: ")[1].split(",")[0].strip("'\"")
                   for w in warns if "same block" in w)
        again = canvas.lint_layout(els, artifact_type="wireframe",
                                   aid="dash", waives={key: "held copy"})
        self.assertFalse(any("same block" in w
                             for w in again["warnings"]), again)

    def test_frames_of_different_shape_are_not_a_variant_set(self):
        els = self.screens("Excess Return", "Alpha")
        # give the degraded frame a second block: no longer diffable
        els.append({"id": "extra", "type": "rectangle", "x": 920,
                    "y": 300, "width": 160, "height": 60,
                    "frameId": "f-degraded",
                    "customData": {"role": "node", "kind": "block"}})
        warns = canvas.lint_layout(els, artifact_type="wireframe",
                                   aid="dash")["warnings"]
        self.assertFalse(any("same block" in w for w in warns), warns)


class TestCheckRender(Base):
    """`check_batch` hands back the scene it would have drawn (v0.6).

    Under `pulled` cadence the agent cannot see a queued revision, which
    is exactly the feedback legibility needs — it said so twice in the
    v0.5 assessment and then hand-rolled a copy-the-project workaround,
    as the v0.4 agent had before it.
    """

    def batch(self):
        return {"base_revn": 0, "artifact": "f",
                "create": {"id": "f", "name": "F", "type": "flow",
                           "concept": "f", "concept_name": "F"},
                "ops": [{"op": "add", "element": {
                    "type": "rectangle", "id": "a", "kind": "step",
                    "label": "Ingest", "x": 100, "y": 100}}]}

    def test_check_returns_the_would_be_elements(self):
        r = self.store.check_batch(self.batch())
        self.assertTrue(r["ok"])
        self.assertTrue(any(e["id"] == "a" for e in r["elements"]))

    def test_check_commits_nothing(self):
        self.store.check_batch(self.batch())
        self.assertNotIn("f", self.store.scenes)

    def test_rejected_batch_has_no_elements_to_draw(self):
        bad = self.batch()
        bad["ops"] = [{"op": "mod", "id": "ghost", "attrs": {"label": "x"}}]
        r = self.store.check_batch(bad)
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("elements", []), [])

    def test_the_would_be_scene_renders(self):
        r = self.store.check_batch(self.batch())
        svg, w, h = canvas.render_svg(r["elements"], title="f (proposed)")
        self.assertIn("Ingest", svg)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)


class TestDecorationFollowsItsBox(Base):
    """An image placeholder's X strokes track its geometry (v0.6).

    The `kind: image` composite derives its two diagonals from the box's
    width/height at creation time. A later resize used to leave them at
    the old span — the X overshot its own box into the panel below, and
    no lint could see it because decorations are filtered out of every
    geometry check (v0.5 assessment R2-10).
    """

    def placeholder(self, w=680, h=116):
        self.store.apply_batch({
            "base_revn": 0, "artifact": "wf",
            "create": {"id": "wf", "name": "WF", "type": "wireframe",
                       "concept": "wf", "concept_name": "WF"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "charts", "kind": "image",
                "label": "Charts", "x": 120, "y": 304,
                "width": w, "height": h}}]})

    def strokes(self):
        return {e["id"]: e for e in self.store.scenes["wf"]
                if e["id"] in ("charts-x1", "charts-x2")}

    def test_resize_re_derives_the_diagonals(self):
        self.placeholder()
        self.store.apply_batch({
            "base_revn": 1, "artifact": "wf",
            "ops": [{"op": "mod", "id": "charts",
                     "attrs": {"height": 72}}]})
        s = self.strokes()
        self.assertEqual(s["charts-x1"]["height"], 72)
        self.assertEqual(s["charts-x1"]["points"][-1], [680, 72])
        # x2 runs bottom-left to top-right, so its origin moves too
        self.assertEqual(s["charts-x2"]["y"], 304 + 72)
        self.assertEqual(s["charts-x2"]["points"][-1], [680, -72])

    def test_width_resize_re_derives_too(self):
        self.placeholder()
        self.store.apply_batch({
            "base_revn": 1, "artifact": "wf",
            "ops": [{"op": "mod", "id": "charts",
                     "attrs": {"width": 400}}]})
        self.assertEqual(self.strokes()["charts-x1"]["points"][-1],
                         [400, 116])

    def test_move_still_translates_the_diagonals(self):
        # the pre-existing x/y behaviour must survive the width/height fix
        self.placeholder()
        self.store.apply_batch({
            "base_revn": 1, "artifact": "wf",
            "ops": [{"op": "mod", "id": "charts", "attrs": {"x": 200}}]})
        self.assertEqual(self.strokes()["charts-x1"]["x"], 200)

    def test_a_stale_decoration_is_linted(self):
        # the state the fixture was found in: box shrunk, strokes not
        self.placeholder()
        for e in self.store.scenes["wf"]:
            if e["id"] == "charts":
                e["height"] = 72
        warns = canvas.lint_layout(self.store.scenes["wf"],
                                   artifact_type="wireframe")["notes"]
        self.assertTrue(any("extends" in w and "grouped with" in w
                            for w in warns), warns)

    def test_a_decoration_that_fits_is_silent(self):
        self.placeholder()
        notes = canvas.lint_layout(self.store.scenes["wf"],
                                   artifact_type="wireframe")["notes"]
        self.assertFalse(any("extends" in n and "grouped with" in n
                             for n in notes), notes)


class TestRouterTotalityAndSelfLoops(Base):
    """WP1 (r4-11, D8, E-8, E-9): routing is total, reflexive arrows are
    a supported relationship class, and no failure reaches the agent as
    a traceback."""

    def flow_nodes(self):
        """Seed a 3-node flow (source -> transform -> sink).

        Returns:
            The seeded elements list.
        """
        return [
            {"id": "s", "type": "rectangle", "x": 0, "y": 0,
             "width": 100, "height": 50,
             "customData": {"kind": "source", "role": "node"}},
            {"id": "t", "type": "rectangle", "x": 300, "y": 0,
             "width": 100, "height": 50,
             "customData": {"kind": "transform", "role": "node"}},
            {"id": "k", "type": "rectangle", "x": 600, "y": 0,
             "width": 100, "height": 50,
             "customData": {"kind": "sink", "role": "node"}},
        ]

    def test_self_loop_routes_and_binds(self):
        # The r4-11 class: "a PipelineRun is a rerun of another
        # PipelineRun" was undrawable — from == to raised ValueError out
        # of min() on an empty candidate list.
        box = {"id": "n1", "type": "rectangle", "x": 100, "y": 100,
               "width": 160, "height": 80}
        arrow = {"id": "a1", "type": "arrow"}
        canvas.route_arrow(arrow, box, box)
        self.assertEqual(len(arrow["points"]), 5)
        self.assertEqual(arrow["startBinding"]["elementId"], "n1")
        self.assertEqual(arrow["endBinding"]["elementId"], "n1")
        # CURVED again since v0.9 WP4 stage 3, and this is the path that
        # made stage 1 go sharp: r5-14 called the self-loop's erased
        # corner "a stray L-shaped stub". What changed is not the taste
        # but the instruments — the label anchor, the interior-run walk
        # and `render_svg` all read the flattened path now, so the erased
        # corner is measured where it is drawn. Five points, so the
        # per-arrow rule takes it.
        self.assertEqual(arrow["roundness"], {"type": 2})

    def test_self_loop_reroute_is_idempotent(self):
        # The F1/obstacle post-passes call route_arrow again; a loop must
        # come back identical, never degenerate.
        box = {"id": "n1", "type": "rectangle", "x": 100, "y": 100,
               "width": 160, "height": 80}
        other = {"id": "n2", "type": "rectangle", "x": 130, "y": 90,
                 "width": 80, "height": 40}
        arrow = {"id": "a1", "type": "arrow"}
        canvas.route_arrow(arrow, box, box)
        before = [list(p) for p in arrow["points"]]
        canvas.route_arrow(arrow, box, box, obstacles=[other])
        self.assertEqual(before, arrow["points"])

    def test_coincident_and_concentric_pairs_route_a_stub(self):
        # Arm 4's live trigger: a new node placed before layout separates
        # it shares its neighbor's spot. Both shapes must route, not raise.
        a = {"id": "n1", "type": "rectangle", "x": 100, "y": 100,
             "width": 160, "height": 80}
        b = {"id": "n2", "type": "rectangle", "x": 100, "y": 100,
             "width": 160, "height": 80}
        c = {"id": "n3", "type": "rectangle", "x": 140, "y": 120,
             "width": 80, "height": 40}
        for dst in (b, c):
            arrow = {"id": "a-%s" % dst["id"], "type": "arrow"}
            canvas.route_arrow(arrow, a, dst)
            self.assertGreaterEqual(len(arrow["points"]), 2)
            span = max(abs(p[0]) + abs(p[1]) for p in arrow["points"])
            self.assertGreater(span, 0.5)

    def test_offset_pair_still_routes_normally(self):
        # Control: the totality guard must not change ordinary routing.
        a = {"id": "n1", "type": "rectangle", "x": 0, "y": 0,
             "width": 100, "height": 50}
        b = {"id": "n2", "type": "rectangle", "x": 300, "y": 0,
             "width": 100, "height": 50}
        arrow = {"id": "a1", "type": "arrow"}
        canvas.route_arrow(arrow, a, b)
        self.assertEqual(arrow["startBinding"]["elementId"], "n1")
        self.assertEqual(arrow["endBinding"]["elementId"], "n2")
        self.assertEqual(len(arrow["points"]), 2)

    def test_self_loop_through_apply_batch(self):
        # End to end through the documented write path — the exact move
        # arm 3 could not make.
        errors = []
        els = canvas.apply_ops(self.flow_nodes(), [
            {"op": "add", "element": {"id": "e1", "type": "arrow"},
             "from": "s", "to": "t"},
            {"op": "add", "element": {"id": "e2", "type": "arrow"},
             "from": "t", "to": "k"},
            {"op": "add", "element": {"id": "loop", "type": "arrow",
                                      "label": "retry"},
             "from": "t", "to": "t"},
        ], errors)
        self.assertEqual(errors, [])
        loop = next(e for e in els if e["id"] == "loop")
        self.assertEqual(loop["startBinding"]["elementId"], "t")
        self.assertEqual(loop["endBinding"]["elementId"], "t")

    def test_self_loop_exempt_from_flow_and_label_lints(self):
        # A looped transform is not a black hole, not a source-to-sink
        # short circuit, and its label never draws "wider than its run".
        errors = []
        els = canvas.apply_ops(self.flow_nodes(), [
            {"op": "add", "element": {"id": "e1", "type": "arrow"},
             "from": "s", "to": "t"},
            {"op": "add", "element": {"id": "e2", "type": "arrow"},
             "from": "t", "to": "k"},
            {"op": "add", "element": {"id": "loop", "type": "arrow",
                                      "label": "retry with backoff"},
             "from": "t", "to": "t"},
        ], errors)
        self.assertEqual(errors, [])
        lint = canvas.lint_layout(els, artifact_type="flow")
        self.assertEqual(lint["errors"], [])
        self.assertFalse(any("wider than its arrow" in w and "loop" in w
                             for w in lint["warnings"]), lint["warnings"])
        # The firing half. The rule-8 census (2026-08-16) tagged this
        # NEEDS-DESIGN on the reading that the pole had to be a self-loop
        # carrying a too-wide label — but that scene is the one the
        # exemption deliberately swallows, so it could never fire. The
        # control the silence actually needs is the same check speaking
        # on a NON-loop: that separates "the exemption works" from "the
        # check is dead", which is the only thing in doubt. Measured
        # 2026-08-16: a 219px label on this fixture's 200px s→t run.
        wide = canvas.apply_ops(self.flow_nodes(), [
            {"op": "add", "element": {"id": "e3", "type": "arrow",
                                      "label": "retry with exponential "
                                               "backoff"},
             "from": "s", "to": "t"}], [])
        self.assertTrue(any("wider than its arrow" in w and "e3" in w
                            for w in canvas.lint_layout(
                                wide, artifact_type="flow")["warnings"]))

    def test_self_edge_excluded_from_reachability(self):
        els = []
        errors = []
        els = canvas.apply_ops(self.flow_nodes(), [
            {"op": "add", "element": {"id": "loop", "type": "arrow"},
             "from": "t", "to": "t"},
        ], errors)
        reach = canvas.flow_reachable(els)
        self.assertNotIn("t", reach.get("t", set()))

    def test_unbound_labeled_arrow_warns_bound_stays_quiet(self):
        # D8, both directions: the free labeled arrow draws exactly one
        # warning; the bound arrows draw none; a decoration line is
        # exempt entirely.
        errors = []
        els = canvas.apply_ops(self.flow_nodes(), [
            {"op": "add", "element": {"id": "e1", "type": "arrow"},
             "from": "s", "to": "t"},
        ], errors)
        els.append({"id": "free", "type": "arrow", "x": 0, "y": 200,
                    "width": 100, "height": 0,
                    "points": [[0, 0], [100, 0]]})
        els.append({"id": "free-lbl", "type": "text", "text": "informs",
                    "containerId": "free", "x": 20, "y": 190,
                    "width": 60, "height": 18, "fontSize": 14})
        els.append({"id": "deco", "type": "line", "x": 0, "y": 300,
                    "width": 80, "height": 0, "points": [[0, 0], [80, 0]],
                    "customData": {"role": "decoration"}})
        lint = canvas.lint_layout(els, artifact_type="flow")
        unbound = [w for w in lint["warnings"] if "binds nothing" in w]
        self.assertEqual(len(unbound), 1, lint["warnings"])
        self.assertTrue(unbound[0].startswith("arrow free"))
        self.assertFalse(any(w.startswith("arrow deco")
                             for w in unbound))

    def test_routing_failure_is_an_error_not_a_traceback(self):
        # E-9: monkeypatch route_arrow to raise; the batch must reject
        # whole with an ERROR naming the op — never a traceback.
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "f", "type": "flow",
                       "concept": "c", "name": "F"},
            "ops": [{"op": "add", "element": {
                "id": "n1", "type": "rectangle", "x": 0, "y": 0,
                "width": 100, "height": 50, "label": "A"}}]})
        orig = canvas.route_arrow

        def boom(*args, **kwargs):
            raise ValueError("boom")

        canvas.route_arrow = boom
        try:
            with self.assertRaises(canvas.BatchError) as ctx:
                self.store.apply_batch({
                    "base_revn": 1, "artifact": "f",
                    "ops": [{"op": "add", "element": {
                        "id": "n2", "type": "rectangle", "x": 300,
                        "y": 0, "width": 100, "height": 50,
                        "label": "B"}},
                        {"op": "add", "element": {"id": "a",
                                                  "type": "arrow"},
                         "from": "n1", "to": "n2"}]})
        finally:
            canvas.route_arrow = orig
        msg = str(ctx.exception)
        self.assertIn("op 1", msg)
        self.assertIn("internal routing error", msg)
        self.assertIn("ValueError", msg)

    def test_check_path_reports_the_same_error_envelope(self):
        # The offline --check path was the barest escape route: a raw
        # traceback to stdout. check_batch must return ok=False with the
        # enveloped message instead.
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "f", "type": "flow",
                       "concept": "c", "name": "F"},
            "ops": [{"op": "add", "element": {
                "id": "n1", "type": "rectangle", "x": 0, "y": 0,
                "width": 100, "height": 50, "label": "A"}}]})
        orig = canvas.route_arrow

        def boom(*args, **kwargs):
            raise ValueError("boom")

        canvas.route_arrow = boom
        try:
            result = self.store.check_batch({
                "base_revn": 1, "artifact": "f",
                "ops": [{"op": "add", "element": {"id": "a",
                                                  "type": "arrow",
                                                  "x": 0, "y": 0},
                         "from": "n1", "to": "n1"}]})
        finally:
            canvas.route_arrow = orig
        self.assertFalse(result["ok"])
        self.assertTrue(any("internal routing error" in e
                            for e in result["errors"]), result["errors"])


class TestReferentialIntegrity(Base):
    """WP2 (the r4 headline): nothing validated a reference after the
    thing it referred to was gone — bindings dangled on disk while the
    lint's error was unreachable, mapping members pointed at corpses,
    notes floated, and catch_up blamed a phantom outside editor."""

    def seed(self):
        """Two nodes, a bound arrow, a mapping, and an anchored note.

        Returns:
            The head revn after seeding.
        """
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "f", "type": "flow",
                       "concept": "c", "name": "F"},
            "ops": [
                {"op": "add", "element": {
                    "id": "n1", "type": "rectangle", "x": 0, "y": 0,
                    "width": 100, "height": 50, "label": "A",
                    "kind": "source", "role": "node"}},
                {"op": "add", "element": {
                    "id": "n2", "type": "rectangle", "x": 300, "y": 0,
                    "width": 100, "height": 50, "label": "B",
                    "kind": "sink", "role": "node"}},
                {"op": "add", "element": {"id": "t1", "type": "arrow"},
                 "from": "n1", "to": "n2"},
            ]})
        self.store.apply_batch({
            "base_revn": 1, "artifact": "f", "ops": [
                {"op": "registry", "action": "add_mapping", "concept": "c",
                 "elements": ["f#n2", "f#n1"]},
                {"op": "add", "element": {
                    "id": "note1", "type": "text", "text": "watch",
                    "x": 320, "y": 80, "role": "annotation",
                    "annotates": "n2"}},
            ]})
        return self.store.head_revn()

    def user_delete_n2(self):
        """Delete n2 the way the client does — binding left dangling.

        Returns:
            The commit record.
        """
        els = [json.loads(json.dumps(e)) for e in self.store.scenes["f"]
               if e["id"] != "n2" and e.get("containerId") != "n2"]
        return self.store.commit(author="user", new_scenes={"f": els},
                                 base_revn=self.store.head_revn())

    def repair_n2(self, store=None):
        """Put n2 back, the way a later commit repairs a broken reference.

        Args:
            store: Which store commits it — the r5-19 tests need the
                repair to land on a store that OPENED on the damage,
                which is the long-lived server this test class otherwise
                has no reason to build.

        Returns:
            The commit record.
        """
        store = store or self.store
        els = [json.loads(json.dumps(e)) for e in store.scenes["f"]]
        els.append({"id": "n2", "type": "rectangle", "x": 300, "y": 0,
                    "width": 100, "height": 50})
        return store.commit(author="user", new_scenes={"f": els},
                            base_revn=store.head_revn())

    def test_deletion_facts_name_every_broken_reference(self):
        self.seed()
        rec = self.user_delete_n2()
        facts = {f["fact"] for f in rec["artifacts"]["f"]["facts"]}
        self.assertIn("arrow_orphaned", facts)
        self.assertIn("mapping_dangling", facts)
        self.assertIn("note_orphaned", facts)

    def test_clean_deletion_emits_no_reference_facts(self):
        # Silent half: deleting an UNREFERENCED node breaks nothing and
        # must say nothing about references.
        self.seed()
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "f",
            "ops": [{"op": "add", "element": {
                "id": "n3", "type": "rectangle", "x": 600, "y": 200,
                "width": 100, "height": 50, "label": "C"}}]})
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "f",
            "ops": [{"op": "del", "id": "n3"}]})
        facts = {f["fact"] for f in rec["artifacts"]["f"]["facts"]}
        self.assertFalse(facts & {"arrow_orphaned", "mapping_dangling",
                                  "note_orphaned"}, facts)

    def test_fresh_load_reports_raw_findings_and_lint_debt_carries_them(self):
        # The r4-7 unreachability, both halves: the binding dangles ON
        # DISK after a user delete; a fresh Store must (a) report it from
        # the RAW scene even though validate_scene repairs it in memory,
        # (b) carry it into lint_debt, and (c) name the repair.
        self.seed()
        self.user_delete_n2()
        store2 = canvas.Store(self.project)
        ref = store2.referential
        self.assertTrue(any("t1" in m and "no longer exists" in m
                            for m in ref.get("f", {}).get("errors", [])),
                        ref)
        self.assertTrue(any("f#n2" in m for m in
                            ref.get("registry", {}).get("warnings", [])),
                        ref)
        debt = store2.lint_debt()
        self.assertGreaterEqual(debt.get("f", {}).get("errors", 0), 1)
        self.assertGreaterEqual(
            debt.get("registry", {}).get("warnings", 0), 1)
        self.assertTrue(any(i["code"] == "ART-005" and i.get("repaired")
                            for i in store2.issues), store2.issues)

    def test_intact_project_has_no_referential_findings(self):
        # Silent half of the load pass.
        self.seed()
        store2 = canvas.Store(self.project)
        self.assertEqual(store2.referential, {})

    def test_lint_debt_tracks_a_repair_with_no_restart(self):
        """r5-19: the standing referential nag follows the head revision.

        `self.referential` is assigned only in `load`, and `lint_debt`
        folded that frozen set into every recompute — so a long-lived
        server went on reporting a reference a later commit had already
        repaired. Measured in the r5 assessment: server said 2 errors,
        CLI said none, disk said none.

        Both directions on ONE store with no restart between them: a
        dangle opened after load reaches the debt, and the repair that
        closes it takes the finding with it. The REGISTRY mapping arm is
        what is asserted, because `referential_findings` is the only pass
        that reports it — `lint_layout` backstops the arrow arm, so the
        same assertion there would pass with this bug fully intact.

        That backstop reasoning is also how the arrow arm went unprobed
        while the recompute doubled it, so it no longer stands alone:
        `test_the_arrow_arm_is_reported_once_not_twice` below covers the
        arm this one deliberately skips.
        """
        self.seed()
        self.user_delete_n2()
        said = self.store.lint_lines().get("registry", {})
        self.assertTrue(any("f#n2" in m for m in said.get("warnings", [])),
                        "a mapping member deleted AFTER load never reached "
                        "the debt: %r" % (said,))
        self.repair_n2()
        said = self.store.lint_lines().get("registry", {})
        self.assertFalse(any("f#n2" in m for m in said.get("warnings", [])),
                         "the repair landed and the server still nags: %r"
                         % (said,))

    def test_the_arrow_arm_is_reported_once_not_twice(self):
        """Two passes, one byte-identical sentence, one broken arrow.

        `referential_findings` and `lint_layout` emit the SAME arrow-
        binding error, character for character. They never collided while
        the referential pass ran only on the RAW disk scene:
        `validate_scene` nulls a dangling binding at load (ART-005), so
        the repaired scene `lint_layout` reads was clean and exactly one
        of the two ever spoke. Recomputing the pass on the LIVE scenes
        put both on the same picture for a break opened AFTER load, where
        nothing has nulled anything, and one broken arrow counted as two
        — the r5-19 two-surfaces disagreement inverted, server 2 against
        a CLI reading of 1.

        Asserted the way it was measured, and on the COUNT rather than on
        "no duplicates", because a dedupe applied to the wrong list would
        satisfy the weaker claim by dropping a finding.

        Whole-debt equality is deliberately NOT asserted here. The two
        readings genuinely differ by one warning — the fresh load nulled
        the binding, so `lint_layout` calls that arrow half-bound where
        the running store still holds the dangling reference and errors
        on it. That difference predates this work and reproduces on the
        parent commit; the arm under test is the one that must agree.
        """
        self.seed()
        self.user_delete_n2()
        got = self.store.lint_lines().get("f", {}).get("errors", [])
        self.assertEqual(
            len([m for m in got if "binds n2" in m]), 1,
            "one broken arrow, reported twice — `referential_findings` "
            "and `lint_layout` both spoke: %r" % (got,))
        self.assertEqual(self.store.lint_debt().get("f", {}).get("errors"),
                         canvas.Store(self.project).lint_debt()
                         .get("f", {}).get("errors"),
                         "the running store and a fresh load of the same "
                         "disk disagree about how many errors there are")

    def test_the_running_store_and_a_fresh_load_agree_after_a_repair(self):
        """The symptom as it was measured: two readings, one moment.

        The r5 finding is a DISAGREEMENT — the server and the CLI, which
        opens the project fresh, read the same disk differently. Asserted
        as whole-debt equality rather than on one arm, since the point is
        that no arm may drift; the arms travel together out of one pass
        and a recompute that dropped `artifact_files` would mute the two
        file arms on the server side only.

        The repair lands on a store that OPENED on the damage, which is
        the only arrangement that reproduces it: the frozen set has to be
        non-empty before the commit for the commit to fail to clear it.
        That the server nags first is asserted, not assumed — without it
        the equality below is satisfied by two silent readings.
        """
        self.seed()
        self.user_delete_n2()
        server = canvas.Store(self.project)
        self.assertTrue(server.lint_debt().get("registry"),
                        "the damaged project must open with the pre-repair "
                        "report — nothing is being measured otherwise")
        self.repair_n2(server)
        self.assertEqual(server.lint_debt(),
                         canvas.Store(self.project).lint_debt())

    def test_the_debt_follows_the_branch_a_switch_lands_on(self):
        """The same disagreement, reached without a commit (v0.9 Task 42).

        Both freshness gates — `referential_now`'s merge and `lint_debt`'s
        cache — used to key on `head_revn()`, reading head equality as
        "the artifacts on disk are still the ones I read".
        `switch_branch` rewrites every artifact from `state_at(b["head"])`
        WITHOUT moving head, so a switch to a branch forked at this
        revision and back restores the committed scene under an unchanged
        revision number, and both gates went on describing the departed
        picture.

        The damage is applied to the FILE rather than committed, because
        a commit moves the revn and every branch's head with it — a
        sibling at the same head is the only arrangement where the number
        can stay still while the bytes change, and it is the ordinary one
        (two branches share a head until one of them commits).

        Asserted as whole-debt equality against a fresh load of the same
        disk, the r5-19 idiom, because it takes both halves of the fix:
        an invalidated cache still merges a stale referential report, and
        a dropped referential report still hides behind a stale cache.
        """
        head = self.seed()
        self.store.registry["branches"].append(
            {"name": "side", "head": head, "archived": False,
             "forked_from": "main", "forked_at_revn": head})
        self.store._save_registry()
        path = self.project.artifacts_dir / "f.excalidraw"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["elements"] = [e for e in doc["elements"]
                           if e["id"] != "n2" and e.get("containerId") != "n2"]
        path.write_text(json.dumps(doc), encoding="utf-8")
        server = canvas.Store(self.project)
        self.assertTrue(server.lint_debt().get("registry"),
                        "the damaged project must open with the pre-repair "
                        "report — nothing is being measured otherwise")
        server.switch_branch("side")
        server.switch_branch("main")
        self.assertEqual(server.lint_debt(),
                         canvas.Store(self.project).lint_debt())

    def two_damaged_artifacts(self):
        """Two artifacts, each with a raw-only dangling binding, one pinned.

        The damage is written to the FILES rather than committed, because
        the point is a finding that exists ONLY in the bytes on disk:
        `validate_scene` nulls the binding in memory, so the live pass is
        silent about it and the load-time report is the only thing that
        has it.

        Returns:
            A store opened on the damage, with `pin-b` open on artifact b.
        """
        for aid in ("a", "b"):
            self.store.apply_batch({
                "base_revn": self.store.head_revn(), "artifact": aid,
                "create": {"id": aid, "name": aid.upper(), "type": "flow",
                           "concept": aid, "concept_name": aid.upper()},
                "ops": [{"op": "add", "element": {
                    "type": "rectangle", "id": aid + n, "label": n.upper(),
                    "x": 300 * i, "y": 0, "width": 100, "height": 60,
                    "role": "node"}} for i, n in enumerate(("n1", "n2"))]
                + [{"op": "add", "element": {
                    "type": "arrow", "id": aid + "t1",
                    "from": aid + "n1", "to": aid + "n2"}}]})
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "b",
            "ops": [{"op": "pin", "target": "bn1", "id": "pin-b",
                     "question": "why?"}]})
        for aid in ("a", "b"):
            path = self.project.artifacts_dir / (aid + ".excalidraw")
            doc = json.loads(path.read_text(encoding="utf-8"))
            for e in doc["elements"]:
                if e["id"] == aid + "t1":
                    e["endBinding"] = {"elementId": "GHOST", "focus": 0,
                                       "gap": 1}
            path.write_text(json.dumps(doc), encoding="utf-8")
        return canvas.Store(self.project)

    def test_one_artifacts_write_does_not_spend_anothers_finding(self):
        """A standing finding outlives a write to the artifact next door.

        The freshness rule is per-scope for a reason. A store-wide "some
        file was written" reads `answer_pin` on artifact b — which rewrites
        b's file and nothing else — as grounds to drop the pre-repair
        report for a, whose bytes on disk still carry the dangling binding
        and whose finding a fresh load still reports. That is r5-19
        inverted: not a nag that outlived its subject, but an ERROR that
        vanished while it was still true.

        Both directions in one arrangement, because a rule that keeps too
        much and a rule that keeps too little both satisfy half of it:
        a's finding SURVIVES b's write, and b's own finding GOES with it —
        `answer_pin` rewrote b's file from the repaired memory scene, so
        the damage the report described is genuinely off the disk.

        The whole-debt equality is the binding claim and the r5-19 idiom:
        the running store and a fresh load of the same disk must read it
        the same way. Both readings are asserted to be non-trivial first,
        since two matching silences would satisfy the equality alone.
        """
        server = self.two_damaged_artifacts()
        self.assertEqual(
            sorted(server.referential), ["a", "b"],
            "both artifacts must open with a pre-repair finding — "
            "nothing is being measured otherwise")
        server.answer_pin("pin-b", "because")
        now = server.referential_now()
        self.assertIn("GHOST", json.dumps(now.get("a", {})),
                      "a was never written and its binding still dangles "
                      "on disk, but answering a pin on b dropped its "
                      "standing finding")
        self.assertNotIn("GHOST", json.dumps(now.get("b", {})),
                         "answering the pin rewrote b's file from the "
                         "repaired scene, so b's pre-repair finding is "
                         "spent and must not still be merged in")
        self.assertEqual(server.lint_debt(),
                         canvas.Store(self.project).lint_debt())

    def test_referential_findings_unit_directions(self):
        raw = {"a": [
            {"id": "n1", "type": "rectangle"},
            {"id": "ok", "type": "arrow",
             "startBinding": {"elementId": "n1"}, "endBinding": None},
            {"id": "bad", "type": "arrow",
             "startBinding": {"elementId": "ghost"},
             "endBinding": {"elementId": "n1"}},
            {"id": "note", "type": "text",
             "customData": {"annotates": "ghost"}},
            {"id": "lnk", "type": "rectangle",
             "link": "artifact:nowhere"},
            "corrupt-non-dict",
        ]}
        reg = {"mappings": [
            {"concept": "c", "elements": ["a#n1", "a#ghost"]},
            {"concept": "c2", "elements": ["a#n1"]}]}
        out = canvas.referential_findings(raw, reg, {"a"})
        self.assertEqual(len(out["a"]["errors"]), 1)
        self.assertIn("bad", out["a"]["errors"][0])
        self.assertEqual(len(out["a"]["notes"]), 2)  # note + links_to
        self.assertEqual(len(out["registry"]["warnings"]), 1)
        self.assertIn("a#ghost", out["registry"]["warnings"][0])

    def test_repair_only_reconciliation_names_its_repairs_and_converges(self):
        """A load-time repair reconciles as a repair, and then stops.

        The standing home of the repair-only attribution path since v0.9
        WP4. It used to ride the recorded fixtures, but all seven of
        their load repairs turned out to be the r5-13 note-padding false
        positive, and a project that loads clean cannot exercise an
        attribution path for repairs — so the scene is built here, and
        built BROKEN on purpose: a 400px label in a 120px box, which the
        fitter really does wrap and really does grow the box for.

        `commit` writes the artifact and the save from the same
        elements, so disk and replayed history agree BEFORE the load —
        which is the whole premise of `repair_only`. The divergence the
        second store sees is the loader's own work and nothing else.

        The last assertion is the one r5-13 was about: the repair has to
        PERSIST. A repair that reconciles and then fires again on the
        next load mints a fresh revision on every resume, which is how a
        note-bearing project accumulated a reconciliation per resume —
        and, since Task 42, spends referential standing doing it.
        """
        self.seed()
        text = "Escalate to the compliance review board immediately"
        els = [json.loads(json.dumps(e)) for e in self.store.scenes["f"]]
        els += [{"id": "wide", "type": "rectangle", "x": 0, "y": 200,
                 "width": 120, "height": 60, "customData": {"role": "node"},
                 "boundElements": [{"id": "wide-t", "type": "text"}]},
                {"id": "wide-t", "type": "text", "x": 4, "y": 220,
                 "width": 400, "height": 20, "text": text,
                 "originalText": text, "fontSize": 16,
                 "containerId": "wide", "textAlign": "center"}]
        self.store.commit(author="user", new_scenes={"f": els},
                          base_revn=self.store.head_revn())
        store2 = canvas.Store(self.project)
        self.assertEqual([i["code"] for i in store2.scene_repairs],
                         ["ART-011", "ART-012"])
        rec = store2.catch_up()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["author"], "out-of-session")
        self.assertTrue(rec.get("reconciliation"))
        self.assertEqual(len(rec.get("repairs") or []), 2)
        self.assertIn("load-time repair: ART-011 ×1, ART-012 ×1",
                      rec["summary"]["headline"])
        self.assertIn("no outside edits", rec["summary"]["headline"])
        store3 = canvas.Store(self.project)
        self.assertEqual(store3.scene_repairs, [],
                         "the repair did not persist — it recurs on every "
                         "load, and each one mints a reconciliation")
        self.assertIsNone(store3.catch_up())

    def test_genuine_outside_edit_still_reconciles_as_one(self):
        # Control: a real out-of-session edit keeps the classic
        # reconciliation (author, honest content headline) and does NOT
        # take the repair-only path.
        self.seed()
        p = self.tmp / "project_knowledge" / "artifacts" / "f.excalidraw"
        doc = json.loads(p.read_text(encoding="utf-8"))
        for e in doc["elements"]:
            if e["id"] == "n1":
                e["x"] = 555
        p.write_text(json.dumps(doc), encoding="utf-8")
        store2 = canvas.Store(self.project)
        rec = store2.catch_up()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["author"], "out-of-session")
        self.assertNotIn("load-time repair", rec["summary"]["headline"])
        self.assertNotIn("saved without changing anything",
                         rec["summary"]["headline"])
        store3 = canvas.Store(self.project)
        self.assertIsNone(store3.catch_up())


class TestComposedReconciliation(Base):
    """WP3 (r4-8/r4-9/r4-10): composed parts are DERIVED — geometry from
    the host, presence from the state — on every path, and a user's
    part edit is read as the state gesture it is."""

    def seed_controls(self):
        """Seed a wireframe with a checked checkbox and a 70% slider."""
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "w", "type": "wireframe",
                       "concept": "c", "name": "W"},
            "ops": [
                {"op": "add", "element": {
                    "id": "cb", "type": "rectangle", "x": 100, "y": 100,
                    "width": 200, "height": 28, "label": "Macro calendar",
                    "kind": "checkbox", "checked": True}},
                {"op": "add", "element": {
                    "id": "sl", "type": "rectangle", "x": 100, "y": 200,
                    "width": 160, "height": 44, "label": "Confidence",
                    "kind": "slider", "value": 70}},
            ]})

    def user_save(self, els):
        """Commit a user save of the given elements.

        Args:
            els: The full element list to save.

        Returns:
            The commit record.
        """
        return self.store.commit(author="user", new_scenes={"w": els},
                                 base_revn=self.store.head_revn())

    def scene_copy(self, drop=None):
        """Deep-copy the live scene, optionally dropping one element id.

        Args:
            drop: Element id to omit, or None.

        Returns:
            The copied element list.
        """
        return [json.loads(json.dumps(e)) for e in self.store.scenes["w"]
                if e["id"] != drop]

    def test_stroke_delete_is_an_uncheck_everywhere(self):
        # The r4-8 beat: render, record, memory and DISK must all agree.
        self.seed_controls()
        rec = self.user_save(self.scene_copy(drop="cb-chk"))
        facts = {f["fact"] for f in rec["artifacts"]["w"]["facts"]}
        self.assertIn("state_toggled", facts)
        self.assertIn("switched off", rec["summary"]["headline"])
        cb = next(e for e in self.store.scenes["w"] if e["id"] == "cb")
        self.assertFalse(cb["customData"]["checked"])
        self.assertFalse(any(e["id"] == "cb-chk"
                             for e in self.store.scenes["w"]))
        disk = json.loads(
            (self.tmp / "project_knowledge" / "artifacts" /
             "w.excalidraw").read_text(encoding="utf-8"))
        dcb = next(e for e in disk["elements"] if e["id"] == "cb")
        self.assertFalse(dcb["customData"]["checked"])

    def test_stroke_restore_flips_back_with_a_fact(self):
        self.seed_controls()
        self.user_save(self.scene_copy(drop="cb-chk"))
        els = self.scene_copy()
        els.append({"id": "cb-chk", "type": "line", "x": 111, "y": 112,
                    "width": 10, "height": 8,
                    "points": [[0, 0], [4, 4], [10, -6]],
                    "customData": {"chk_of": "cb", "role": "decoration"},
                    "groupIds": ["cb-grp"]})
        rec = self.user_save(els)
        facts = {f["fact"] for f in rec["artifacts"]["w"]["facts"]}
        self.assertIn("state_toggled", facts)
        cb = next(e for e in self.store.scenes["w"] if e["id"] == "cb")
        self.assertTrue(cb["customData"]["checked"])

    def test_slider_thumb_drag_becomes_value_changed(self):
        self.seed_controls()
        els = self.scene_copy()
        th = next(e for e in els if e["id"] == "sl-thumb")
        th["x"] = 100 + 10 + (160 - 20 - 12) * 0.25
        rec = self.user_save(els)
        facts = {f["fact"] for f in rec["artifacts"]["w"]["facts"]}
        self.assertIn("value_changed", facts)
        sl = next(e for e in self.store.scenes["w"] if e["id"] == "sl")
        self.assertAlmostEqual(sl["customData"]["value"], 25.0, delta=1.0)

    def test_pasted_control_composes_without_phantom_gesture(self):
        # Delta-based, not state-based: a NEW host arriving without parts
        # is a paste, not an uncheck.
        self.seed_controls()
        els = self.scene_copy()
        els.append({"id": "cb2", "type": "rectangle", "x": 100, "y": 300,
                    "width": 200, "height": 28,
                    "customData": {"kind": "checkbox", "checked": True,
                                   "role": "node"},
                    "groupIds": ["cb2-grp"]})
        rec = self.user_save(els)
        facts = {f["fact"] for f in rec["artifacts"]["w"]["facts"]}
        self.assertNotIn("state_toggled", facts)
        self.assertTrue(any(e["id"] == "cb2-chk"
                            for e in self.store.scenes["w"]))

    def test_untouched_save_still_says_nothing_changed(self):
        # Silent half: interpretation + reconciliation must be idempotent.
        self.seed_controls()
        rec = self.user_save(self.scene_copy())
        self.assertEqual(rec["summary"]["headline"],
                         "saved without changing anything")

    def test_resize_rederives_every_part_kind(self):
        # The stale-on-resize hole was xbox-only until v0.8.
        self.seed_controls()
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "w",
            "ops": [{"op": "mod", "id": "sl", "attrs": {"width": 320}}]})
        sl = next(e for e in self.store.scenes["w"] if e["id"] == "sl")
        tr = next(e for e in self.store.scenes["w"]
                  if e["id"] == "sl-track")
        th = next(e for e in self.store.scenes["w"]
                  if e["id"] == "sl-thumb")
        self.assertEqual(tr["width"], 300)
        self.assertAlmostEqual(
            th["x"], canvas._slider_thumb_x(sl, sl["customData"]["value"]),
            delta=1.0)
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "w",
            "ops": [{"op": "mod", "id": "cb",
                     "attrs": {"height": 60}}]})
        cb = next(e for e in self.store.scenes["w"] if e["id"] == "cb")
        box = next(e for e in self.store.scenes["w"]
                   if e["id"] == "cb-box")
        self.assertAlmostEqual(box["y"],
                               cb["y"] + cb["height"] / 2.0 - 8, delta=0.5)

    def test_links_change_narrates_instead_of_no_changes(self):
        # r4-9's second face: wiring click-throughs used to narrate as
        # "saved without changing anything" while the links landed.
        self.seed_controls()
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "w",
            "ops": [{"op": "mod", "id": "cb",
                     "attrs": {"links_to": "w"}}]})
        facts = {f["fact"] for f in rec["artifacts"]["w"]["facts"]}
        self.assertIn("links_changed", facts)
        self.assertIn("now links to", rec["summary"]["headline"])

    def test_suppressed_only_save_says_housekeeping_not_no_changes(self):
        # r4-9's headline face: every fact suppressed is housekeeping,
        # never "no changes".
        self.seed_controls()
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "w",
            "ops": [{"op": "add", "element": {
                "id": "deco1", "type": "line", "x": 0, "y": 400,
                "width": 80, "height": 0, "points": [[0, 0], [80, 0]],
                "role": "decoration"}}]})
        els = self.scene_copy(drop="deco1")
        rec = self.user_save(els)
        self.assertIn("housekeeping only", rec["summary"]["headline"])
        self.assertNotEqual(rec["summary"]["headline"], "no changes")


class TestPictureIsTheTruth(Base):
    """WP4 (r4-12, r4-1, D15, B6, E-4, D24): the measurements that decide
    legibility must agree with the renderer the user actually sees."""

    def test_kpi_value_width_no_longer_wraps(self):
        # The '62' tile: stored width must be >= the real Nunito need
        # (28.8px at fontSize 24) — the old int(0.6-em) box was 28 and
        # the live editor wrapped it into stacked digits.
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {
                "id": "kpi", "type": "rectangle", "x": 0, "y": 0,
                "width": 292, "height": 120, "label": "Sentiment index",
                "kind": "kpi", "value": "62"}}], errors)
        self.assertEqual(errors, [])
        val = next(e for e in els
                   if (e.get("customData") or {}).get("value_of") == "kpi")
        need, _ = canvas.text_dims("62", val.get("fontSize", 24))
        self.assertGreaterEqual(val["width"], 29)
        self.assertGreaterEqual(val["width"], need)

    def test_crossing_arrow_fires_and_fan_attach_stays_legal(self):
        # r4-1 both directions: an endpoint that crossed the whole box
        # and stopped near the far edge fires the crosses-through check;
        # a fanned endpoint sitting exactly ON an edge stays silent.
        box = {"id": "b", "type": "rectangle", "x": 360, "y": 60,
               "width": 200, "height": 60,
               "customData": {"role": "node", "kind": "transform"}}
        crossing = {"id": "a1", "type": "arrow", "x": 300, "y": 90,
                    "width": 248, "height": 0,
                    "points": [[0, 0], [248, 0]],
                    "endBinding": {"elementId": "b", "focus": 0,
                                   "gap": 6}}
        fan = {"id": "a2", "type": "arrow", "x": 300, "y": 20,
               "width": 150, "height": 40,
               "points": [[0, 0], [150, 40]],
               "endBinding": {"elementId": "b", "focus": 0, "gap": 6}}
        lint = canvas.lint_layout([box, crossing, fan],
                                  artifact_type="flow")
        crossing_msgs = [m for tier in ("errors", "warnings")
                        for m in lint[tier] if "a1" in m]
        self.assertTrue(any("crossing through" in m or "runs" in m
                            for m in crossing_msgs), lint)
        fan_msgs = [m for tier in ("errors", "warnings")
                    for m in lint[tier] if "a2" in m and
                    ("inside" in m or "crossing" in m)]
        self.assertEqual(fan_msgs, [], lint)

    def test_body_kind_composes_wavy_lines_and_rederives(self):
        # E-4 / v0.2 gap #12: the wavy stand-in exists now, and its
        # lines follow a resize like every other composed part.
        errors = []
        els = canvas.apply_ops([], [
            {"op": "add", "element": {
                "id": "body", "type": "rectangle", "x": 0, "y": 0,
                "width": 200, "height": 64, "kind": "body"}}], errors)
        self.assertEqual(errors, [])
        lines = [e for e in els
                 if (e.get("customData") or {}).get("body_of") == "body"]
        self.assertGreaterEqual(len(lines), 2)
        self.assertTrue(all(len(ln["points"]) > 5 for ln in lines))
        els2 = canvas.apply_ops(els, [
            {"op": "mod", "id": "body", "attrs": {"width": 400}}], errors)
        self.assertEqual(errors, [])
        lines2 = [e for e in els2
                  if (e.get("customData") or {}).get("body_of") == "body"]
        self.assertTrue(all(ln["width"] > 300 for ln in lines2[:-1]))

    def test_glossary_parser_rejects_sentence_fragments(self):
        # D24: the live false alarm — a bolded clause inside an entry
        # body minted as a term after the agent's last lint pass.
        text = ("**Run**: one 06:00 execution.\n"
                "**Switched off by default since Aug 2026**: not a term.\n"
                "**Macro Calendar** — the fifth source.\n")
        terms = canvas.parse_glossary_terms(text)
        self.assertIn("Run", terms)
        self.assertIn("Macro Calendar", terms)
        self.assertNotIn("Switched off by default since Aug 2026", terms)

    def test_pin_spot_dodges_dense_neighbours_hugs_on_flows(self):
        # D15: constant offset was layout-density-blind.
        target = {"id": "t", "type": "rectangle", "x": 100, "y": 100,
                  "width": 120, "height": 80}
        neighbour = {"id": "n", "type": "rectangle", "x": 232, "y": 60,
                     "width": 120, "height": 80}
        px, py = canvas.pin_spot(target, [target, neighbour])
        self.assertLessEqual(px + 26, 232)  # inside the target's column
        far = {"id": "n2", "type": "rectangle", "x": 600, "y": 100,
               "width": 120, "height": 80}
        hug = canvas.pin_spot(target, [target, far])
        self.assertEqual(hug, canvas.marker_anchor(target, dx=8, dy=-8,
                                                   corner="tr"))

    def test_toggle_travel_is_readable(self):
        # B6 residue: 12px of travel was "merely subtle at export scale".
        el = {"id": "tg", "x": 0, "y": 0, "width": 200, "height": 28}
        travel = canvas._toggle_thumb_x(el, True) - \
            canvas._toggle_thumb_x(el, False)
        self.assertGreaterEqual(travel, 16)


class TestEventLoopAndRounds(Base):
    """WP5 (r4-3, r4-6, D9, D10): the loop's substrate — event taxonomy,
    the chat-only round stall, and the nags that arm the mechanisms."""

    def test_event_taxonomy_covers_every_emitted_type(self):
        # Self-checking against the source: every events.append("type")
        # in canvas.py must be classified user/agent/system — an
        # unclassified type is the r4-6 gap reopening (agent_revision
        # was 87% of live traffic and documented nowhere).
        src = (Path(canvas.__file__)).read_text(encoding="utf-8")
        emitted = set(re.findall(
            r'(?:events|self\.events)\.append\(\s*"([a-z_]+)"', src))
        classified = set(canvas.USER_EVENT_TYPES) | \
            set(canvas.AGENT_EVENT_TYPES) | \
            {"server_started", "reconciliation"}
        self.assertTrue(emitted, "taxonomy scan found no emissions")
        unclassified = emitted - classified
        self.assertFalse(unclassified,
                         "event types with no owner: %r" % unclassified)

    @staticmethod
    def _emitted_event_types():
        """Every event type `canvas.py` can emit, parsed rather than grepped.

        AST rather than a regex BECAUSE OF THE MISS THIS GUARD ANSWERS.
        Task 26's hand census grepped line-anchored and read 14 where the
        source has 15 — `agent_revision_failed`'s literal sits on the
        line AFTER its open paren, so a per-line reader walks straight
        past the one type that commit existed to document. A parser has
        no line concept and cannot repeat that, which is the property
        being bought; the sibling guard above still uses its whitespace-
        skipping regex and agrees today, and if the two ever part the
        parser is right.

        Returns:
            The set of string literals passed first to any `.events.append`
            or bare `events.append` call. Non-literal first arguments (the
            replay path re-appends whole dicts) are not types and are
            skipped.
        """
        src = Path(canvas.__file__).read_text(encoding="utf-8")
        out = set()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute) or fn.attr != "append":
                continue
            tgt = fn.value
            owner = (tgt.id if isinstance(tgt, ast.Name)
                     else tgt.attr if isinstance(tgt, ast.Attribute)
                     else None)
            if owner != "events" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value,
                                                              str):
                out.add(first.value)
        return out

    def test_the_skill_taxonomy_table_is_the_emitted_set(self):
        """SKILL.md's taxonomy is DERIVED-AND-COMPARED, not transcribed.

        THE GUARD THIS REPO DID NOT HAVE, and the gap was found the
        expensive way: task 26 shipped a census of these types that was
        wrong by one, and the type it missed was the type the commit
        existed to add. The old guard one method up asks only
        `emitted - classified`, which is one direction of one of the
        three comparisons that matter — it is green over a constant
        naming a type nothing emits, and green over ANY drift in the
        skill file, which is the copy an agent actually reads.

        All three claims are checked here, both ways each:

        1. the emitted set equals the classified constants,
        2. each of the table's three cells equals its constant, so a type
           that changes OWNER is caught and not just one that vanishes —
           the whole point of the table is who reacts to what,
        3. the spelled numeral in the sentence above the table equals the
           cardinality, which is the literal that went stale.
        """
        emitted = self._emitted_event_types()
        system = {"server_started", "reconciliation"}
        self.assertEqual(
            emitted,
            set(canvas.USER_EVENT_TYPES) | set(canvas.AGENT_EVENT_TYPES)
            | system,
            "the emitted types and the classified constants have parted")

        # NOT `.resolve()`, deliberately: the census probe runs this in a
        # scratch tree whose `scripts/` is a symlink back here, so
        # resolving would walk out of the copy and read the real file —
        # a guard that cannot be perturbed is a guard nobody can trust.
        skill = (Path(canvas.__file__).parent.parent
                 / "SKILL.md").read_text(encoding="utf-8")
        rows = [ln for ln in skill.splitlines()
                if ln.startswith("|") and "`agent_revision_discarded`" in ln]
        self.assertEqual(len(rows), 1,
                         "the taxonomy row is not findable; re-anchor this "
                         "guard rather than deleting it")
        cells = [c.strip() for c in rows[0].strip("|").split("|")]
        self.assertEqual(len(cells), 3, "the taxonomy table lost a column")
        theirs, yours, sysc = (set(re.findall(r"`([a-z_]+)`", c))
                               for c in cells)
        self.assertEqual(theirs, set(canvas.USER_EVENT_TYPES),
                         "the skill's THEIRS cell and USER_EVENT_TYPES "
                         "disagree — an agent narrating this table would "
                         "take the wrong round")
        self.assertEqual(yours, set(canvas.AGENT_EVENT_TYPES),
                         "the skill's YOURS cell and AGENT_EVENT_TYPES "
                         "disagree")
        self.assertEqual(sysc, system, "the skill's SYSTEM cell drifted")

        words = ("zero", "one", "two", "three", "four", "five", "six",
                 "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "thirteen", "fourteen", "fifteen", "sixteen",
                 "seventeen", "eighteen", "nineteen", "twenty")
        said = re.search(r"\*\*Event taxonomy\*\* — all (\w+) types", skill)
        self.assertIsNotNone(said, "the taxonomy sentence has been reworded "
                                   "away from its own numeral")
        self.assertLess(len(emitted), len(words),
                        "more event types than this spelling table covers")
        self.assertEqual(said.group(1), words[len(emitted)],
                         "SKILL.md says %r types and canvas.py emits %d"
                         % (said.group(1), len(emitted)))

    def seed_round_stall(self):
        """One artifact, one open pin, then N agent-only commits."""
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "f", "type": "flow",
                       "concept": "c", "name": "F"},
            "ops": [
                {"op": "add", "element": {
                    "id": "n1", "type": "rectangle", "x": 0, "y": 0,
                    "width": 100, "height": 50, "label": "A"}},
                {"op": "pin", "target": "n1",
                 "question": "does A really start the run?"}]})
        for k in range(3):
            self.store.apply_batch({
                "base_revn": self.store.head_revn(), "artifact": "f",
                "ops": [{"op": "mod", "id": "n1",
                         "attrs": {"x": 10 * (k + 1)}}]})

    def test_round_stall_fires_after_agent_only_run(self):
        self.seed_round_stall()
        rs = self.store.round_stall()
        self.assertIsNotNone(rs)
        self.assertGreaterEqual(rs["commits"], 4)

    def test_round_stall_resets_on_user_save_and_needs_open_pins(self):
        self.seed_round_stall()
        els = [json.loads(json.dumps(e)) for e in self.store.scenes["f"]]
        els.append({"id": "note-u", "type": "text", "text": "hm",
                    "x": 300, "y": 300, "width": 40, "height": 18,
                    "customData": {"role": "annotation",
                                   "author": "user"}})
        self.store.commit(author="user", new_scenes={"f": els},
                          base_revn=self.store.head_revn())
        self.assertIsNone(self.store.round_stall())

    def test_set_round_validates_instead_of_silently_ignoring(self):
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "f", "type": "flow",
                       "concept": "c", "name": "F"},
            "ops": [{"op": "add", "element": {
                "id": "n1", "type": "rectangle", "x": 0, "y": 0,
                "width": 100, "height": 50, "label": "A"}}]})
        with self.assertRaises(canvas.BatchError) as ctx:
            self.store.apply_batch({
                "base_revn": self.store.head_revn(), "artifact": "f",
                "ops": [{"op": "registry", "action": "set_round",
                         "round": "four"}]})
        self.assertIn("must be an integer", str(ctx.exception))
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "f",
            "ops": [{"op": "registry", "action": "set_round",
                     "round": 7}]})
        self.assertEqual(self.store.registry["round"], 7)

    def test_unmapped_kpi_nags_and_mapped_stays_quiet(self):
        # D9 both directions: tripwire coverage is exactly as good as
        # the mapping discipline, so an unarmed KPI must nag while a
        # flow exists — and go quiet once mapped.
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "flow1", "type": "flow",
                       "concept": "c", "name": "Flow"},
            "ops": [{"op": "add", "element": {
                "id": "risk", "type": "rectangle", "x": 0, "y": 0,
                "width": 100, "height": 50, "label": "Risk model"}}]})
        self.store.apply_batch({
            "base_revn": self.store.head_revn(),
            "create": {"id": "dash", "type": "wireframe",
                       "concept": "c2", "name": "Dash"},
            "ops": [{"op": "add", "element": {
                "id": "kpi-var", "type": "rectangle", "x": 0, "y": 0,
                "width": 200, "height": 90, "label": "VaR",
                "kind": "kpi", "value": "2.4%"}}]})
        types = {a: self.store.artifact_type(a) for a in self.store.scenes}
        cross = canvas.cross_lint(self.store.scenes, types,
                                  self.store.registry, [])
        notes = (cross.get("dash") or {}).get("notes") or []
        self.assertTrue(any("KPI tile(s) unmapped" in n for n in notes),
                        cross)
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dash",
            "ops": [{"op": "registry", "action": "add_mapping",
                     "concept": "c",
                     "elements": ["dash#kpi-var", "flow1#risk"]}]})
        cross2 = canvas.cross_lint(self.store.scenes, types,
                                   self.store.registry, [])
        notes2 = (cross2.get("dash") or {}).get("notes") or []
        self.assertFalse(any("KPI tile(s) unmapped" in n for n in notes2),
                         cross2)

    def test_muted_rename_is_named_while_the_ruling_holds(self):
        # D10: a rename swallowed by a value-scoped divergence ruling is
        # armed silence — it must surface as tripwires_muted, with no
        # tripwire fired and the ruling intact.
        self.test_unmapped_kpi_nags_and_mapped_stays_quiet()
        self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dash",
            "ops": [{"op": "registry", "action": "annotate_mapping",
                     "index": 0,
                     "note": "intentionally-divergent: tile wording "
                             "belongs to the screen",
                     "kinds": ["label_renamed", "renamed",
                               "value_changed"]}]})
        rec, _ = self.store.apply_batch({
            "base_revn": self.store.head_revn(), "artifact": "dash",
            "ops": [{"op": "mod", "id": "kpi-var",
                     "attrs": {"label": "Excess Return"}}]})
        self.assertEqual(rec["tripwires"], [])
        muted = rec.get("tripwires_muted") or []
        self.assertTrue(any("scopes naming out" in m for m in muted),
                        rec)


class TestXAsUserFidelity(unittest.TestCase):
    """WP6 (D28 + the r4-10 driver half): the assessor's user-gesture
    driver must write what the real client writes — a drifted driver
    manufactures findings with the authority of real ones. Server-spun:
    these verbs ARE HTTP round-trips."""

    CANVAS = str(Path(__file__).resolve().parent.parent /
                 "skills" / "wysiwyg-grilling" / "scripts" / "canvas.py")

    @classmethod
    def setUpClass(cls):
        """Start one server on a seeded temp project."""
        cls.tmp = Path(tempfile.mkdtemp(prefix="wysiwyg-xuser-"))
        (cls.tmp / "project_knowledge").mkdir(parents=True)
        cls.project = canvas.Project(cls.tmp)
        cls.project.ensure_tree()
        store = canvas.Store(cls.project)
        store.apply_batch({
            "base_revn": 0,
            "create": {"id": "w", "type": "wireframe",
                       "concept": "c", "name": "W"},
            "ops": [
                {"op": "add", "element": {
                    "id": "panel", "type": "rectangle", "x": 100,
                    "y": 100, "width": 200, "height": 80,
                    "label": "Data sources"}},
                {"op": "add", "element": {
                    "id": "img", "type": "rectangle", "x": 100, "y": 300,
                    "width": 120, "height": 90, "kind": "image",
                    "label": "chart"}},
                {"op": "add", "element": {
                    "id": "cb", "type": "rectangle", "x": 400, "y": 100,
                    "width": 200, "height": 28, "label": "Macro",
                    "kind": "checkbox", "checked": True}},
            ]})
        out = subprocess.run(
            [sys.executable, cls.CANVAS, "--project", str(cls.tmp),
             "start", "--no-browser"],
            capture_output=True, text=True, timeout=60)
        if "URL=" not in out.stdout:
            raise unittest.SkipTest("server did not start: %s"
                                    % out.stderr[-200:])

    @classmethod
    def tearDownClass(cls):
        """Stop the server and remove the project."""
        subprocess.run(
            [sys.executable, cls.CANVAS, "--project", str(cls.tmp),
             "stop"], capture_output=True, text=True, timeout=30)
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for p in (cls.project.state_path, cls.project.events_path,
                  cls.project.log_path):
            if p.exists():
                p.unlink()

    def x(self, *argv):
        """Run an x-as-user verb against the live server.

        Args:
            argv: CLI arguments after `x-as-user`.

        Returns:
            The completed process (checked for rc 0).
        """
        out = subprocess.run(
            [sys.executable, self.CANVAS, "--project", str(self.tmp),
             "x-as-user", *argv],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        return out

    def scene(self):
        """Read the artifact fresh from disk.

        Returns:
            The element list of artifact `w`.
        """
        doc = json.loads(
            (self.tmp / "project_knowledge" / "artifacts" /
             "w.excalidraw").read_text(encoding="utf-8"))
        return doc["elements"]

    def url(self):
        """The running server's base URL.

        Returns:
            The `http://…/` prefix `x-as-user` posts against.
        """
        return self.project.read_state()["url"]

    def x_fails(self, *argv):
        """Run an x-as-user verb expected to be refused.

        Args:
            argv: CLI arguments after `x-as-user`.

        Returns:
            The completed process, asserted to have failed.
        """
        out = subprocess.run(
            [sys.executable, self.CANVAS, "--project", str(self.tmp),
             "x-as-user", *argv],
            capture_output=True, text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0,
                            "expected a refusal, got rc 0: %s" % out.stdout)
        return out

    def test_checkout_says_what_it_wants_instead_of_crashing(self):
        """r5-12: a missing `--target` is refused, not raised.

        v0.8's promise is that every failure prints an `ERROR=` line, and
        three sibling guards in this one function keep it FOR THEIR
        ARGUMENTS — `config`'s `key=value`, the shared `--artifact`
        check, and `note`'s `--text`. That is the claim, and it is
        narrower than "this function never leaks a traceback". What is
        covered beyond arguments: the state faults (no such element, not
        a checkbox), the artifact READ, which catches `OSError`,
        `ValueError` and `URLError`, the save POST, which catches
        `HTTPError`, and — since TASK-POLISH — the three early-returning
        verbs `answer`, `config` and `checkout`, which posted with no
        `try` at all and now go through `_x_post` (`HTTPError` and the
        socket-level `URLError`/`OSError`, with different messages,
        because a rejection and an unreachable server tell an assessor
        different things). WHAT IS STILL NOT COVERED, stated so the gap
        is a decision rather than a discovery: the save POST catches
        `HTTPError` only, so a socket-level failure THERE still surfaces
        raw. `_x_post` is deliberately wider than the path it was
        modelled on; widening the save path too is a behaviour change to
        a path this chore was not scoped to.
        `checkout` fed
        its default empty `--target` straight to `int()`, so the one verb
        an assessor reaches for while lost in history answered with a
        `ValueError` traceback — which the skill's own troubleshooting
        page teaches the reader to read as "the tool is broken".

        A non-number is checked beside the absent one because they are
        one fault to the caller and were one crash in the code, and the
        happy pole is checked because a guard that refuses everything
        keeps the promise by deleting the verb.
        """
        for argv in (["checkout"], ["checkout", "--target", "live"]):
            with self.subTest(argv=argv):
                out = self.x_fails(*argv)
                said = out.stdout + out.stderr
                self.assertIn("ERROR=", said)
                self.assertIn("--target", said)
                self.assertNotIn("Traceback", said)
        # the live pole: a real save number detaches, and the state the
        # rail reads says so. Restored afterwards because a detached
        # server forks the next save, and the sibling tests here save.
        self.addCleanup(canvas.http_json, self.url() + "api/checkout",
                        payload={"revn": None})
        self.assertIn("CHECKED_OUT=1",
                      self.x("checkout", "--target", "1").stdout)
        self.assertEqual(
            canvas.http_json(self.url() + "api/state")["checkout_revn"], 1)

    def test_a_rejected_answer_is_an_error_line_not_a_traceback(self):
        """The early-returning verbs' envelope, both directions.

        `answer` stands for its two siblings: all three now post through
        `_x_post`, and this is the one whose refusal is reachable
        without breaking the server or the project — `/api/pins/answer`
        returns 404 for an unknown pin id, which is the shape an
        assessor hits by pasting a stale id out of an earlier round.

        Before the fix that printed `urllib.error.HTTPError: HTTP Error
        404` with a traceback, against v0.8's promise that every failure
        prints `ERROR=`, and the troubleshooting page teaches a
        traceback to mean the tool itself is broken — so the assessor is
        told to stop rather than to fix the id.

        THE HAPPY POLE IS ASSERTED TOO, on a real pin answered through
        the same verb, because an envelope that swallowed the success
        path would keep the promise by breaking the command.
        """
        out = self.x_fails("answer", "--target", "pin-does-not-exist",
                           "--text", "yes")
        said = out.stdout + out.stderr
        self.assertIn("ERROR=", said, said)
        self.assertIn("answer", said, said)
        self.assertNotIn("Traceback", said, said)

        # the live pole: a pin that exists is answered and says so
        canvas.http_json(
            self.url() + "api/apply",
            payload={"base_revn": canvas.http_json(
                self.url() + "api/state")["head_revn"],
                "artifact": "w",
                "ops": [{"op": "pin", "target": "panel",
                         "id": "pin-envelope",
                         "question": "which source wins?"}]})
        pins = canvas.http_json(self.url() + "api/state")["pins"]
        self.assertTrue(pins, "the pin op left no pin to answer")
        got = self.x("answer", "--target", pins[-1]["id"], "--text", "edgar")
        self.assertIn("ANSWERED=", got.stdout, got.stdout)
        self.assertNotIn("ERROR=", got.stdout + got.stderr)

    def test_rename_reserves_the_height_the_client_will_paint(self):
        """The fifth line-height site, and the only one behind the CLI.

        `cmd_x_as_user`'s rename re-measures the label — that is the
        WP6/D28 fidelity property the verb exists for — and it did so at
        the 1.25 default while the paint uses the element's own
        `lineHeight`. Its four siblings are pinned in
        `TestStoredHeightMatchesThePaintedLineHeight`; this one needs a
        live server, so it is here with one.

        On its OWN element rather than the class's shared `panel`,
        because setting a line height is a durable scene edit and the
        sibling tests read that fixture.

        Seeded through `/api/save` and not `/api/apply`: an agent batch
        is held behind the banner whenever the canvas is dirty, so the
        first draft of this test edited a scene the element had not
        reached yet and then measured the queued copy the server minted
        at 1.25 afterwards. A save is also the honest path — the
        `lineHeight` being set here is the CLIENT's, and the client
        posts scenes.

        96px at `lineHeight: 2.0` against the 1.25 default's 60, at
        fontSize 16 — curator batch 26's 36px under-measure.
        """
        text = "aaa\nbbb\nccc"
        els = canvas.http_json(self.url() + "api/artifact/w")["elements"]
        els = [*els,
               {"id": "lh", "type": "rectangle", "x": 700, "y": 700,
                "width": 400, "height": 300,
                "boundElements": [{"id": "lh-label", "type": "text"}]},
               {"id": "lh-label", "type": "text", "x": 710, "y": 710,
                "width": 40, "height": 20, "text": "one",
                "originalText": "one", "fontSize": 16, "lineHeight": 2.0,
                "containerId": "lh", "autoResize": True}]
        canvas.http_json(
            self.url() + "api/save",
            payload={"scenes": {"w": els},
                     "base_revn": canvas.http_json(
                         self.url() + "api/state")["head_revn"]})
        seeded = next(e for e in self.scene() if e["id"] == "lh-label")
        self.assertEqual(seeded.get("lineHeight"), 2.0,
                         "the save did not keep the client's line height, "
                         "so the rename below has nothing to read back")
        self.x("rename", "--artifact", "w", "--target", "lh", "--text", text)
        lbl = next(e for e in self.scene() if e["id"] == "lh-label")
        fs = lbl.get("fontSize", 16)
        self.assertEqual(lbl.get("lineHeight"), 2.0,
                         "the rename overwrote the client's line height, "
                         "so this measures the default twice")
        self.assertEqual(
            (lbl["height"], canvas.text_dims(text, fs)[1]), (96, 60),
            "`x-as-user rename` stored %rpx where the paint draws 96 at "
            "lineHeight 2.0 (the default reads 60)" % (lbl["height"],))

    def test_verbs_write_what_the_client_writes(self):
        # rename re-measures like the client does
        self.x("rename", "--artifact", "w", "--target", "panel",
               "--text", "Data sources and health")
        els = self.scene()
        lbl = next(e for e in els
                   if e.get("containerId") == "panel")
        self.assertEqual(lbl["text"].replace("\n", " "),
                         "Data sources and health")
        # move drags the whole composed group — X strokes travel
        img = next(e for e in self.scene() if e["id"] == "img")
        x1_before = next(e for e in self.scene()
                         if e["id"] == "img-x1")
        self.x("move", "--artifact", "w", "--target", "img",
               "--dx", "60", "--dy", "0")
        els = self.scene()
        img2 = next(e for e in els if e["id"] == "img")
        x1 = next(e for e in els if e["id"] == "img-x1")
        self.assertEqual(img2["x"] - img["x"], 60)
        self.assertEqual(x1["x"] - x1_before["x"], 60)
        # toggle flips state only; reconciliation composes the glyph
        self.assertTrue(any(e["id"] == "cb-chk" for e in els))
        self.x("toggle", "--artifact", "w", "--target", "cb")
        els = self.scene()
        cb = next(e for e in els if e["id"] == "cb")
        self.assertFalse(cb["customData"]["checked"])
        self.assertFalse(any(e["id"] == "cb-chk" for e in els))
        self.x("toggle", "--artifact", "w", "--target", "cb")
        els = self.scene()
        cb = next(e for e in els if e["id"] == "cb")
        self.assertTrue(cb["customData"]["checked"])
        self.assertTrue(any(e["id"] == "cb-chk" for e in els))
        # delete takes the whole composed group, like the real client
        self.x("delete", "--artifact", "w", "--target", "img")
        els = self.scene()
        self.assertFalse(any(e["id"].startswith("img")
                             for e in els))


class TestMermaidSeeding(Base):
    """WP9: mermaid → op-batch mapping, both target types.

    Flowchart mapping runs against the CHECKED-IN captured skeleton
    fixture (tests/fixtures/mermaid/) so CI never needs a browser; the
    capture came from the real tab pipeline. ER mapping is pure text
    parsing. Every seed lands through apply_batch — the point of the
    design is that a seed is an ordinary revision.
    """

    SKELETONS = (Path(__file__).resolve().parent / "fixtures" /
                 "mermaid" / "signal-pipeline.skeletons.json")

    def test_kind_classifier(self):
        """First-keyword dispatch skips frontmatter and comments."""
        self.assertEqual(canvas.mermaid_kind("flowchart TD\nA-->B"),
                         "flowchart")
        self.assertEqual(canvas.mermaid_kind("graph LR\nA-->B"), "graph")
        self.assertEqual(
            canvas.mermaid_kind("%% note\n---\ntitle: T\n---\nerDiagram"),
            "erdiagram")
        self.assertEqual(canvas.mermaid_kind(""), "")
        # sequence/class/state parse natively upstream but are NOT
        # mappable to this skill's grammar — the refusal set
        for kw in ("sequencediagram", "classdiagram", "statediagram-v2",
                   "gantt"):
            self.assertNotIn(kw, canvas.MERMAID_MAPPED)

    def test_er_parse_full_grammar(self):
        """Symbol relations, attribute blocks, aliases, quoted names."""
        p = canvas._parse_mermaid_er(
            'erDiagram\n'
            '  CUSTOMER ||--o{ ORDER : places\n'
            '  ORDER }|..|{ "Line Item" : contains\n'
            '  p[Person] ||--|| CUSTOMER : "is one"\n'
            '  RUN |o--o| RUN : "rerun of"\n'
            '  CUSTOMER {\n'
            '    string name PK "legal name"\n'
            '    int age\n'
            '  }\n')
        self.assertEqual(p["errors"], [])
        self.assertEqual(p["entities"]["p"]["display"], "Person")
        self.assertEqual(p["entities"]["CUSTOMER"]["attrs"][0],
                         {"type": "string", "name": "name",
                          "keys": "PK", "comment": "legal name"})
        r0 = p["relations"][0]
        self.assertEqual((r0["lc"], r0["rc"], r0["label"]),
                         ("1", "0..*", "places"))
        r1 = p["relations"][1]
        self.assertEqual((r1["a"], r1["b"], r1["lc"], r1["rc"]),
                         ("ORDER", "Line Item", "1..*", "1..*"))
        self.assertEqual(p["relations"][3]["a"], p["relations"][3]["b"])
        # the silent half: a verbose-form line errors by TEXT, never
        # half-parses
        bad = canvas._parse_mermaid_er(
            "erDiagram\n  CUSTOMER one to many ORDER : places\n")
        self.assertEqual(len(bad["errors"]), 1)
        self.assertIn("one to many", bad["errors"][0])

    def test_er_seed_lands_through_apply(self):
        """Entities compose attr rows; cardinality drives arrowheads."""
        p = canvas._parse_mermaid_er(
            'erDiagram\n'
            '  CUSTOMER ||--o{ ORDER : places\n'
            '  ORDER }|--|{ SKU : lists\n'
            '  RUN ||--|| REPORT : yields\n'
            '  RUN |o--o| RUN : "rerun of"\n'
            '  CUSTOMER {\n'
            '    string name PK\n    string sector\n'
            '    int age\n    bool active\n  }\n')
        ops = canvas._er_seed_ops(p)
        record, _ = self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "dom", "type": "domain", "concept": "model",
                       "name": "Model"},
            "ops": ops})
        els = self.store.scenes["dom"]
        ix = {e["id"]: e for e in els}
        cust = ix["customer"]
        self.assertEqual(cust["customData"]["kind"], "entity")
        rows = [e for e in els
                if (e.get("customData") or {}).get("attr_of") == "customer"]
        self.assertEqual(len(rows), 3)   # visible rows capped at 3
        self.assertIn("- bool active",
                      cust["customData"]["tooltip"])  # overflow → tooltip
        one_many = ix["r-customer-places"]
        self.assertIsNone(one_many["startArrowhead"])
        self.assertEqual(one_many["endArrowhead"], "arrow")
        many_many = ix["r-order-lists"]
        self.assertEqual((many_many["startArrowhead"],
                          many_many["endArrowhead"]), ("arrow", "arrow"))
        one_one = ix["r-run-yields"]
        self.assertEqual((one_one["startArrowhead"],
                          one_one["endArrowhead"]), (None, None))
        # reflexive: verb-only label, loop route, cardinality in tooltip
        loop = ix["r-run-rerun-of"]
        self.assertEqual(
            (loop.get("startBinding") or {}).get("elementId"),
            (loop.get("endBinding") or {}).get("elementId"))
        lbl = next(e for e in els if e.get("containerId") == loop["id"])
        self.assertEqual(lbl["text"], "rerun of")
        self.assertIn("0..1", loop["customData"]["tooltip"])
        # label carries the many-side token on the non-reflexive pair
        lbl2 = next(e for e in els
                    if e.get("containerId") == one_many["id"])
        self.assertEqual(lbl2["text"], "places 0..*")

    def test_flow_seed_from_captured_skeletons(self):
        """The real captured conversion maps, lands and lints clean."""
        skeletons = json.loads(self.SKELETONS.read_text())
        ops, notes, errors = canvas._flow_seed_ops(skeletons)
        self.assertEqual(errors, [])
        record, _ = self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "flow", "type": "flow", "concept": "pipe",
                       "name": "Pipe"},
            "ops": ops})
        els = self.store.scenes["flow"]
        ix = {e["id"]: e for e in els}
        # semantic slugs, never the mermaid short ids
        self.assertIn("ingest-market-data", ix)
        self.assertIn("schema-valid", ix)
        self.assertNotIn("ingest", ix)
        self.assertEqual(ix["schema-valid"]["type"], "diamond")
        # dagre geometry kept, snapped to the 4px grid
        for eid in ("ingest-market-data", "schema-valid",
                    "quarantine-batch"):
            self.assertEqual(ix[eid]["x"] % 4, 0)
            self.assertEqual(ix[eid]["y"] % 4, 0)
        # the retry edge is a routed self-loop
        loop = ix["score-signals-score-signals"]
        self.assertEqual(
            (loop.get("startBinding") or {}).get("elementId"),
            "score-signals")
        self.assertEqual(
            (loop.get("endBinding") or {}).get("elementId"),
            "score-signals")
        self.assertEqual(loop.get("strokeStyle"), "dashed")
        # edge labels ride along
        yes = ix["schema-valid-enrich-with-fundamentals"]
        ylbl = next(e for e in els if e.get("containerId") == yes["id"])
        self.assertEqual(ylbl["text"], "yes")
        # and the seeded artifact stands clean under the geometry lints
        # (the differential control for the router's interior-elbow
        # filter: the ORIGINAL route of the "no" branch ran 72px inside
        # its target)
        lint = canvas.project_lint(
            self.project, els, registry=self.store.registry,
            artifact_type="flow", aid="flow")
        self.assertEqual(lint["errors"], [])

    def test_flow_seed_subgraph_frames(self):
        """One subgraph level maps to a frame; nesting refuses.

        Hand-built skeletons: the vendored converter (2.2.2) cannot yet
        emit subgraph containers (it degrades to a picture — refused at
        the CLI), but the mapping is specified and tested so it works
        the day upstream fixes the parser.
        """
        sub = {"id": "desk", "type": "rectangle",
               "groupIds": ["subgraph_group_desk"],
               "label": {"text": "Data desk"},
               "x": 0, "y": 0, "width": 400, "height": 200}
        member = {"id": "triage", "type": "rectangle",
                  "groupIds": ["subgraph_group_desk"],
                  "label": {"text": "Triage alert"},
                  "x": 40, "y": 60, "width": 160, "height": 60}
        loose = {"id": "review", "type": "rectangle",
                 "label": {"text": "Review"},
                 "x": 500, "y": 60, "width": 160, "height": 60}
        ops, notes, errors = canvas._flow_seed_ops([sub, member, loose])
        self.assertEqual(errors, [])
        frame_op = next(o for o in ops
                        if o["element"]["type"] == "frame")
        self.assertEqual(frame_op["element"]["id"], "data-desk")
        triage = next(o["element"] for o in ops
                      if o["element"]["id"] == "triage-alert")
        self.assertEqual(triage["frameId"], "data-desk")
        review = next(o["element"] for o in ops
                      if o["element"]["id"] == "review")
        self.assertNotIn("frameId", review)
        nested = dict(sub, groupIds=["subgraph_group_desk",
                                     "subgraph_group_outer"])
        _, _, errs = canvas._flow_seed_ops([nested, member])
        self.assertTrue(errs and "nested" in errs[0])

    def test_flow_seed_image_degrade_named(self):
        """The library's silent picture-downgrade is named, not mapped."""
        _, _, errs = canvas._flow_seed_ops([{"type": "image"}])
        self.assertEqual(len(errs), 1)
        self.assertIn("degraded this diagram to a picture", errs[0])

    def test_budget_rider_on_oversized_seed(self):
        """An over-budget seed carries set_budget + reason; a small one
        doesn't."""
        big = "erDiagram\n" + "\n".join(
            "  E%d ||--o{ E%d : feeds" % (i, i + 1) for i in range(11))
        p = canvas._parse_mermaid_er(big)
        self.assertEqual(p["errors"], [])
        ops = canvas._er_seed_ops(p)
        n = sum(1 for o in ops
                if o["element"]["type"] == "rectangle")
        self.assertGreater(n, 8)
        # the CLI adds the rider; prove the batch validates WITH it, at
        # the same create-then-registry timing the command uses
        ops.append({"op": "registry", "action": "set_budget",
                    "artifact": "dom", "nodes": n, "arrows": 12,
                    "reason": "mermaid seed: %d nodes" % n})
        record, _ = self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "dom", "type": "domain", "concept": "m",
                       "name": "M"},
            "ops": ops})
        self.assertIn("dom", self.store.registry.get("budgets", {}))

    def test_flow_to_mermaid_roundtrip_text(self):
        """--relayout's generator: ids carry identity, keywords are
        safe, labels are quoted, arrow labels ride the edge."""
        els = [
            {"id": "end", "type": "rectangle", "x": 0, "y": 0,
             "width": 160, "height": 60,
             "customData": {"role": "node"}},
            {"id": "end-label", "type": "text", "containerId": "end",
             "text": "Wrap [it] up"},
            {"id": "gate", "type": "diamond", "x": 300, "y": 0,
             "width": 120, "height": 120,
             "customData": {"role": "node"}},
            {"id": "gate-label", "type": "text", "containerId": "gate",
             "text": "Ready?"},
            {"id": "lone", "type": "ellipse", "x": 600, "y": 0,
             "width": 100, "height": 60,
             "customData": {"role": "node"}},
            {"id": "store", "type": "rectangle", "x": 900, "y": 0,
             "width": 160, "height": 60, "roundness": {"type": 3},
             "customData": {"role": "node"}},
            {"id": "t1", "type": "arrow", "x": 0, "y": 0,
             "points": [[0, 0], [100, 0]],
             "startBinding": {"elementId": "gate"},
             "endBinding": {"elementId": "end"}},
            {"id": "t1-label", "type": "text", "containerId": "t1",
             "text": "yes"},
        ]
        text, n = canvas._flow_to_mermaid(els)
        self.assertEqual(n, 4)
        # the mermaid keyword `end` is shielded by the n_ prefix
        self.assertIn('n_gate{"Ready?"} -->|yes| n_end["Wrap [it] up"]',
                      text)
        # an unconnected node still gets declared (so dagre places it).
        # FLIPPED: this asserted the STADIUM `(["lone"])` until v0.9,
        # pinning a defect. The vendored converter sends STADIUM and
        # ROUND to one result — a rounded RECTANGLE — so an ellipse
        # exported that way came back a rectangle, live, for every
        # `--relayout`. `((…))` is the only form that re-imports as an
        # ellipse. Both poles are asserted because the wrong one reads
        # as correct.
        self.assertIn('n_lone(("lone"))', text)
        self.assertNotIn('n_lone(["lone"])', text)
        # and roundness is no longer dropped: a rounded rectangle (the
        # `store` kind) exported as `["x"]` came back square
        self.assertIn('n_store("store")', text)
        self.assertNotIn('n_store["store"]', text)

    def _relayout(self, aid, skeletons):
        """Run `mermaid --relayout` over `aid` with the browser stubbed.

        The conversion is the only part of the command that needs a tab,
        and it is not what these tests are about — stubbing it keeps the
        guard, the op build and the NOTE on the real code path.

        Args:
            aid: The flow artifact to re-lay.
            skeletons: What the conversion returns — the positions dagre
                would have produced, keyed `n_<element id>`.

        Returns:
            The command's stdout, NOTE line included.
        """
        ns = argparse.Namespace(
            project=str(self.tmp), artifact=aid, relayout=True, file=None,
            concept=None, tab_timeout=5, no_headless=True, check=False,
            render=False, from_skeletons=None)
        buf = io.StringIO()
        with mock.patch.object(canvas, "_mermaid_convert",
                               return_value=(skeletons, None)), \
                contextlib.redirect_stdout(buf):
            canvas.cmd_mermaid(ns)
        return buf.getvalue()

    @staticmethod
    def _dagre_row(*ids):
        """Skeletons that put every named node on one new row.

        Args:
            ids: The element ids dagre placed, in left-to-right order.

        Returns:
            A skeleton list far enough from the seed's own coordinates
            that every node earns a `mod x/y` op.
        """
        return [{"id": "n_" + eid, "type": "rectangle", "x": i * 300,
                 "y": 400, "width": 140, "height": 60}
                for i, eid in enumerate(ids)]

    def test_relayout_names_the_node_the_user_dragged(self):
        """The guard reads PLACEMENT authorship, not creation authorship.

        `r5-11`: the guard filtered `customData.author == "user"` over an
        ops list that only ever holds nodes the AGENT created — the empty
        set by construction, so the one thing the NOTE exists to protect
        was the one thing it could never name. A drag writes no `author`;
        it writes a position. So the position gets its own authorship.
        """
        self.store.apply_batch(seed_flow_batch())
        els = self.scene()
        for e in els:
            if e["id"] == "payment":
                e["x"] += 64
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els})
        out = self._relayout("checkout-flow",
                             self._dagre_row("cart", "checkout", "payment",
                                             "confirm"))
        note = next((ln for ln in out.splitlines()
                     if ln.startswith("NOTE=")), "")
        self.assertIn("1 user-placed element", note)
        self.assertIn("payment", note)
        # and it names ONLY that one — the other three moved too
        for other in ("cart", "checkout", "confirm"):
            self.assertNotIn(other, note)

    def test_relayout_over_an_untouched_flow_says_nothing(self):
        """The other pole: agent-drawn placement is the agent's to redo.

        Without this arm the fix could be `moved_user = ops` — every
        node named on every re-layout, which reads as caution and is
        noise the user cannot act on.
        """
        self.store.apply_batch(seed_flow_batch())
        out = self._relayout("checkout-flow",
                             self._dagre_row("cart", "checkout", "payment",
                                             "confirm"))
        # three of the four move: the re-layout anchors dagre's min
        # corner onto the current one, so the leftmost node stays put
        self.assertIn("MOVES=3", out)       # the re-layout really ran
        self.assertNotIn("user-placed", out)

    def test_relayout_carries_the_frame_with_its_members(self):
        """A lane does not stay put while its members re-lay.

        Frames are not in the conversion — `_flow_to_mermaid` emits
        nodes and dagre places nodes — so the re-layout used to walk
        every member out of a lane that never moved, leaving a `frameId`
        the picture contradicts. Measured before the fix on this exact
        scene: three of the four members ended up wholly outside the
        frame and `lint_layout` reported nothing at all, which is why
        this asserts the LINT as well as the geometry.
        """
        ops = [{"op": "add", "element": {
            "type": "frame", "id": "laneA", "label": "Lane A", "x": 0,
            "y": 0, "width": 900, "height": 200}}]
        for i, nid in enumerate(("a", "b", "c", "d")):
            ops.append({"op": "add", "element": {
                "type": "rectangle", "id": nid, "label": nid.upper(),
                "x": 40 + i * 220, "y": 60, "width": 140, "height": 60,
                "role": "node", "frameId": "laneA"}})
        for a, b in (("a", "b"), ("b", "c"), ("c", "d")):
            ops.append({"op": "add",
                        "element": {"type": "arrow", "id": "t_%s%s" % (a, b)},
                        "from": a, "to": b})
        self.store.apply_batch({
            "base_revn": 0, "artifact": "laned",
            "create": {"id": "laned", "type": "flow", "concept": "c",
                       "name": "Laned"},
            "ops": ops})
        # the shape that breaks it: a row re-laid as a column, so the
        # lane needs a different shape and not just a slide
        self._relayout("laned", [
            {"id": "n_" + n, "type": "rectangle", "x": 0, "y": i * 140,
             "width": 140, "height": 60}
            for i, n in enumerate(("a", "b", "c", "d"))])
        els = canvas.Store(self.project).scenes["laned"]
        fr = next(e for e in els if e["type"] == "frame")
        for m in (e for e in els if e.get("frameId") == "laneA"):
            self.assertGreaterEqual(m["x"], fr["x"], m["id"])
            self.assertGreaterEqual(m["y"], fr["y"], m["id"])
            self.assertLessEqual(m["x"] + m["width"],
                                 fr["x"] + fr["width"], m["id"])
            self.assertLessEqual(m["y"] + m["height"],
                                 fr["y"] + fr["height"], m["id"])
        self.assertEqual(
            [w for w in canvas.lint_layout(els, artifact_type="flow")
             ["warnings"] if "says it is in frame" in w], [])

    def test_relayout_of_a_misaligned_lane_that_agrees_is_still_a_noop(self):
        """The frame refit may not invent a revision out of grid snapping.

        The other pole of the test above, and the one the refit put at
        risk. The refit snaps to the 4px grid and compares exactly, so a
        lane sitting at coordinates a real drag produces — 3, not 4 —
        never equalled its own snapped box and emitted a `mod` on a
        canvas where dagre agreed with every single node. That turned
        "noop, dagre agrees with the current placement" into a queued
        revision nudging the lane by up to 3px, on exactly the messy
        user-arranged flows `--relayout` exists for.

        A tolerance would not have settled it: at (2,6) each axis is off
        by exactly 2, which the node loop's `< 2` still admits. The gate
        asks whether anything moved at all.
        """
        ops = [{"op": "add", "element": {
            "type": "frame", "id": "laneA", "label": "Lane A", "x": 3,
            "y": 0, "width": 900, "height": 200}}]
        for i, nid in enumerate(("a", "b", "c", "d")):
            ops.append({"op": "add", "element": {
                "type": "rectangle", "id": nid, "label": nid.upper(),
                "x": 40 + i * 220, "y": 60, "width": 140, "height": 60,
                "role": "node", "frameId": "laneA"}})
        for a, b in (("a", "b"), ("b", "c"), ("c", "d")):
            ops.append({"op": "add",
                        "element": {"type": "arrow", "id": "t_%s%s" % (a, b)},
                        "from": a, "to": b})
        self.store.apply_batch({
            "base_revn": 0, "artifact": "askew",
            "create": {"id": "askew", "type": "flow", "concept": "c",
                       "name": "Askew"},
            "ops": ops})
        before = {e["id"]: (e.get("x"), e.get("y"), e.get("width"),
                            e.get("height"))
                  for e in canvas.Store(self.project).scenes["askew"]}
        # dagre returns the placement the canvas already has
        out = self._relayout("askew", [
            {"id": "n_" + n, "type": "rectangle", "x": i * 220, "y": 0,
             "width": 140, "height": 60}
            for i, n in enumerate(("a", "b", "c", "d"))])
        self.assertIn("RELAYOUT=noop", out)
        self.assertNotIn("MOVES=", out)
        after = {e["id"]: (e.get("x"), e.get("y"), e.get("width"),
                           e.get("height"))
                 for e in canvas.Store(self.project).scenes["askew"]}
        self.assertEqual(before, after)     # and nothing was written

    def test_cmd_refusals(self):
        """Unmapped types, existing artifacts and subgraphs refuse with
        named reasons; the ER path needs no server at all."""
        canvas_py = Path(canvas.__file__)
        env_project = str(self.tmp)

        def run(stdin_text, *argv):
            return subprocess.run(
                [sys.executable, str(canvas_py), "--project", env_project,
                 "mermaid", *argv],
                input=stdin_text, capture_output=True, text=True,
                timeout=60)

        r = run("sequenceDiagram\n  A->>B: hi\n",
                "--artifact", "seq", "--concept", "c")
        self.assertEqual(r.returncode, 2)
        self.assertIn("isn't mappable", r.stderr)   # die() → stderr
        r = run("flowchart TD\n  subgraph s\n  a-->b\n  end\n",
                "--artifact", "f", "--concept", "c")
        self.assertEqual(r.returncode, 2)
        self.assertIn("subgraph", r.stderr)
        # ER seeds land offline — no server, no browser
        r = run("erDiagram\n  A ||--o{ B : owns\n",
                "--artifact", "dom", "--concept", "c")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SEED_KIND=erdiagram", r.stdout)
        # seed-only: an existing artifact refuses — the drawing is the
        # truth once landed
        r = run("erDiagram\n  A ||--o{ B : owns\n",
                "--artifact", "dom", "--concept", "c")
        self.assertEqual(r.returncode, 2)
        self.assertIn("already exists", r.stderr)


class TestRouterInteriorElbows(Base):
    """v0.8: no candidate elbow may land inside an endpoint box."""

    def test_side_entry_elbow_stays_outside(self):
        """The mermaid-found geometry: source centre inside the
        destination's x-span used to elbow THROUGH the box."""
        src = {"id": "s", "x": 392, "y": 192, "width": 192, "height": 192}
        dst = {"id": "d", "x": 444, "y": 476, "width": 212, "height": 60}
        arrow = {"id": "a", "type": "arrow"}
        canvas.route_arrow(arrow, src, dst)
        pts = [(arrow["x"] + p[0], arrow["y"] + p[1])
               for p in arrow["points"]]
        for px, py in pts[1:-1]:
            inside_dst = (444 + 1 < px < 656 - 1 and
                          476 + 1 < py < 536 - 1)
            inside_src = (392 + 1 < px < 584 - 1 and
                          192 + 1 < py < 384 - 1)
            self.assertFalse(inside_dst or inside_src,
                             "elbow %r inside an endpoint box" % ((px, py),))

    def test_offset_pair_still_elbows(self):
        """The silent half: a legal L-elbow route is untouched."""
        src = {"id": "s", "x": 0, "y": 0, "width": 160, "height": 60}
        dst = {"id": "d", "x": 400, "y": 300, "width": 160, "height": 60}
        arrow = {"id": "a", "type": "arrow"}
        canvas.route_arrow(arrow, src, dst)
        self.assertGreaterEqual(len(arrow["points"]), 3)


class TestPhantomPassThrough(Base):
    """WP4b e1: a node with a stroke drawn across it stops being a step.

    The catalogue proves the ELK configuration end to end
    (`phantom_passthrough_shared_attach`). These pin the scope decision
    underneath it, which is the whole content of the check: the broad
    reading — any collinear in/out pair on opposite borders — is the
    normal correct flowchart, 70 findings over the 24 frozen artifacts,
    and does not ship. What ships is the half where ink crosses the box.
    """

    def _rank(self, foot, node_w=80):
        """A -> N -> Z on one rank line, with e2's foot placed by caller.

        Args:
            foot: Absolute x of e2's start point.
            node_w: N's width, so a caller can vary what "across" means.

        Returns:
            The five-element scene.
        """
        return [
            {"id": "A", "type": "rectangle", "x": 0, "y": 100,
             "width": 80, "height": 40, "customData": {"role": "node"}},
            {"id": "N", "type": "rectangle", "x": 200, "y": 100,
             "width": node_w, "height": 40, "customData": {"role": "node"}},
            {"id": "Z", "type": "rectangle", "x": 528, "y": 100,
             "width": 80, "height": 40, "customData": {"role": "node"}},
            {"id": "e1", "type": "arrow", "x": 80, "y": 120,
             "points": [[0, 0], [120, 0]],
             "startBinding": {"elementId": "A", "focus": 0, "gap": 1},
             "endBinding": {"elementId": "N", "focus": 0, "gap": 1},
             "customData": {"role": "edge", "route": "server"}},
            {"id": "e2", "type": "arrow", "x": foot, "y": 120,
             "points": [[0, 0], [528 - foot, 0]],
             "startBinding": {"elementId": "N", "focus": 0, "gap": 1},
             "endBinding": {"elementId": "Z", "focus": 0, "gap": 1},
             "customData": {"role": "edge", "route": "server"}},
        ]

    def _hits(self, els):
        """The phantom pass-through warnings a scene produces.

        Args:
            els: The scene's elements.

        Returns:
            The matching lint lines.
        """
        out = canvas.lint_layout(els, artifact_type="flow")
        return [m for m in out["errors"] + out["warnings"] + out["notes"]
                if "read as one stroke through" in m]

    def test_both_feet_on_one_border_covers_the_whole_node(self):
        """The ELK production configuration: 80px of N under the stroke."""
        hits = self._hits(self._rank(200))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("80px of it has arrow drawn over it", hits[0])
        self.assertIn("leave 0px of the node's 80px bare", hits[0])

    def test_feet_on_opposite_borders_are_silent(self):
        """The scope decision, as a test: this is the normal drawing.

        One foot moves 80px, the arrows stay collinear and stay opposed,
        and the only thing that changes is whether ink crosses the box.
        A check built on the broad criterion passes every other
        assertion in this class and fails this one.
        """
        self.assertEqual(self._hits(self._rank(280)), [])

    def test_a_partial_overlap_reports_only_what_is_covered(self):
        """The magnitude is the covered span, not the node and not the run.

        e2's foot sits 50px inside N's left border, so it draws over the
        remaining 30px and 50px of the node stays bare. Three other
        readings are available in the same picture — 50, 80 and 448 —
        and this excludes all of them.
        """
        hits = self._hits(self._rank(250))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("leave 50px of the node's 80px bare", hits[0])
        self.assertIn("30px of it has arrow drawn over it", hits[0])

    def test_a_leaving_foot_behind_the_arriving_one_covers_it_all(self):
        """The case a naive foot-difference gets wrong by 20px.

        e2 starts 20px OUTSIDE N's left border, so the two strokes
        overlap on open canvas as well and the whole node has arrow
        across it. The gap between the feet is 20px and the covered
        span is 80px, not 60px — nothing about the node is bare.
        """
        hits = self._hits(self._rank(180))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("leave 0px of the node's 80px bare", hits[0])
        self.assertIn("80px of it has arrow drawn over it", hits[0])

    def test_a_turn_at_the_node_is_not_a_pass_through(self):
        """N is a step when the eye has to turn at it, whatever the feet.

        e2 leaves N's BOTTOM edge going down. The feet are 0px apart on
        x, so an implementation that tested coincidence rather than a
        shared axis would fire here — and would be reporting a corner.
        """
        els = self._rank(200)
        els[4]["x"], els[4]["y"] = 240, 140
        els[4]["points"] = [[0, 0], [0, 200]]
        self.assertEqual(self._hits(els), [])

    def test_a_self_loop_is_not_a_pass_through(self):
        """One node at both ends is a loop, not a stroke passing by."""
        node = {"id": "A", "type": "rectangle", "x": 0, "y": 100,
                "width": 80, "height": 40, "customData": {"role": "node"}}
        pts = canvas._self_loop_path(node)
        x0, y0 = pts[0]
        loop = {"id": "e1", "type": "arrow", "x": x0, "y": y0,
                "points": [[p[0] - x0, p[1] - y0] for p in pts],
                "startBinding": {"elementId": "A", "focus": 0, "gap": 1},
                "endBinding": {"elementId": "A", "focus": 0, "gap": 1},
                "customData": {"role": "edge", "route": "server"}}
        self.assertEqual(self._hits([node, loop]), [])

    def test_two_arrows_arriving_head_on_are_not_a_pass_through(self):
        """Exactly one arriving and one leaving, or there is no relation.

        Both arrowheads land on N from opposite sides. A reader sees a
        convergence, not a stroke continuing past — there is nothing for
        the completion to join into, because neither arrow goes anywhere
        after N.
        """
        els = self._rank(200)
        els[4]["startBinding"], els[4]["endBinding"] = (
            {"elementId": "Z", "focus": 0, "gap": 1},
            {"elementId": "N", "focus": 0, "gap": 1})
        els[4]["x"], els[4]["points"] = 528, [[0, 0], [-328, 0]]
        self.assertEqual(self._hits(els), [])

    def _shaped(self, kind, foot_a, foot_b, y):
        """A -> N -> Z where N is any node shape and the line is chosen.

        Args:
            kind: N's element type.
            foot_a: Absolute x where e1's head lands.
            foot_b: Absolute x where e2's foot starts.
            y: The shared line both arrows run on.

        Returns:
            The five-element scene.
        """
        els = self._rank(200)
        els[1]["type"] = kind
        els[3]["y"], els[4]["y"] = y, y
        els[3]["points"] = [[0, 0], [foot_a - 80, 0]]
        els[4]["x"] = foot_b
        els[4]["points"] = [[0, 0], [528 - foot_b, 0]]
        return els

    def test_feet_on_a_diamonds_own_outline_are_silent(self):
        """Shape blindness, instance seven — and it was self-inflicted.

        A rhombus fills half its box, so at quarter height its real
        chord is 40px inside an 80px box. Two arrows terminating
        CORRECTLY on that outline sit 40px apart with the whole body
        between them and no ink on the shape at all. Reading the box
        called those 40px of corner void "arrow drawn over the node"
        and offered a remedy that would have degraded a drawing that
        was already right — the exact failure this check was narrowed
        to avoid (task 24 review, F1).
        """
        self.assertEqual(self._hits(self._shaped("diamond", 220, 260, 110)),
                         [])

    def test_feet_on_an_ellipses_own_outline_are_silent(self):
        """The same, on the shape whose chord shrinks more gently."""
        self.assertEqual(
            self._hits(self._shaped("ellipse", 211.4, 268.6, 106)), [])

    def test_a_rectangle_reads_the_same_off_the_centre_line(self):
        """The control that keeps the fix from being a blanket exemption.

        A box's bbox IS its outline at every height, so the clip returns
        the same interval the old bbox reading did and nothing about the
        ordinary case moves. Without this, suppressing the check on
        anything off-centre would satisfy the two tests above.
        """
        self.assertEqual(self._hits(self._shaped("rectangle", 200, 280, 110)),
                         [])
        hits = self._hits(self._shaped("rectangle", 200, 240, 110))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("40px of it has arrow drawn over it", hits[0])

    def test_ink_across_a_diamond_fires_against_its_real_chord(self):
        """The other pole: the check still bites on inset shapes.

        On the centre line the diamond's chord is its full 80px and e2
        starts at the centre, so 40px of the shape carries arrow. The
        magnitude is measured against the CHORD, which the row below
        proves: at quarter height the same 20px of ink is reported
        against a 40px node rather than an 80px one.
        """
        hits = self._hits(self._shaped("diamond", 200, 240, 120))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("leave 40px of the node's 80px bare", hits[0])
        self.assertIn("40px of it has arrow drawn over it", hits[0])

        hits = self._hits(self._shaped("diamond", 220, 240, 110))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("leave 20px of the node's 40px bare", hits[0])
        self.assertIn("20px of it has arrow drawn over it", hits[0])

    def test_ink_across_an_ellipse_fires_too(self):
        """The third shape, so the fix is not diamond-specific."""
        hits = self._hits(self._shaped("ellipse", 200, 240, 120))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("40px of it has arrow drawn over it", hits[0])

    def test_the_frozen_fixtures_have_no_phantom_pass_throughs(self):
        """The corpus pole, and the number the scope decision rests on.

        Zero here against 70 under the broad criterion is the whole
        argument for the narrowing: those 70 were every chained node of
        every correct flow, and the finding's own remedy would have
        degraded each of those drawings.

        THE SYNTHETIC SCENE RIDES THE SAME LOOP, and that is the point of
        it rather than a convenience. Curator batch 24 (2026-08-16) added
        it against the mortality spike's §4c class: a CORPUS-WIDE SILENCE
        has no firing anywhere in it by construction — a corpus that
        fires is a corpus with a defect — so it is a pure absence, and a
        `phantom_passthrough` patched to answer nothing reports exactly
        the zero this test asserts and passes. Measured: it did. The zero
        was load-bearing (it is quoted above as the scope argument) and
        the measurement could not carry it.

        Appending `_rank(200)` to the iteration rather than asserting it
        separately is what makes the repair worth anything, per guide
        rule 8's second half: the firing has to run THROUGH the entry
        point under test, and here that is `_hits` inside this loop.
        Assert it before the loop and a caching or artifact-type
        regression that silenced the corpus arm alone would still pass.
        `TestCorridorKind.test_no_frozen_artifact_contains_a_merged_
        stroke` is the same shape reached from the other side: its corpus
        genuinely produces corridors, so its own non-empty output is the
        firing and no synthetic scene is needed.
        """
        root = Path(__file__).resolve().parent / "fixtures"
        scenes = [(p.name, json.loads(p.read_text()).get("elements") or [])
                  for p in sorted(root.rglob("*.excalidraw"))]
        spoke: list[str] = []
        for name, els in [*scenes, (None, self._rank(200))]:
            hits = self._hits(els)
            if name is None:
                spoke = hits
            else:
                self.assertEqual(hits, [], name)
        self.assertEqual(len(spoke), 1,
                         "the shared-attach chain fed through this same "
                         "loop must fire, or the zero above is a "
                         "statement about the lint and not about the "
                         "corpus; got %s" % spoke)
        self.assertIn("80px of it has arrow drawn over it", spoke[0])


class TestDegenerateArrowGeometry(Base):
    """WP4b e15: a malformed path is named before anything reads it.

    Every arrow check downstream of this one — the endpoint gap, the
    interior run, the crossing count, the direction read — begins by
    asking the points list a question a degenerate list cannot answer,
    and each of them answers anyway. The number that comes back is noise
    sitting inside a tolerance band, which is health's exact shape.

    Both poles are here for all four arms, and the overshoot arm carries
    a second assertion the other three do not need: what the endpoint
    check it sits next to says on that scene. That assertion USED to be
    silence — the hole this arm was built to close — and Task 54 closed
    the near half of it by putting a tolerance floor under the
    interior-run walk's gate. This class said it would rather say so
    than keep a rule nothing needs, so it says so: within `tol` the two
    checks now speak together, and the arm's remaining justification is
    the far half, where `endpoint_gap` takes the scene and reports the
    gap instead of the crossing. Both are asserted below.
    """

    def _chain(self, pts, ax=80, ay=120, bind=True):
        """A -> N on one rank line, with the arrow's geometry supplied.

        Args:
            pts: The arrow's `points`, relative to `(ax, ay)`.
            ax: The arrow's own x origin.
            ay: The arrow's own y origin.
            bind: Whether to bind the arrow's ends to A and N.

        Returns:
            The three-element scene: nodes A and N, arrow e1.
        """
        arrow = {"id": "e1", "type": "arrow", "x": ax, "y": ay,
                 "points": pts,
                 "customData": {"role": "edge", "route": "server"}}
        if bind:
            arrow["startBinding"] = {"elementId": "A", "focus": 0, "gap": 1}
            arrow["endBinding"] = {"elementId": "N", "focus": 0, "gap": 1}
        return [
            {"id": "A", "type": "rectangle", "x": 0, "y": 100,
             "width": 80, "height": 40, "customData": {"role": "node"}},
            {"id": "N", "type": "rectangle", "x": 200, "y": 100,
             "width": 80, "height": 40, "customData": {"role": "node"}},
            arrow,
        ]

    def _lint(self, els):
        """Run the layout lint over a flow scene.

        Args:
            els: The scene's elements.

        Returns:
            The concatenated errors and warnings.
        """
        out = canvas.lint_layout(els, artifact_type="flow")
        return out["errors"] + out["warnings"]

    def _degenerate(self, els):
        """The degenerate-geometry warnings a scene produces.

        Args:
            els: The scene's elements.

        Returns:
            The matching lint lines.
        """
        return [m for m in self._lint(els) if "degenerate geometry" in m]

    def test_a_well_formed_arrow_is_silent(self):
        """The other pole for all four arms at once.

        A straight two-point arrow from A's right edge to N's left edge:
        distinct points, a 120px final segment, distinct endpoints, and
        a head that stops on the border it binds. Nothing here is
        degenerate and the check must say nothing, or every assertion
        below is satisfied by a rule that fires on every arrow.
        """
        self.assertEqual(self._degenerate(self._chain([[0, 0], [120, 0]])),
                         [])

    def test_a_near_zero_final_segment_is_named_with_its_length(self):
        """A 2px last leg: the head's direction is rounding, not routing.

        The magnitude is asserted because the floor is derived from the
        storage (whole-pixel rounding) rather than picked, so a check
        that reported the whole path's length, or the elbow leg's, would
        be measuring something else and passing this by existence.
        """
        hits = self._degenerate(
            self._chain([[0, 0], [118, 0], [120, 0]]))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("final segment is 2.0px long", hits[0])

    def test_a_duplicated_waypoint_is_named_by_its_index(self):
        """A repeated interior point, reported 1-based as the agent sees it.

        With the count and its denominator (task 24 review, F2): "1 of
        3" and "3 of 3" are different repairs on paths whose index list
        alone reads the same way.
        """
        hits = self._degenerate(
            self._chain([[0, 0], [60, 0], [60, 0], [120, 0]]))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("point 3 repeats the point before — 1 of its 3 "
                      "segments have zero length", hits[0])

    def test_several_duplicates_report_the_count_not_just_the_list(self):
        """The pole that makes the denominator load-bearing."""
        hits = self._degenerate(self._chain(
            [[0, 0], [60, 0], [60, 0], [60, 0], [120, 0]]))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("points 3, 4 repeat the point before — 2 of its 4 "
                      "segments have zero length", hits[0])

    def test_a_path_that_returns_to_its_start_is_named(self):
        """Coincident endpoints on an unbound arrow: it goes nowhere.

        Unbound on purpose — a self-loop binds one node at both ends and
        `_self_loop_path` gives it five DISTINCT points, so it is not
        this fault and must not be caught by it.

        The magnitude is the INK (task 24 review, F2): 60 + 40 + 72.1 =
        172px of stroke that arrives back where it started, which is
        what says whether a reader sees a stray tick or a whole loop.
        """
        hits = self._degenerate(self._chain(
            [[0, 0], [60, 0], [60, 40], [0, 0]], bind=False))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("starts and ends on the same point, so its 172px of "
                      "stroke goes nowhere", hits[0])

    def test_a_routed_self_loop_is_not_a_closed_path(self):
        """The control for the arm above, on the shape that looks like it."""
        node = {"id": "A", "type": "rectangle", "x": 0, "y": 100,
                "width": 80, "height": 40, "customData": {"role": "node"}}
        pts = canvas._self_loop_path(node)
        x0, y0 = pts[0]
        loop = {"id": "e1", "type": "arrow", "x": x0, "y": y0,
                "points": [[p[0] - x0, p[1] - y0] for p in pts],
                "startBinding": {"elementId": "A", "focus": 0, "gap": 1},
                "endBinding": {"elementId": "A", "focus": 0, "gap": 1},
                "customData": {"role": "edge", "route": "server"}}
        self.assertEqual(self._degenerate([node, loop]), [])

    def test_a_head_past_the_far_outline_is_named_with_the_overshoot(self):
        """3px past N's far edge, having crossed all 80px of the node."""
        hits = self._degenerate(self._chain([[0, 0], [203, 0]]))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("head sits 3px past N's far outline", hits[0])

    def test_the_endpoint_check_now_reaches_that_overshoot(self):
        """The hole this arm was built for, recorded as closed (Task 54).

        This assertion was `assertEqual(..., [])` from WP4b until
        2026-08-16: the interior-run walk was gated on `not outside`, a
        head past the far border IS outside, and the 3px gap sat under
        the 14px tolerance, so between them a stroke drawn straight
        through an 80px box read as a clean binding. The floor Task 54
        put under that gate is what changed, and the class docstring's
        standing instruction was to say so here rather than keep the
        old rule.

        So the pair is asserted rather than the silence, and the
        magnitudes are what make it a pair and not a duplicate: the
        walk names the 78px of N that was crossed (80 less the walk's
        1px inset at each face), this arm names the 3px poking out the
        far side. One repair, read from both ends.
        """
        lint = self._lint(self._chain([[0, 0], [203, 0]]))
        self.assertEqual(
            len([m for m in lint if "runs 78px inside it" in m]), 1, lint)
        self.assertEqual(
            len([m for m in lint
                 if "head sits 3px past N's far outline" in m]), 1, lint)

    def test_past_the_floor_the_overshoot_arm_is_the_only_honest_voice(self):
        """The half of the hole that did NOT close, and the arm's keep.

        One px further out than the test above — 15px past N's far
        outline against a 14px tolerance — and the floor ends. The walk
        is gated off again and `endpoint_gap` takes the scene, which
        reports the arrow "ends 15px away" from a node it has just
        traversed end to end: the anti-correlated severity where deeper
        damage reads as a smaller number. This arm is then the only
        voice that says the whole node was crossed, which is why Task 54
        closing the near half did not make it a rule nothing needs.
        """
        lint = self._lint(self._chain([[0, 0], [215, 0]]))
        self.assertEqual(
            len([m for m in lint if "ends 15px away" in m]), 1, lint)
        self.assertFalse([m for m in lint if "inside it" in m], lint)
        self.assertEqual(
            len([m for m in lint
                 if "head sits 15px past N's far outline" in m]), 1, lint)

    def test_one_broken_arrow_reports_one_warning(self):
        """A 2-point arrow collapsed onto one pixel says one thing.

        The three point-list arms co-occur by construction here — the
        only pair is a duplicate, so the final segment is zero-length and
        the endpoints coincide — and the repair is one edit. The
        final-segment clause is suppressed when that pair is already
        reported as a duplicate, which is what keeps this at one clause
        rather than three.
        """
        hits = self._degenerate(
            self._chain([[0, 0], [0, 0]], bind=False))
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("repeats the point before", hits[0])
        self.assertNotIn("final segment", hits[0])

    def test_the_frozen_fixtures_have_no_degenerate_arrows(self):
        """The corpus pole: drawings that shipped are all well formed.

        A hygiene check is only useful if it is quiet on healthy input,
        and 24 committed artifacts are the largest healthy input we have.
        The day this fails, either the router has started emitting
        degenerate paths or the floor is too generous — both worth a
        loud test rather than a note.
        """
        root = Path(__file__).resolve().parent / "fixtures"
        seen = 0
        for path in sorted(root.rglob("*.excalidraw")):
            doc = json.loads(path.read_text())
            seen += 1
            self.assertEqual(
                self._degenerate(doc.get("elements") or []), [], path.name)
        self.assertGreaterEqual(seen, 20, "fixture corpus went missing")


class TestCrossesThroughRun(Base):
    """v0.8: the interior-run walk sees what the chord could not."""

    def _flow_with(self, arrow_pts, ax=0, ay=0):
        """One box and one server-routed arrow with given geometry."""
        els = [
            {"id": "box", "type": "rectangle", "x": 444, "y": 476,
             "width": 212, "height": 60,
             "customData": {"role": "node"}},
            {"id": "far", "type": "rectangle", "x": 400, "y": 80,
             "width": 160, "height": 60,
             "customData": {"role": "node"}},
            {"id": "a", "type": "arrow", "x": ax, "y": ay,
             "points": arrow_pts,
             "startBinding": {"elementId": "far", "focus": 0, "gap": 6},
             "endBinding": {"elementId": "box", "focus": 0, "gap": 6},
             "customData": {"role": "edge", "route": "server"}},
        ]
        return els

    def test_on_border_endpoint_with_interior_approach_fires(self):
        """Endpoint ON the edge, approach through the interior — the
        exact shape the first dagre seed produced (72px inside)."""
        els = self._flow_with(
            [[0, 0], [0, 122], [-44, 122]], ax=488, ay=384)
        lint = canvas.project_lint(
            self.project, els, registry=self.store.registry,
            artifact_type="flow", aid="t")
        # hand-built geometry reads as user-shaped, so the finding rides
        # the warning tier; the SHAPE detection is what's under test —
        # the routed-tier split has its own coverage
        hits = [e for e in lint["errors"] + lint["warnings"]
                if "runs 72px inside it" in e]
        self.assertEqual(len(hits), 1,
                         lint["errors"] + lint["warnings"])

    def test_perpendicular_border_entry_stays_quiet(self):
        """The silent half: a fan attach point entered from outside.

        Both wordings are excluded, not just the interior one: since
        v0.9 WP4 this check has a second sentence for a run along the
        border, and matching only "inside it" would let this control
        pass over the very finding the fan attach point must not draw.
        """
        els = self._flow_with([[0, 0], [0, 92]], ax=520, ay=384)
        lint = canvas.project_lint(
            self.project, els, registry=self.store.registry,
            artifact_type="flow", aid="t")
        self.assertFalse([e for e in lint["errors"] + lint["warnings"]
                          if "inside it" in e or "own border" in e],
                         lint["errors"] + lint["warnings"])

    def test_boundary_running_approach_is_named_not_excused(self):
        """The v0.8 silence, deliberately reversed by r5-5.

        v0.8 pinned this scene SILENT: the strictly-interior walk (1px in
        from the border) was chosen so that fanned attach points and
        approaches lying along the border both scored zero, and this test
        guarded the second half of that. Round 5 looked at the picture
        and called that half a blind spot, not a tolerance — a 60px run
        drawn flat along a box's own top edge merges into the outline and
        reads as a line through the box, whichever end of the arrow it
        belongs to (`r5-5`).

        So the scene is unchanged and the verdict is inverted. What is
        NOT reversed is the other half: `test_perpendicular_border_entry_
        stays_quiet` above still pins the fan attach points silent, and
        that is the pole this change had to leave alone.
        """
        els = self._flow_with(
            [[0, 0], [0, 92], [60, 92]], ax=444, ay=384)
        lint = canvas.project_lint(
            self.project, els, registry=self.store.registry,
            artifact_type="flow", aid="t")
        hits = [e for e in lint["errors"] + lint["warnings"]
                if "runs 60px along" in e and "own border" in e]
        self.assertEqual(len(hits), 1,
                         lint["errors"] + lint["warnings"])
        # and it does NOT claim the arrow went inside the box, which is
        # the thing an arrow drawn on a border never does
        self.assertFalse([e for e in lint["errors"] + lint["warnings"]
                          if "inside it" in e])


class TestInteriorRunGateFloor(Base):
    """v0.9 Task 54: the interior-run walk's gate gets a tolerance floor.

    The walk is gated on `not outside`, and the rectangle branch
    computed `outside` as a raw bbox distance with no floor under it, so
    ANY positive gap switched the walk off entirely. Moving a tail from
    ON a border to 1px PAST it therefore made the drawing strictly worse
    — the arrow now crosses the whole node — and took the lint from one
    error to silence, in errors, warnings and notes alike, all the way
    out to the tolerance where `endpoint_gap` finally speaks and reports
    the gap instead of the crossing (`tolerable_gap_hides_interior_run`).

    The floor is one line, and a gate has two edges, so both are here.
    """

    def _crossing(self, gap):
        """N and B, with an arrow crossing all of N from outside it.

        N is a RECTANGLE on purpose: the diamond/ellipse branch has had
        this floor since v0.9 WP4, so pinning the fix on one would pin a
        scene that already worked. Its 100x80 also keeps `endpoint_tol`
        at its 14px base rather than the height-derived value, which is
        the number both edges below are stated against.

        Args:
            gap: Pixels the tail sits OUTSIDE N's left border. At 0 it
                is exactly on the border, which is the pole the walk
                could always see.

        Returns:
            The three-element flow scene: nodes N and B, arrow e1.
        """
        return [
            {"id": "N", "type": "rectangle", "x": 200, "y": 100,
             "width": 100, "height": 80, "customData": {"role": "node"}},
            {"id": "B", "type": "rectangle", "x": 400, "y": 100,
             "width": 100, "height": 80, "customData": {"role": "node"}},
            {"id": "e1", "type": "arrow", "x": 200 - gap, "y": 140,
             "points": [[0, 0], [200 + gap, 0]],
             "startBinding": {"elementId": "N", "focus": 0, "gap": 0},
             "endBinding": {"elementId": "B", "focus": 0, "gap": 0},
             "customData": {"role": "edge"}},
        ]

    def _lint(self, els):
        """Run the layout lint over a flow scene.

        Args:
            els: The scene's elements.

        Returns:
            The concatenated errors and warnings.
        """
        out = canvas.lint_layout(els, artifact_type="flow")
        return out["errors"] + out["warnings"]

    def test_a_tail_within_tolerance_still_reports_the_interior_run(self):
        """The band the floor opened, and the magnitude is what pins it.

        Every gap from 1px to the 14px tolerance now reports the same
        98px — N's 100px width less the walk's 1px inset at each face —
        because the run is clipped to the node and does not care how far
        outside the tail began. A fix that measured from the tail rather
        than from the outline would shrink this number as the gap grew,
        which is the reading that would mean the floor was in the wrong
        place.
        """
        for gap in (1, 3, 7, 14):
            lint = self._lint(self._crossing(gap))
            hits = [m for m in lint if "runs 98px inside it" in m]
            self.assertEqual(len(hits), 1, (gap, lint))

    def test_the_floor_leaves_the_walks_other_consumers_unchanged(self):
        """THE COLLATERAL TEST: everything else the gate fix could move.

        The fix zeroes `outside` and `inside` under the tolerance, which
        is why these four and not others are the at-risk neighbours:

        1. `endpoint_gap`'s DETACHED arm ("ends Npx away"). It reads the
           two variables the floor overwrites, so a floor reaching one
           px too far, or a gate opened rather than floored, replaces a
           30px detachment with a crossing error and loses the 30. The
           scene is deliberately the mutant's own geometry with the one
           bit moved — a tail 30px out, still crossing all of N — so
           what separates the two verdicts is only the floor's height.
        2. `endpoint_gap`'s INSIDE arm ("ends Npx inside the shape"),
           which reads the other overwritten variable. A floor written
           against `outside` alone would leave it, one written against
           both without the sign would silence it.
        3. The ON-BORDER sentence (r5-5). It is the walk's other output
           behind the SAME gate, so it changes reachability with the
           fix; what must not change is what it says when it speaks —
           the 32px magnitude, and the WARNING tier it keeps even when
           the server owns the path, because the endpoint is legally
           attached and the complaint is legibility.
        4. The fanned attach point (r4-1), and it guards a STANDING
           SILENCE rather than the floor's height. This is the case the
           floor newly ADMITS to the walk — a perpendicular approach
           stopping short of the border was gated off before and is
           walked now — so what it holds is that the r4-1 class stays
           quiet across the whole gap band once it is being measured at
           all. It does NOT discriminate a floor opened into a door,
           which is the reading the first draft of this paragraph
           implied: the Task 54 review replayed the corpus under that
           mutation and got 0/50/20/10 findings, byte-identical, so no
           corpus attachment changes verdict either way. Part 4 speaks
           for the class it names and for nothing else; parts 1-3 are
           where the floor's height is actually pinned.
        """
        # 1. a real detachment keeps its own finding, not the walk's
        lint = self._lint(self._crossing(30))
        self.assertEqual(
            len([m for m in lint if "ends 30px away" in m]), 1, lint)
        self.assertFalse([m for m in lint if "inside it" in m], lint)

        # 2. and so does the inside-the-shape half of the same check
        deep = self._crossing(0)
        deep[2].update({"x": 480, "points": [[0, 0], [-260, 0]]})
        deep[2]["startBinding"] = {"elementId": "B", "focus": 0, "gap": 0}
        deep[2]["endBinding"] = {"elementId": "N", "focus": 0, "gap": 0}
        lint = self._lint(deep)
        self.assertEqual(
            len([m for m in lint
                 if "its end point ends 20px inside the shape" in m]),
            1, lint)

        # 3. the r5-5 on-border run: same words, same 32px, same tier
        src = {"id": "s", "type": "rectangle", "x": 0, "y": 0,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        dst = {"id": "d", "type": "rectangle", "x": 160, "y": 92,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        arrow = {"id": "a", "type": "arrow", "x": 160, "y": 32,
                 "points": [[0, 0], [0, 92]],
                 "startBinding": {"elementId": "s", "focus": 0, "gap": 6},
                 "endBinding": {"elementId": "d", "focus": 0, "gap": 6},
                 "customData": {"role": "edge"}}
        self.assertTrue(canvas.server_owns_geometry(arrow))
        out = canvas.lint_layout([src, dst, arrow], artifact_type="flow")
        self.assertEqual(
            len([m for m in out["warnings"]
                 if "runs 32px along s's own border" in m]), 1, out)
        self.assertFalse([m for m in out["errors"] if "own border" in m],
                         out["errors"])

        # 4. the perpendicular approach the floor newly admits to the
        #    walk scores zero across the whole band, at a fanned
        #    off-centre attach point rather than the edge midpoint
        for gap in (0, 3, 10, 14):
            els = [
                {"id": "N", "type": "rectangle", "x": 200, "y": 100,
                 "width": 100, "height": 80,
                 "customData": {"role": "node"}},
                {"id": "B", "type": "rectangle", "x": 200, "y": 300,
                 "width": 100, "height": 80,
                 "customData": {"role": "node"}},
                {"id": "e1", "type": "arrow", "x": 230, "y": 300,
                 "points": [[0, 0], [0, -(120 - gap)]],
                 "startBinding": {"elementId": "B", "focus": 0, "gap": 0},
                 "endBinding": {"elementId": "N", "focus": 0, "gap": 0},
                 "customData": {"role": "edge"}},
            ]
            self.assertFalse(
                [m for m in self._lint(els)
                 if "inside it" in m or "own border" in m], (gap, els))


class TestBorderCollinearExit(Base):
    """r5-5: an edge must not be drawn along the box it leaves.

    The round-5 picture: an arrow leaves its source's right-edge midpoint
    and, instead of stepping away, runs straight DOWN the border to the
    corner and beyond. Half the source's right edge is then arrow and box
    at once, and a reader sees a line passing through the box. Both
    halves are here — the router must not produce the path, and the lint
    must name it when hand-built geometry does.
    """

    def _pair(self, dst_y):
        """Two 160x64 boxes, the destination's left face flush with the
        source's right face and `dst_y` below it."""
        src = {"id": "s", "type": "rectangle", "x": 0, "y": 0,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        dst = {"id": "d", "type": "rectangle", "x": 160, "y": dst_y,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        return src, dst

    def _abs_points(self, arrow):
        """The arrow's points in scene coordinates."""
        return [(arrow["x"] + p[0], arrow["y"] + p[1])
                for p in arrow["points"]]

    def _scene(self, points, ax, ay):
        """One 160x64 box and a hand-shaped arrow leaving it."""
        src = {"id": "s", "type": "rectangle", "x": 0, "y": 0,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        far = {"id": "f", "type": "rectangle", "x": 400, "y": 300,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        arrow = {"id": "a", "type": "arrow", "x": ax, "y": ay,
                 "points": points,
                 "startBinding": {"elementId": "s", "focus": 0, "gap": 6},
                 "endBinding": {"elementId": "f", "focus": 0, "gap": 6},
                 "customData": {"role": "edge", "route": "server"}}
        return [src, far, arrow]

    def test_exit_along_its_own_border_is_named(self):
        """The r5-5 geometry: 32px of arrow drawn on the source's edge.

        The arrow starts at (160, 32) — the right edge's midpoint — and
        drops to (160, 124). The first 32px of that drop are the border
        itself, from the midpoint to the bottom-right corner. Every
        strictly-interior measure scores it zero, because no part of the
        run is ever a pixel inside the box.
        """
        els = self._scene([[0, 0], [0, 92], [240, 300]], ax=160, ay=32)
        lint = canvas.lint_layout(els, artifact_type="flow")
        hits = [e for e in lint["errors"] + lint["warnings"]
                if "runs 32px along" in e and "own border" in e]
        self.assertEqual(len(hits), 1,
                         lint["errors"] + lint["warnings"])

    def test_perpendicular_exit_stays_silent(self):
        """The other pole: the same arrow, stepping off the edge first.

        One variable moves — the exit runs AWAY from the border for 24px
        before turning down — and the finding goes. Without this the
        check could fire on every arrow bound to anything.
        """
        els = self._scene([[0, 0], [24, 0], [24, 92], [264, 300]],
                          ax=160, ay=32)
        lint = canvas.lint_layout(els, artifact_type="flow")
        self.assertFalse([e for e in lint["errors"] + lint["warnings"]
                          if "inside it" in e or "own border" in e],
                         lint["errors"] + lint["warnings"])

    def test_a_server_routed_border_exit_warns_and_does_not_error(self):
        """The tier itself, named — not inherited from a fixture.

        The two lint tests above build 3+ point paths, so
        `server_owns_geometry` reads them as the user's geometry and
        they exercise the user-shaped branch. The one judgement call in
        this change — an on-border run stays a WARNING even when the
        server owns the path, because the endpoint is legally attached
        and the complaint is legibility — was defended only by three
        fixtures' `test_no_lint_errors_anywhere` failing if it were
        flipped (task 19 review, F7). This asserts it directly.

        Two points and no `routed` mark, which `server_owns_geometry`
        reads as server-owned: the same geometry the old router emitted
        for a flush pair, which is where these came from.
        """
        src = {"id": "s", "type": "rectangle", "x": 0, "y": 0,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        dst = {"id": "d", "type": "rectangle", "x": 160, "y": 92,
               "width": 160, "height": 64, "customData": {"role": "node"}}
        arrow = {"id": "a", "type": "arrow", "x": 160, "y": 32,
                 "points": [[0, 0], [0, 92]],
                 "startBinding": {"elementId": "s", "focus": 0, "gap": 6},
                 "endBinding": {"elementId": "d", "focus": 0, "gap": 6},
                 "customData": {"role": "edge"}}
        self.assertTrue(canvas.server_owns_geometry(arrow))
        lint = canvas.lint_layout([src, dst, arrow], artifact_type="flow")
        named = [e for e in lint["warnings"] if "own border" in e]
        self.assertTrue(named, lint["errors"] + lint["warnings"])
        self.assertFalse([e for e in lint["errors"] if "own border" in e],
                         lint["errors"])
        # not the user-shaped wording either — that prefix would mean the
        # tier was reached for the wrong reason
        self.assertFalse([e for e in named if e.startswith("user-shaped")])

    def test_router_will_not_exit_along_the_source_border(self):
        """The producing half: this pair used to route flat down the edge.

        `_route_candidates` offers a Z-detour whose middle leg slides
        past an obstacle at `(hx + ex) / 2`; when the destination's left
        face is flush with the source's right face those two are the same
        x, the detour collapses to a straight two-point drop, and being
        the shortest and least bent candidate it wins every time.
        """
        src, dst = self._pair(92)
        arrow = {"id": "a", "type": "arrow"}
        canvas.route_arrow(arrow, src, dst)
        pts = self._abs_points(arrow)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            for box in (src, dst):
                bx2 = box["x"] + box["width"]
                by2 = box["y"] + box["height"]
                flat = (abs(x1 - x2) < 0.5 and
                        min(abs(x1 - box["x"]), abs(x1 - bx2)) < 0.5 and
                        min(max(y1, y2), by2) - max(min(y1, y2),
                                                    box["y"]) > 8)
                self.assertFalse(
                    flat, "segment %r runs along %s's border in %r"
                          % (((x1, y1), (x2, y2)), box["id"], pts))

    def test_router_still_takes_the_short_path_when_it_is_clean(self):
        """The silent half: an ordinary offset pair keeps its L-elbow.

        The border filter drops candidates; a filter that dropped too
        many would push every pair onto a longer detour and this is what
        would notice.
        """
        src = {"id": "s", "type": "rectangle", "x": 0, "y": 0,
               "width": 160, "height": 60}
        dst = {"id": "d", "type": "rectangle", "x": 400, "y": 300,
               "width": 160, "height": 60}
        arrow = {"id": "a", "type": "arrow"}
        canvas.route_arrow(arrow, src, dst)
        self.assertEqual(len(arrow["points"]), 3)


def _box_anchor(el, other_cx, other_cy):
    """`edge_anchor`'s pre-WP4 closed form, kept as an independent check.

    Re-derived here rather than imported so the byte-identical claim for
    box-filling shapes is tested against a second implementation and not
    against the branch under test.

    Args:
        el: The element to anchor on.
        other_cx: The other element's centre x.
        other_cy: The other element's centre y.

    Returns:
        `(x, y)` on the bounding box's edge.
    """
    cx = el["x"] + el.get("width", 0) / 2.0
    cy = el["y"] + el.get("height", 0) / 2.0
    dx, dy = other_cx - cx, other_cy - cy
    hw = max(el.get("width", 0) / 2.0, 1)
    hh = max(el.get("height", 0) / 2.0, 1)
    scale = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return (cx + dx * scale, cy + dy * scale)


# The two shapes the WP4 clip exists for, at the sizes that made the
# defects: the 200x100 rhombus of the endpoint mutants, and the 240x64
# pill from the tearsheet fixture whose tail grazes its own tangent.
_WP4_DIA = {"id": "d", "type": "diamond", "x": 300, "y": 300,
            "width": 200, "height": 100, "customData": {"role": "node"}}
_WP4_ELL = {"id": "e", "type": "ellipse", "x": 60, "y": 280,
            "width": 240, "height": 64, "customData": {"role": "node"}}


class TestInscribedShapeClip(Base):
    """v0.9 WP4: the primitive that measures the drawn shape, not its box.

    The endpoint mutants (`diamond_corner_silence` and siblings) carry
    the lint's side of this. What lives here is everything they do NOT
    reach: the clip's own arithmetic, the clearance gate that keeps a
    grazing approach axis from inventing a gap, and the router anchor
    that has to agree with the lint or the tool condemns its own output.
    """

    def test_clip_reads_the_rhombus_not_the_box(self):
        """At y=305 the 200x100 rhombus spans x 390..410, not 300..500."""
        t0, t1 = canvas.shape_clip(_WP4_DIA, 310, 305, 1, 0)
        self.assertAlmostEqual(310 + t0, 390)
        self.assertAlmostEqual(310 + t1, 410)

    def test_norm_is_one_exactly_on_each_outline(self):
        """The shared containment contract: 1.0 IS the drawn edge."""
        # rhombus facet midpoint, ellipse's rightmost point, box corner
        self.assertAlmostEqual(canvas.shape_norm(_WP4_DIA, 350, 325), 1.0)
        self.assertAlmostEqual(canvas.shape_norm(_WP4_ELL, 300, 312), 1.0)
        self.assertAlmostEqual(
            canvas.shape_norm(dict(_WP4_DIA, type="rectangle"), 500, 400),
            1.0)
        # and the box's corner is OUTSIDE the two inscribed shapes
        self.assertGreater(canvas.shape_norm(_WP4_DIA, 500, 400), 1.0)
        self.assertGreater(canvas.shape_norm(_WP4_ELL, 300, 344), 1.0)
        self.assertIsNone(canvas.shape_norm(_WP4_DIA, 400, 350, inset=60))

    def test_clip_of_a_rectangle_is_its_box(self):
        """A rectangle fills its box, so the clip must not move its edges."""
        rect = dict(_WP4_DIA, type="rectangle")
        t0, t1 = canvas.shape_clip(rect, 310, 305, 1, 0)
        self.assertAlmostEqual(310 + t0, 300)
        self.assertAlmostEqual(310 + t1, 500)

    def test_clearance_is_exact_on_a_facet_and_zero_on_the_outline(self):
        """The rhombus's facets are planes, so first order is the answer."""
        # (350,325) is the top-left facet's midpoint: 0.5 + 0.5 == 1
        self.assertAlmostEqual(canvas.shape_clearance(_WP4_DIA, 350, 325), 0)
        # (310,305) is 0.8 outline-units out, over a gradient of 0.02236
        self.assertAlmostEqual(canvas.shape_clearance(_WP4_DIA, 310, 305),
                               35.777, places=3)
        self.assertLess(canvas.shape_clearance(_WP4_DIA, 400, 350), 0)

    def _tail_at(self, node, tail, head):
        """One arrow whose START binds `node`, tail then head absolute."""
        return [dict(node),
                {"id": "far", "type": "rectangle", "x": 500, "y": 140,
                 "width": 80, "height": 50, "customData": {"role": "node"}},
                {"id": "a", "type": "arrow", "x": tail[0], "y": tail[1],
                 "points": [[0, 0], [head[0] - tail[0], head[1] - tail[1]]],
                 "startBinding": {"elementId": node["id"], "focus": 0,
                                  "gap": 6},
                 "customData": {"role": "edge"}}]

    def test_a_grazing_approach_axis_does_not_invent_a_gap(self):
        """8px off an ellipse's shoulder is 8px, not the 84px the axis says.

        The tearsheet fixture's shape, and the over-fire this check came
        within one commit of shipping: the tail sits on the box's top
        edge, so the horizontal approach axis is the TANGENT at the
        ellipse's top and clips 84px away — while the outline is 8px
        below. Clearance is what decides; the axis only reports.
        """
        els = self._tail_at(_WP4_ELL, (264, 280), (516, 184))
        self.assertLess(canvas.shape_clearance(_WP4_ELL, 264, 280), 14)
        self.assertGreater(canvas.shape_clip(_WP4_ELL, 264, 280, -1, 0)[0],
                           80)
        lint = canvas.lint_layout(els)
        self.assertFalse([m for m in lint["errors"] + lint["warnings"]
                          if "claims to bind" in m],
                         lint["errors"] + lint["warnings"])

    def test_the_same_tail_pulled_clear_of_the_shape_does_fire(self):
        """The live pole: 40px out on the same ellipse is still reported."""
        els = self._tail_at(_WP4_ELL, (264, 240), (516, 184))
        self.assertGreater(canvas.shape_clearance(_WP4_ELL, 264, 240), 14)
        lint = canvas.lint_layout(els)
        self.assertTrue([m for m in lint["errors"] + lint["warnings"]
                         if "claims to bind" in m])

    def test_tolerance_scales_with_the_node_but_never_tightens(self):
        """14px is 20% of a 60px pill and 5% of a 240px diamond."""
        pill = {"type": "ellipse", "width": 200, "height": 60}
        big = {"type": "diamond", "width": 240, "height": 240}
        self.assertEqual(canvas.endpoint_tol(pill, 14), 14)
        self.assertEqual(canvas.endpoint_tol(big, 14), 24)

    def test_router_anchors_on_the_outline_so_the_lint_stays_quiet(self):
        """The anchor and the lint have to agree, or the tool self-condemns."""
        for node in (_WP4_DIA, _WP4_ELL):
            with self.subTest(shape=node["type"]):
                ax, ay = canvas.edge_anchor(node, 900, 900)
                self.assertAlmostEqual(
                    canvas.shape_clearance(node, ax, ay), 0, places=6)

    def test_router_anchor_on_a_rectangle_is_byte_identical(self):
        """A box-filling shape must not drift through the new path."""
        rect = dict(_WP4_DIA, type="rectangle")
        for other in ((900, 900), (-40, 320), (400, -10), (401, 351)):
            with self.subTest(other=other):
                self.assertEqual(canvas.edge_anchor(rect, *other),
                                 _box_anchor(rect, *other))

    def test_crossing_test_reads_the_outline_not_the_box(self):
        """The corner void is canvas, and the body is still the body.

        Both poles, because a predicate that stopped firing entirely
        would pass the silence half on its own.
        """
        # (302,309)->(312,302) threads the diamond's empty top-left
        # corner; y=350 is its own centerline.
        self.assertFalse(canvas._seg_hits_rect(302, 309, 312, 302, _WP4_DIA))
        self.assertTrue(canvas._seg_hits_rect(210, 350, 590, 350, _WP4_DIA))
        # the ellipse's corner void and its horizontal diameter
        self.assertFalse(canvas._seg_hits_rect(62, 282, 82, 282, _WP4_ELL))
        self.assertTrue(canvas._seg_hits_rect(40, 312, 320, 312, _WP4_ELL))

    def test_crossing_test_on_a_rectangle_still_reads_its_box(self):
        """A rectangle's box IS its outline, so both corners stay hits."""
        rect = dict(_WP4_DIA, type="rectangle")
        self.assertTrue(canvas._seg_hits_rect(302, 309, 312, 302, rect))
        self.assertTrue(canvas._seg_hits_rect(210, 350, 590, 350, rect))

    def test_crossing_test_stops_at_the_segment_ends(self):
        """The clip is over an infinite line; only [0,1] of it is drawn.

        Without the interval check a segment stopping 200px short of a
        node would read as passing through it, which is the one way a
        clip-based predicate can be WIDER than the box test it replaced.
        """
        self.assertFalse(canvas._seg_hits_rect(0, 350, 100, 350, _WP4_DIA))
        self.assertFalse(canvas._seg_hits_rect(700, 350, 800, 350, _WP4_DIA))

    def test_a_degenerate_segment_is_a_point(self):
        """Two coincident path points have no direction to clip along."""
        self.assertTrue(canvas._seg_hits_rect(400, 350, 400, 350, _WP4_DIA))
        self.assertFalse(canvas._seg_hits_rect(310, 305, 310, 305, _WP4_DIA))


class TestShapeAwareLabelRoom(Base):
    """v0.9 WP4 (task 17): the label half of the same primitive.

    `diamond_label_overflows_shape` carries the lint's side. What lives
    here is the arithmetic that mutant only sees one point of, and the
    two FITTER properties it cannot see at all — the mutant's scene is
    built by hand and never calls `fit_label_in`.
    """

    def test_band_width_reproduces_the_catalogue_derivations(self):
        """The four numbers the catalogue derived by hand, off the clip.

        These are quoted in `diamond_label_overflows_shape`'s entry as
        the reason the ellipse is the weaker case and needs no mutant of
        its own. If the helper and the prose ever disagree, the prose is
        what the next reader will trust, so pin the agreement.
        """
        dia = {"type": "diamond", "x": 0, "y": 0, "width": 200,
               "height": 100}
        # a 20px label centred in a 200x100 rhombus: 160px, not 200px
        self.assertAlmostEqual(canvas.shape_band_width(dia, 40, 60), 160.0)
        # ...and 200px at the centre line, which is what made it invisible
        self.assertAlmostEqual(canvas.shape_band_width(dia, 50, 50), 200.0)
        ell = dict(dia, type="ellipse")
        self.assertAlmostEqual(canvas.shape_band_width(ell, 40, 60),
                               195.959, places=3)
        self.assertAlmostEqual(
            canvas.shape_band_width(dict(ell, height=60), 20, 40),
            188.562, places=3)
        # a rectangle IS its box, at every band
        self.assertAlmostEqual(
            canvas.shape_band_width(dict(dia, type="rectangle"), 40, 60),
            200.0)

    def test_fitter_leaves_a_label_no_wrap_can_improve(self):
        """On a 240x80 diamond the label that fits unwrapped is untouched.

        The rule this pins is BEST candidate, not LAST. Wrapping buys
        width by spending height, and height is what a rhombus charges
        for: here the 171px label has a 180px chord at its own 20px band
        and fits, but every wrap available to it is taller and therefore
        narrower-roomed, and a walk that took its fixpoint would wrap
        this to 60px and hang it 30px outside a shape it was already
        inside.
        """
        cont = {"id": "n1", "type": "diamond", "x": 0, "y": 0,
                "width": 240, "height": 80}
        lbl = {"id": "t1", "type": "text", "text": "Send for second review",
               "originalText": "Send for second review", "fontSize": 16,
               "width": 171, "height": 20, "containerId": "n1"}
        canvas.fit_label_in(cont, lbl)
        self.assertEqual((lbl["width"], lbl["height"]), (171, 20))
        self.assertEqual(cont["height"], 80, "nothing to grow for")
        self.assertNotIn("autoResize", lbl,
                         "a label left alone must not be pinned fixed-width")

    def test_box_containers_keep_the_budget_they_always_had(self):
        """A rectangle still gets `width - 24` and wraps exactly as before.

        The shape term must be inert where the bbox IS the shape, or WP4
        would have re-laid every rectangle label in every artifact. The
        fixture replay says it did not; this says why.
        """
        text = "Escalate to the regional compliance desk for manual review"
        cont = {"id": "n1", "type": "rectangle", "x": 0, "y": 0,
                "width": 160, "height": 60}
        lbl = {"id": "t1", "type": "text", "text": text,
               "originalText": text, "fontSize": 16,
               "width": canvas.text_dims(text, 16)[0], "height": 20,
               "containerId": "n1"}
        canvas.fit_label_in(cont, lbl)
        self.assertEqual(lbl["width"], 136)          # 160 - 24
        self.assertEqual(lbl["height"],
                         canvas.text_dims(
                             canvas.wrap_label_text(text, 136, 16), 16)[1])
        self.assertIs(lbl["autoResize"], False)
        self.assertEqual(cont["height"], lbl["height"] + 16)
        self.assertAlmostEqual(canvas.label_budget(cont, lbl["height"]), 136)

    def test_the_walk_never_steps_over_the_box_rule(self):
        """The budget `width - 24` would have chosen is always a candidate.

        On a narrow rhombus the first shape-aware step lands on the 60px
        floor, skipping a budget between the two ends that beats both:
        a 90x100 diamond wrapping this label to the box rule's 66px
        overhangs by 30px, and to the floor's 60px by 42px, because the
        narrower wrap costs a fourth line and a rhombus charges for
        height. Keeping `box` in the candidate set is what makes "never
        worse than the rule it replaces" true by construction.
        """
        text = "Send for second review"
        cont = {"id": "n1", "type": "diamond", "x": 0, "y": 0,
                "width": 90, "height": 100}
        lbl = {"id": "t1", "type": "text", "text": text,
               "originalText": text, "fontSize": 16,
               "width": canvas.text_dims(text, 16)[0], "height": 20,
               "containerId": "n1"}
        canvas.fit_label_in(cont, lbl)
        self.assertEqual((lbl["width"], lbl["height"]), (66, 60))  # 90 - 24
        self.assertAlmostEqual(
            lbl["width"] - canvas.label_room(cont, lbl["height"]), 30.0)

    def test_an_integer_wide_box_gives_an_integer_room(self):
        """Float dust off the clip must not reach a stored label width.

        The clip divides by a half-width and multiplies it back, so 72
        of the 781 integer widths in 20..800 came back inexact — 210
        gave 209.99999999999997, which `fit_label_in` stored as a label
        width of 185.99999999999997 where it had stored 186. That is a
        float in saved JSON and a diff out of nothing (v0.9 WP4 review,
        F2). Only dust is snapped; a genuinely fractional chord is not.
        """
        inexact = [w for w in range(20, 801)
                   if canvas.label_room({"type": "rectangle", "x": 0, "y": 0,
                                         "width": w, "height": 60}, 20) != w]
        self.assertEqual(inexact, [])
        text = "Escalate to the regional compliance desk for manual review"
        cont = {"id": "n1", "type": "rectangle", "x": 0, "y": 0,
                "width": 210, "height": 60}
        lbl = {"id": "t1", "type": "text", "text": text,
               "originalText": text, "fontSize": 16,
               "width": canvas.text_dims(text, 16)[0], "height": 20,
               "containerId": "n1"}
        canvas.fit_label_in(cont, lbl)
        self.assertEqual(lbl["width"], 186)          # 210 - 24, exactly
        self.assertNotIsInstance(lbl["width"], float,
                                 "186.0 and 186 are different bytes in "
                                 "saved JSON even though they fingerprint "
                                 "the same")
        # the ellipse's real chord is fractional and must stay that way
        self.assertAlmostEqual(
            canvas.shape_band_width({"type": "ellipse", "x": 0, "y": 0,
                                     "width": 200, "height": 100}, 40, 60),
            195.959, places=3)

    def _overhangs(self, els):
        """The scene's `label_overflows_shape` warnings.

        Args:
            els: The scene's element list.

        Returns:
            The matching warning lines.
        """
        lint = canvas.lint_layout(els, artifact_type="flow")
        return [w for w in lint["warnings"] if "overhangs" in w]

    def _bound(self, shape, cw, ch, text, **lbl):
        """One node of `shape` carrying a bound label, centred.

        Args:
            shape: The container's element type.
            cw: Container width.
            ch: Container height.
            text: The label's text.
            **lbl: Overrides merged into the label element.

        Returns:
            The two-element scene, node then label.
        """
        w, h = canvas.text_dims(text, 16)
        w, h = lbl.pop("width", w), lbl.pop("height", h)
        label = {"id": "t1", "type": "text", "x": (cw - w) / 2,
                 "y": (ch - h) / 2, "width": w, "height": h, "text": text,
                 "originalText": text, "fontSize": 16, "textAlign": "center",
                 "verticalAlign": "middle", "containerId": "n1"}
        label.update(lbl)
        return [{"id": "n1", "type": shape, "x": 0, "y": 0, "width": cw,
                 "height": ch, "customData": {"role": "node"},
                 "boundElements": [{"id": "t1", "type": "text"}]}, label]

    def test_the_check_reads_ink_not_the_wrapping_frame(self):
        """A fitted label's stored box is a frame, and frames are not ink.

        `autoResize: False` means the fitter chose that width for the
        CLIENT to wrap inside and left the text unwrapped; the client
        then centres the wrapped lines in it. Measuring the frame as the
        drawn width condemned argus-r4-arm4's `to-compose-label` — a
        136px frame holding 85px of text that sits 20px clear of the
        ellipse on both sides — for hanging 11px over empty canvas that
        has no text on it (v0.9 WP4 review, F1).
        """
        # the frame is wider than the ellipse's chord; the ink is not
        els = self._bound("ellipse", 160, 64, "→ Review & publish",
                          width=136, height=40, autoResize=False)
        self.assertEqual(canvas.text_dims(
            canvas.wrap_label_text("→ Review & publish", 136, 16), 16)[0], 85)
        self.assertEqual(self._overhangs(els), [])
        # and the check still bites on that branch when the INK is wide:
        # same frame, same shape, text that does not wrap small
        els = self._bound("ellipse", 160, 64, "Unsplittable" * 2,
                          width=136, height=20, autoResize=False)
        self.assertEqual(len(self._overhangs(els)), 1)

    def test_the_marker_inset_gate_is_what_silences_a_rectangle(self):
        """Pin the GATE, not the geometry that would silence it anyway.

        A label narrower than its box is silent whether or not the gate
        exists, so the obvious control proves nothing. This label is one
        unsplittable word WIDER than its rectangle, which is the case
        where both checks have something to say: `text_overflow` fires
        (no wrap can rescue a single word), and the geometry the shape
        check runs on would fire too. Only `marker_inset` returning 0
        for a box keeps the second one quiet. Delete the gate and this
        goes red — as it should, because that is the same pixels
        reported twice under two names.
        """
        text = "Unsplittable" * 3
        els = self._bound("rectangle", 200, 100, text)
        ink = canvas.text_dims(text, 16)[0]
        self.assertGreater(ink, 200, "the label must overhang the BOX")
        # what the check would compute if it ever reached this scene
        self.assertEqual(canvas.marker_inset("rectangle"), 0.0)
        self.assertGreaterEqual(
            ink - canvas.shape_band_width(els[0], els[1]["y"],
                                          els[1]["y"] + els[1]["height"]), 1,
            "the gate must be the ONLY thing keeping this quiet")
        self.assertEqual(self._overhangs(els), [])
        # and `text_overflow`, which owns the box case, does report it
        lint = canvas.lint_layout(els, artifact_type="flow")
        self.assertTrue([w for w in lint["warnings"] if "does not fit" in w])
        # the same label on a diamond IS this check's business
        els[0]["type"] = "diamond"
        self.assertEqual(len(self._overhangs(els)), 1)


class TestDeletionConsequenceSurface(Base):
    """v0.8 beats, beat 3: the facts existed on disk; no agent-facing
    surface named them, and lint was blind to half-unbound arrows."""

    def _delete_hub(self):
        """Seed a 3-arrow hub and delete it; returns (record, scene)."""
        self.store.apply_batch(seed_flow_batch())
        record, _ = self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "del", "id": "checkout"}]})
        return record, self.store.scenes["checkout-flow"]

    def test_consequence_lines_name_the_fallout(self):
        record, _ = self._delete_hub()
        lines = canvas.consequence_lines(record)
        self.assertTrue(lines, "deletion produced no consequence lines")
        joined = "\n".join(lines)
        self.assertIn("t1", joined)     # cart→checkout lost its end
        self.assertIn("t2", joined)     # checkout→payment lost its start
        # and the silent half: an ordinary add has no consequences
        rec2, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow",
            "ops": [{"op": "add", "element": {
                "id": "aside", "type": "rectangle", "x": 900, "y": 40,
                "width": 160, "height": 60, "label": "Aside",
                "role": "node"}}]})
        self.assertEqual(canvas.consequence_lines(rec2), [])

    def test_half_unbound_arrow_draws_the_warning(self):
        _, els = self._delete_hub()
        lint = canvas.project_lint(
            self.project, els, registry=self.store.registry,
            artifact_type="flow", aid="checkout-flow")
        halves = [w for w in lint["warnings"]
                  if "lost its" in w and "endpoint" in w]
        # t1 lost its end, t2 its start (t1/t2 are unlabeled and this is
        # a flow view — the warning keys on the half-bound state, so it
        # must fire regardless of label)
        self.assertGreaterEqual(len(halves), 2, lint["warnings"])

    def test_fully_bound_and_sketch_arrows_stay_quiet(self):
        self.store.apply_batch(seed_flow_batch())
        els = self.scene()
        # a decoration-free unlabeled arrow with BOTH ends unbound on a
        # flow view is sketch furniture — still quiet (unchanged rule)
        els.append({"id": "sketch", "type": "arrow", "x": 900, "y": 400,
                    "points": [[0, 0], [80, 0]], "startBinding": None,
                    "endBinding": None, "customData": {"role": "edge"}})
        lint = canvas.project_lint(
            self.project, els, registry=self.store.registry,
            artifact_type="flow", aid="checkout-flow")
        self.assertFalse([w for w in lint["warnings"]
                          if "lost its" in w or "binds nothing" in w],
                         lint["warnings"])

    def test_self_loop_exempt_from_shared_attach_warning(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "add",
                     "element": {"id": "loop", "type": "arrow",
                                 "label": "retry"},
                     "from": "checkout", "to": "checkout"}]})
        els = self.scene()
        lint = canvas.project_lint(
            self.project, els, registry=self.store.registry,
            artifact_type="flow", aid="checkout-flow")
        self.assertFalse(
            [w for w in lint["warnings"]
             if "share an attach point" in w and "loop" in w],
            lint["warnings"])


class TestBeatsBFixes(Base):
    """v0.8 beats B: stale toggle-pill width, set_round stacking, the
    stall ratchet, note-id collisions, duplicate ids at the write path."""

    def _toggle_scene(self):
        self.store.apply_batch({
            "base_revn": 0,
            "create": {"id": "w", "type": "wireframe", "concept": "c",
                       "name": "W"},
            "ops": [{"op": "add", "element": {
                "id": "tg", "type": "rectangle", "x": 40, "y": 40,
                "width": 220, "height": 28, "label": "Macro",
                "kind": "toggle", "checked": False}}]})

    def test_stale_pill_width_heals_on_reconcile(self):
        self._toggle_scene()
        els = self.store.scenes["w"]
        box = next(e for e in els
                   if (e.get("customData") or {}).get("box_of") == "tg")
        box["width"] = 28    # a pre-v0.8 artifact under the old constant
        self.store.commit(author="agent", new_scenes={"w": els},
                          base_revn=1)
        self.store.apply_batch({
            "base_revn": 2, "artifact": "w",
            "ops": [{"op": "mod", "id": "tg",
                     "attrs": {"checked": True}}]})
        els = self.store.scenes["w"]
        box = next(e for e in els
                   if (e.get("customData") or {}).get("box_of") == "tg")
        thumb = next(e for e in els
                     if (e.get("customData") or {})
                     .get("thumb_of") == "tg")
        self.assertEqual(box["width"], canvas.TOGGLE_PILL_W)
        # the on-position thumb stays ON its track
        self.assertLessEqual(thumb["x"] + thumb["width"],
                             box["x"] + box["width"])

    def test_set_round_lands_exactly_after_a_user_turn(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": self.scene()},
                          base_revn=1)
        start = self.store.registry.get("round", 0)
        rec, _ = self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow",
            "ops": [
                {"op": "add", "element": {
                    "id": "n1", "type": "rectangle", "x": 900, "y": 40,
                    "width": 160, "height": 60, "label": "N1",
                    "role": "node"}},
                {"op": "registry", "action": "set_round",
                 "round": start + 3}]})
        # asked for start+3, got start+3 — the auto-bump used to stack
        # +1 exactly on the first agent commit after a user turn
        self.assertEqual(self.store.registry["round"], start + 3)
        self.assertEqual(rec["round"], start + 3)
        # and set_round now appears in the registry narration
        self.assertTrue(any(c.get("action") == "set_round"
                            for c in rec["registry_changes"]))

    def test_auto_bump_survives_without_set_round(self):
        """The silent half: an ordinary agent move still opens a round."""
        self.store.apply_batch(seed_flow_batch())
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": self.scene()},
                          base_revn=1)
        start = self.store.registry.get("round", 0)
        self.store.apply_batch({
            "base_revn": 2, "artifact": "checkout-flow",
            "ops": [{"op": "add", "element": {
                "id": "n2", "type": "rectangle", "x": 900, "y": 140,
                "width": 160, "height": 60, "label": "N2",
                "role": "node"}}]})
        self.assertEqual(self.store.registry["round"], start + 1)

    def test_round_stall_clears_when_advice_is_taken(self):
        self.store.apply_batch(seed_flow_batch())
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow",
            "ops": [{"op": "pin", "target": "cart",
                     "question": "still open?"}]})
        for i in range(3):
            self.store.apply_batch({
                "base_revn": 2 + i, "artifact": "checkout-flow",
                "ops": [{"op": "mod", "id": "cart",
                         "attrs": {"x": 100 + 4 * i}}]})
        self.assertIsNotNone(self.store.round_stall())
        rnd = self.store.registry.get("round", 0)
        self.store.apply_batch({
            "base_revn": 5, "artifact": "checkout-flow",
            "ops": [{"op": "registry", "action": "set_round",
                     "round": rnd + 1}]})
        # taking the nag's advice CLEARS it — it used to survive and
        # ratchet the round on every subsequent apply
        self.assertIsNone(self.store.round_stall())

    def test_note_ids_never_collide(self):
        first = canvas._x_user_note("same text", 0, 0)
        ids = {e["id"] for e in first}
        second = canvas._x_user_note("same text", 40, 40, existing=ids)
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        self.assertTrue(second[0]["id"].startswith("usernote-"))

    def test_duplicate_ids_dropped_at_the_write_moment(self):
        self.store.apply_batch(seed_flow_batch())
        els = self.scene()
        els.append(dict(els[0]))    # a byte-identical duplicate id
        self.store.commit(author="user",
                          new_scenes={"checkout-flow": els},
                          base_revn=1)
        ids = [e["id"] for e in self.store.scenes["checkout-flow"]]
        self.assertEqual(len(ids), len(set(ids)))
        # and a fresh load repairs nothing — the write healed it
        store2 = canvas.Store(self.project)
        ids2 = [e["id"] for e in store2.scenes["checkout-flow"]]
        self.assertEqual(len(ids2), len(set(ids2)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
