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

A SECOND BATCH JOINED IT the same day, under `THE SECOND BATCH` below:
four more members of the same class, folded at `b4a49d8`. They belong
here rather than in a file of their own because the question is
identical and the machinery — `InstrumentCase.part`, `run_case`, the
pre-fix restorations — is already written.

NO RED-BY-INTENT LIVES HERE. Every case is green at `4ac8778`; the
fixes landed, and a pin for a landed fix is green or it is wrong. The
class carries no `@unittest.expectedFailure`, so
`HAND_AUTHORED_RED_CLASSES` in `tests/test_mutants.py` gains nothing
from this file — by its own guard, not by hand.
"""
from __future__ import annotations

import contextlib
import copy
import io
import itertools
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
import mutants_mortality
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

    A SECOND RESTORATION, for the empty-roster case, because that
    reverse-apply does not produce the world it needs — it deletes the
    census wholesale, where the case is about a census that ran and
    found nothing to say. Replace `guard_mutants.assert_roster_populated`
    with a no-op, which is exactly the pre-fix path: `check_subjects`
    then walks `[]`, completes, and returns `[]`, and the case fails
    `AssertionError: SystemExit not raised`. Measured 2026-08-20.

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

    def test_an_empty_roster_is_refused_and_not_reported_clean(self) -> None:
        """THE INPUT EVERY CASE ABOVE MISSED: no subjects at all.

        The two cases above hand the census rosters of four rows and of
        two, and the live-roster case below hands it twenty-three, so
        none of them could reach the one roster that made the gate
        meaningless. `GUARDS = []` walked to completion, found no broken
        subject because there was no subject, and `--check` printed
        "0 mutants, every anchor applies and every named test resolves"
        and exited 0. A merge that emptied or truncated the roster was
        therefore reported as a clean commit by the hook that exists to
        notice exactly that.

        Asserting the STATUS and not the wording, like its neighbours:
        2 is refuse-to-run, and it must not collapse into the 1 that
        means some subjects need re-pointing. There is nothing here to
        re-point.
        """
        check = self.subject_check()
        with self.census_over([]), self.assertRaises(SystemExit) as caught:
            check()
        self.assertEqual(
            caught.exception.code, 2,
            "an empty roster must refuse to run (2) rather than report "
            "broken subjects (1) or, worse, return [] — there are no "
            "subjects to report on, which is the finding")

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


# ---------------------------------------------------------------------------
# THE SECOND BATCH — the four fixes folded at `b4a49d8`, same class.
#
# `instruments._nodes` answered `[]` to a scene carrying no roles and the
# VANDALISED copy scored clean; three of `TestDiscovery`'s cases passed on
# `[] == []`; one of them read a second hand-built path to the sweep record;
# and the mortality driver wrote `skipped` while nothing read it, so a kill
# that skipped every test reported as a detector nobody guards. Written
# 2026-08-20 by a curator who did not write any of them, against `b4a49d8`.
# Every figure below was re-derived here, not copied from the fix's message.
# ---------------------------------------------------------------------------

# The one artifact the role refusal was measured on. NAMED, not discovered:
# three corpus files are called `admin-console*` and they carry 27, 15 and 12
# roled nodes, so "the first admin-console" would silently change subject the
# day a project directory is added.
ROLE_SUBJECT = (CORPUS / "argus-r4-arm3" / "artifacts" /
                "admin-console.excalidraw")
# `ROLE_SUBJECT` is a wireframe and carries no arrows at all, so the
# over-fire half needs a flow drawing to take its arrows from. Also named:
# the point is that a REAL scene of nothing but connectors is answered and
# not refused, and a synthetic arrow would only prove the branch.
ARROW_SUBJECT = (CORPUS / "argus-r5" / "artifacts" / "daily-run.excalidraw")


def scene_of(path: Path) -> list[dict]:
    """Read one frozen artifact's element list.

    Args:
        path: An `.excalidraw` file in the corpus.

    Returns:
        Its `elements` list, freshly parsed so a caller may edit it.
    """
    return json.loads(path.read_text(encoding="utf-8"))["elements"]


def without_roles(elements: list[dict]) -> list[dict]:
    """A copy with `customData.role` deleted from every element.

    The producer-never-wrote-roles half of the refusal: `customData`
    survives, so this is the shape a scene takes when the key is
    dropped rather than the shape it takes when a diagram arrives from
    somewhere that never had one.

    Args:
        elements: A scene.

    Returns:
        A deep copy carrying no roles at all.
    """
    out = copy.deepcopy(elements)
    for el in out:
        data = el.get("customData")
        if isinstance(data, dict):
            el["customData"] = {k: v for k, v in data.items() if k != "role"}
    return out


def under_a_role_vocabulary(elements: list[dict], spelling: str) -> list[dict]:
    """A copy where every `role == "node"` is respelled `spelling`.

    The OTHER cause, and the reason the refusal reports a tally rather
    than only a refusal: a vocabulary drift leaves every element roled,
    just not in a word this module reads.

    Args:
        elements: A scene.
        spelling: The word to use instead of "node".

    Returns:
        A deep copy under the new vocabulary.
    """
    out = copy.deepcopy(elements)
    for el in out:
        data = el.get("customData")
        if isinstance(data, dict) and data.get("role") == "node":
            data["role"] = spelling
    return out


def keeping_one_role(elements: list[dict]) -> tuple[list[dict], str]:
    """A copy with exactly ONE node still roled and every other bare.

    Args:
        elements: A scene carrying at least one roled node.

    Returns:
        `(scene, kept_id)` — the copy, and the id of the survivor.
    """
    out, kept = without_roles(elements), ""
    for before, after in zip(elements, out):
        if (before.get("customData") or {}).get("role") != "node":
            continue
        if before["type"] not in instruments.NODE_TYPES:
            continue
        kept = before["id"]
        after.setdefault("customData", {})["role"] = "node"
        break
    return out, kept


def nodes_before_the_refusal(elements: list[dict]) -> list[dict]:
    """`_nodes` as it stood before `b4a49d8`: the filter, asking nothing.

    THE PRE-FIX IMPLEMENTATION, kept whole so the inversion below is
    measured rather than argued. Byte-for-byte the two lines the fix
    replaced.

    Args:
        elements: Full scene element list.

    Returns:
        Node-typed elements roled `node`, or `[]` — never a refusal.
    """
    return [e for e in elements if e["type"] in instruments.NODE_TYPES
            and (e.get("customData") or {}).get("role") == "node"]


def roled_node_ratios() -> list[tuple[float, str, int, int]]:
    """Per artifact, what fraction of its node-typed shapes is roled `node`.

    Derived rather than written down, because the number it produces is
    the whole cost argument for the ratio floor the refusal deliberately
    does NOT have: a floor set above the corpus minimum disqualifies a
    shipped drawing.

    Returns:
        `(ratio, artifact name, roled, node-typed)` for every corpus
        artifact holding at least one node-typed shape, ascending.
    """
    out = []
    for path in sorted(CORPUS.rglob("*.excalidraw")):
        typed = [e for e in scene_of(path)
                 if e["type"] in instruments.NODE_TYPES]
        if not typed:
            continue
        roled = sum(1 for e in typed
                    if (e.get("customData") or {}).get("role") == "node")
        out.append((roled / len(typed), path.name, roled, len(typed)))
    return sorted(out)


class TestTheNodePopulationRefusesAnAllUnroledScene(InstrumentCase):
    """`_nodes` refuses a scene it can only read from an empty population.

    THE DEFECT, AND IT IS NOT MERE SILENCE. `_nodes` filtered on
    `customData.role == "node"` and answered `[]` when nothing matched.
    An empty node population does not score WORSE than a real one — it
    scores PERFECT, because `node_overlap` and `gridiness` are ratios
    over pairs that no longer exist. `node_overlap` is in
    `instruments.GATES`, so the reading the pre-fix code produced was
    not quiet, it was INVERTED: the healthy drawing was the one
    disqualified and the vandalised copy was the one that passed clean.

    THE SCORE DOES NOT SIMPLY GO UP, and the pin says so rather than
    repeating the simpler story. Re-derived here on `ROLE_SUBJECT`: the
    weighted sum goes DOWN (0.9407 -> 0.9286) and `bends_mean` falls
    1.0000 -> 0.6429, because stripping roles also widens the arrow
    population. What inverts is the gate and the defect list, which is
    why those are what the case below asserts.

    RESTORATION USED. `mock.patch.object(instruments, "_nodes",
    nodes_before_the_refusal)` — the pre-fix body reinstalled, since
    `score_layout` reaches its selector through the module global. Under
    it `test_the_pre_refusal_reading_preferred_the_wreck` records the
    inversion and the two refusal cases fail on a call that returns `[]`
    instead of raising.

    THE LIMIT HAS ITS OWN CASE and is not papered over; see
    `test_one_surviving_role_defeats_the_refusal_entirely`.
    """

    def selector(self) -> Any:
        """The node selector, or a sentence saying it is gone.

        Returns:
            `instruments._nodes`.
        """
        return self.part(
            instruments, "_nodes",
            "every node-derived reading in this module now comes from "
            "somewhere unnamed, and the population it reads cannot be "
            "asked whether it is empty")

    def test_a_role_stripped_scene_is_refused_and_the_tally_names_why(
            self) -> None:
        """A scene with no roles at all is refused, counting what it saw.

        The magnitude is the tally, not the refusal: a message that only
        said "no nodes" would leave a reader unable to tell a producer
        that never wrote roles from one whose vocabulary drifted.
        """
        nodes = self.selector()
        healthy = scene_of(ROLE_SUBJECT)
        self.assertEqual(
            len(nodes(healthy)), 27,
            "`%s` no longer carries the 27 roled nodes this case was "
            "measured on, so the figures below are about a different "
            "drawing" % ROLE_SUBJECT.name)
        with self.assertRaises(ValueError) as raised:
            nodes(without_roles(healthy))
        said = str(raised.exception)
        self.assertIn(
            "<unroled>=35", said,
            "the refusal does not report that all 35 node-typed shapes "
            "were BARE, so a missing-customData read cannot be told from "
            "a renamed one: %r" % said)
        self.assertIn(
            "35 node-typed shapes", said,
            "the refusal names no population size, so a reader cannot "
            "tell a scene of 35 unread shapes from a scene of one: %r"
            % said)

    def test_a_renamed_vocabulary_reads_differently_from_a_missing_one(
            self) -> None:
        """The two causes produce two different tallies, not one refusal.

        NON-DISCRIMINATING IS AS BAD AS MUTE. Both scenes below are
        refused; the case is whether the refusals can be told apart, so
        the two messages are exhibited side by side and required to
        differ.
        """
        nodes = self.selector()
        healthy = scene_of(ROLE_SUBJECT)
        with self.assertRaises(ValueError) as bare:
            nodes(without_roles(healthy))
        with self.assertRaises(ValueError) as drifted:
            nodes(under_a_role_vocabulary(healthy, "box"))
        self.assertIn(
            "box=27, decoration=7, note=1", str(drifted.exception),
            "a scene whose nodes are all roled `box` is refused without "
            "reporting the vocabulary it found, so the repair — teach the "
            "module the new word — is invisible: %r" % str(drifted.exception))
        self.assertNotEqual(
            str(bare.exception), str(drifted.exception),
            "a stripped scene and a renamed one are refused in the SAME "
            "words, so the refusal has stopped discriminating between two "
            "causes with two different repairs")

    def test_the_pre_refusal_reading_preferred_the_wreck(self) -> None:
        """THE RED: the gate disqualified the healthy drawing, not the wreck.

        Every figure is re-derived under the restored pre-fix selector.
        The assertions are RELATIONS — which drawing is gated, which way
        the defect list moved, which one won the count — because those
        are what invert. The absolute magnitudes are carried in the
        messages, and only `node_overlap == 1.0` is asserted as a
        number: a perfect score is the ceiling, and an empty population
        reaching it is the defect itself rather than a fixture accident.
        """
        healthy = scene_of(ROLE_SUBJECT)
        wrecked = without_roles(healthy)
        with mock.patch.object(instruments, "_nodes",
                               nodes_before_the_refusal):
            self.assertEqual(
                len(instruments._nodes(wrecked)), 0,
                "the restoration did not restore: the pre-fix selector "
                "must answer an empty population here, or this case is "
                "measuring the fix")
            good = instruments.score_layout(healthy)
            bad = instruments.score_layout(wrecked)
            versus = instruments.compare_layouts(healthy, wrecked)
        self.assertEqual(
            bad["metrics"]["node_overlap"], 1.0,
            "an empty node population no longer scores a PERFECT "
            "node_overlap, so the premise of this whole refusal has "
            "moved and its cost needs re-deriving (was %.4f)"
            % bad["metrics"]["node_overlap"])
        self.assertLess(
            good["metrics"]["node_overlap"],
            bad["metrics"]["node_overlap"],
            "the vandalised copy no longer outscores the healthy one on "
            "the gated metric")
        self.assertTrue(
            good["defects"] and not bad["defects"],
            "the healthy drawing reports %d defect(s) and the wreck %d — "
            "the inversion this refusal exists to stop is that the wreck "
            "reports NONE"
            % (len(good["defects"]), len(bad["defects"])))
        self.assertEqual(
            (versus["gates"]["a"], versus["gates"]["b"]),
            (["node_overlap"], []),
            "THE FINDING, and it is not `the score goes up`: the weighted "
            "sum actually falls (%.4f -> %.4f) while the count verdict "
            "goes to the wreck %d-%d. What matters is the GATE — the "
            "healthy drawing is the disqualified one and the vandalised "
            "copy passes clean"
            % (versus["weighted"]["a"], versus["weighted"]["b"],
               versus["wins"]["b"], versus["wins"]["a"]))
        self.assertEqual(
            versus["count_winner"], "b",
            "the count verdict no longer goes to the wreck, so the "
            "pre-fix world this pin describes is not the one it measured")

    def test_the_whole_frozen_corpus_is_read_without_complaint(self) -> None:
        """THE SILENT HALF: 24 shipped artifacts, no refusal, no moved reading.

        A refusal that fired on a real drawing would be worse than the
        silence it replaced, so the cost is measured over every frozen
        artifact rather than argued. The role census is derived here too:
        a tally written down would go stale the next time a fixture
        lands and would be repaired by editing the number.
        """
        nodes = self.selector()
        tally: dict[str, int] = {}
        refused = []
        artifacts = sorted(CORPUS.rglob("*.excalidraw"))
        for path in artifacts:
            elements = scene_of(path)
            for el in elements:
                if el["type"] not in instruments.NODE_TYPES:
                    continue
                role = (el.get("customData") or {}).get("role")
                key = "<unroled>" if role in (None, "") else str(role)
                tally[key] = tally.get(key, 0) + 1
            try:
                nodes(elements)
            except ValueError as exc:
                refused.append("%s: %s" % (path.name, exc))
        self.assertEqual(
            refused, [],
            "the refusal fires on a SHIPPED drawing, so it has stopped "
            "being free and every reading it blocks is a real one lost")
        self.assertEqual(
            tally.get("<unroled>", 0), 0,
            "some node-typed shape in the corpus now carries no role at "
            "all, which is one whole artifact away from tripping the "
            "refusal: census %r" % tally)
        self.assertGreaterEqual(
            len(artifacts), 24,
            "the corpus this cost was measured over has shrunk to %d "
            "artifacts, so `no shipped drawing trips it` is a claim about "
            "fewer drawings than it was" % len(artifacts))

    def test_a_scene_with_no_node_typed_shapes_answers_rather_than_refuses(
            self) -> None:
        """THE OTHER SILENT HALF: `typed and not nodes`, not `not nodes`.

        An arrows-only or empty scene has no node population to lose, so
        `[]` is the honest answer and a refusal there would be an
        over-fire — the shape that would make callers stop asking.
        """
        nodes = self.selector()
        arrows = [e for e in scene_of(ARROW_SUBJECT) if e["type"] == "arrow"]
        self.assertTrue(arrows, "`%s` carries no arrows, so this case has "
                                "no arrows-only scene to build"
                                % ARROW_SUBJECT.name)
        self.assertEqual(
            nodes([]), [],
            "an empty scene is now REFUSED rather than read as empty, "
            "which makes the refusal fire where nothing was lost")
        self.assertEqual(
            nodes(arrows), [],
            "a scene of arrows and nothing else is refused, though it "
            "holds no node-typed shape whose role could have gone missing")

    def test_one_surviving_role_defeats_the_refusal_entirely(self) -> None:
        """THE KNOWN LIMIT, pinned so nobody reads the refusal as total.

        Leave ONE of the 27 nodes roled and strip the other 26: the
        population is 1, non-empty, and the whole inverted reading comes
        back — `node_overlap` 1.0000, `gridiness` 1.0000, no defects, no
        refusal. The refusal is all-or-nothing by construction and this
        is the hole it cannot cover.

        IT IS A DECISION, NOT A PATCH. A ratio floor would catch it, but
        the corpus minimum is derived below and a threshold above it
        disqualifies a shipped drawing — so the false-positive surface
        has to be chosen by whoever owns the check, not smuggled in by a
        pin.

        IF THIS CASE GOES RED, THE LIMIT CLOSED. That is good news and
        not a regression: retire this case and the matching paragraph in
        `_nodes`' docstring in the same change, and write the chosen
        threshold's cost down next to it.
        """
        nodes = self.selector()
        healthy = scene_of(ROLE_SUBJECT)
        crippled, kept = keeping_one_role(healthy)
        self.assertTrue(kept, "no node could be kept roled, so this case "
                              "is not building the partial-loss scene")
        surviving = nodes(crippled)
        self.assertEqual(
            len(surviving), 1,
            "the partial-loss scene no longer reads as a population of "
            "one, so this limit is not the one described")
        reading = instruments.score_layout(crippled)
        self.assertEqual(
            (reading["metrics"]["node_overlap"],
             reading["metrics"]["gridiness"], len(reading["defects"])),
            (1.0, 1.0, 0),
            "26 of 27 roles are gone and the reading is no longer the "
            "full inverted one — the limit has moved and its description "
            "in `_nodes`' docstring needs re-deriving")
        floor, name, roled, typed = roled_node_ratios()[0]
        self.assertLess(
            floor, 1.0,
            "every corpus artifact now roles every node-typed shape, so a "
            "ratio floor has stopped costing anything and the reason this "
            "limit was left open no longer holds")
        self.assertGreater(
            len(nodes(crippled)), 0,
            "the refusal has grown a PARTIAL-loss arm. If that is "
            "deliberate, retire this case and `_nodes`' limit paragraph "
            "together, and record the threshold's false-positive surface: "
            "the corpus minimum is %.3f (%s, %d/%d), so any floor at or "
            "above it disqualifies a shipped drawing"
            % (floor, name, roled, typed))


@contextlib.contextmanager
def record_moved_to(payload: dict) -> Iterator[Path]:
    """Point `test_mutants.SWEEP_RECORD` at a scratch record and yield it.

    MOVING THE RECORD IS THE PROOF SHAPE. The pre-fix disposition case
    built its own path instead of reading the module constant, so the
    two disagreed the moment the record lived anywhere else — the writer
    updating the new file while the reader passed over the old one. A
    redirect is that move, made in-process.

    Args:
        payload: The record body to write, in `run_sweep`'s shape.

    Yields:
        The scratch record's path.
    """
    with tempfile.TemporaryDirectory() as box:
        moved = Path(box) / "mutants_sweep.json"
        moved.write_text(json.dumps(payload, indent=2) + "\n",
                         encoding="utf-8")
        with mock.patch.object(test_mutants, "SWEEP_RECORD", moved):
            yield moved


def dispositions_missing_before_one_name() -> list[str]:
    """The pre-fix disposition read: a second, hand-built path to the record.

    Byte-for-byte the expression `b4a49d8` replaced, so the claim that
    the pre-fix case PASSED over a moved record is measured here rather
    than argued.

    Returns:
        Survivor ids in the file at the hardcoded path carrying no
        verdict — `[]` being exactly the pass the defect produced.
    """
    rec = json.loads((Path(test_mutants.__file__).parent /
                      "mutants_sweep.json").read_text(encoding="utf-8"))
    return [s["id"] for s in rec["survivors"]
            if s["id"] not in test_mutants.DISPOSITIONS]


def currency_held_before_the_floor() -> bool:
    """The pre-fix currency read: the equality, and nothing else.

    Returns:
        Whether the live sweep's survivor ids match the record's — which
        is the whole of what the case used to assert, and is `True` for
        two sweeps that both found nothing INCLUDING a sweep that found
        nothing because it ran nothing.
    """
    rec = json.loads(test_mutants.SWEEP_RECORD.read_text(encoding="utf-8"))
    survivors, _skipped = test_mutants.sweep_cells()
    return ([s["id"] for s in survivors] ==
            [s["id"] for s in rec["survivors"]])


@contextlib.contextmanager
def bases_drawing_nothing(*silenced: str) -> Iterator[None]:
    """Replace the named sweep bases with builders that draw nothing.

    THIS IS THE REAL DISEASE, not a contrived one. Every cell is
    skippable by design — `sweep_args` answers `(None, reason)` for a
    base carrying no arrow, no binding or no labelled node — so a base
    that loses its bindings removes five cells in silence and the sweep
    goes on reporting a clean drawing. An empty builder is the limit of
    that: the base still exists, still has its name, and buys nothing.

    Args:
        *silenced: Base names to empty; every name in `SWEEP_BASES` when
            none is given.

    Yields:
        Nothing; the patch is undone on exit.
    """
    names = set(silenced) or {n for n, _ in test_mutants.SWEEP_BASES}
    patched = tuple((n, (lambda: []) if n in names else b)
                    for n, b in test_mutants.SWEEP_BASES)
    with mock.patch.object(test_mutants, "SWEEP_BASES", patched):
        yield


def floor_cells() -> tuple[str, ...]:
    """The `base/op` roster the currency case really iterates.

    READ OUT OF THE CASE'S OWN CODE rather than copied. A list retyped
    here would be a second instance of the roster, and a second instance
    is what makes the absence of the first invisible: the copy would go
    on naming eight cells after the original was cut to two, and this
    pin would report the floor as healthy.

    Returns:
        The literal tuple of `base/op` names the case loops over, or
        `()` when its code no longer holds exactly one such constant.
    """
    code = test_mutants.TestDiscovery.\
        test_live_sweep_reproduces_the_record.__code__
    found = [c for c in code.co_consts
             if isinstance(c, tuple) and c
             and all(isinstance(s, str) and "/" in s for s in c)]
    return found[0] if len(found) == 1 else ()


def cells_the_floor_flagged(result: unittest.TestResult) -> set[str]:
    """Which `base/op` cells a currency-case run actually complained about.

    READ FROM THE SUBTEST PARAMETERS, not from the message bodies. The
    floor fails through `assertIn(cell, ran, ...)`, and `assertIn`'s own
    text prints the whole `ran` SET — so a naive substring search over
    the messages finds every cell that is still running and reports the
    floor as naming cells it never named. That is the check reading its
    own haystack, and it cost this pin its first green.

    Args:
        result: The result of running the currency case.

    Returns:
        The cell names the failing subtests were parameterised with, or
        the empty set when the run produced no parameterised failure —
        which is itself one of the answers the callers judge.
    """
    flagged = set()
    for case, _msg in result.failures + result.errors:
        params = getattr(case, "params", None) or {}
        cell = params.get("cell")
        if isinstance(cell, str):
            flagged.add(cell)
    return flagged


def ran_cells() -> set[str]:
    """Every `base/op` cell the live sweep actually executes.

    Returns:
        The cells `sweep_cells` built and ran, as `base/op` strings.
    """
    _survivors, skipped = test_mutants.sweep_cells()
    return {"%s/%s" % (base, op)
            for base, _ in test_mutants.SWEEP_BASES
            for op in test_mutants.CORRESPONDENCE} - {
        "%s/%s" % (base, op) for base, op, _ in skipped}


class TestTheSweepRecordHasExactlyOneName(InstrumentCase):
    """Move the record and every reader moves with it, or fails saying so.

    THE DEFECT. `test_recorded_sweep_is_fully_dispositioned` opened
    `Path(__file__).parent / "mutants_sweep.json"` while `run_sweep`'s
    writer and every other reader used `SWEEP_RECORD`. Two names for one
    file is not a style question: move the file and the writer follows
    the constant while this reader goes on opening whatever is left at
    the old path — PASSING, in perpetuity, over a record nothing updates.
    A test that reads a stale file reports the state of a world that no
    longer exists.

    RESTORATION USED. `dispositions_missing_before_one_name` — the
    hardcoded expression, kept whole. Under the redirect below it
    answers `[]` over the committed record while the redirected record
    holds an undispositioned survivor, which is the pre-fix pass
    measured rather than asserted.

    THE PROPERTY IS BEHAVIOURAL and not a source scan. `mutants_sweep
    .json` appears four times in `test_mutants.py` and three of those
    are prose; counting them would pin the comments.
    """

    def record(self) -> Any:
        """The one name, or a sentence saying it is gone.

        Returns:
            `test_mutants.SWEEP_RECORD`.
        """
        return self.part(
            test_mutants, "SWEEP_RECORD",
            "the sweep record has no single name again, so a reader and "
            "the writer can disagree about which file the record is")

    def stale(self) -> dict:
        """A record no live sweep could have produced, for the redirect.

        The survivor id names a live operator and a live base, so the
        `assertNamesALiveCell` arm cannot fire and the only thing left to
        fail is the disposition check itself.

        Returns:
            A record body carrying one undispositioned survivor.
        """
        op = sorted(test_mutants.CORRESPONDENCE)[0]
        base = test_mutants.SWEEP_BASES[0][0]
        return {"swept": "2026-08-20",
                "survivors": [{"id": "%s:%s:deadbeef" % (op, base),
                               "detail": "a survivor nobody has judged"}]}

    def test_a_moved_record_is_the_one_every_reader_reads(self) -> None:
        """Both record-reading cases follow the constant, or they fail.

        Run as a pair, because the single-name property is about the SET
        of readers: one reader still holding a private path is exactly
        the defect, and a case that only checked the repaired reader
        would pass while the other one drifted.
        """
        self.record()
        with record_moved_to(self.stale()):
            for method in ("test_recorded_sweep_is_fully_dispositioned",
                           "test_live_sweep_reproduces_the_record"):
                with self.subTest(reader=method):
                    result = run_case(test_mutants, "TestDiscovery", method)
                    self.assertFalse(
                        result.wasSuccessful(),
                        "%r passed over a record that was MOVED and holds "
                        "an undispositioned survivor, so it is reading "
                        "some other file — the writer updates one record "
                        "and this reader judges another" % method)

    def test_the_pre_fix_reader_passed_over_the_stale_file(self) -> None:
        """THE RED, measured: the hardcoded path answers about the old file.

        Both halves in one world. The redirected record holds an
        undispositioned survivor, and the pre-fix expression reports
        nothing missing — because it never looked there.
        """
        with record_moved_to(self.stale()) as moved:
            live = json.loads(moved.read_text(encoding="utf-8"))
            self.assertTrue(
                [s["id"] for s in live["survivors"]
                 if s["id"] not in test_mutants.DISPOSITIONS],
                "the redirected record carries nothing undispositioned, so "
                "there is no defect here for either reader to miss")
            self.assertEqual(
                dispositions_missing_before_one_name(), [],
                "the committed record at the hardcoded path is itself "
                "undispositioned, so this case cannot show the pre-fix "
                "reader passing for the wrong reason — re-derive it")

    def test_the_redirected_readers_are_silent_over_a_good_record(
            self) -> None:
        """THE SILENT HALF: it is the CONTENT that fails, not the move.

        Redirect to a byte copy of the committed record and both cases
        pass. Without this the case above is satisfied by a reader that
        fails whenever `SWEEP_RECORD` is touched at all, which would be
        a floor nobody could ever move a file past.
        """
        committed = json.loads(
            self.record().read_text(encoding="utf-8"))
        with record_moved_to(committed):
            for method in ("test_recorded_sweep_is_fully_dispositioned",
                           "test_live_sweep_reproduces_the_record"):
                with self.subTest(reader=method):
                    result = run_case(test_mutants, "TestDiscovery", method)
                    self.assertEqual(
                        (len(result.failures), len(result.errors)), (0, 0),
                        "%r fails over a faithful copy of the committed "
                        "record placed somewhere else, so it is asserting "
                        "the record's LOCATION rather than its contents: "
                        "%s" % (method, (result.failures + result.errors)
                                and (result.failures + result.errors)[0][1]
                                .strip().splitlines()[-1]))


class TestTheSweepFloorFiresWhenTheSweepGoesSilent(InstrumentCase):
    """The currency case can tell "found nothing" from "ran nothing".

    THE DEFECT. `test_live_sweep_reproduces_the_record` was one
    assertion — the live survivor ids equal the recorded ones — and both
    sides are EMPTY when all is well. Two sweeps that found nothing
    satisfy it, including a sweep that found nothing because every cell
    was skipped. Skipping is the normal path here (`sweep_args` answers
    a reason rather than raising), so a base that loses its bindings
    removes five cells silently and the harness degrades into an empty
    loop reporting a clean drawing.

    RESTORATION USED. `currency_held_before_the_floor` — the equality,
    kept whole — run inside the same silenced world the repaired case is
    run in, so the pre-fix pass is a measurement.

    THE ROSTER IS NOT COPIED. `floor_cells` reads the cell names out of
    the case's own code object; a list retyped here would go on naming
    eight cells after the original was cut to two.
    """

    def cells(self) -> tuple[str, ...]:
        """The floor's roster, asserting rather than silently answering `()`.

        Returns:
            The `base/op` names the currency case iterates.
        """
        roster = floor_cells()
        self.assertTrue(
            roster,
            "`test_live_sweep_reproduces_the_record` no longer holds "
            "exactly one literal tuple of `base/op` cells, so its floor "
            "either went away or stopped being readable from here — "
            "either way this pin is no longer watching it")
        return roster

    def test_a_sweep_that_runs_no_cell_at_all_fails_the_currency_case(
            self) -> None:
        """Every base drawing nothing: 15 cells skipped, and the case fails.

        The failure must name the cells, not merely occur: a floor that
        said only "the sweep is empty" would leave the reader to find
        which base lost its bindings.

        THE BEHAVIOURAL ASSERTION COMES FIRST, and the roster is read
        only after it. Ordered the other way, the pre-fix restoration
        goes red on "this case holds no cell tuple" — a true sentence
        about the SYNTAX that says nothing about whether the floor can
        see a silent sweep, which is what this pin is for.
        """
        with bases_drawing_nothing(), record_moved_to(
                {"swept": "2026-08-20", "survivors": []}):
            self.assertEqual(
                ran_cells(), set(),
                "the silenced world still runs cells, so this case is not "
                "measuring a sweep that ran nothing")
            result = run_case(test_mutants, "TestDiscovery",
                              "test_live_sweep_reproduces_the_record")
        self.assertFalse(
            result.wasSuccessful(),
            "the sweep ran NO cell at all and the currency case passed: "
            "`[] == []` is satisfied by a harness that discovered nothing "
            "and by one that never looked")
        self.assertEqual(
            sorted(set(self.cells()) - cells_the_floor_flagged(result)), [],
            "the sweep went entirely silent and these cells were not named "
            "in the failure, so the reader is told the sweep is stale "
            "without being told which cells stopped running")

    def test_the_pre_fix_currency_case_passed_over_the_silenced_sweep(
            self) -> None:
        """THE RED, measured: the bare equality holds when nothing ran."""
        with bases_drawing_nothing(), record_moved_to(
                {"swept": "2026-08-20", "survivors": []}):
            self.assertTrue(
                currency_held_before_the_floor(),
                "the pre-fix equality does not hold over a fully silenced "
                "sweep, so the defect this floor was written for is not "
                "the one being reproduced here")

    def test_losing_a_single_base_is_enough_to_fire(self) -> None:
        """Discrimination: the floor names the base that went, not all of them.

        The real failure removes cells one at a time. A floor that only
        fired on total silence would sit quiet through every step of it,
        so one base is emptied and the failure is required to name that
        base's cells and to leave the others alone.

        BOTH SIDES ARE MEASURED, not read off the roster: the cells that
        stopped are the difference between the live sweep and the
        crippled one. A `startswith` over the floor's own literal would
        be asking the floor to agree with itself.
        """
        gone = test_mutants.SWEEP_BASES[0][0]
        before = ran_cells()
        with bases_drawing_nothing(gone):
            expected = sorted(before - ran_cells())
            others = sorted(ran_cells())
        self.assertTrue(expected and others,
                        "emptying base %r stops %d cell(s) and leaves %d "
                        "running, so it cannot show the floor "
                        "discriminating" % (gone, len(expected), len(others)))
        with bases_drawing_nothing(gone):
            survivors, _skipped = test_mutants.sweep_cells()
            with record_moved_to({"swept": "2026-08-20",
                                  "survivors": survivors}):
                result = run_case(test_mutants, "TestDiscovery",
                                  "test_live_sweep_reproduces_the_record")
        flagged = cells_the_floor_flagged(result)
        self.assertEqual(
            sorted(set(expected) - flagged), [],
            "base %r builds nothing and its cells are unflagged: the floor "
            "only notices a sweep that has gone entirely silent, which is "
            "the last step of the decay and not the first" % gone)
        self.assertEqual(
            sorted(flagged & set(others)), [],
            "the floor flags cells that are still running, so it cannot "
            "point at the base that stopped")

    def test_the_live_sweep_runs_every_cell_the_floor_names(self) -> None:
        """THE SILENT HALF: unpatched, the floor is satisfied and quiet.

        The subset is the assertion and not equality, deliberately. A
        cell becoming buildable is a gap in the floor for the check's
        owner to close; making it fail HERE would turn a widened sweep
        into a red in a curator's file, which is a decision this pin does
        not get to make.
        """
        roster, ran = self.cells(), ran_cells()
        self.assertEqual(
            sorted(set(roster) - ran), [],
            "the floor names cells the live sweep does not run, so the "
            "currency case is red for everyone right now")
        result = run_case(test_mutants, "TestDiscovery",
                          "test_live_sweep_reproduces_the_record")
        self.assertTrue(
            result.wasSuccessful(),
            "the currency case is failing against the tree as committed: "
            "%s" % ((result.failures + result.errors) and
                    (result.failures + result.errors)[0][1]
                    .strip().splitlines()[-1]))


def kill_report(total: int, skipped: int, bad: tuple[str, ...] = ()) -> dict:
    """One driver suite report, in the shape the mortality arms read.

    Args:
        total: `testsRun` — which COUNTS SKIPS, and is the whole reason
            the skip arm cannot be folded into the population arm.
        skipped: How many of those tests were skipped.
        bad: Test ids that failed, i.e. the kill's witnesses.

    Returns:
        A report `_bad`, `witnesses` and both arms can read.
    """
    return {"total": total, "skipped": skipped, "failures": list(bad),
            "errors": [], "unexpected": [], "drops": 1}


def sweep_stub(check: str, inert: dict, killed: dict) -> Any:
    """A real `Sweep` filled in by hand, so no subprocess has to run.

    The live sweep copies the tree and re-runs the whole suite once per
    check; it is gated behind `MUTANTS_MORTALITY=1` and costs twenty
    minutes. The VERDICTS are pure functions of two reports, so they are
    exercised over two reports. `probes` is left empty on purpose:
    `assertKillTookEffect` returns early for a check it has no probe
    for, which leaves exactly the arms under test in the path.

    Args:
        check: The killed check's name.
        inert: The patched-but-unkilled model-tier report.
        killed: The report from the run with `check` killed.

    Returns:
        A `mutants_mortality.Sweep` carrying just those two reports.
    """
    built = mutants_mortality.Sweep(Path("/nonexistent/killed"), "pin")
    built.inert["model"] = inert
    built.kills[check] = killed
    return built


def verdict_of(check: str, inert: dict, killed: dict) -> str:
    """Run the REAL `assertMortal` over a stubbed sweep; report what it said.

    Args:
        check: The killed check's name.
        inert: The inert model-tier report.
        killed: The report from the killed run.

    Returns:
        The assertion message `assertMortal` produced, or the empty
        string when it passed — which is itself one of the answers
        under test.
    """
    case = mutants_mortality._Mortal("assertMortal")
    with mock.patch.object(mutants_mortality, "sweep",
                           lambda: sweep_stub(check, inert, killed)):
        try:
            case.assertMortal(check)
        except AssertionError as said:
            return str(said)
    return ""


def verdict_before_the_skip_arm(check: str, inert: dict, killed: dict) -> str:
    """`assertMortal` as it stood before `b4a49d8`: no run-level question.

    The pre-fix body, kept whole. It read only `_bad` — failures, errors
    and unexpected successes — so a run in which nothing executed and a
    run in which everything passed reached the same sentence.

    Args:
        check: The killed check's name.
        inert: The inert model-tier report.
        killed: The report from the killed run.

    Returns:
        What the pre-fix assertion said, or the empty string when it
        passed.
    """
    case = mutants_mortality._Mortal("assertMortal")
    with mock.patch.object(mutants_mortality, "sweep",
                           lambda: sweep_stub(check, inert, killed)):
        try:
            case.assertKillTookEffect(check)
            seen = mutants_mortality.sweep().witnesses(check)
            case.assertTrue(seen,
                            "NOTHING notices %r going blind: every test "
                            "that names it passes with the detector dead"
                            % check)
        except AssertionError as said:
            return str(said)
    return ""


class TestTheKillReportTellsSkippedFromSurvived(InstrumentCase):
    """A kill that skipped every test does not read as a check nobody guards.

    THE DEFECT. The mortality driver has always written `skipped`, and
    nothing read it. The only reader of a kill's report was `_bad` —
    failures, errors, unexpected successes — so a kill that turned every
    test naming the check into a SKIP left `_bad` empty, left
    `witnesses` empty, and `assertMortal` announced "NOTHING notices it
    going blind". That is a verdict about the TESTS delivered over a run
    in which nothing executed, and it would be repaired by writing a
    guard for a detector that already had one.

    `total` COULD NOT STAND IN, which is the part worth measuring rather
    than believing: `total` is `testsRun`, and unittest counts a skip in
    `testsRun`, so a wholesale skip holds it exact. The first case below
    measures that against a real runner instead of asserting it.

    RESTORATION USED. `verdict_before_the_skip_arm` — the pre-fix
    `assertMortal` body, kept whole. Under it the skipped run, the
    truncated run and the genuinely-survived run all produce the same
    sentence, which is the case this class exists to make.

    THE SIBLING ALREADY HAD THIS. `guard_mutants.verdict` table-drives
    six answers including `SKIPPED`, and
    `TestTheGuardCensusVerdictNeverReadsAnExitCode` above pins them; this
    class is the same discipline for the driver that lacked it, which is
    why it is table-driven too.
    """

    def mortal(self) -> Any:
        """The row assertion, or a sentence saying it is gone.

        THE NAME PINNED HERE IS `assertMortal`, which predates the fix,
        and NOT `assertRanUnderTheKill`, which the fix introduced. An
        anchor is as prescriptive as an assertion: naming the new helper
        would require every future repair to keep that particular
        method, when what this class is owed is the VERDICTS. A repair
        that folds the arm back inline and still tells the three runs
        apart passes every case below, as it should.

        Returns:
            `_Mortal.assertMortal`.
        """
        return self.part(
            mutants_mortality._Mortal, "assertMortal",
            "the mortality rows have no shared assertion any more, so "
            "there is nothing here that can be asked what it says about "
            "a kill that ran nothing")

    def check(self) -> str:
        """A live model-tier check name, so the tier lookup is the real one.

        Returns:
            The first model kill, which `assertRanUnderTheKill` must
            route to the `model` tier rather than to `render`.
        """
        model = mutants_mortality._MODEL_KILLS
        self.assertTrue(model, "no model-tier kills are registered, so "
                               "this class has no subject")
        return model[0]

    def test_unittest_counts_a_skip_in_tests_run(self) -> None:
        """THE PREMISE, measured: a skipped test still moves `testsRun`.

        The whole fix rests on this. If `testsRun` excluded skips the
        population arm would already catch a wholesale skip and the new
        arm would be dead weight, so the claim is run against a real
        runner rather than restated. Bound to the driver's own two
        expressions first, so this is not measuring a re-typed stand-in.
        """
        driver = mutants_mortality._DRIVER
        for expression in ('"total": result.testsRun',
                           '"skipped": len(result.skipped)'):
            with self.subTest(expression=expression):
                self.assertIn(
                    expression, driver,
                    "the driver no longer builds its report this way, so "
                    "the runner measured below is not the one the arms "
                    "read from")

        class _EverythingSkips(unittest.TestCase):
            """Three tests, none of which runs. Local so nothing collects it."""

            @unittest.skip("the kill turned this into a skip")
            def test_one(self) -> None:
                """Skip."""

            @unittest.skip("the kill turned this into a skip")
            def test_two(self) -> None:
                """Skip."""

            @unittest.skip("the kill turned this into a skip")
            def test_three(self) -> None:
                """Skip."""

        suite = unittest.TestLoader().loadTestsFromTestCase(_EverythingSkips)
        ran = unittest.TextTestRunner(stream=io.StringIO(),
                                      verbosity=0).run(suite)
        self.assertEqual(
            (ran.testsRun, len(ran.skipped)), (3, 3),
            "unittest has stopped counting a skip in `testsRun`. If that "
            "is real, the population arm now catches a wholesale skip on "
            "its own and this whole class needs re-deriving")
        self.assertTrue(
            ran.wasSuccessful(),
            "a suite of nothing but skips no longer reports success, so "
            "the silence this fix is about would announce itself")

    def test_the_three_kill_verdicts_are_table_driven(self) -> None:
        """Skipped, dropped and survived reach three different sentences.

        The rows are ordered by the precedence the arms impose: a report
        can have moved BOTH its skip count and its population, and only
        one answer is right for a reader trying to find out what
        happened. The distinctness assertion at the end is the point —
        three verdicts that spell themselves the same way are one
        verdict wearing three names.
        """
        self.mortal()
        check = self.check()
        inert = kill_report(1897, 49)
        table = [
            ("skipped", kill_report(1897, 77), ("49", "77"),
             "THE ROW THE FIX WAS WRITTEN FOR — the kill turned 28 tests "
             "into skips and held `total` exact"),
            ("dropped", kill_report(1801, 49), ("1897", "1801"),
             "the kill never collected 96 tests, and tests that were "
             "never collected are not evidence about a detector"),
            ("survived", kill_report(1897, 49), (),
             "it ran, it skipped nothing, and nothing went bad: the "
             "finding this instrument exists to produce"),
        ]
        said: dict[str, str] = {}
        for label, killed, figures, why in table:
            with self.subTest(verdict=label, why=why):
                spoke = verdict_of(check, inert, killed)
                self.assertTrue(
                    spoke, "the %s row passed silently, so this row is not "
                           "a verdict at all" % label)
                self.assertIn(
                    check, spoke,
                    "the %s verdict does not name the killed check, so a "
                    "reader cannot tell which row it is about" % label)
                for figure in figures:
                    self.assertIn(
                        figure, spoke,
                        "the %s verdict does not carry %s, so it reports "
                        "that something moved without reporting how far: "
                        "%r" % (label, figure, spoke))
                said[label] = spoke
        for one, other in itertools.combinations(sorted(said), 2):
            with self.subTest(pair=(one, other)):
                self.assertNotEqual(
                    said[one], said[other],
                    "the %s run and the %s run are reported in the SAME "
                    "words. Non-discriminating is as bad as mute and "
                    "harder to spot: the reader is told a detector is "
                    "unguarded when the truth is that the run went "
                    "missing" % (one, other))
        self.assertNotIn(
            "77", said["survived"],
            "the survived verdict quotes the skipped row's figures, so it "
            "is not a verdict about this run: %r" % said["survived"])

    def test_a_kill_a_real_test_noticed_still_reads_as_mortal(self) -> None:
        """THE SILENT HALF: the arm has not been made to reject every row.

        An arm that raised on everything would satisfy all three rows
        above and destroy the instrument, so the live pole is asserted
        over a report in which a named test really went bad.
        """
        self.mortal()
        check = self.check()
        witnessed = kill_report(1897, 49,
                                bad=("tests.test_backend.T.test_notices",))
        self.assertEqual(
            verdict_of(check, kill_report(1897, 49), witnessed), "",
            "a kill that ran the whole tier, skipped nothing, and was "
            "caught by a named test is now rejected — the rows this "
            "instrument exists to report can no longer be reported")

    def test_the_pre_fix_assertion_spelled_all_three_the_same_way(
            self) -> None:
        """THE RED, measured: one sentence for three different runs.

        Run under `verdict_before_the_skip_arm`, the skipped run, the
        truncated run and the genuinely-survived run are reported
        identically — as a detector nobody guards. That is the verdict
        being wrong, not merely thin: it points at the tests when the
        fault is in the run.
        """
        check = self.check()
        inert = kill_report(1897, 49)
        before = {
            "skipped": verdict_before_the_skip_arm(
                check, inert, kill_report(1897, 77)),
            "dropped": verdict_before_the_skip_arm(
                check, inert, kill_report(1801, 49)),
            "survived": verdict_before_the_skip_arm(
                check, inert, kill_report(1897, 49)),
        }
        self.assertEqual(
            before["skipped"], before["survived"],
            "the pre-fix assertion already told a skipped run from a "
            "survived one, so the defect being reproduced here is not "
            "the one that was fixed")
        self.assertEqual(
            before["dropped"], before["survived"],
            "the pre-fix assertion already told a truncated run from a "
            "survived one — re-derive this restoration")
        self.assertIn(
            "NOTHING notices", before["skipped"],
            "the pre-fix verdict over a wholly skipped run is not the "
            "detector-is-unguarded sentence, so this class is describing "
            "the wrong defect: %r" % before["skipped"])

    def test_the_control_refuses_a_patch_that_stops_running_tests(
            self) -> None:
        """The same arm one level up, where the controls decide what is real.

        `_control` is what licenses every row: it proves the
        instrumentation is inert before a single kill is believed. Its
        population check reads `total`, so it was equally happy about a
        patched tier that had stopped RUNNING — and an inert control is
        the one thing no row can be measured without.

        `_run` is stubbed rather than started: the arm is a comparison
        of two reports, and copying the tree to prove it would be
        measuring the copy.

        Every row's answer is RECORDED before it is judged, the empty
        string standing for a control that raised nothing. A `with
        assertRaises` inside the subTest would leave the dict short when
        a row passed, and this method would then die on a `KeyError` —
        an error-red that reads as a broken pin rather than as a silent
        control.
        """
        pristine = kill_report(1897, 49)
        rows = {"skipped": kill_report(1897, 77),
                "dropped": kill_report(1801, 49)}
        spoke: dict[str, str] = {}
        for label, inert in rows.items():
            built = mutants_mortality.Sweep(Path("/nonexistent/killed"), "pin")
            with mock.patch.object(mutants_mortality, "_run",
                                   side_effect=[pristine, inert]):
                try:
                    mutants_mortality._control(built, "model", False)
                    spoke[label] = ""
                except mutants_mortality.MortalityError as refused:
                    spoke[label] = str(refused)
            with self.subTest(control=label):
                self.assertTrue(
                    spoke[label],
                    "the controls ACCEPTED a %s model tier and licensed "
                    "every kill below it, so the rows would be measured "
                    "against a patch that is not inert" % label)
        self.assertIn(
            "77", spoke["skipped"],
            "the control refuses a tier the patch stopped running without "
            "saying how many tests stopped: %r" % spoke["skipped"])
        self.assertNotEqual(
            spoke["skipped"], spoke["dropped"],
            "losing tests and keeping them but not running them are "
            "refused in the same words, so the reader cannot tell which "
            "of two different repairs is called for")

    def test_the_control_accepts_a_patch_that_changes_nothing(self) -> None:
        """THE SILENT HALF: an honestly inert patch still licenses its rows.

        Without this the case above is satisfied by a control that
        refuses every sweep, which would take the whole instrument off
        the air rather than making it honest.
        """
        pristine, inert = kill_report(1897, 49), kill_report(1897, 49)
        built = mutants_mortality.Sweep(Path("/nonexistent/killed"), "pin")
        with mock.patch.object(mutants_mortality, "_run",
                               side_effect=[pristine, inert]):
            mutants_mortality._control(built, "model", False)
        self.assertEqual(
            (built.pristine["model"], built.inert["model"]),
            (pristine, inert),
            "an inert patch that ran and skipped exactly what the "
            "unpatched copy did was refused, so no kill below it can be "
            "measured at all")
