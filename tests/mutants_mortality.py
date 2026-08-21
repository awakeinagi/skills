r"""Detector mortality: kill each registered check, require its death to hurt.

Every check this repo registers is supposed to be held down by some
behavioural test. This module measures whether that is true, the only way it
can be measured rather than argued: it builds a throwaway copy of the tree,
patches ONE registered detector so it reports nothing, runs the real suites
against the corpse, and records which tests fail. A check whose death nothing
notices is a check the harness only believes in.

Running it (it is NOT in the every-commit suite, and not auto-discovered —
`unittest discover` collects `test_*.py`, and this module is deliberately not
named that way, so its cost is zero unless you ask for it)::

    MUTANTS_MORTALITY=1 python3 tests/mutants_mortality.py          # model tier
    MUTANTS_MORTALITY=1 MUTANTS_RENDER=1 python3 tests/mutants_mortality.py

The render tier opts in through `MUTANTS_RENDER=1`, the knob that already
means "spend the browser time", rather than through a second one of its own.

WHAT IT PROVES. Per check: that at least one behavioural test fails when that
check goes blind, and HOW MANY do. The witness count is a free second output
and the only one of its kind in the repo — `parity_clipped` had a single
witness when the originating spike measured it, and nothing else would have
said so. A check dropping to one witness is a warning this sweep produces for
nothing.

BUT A WITNESS COUNT IS NOT COMPARABLE ACROSS A COMMIT THAT FLIPS A RED, and
the correction is per-red rather than per-red-count. An
`@unittest.expectedFailure` pin whose expectation A DEAD DETECTOR SATISFIES —
a red asserting the check is SILENT, where that silence is the defect's own
signature — turns UNEXPECTED SUCCESS when the check is killed, and `_bad`
counts an unexpected success as a witness (correctly: a red going green IS a
death noticed). Flipping such a red therefore takes a witness away with ZERO
change to the detector, and the drop reads exactly like a lost test. **The
converse red does not do this at all:** one asserting the check SHOULD fire
and it does not stays an expected failure under the kill and contributes
nothing, because a dead detector does not satisfy it either. So "every red
adds one" is wrong in both directions — only silence-shaped reds count, and
each must be checked rather than counted.

Measured at `2cca618` across seven kills spanning both tiers: **no kill
produced a single unexpected success**, so the correction was ZERO at that
commit and every witness count above is a plain test count. It has not always
been: the originating spike's `ablation_continuity` row was three, one of
which it recorded as "a RED pin turning UNEXPECTED SUCCESS".

That measurement is deliberately NOT stated as a fraction of the suite's
reds, and the earlier draft that did state it that way is worth keeping as a
warning — including the fact that the first correction of it was also wrong.
The draft said "zero of the suite's 19 reds". **19 was real**: the whole
suite's run-time `expected failures` count at `bc48bb1`, the first baseline
taken here. It was carried by hand into a sentence dated to `2cca618`, where
the true figure was 25 (24 at `7cc146a`). So the number was never invented,
only MIS-DATED — and mis-dating is the failure mode that actually recurs in
this repo, which the first fix of this paragraph obscured by calling it a
tier-attribution error instead.

A red count is the wrong instrument even when it is current. Reds are added
and flipped constantly, only silence-shaped ones can ever contribute, and
flipping a NON-silence-shaped red moves the correction by nothing at all.
`tests/test_mutants.py` has carried a paragraph making this same argument
about its own red census since before this module existed: that a bare
`grep -c @unittest.expectedFailure` OVERCOUNTS because several matches are
prose, that the honest count is
`grep -cE '^\s*@unittest\.expectedFailure\s*$'` or the runner's own line, and
that the figure it used to quote "is derivable, it went stale twice", so it
was deleted rather than recounted. Two agents then reproduced that lesson
from scratch, in the file next door, by quoting a stale red count and
mis-diagnosing it with a bare grep. Read that paragraph before you count
anything here.

Better: do not count. Read the `unexpected` list the driver already reports
for the run in front of you. Never count reds.

AND THE RENDER TIER'S WITNESS COUNTS CARRY ±1 OF NOISE THAT IS NOT THE
DETECTOR'S. Measured directly: every render kill run TWICE at `2cca618`, same
copy, same machine. `ablation_existence` (17), `client_ablation_existence` (3)
and `parity_clipped` (3) reproduced their witness SETS exactly;
`ablation_continuity` came back 3 then 2, and `client_ablation_continuity` 3
then 4. **The kills themselves are deterministic — drop counts were identical
across every pair (2/2, 17/17, 4/4, 3/3, 3/3).** The whole difference is one
flaky test, `test_mutant_snapshot_cap_drops_the_rightmost_node`, which failed
in one run of each and is a witness to neither: it appeared under two
UNRELATED kills, and a snapshot cap has nothing to do with ablation
continuity. It passed the inert run, so subtracting the inert baseline — the
defence that exists for exactly this — did not catch it.

So: **read a render-tier witness count as "about this many", and diff the
witness SETS rather than the counts before believing a movement.** The driver
already reports the ids; `_bad` returns a set for this reason. The model
tier has no such caveat: all 16 of the model rows that existed at `2cca618`
reproduced byte-identically across runs at two different commits.

WHAT IT CANNOT PROVE, and the distinction is the whole reason rule 8 exists.
A NOTICED DEATH IS NOT A GOOD CONTROL. This module works at CHECK level: it
answers "does this check's death cost anything anywhere". It says nothing
about any INDIVIDUAL pin. A check whose death fails eight tests can still have
a ninth test that claims to guard it and does not — the check survives because
some OTHER test notices, and that ninth pin is decorative. The originating
spike found twenty-one such pins under a check-level result that was
spotlessly clean. That per-pin question is the curators' work, not this
module's: `coverage_table` answers "is this check proven anywhere",
`TestCoverage.test_every_catalogue_silence_has_a_firing_beside_it` answers it
for catalogue silences, and this answers "does its death cost anything". None
of the three answers "does THIS assertion mean what its docstring says".

RULE 8 APPLIES ONE LEVEL UP (docs/design/agent-operating-guide.md §7 rule 8):
a mutation harness needs its own liveness control. Two of the originating
spike's three kill implementations were silently wrong — a wrapper that broke
`inspect.getsource` and fabricated twelve "noticed" rows, and a tail-defined
global below a `__main__` guard that killed the CLI path and read as CPU
contention. Neither announced itself. So no row here is reported on trust;
three independent witnesses stand in front of every kill:

* INERTNESS. The instrumentation is installed UNCONDITIONALLY — every wrapper
  is in place on the inert run too, passing its arguments straight through —
  and the suite must then be green with the same test count as the same tree
  unpatched. A patched tree that is not inert invalidates every row after it,
  so the sweep raises rather than measuring kills. This is what catches a
  wrapper that breaks a `getsource` pin: it goes red with no kill selected,
  where it cannot be mistaken for a detector's death. (Wrappers carry
  `functools.wraps`, so `inspect.getsource` unwraps to the real source and the
  spike's first bad kill cannot recur in this form; inertness is what proves
  that rather than assumes it.)
* THE DROP COUNTER. Each kill counts the findings it actually destroyed during
  the run, and a kill that destroyed nothing is an ERROR, never a "nothing
  noticed" row. The count lives on disk, one byte per destroyed finding, for
  two reasons: the client tier renders through a `canvas.py` SUBPROCESS, where
  an in-memory counter reads zero for the wrong reason — that is exactly how
  the spike's second bad kill hid — and a byte already appended survives a
  server killed by signal.
* SURGICALITY (model tier only). A probe runs the catalogue's own scenes
  through `collect_findings` and requires the killed check to stop answering
  while EVERY OTHER check answers exactly what it did before. A kill that also
  blinded a second check would otherwise inflate the first one's witness list
  with the second one's tests, and nothing downstream could tell. "Stop
  answering" is measured as [findings, magnitude sum] rather than a count,
  because `_collect_crossings` emits one finding per scene whatever the answer
  is: blinding `edge_crossings` leaves its COUNT at 82 and moves only the
  number inside, which a count-only probe would have read as a kill that never
  bit. Every model-tier check fires somewhere in the catalogue, so every one
  of them carries this control — asserted per kill rather than pinned to a
  count here, because the count moves whenever a detector is registered. The
  render tier's three producers need a browser and
  `collect_findings` never calls them, so those five rows rest on inertness
  and the drop counter alone — said in their class docstring rather than left
  to be discovered.

A WITNESS, precisely: a test that fails (or errors, or turns unexpected
success — a red pin going green is a death noticed) under the kill and did NOT
under the inert run. The inert run is green, so the subtraction is a no-op by
construction; it is written down anyway, because the day it stops being a
no-op is the day a row would otherwise be fabricated.

COST. One tree copy, then one full suite run per kill: measured at `2cca618`,
~7 minutes for the model tier's 16 checks then and ~12 more for the render
tier's 5, and it grows by roughly one suite run per check added, with the kills
fanned out over `MUTANTS_MORTALITY_JOBS` workers (4 by default, 2 for the
render tier, whose runs each drive a browser). Nothing is shared between
workers that a test can observe: each gets its own `TMPDIR`, which is where
`canvas.py` puts its runtime directory.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mutants import DETECTORS

MORTALITY = os.environ.get("MUTANTS_MORTALITY") == "1"
RENDER = os.environ.get("MUTANTS_RENDER") == "1"
JOBS = max(1, int(os.environ.get("MUTANTS_MORTALITY_JOBS") or "4"))

_REPO = Path(__file__).resolve().parents[1]
_CANVAS = "skills/wysiwyg-grilling/scripts/canvas.py"
_SENTINEL = "MORTALITY-JSON "

# The four instrument checks, each naming the `tests/instruments.py` function
# that IS the check and the empty value it must return to go blind. The empty
# values are not uniform — `edge_crossings` answers `(count, pairs)` — which is
# why this is a table and not a loop over names.
_INSTRUMENTS: dict[str, tuple[str, str, str]] = {
    "crossings_count": ("edge_crossings", "(0, [])", "len(out[1])"),
    "shared_corridor": ("shared_corridors", "[]", "len(out)"),
    "false_bidi": ("false_bidi", "[]", "len(out)"),
    "float_diamond": ("float_diamond", "[]", "len(out)"),
}

# The render tier's five checks, each naming (producer, check-name-it-emits).
# KEYED ON THE PAIR AND NOT THE NAME, which is the trap here:
# `client_ablation_findings` emits `ablation_existence` and
# `ablation_continuity` — the SAME two names its tier-1 sibling emits. A kill
# that dropped findings by name alone would blind both tiers at once and
# report each one's witnesses as the other's.
_PRODUCERS: dict[str, tuple[str, str]] = {
    "ablation_existence": ("ablation_findings", "ablation_existence"),
    "ablation_continuity": ("ablation_findings", "ablation_continuity"),
    "parity_clipped": ("parity_findings", "parity_clipped"),
    "client_ablation_existence":
        ("client_ablation_findings", "ablation_existence"),
    "client_ablation_continuity":
        ("client_ablation_findings", "ablation_continuity"),
}

_LINT: tuple[str, ...] = tuple(sorted(
    n for n, s in DETECTORS.items() if s.get("lint_re") is not None))
_MODEL_KILLS: tuple[str, ...] = _LINT + tuple(sorted(_INSTRUMENTS))
_RENDER_KILLS: tuple[str, ...] = tuple(sorted(_PRODUCERS))

# `canvas.py` anchors. The first is unique in the file; the second is NOT —
# `lint_glossary` returns the identical line — so it is resolved as the first
# occurrence AFTER the first, never by a bare search.
_DEF_LINT = ("def lint_layout(els, artifact_type=None, budget=None, "
             "waives=None,\n")
_RET_LINT = '    return {"errors": errors, "warnings": warnings, "notes": notes}\n'

# Inserted just above that return, INSIDE the function body. Inside and not
# around it, because `TestCoverage.test_lint_layout_append_count_is_pinned`
# reads `inspect.getsource(canvas.lint_layout)`; and these three lines are
# free of the substring `.append` that the pin counts, so the count stands.
_FILTER_LINT = ("    errors = _mort_filter(errors)\n"
                "    warnings = _mort_filter(warnings)\n"
                "    notes = _mort_filter(notes)\n")

_PREAMBLE_LINT = '''\
# --- detector-mortality instrumentation (tests/mutants_mortality.py) -------
# Injected into a THROWAWAY COPY of canvas.py. Defined HERE, above
# `lint_layout` and 6000 lines above the `__main__` guard, because state
# defined at the file tail is never executed on the script path — the CLI and
# the server raise `NameError`, which reads exactly like environmental
# flakiness. That cost the originating spike a cycle.
import os as _mort_os
import re as _mort_re

_MORT_KILL = _mort_os.environ.get("MORT_KILL") or ""
_MORT_LOG = _mort_os.environ.get("MORT_LOG") or ""
_MORT_PATTERNS = {
%(patterns)s}
_MORT_RE = _MORT_PATTERNS.get(_MORT_KILL)


def _mort_note(gone):
    """Record `gone` destroyed findings, one byte each, appended not summed.

    Args:
        gone: How many findings this call destroyed.
    """
    if gone > 0 and _MORT_LOG:
        with open(_MORT_LOG, "a") as fh:
            fh.write("x" * gone)


def _mort_filter(lines):
    """Drop the killed check's own lint lines, counting what it destroys.

    Matched by the detector's OWN compiled regex, taken from `DETECTORS` when
    the copy was built, so the kill and `collect_findings` cannot disagree
    about which line belongs to which check. Filtering the prose (rather than
    the parsed finding) is what also blinds the tests that grep lint output
    directly, which is most of them.

    Args:
        lines: One of `lint_layout`'s three channels.

    Returns:
        The channel unchanged when no kill is selected, else without the
        killed check's lines.
    """
    if _MORT_RE is None:
        return lines
    kept = [ln for ln in lines if not _MORT_RE.search(ln)]
    _mort_note(len(lines) - len(kept))
    return kept


# --- end detector-mortality instrumentation --------------------------------
'''

_PATCH_INSTRUMENTS = '''

# --- detector-mortality instrumentation (tests/mutants_mortality.py) -------
# Appended to a THROWAWAY COPY. This file has no `__main__` guard, so the tail
# is reached on every path that imports it.
import functools as _mort_ft
import os as _mort_os

_MORT_KILL = _mort_os.environ.get("MORT_KILL") or ""
_MORT_LOG = _mort_os.environ.get("MORT_LOG") or ""


def _mort_note(gone):
    """Record `gone` destroyed findings, one byte each.

    Args:
        gone: How many findings this call destroyed.
    """
    if gone > 0 and _MORT_LOG:
        with open(_MORT_LOG, "a") as fh:
            fh.write("x" * gone)


def _mort_blind(fn, kill, empty, size):
    """Wrap an instrument so the selected kill makes it answer nothing.

    The real function still RUNS — its answer is what the drop counter counts
    before throwing it away, so the count is measured rather than assumed.
    Every instrument is wrapped whether or not it is the one being killed, so
    the inert run exercises this wrapper too and a wrapper that breaks
    something goes red where no kill can be blamed for it.

    Args:
        fn: The real instrument function.
        kill: True when this instrument is the selected kill.
        empty: The value a blind instrument returns.
        size: Callable turning the real answer into a count of findings.

    Returns:
        The wrapped function; `functools.wraps` keeps `inspect.getsource`
        pointed at the real one.
    """
    @_mort_ft.wraps(fn)
    def wrapped(*a, **kw):
        out = fn(*a, **kw)
        if not kill:
            return out
        _mort_note(size(out))
        return empty
    return wrapped


%(wraps)s
# --- end detector-mortality instrumentation --------------------------------
'''

_PATCH_RENDER = '''

# --- detector-mortality instrumentation (tests/mutants_mortality.py) -------
# Appended to a THROWAWAY COPY. This file has no `__main__` guard.
import functools as _mort_ft
import os as _mort_os

_MORT_KILL = _mort_os.environ.get("MORT_KILL") or ""
_MORT_LOG = _mort_os.environ.get("MORT_LOG") or ""
_MORT_PRODUCERS = %(producers)r
_MORT_SELECTED = _MORT_PRODUCERS.get(_MORT_KILL)


def _mort_note(gone):
    """Record `gone` destroyed findings, one byte each.

    Args:
        gone: How many findings this call destroyed.
    """
    if gone > 0 and _MORT_LOG:
        with open(_MORT_LOG, "a") as fh:
            fh.write("x" * gone)


def _mort_blind_producer(fn, check):
    """Wrap a findings producer so `check` disappears from what it reports.

    Args:
        fn: The real producer (`ablation_findings` and friends).
        check: The check name to drop, or None to pass everything through.

    Returns:
        The wrapped producer; `functools.wraps` keeps `inspect.getsource`
        pointed at the real one.
    """
    @_mort_ft.wraps(fn)
    def wrapped(*a, **kw):
        out = fn(*a, **kw)
        if check is None:
            return out
        kept = [f for f in out if f.get("check") != check]
        _mort_note(len(out) - len(kept))
        return kept
    return wrapped


for _mort_name in ("ablation_findings", "parity_findings",
                   "client_ablation_findings"):
    _mort_want = (_MORT_SELECTED[1] if _MORT_SELECTED is not None
                  and _MORT_SELECTED[0] == _mort_name else None)
    globals()[_mort_name] = _mort_blind_producer(globals()[_mort_name],
                                                 _mort_want)
# --- end detector-mortality instrumentation --------------------------------
'''

# The driver runs INSIDE the copy: the parent never imports the killed tree.
# It reports test ids rather than a pass/fail, because the witness list is the
# deliverable and a bare exit code would throw it away.
_DRIVER = '''"""Run the copy's own suite (or a catalogue probe) and report JSON."""
import io
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"


def drops():
    """Bytes appended by the kill: one per destroyed finding."""
    try:
        return os.path.getsize(os.environ.get("MORT_LOG") or "")
    except OSError:
        return 0


def probe():
    """Per check, [findings, magnitude sum] over every catalogue scene.

    The mutant scene is the OPERATOR APPLIED to its builder, exactly as
    `TestMutantCatalogue` runs it — the builder alone is the healthy scene,
    and half the catalogue's checks never fire on those.

    The magnitude sum is carried because a count alone cannot see every
    death: `_collect_crossings` emits one finding per scene whatever the
    answer is, so a blinded `edge_crossings` leaves the COUNT untouched and
    moves only the number inside.
    """
    sys.path.insert(0, str(TESTS))
    import test_mutants as tm
    sig = {}

    def tally(elements):
        for finding in tm.collect_findings(elements):
            row = sig.setdefault(finding["check"], [0, 0.0])
            row[0] += 1
            row[1] += finding["magnitude"] or 0

    for mutant in tm.CATALOGUE.values():
        tally(tm.OPERATORS[mutant.op](mutant.build(), **mutant.args))
        if mutant.neighbour is not None:
            tally(mutant.neighbour.build())
    return {"counts": {k: [v[0], round(v[1], 6)] for k, v in sig.items()}}


def suite():
    """Every test in the copy, by id, split into the ways they can go bad."""
    loader = unittest.TestLoader()
    tests = loader.discover(str(TESTS), top_level_dir=str(TESTS))
    result = unittest.TextTestRunner(stream=io.StringIO(),
                                     verbosity=0).run(tests)
    return {"total": result.testsRun,
            "failures": sorted(t.id() for t, _ in result.failures),
            "errors": sorted(t.id() for t, _ in result.errors),
            "unexpected": sorted(t.id() for t in result.unexpectedSuccesses),
            "skipped": len(result.skipped)}


out = probe() if sys.argv[1] == "probe" else suite()
out["drops"] = drops()
sys.stdout.write("%(sentinel)s" + json.dumps(out) + "\\n")
'''


class MortalityError(RuntimeError):
    """The instrument is broken, so no row it would report can be believed."""


def _lint_patterns() -> str:
    """Render the lint checks' own regexes as a literal table for the patch.

    Derived from `DETECTORS` at build time rather than hand-copied, so the
    kill matches exactly what `collect_findings` matches and the table cannot
    go stale behind a renamed check or a reworded message.

    Returns:
        Python source for the body of the `_MORT_PATTERNS` dict literal.
    """
    return "".join("    %r: _mort_re.compile(%r, %d),\n"
                   % (name, DETECTORS[name]["lint_re"].pattern,
                      DETECTORS[name]["lint_re"].flags) for name in _LINT)


def _splice(text: str, anchor: str, insert: str, after: int = 0) -> str:
    """Insert `insert` immediately before `anchor`, proving the anchor is one.

    Args:
        text: The file's full source.
        anchor: The exact text to insert in front of.
        insert: The text to insert.
        after: Only consider occurrences at or beyond this offset.

    Returns:
        The spliced source.

    Raises:
        MortalityError: If the anchor is missing, or ambiguous within the
            window being searched.
    """
    at = text.find(anchor, after)
    if at < 0:
        raise MortalityError("anchor not found in the copy: %r" % anchor[:60])
    if not after and text.count(anchor) != 1:
        raise MortalityError("anchor is not unique (%d hits): %r"
                             % (text.count(anchor), anchor[:60]))
    return text[:at] + insert + text[at:]


def _instrument(root: Path) -> None:
    """Install the kill machinery in a copy, killing nothing by itself.

    Three files are patched: `canvas.py` gains the lint filter inside
    `lint_layout`, `tests/instruments.py` gains wrappers on the four
    instruments, and `tests/test_mutants_render.py` gains wrappers on the
    three findings producers. Which check dies is chosen at RUN time by
    `MORT_KILL`, so one copy serves every kill — and serves the inert run
    that proves the machinery itself changes nothing.

    Every anchor goes through `_splice`, which raises `MortalityError` rather
    than patch the wrong place: a splice that lands in `lint_glossary` would
    kill nothing and read as a check nobody guards.

    Args:
        root: The copy's root directory.
    """
    canvas = root / _CANVAS
    src = canvas.read_text(encoding="utf-8")
    src = _splice(src, _DEF_LINT, _PREAMBLE_LINT % {"patterns": _lint_patterns()})
    src = _splice(src, _RET_LINT, _FILTER_LINT,
                  after=src.index(_DEF_LINT) + len(_DEF_LINT))
    canvas.write_text(src, encoding="utf-8")

    wraps = "".join(
        "%s = _mort_blind(%s, _MORT_KILL == %r, %s, lambda out: %s)\n"
        % (fn, fn, check, empty, size)
        for check, (fn, empty, size) in sorted(_INSTRUMENTS.items()))
    with (root / "tests" / "instruments.py").open("a", encoding="utf-8") as fh:
        fh.write(_PATCH_INSTRUMENTS % {"wraps": wraps})
    with (root / "tests" / "test_mutants_render.py").open(
            "a", encoding="utf-8") as fh:
        fh.write(_PATCH_RENDER % {"producers": _PRODUCERS})


def _install_driver(root: Path) -> None:
    """Put the reporting driver in a copy.

    Both copies need it, not just the instrumented one — the pristine control
    is run through the same code path.

    Args:
        root: The copy's root directory.
    """
    (root / "_mortality_driver.py").write_text(
        _DRIVER % {"sentinel": _SENTINEL}, encoding="utf-8")


def _copy_tree(dest: Path) -> str:
    """Copy every TRACKED file at its working-tree content into `dest`.

    Tracked and not `git archive HEAD`, because the useful question is
    whether the code you have RIGHT NOW is guarded — uncommitted edits
    included. Tracked and not a plain directory copy, because `.venv`,
    `__pycache__` and the render cache are none of the sweep's business.

    Args:
        dest: Directory to build the copy in; created if absent.

    Returns:
        A one-line provenance string naming the commit and any dirty paths,
        so a row is never attributed to the wrong tree. Recorded because the
        tree moved three times under the originating spike, and one of those
        commits was the repair of the defect it was hunting.
    """
    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=str(_REPO), text=True,
                              capture_output=True)
        if done.returncode:
            raise MortalityError("git %s failed: %s"
                                 % (" ".join(args), done.stderr.strip()))
        return done.stdout

    for rel in git("ls-files", "-z").split("\0"):
        if not rel:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REPO / rel, target)
    dirty = sorted(ln[3:] for ln in git("status", "--porcelain").splitlines()
                   if ln[3:] and not ln.startswith("??"))
    return "%s%s" % (git("rev-parse", "HEAD").strip(),
                     " + dirty: %s" % ", ".join(dirty) if dirty else "")


def _run(root: Path, mode: str, kill: str, render: bool) -> dict[str, Any]:
    """Run the driver once in the copy and return its JSON report.

    Args:
        root: The instrumented copy's root.
        mode: "suite" or "probe".
        kill: The check to kill, or "" for the inert run.
        render: Whether to set `MUTANTS_RENDER=1` for this run.

    Returns:
        The driver's report, with `drops` added by the driver itself.

    Raises:
        MortalityError: If the driver crashed or printed no report.
    """
    env = dict(os.environ)
    env.pop("MUTANTS_MORTALITY", None)
    with tempfile.TemporaryDirectory(prefix="mort-run-") as scratch:
        env["TMPDIR"] = scratch
        env["MORT_KILL"] = kill
        env["MORT_LOG"] = str(Path(scratch) / "drops")
        env["MUTANTS_RENDER"] = "1" if render else ""
        done = subprocess.run([sys.executable, "_mortality_driver.py", mode],
                              cwd=str(root), env=env, text=True,
                              capture_output=True)
        for line in done.stdout.splitlines():
            if line.startswith(_SENTINEL):
                return json.loads(line[len(_SENTINEL):])
    raise MortalityError("driver produced no report for kill=%r mode=%r "
                         "(rc=%d)\n%s" % (kill, mode, done.returncode,
                                          done.stderr[-2000:]))


def _bad(report: dict[str, Any]) -> set[str]:
    """Every test id the run went wrong on, however it went wrong.

    An unexpected success counts: a red pin that turns green when a detector
    dies is that detector's death being noticed, loudly.

    Args:
        report: A driver suite report.

    Returns:
        The union of failures, errors and unexpected successes.
    """
    return set(report["failures"]) | set(report["errors"]) | set(
        report["unexpected"])


class Sweep:
    """One built copy, its two controls, and the measured kills.

    Attributes:
        root: The instrumented copy.
        provenance: Commit and dirty paths the copy was taken from.
        pristine: The unpatched run's report, per tier.
        inert: The patched-but-unkilled run's report, per tier.
        kills: Per check, its suite report.
        probes: Per check (model tier only), its catalogue finding counts.
        baseline_probe: The same counts with no kill selected.
    """

    def __init__(self, root: Path, provenance: str) -> None:
        """Record where the copy is; `measure` fills the rest in.

        Args:
            root: The instrumented copy's root.
            provenance: Commit and dirty paths the copy was taken from.
        """
        self.root, self.provenance = root, provenance
        self.pristine: dict[str, dict[str, Any]] = {}
        self.inert: dict[str, dict[str, Any]] = {}
        self.kills: dict[str, dict[str, Any]] = {}
        self.probes: dict[str, dict[str, int]] = {}
        self.baseline_probe: dict[str, int] = {}

    def witnesses(self, check: str) -> set[str]:
        """The tests that noticed `check` dying and were fine without it.

        Args:
            check: The killed check.

        Returns:
            Test ids that went bad under the kill but not on the inert run.
        """
        tier = "render" if check in _RENDER_KILLS else "model"
        return _bad(self.kills[check]) - _bad(self.inert[tier])


def _control(sweep: Sweep, tier: str, render: bool) -> None:
    """Run and check a tier's two controls before any kill is believed.

    Args:
        sweep: The sweep to fill in.
        tier: "model" or "render".
        render: Whether this tier needs `MUTANTS_RENDER=1`.

    Raises:
        MortalityError: If the unpatched copy is not green, or if the
            instrumentation is not inert against it.
    """
    pristine = _run(sweep.root.parent / "pristine", "suite", "", render)
    if _bad(pristine):
        raise MortalityError(
            "the %s tier is not green BEFORE instrumentation, so nothing "
            "below it can be measured: %s"
            % (tier, ", ".join(sorted(_bad(pristine))[:5])))
    inert = _run(sweep.root, "suite", "", render)
    if _bad(inert):
        raise MortalityError(
            "the instrumentation is NOT INERT on the %s tier — it goes red "
            "with no kill selected, so every row would be a fabrication: %s"
            % (tier, ", ".join(sorted(_bad(inert))[:5])))
    if inert["total"] != pristine["total"]:
        raise MortalityError(
            "the instrumentation changed the %s tier's population "
            "(%d -> %d): tests it silently dropped cannot notice anything"
            % (tier, pristine["total"], inert["total"]))
    sweep.pristine[tier], sweep.inert[tier] = pristine, inert


def _fan(jobs: int, work: Iterable[tuple[str, tuple[Any, ...]]],
         call: Any) -> dict[str, Any]:
    """Run `call` over `work` on a small thread pool, keyed by name.

    Args:
        jobs: Worker count.
        work: (name, args) pairs.
        call: Callable applied to each args tuple.

    Returns:
        Each name mapped to its result.
    """
    items = list(work)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {name: pool.submit(call, *args) for name, args in items}
        return {name: fut.result() for name, fut in futures.items()}


_SWEEP: Sweep | None = None
_FAILED: BaseException | None = None


def sweep() -> Sweep:
    """Build the copy and measure every kill, once per process.

    The controls run first and the kills are not measured at all if either
    fails — an instrument that cannot prove it is inert does not get to
    report rows.

    A control failure is cached and re-raised, not retried: the second test
    to ask would otherwise pay another twenty minutes to be told the same
    thing.

    Returns:
        The completed sweep.
    """
    global _SWEEP, _FAILED
    if _FAILED is not None:
        raise _FAILED
    if _SWEEP is not None:
        return _SWEEP
    try:
        _SWEEP = _measure()
    except BaseException as exc:
        _FAILED = exc
        raise
    return _SWEEP


def _measure() -> Sweep:
    """Do the work `sweep` caches: copy, instrument, control, kill.

    The kills are fanned out only AFTER `_control` has returned for their
    tier, which is the ordering that makes inertness a precondition rather
    than a report.

    Returns:
        The completed sweep.
    """
    base = Path(tempfile.mkdtemp(prefix="mortality-"))
    provenance = _copy_tree(base / "pristine")
    shutil.copytree(base / "pristine", base / "killed")
    _install_driver(base / "pristine")
    built = Sweep(base / "killed", provenance)
    _instrument(built.root)
    _install_driver(built.root)

    _control(built, "model", render=False)
    built.baseline_probe = _run(built.root, "probe", "", False)["counts"]
    built.kills.update(_fan(
        JOBS, ((c, (built.root, "suite", c, False)) for c in _MODEL_KILLS),
        _run))
    built.probes.update({
        c: r["counts"] for c, r in _fan(
            JOBS, ((c, (built.root, "probe", c, False)) for c in _MODEL_KILLS),
            _run).items()})
    if RENDER:
        _control(built, "render", render=True)
        built.kills.update(_fan(
            min(2, JOBS),
            ((c, (built.root, "suite", c, True)) for c in _RENDER_KILLS),
            _run))
    return built


@unittest.skipUnless(MORTALITY, "mortality sweep: set MUTANTS_MORTALITY=1 "
                                "(it re-runs the whole suite per check)")
class TestHarnessLiveness(unittest.TestCase):
    """The instrument's own liveness controls — rule 8 one level up.

    Everything in the two classes below rests on these. They are separated
    out rather than folded into `setUpClass` so that a broken instrument
    reports as a broken instrument, by name, instead of as a wall of dead
    detectors nobody can distinguish from the real thing.
    """

    def test_the_unpatched_copy_is_green_and_the_patch_changes_nothing(
            self) -> None:
        """Both controls, stated as the assertion rather than the setup.

        `sweep()` refuses to measure a kill when either control fails, so by
        the time this runs the answer is already known; asserting it here is
        what puts the failure under a name that says which of the two broke.
        """
        built = sweep()
        for tier, pristine in built.pristine.items():
            self.assertEqual(_bad(pristine), set(), "%s pristine" % tier)
            self.assertEqual(_bad(built.inert[tier]), set(),
                             "%s inert" % tier)
            self.assertEqual(built.inert[tier]["total"], pristine["total"],
                             "%s population moved under the patch" % tier)

    def test_the_drop_counter_reads_zero_when_a_kill_bites_nothing(
            self) -> None:
        """The witness's own witness: prove the counter can say "nothing".

        A drop counter that could only ever count up would pass every kill
        whatever it did, which is the second-absence mistake wearing the
        harness's clothes. So: select a check that does not exist, run the
        probe, and require the count to be ZERO. Every real kill below
        asserts the same counter is non-zero, and the two together are what
        make it evidence.
        """
        built = sweep()
        idle = _run(built.root, "probe", "no-such-check-exists", False)
        self.assertEqual(idle["drops"], 0,
                         "a kill naming no check destroyed findings anyway")
        self.assertEqual(idle["counts"], built.baseline_probe,
                         "an unselected kill moved the findings")

    def test_every_registered_check_is_swept(self) -> None:
        """A check with no kill implementation is a check this never reads.

        The failure mode is silent by nature: register a twenty-second
        detector, and the sweep goes on reporting twenty-one clean rows
        while the new one is guarded by nothing at all. Cheap, so it is
        asserted rather than trusted.
        """
        swept = set(_MODEL_KILLS) | set(_RENDER_KILLS)
        registered = {n for n, s in DETECTORS.items() if not s.get("render")}
        self.assertEqual(registered - swept, set(),
                         "registered detectors with no kill implementation")
        rendered = {n for n, s in DETECTORS.items() if s.get("render")}
        self.assertEqual(rendered - swept, set(),
                         "render detectors with no kill implementation")


class _Mortal(unittest.TestCase):
    """Shared assertions for one killed check.

    Every row goes through `assertMortal`, so no row can be reported without
    its kill having been proven to take effect first.
    """

    def assertKillTookEffect(self, check: str) -> None:
        """Require the kill to have destroyed findings, and only its own.

        Args:
            check: The killed check.
        """
        built = sweep()
        self.assertGreater(
            built.kills[check]["drops"], 0,
            "the %r kill destroyed NOTHING during the run, so this row is "
            "about a kill that never bit — not about a check nobody guards"
            % check)
        if check not in built.probes:
            return
        probe, base = built.probes[check], built.baseline_probe
        self.assertEqual(probe.get("detector-error", [0, 0])[0], 0,
                         "the %r kill made a detector CRASH; a crash is not "
                         "a silence, and the findings it loses are not this "
                         "check's" % check)
        self.assertGreater(base.get(check, [0, 0])[0], 0,
                           "no catalogue scene makes %r fire, so the probe "
                           "cannot watch it go quiet" % check)
        self.assertNotEqual(probe.get(check), base.get(check),
                            "%r answers exactly what it did before its own "
                            "kill, so the kill did not reach it" % check)
        self.assertEqual(probe.get(check, [0, 0])[1], 0,
                         "%r still reports measured magnitudes after its "
                         "kill" % check)
        collateral = {n: (base.get(n), probe.get(n))
                      for n in set(base) | set(probe)
                      if n != check and base.get(n) != probe.get(n)}
        self.assertEqual(collateral, {},
                         "the %r kill also moved OTHER checks, so its "
                         "witness list is not all its own" % check)

    def assertMortal(self, check: str) -> None:
        """Require `check`'s death to be noticed by a behavioural test.

        Args:
            check: The check to kill.
        """
        self.assertKillTookEffect(check)
        seen = sweep().witnesses(check)
        self.assertTrue(seen, "NOTHING notices %r going blind: every test "
                              "that names it passes with the detector dead"
                        % check)


@unittest.skipUnless(MORTALITY, "mortality sweep: set MUTANTS_MORTALITY=1")
class TestModelTierMortality(_Mortal):
    """The 15 lint checks and 4 instruments, killed one at a time.

    These rows carry all three controls: inertness, the drop counter, and the
    surgicality probe over the catalogue's own scenes.
    """

    def test_endpoint_gap_death_is_noticed(self) -> None:
        """An arrow's endpoint gap going unreported must break something."""
        self.assertMortal("endpoint_gap")

    def test_runs_on_node_death_is_noticed(self) -> None:
        """An arrow crossing its own bound node, unreported."""
        self.assertMortal("runs_on_node")

    def test_passes_through_foreign_death_is_noticed(self) -> None:
        """An arrow crossing a node it has no business in, unreported."""
        self.assertMortal("passes_through_foreign")

    def test_shared_attach_point_death_is_noticed(self) -> None:
        """Two arrows landing on one point, unreported."""
        self.assertMortal("shared_attach_point")

    def test_phantom_passthrough_death_is_noticed(self) -> None:
        """A pair reading as one line through a node, unreported."""
        self.assertMortal("phantom_passthrough")

    def test_label_label_overlap_death_is_noticed(self) -> None:
        """Two labels written over each other, unreported."""
        self.assertMortal("label_label_overlap")

    def test_label_on_foreign_node_death_is_noticed(self) -> None:
        """An arrow's label parked on somebody else's box, unreported."""
        self.assertMortal("label_on_foreign_node")

    def test_text_overflow_death_is_noticed(self) -> None:
        """Text wider or taller than what holds it, unreported."""
        self.assertMortal("text_overflow")

    def test_label_overflows_shape_death_is_noticed(self) -> None:
        """A label wider than its shape's chord, unreported."""
        self.assertMortal("label_overflows_shape")

    def test_text_overlaps_node_death_is_noticed(self) -> None:
        """A roled text lying over a node, unreported."""
        self.assertMortal("text_overlaps_node")

    def test_annotation_overlaps_node_death_is_noticed(self) -> None:
        """A role-less annotation lying over a node, unreported."""
        self.assertMortal("annotation_overlaps_node")

    def test_min_clearance_death_is_noticed(self) -> None:
        """Two elements crowded past the clearance floor, unreported."""
        self.assertMortal("min_clearance")

    def test_shared_lane_death_is_noticed(self) -> None:
        """Two arrows drawn in one lane, unreported."""
        self.assertMortal("shared_lane")

    def test_false_bidirectional_death_is_noticed(self) -> None:
        """A pair reading as one bidirectional edge, unreported."""
        # The LIVE lint, not `false_bidi` below. Both rows exist and both
        # must die separately: killing the instrument leaves the agent
        # still warned, and killing the lint leaves the score vector
        # still right — which is exactly the split the promotion created
        # and the reason the two carry different names.
        self.assertMortal("false_bidirectional")

    def test_orphan_label_death_is_noticed(self) -> None:
        """A caption left inked over a hidden host, unreported."""
        self.assertMortal("orphan_label")

    def test_contrast_text_death_is_noticed(self) -> None:
        """Ink a reader cannot make out on the paper, unreported."""
        # `_LINT` derives from DETECTORS, so these three joined the sweep
        # the moment they were registered; the rows are here so each has
        # a NAMED witness assertion rather than only the derived kill.
        self.assertMortal("contrast_text")

    def test_contrast_object_death_is_noticed(self) -> None:
        """A stroke a reader cannot pick out of the paper, unreported."""
        self.assertMortal("contrast_object")

    def test_min_font_death_is_noticed(self) -> None:
        """Type below the measured legibility floor, unreported."""
        self.assertMortal("min_font")

    def test_unreadable_color_death_is_noticed(self) -> None:
        """A colour the check cannot read, silently skipped again."""
        # The fourth of that family and the one whose row could not be
        # OBSERVED until curator batch 33: `_LINT` derives from
        # `DETECTORS`, so this check joined the sweep on the day it was
        # registered, but `assertKillTookEffect`'s `base.get(check) > 0`
        # arm is only reached from a named method like this one — and
        # until `unreadable_stroke_is_reported_not_skipped` joined the
        # CATALOGUE there was no scene in it that made the check fire,
        # so `probe()` had nothing to watch go quiet. Landing the pair
        # and this row together is what the TASK-COLORPARSE review asked
        # for; landing this row alone would have asserted a surgicality
        # probe over an empty baseline (2026-08-18).
        self.assertMortal("unreadable_color")

    def test_crossings_count_death_is_noticed(self) -> None:
        """`edge_crossings` answering "none", everywhere."""
        self.assertMortal("crossings_count")

    def test_shared_corridor_death_is_noticed(self) -> None:
        """`shared_corridors` answering "none", everywhere."""
        self.assertMortal("shared_corridor")

    def test_false_bidi_death_is_noticed(self) -> None:
        """`false_bidi` answering "none", everywhere."""
        self.assertMortal("false_bidi")

    def test_float_diamond_death_is_noticed(self) -> None:
        """`float_diamond` answering "none", everywhere."""
        self.assertMortal("float_diamond")


@unittest.skipUnless(MORTALITY and RENDER,
                     "mortality sweep, render tier: set MUTANTS_MORTALITY=1 "
                     "and MUTANTS_RENDER=1")
class TestRenderTierMortality(_Mortal):
    """The three render producers' five checks, killed one at a time.

    These five rows rest on inertness and the drop counter ONLY. There is no
    surgicality probe for them: `collect_findings` never calls a render
    producer — they measure pixels, and the pixels need a browser — so the
    catalogue probe cannot see these checks fire or go quiet. What stands in
    its place is the producer/check PAIR the kill is keyed on, which is what
    keeps the client tier's two checks distinct from tier 1's identically
    named two.
    """

    def test_ablation_existence_death_is_noticed(self) -> None:
        """Tier 1 failing to notice an element that leaves no ink."""
        self.assertMortal("ablation_existence")

    def test_ablation_continuity_death_is_noticed(self) -> None:
        """Tier 1 failing to notice a connector cut into pieces."""
        self.assertMortal("ablation_continuity")

    def test_parity_clipped_death_is_noticed(self) -> None:
        """Tier 1 failing to notice its own frame cutting its own ink."""
        self.assertMortal("parity_clipped")

    def test_client_ablation_existence_death_is_noticed(self) -> None:
        """The CLIENT's reading failing to notice an inkless element."""
        self.assertMortal("client_ablation_existence")

    def test_client_ablation_continuity_death_is_noticed(self) -> None:
        """The CLIENT's reading failing to notice a severed connector."""
        self.assertMortal("client_ablation_continuity")


def report() -> str:
    """The sweep's table: witnesses and drops per check, in one block.

    The witness COUNT is the output nothing else in the repo produces, and a
    check that drops to one witness is worth a look even when it passes — so
    the table is printed on a successful run rather than buried in a failure
    message.

    Returns:
        A printable report.
    """
    built = sweep()
    rows = ["mortality sweep over %s" % built.provenance,
            "%-30s %7s %8s %s" % ("check", "drops", "witness", "tier")]
    for tier, checks in (("model", _MODEL_KILLS), ("render", _RENDER_KILLS)):
        for check in checks:
            if check not in built.kills:
                continue
            rows.append("%-30s %7d %8d %s"
                        % (check, built.kills[check]["drops"],
                           len(built.witnesses(check)), tier))
    return "\n".join(rows)


if __name__ == "__main__":
    if not MORTALITY:
        sys.stderr.write(
            "the mortality sweep re-runs the whole suite once per registered "
            "check, so it is opt-in:\n"
            "  MUTANTS_MORTALITY=1 python3 tests/mutants_mortality.py\n"
            "  MUTANTS_MORTALITY=1 MUTANTS_RENDER=1 python3 "
            "tests/mutants_mortality.py   # + the render tier\n")
        raise SystemExit(2)
    _RESULT = unittest.main(exit=False, verbosity=2).result
    if _SWEEP is not None:
        sys.stdout.write("\n" + report() + "\n")
    raise SystemExit(0 if _RESULT.wasSuccessful() else 1)
