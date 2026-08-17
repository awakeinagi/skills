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
    # THE NEXT THREE ALL DIED AGAIN inside one wave, and TWO AGENTS
    # RE-ANCHORED THEM WITHIN AN HOUR OF EACH OTHER — v0.9 WP8 task 30
    # and curator batch 27, both on 2026-08-17, neither aware of the
    # other. Task 30's account is right and is kept: task 29 had written
    # all three against the state it found — an EMPTY `CATALOGUE_RED_IDS`,
    # a `*(none)*` census row, a one-entry `HAND_AUTHORED_RED_CLASSES` —
    # and the very next commit refilled the catalogue, so each anchored on
    # a string the file had stopped containing. A catalogue is empty for a
    # commit and populated for months.
    #
    # WHERE THE TWO REPAIRS DIFFERED IS THE LESSON, so the stronger one is
    # kept per probe and the weaker recorded beside it. Task 30 moved each
    # anchor one element in — to the row's FIRST ID, to the dict's FIRST
    # ENTRY — which is still the container's contents, and batch 27 added
    # a class to that dict hours later. Anchoring on the CONTENTS of a
    # container that exists to change is a probe with a half-life however
    # far in you point.
    #
    # THE RULE NOW: anchor on the DECLARATION, perturb by INJECTION.
    # `X = ` and `| cell |` survive every edit to what follows them, and
    # prepending a phantom is a real perturbation whatever the container
    # already holds — empty or full, one entry or ten. `catalogue-reds`
    # injects through `|`, set union, so it reads the same against
    # `set()` and against a three-id literal; the other two prepend into
    # a dict and a table cell. None of the three can be silently
    # satisfied by the subject's own drift, which is what the old
    # value-matching anchors could not promise.
    #
    # DIRECTION IS DELIBERATE: all three ADD a phantom red rather than
    # removing a real one. A census that under-reports is already caught
    # by the flip contract (unittest fails on a red that started
    # passing); a census that OVER-reports is caught by nothing else at
    # all, so that is the pole worth a probe.
    "model-row-stale": {
        "test": "test_the_handover_transcribes_the_reds_it_declares",
        "file": "SESSION-HANDOVER.md",
        "why": "the catalogue-reds row claims a red the catalogue does "
               "not declare",
        # Task 30 and batch 27 re-anchored this independently within the
        # hour, at "| model (default suite) | `corner_feet" and at the
        # cell alone. The CELL ALONE WINS: the id is the first entry in
        # the row, which is the container's contents again one element
        # in, and it dies the day that mutant flips or the row is sorted.
        "old": "| model (default suite) |",
        "new": "| model (default suite) | `a_red_nobody_flipped`,",
    },
    "hand-authored-count-drift": {
        "test": "test_the_hand_authored_red_classes_are_the_ones_that_exist",
        "file": "tests/test_mutants.py",
        "why": "a red class is declared that no test class carries",
        # THE THIRD AND FOURTH RE-ANCHORS LANDED THE SAME AFTERNOON, and
        # the difference between them is the rule. Batch 26 anchored on a
        # trailing comma TASK-MICROFIX deleted; task 29 on a one-entry
        # dict the spike-program batch refilled to three; task 30 then on
        # `{"TestBatchPathIntegrity": 1,` — an ENTRY, better than a count
        # and still the container's contents, and batch 27 added a class
        # to this very dict hours later. The declaration
        # `HAND_AUTHORED_RED_CLASSES = {` is the only part that has not
        # moved across four re-anchors, and a phantom class prepended
        # into it perturbs whatever the dict happens to hold.
        "old": "HAND_AUTHORED_RED_CLASSES = {",
        "new": 'HAND_AUTHORED_RED_CLASSES = {"TestNobodyWrote": 1, ',
    },
    "catalogue-reds-drift": {
        "test": "test_the_catalogue_reds_are_the_ones_declared",
        "file": "tests/test_mutants.py",
        "why": "CATALOGUE_RED_IDS declares a red the decorators do not "
               "carry",
        # `= ` and not `= {`: set union reads the same against `set()`
        # and against a literal, so this one also survives the catalogue
        # emptying again — which is what killed both previous anchors.
        "old": "CATALOGUE_RED_IDS = ",
        "new": 'CATALOGUE_RED_IDS = {"a_red_nobody_declared"} | ',
    },
    # THE THIRD CENSUS THIS FILE WATCHES, added by curator batch 27
    # (2026-08-17): SKILL.md's event taxonomy, which is the copy an AGENT
    # reads and therefore the copy whose staleness costs a live round.
    # Task 26 shipped it wrong by one and the missing type was the one
    # that commit existed to document; the guard it now has
    # (`test_the_skill_taxonomy_table_is_the_emitted_set`) makes three
    # comparisons and these two probes take one each.
    #
    # PERTURBING SKILL.md IS WHY `_scratch` STOPPED SYMLINKING `skills/`.
    # Before that a probe here would have written through the symlink and
    # edited the shipped skill.
    "taxonomy-owner-drift": {
        "test": "test_the_skill_taxonomy_table_is_the_emitted_set",
        "file": "skills/wysiwyg-grilling/SKILL.md",
        "why": "the table hands the agent a type its constants do not "
               "own — the reaction, not just the roster, goes wrong",
        "old": "| `agent_revision`, ",
        "new": "| `agent_phantom`, `agent_revision`, ",
    },
    # THE NUMERAL IS PERTURBED BY DUPLICATION rather than by editing the
    # word, for the anchor reason this file keeps relearning: the word IS
    # the moving literal, so an anchor on it dies the next time the
    # taxonomy grows. A second sentence above the real one is a stable
    # injection AND probes the reading direction — a guard that took the
    # LAST match would stay green here while a human read the first.
    "taxonomy-numeral-drift": {
        "test": "test_the_skill_taxonomy_table_is_the_emitted_set",
        "file": "skills/wysiwyg-grilling/SKILL.md",
        "why": "the spelled count above the table and the emitted "
               "cardinality have parted (task 26 shipped exactly this)",
        "old": "**Event taxonomy** — all ",
        "new": "**Event taxonomy** — all three types (a stale copy).\n\n"
               "**Event taxonomy** — all ",
    },
}


def _mirror_skills(src: Path, dst: Path) -> None:
    """Mirror the product tree with its prose WRITABLE and its bulk shared.

    `skills/` used to be one symlink, on the reasoning that nothing here
    perturbs the product. The taxonomy probes perturb SKILL.md, and
    writing through a symlinked directory would have edited the REAL
    file — a probe that damages the repo it is measuring. So directories
    are recreated, `.md` files are COPIED (18 of them, all prose), and
    every other file is symlinked: the 23MB web bundle and the 900KB
    `canvas.py` are shared, and the tree still costs kilobytes per probe.

    Args:
        src: The directory to mirror.
        dst: Where to build the mirror; created if absent.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.name == "__pycache__":
            continue
        if entry.is_dir():
            _mirror_skills(entry, dst / entry.name)
        elif entry.suffix == ".md":
            shutil.copy2(entry, dst / entry.name)
        else:
            (dst / entry.name).symlink_to(entry)


def _scratch(into: Path) -> Path:
    """Build a runnable copy of the repo's test surface in `into`.

    `skills/` is mirrored by `_mirror_skills` — prose copied, bulk
    symlinked — because some guards read the skill's own text and a
    probe has to be able to break it. `SESSION-HANDOVER.md` and `tests/`
    are real copies because they are what the probes break.

    Args:
        into: An empty directory to build the tree in.

    Returns:
        The tree's root, ready to run `unittest discover -s tests` in.
    """
    shutil.copytree(REPO / "tests", into / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(REPO / "SESSION-HANDOVER.md", into / "SESSION-HANDOVER.md")
    _mirror_skills(REPO / "skills", into / "skills")
    for name in ("pyproject.toml", "uv.lock"):
        if (REPO / name).exists():
            shutil.copy2(REPO / name, into / name)
    return into


def anchor_fault(name: str, spec: dict[str, str]) -> str:
    """Report why this probe cannot perturb anything, or `""` if it can.

    Separated from `run_probe` because the runner used to raise on the
    first bad anchor and never reach the rest, and that is exactly how
    THREE dead anchors hid behind one for a whole wave (batch 27,
    2026-08-17). Every anchor is now checked before any probe runs, so
    one re-anchoring job is reported as one job.

    Args:
        name: The probe's key in `PROBES`, for the message.
        spec: That probe's entry — `file` and `old` are read.

    Returns:
        A one-line fault description, or the empty string when the
        anchor appears exactly once in its file.
    """
    text = (REPO / spec["file"]).read_text(encoding="utf-8")
    hits = text.count(spec["old"])
    if hits == 1:
        return ""
    return ("probe %r anchors on a string appearing %d times in %s, not "
            "once — re-anchor it. A probe that cannot find its anchor "
            "runs against an unmodified tree and reports the guard "
            "silent when nothing was broken" % (name, hits, spec["file"]))


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
        fault = anchor_fault(name, spec)
        if fault:
            raise AssertionError(fault)
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
        suite never runs it. Dead anchors are reported ALL AT ONCE and
        before any probe runs, so a re-anchoring job cannot be discovered
        one probe at a time.
    """
    picked = {k: v for k, v in PROBES.items()
              if not argv or any(a in k for a in argv)}
    if not picked:
        print("no probe matches %r; known: %s"
              % (argv, ", ".join(sorted(PROBES))))
        return 1
    faults = [f for f in (anchor_fault(n, s)
                          for n, s in sorted(picked.items())) if f]
    if faults:
        for fault in faults:
            print(fault)
        print("\n%d of %d probe(s) cannot perturb their subject; NO probe "
              "was run, because a dead anchor's verdict would be a lie "
              "about the guard" % (len(faults), len(picked)))
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
