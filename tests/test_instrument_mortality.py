r"""Acceptance for the class: an instrument that cannot report its own death.

ONE QUESTION, ASKED OF SEVEN INSTRUMENTS. *What would this say if the
thing it watches were entirely absent?* If the answer is "pass", it is
not an instrument — it is a sentence that happens to be true today. The
seven fixes folded at `4ac8778` (branch `instrument-mortality`,
`7fd48f1` and `b00da0f`) each closed one member of that class, and
shipped without acceptance tests by their author's own charter. These
are those tests.

WHY A NAIVE RED IS WORTH NOTHING HERE. The fixes are already in the
tree, so a test written against HEAD is born green and proves only that
it was written after the fix. Every case below was therefore proved by
RESTORING THE PRE-FIX WORLD and watching it fail BY ASSERTION — the
restoration is named in each class's docstring, and is either a reverse
apply of the fix's own hunks (`git show <sha> -- <path> | git apply -R`)
or a monkeypatch re-installing the pre-fix implementation. Where more
than one honest repair exists, the case was re-run under each, so that
no case has quietly prescribed the particular repair that landed.

WHAT EACH CASE OWES. A red half — the instrument, deprived of its
subject, must say something — AND a silent half, because a floor that
fires when its subject is present is worse than no floor at all. Read
the pairs: `..._is_refused` / `..._is_accepted`,
`..._fails_when_the_corpus_is_empty` / `..._is_silent_over_a_corpus_of_
one`, and the parser-liveness case under the hook reader, which exists
because a config reader that returns nothing passes every predicate
below vacuously. That is this module's own subject, one level up.

ORIGIN: written 2026-08-20 against the fold candidate `4ac8778`, by a
curator who did not write the fixes — which is the point. An acceptance
test and the fix it accepts coming from the same hands is the pattern
that shipped run 5's failure-path defects.

NO RED-BY-INTENT LIVES HERE. Every case is green at `4ac8778`; the
fixes landed, and a pin for a landed fix is green or it is wrong. The
class carries no `@unittest.expectedFailure`, so
`HAND_AUTHORED_RED_CLASSES` in `tests/test_mutants.py` gains nothing
from this file — by its own guard, not by hand.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import guard_mutants
import instruments
import livedoc
import test_backend
import test_mutants

REPO = Path(__file__).resolve().parents[1]
CORPUS = Path(__file__).resolve().parent / "fixtures"

# The three corpus walks fixed by `b00da0f`, as (module, class, method).
# Named rather than discovered: a discovery rule would silently shrink to
# nothing the day a class is renamed, which is the defect this file is
# about. A rename breaks the import here, loudly.
CORPUS_WALKS: tuple[tuple[Any, str, str], ...] = (
    (test_backend, "TestFanAttachPoints",
     "test_the_search_adds_no_crossing_and_no_corridor"),
    (test_backend, "TestPhantomPassThrough",
     "test_the_frozen_fixtures_have_no_phantom_pass_throughs"),
    (test_mutants, "TestThePinHugSurvivesItsOwnContainer",
     "test_the_corpus_carries_no_soft_deleted_shape"),
)

# A module that exists only to be named by the guard census. Two dotted
# components because `guard_mutants._DRIVER` imports
# `name.split(".")[0] + "." + name.split(".")[1]` — the shape of the real
# `tests.test_backend`.
_STUB = '''"""A throwaway module the guard census can name."""
import unittest


class TestStub(unittest.TestCase):
    """One test that passes, one that skips, one that fails."""

    def test_lives(self):
        """Pass, so a roster naming it is healthy."""

    @unittest.skip("so the SKIPPED verdict has a real subject")
    def test_skips(self):
        """Skip."""

    def test_dies(self):
        """Fail, so the KILLED verdict has a real subject."""
        raise AssertionError("the mutation was observed")
'''


class _CorpusAnchor:
    """Stand in for `Path(__file__)` so a walk resolves onto a fake tree.

    The three corpus walks each compute their root as
    `Path(__file__).resolve().parent / "fixtures"`. Redirecting THAT
    expression — rather than deleting 24 files from a real checkout —
    is what makes "the corpus is gone" a sub-second in-process
    condition instead of a tree copy and a subprocess. `resolve` and
    `parent` are the only two attributes the expression touches, so
    they are the only two this carries: anything else reaching for a
    real `Path` method gets an `AttributeError`, which is the loud
    answer rather than a plausible wrong one.
    """

    def __init__(self, root: Path) -> None:
        """Remember the directory this anchor's `parent` reports.

        Args:
            root: The directory a redirected walk should treat as the
                one holding `fixtures/`.
        """
        self._root = root

    def resolve(self) -> _CorpusAnchor:
        """Answer itself, as `Path.resolve` answers a path.

        Returns:
            This anchor.
        """
        return self

    @property
    def parent(self) -> Path:
        """The directory the redirected walk will look inside.

        Returns:
            A real `Path`, so the walk's `/ "fixtures"` and `rglob` are
            the real ones and only the location is synthetic.
        """
        return self._root


@contextlib.contextmanager
def corpus_of(module: Any, artifacts: tuple[Path, ...]) -> Iterator[Path]:
    """Run `module`'s corpus walks against exactly `artifacts`.

    Args:
        module: The test module whose `Path` name is redirected. Only
            `Path(module.__file__)` is intercepted — every other call
            reaches the real `pathlib.Path`, because these modules use
            `Path` for temp dirs and product paths too and a blanket
            replacement breaks them in ways that read as errors rather
            than as the finding.
        artifacts: Scene files to copy into the scratch `fixtures/`.
            Empty means the corpus is gone.

    Yields:
        The scratch `fixtures/` directory, for the failure message.
    """
    real = Path
    with tempfile.TemporaryDirectory() as tmp:
        root = real(tmp)
        (root / "fixtures").mkdir()
        for src in artifacts:
            shutil.copy2(src, root / "fixtures" / src.name)

        def fake(*args: Any, **kwargs: Any) -> Any:
            """Intercept only this module's own `__file__`.

            Args:
                *args: As `pathlib.Path`.
                **kwargs: As `pathlib.Path`.

            Returns:
                A `_CorpusAnchor` for the module's own file, else a
                real `Path`.
            """
            if args == (module.__file__,):
                return _CorpusAnchor(root)
            return real(*args, **kwargs)

        with mock.patch.object(module, "Path", fake):
            yield root / "fixtures"


class InstrumentCase(unittest.TestCase):
    """Base: resolve a named part of an instrument, or fail by ASSERTION.

    Restoring a pre-fix world usually DELETES the function under test,
    and `getattr` on a missing name raises — which unittest reports as
    an error. An error says "this test is broken"; the finding here is
    "this instrument is". Every case below therefore reaches its
    subject through `part`, so the pre-fix world produces a sentence
    about the instrument and never a traceback about the pin.
    """

    def part(self, module: Any, name: str, why: str) -> Any:
        """Fetch `module.name`, asserting rather than raising if it is gone.

        Args:
            module: The instrument module.
            name: The attribute this case needs.
            why: What its absence means, in the reader's terms.

        Returns:
            The attribute.
        """
        got = getattr(module, name, None)
        self.assertIsNotNone(
            got, "%s no longer offers %s — %s"
                 % (module.__name__.split(".")[-1], name, why))
        return got


def run_case(module: Any, cls: str, method: str) -> unittest.TestResult:
    """Run one existing test method and hand back its result object.

    Reading the RESULT rather than letting the case run inside this one
    is the same move `guard_mutants.verdict` makes for the same reason:
    a nested failure has to be an OBSERVATION here, not this test's own
    failure.

    Args:
        module: Module holding the class.
        cls: Class name.
        method: Method name.

    Returns:
        The `TestResult` after running exactly that one case.
    """
    result = unittest.TestResult()
    getattr(module, cls)(method).run(result)
    return result


def stub_package(into: Path) -> None:
    """Write the two-component stub package the guard census can name.

    Args:
        into: Directory to create `pinstub_pkg/` inside; it is put on
            `sys.path` by the caller.
    """
    pkg = into / "pinstub_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "stub.py").write_text(_STUB, encoding="utf-8")


def report_for(name: str) -> dict[str, Any]:
    """Build a real driver report for `name`, in-process.

    NOT A HAND-WRITTEN DICT, deliberately. The whole defect is that
    `unittest`'s answer to a name that does not resolve looks like a
    failing test from outside, and a report invented here would encode
    this author's belief about that answer instead of measuring it.
    These are the same four lines `guard_mutants._DRIVER` runs.

    Args:
        name: A dotted test name, resolvable or not.

    Returns:
        A report in `verdict`'s input shape.
    """
    loader = unittest.TestLoader()
    loader.errors = []
    suite = loader.loadTestsFromName(name)
    res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    return {"import_error": None,
            "load_errors": [str(e).splitlines()[-1] for e in loader.errors],
            "run": res.testsRun, "failures": len(res.failures),
            "errors": len(res.errors),
            "unexpected": len(getattr(res, "unexpectedSuccesses", ()) or ()),
            "skipped": len(res.skipped)}


def hooks_in(text: str) -> list[dict[str, str]]:
    """Read `.pre-commit-config.yaml`'s hook blocks without a yaml parser.

    `pyyaml` is third-party and this tree is stdlib-only, so the config
    is scanned rather than parsed. The scan is deliberately shallow —
    it collects the scalar `key: value` lines nested under each
    `- id: NAME` and nothing else — and its own liveness is asserted
    twice below, by a floor on the ids it finds in the real file and by
    a case proving it FIRES on a synthetic bad config. A reader that
    silently returns `[]` would pass every predicate here, which is
    this file's subject one level up.

    Continuation lines of a folded `entry: >-` block sit deeper than
    their hook and can look like `key: value`; `setdefault` means the
    first spelling of a key wins, so a shell line containing a colon
    cannot overwrite a real setting.

    Args:
        text: The whole config file.

    Returns:
        One dict per hook, in file order, always carrying `id`.
    """
    hooks: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    indent = 0
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        pad, body = len(raw) - len(raw.lstrip()), raw.strip()
        if body.startswith("- id:"):
            cur, indent = {"id": body[len("- id:"):].strip()}, pad
            hooks.append(cur)
            continue
        if cur is None:
            continue
        if pad <= indent:
            cur = None
            continue
        key, sep, value = body.partition(":")
        if sep and key.strip():
            cur.setdefault(key.strip(), value.strip())
    return hooks


def manual_hooks_that_need_a_changed_file(text: str) -> list[str]:
    """Name every manual-stage hook that would skip on a clean tree.

    THE PREDICATE, kept as a function so the same reading can be run
    over the pre-fix config to prove this file's red. `stages` decides
    when a hook is ELIGIBLE; `always_run` decides whether it needs a
    changed file to bother. A manual hook is one somebody deliberately
    asked for, so "no files to check" is never an honest answer to
    give them — it is the named suite reporting green for not having
    run.

    Args:
        text: The whole config file.

    Returns:
        The ids of manual hooks lacking `always_run: true`, in file
        order.
    """
    return [h["id"] for h in hooks_in(text)
            if "manual" in h.get("stages", "")
            and h.get("always_run") != "true"]


class TestTheGuardCensusVerdictNeverReadsAnExitCode(InstrumentCase):
    """`verdict` maps a load error to NO-TEST, and a kill only to KILLED.

    THE DEFECT. `run_one` ended `return "KILLED" if proc.returncode != 0
    else "SURVIVED"`. `unittest` answers an unresolvable test name with a
    synthetic `unittest.loader._FailedTest` that ERRORS, so the process
    exits non-zero and a DELETED test read as a guard killed by it. That
    is not hypothetical: this harness once reported 23/23 over a tree
    whose tests had been deleted.

    RESTORATION USED. Reverse-applied `7fd48f1`'s `tests/guard_mutants.py`
    hunks (`git show 7fd48f1 -- tests/guard_mutants.py | git apply -R`),
    which removes `verdict` and `_DRIVER` and puts the returncode line
    back. Under it `test_the_six_verdicts_are_table_driven` fails on its
    first assertion — the module exposes no `verdict` — and
    `test_a_name_that_does_not_resolve_reads_as_no_test_end_to_end` fails
    on the exit-code reading it makes explicit.

    All six answers are table-driven and not just the load-error one,
    because the fix's value is the whole mapping: a repair that answered
    NO-TEST to everything would satisfy the one row that motivated it.
    """

    def setUp(self) -> None:
        """Put a nameable stub package on `sys.path` for this case."""
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        stub_package(self.tmp)
        sys.path.insert(0, str(self.tmp))
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(sys.path.remove, str(self.tmp))

    def classifier(self) -> Any:
        """The report-reading classifier, or a sentence saying it is gone.

        Returns:
            `guard_mutants.verdict`.
        """
        return self.part(
            guard_mutants, "verdict",
            "the guard census is reading a process EXIT CODE again, and an "
            "exit code cannot tell a test that observed the mutation from a "
            "test that is no longer there")

    def test_the_six_verdicts_are_table_driven(self) -> None:
        """Table-drive all six answers, over the driver's own report shape.

        The rows are ordered by the precedence that matters: a report
        can carry both a load error and zero runs, and both a skip and a
        failure, and only one answer is right for each.
        """
        base = {"import_error": None, "load_errors": [], "run": 0,
                "failures": 0, "errors": 0, "unexpected": 0, "skipped": 0}
        table = [
            ("HARNESS-ERROR", None,
             "no report at all — the child crashed or timed out"),
            ("IMPORT-ERROR", {"import_error": "ImportError: boom"},
             "the mutated source would not import"),
            ("NO-TEST", {"load_errors": ["AttributeError: no test_gone"]},
             "THE ROW THAT COST 23/23 — the name does not resolve"),
            ("NO-TEST", {"run": 0},
             "nothing ran, so nothing observed anything"),
            ("SKIPPED", {"run": 2, "skipped": 2},
             "every test it names was skipped"),
            ("KILLED", {"run": 1, "failures": 1},
             "the named test failed: the mutation was observed"),
            ("KILLED", {"run": 1, "errors": 1},
             "the named test errored on the mutation"),
            ("KILLED", {"run": 1, "unexpected": 1},
             "a red went green under the mutation, which is a kill"),
            ("SURVIVED", {"run": 1},
             "it ran, it passed: nothing observed the mutation"),
        ]
        verdict = self.classifier()
        for want, patch, why in table:
            report = None if patch is None else {**base, **patch}
            with self.subTest(want=want, why=why):
                self.assertEqual(verdict(report), want, why)

    def test_a_name_that_does_not_resolve_reads_as_no_test_end_to_end(
            self) -> None:
        """A deleted test reads NO-TEST, though its exit code says KILLED.

        Both halves are measured here rather than argued. The first
        assertion is the DEFECT, pinned: `unittest` reports the missing
        name as one errored test, which is a non-zero exit and was read
        as KILLED. The second is the fix. The third runs
        `guard_mutants._DRIVER` in a real subprocess, so the wiring
        `run_one` depends on is proved and not only the classifier —
        without rewriting `canvas.py`, which is why `run_one` itself is
        not called from the suite.
        """
        gone = "pinstub_pkg.stub.TestStub.test_gone"
        verdict = self.classifier()
        driver = self.part(
            guard_mutants, "_DRIVER",
            "the census starts `-m unittest <name>` again and reads what "
            "the process did, not what the runner found")
        loader = unittest.TestLoader()
        loader.errors = []
        suite = loader.loadTestsFromName(gone)
        ran = unittest.TextTestRunner(stream=io.StringIO(),
                                      verbosity=0).run(suite)
        self.assertFalse(
            ran.wasSuccessful(),
            "unittest has stopped answering an unresolvable name with a "
            "failing synthetic test, which is the premise of this whole "
            "guard — re-derive the mapping before trusting it")
        self.assertEqual(
            verdict(report_for(gone)), "NO-TEST",
            "a test that is no longer there reads as a guard KILLED by "
            "it — the census reports a perfect score over deleted tests")
        proc = subprocess.run(
            [sys.executable, "-B", "-c", driver, gone],
            cwd=str(self.tmp), capture_output=True, text=True, check=False)
        line = next((ln for ln in proc.stdout.splitlines()
                     if ln.startswith("GUARDMUTANT ")), None)
        self.assertIsNotNone(
            line, "the driver printed no report, so run_one has nothing to "
                  "read: stdout=%r stderr=%r" % (proc.stdout, proc.stderr))
        self.assertEqual(
            verdict(json.loads(line[len("GUARDMUTANT "):])), "NO-TEST",
            "end to end, a missing test still does not read as NO-TEST")

    def test_a_test_that_really_failed_still_reads_as_killed(self) -> None:
        """THE SILENT HALF: it has not been made to say NO-TEST about all.

        A repair that stopped reporting kills would satisfy every
        assertion above and destroy the instrument, so the live pole is
        asserted over a real run of a real failing test rather than over
        a hand-built report.
        """
        verdict = self.classifier()
        self.assertEqual(
            verdict(report_for("pinstub_pkg.stub.TestStub.test_dies")),
            "KILLED",
            "a named test that ran and failed is a guard OBSERVED; if this "
            "reads as anything else the census can no longer find a kill")
        self.assertEqual(
            verdict(report_for("pinstub_pkg.stub.TestStub.test_lives")),
            "SURVIVED",
            "a named test that ran and passed is the finding this whole "
            "instrument exists to produce")


class TestTheGuardCensusChecksItsOwnSubjects(InstrumentCase):
    """`check_subjects` fires on a renamed test and on a no-op mutation.

    THE DEFECT. The sweep is hand-run and nothing ran it, so it could
    lose every subject it has and say nothing until a curator happened
    to start it. `check_subjects` is the half cheap enough for the gate:
    it asks whether each mutation still APPLIES and whether each named
    test still RESOLVES.

    RESTORATION USED. The same reverse apply of `7fd48f1`'s
    `tests/guard_mutants.py` hunks, which removes `check_subjects`
    entirely; the first assertion below then fails by assertion rather
    than by `AttributeError`. Re-run with the function present but
    re-worded, to prove nothing here depends on its message text.

    THE ROSTER IS SYNTHETIC and `SRC` points at a three-line scratch
    file, because the question is whether the CHECK can see a broken
    subject — not whether today's `canvas.py` has one. The live roster
    gets its own case, as the silent half.
    """

    def setUp(self) -> None:
        """Point the census at a scratch source and a nameable stub."""
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        stub_package(self.tmp)
        self.src = self.tmp / "scratch_source.py"
        self.src.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    @contextlib.contextmanager
    def census_over(self, roster: list[tuple[str, str, str, int, str]]
                    ) -> Iterator[None]:
        """Run the census against a synthetic roster and source.

        Args:
            roster: `GUARDS`-shaped rows.

        Yields:
            Nothing; the patches are live for the block.
        """
        with mock.patch.object(guard_mutants, "SRC", self.src), \
                mock.patch.object(guard_mutants, "ROOT", self.tmp), \
                mock.patch.object(guard_mutants, "MODULE",
                                  "pinstub_pkg.stub"), \
                mock.patch.object(guard_mutants, "GUARDS", roster):
            yield

    def subject_check(self) -> Any:
        """The gate half of the census, or a sentence saying it is gone.

        Returns:
            `guard_mutants.check_subjects`.
        """
        return self.part(
            guard_mutants, "check_subjects",
            "the sweep is a hand-run tool again with nothing watching "
            "whether it still HAS subjects, which is how it stayed broken "
            "silently for as long as it existed")

    def test_a_renamed_test_and_a_no_op_mutation_are_both_reported(
            self) -> None:
        """Three broken subjects, three complaints, each naming its guard.

        The magnitude matters as much as the firing: a check that
        complained about the whole roster whenever anything was wrong
        would be useless to the person re-pointing one anchor. So the
        healthy row must produce NOTHING while its three neighbours each
        produce exactly one line.
        """
        roster = [
            ("healthy", "alpha", "ALPHA", 1, "TestStub.test_lives"),
            ("missing-anchor", "omega", "OMEGA", 1, "TestStub.test_lives"),
            ("no-op", "beta", "beta", 1, "TestStub.test_lives"),
            ("renamed-test", "gamma", "GAMMA", 1, "TestStub.test_gone"),
        ]
        check = self.subject_check()
        with self.census_over(roster):
            bad = check()
        self.assertEqual(
            len(bad), 3,
            "expected one complaint per broken subject and got %d: %s"
            % (len(bad), bad))
        for label in ("missing-anchor", "no-op", "renamed-test"):
            with self.subTest(label=label):
                self.assertEqual(
                    len([c for c in bad if label in c]), 1,
                    "no complaint names %r, so a curator reading this "
                    "output would not know which guard to re-point: %s"
                    % (label, bad))
        self.assertEqual(
            [c for c in bad if "healthy" in c], [],
            "the intact guard was reported broken; a check that complains "
            "about everything is read as noise and then ignored")

    def test_a_roster_whose_subjects_are_intact_is_silent(self) -> None:
        """THE SILENT HALF: nothing wrong, nothing said.

        Without this, `return ["something"]` passes the case above and
        the gate hook fails on every commit forever.
        """
        roster = [("healthy", "alpha", "ALPHA", 1, "TestStub.test_lives"),
                  ("second", "gamma", "GAMMA", 1, "TestStub.test_skips")]
        check = self.subject_check()
        with self.census_over(roster):
            self.assertEqual(
                check(), [],
                "a roster whose anchors all apply and whose tests all "
                "resolve was reported broken")

    def test_the_live_roster_still_has_every_subject(self) -> None:
        """The real `GUARDS` against the real `canvas.py`, as the hook runs.

        This is the assertion the `guard-mutants-check` hook makes on
        every commit, made once more inside the suite so that a tree
        whose hooks are not installed still learns the census has lost a
        subject. Measured at 0.2s.
        """
        self.assertEqual(
            self.subject_check()(), [],
            "the guard census has lost a subject: until it is re-pointed "
            "the sweep cannot give an honest answer. Then run "
            "`python3 tests/guard_mutants.py` for the full sweep")


class TestTheConnectorPopulationIsRoleAwareNotTypeOnly(unittest.TestCase):
    """`_arrows` admits a roleless line and refuses a decorative one.

    THE DEFECT. `instruments._arrows` filtered `type == "arrow"` while
    `canvas.py` calls a connector `("arrow", "line")` everywhere it
    routes or lints one. A scene whose relations are `line`-typed —
    which is what a user drawing a connector by hand produces — came
    back with an empty population, and `edge_crossings` answered a
    confident `0` about a drawing it never examined. That is worse than
    silence: a zero is read as a good score.

    RESTORATION USED. Two monkeypatches, run from a throwaway `/tmp`
    plugin rather than by editing this tree: the PRE-FIX population
    (`type == "arrow"`) and the NAIVE widening (`type in ("arrow",
    "line")`) that closing the hole the obvious way produces. The
    pre-fix population fails the two positive cases; the naive widening
    fails the two negative ones. Both poles are needed, and that is why
    the negative pole is here rather than being left to the fix's
    commit message.

    FIX-AGNOSTIC. Re-run green under three honest role-aware repairs —
    `canvas.py`'s defaulting `role_of` (the one that landed), the raw
    `customData.role` read without a default, and a membership test on
    the `(type, role)` pair.

    MAGNITUDES ARE PINNED ON MINIMAL SCENES; the corpus cases assert an
    INVARIANCE rather than a total, so they stay true over an honest
    drawing. The measured absolutes they replace are in the commit
    message for `7fd48f1`: 18 crossings across 6 artifacts restored, 0
    of 24 scores moved, against 6 manufactured crossings and 4 scores
    moved for the naive widening.
    """

    @staticmethod
    def crossing_pair(kind: str, role: str | None) -> list[dict]:
        """Two straight connectors that cross exactly once at (100, 100).

        The smallest scene that can hold this defect: no nodes, no
        labels, round coordinates, and `role` the only attribute in
        play. `_arrows` reads the type and the role and nothing else —
        bindings included — so a scene carrying bound rectangles would
        add four elements and no evidence.

        Args:
            kind: `"arrow"` or `"line"`.
            role: `customData.role`, or None to omit `customData`
                entirely — the roleless element a user's own hand
                produces.

        Returns:
            A two-element scene.
        """
        def el(eid: str, x: float, dx: float) -> dict:
            """One straight connector.

            Args:
                eid: Element id.
                x: Left origin.
                dx: Horizontal extent, signed.

            Returns:
                An element dict.
            """
            out = {"id": eid, "type": kind, "x": x, "y": 0.0,
                   "width": abs(dx), "height": 200.0, "roundness": None,
                   "points": [[0, 0], [dx, 200]]}
            if role is not None:
                out["customData"] = {"role": role}
            return out
        return [el("c1", 0.0, 200.0), el("c2", 200.0, -200.0)]

    def test_two_crossing_roleless_lines_count_as_one_crossing(self) -> None:
        """A hand-drawn `line` connector is a connector.

        Magnitude and direction both: exactly ONE crossing, between
        exactly those two ids. The pre-fix population answers 0 here,
        and 0 is the reading that gets published as a clean drawing.
        """
        for role in (None, "node", "relation"):
            with self.subTest(role=role):
                count, pairs = instruments.edge_crossings(
                    self.crossing_pair("line", role))
                self.assertEqual(
                    (count, pairs), (1, [("c1", "c2")]),
                    "two line-typed connectors visibly cross once and the "
                    "instrument reports %d — a scene whose relations are "
                    "lines is being scored without being examined"
                    % count)

    def test_two_crossing_decoration_lines_count_as_none(self) -> None:
        """THE NEGATIVE POLE: furniture is not a connector.

        Chart axes, slider tracks and checkbox ticks are `line`
        elements marked `role: decoration`, and admitting them
        manufactures crossings out of things no reader would call a
        relation. This is the case a naive `type in ("arrow", "line")`
        widening fails, and the reason the fix asks the role.
        """
        count, pairs = instruments.edge_crossings(
            self.crossing_pair("line", "decoration"))
        self.assertEqual(
            (count, pairs), (0, []),
            "two decorative lines were counted as %d crossing(s): the "
            "widening has manufactured a finding out of furniture" % count)

    def test_an_arrow_pair_still_crosses(self) -> None:
        """The control: the detector lives in the population that never moved.

        A crossing counter returning 0 for everything would satisfy the
        negative pole above, so its live pole is asserted on the same
        geometry with the type it always accepted.
        """
        count, _pairs = instruments.edge_crossings(
            self.crossing_pair("arrow", "node"))
        self.assertEqual(count, 1,
                         "the crossing counter no longer sees two crossing "
                         "arrows, so nothing below means anything")

    def test_retyping_every_relation_to_line_moves_no_artifact(self) -> None:
        """THE INVARIANT: how a relation is TYPED is not a fact about it.

        Deliberately not a total. A literal here would go stale on the
        next artifact and would have to be re-derived by whoever added
        it; the invariance stays true over any honest drawing, and it is
        what the defect actually broke. The pre-fix population fails
        this on 6 of the 24 artifacts, losing 18 crossings to 0.
        """
        artifacts = sorted(CORPUS.rglob("*.excalidraw"))
        self.assertTrue(
            artifacts, "no frozen artifact under %s, so this invariance "
                       "is a claim about no drawings" % CORPUS)
        for path in artifacts:
            scene = json.loads(path.read_text(encoding="utf-8"))["elements"]
            retyped = [{**e, "type": "line"} if e["type"] == "arrow" else e
                       for e in scene]
            with self.subTest(artifact=path.name):
                self.assertEqual(
                    instruments.edge_crossings(retyped)[0],
                    instruments.edge_crossings(scene)[0],
                    "retyping this artifact's relations from arrow to line "
                    "changed its crossing count, so the reading depends on "
                    "a spelling rather than on the drawing")

    def test_dropping_every_decoration_line_moves_no_artifact(self) -> None:
        """THE NEGATIVE POLE: furniture contributes nothing to any artifact.

        The invariance form of the 6 manufactured crossings the naive
        widening produces on `dashboard` and `dashboard-wireframe`. It
        is green in the pre-fix world too, and that is correct: a
        neighbour's job is to stay silent while the fix moves, and this
        one is what stops "admit every line" from counting as a repair.
        """
        artifacts = sorted(CORPUS.rglob("*.excalidraw"))
        self.assertTrue(
            artifacts, "no frozen artifact under %s, so this invariance "
                       "is a claim about no drawings" % CORPUS)
        for path in artifacts:
            scene = json.loads(path.read_text(encoding="utf-8"))["elements"]
            stripped = [e for e in scene
                        if not (e["type"] == "line"
                                and (e.get("customData") or {}).get("role")
                                == "decoration")]
            with self.subTest(artifact=path.name):
                self.assertEqual(
                    instruments.edge_crossings(stripped)[0],
                    instruments.edge_crossings(scene)[0],
                    "deleting this artifact's decorative lines changed its "
                    "crossing count, so decoration is being scored as if a "
                    "reader would follow it")


class TestTheCorpusFloorsFailOnAnEmptyCorpus(unittest.TestCase):
    """Three census walks that published a zero derived from an empty list.

    THE DEFECT. `for path in sorted(root.rglob("*.excalidraw"))` over a
    missing corpus is the canonical vacuous census: it runs, it asserts
    nothing, and it reports OK. All three named below passed over a tree
    with all 24 artifacts deleted, and all three publish that zero as
    the load-bearing claim in their own docstrings — "no drawing can
    show it", over no drawings.

    RESTORATION USED. Reverse-applied `b00da0f`'s `tests/test_backend.py`
    and `tests/test_mutants.py` hunks, which deletes the three floors and
    nothing else, leaving the rest of the tree at the fold. All three
    subTests below then fail by assertion with "still passed over an
    empty corpus".

    FIX-AGNOSTIC. Re-run green under `assertTrue`, `assertGreater(len,
    0)`, a bare `raise AssertionError` and `self.fail(...)`. It is red
    under `self.skipTest(...)`, and that is a judgement rather than an
    oversight: a skip is a green suite, which is the silence the whole
    class is about.

    HOW THE CORPUS IS EMPTIED: by redirecting the walk's own
    `Path(__file__)` onto a scratch tree, not by deleting files. Same
    condition, sub-second, and it cannot damage the checkout it is
    measuring.
    """

    def test_each_corpus_walk_fails_when_the_corpus_is_empty(self) -> None:
        """Every one of the three says something when its subject is gone.

        `wasSuccessful()` rather than a message match, so any honest
        floor satisfies it — and so a floor that raises rather than
        asserts still counts. What is NOT tolerated is a skip: a skipped
        test is a successful result, and a census that skips itself over
        an empty corpus has re-opened the hole in a new costume.
        """
        for module, cls, method in CORPUS_WALKS:
            with self.subTest(case="%s.%s" % (cls, method)):
                with corpus_of(module, ()) as empty:
                    result = run_case(module, cls, method)
                self.assertFalse(
                    result.wasSuccessful(),
                    "%s.%s still passed over an empty corpus (%s): the zero "
                    "it publishes is a fact about an empty list and not "
                    "about the drawings" % (cls, method, empty))
                self.assertFalse(
                    result.skipped,
                    "%s.%s answered an empty corpus with a SKIP, which is a "
                    "green suite — the census must fail, not excuse itself"
                    % (cls, method))

    def test_each_corpus_walk_is_silent_over_a_corpus_of_one(self) -> None:
        """THE SILENT HALF: a non-empty corpus is not a finding.

        This is what proves the failures above came from EMPTINESS
        rather than from the redirection — same scratch tree, same
        patched `Path`, one real artifact copied in, and all three go
        green. One artifact rather than 24 because the claim is about
        the floor and not about the corpus, and 24 would add ~2.2s to
        every commit to re-derive what the suite already asserts
        elsewhere.

        The artifact is the corpus's own first entry rather than a name
        typed here, so this cannot quietly start measuring a file that
        no longer exists.
        """
        artifacts = sorted(CORPUS.rglob("*.excalidraw"))
        self.assertTrue(artifacts, "no frozen artifact under %s" % CORPUS)
        for module, cls, method in CORPUS_WALKS:
            with self.subTest(case="%s.%s" % (cls, method)):
                with corpus_of(module, (artifacts[0],)):
                    result = run_case(module, cls, method)
                self.assertTrue(
                    result.wasSuccessful(),
                    "%s.%s failed over a corpus holding %s, so its floor is "
                    "firing on something other than emptiness: %s"
                    % (cls, method, artifacts[0].name,
                       result.failures + result.errors))


class TestTheLiveValueScanSurfaceCannotBeEmpty(unittest.TestCase):
    """`tracked_prose_files` refuses an index that lists no markdown.

    THE DEFECT. The `except` arm already named the consequence — "an
    empty scan surface would let every live value pass by measuring
    nothing" — but `git ls-files` EXITS 0 and prints nothing for a
    checkout whose markdown is gone, untracked, or excluded by a
    mis-set pathspec, so the success path returned a quiet empty list.
    `check` was saved downstream by `unplaced_calculators`; `refresh`
    was not, and printed "refreshed 0 value(s) across 0 tracked
    markdown file(s)", exit 0.

    RESTORATION USED. Reverse-applied `b00da0f`'s `tests/livedoc.py`
    hunk, which removes the `if not files: raise` and returns the empty
    list; all three poles below then fail by assertion on "did not
    raise".

    FIX-AGNOSTIC. Re-run green under repairs raising `AssertionError`,
    `RuntimeError` and `SystemExit`, which is why the case asserts only
    that it refuses and that it says why — not which exception it picks.

    THE SURFACE IS A REAL GIT CHECKOUT, built in a temp dir, so the
    actual `git ls-files` call is the one under test. Mocking
    `subprocess` here would prove a belief about git's exit code, and
    that belief is the defect.
    """

    @staticmethod
    def checkout(*names: str) -> Path:
        """Build a throwaway git checkout tracking exactly `names`.

        Args:
            *names: Repo-relative paths to create and add to the index.

        Returns:
            The checkout root.
        """
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("body\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        return root

    def test_an_index_that_lists_no_prose_is_refused(self) -> None:
        """Three ways the surface goes empty while git stays happy.

        The third is the one that separates honest repairs from
        near-misses: a repair that floored on git's RAW output would
        accept a checkout whose only markdown lives under
        `tests/fixtures/`, which this module excludes by design — the
        surface it will actually scan is still empty.

        `BaseException` AND NOT `Exception`, which this case learned the
        hard way. The invariant is that the function does not RETURN;
        picking `Exception` quietly excluded `SystemExit`, and
        `guard_mutants.assert_pristine` refuses exactly that way one
        file over. A pin that admits only the exception type the fix
        happened to choose has prescribed the fix.
        """
        cases = {
            "an index with nothing in it": (),
            "a checkout with code but no prose": ("canvas.py",),
            "prose that is all excluded fixtures": ("tests/fixtures/a.md",),
        }
        for why, names in cases.items():
            root = self.checkout(*names)
            self.addCleanup(shutil.rmtree, root, ignore_errors=True)
            with self.subTest(why=why):
                with mock.patch.object(livedoc, "REPO", root), \
                        self.assertRaises(BaseException) as caught:
                    livedoc.tracked_prose_files()
                self.assertTrue(
                    str(caught.exception).strip(),
                    "it refused %s without saying why, and a refusal "
                    "nobody can act on is reported as a flaky tool" % why)

    def test_a_checkout_with_one_tracked_markdown_is_accepted(self) -> None:
        """THE SILENT HALF: a surface with prose on it is scanned.

        `raise` unconditionally passes every assertion above, and would
        take `livedoc check` — a hook on every commit — down with it.
        """
        root = self.checkout("README.md", "tests/fixtures/a.md", "canvas.py")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with mock.patch.object(livedoc, "REPO", root):
            files = livedoc.tracked_prose_files()
        self.assertEqual(
            [p.name for p in files], ["README.md"],
            "a checkout tracking one markdown file outside the fixtures "
            "was not scanned as one file: %s" % files)

    def test_the_real_checkout_has_a_surface_to_scan(self) -> None:
        """And the production arm, which is what the hook depends on."""
        self.assertTrue(
            livedoc.tracked_prose_files(),
            "this checkout tracks no markdown outside the fixtures, so "
            "every live value would be reported current against nothing")


class TestEveryManualHookRunsWithNothingStaged(unittest.TestCase):
    """A hook somebody asked for by name must not answer "Skipped".

    THE DEFECT. `pre-commit run guard-mutants-sweep --hook-stage manual`
    on a clean tree printed "(no files to check)Skipped" and exited 0 —
    a 32-second census somebody deliberately asked for, reporting
    success for not having run. The pre-existing `e2e-tests` hook had
    the same shape, so the whole Playwright suite could report green for
    not having run.

    RESTORATION USED. The predicate is a function, so the reading was
    re-run over the pre-fix config text
    (`git show 7053b14:.pre-commit-config.yaml`), where it names
    `guard-mutants-sweep` and `e2e-tests`; and over `7fd48f1`'s, where
    the sweep hook exists and `e2e-tests` is still bare. The
    `guard-mutants-check` case fails at `7053b14` for the other reason
    in this class — at that commit no hook ran the guard census at all.

    ONE HONEST REPAIR, stated rather than glossed: `always_run: true` is
    pre-commit's only switch for "run without a changed file", so there
    is nothing to be fix-agnostic ABOUT here. The predicate is written
    over the parsed setting rather than over the file's text, so a
    reordering or a re-comment does not move it.
    """

    def setUp(self) -> None:
        """Read the config once for the cases that share it."""
        self.path = REPO / ".pre-commit-config.yaml"
        self.text = self.path.read_text(encoding="utf-8")

    def test_the_reader_finds_the_hooks_this_repo_declares(self) -> None:
        """THIS FILE'S OWN SUBJECT: a reader that finds nothing passes all.

        A floor on named ids rather than on a count, because a count
        goes stale the next time a hook is added and would be repaired
        by editing this number — which teaches the reader to edit
        numbers.
        """
        found = {h["id"] for h in hooks_in(self.text)}
        for hook in ("backend-tests", "livedoc", "mypy", "ruff-check",
                     "guard-mutants-check", "guard-mutants-sweep",
                     "e2e-tests", "eslint", "tsc", "frontend-deps"):
            with self.subTest(hook=hook):
                self.assertIn(
                    hook, found,
                    "the config reader below cannot see %r, so every "
                    "assertion it makes about this repo's hooks is vacuous"
                    % hook)

    def test_the_reader_fires_on_a_manual_hook_without_always_run(
            self) -> None:
        """THE LIVE POLE: the predicate can still say no.

        Asserted against a synthetic config carrying the defect and its
        repair side by side, so the reader is proved to separate them
        rather than to answer `[]` whatever it is handed.
        """
        config = ("repos:\n"
                  "  - repo: local\n"
                  "    hooks:\n"
                  "      - id: bare-manual\n"
                  "        entry: true\n"
                  "        stages: [manual]\n"
                  "      - id: guarded-manual\n"
                  "        entry: true\n"
                  "        always_run: true\n"
                  "        stages: [manual]\n"
                  "      - id: ordinary\n"
                  "        entry: true\n")
        self.assertEqual(
            manual_hooks_that_need_a_changed_file(config), ["bare-manual"],
            "the reader cannot tell a manual hook that skips on a clean "
            "tree from one that does not")

    def test_no_manual_hook_needs_a_changed_file(self) -> None:
        """Every manual-stage hook runs when somebody asks for it."""
        self.assertEqual(
            manual_hooks_that_need_a_changed_file(self.text), [],
            "these manual-stage hooks carry no `always_run: true`, so "
            "`pre-commit run <id> --hook-stage manual` on a clean tree "
            "prints Skipped and exits 0 — success for not having run")

    def test_the_guard_census_gate_hook_is_unscoped_and_always_run(
            self) -> None:
        """The 0.5s half of the census runs on every commit, unscoped.

        Two claims, and the second is the one that rots. A `files:`
        pattern here would be a hand-written list of the paths that can
        break the census — and a pattern that stops matching is this
        same silent-instrument defect one level up, bought for half a
        second.
        """
        gate = [h for h in hooks_in(self.text)
                if "guard_mutants.py --check" in h.get("entry", "")]
        self.assertEqual(
            len(gate), 1,
            "expected exactly one hook running the guard census's --check "
            "half; found %d. Nothing ran this instrument for as long as it "
            "existed, which is how it stayed broken silently" % len(gate))
        self.assertEqual(
            gate[0].get("always_run"), "true",
            "the guard census gate needs a changed file to bother, so a "
            "commit that touches nothing it recognises is told nothing")
        self.assertNotIn(
            "files", gate[0],
            "the guard census gate has been scoped by a `files:` pattern. "
            "It costs 0.50s; a pattern that stops matching is silent, and "
            "half a second does not buy one")
        self.assertNotIn(
            "manual", gate[0].get("stages", ""),
            "the gate half has been moved to the manual stage, which is "
            "where the sweep already sits — nothing would run it again")
