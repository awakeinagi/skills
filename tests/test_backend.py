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


if __name__ == "__main__":
    unittest.main(verbosity=2)
