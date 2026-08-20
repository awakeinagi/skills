"""Delete each pin guard individually; assert its named test goes red.

A guard whose absence nothing notices is not guarded — it is a line of
code that happens to be correct today. This is the instrument for that
claim: for each guard site it patches the guard out of `canvas.py`, runs
only the test that claims to prove it, restores the file, and reports
KILLED or SURVIVED.

WHY IT EXISTS. The first round of the pin work shipped 35 tests described
as "both directions on every guard". A verifier then mutated each guard
individually and found EIGHT of twelve could be deleted outright with the
whole suite green: the lines were live — flipping them to skip-everything
broke 3, 22, 14, 2 and 5 tests — but nothing observed their PRESENCE. Two
tests were passing for the wrong reason: one asserted a path was
unchanged in a scene the router had no intention of touching, and one
named the fan while exercising the router.

NOT PART OF THE SUITE, deliberately. It rewrites `canvas.py` in place and
restores it, which is acceptable for a hand-run instrument and wrong for
anything `unittest discover` may run beside other work. Run it whenever a
guard is added, moved or re-worded::

    python3 tests/guard_mutants.py            # every site, ~32s
    python3 tests/guard_mutants.py relayout   # one, by label substring
    python3 tests/guard_mutants.py --check    # subjects only, no subprocess

Exit status is non-zero if any guard survived its own test.

AND THE SWEEP ITSELF IS AN INSTRUMENT, so it owes the same answer it
demands: what would it say if the thing it watches were entirely absent?
It used to say "perfect". Two separate failures, both now closed:

  - It mapped ANY non-zero exit to KILLED, and `unittest` answers an
    unresolvable test name with a synthetic test that ERRORS. So a
    DELETED test read as a guard killed by it; the harness once reported
    23/23 over a tree whose tests were gone. `run_one` now reads the
    runner's RESULT OBJECT — see `_DRIVER` and `verdict` — and a name
    that does not resolve is `NO-TEST`, which `main` counts as
    unobserved and never as a kill.
  - Nothing ran it. `assert_pristine` guards the anchors only once
    somebody starts a sweep, and nobody did: its only mentions in
    `tests/test_backend.py` are comments. `--check` is the half cheap
    enough for the every-commit gate (no subprocess, no write to
    `canvas.py`), and `.pre-commit-config.yaml` runs it there while the
    full sweep sits at the manual stage beside the e2e suite::

        uvx pre-commit run guard-mutants-sweep --hook-stage manual

    The gate half cannot tell you a guard is unobserved — only the sweep
    can. What it CAN tell you is that this file has stopped being able
    to ask, which is the failure that was previously silent.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "skills" / "wysiwyg-grilling" / "scripts" / "canvas.py"
MODULE = "tests.test_backend"

# THE DRIVER, AND WHY THIS IS NOT `-m unittest <name>`. An exit code
# answers "did the process end badly", and this instrument needs "did the
# NAMED TEST observe the mutation" — two questions that agree right up
# until they matter. `unittest` turns an unresolvable test name into a
# synthetic `unittest.loader._FailedTest` that ERRORS, so a test that has
# been renamed, moved or DELETED exits non-zero and the old code read
# that as KILLED. That is not a hypothetical: this harness once reported
# a perfect score over a tree whose tests had been deleted, because every
# missing test "failed". A guard census that reads as perfect when the
# tests are gone is the exact instrument-cannot-report-its-own-death
# defect it was built to find, one level up.
#
# So the child imports the module itself, loads the name itself, runs a
# `TextTestRunner`, and prints the RESULT OBJECT as json. Every verdict
# below is read from counts, never from `returncode`; `returncode` is now
# used for one thing only — deciding whether there is a result at all.
_DRIVER = r'''
import io, json, sys, unittest
name = sys.argv[1]
out = {"import_error": None, "load_errors": [], "run": 0, "failures": 0,
       "errors": 0, "unexpected": 0, "skipped": 0}
try:
    __import__(name.split(".")[0] + "." + name.split(".")[1])
except BaseException as exc:                       # noqa: BLE001
    out["import_error"] = "%s: %s" % (type(exc).__name__, exc)
    print("GUARDMUTANT " + json.dumps(out))
    raise SystemExit(0)
loader = unittest.TestLoader()
suite = loader.loadTestsFromName(name)
out["load_errors"] = [str(e).splitlines()[-1] for e in (loader.errors or [])]
if not out["load_errors"]:
    res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    out["run"] = res.testsRun
    out["failures"] = len(res.failures)
    out["errors"] = len(res.errors)
    out["unexpected"] = len(getattr(res, "unexpectedSuccesses", ()) or ())
    out["skipped"] = len(res.skipped)
print("GUARDMUTANT " + json.dumps(out))
'''


def verdict(report: dict | None) -> str:
    """Turn a driver report into a guard verdict. The whole point of the file.

    Kept separate from the subprocess so the mapping can be exercised
    without starting one, and so the ONE line that used to read
    `returncode != 0` is now a named function with the four
    not-a-kill answers spelled out.

    Args:
        report: The driver's parsed json, or None when the child
            produced none (it crashed, was killed, or timed out).

    Returns:
        "KILLED" when the named test observed the mutation;
        "SURVIVED" when it ran and did not; "NO-TEST" when the name does
        not resolve to a runnable test; "SKIPPED" when every test it
        names was skipped; "IMPORT-ERROR" when the mutated source would
        not import at all — a real signal, but the interpreter's and not
        the guard test's; "HARNESS-ERROR" when there is no report.
    """
    if report is None:
        return "HARNESS-ERROR"
    if report["import_error"]:
        return "IMPORT-ERROR"
    if report["load_errors"] or report["run"] == 0:
        return "NO-TEST"
    if report["skipped"] >= report["run"]:
        return "SKIPPED"
    if report["failures"] or report["errors"] or report["unexpected"]:
        return "KILLED"
    return "SURVIVED"


_SKIP_ARROW = (
    "        if pinned_to_canvas(a):\n"
    "            skipped += 1\n"
    "            continue\n")
_SKIP_TIDY = (
    "                if pinned_to_canvas(e):\n"
    '                    pinned.add(e["id"])\n'
    "                    continue\n")
# RE-POINTED by the group-ruling change, and it MATTERS THAT THIS ONE
# MOVED. `_SKIP_TIDY` used to occur twice — the snap loop and the routing
# loop, byte identical — and the snap loop's copy is now a unit-level
# question instead (`_TIDY_UNIT_HELD`). So `_SKIP_TIDY` occurs ONCE, the
# routing loop's entry drops to occurrence 1, and a `_tidy_pass.snap`
# left pointing at occurrence 1 would have mutated the ROUTING loop while
# asking the SNAP test: the exact mis-pairing that produced this
# instrument's first two false survivors. `assert_pristine` now refuses
# on a multiplicity change for precisely this reason — see its docstring.
_TIDY_UNIT_HELD = (
    '                held = {m["id"] for m in unit if pinned_to_canvas(m)}')
_FINAL_ROUTE = (
    '            if e.get("type") != "arrow" '
    "or not server_owns_geometry(e) \\\n"
    "                    or pinned_to_canvas(e):")
_RESOLVE = (
    '            if el is not None and role_of(el) == "pin" and \\\n'
    "                    not pinned_to_canvas(el):")
# RE-POINTED when the N-1 fix rewrote this arm. The harness REFUSED to
# run until it was — which is the anti-rot half of the pristine check
# doing its other job: a moved anchor and a crashed sweep look identical
# from here, and both must stop the run rather than silently skip a site.
_ART011 = "            recenter_label(kept, cont)"
_ART011_OLD = (
    '            el["x"] = cont["x"] + max(\n'
    '                (cont.get("width", 0) - el.get("width", 0)) / 2, 4)\n'
    '            el["y"] = cont["y"] + max(\n'
    '                (cont.get("height", 0) - el.get("height", 0)) / 2, 4)')
# The NARRATION condition, not a guard. The guard census counts sites
# that stop a MOVE; N-1 was a site that stopped the move correctly and
# said something false about why, which no guard mutant can reach. This
# is the first entry that mutates a SENTENCE's condition, and its pair is
# the negative-direction class rather than a movement assertion.
_ART011_SAYS = (
    '                    " — " + pinned_clause(1) if pinned_to_canvas(el)\n'
    '                    else ""),')
_ART011_SAYS_N1 = (
    '                    " — " + pinned_clause(1) if True\n'
    '                    else ""),')

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
    # ONE LINE, TWO CLAIMS, and the two entries are deliberate rather
    # than a duplicate. The snap loop used to ask the pin question twice —
    # once about the element it was about to snap, once per part inside
    # the carry — and the group ruling merged them into a single
    # unit-level question: a pin ANYWHERE in a group holds the whole
    # group. The mutation is therefore the same for both, but the two
    # facts it breaks are not, and each still needs its own test to die.
    # `.snap` is the leader-pinned pole, `.group_cascade` the
    # follower-pinned one.
    ("_tidy_pass.snap", _TIDY_UNIT_HELD, "                held = set()", 1,
     "TestPinnedSurvivesEveryNonUserMover."
     "test_tidy_snap_skips_pinned_and_still_snaps_the_rest"),
    # THE SITE THE ROSTER OMITTED. Verified unobserved before it had a
    # test: deleting it left all 1618 tests green. Its own comment states
    # the rule C-1 breaks — "a pin is a pin whichever end of the group it
    # sits on" — so the guard articulating the invariant was the one
    # nothing checked.
    ("_tidy_pass.group_cascade", _TIDY_UNIT_HELD,
     "                held = set()", 1,
     "TestEachPinGuardIsObserved."
     "test_a_pinned_part_does_not_ride_its_owners_snap"),
    # occurrence 1 of `_SKIP_TIDY` is the ROUTING loop, and now the only
    # one — see `_TIDY_UNIT_HELD` for why it used to be occurrence 2.
    ("_tidy_pass.routing_loop", _SKIP_TIDY, "", 1,
     "TestEachPinGuardIsObserved."
     "test_tidys_router_leaves_a_pinned_arrow_alone"),
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
    # DE-INDENTED by the group ruling: the carry lost the `role ==
    # "decoration"` arm it used to nest inside, so the guard sits one
    # level shallower. The anchor is the guard's own line and nothing
    # around it, which is why re-pointing it was a four-space edit.
    ("apply_ops.group_carry",
     "                    if pinned_to_canvas(other):",
     "                    if False:", 1,
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
    ("validate_scene.ART011_clause", _ART011_SAYS, _ART011_SAYS_N1, 1,
     "TestNoPassClaimsAPinItDidNotHonour."
     "test_the_load_refit_says_nothing_about_pins_when_none_are_pinned"),
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
        One of `verdict`'s answers, or "ANCHOR-MISS" / "NO-OP" when the
        mutation could not be applied at all.
    """
    mutated = nth_replace(original, old, new, n)
    if mutated is None:
        return "ANCHOR-MISS"
    if mutated == original:
        return "NO-OP"
    SRC.write_text(mutated, encoding="utf-8")
    try:
        # `-B` AND A SWEPT CACHE, because a SURVIVED that is really a
        # stale `.pyc` is the worst result this instrument can produce:
        # it reports a guard as unobserved, which reads as a finding and
        # sends someone writing a test for a hole that is not there.
        # Observed once in three runs on a loaded machine —
        # `contention_feet` SURVIVED in a full sweep and was KILLED both
        # in isolation and on re-run — and rapid write/exec cycles inside
        # one mtime granule are the standard cause. Cheap to rule out,
        # expensive to chase later.
        for cache in ROOT.rglob("__pycache__"):
            for pyc in cache.glob("*.pyc"):
                pyc.unlink(missing_ok=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            proc = subprocess.run(
                [sys.executable, "-B", "-c", _DRIVER, MODULE + "." + test],
                cwd=str(ROOT), capture_output=True, text=True, timeout=900,
                check=False, env=env)
        except subprocess.TimeoutExpired:
            return "HARNESS-ERROR"
        line = next((ln for ln in proc.stdout.splitlines()
                     if ln.startswith("GUARDMUTANT ")), None)
        return verdict(json.loads(line[12:]) if line else None)
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

    AND A MULTIPLICITY CHANGE IS AS BAD AS A MISSING ONE, which is the
    second check and the newer one. An anchor that goes from two
    occurrences to one does not go missing — it silently RE-PAIRS, and
    occurrence 1 becomes a different site than the roster believes.
    `_SKIP_TIDY` did exactly that when the snap loop's copy became a
    unit-level question: `_tidy_pass.snap` would have mutated the ROUTING
    loop and asked the SNAP test about it, which is the shape of this
    instrument's first two false survivors. So the count must equal the
    highest occurrence the roster claims for that snippet — never merely
    reach it. A spare unclaimed copy means a real guard nobody registered
    or a roster entry pointing at an ambiguous line, and both are
    findings rather than conditions to run under.

    Args:
        original: The source as read at the start of this run.

    Raises:
        SystemExit: If any guard anchor is missing or its occurrence
            count no longer matches what the roster claims.
    """
    claimed: dict[str, int] = {}
    labelled: dict[str, list[str]] = {}
    for label, old, _new, n, _t in GUARDS:
        claimed[old] = max(claimed.get(old, 0), n)
        labelled.setdefault(old, []).append(label)
    missing = [label for label, old, _new, n, _t in GUARDS
               if original.count(old) < n]
    if missing:
        sys.stderr.write(
            "REFUSING TO RUN: %d guard anchor(s) missing from %s — a "
            "previous sweep probably died before restoring. Restore the "
            "file from git before trusting any result: %s\n"
            % (len(missing), SRC, ", ".join(missing)))
        raise SystemExit(2)
    drift = [(", ".join(labelled[old]), n, original.count(old))
             for old, n in sorted(claimed.items())
             if original.count(old) != n]
    if drift:
        sys.stderr.write(
            "REFUSING TO RUN: %d anchor(s) changed multiplicity in %s. "
            "The roster's occurrence numbers no longer name the sites it "
            "thinks they do, so every result would be a coin flip "
            "(label: claimed -> found): %s\n"
            % (len(drift), SRC,
               ", ".join("%s: %d -> %d" % d for d in drift)))
        raise SystemExit(2)


def check_subjects() -> list[str]:
    """Report every way this instrument has lost its subject. No subprocess.

    THIS IS THE HALF THAT RUNS ON EVERY COMMIT, and it exists because
    the sweep below is a hand-run tool that nothing ran: it could be
    broken for as long as you like and say nothing, which is precisely
    the class of defect it was written to hunt. The sweep is too slow
    and far too invasive for the gate — it rewrites `canvas.py` in
    place — but its SUBJECTS are free to check, and an instrument whose
    subjects have gone is already dead whether or not anyone runs it.

    Deliberately behavioural rather than a spelling census. It does not
    ask "does this string appear"; it asks the two questions whose "no"
    means the sweep can no longer produce an honest answer:

      1. does the mutation still APPLY — is there an nth occurrence, and
         does replacing it actually change the file? A `NO-OP` entry is
         a mutant that mutates nothing, and the sweep would run its test
         against a pristine tree and call the survivor a survivor.
      2. does the named test still RESOLVE to a runnable test? This is
         the deleted-tests failure caught statically. `unittest` answers
         a missing name with a synthetic failing test, so at sweep time
         its absence is indistinguishable from a kill unless somebody
         looks — and now somebody does, before any process starts.

    Returns:
        One human-readable complaint per broken subject; empty when the
        instrument still has everything it needs to be honest.
    """
    src = SRC.read_text(encoding="utf-8")
    bad = []
    for label, old, new, n, _test in GUARDS:
        mutated = nth_replace(src, old, new, n)
        if mutated is None:
            bad.append("%s: anchor has no occurrence %d in %s"
                       % (label, n, SRC.name))
        elif mutated == src:
            bad.append("%s: mutation is a no-op — it would change nothing "
                       "and its test would 'survive' a pristine tree" % label)
    sys.path.insert(0, str(ROOT))
    try:
        loader = unittest.TestLoader()
        for label, _old, _new, _n, test in GUARDS:
            loader.errors = []
            suite = loader.loadTestsFromName(MODULE + "." + test)
            if loader.errors or suite.countTestCases() == 0:
                bad.append("%s: names %s, which does not resolve to a test — "
                           "a sweep would read its absence as a KILL"
                           % (label, test))
    finally:
        sys.path.remove(str(ROOT))
    return bad


def main() -> int:
    """Run every mutation and report.

    Returns:
        0 when every guard was killed by its named test, else 1.
    """
    argv = sys.argv[1:]
    if "--check" in argv:
        broken = check_subjects()
        if not broken:
            print("guard_mutants: %d mutants, every anchor applies and "
                  "every named test resolves" % len(GUARDS))
            return 0
        for line in broken:
            sys.stderr.write("  %s\n" % line)
        sys.stderr.write(
            "%d of %d guard mutants have lost a subject. This instrument "
            "cannot give an honest answer until they are re-pointed; then "
            "run `python3 tests/guard_mutants.py` for the full sweep.\n"
            % (len(broken), len(GUARDS)))
        return 1
    original = SRC.read_text(encoding="utf-8")
    assert_pristine(original)
    only = argv[0] if argv else None
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
