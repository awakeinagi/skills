"""Backend unit tests for canvas.py — differ, facts, DAG, durability.

Run: python3 -m pytest tests/ -q   (or python3 tests/test_backend.py)
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas  # noqa: E402


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

    def test_validate_png(self):
        ok, why = canvas.validate_png(b"definitely not a png")
        self.assertFalse(ok)
        # minimal structurally-valid PNG header (10x10) with enough body
        import struct
        ihdr = struct.pack(">II5B", 10, 10, 8, 2, 0, 0, 0)
        png = (b"\x89PNG\r\n\x1a\n" +
               struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0\0\0\0" +
               b"\0" * 64)
        ok, why = canvas.validate_png(png)
        self.assertTrue(ok, why)
        ok, why = canvas.validate_png(png, want_w=2000)
        self.assertFalse(ok)  # dimension mismatch is the strong signal


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

    def test_text_dims_counts_wide_characters_as_two_cells(self):
        narrow, _ = canvas.text_dims("ab", 16)
        wide, _ = canvas.text_dims("報告", 16)
        self.assertEqual(wide, narrow * 2)

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
        els = self.store.state_at(10)["tearsheet-pipeline"]["elements"]
        lint = canvas.lint_layout(els)
        detach = [m for m in lint["errors"] if "claims to bind" in m]
        self.assertEqual(len(detach), 22)
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

    def test_axis_aligned_hand_points_render_sharp(self):
        self.store.apply_batch({
            "base_revn": 1, "artifact": "checkout-flow", "ops": [
                {"op": "mod", "id": "t1",
                 "attrs": {"points": [[0, 0], [60, 0], [60, 80],
                                      [120, 80]]}}]})
        t1 = next(e for e in self.store.scenes["checkout-flow"]
                  if e["id"] == "t1")
        self.assertIsNone(t1.get("roundness"))


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
        # 1 — the ingest fan routes clean (was: diagonals through boxes)
        lint = canvas.lint_layout(s.scenes["pipeline-flow"])
        self.assertFalse([w for w in lint["warnings"]
                          if "passes through" in w or "diagonally" in w],
                         lint["warnings"])
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
        self.assertEqual(ix["note-1"]["strokeColor"], "#5c8a5f")
        rec = self.store.records[1]
        anno = next(f for f in
                    rec["artifacts"]["admin-wireframe"]["facts"]
                    if f["fact"] == "annotated")
        self.assertEqual(anno["author"], "agent")
        self.assertIn("my note", canvas.headline_for(anno))


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
        self.assertFalse(crossed)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
