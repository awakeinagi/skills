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


if __name__ == "__main__":
    unittest.main(verbosity=2)
