"""Delete each pin guard individually; assert its named test goes red.

A guard whose absence nothing notices is not guarded — it is a line of
code that happens to be correct today. This is the instrument for that
claim: for each guard site it patches the guard out of `canvas.py`, runs
only the test that claims to prove it, restores the file, and reports
KILLED or SURVIVED.

WHY IT EXISTS. The first round of the pin work shipped 35 tests described
as "both directions on every guard". A verifier then mutated each guard
individually and found SEVEN of eleven could be deleted outright with the
whole suite green: the lines were live — flipping them to skip-everything
broke 3, 22, 14, 2 and 5 tests — but nothing observed their PRESENCE. Two
tests were passing for the wrong reason: one asserted a path was
unchanged in a scene the router had no intention of touching, and one
named the fan while exercising the router.

NOT PART OF THE SUITE, deliberately. It rewrites `canvas.py` in place and
restores it, which is acceptable for a hand-run instrument and wrong for
anything `unittest discover` may run beside other work. Run it whenever a
guard is added, moved or re-worded::

    python3 tests/guard_mutants.py            # every site
    python3 tests/guard_mutants.py relayout   # one, by label substring

Exit status is non-zero if any guard survived its own test.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "skills" / "wysiwyg-grilling" / "scripts" / "canvas.py"

_SKIP_ARROW = (
    "        if pinned_to_canvas(a):\n"
    "            skipped += 1\n"
    "            continue\n")
_SKIP_TIDY = (
    "                if pinned_to_canvas(e):\n"
    '                    pinned.add(e["id"])\n'
    "                    continue\n")
_FINAL_ROUTE = (
    '            if e.get("type") != "arrow" '
    "or not server_owns_geometry(e) \\\n"
    "                    or pinned_to_canvas(e):")
_RESOLVE = (
    '            if el is not None and role_of(el) == "pin" and \\\n'
    "                    not pinned_to_canvas(el):")
_ART011 = (
    '            before = (el.get("x"), el.get("y"))\n'
    "            recenter_label(kept, cont)")
_ART011_OLD = (
    '            before = (el.get("x"), el.get("y"))\n'
    '            el["x"] = cont["x"] + max(\n'
    '                (cont.get("width", 0) - el.get("width", 0)) / 2, 4)\n'
    '            el["y"] = cont["y"] + max(\n'
    '                (cont.get("height", 0) - el.get("height", 0)) / 2, 4)')

# (label, snippet, replacement, nth occurrence, test that must die)
GUARDS: list[tuple[str, str, str, int, str]] = [
    ("fan_attach_points", _SKIP_ARROW, "", 1,
     "TestEachPinGuardIsObserved."
     "test_the_fan_leaves_a_pinned_arrow_alone"),
    ("contention_feet", _SKIP_ARROW, "", 2,
     "TestEachPinGuardIsObserved."
     "test_contention_feet_leave_a_pinned_arrow_alone"),
    ("reroute_scene", "            if pinned_to_canvas(a):\n",
     "            if False:\n", 1,
     "TestEachPinGuardIsObserved."
     "test_reroute_scene_leaves_a_pinned_arrow_alone"),
    ("apply_ops.F1_post_pass",
     "if server_owns_geometry(e) and not pinned_to_canvas(e):",
     "if server_owns_geometry(e):", 1,
     "TestEachPinGuardIsObserved."
     "test_the_f1_post_pass_leaves_a_pinned_arrow_alone"),
    ("apply_ops.final_routing", _FINAL_ROUTE,
     '            if e.get("type") != "arrow" '
     "or not server_owns_geometry(e):", 1,
     "TestEachPinGuardIsObserved."
     "test_the_final_routing_pass_leaves_a_pinned_arrow_alone"),
    # occurrence 1 of `_SKIP_TIDY` is the SNAP loop, occurrence 2 the
    # ROUTING loop. They are byte identical, and pairing them the wrong
    # way round is how this instrument first reported two false
    # survivors — the mutation ran, the wrong test was asked about it.
    ("_tidy_pass.snap", _SKIP_TIDY, "", 1,
     "TestPinnedSurvivesEveryNonUserMover."
     "test_tidy_snap_skips_pinned_and_still_snaps_the_rest"),
    ("_tidy_pass.routing_loop", _SKIP_TIDY, "", 2,
     "TestEachPinGuardIsObserved."
     "test_tidys_router_leaves_a_pinned_arrow_alone"),
    # THE SITE THE ROSTER OMITTED. Verified unobserved before it had a
    # test: deleting it left all 1618 tests green. Its own comment states
    # the rule C-1 breaks — "a pin is a pin whichever end of the group it
    # sits on" — so the guard articulating the invariant was the one
    # nothing checked.
    ("_tidy_pass.group_cascade",
     "                        if pinned_to_canvas(p):",
     "                        if False:", 1,
     "TestEachPinGuardIsObserved."
     "test_a_pinned_part_does_not_ride_its_owners_snap"),
    # The LOAD path's own router, found by reading curator batch 37's
    # reds: gated on ownership, never on the pin, so a pinned arrow was
    # redrawn by opening the project.
    ("reroute_and_confess",
     "        if pinned_to_canvas(arrow):",
     "        if False:", 1,
     "TestEachPinGuardIsObserved."
     "test_the_loader_does_not_reroute_a_pinned_arrow"),
    ("_cmd_mermaid_relayout",
     '                        if o["id"] in ix '
     'and pinned_to_canvas(ix[o["id"]])})',
     "                        if False})", 1,
     "TestEachPinGuardIsObserved."
     "test_relayout_leaves_pinned_elements_alone"),
    ("normalize_z_order",
     "    slots = [i for i, e in enumerate(els) if not pinned_to_canvas(e)]",
     "    slots = list(range(len(els)))", 1,
     "TestPinnedSurvivesEveryNonUserMover."
     "test_z_rebanding_holds_a_pinned_elements_slot"),
    ("recenter_label",
     "    if label is None or pinned_to_canvas(label):",
     "    if label is None:", 1,
     "TestPinnedSurvivesEveryNonUserMover."
     "test_recenter_label_honours_a_pinned_label"),
    ("apply_ops.group_carry",
     "                        if pinned_to_canvas(other):",
     "                        if False:", 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_the_carry_itself_refuses_a_pinned_part"),
    ("apply_ops.del_cascade",
     '                      if i2 != el["id"] '
     "and pinned_to_canvas(index[i2])}",
     "                      if False}", 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_the_cascade_itself_spares_a_pinned_element_and_says_so"),
    ("apply_ops.resolve_pin", _RESOLVE,
     '            if el is not None and role_of(el) == "pin":', 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_the_resolve_arm_itself_spares_a_pinned_glyph"),
    ("validate_scene.ART011", _ART011, _ART011_OLD, 1,
     "TestTheLoadHealHonoursAPin."
     "test_the_label_refit_does_not_move_a_pinned_label"),
    ("validate_scene.ART007",
     '            if (el.get("x"), el.get("y")) == before:\n'
     "                continue\n", "", 1,
     "TestTheLoadHealHonoursAPin."
     "test_a_declined_recentre_files_no_repair"),
    ("pin_held_ops.judged_kinds",
     '"mod", "del", "reorder", "resolve_pin"):',
     '"mod", "del", "reorder"):', 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_resolve_pin_cannot_take_down_a_pinned_glyph"),
    ("pin_held_ops.midbatch_groups",
     "            if g in pin_groups and eid not in guarded:",
     "            if False:", 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_a_mod_groupids_joining_a_pinned_group_cannot_move_it"),
    ("pin_held_ops.backlink_edge",
     "        elif part_owner_id(p) == eid or part_owner_id(e) == pid:",
     "        elif False:", 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_a_pinned_part_is_safe_after_the_user_ungroups_the_widget"),
    ("reconcile_composed.postcondition",
     "        was = pinned_before.get(e[\"id\"])\n"
     "        if was is not None and (e.get(\"x\"), e.get(\"y\")) != was:\n"
     "            e[\"x\"], e[\"y\"] = was",
     "        pass", 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_the_reconciler_itself_restores_a_pinned_part"),
    ("pin_held_ops.derived_closure",
     '        if oid and oid in guarded and e["id"] not in guarded:',
     "        if False:", 1,
     "TestPinnedSurvivesTheBackDoors."
     "test_a_held_elements_bound_label_is_held_with_it"),
]


def nth_replace(text: str, old: str, new: str, n: int) -> str | None:
    """Replace the nth occurrence of `old`, or answer None if absent.

    The nth rather than the first because two guard sites are BYTE
    IDENTICAL — `_tidy_pass`' snap loop and its routing loop carry the
    same three lines — and pairing them the wrong way round is exactly
    how this instrument first reported two false survivors.

    Args:
        text: The source to patch.
        old: The snippet to find.
        new: Its replacement.
        n: Which occurrence, 1-based, in source order.

    Returns:
        The patched source, or None when there is no nth occurrence.
    """
    idx = -1
    for _ in range(n):
        idx = text.find(old, idx + 1)
        if idx < 0:
            return None
    return text[:idx] + new + text[idx + len(old):]


def run_one(old: str, new: str, n: int, test: str, original: str) -> str:
    """Mutate one guard, run its test, restore the file.

    Args:
        old: The guard snippet to remove or weaken.
        new: What to put in its place.
        n: Which occurrence of `old` this guard is.
        test: Dotted `tests.test_backend` path of the proving test.
        original: The unmodified source, restored in a `finally`.

    Returns:
        "KILLED", "SURVIVED", "ANCHOR-MISS" or "NO-OP".
    """
    mutated = nth_replace(original, old, new, n)
    if mutated is None:
        return "ANCHOR-MISS"
    if mutated == original:
        return "NO-OP"
    SRC.write_text(mutated, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_backend." + test],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900,
            check=False)
        return "KILLED" if proc.returncode != 0 else "SURVIVED"
    finally:
        SRC.write_text(original, encoding="utf-8")


def assert_pristine(original: str) -> None:
    """Refuse to start if a previous sweep left a mutation standing.

    THE FAILURE THIS CATCHES IS SILENT AND POISONS EVERYTHING AFTER IT. A
    sweep killed between "write mutated" and "restore" — a crash, a
    capacity fault, a timeout — leaves the mutation in the file, and the
    NEXT sweep reads that as its pristine baseline. Every result from
    then on is measured against a tree that is quietly wrong, and the
    runs look entirely ordinary. Checking the anchors before starting
    costs nothing and is the difference between a bad run and a bad run
    you can see.

    Args:
        original: The source as read at the start of this run.

    Raises:
        SystemExit: If any guard anchor is missing.
    """
    missing = [label for label, old, _new, n, _t in GUARDS
               if original.count(old) < n]
    if missing:
        sys.stderr.write(
            "REFUSING TO RUN: %d guard anchor(s) missing from %s — a "
            "previous sweep probably died before restoring. Restore the "
            "file from git before trusting any result: %s\n"
            % (len(missing), SRC, ", ".join(missing)))
        raise SystemExit(2)


def main() -> int:
    """Run every mutation and report.

    Returns:
        0 when every guard was killed by its named test, else 1.
    """
    original = SRC.read_text(encoding="utf-8")
    assert_pristine(original)
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for label, old, new, n, test in GUARDS:
        if only and only not in label:
            continue
        status = run_one(old, new, n, test, original)
        rows.append((label, status, test))
        print("%-34s %-11s %s" % (label, status, test.split(".")[-1]))
    bad = [r for r in rows if r[1] != "KILLED"]
    print()
    print("%d/%d guards observed by their named test"
          % (len(rows) - len(bad), len(rows)))
    for label, status, test in bad:
        print("   UNOBSERVED %-30s %s  (%s)" % (label, status, test))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
