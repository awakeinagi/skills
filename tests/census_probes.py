"""Prove the census guards fail when their subject drifts. Run by hand.

WHY THIS FILE EXISTS. The five guards in `TestCoverage` that compare
SESSION-HANDOVER.md against derived facts are the only thing standing
between the census and a prose copy that has quietly stopped being true.
A guard like that is exactly the shape doctrine §1 warns about: it is
green when the census is healthy AND green when it has stopped reading
anything, and the two are indistinguishable from outside. So every one
of them has to be watched failing at least once, against a real drift,
before it is worth anything.

That watching had been done three times and thrown away three times —
reconstructed from scratch by curator batches 23, 25 and MICROFIX, each
of them writing the same scratch-tree-and-perturb script into /tmp and
losing it with the tempdir. This is that script, kept. Curator batch 26,
2026-08-16, from task-microfix concern 5.

NOT COLLECTED BY THE SUITE, on purpose and by construction: unittest
discovery matches `test*.py`, and this is `census_probes.py`. Each probe
copies the tree, breaks the copy and runs one guard against it, which
costs a subprocess per probe and proves nothing about the repo's own
health — it belongs in a curator's hands before touching a guard, not in
every commit. The suite already fails if a guard's SUBJECT drifts; this
answers the different question of whether the guard would notice.

    python tests/census_probes.py            # every probe
    python tests/census_probes.py render     # just the ones matching

A probe PASSES when the guard it targets FAILS. Read the output that way:
`caught` is the healthy result and `SILENT` is a finding about the guard.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Each probe: the guard's test name, and one edit that must make it fail.
# The edits are (old, new) pairs applied to one file, and every one of them
# asserts its anchor appears EXACTLY ONCE before applying — a probe whose
# anchor has been reworded away would otherwise "run" against an unmodified
# tree, watch the guard pass, and report the guard silent when nothing was
# ever broken. That is the same false-negative the guards themselves are
# built against, one level up.
PROBES: dict[str, dict[str, str]] = {
    "render-row-stale": {
        "test": "test_the_handover_transcribes_the_render_rows_reds",
        "file": "SESSION-HANDOVER.md",
        "why": "a red flips and the row still lists it",
        "old": "`test_red_clean_stripe_bands_report_a_perfectly_healthy_"
               "drawing` (UNGATED",
        "new": "`test_red_a_name_no_decorator_carries` (UNGATED",
    },
    "render-row-duplicate": {
        "test": "test_the_handover_transcribes_the_render_rows_reds",
        "file": "SESSION-HANDOVER.md",
        "why": "THE SILENT DIRECTION: a CORRECT copy above a STALE row. "
               "A first-match-wins reader agrees with the copy and reports "
               "the census healthy while the row a human reads is wrong",
        "old": "| render (`tests/test_mutants_render.py`) |",
        "new": "| render (`tests/test_mutants_render.py`) | "
               "`test_red_clean_stripe_bands_report_a_perfectly_healthy_"
               "drawing` |\n| render (`tests/test_mutants_render.py`) |",
    },
    # TWO OF THE FIVE PROBES IN THIS FILE WERE DEAD ON ARRIVAL, found by
    # v0.9 WP7 task 29 when it ran the file end to end — which, it turns
    # out, nothing had done since batch 26 wrote it, because the runner
    # raises on the FIRST bad anchor and never reaches the rest. This one
    # was anchored on `**9 / 1 / 0**`, which task 28 took to 5 four
    # commits earlier; `hand-authored-count-drift` below was anchored on
    # a trailing comma TASK-MICROFIX deleted.
    #
    # THE SHAPE IS THE POINT AND IT IS THIS FILE'S OWN SUBJECT, one level
    # up: these probes are a hand copy of facts that move, watching
    # guards that watch hand copies of facts that move, and nothing was
    # watching them. Each anchor is now chosen to be the most stable
    # substring that still forces a REAL change — here the trailing
    # `/ 0**`, the `test_backend.py` count, which is the digit of the
    # three that moves least. Perturbing it to 7 makes the guard compare
    # (2, 1, 7) against a derived (2, 1, 0).
    #
    # A cheaper-looking fix was tried and rejected: anchoring on the
    # label alone and prefixing a `0` turned `/ 0**` into `/ 00**`, which
    # the guard parses to the same number, so the probe ran, broke
    # nothing, and reported the guard SILENT. A probe that perturbs a
    # value into an equal value is worse than a dead one, because it
    # accuses a healthy guard instead of raising.
    "durable-counts-drift": {
        "test": "test_the_handover_transcribes_the_durable_red_counts",
        "file": "SESSION-HANDOVER.md",
        "why": "the per-file red counts are hand-copied and have drifted "
               "seven recorded times",
        "old": " / 0** for `test_mutants.py`",
        "new": " / 7** for `test_mutants.py`",
    },
    # BOTH OF THE NEXT TWO CHANGED DIRECTION on 2026-08-16 (task 29), and
    # the reason is the interesting part: they used to REMOVE a declared
    # red, and the catalogue now declares none, so there is nothing left
    # to remove. They add a phantom instead — a row and a constant
    # claiming a red the decorators do not carry. That is the stronger
    # direction anyway: a census that under-reports is caught by the
    # flip contract (unittest fails on a red that started passing),
    # while a census that OVER-reports is caught by nothing else at all.
    # RE-ANCHORED at v0.9 WP8 (task 30), which found this probe and the
    # one at the bottom of this dict both dead on arrival — for the same
    # reason, one commit apart from the reason above. Task 29 wrote both
    # against an EMPTY catalogue ("*(none", `set()`), and the very next
    # commit refilled it with the attach-side family, so each anchored on
    # a string the file had stopped containing. Anchoring on emptiness is
    # what keeps failing: a catalogue is empty for a commit and populated
    # for months. Both now anchor on the row/constant's opening instead,
    # which survives whatever the catalogue holds.
    "model-row-stale": {
        "test": "test_the_handover_transcribes_the_reds_it_declares",
        "file": "SESSION-HANDOVER.md",
        "why": "the catalogue-reds row claims a red the catalogue does "
               "not declare",
        "old": "| model (default suite) | `corner_feet",
        "new": "| model (default suite) | `a_red_nobody_flipped`, `corner_feet",
    },
    "hand-authored-count-drift": {
        "test": "test_the_hand_authored_red_classes_are_the_ones_that_exist",
        "file": "tests/test_mutants.py",
        "why": "a red is added to a declared class and the count is not",
        # THE THIRD RE-ANCHOR, and the first two are why this one is
        # written differently. Batch 26 anchored on a trailing comma
        # TASK-MICROFIX deleted; task 29 re-anchored on a one-entry dict
        # the spike-program batch then refilled to three, so the string
        # below carried a `}` that no longer existed and the probe was
        # dead again inside a day. Anchored on the ENTRY alone now —
        # neither the dict's punctuation nor its other members can move
        # it — and perturbed to a count no live class can hold, so the
        # probe keeps working whatever this class's real number becomes.
        # (v0.9 WP8 took it 2 -> 1 when the mod-roundness red flipped.)
        "old": '{"TestBatchPathIntegrity": 1,',
        "new": '{"TestBatchPathIntegrity": 99,',
    },
    "catalogue-reds-drift": {
        "test": "test_the_catalogue_reds_are_the_ones_declared",
        "file": "tests/test_mutants.py",
        "why": "CATALOGUE_RED_IDS declares a red the decorators do not "
               "carry",
        "old": "CATALOGUE_RED_IDS = {",
        "new": 'CATALOGUE_RED_IDS = {"a_red_nobody_declared",',
    },
}


def _scratch(into: Path) -> Path:
    """Build a runnable copy of the repo's test surface in `into`.

    `skills/` is symlinked rather than copied: it is the product tree the
    tests import and nothing here perturbs it, so copying megabytes per
    probe would buy nothing. `SESSION-HANDOVER.md` and `tests/` are real
    copies because they are what the probes break.

    Args:
        into: An empty directory to build the tree in.

    Returns:
        The tree's root, ready to run `unittest discover -s tests` in.
    """
    shutil.copytree(REPO / "tests", into / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(REPO / "SESSION-HANDOVER.md", into / "SESSION-HANDOVER.md")
    (into / "skills").symlink_to(REPO / "skills")
    for name in ("pyproject.toml", "uv.lock"):
        if (REPO / name).exists():
            shutil.copy2(REPO / name, into / name)
    return into


def run_probe(name: str, spec: dict[str, str]) -> bool:
    """Break one census subject and report whether its guard noticed.

    Args:
        name: The probe's key in `PROBES`, for the printed line.
        spec: That probe's entry — `test`, `file`, `old`, `new`, `why`.

    Returns:
        True if the guard FAILED (the healthy result), False if it stayed
        green over a broken census.

    Raises:
        AssertionError: If the probe's anchor is not present exactly once,
            which means the probe is measuring nothing and must be
            re-anchored before its verdict is read.
    """
    tmp = Path(tempfile.mkdtemp(prefix="cb26-census-probe-"))
    try:
        tree = _scratch(tmp / "t")
        target = tree / spec["file"]
        text = target.read_text(encoding="utf-8")
        hits = text.count(spec["old"])
        if hits != 1:
            raise AssertionError(
                "probe %r anchors on a string appearing %d times in %s, "
                "not once — re-anchor it. A probe that cannot find its "
                "anchor runs against an unmodified tree and reports the "
                "guard silent when nothing was broken"
                % (name, hits, spec["file"]))
        target.write_text(text.replace(spec["old"], spec["new"]),
                          encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests",
             "-k", spec["test"]],
            cwd=tree, capture_output=True, text=True, timeout=900)
        ran = "Ran 1 test" in (proc.stdout + proc.stderr)
        if not ran:
            raise AssertionError(
                "probe %r selected %d tests with -k %r, not 1 — the guard "
                "has been renamed and this probe is measuring the wrong "
                "thing" % (name, 0, spec["test"]))
        return proc.returncode != 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    """Run every probe whose name contains one of `argv`, or all of them.

    Args:
        argv: Substrings selecting probes; empty means all.

    Returns:
        0 if every selected guard caught its drift, 1 otherwise — so this
        is usable as a gate in a curator's own checklist even though the
        suite never runs it.
    """
    picked = {k: v for k, v in PROBES.items()
              if not argv or any(a in k for a in argv)}
    if not picked:
        print("no probe matches %r; known: %s"
              % (argv, ", ".join(sorted(PROBES))))
        return 1
    silent = []
    for name, spec in sorted(picked.items()):
        caught = run_probe(name, spec)
        print("%-28s %-8s %s" % (name, "caught" if caught else "SILENT",
                                 spec["why"]))
        if not caught:
            silent.append(name)
    if silent:
        print("\n%d guard(s) stayed green over a broken census: %s"
              % (len(silent), ", ".join(silent)))
    return 1 if silent else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
