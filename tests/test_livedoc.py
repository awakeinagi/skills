"""Tests for the live-value tool in `tests/livedoc.py`.

Watched failing in every direction it claims to catch, because a checker
that only ever runs over a healthy tree proves nothing about itself — the
same argument `tests/census_probes.py` makes about the census guards, one
tool over. So each parse refusal below is exercised against a file that
really is malformed, `check` is watched failing on a value that really has
drifted, and the one that matters most — a calculator whose subject has
been deleted — is proved to raise rather than to quietly agree with the
number already written in the prose.
"""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import livedoc

REPO = Path(__file__).resolve().parents[1]

# THE TWO TESTS IN `TestTheRepoItself` READ THE GIT INDEX, which is the one
# thing a copied tree cannot carry. `mutants_mortality._copy_tree` reproduces
# every TRACKED FILE faithfully — all the markdown is there — and no `.git`,
# so `git ls-files` exits 128 and both tests ERROR instead of skipping. That
# is what took the mortality sweep down at `71d5144`: `_control` runs the
# whole model tier before instrumenting anything and refuses to measure a
# tier that is not green, so two errors here cost all 27 rows and the phase
# gate with them. `census_probes.py`'s scratch tree is the same condition.
#
# Presence of the checkout, mirroring the fixture gate on
# `TestInstrumentSweep.test_instruments_run_over_the_r5_fixture`
# (`skipUnless(<path>.is_dir(), "… not present")`). `.exists()` and not
# `.is_dir()` because `.git` is a FILE in a worktree and a directory in a
# clone, and this repo is worked in worktrees.
#
# DELIBERATELY NOT "does `git ls-files` succeed". A predicate that ran the
# command it gates could never disagree with it, so a genuinely broken git
# inside a real checkout would become a silent skip — the failure this repo
# calls a guard that cannot fail. Presence is the conservative half: it is
# false only when the tree provably has no index, and anything else still
# fails loudly. `TestTheCheckoutGate` holds both halves of that.
_CHECKOUT = (REPO / ".git").exists()

_NO_CHECKOUT = ("not a git checkout — a copied tree (the mortality sweep, "
                "census_probes) has the tracked FILES but no index")


class LiveDocCase(unittest.TestCase):
    """Base with a scratch directory and a one-line file writer.

    Attributes:
        tmp: A directory removed on teardown, where the malformed and
            drifted documents live. Nothing here writes into the repo.
    """

    def setUp(self) -> None:
        """Make the scratch directory."""
        self.tmp = Path(tempfile.mkdtemp(prefix="livedoc-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def doc(self, text: str, name: str = "doc.md") -> Path:
        """Write one scratch document.

        Args:
            text: The file's whole content.
            name: Its filename inside the scratch directory.

        Returns:
            The path written.
        """
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def marked(self, name: str, stored: str) -> str:
        """Build one marker pair around a stored value.

        Written by concatenation rather than as a literal so this file can
        describe the syntax without the tool's own scan finding a marker
        in it — the same trap AGENTS.md hit while documenting the feature.

        Args:
            name: The calculator name.
            stored: The value to sit between the comments.

        Returns:
            The marker pair as one line of prose.
        """
        return "<!-- live:%s -->%s<!-- /live:%s -->" % (name, stored, name)


class TestCheckFindsDrift(LiveDocCase):
    """`check` is the guard: it must fail, name the marker, change nothing."""

    def test_check_fails_and_names_the_drifted_marker(self) -> None:
        """A stale stored value is reported with the marker and both sides."""
        path = self.doc("canvas.py is %s lines.\n"
                        % self.marked("canvas_py_lines", "~5.4k"))
        before = path.read_bytes()
        drifted = livedoc.check_files([path])
        self.assertEqual(len(drifted), 1, drifted)
        self.assertIn("live:canvas_py_lines", drifted[0])
        self.assertIn("~5.4k", drifted[0])
        self.assertIn(livedoc.canvas_py_lines(), drifted[0])
        self.assertEqual(path.read_bytes(), before,
                         "check must change nothing; refresh is the repair")

    def test_check_passes_when_the_stored_value_is_fresh(self) -> None:
        """The same document, current, reports no drift at all."""
        path = self.doc("canvas.py is %s lines.\n"
                        % self.marked("canvas_py_lines",
                                      livedoc.canvas_py_lines()))
        self.assertEqual(livedoc.check_files([path]), [])

    def test_check_reads_every_marker_not_just_the_first(self) -> None:
        """Two markers on one line: a drift in the second is still found.

        A first-match-wins reader is the exact hole the census guards in
        `tests/test_mutants.py` were each found to have, one at a time.
        Cheaper to pin here than to rediscover.
        """
        path = self.doc(
            "%s and %s\n" % (self.marked("canvas_py_lines",
                                         livedoc.canvas_py_lines()),
                             self.marked("test_backend_cases", "120")))
        drifted = livedoc.check_files([path])
        self.assertEqual(len(drifted), 1, drifted)
        self.assertIn("live:test_backend_cases", drifted[0])


class TestRefreshRepairs(LiveDocCase):
    """`refresh` is the repair, and running it twice must be a no-op."""

    def test_refresh_writes_the_derived_value_back(self) -> None:
        """A drifted document comes back current and checks clean."""
        path = self.doc("canvas.py is %s lines.\n"
                        % self.marked("canvas_py_lines", "~5.4k"))
        changed = livedoc.refresh_files([path])
        self.assertEqual(len(changed), 1, changed)
        self.assertIn(livedoc.canvas_py_lines(),
                      path.read_text(encoding="utf-8"))
        self.assertEqual(livedoc.check_files([path]), [])

    def test_refresh_is_idempotent(self) -> None:
        """The second run reports nothing and leaves the bytes identical.

        Byte equality and not just an empty report, because the failure
        this rules out is a rewrite that changes the file without changing
        any value — a trailing-newline or line-ending churn that would put
        this tool in every diff forever.
        """
        path = self.doc("canvas.py is %s lines.\n"
                        % self.marked("canvas_py_lines", "~5.4k"))
        livedoc.refresh_files([path])
        settled = path.read_bytes()
        self.assertEqual(livedoc.refresh_files([path]), [])
        self.assertEqual(path.read_bytes(), settled)

    def test_refresh_leaves_an_already_current_file_untouched(self) -> None:
        """A fresh file is not rewritten, so its mtime does not move."""
        path = self.doc("canvas.py is %s lines.\n"
                        % self.marked("canvas_py_lines",
                                      livedoc.canvas_py_lines()))
        stamp = path.stat().st_mtime_ns
        self.assertEqual(livedoc.refresh_files([path]), [])
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_refresh_rewrites_every_marker_in_one_file(self) -> None:
        """Two drifted values in one file both land, offsets intact.

        The rewrite walks right to left so an earlier replacement cannot
        invalidate a later marker's offsets. A left-to-right version passes
        whenever the two values happen to be the same length, which is what
        makes this worth a test of its own.
        """
        path = self.doc(
            "%s\nand a much longer sentence holding %s\n"
            % (self.marked("canvas_py_lines", "~5.4k"),
               self.marked("test_backend_cases", "120")))
        self.assertEqual(len(livedoc.refresh_files([path])), 2)
        self.assertEqual(livedoc.check_files([path]), [])


class TestRefreshCannotEatItsOwnAnswer(LiveDocCase):
    """The repair command that was reported to corrupt what it repaired.

    TWO WITNESSES, ONE WRONG DIAGNOSIS. TASK-ELBOX (concern 8) and
    curator batch 31 (concern 6) both watched `refresh` turn a marker
    holding the sentinel `0` into `1410410410410410410410410410` and
    both concluded the replacement was substring-based, so a stored
    value that is a substring of its answer eats itself. Measured here
    before anything was written: it is not. `refresh_files` splices by
    OFFSET (`text[:marker.start] + fresh + text[marker.end:]`) and walks
    each file's markers right to left, so a digit sentinel is perfectly
    safe — the first test below drives the exact shape the reports
    blame and it converges in one pass. The advice they leave behind
    ("use a letter, not a digit") is harmless and its stated reason is
    wrong, which is worse than no reason: it points the next reader
    away from the real trigger.

    THE REAL TRIGGER IS A DUPLICATED PATH, and it is reproduced below.
    `refresh_files` scans ALL paths first and then rewrites file by
    file, so a path appearing twice in `paths` has its markers found
    twice AND is opened twice — n entries give n*n splices, each one
    laying the fresh value over the FIRST CHARACTER of the value the
    previous splice wrote. The answer is `fresh + fresh[1:] * (n*n - 1)`,
    which is exactly the shape of the corruption both reports quote:
    `1410` followed by `410` over and over.

    HOW A DUPLICATE GETS IN. `tracked_prose_files` builds `paths` from
    `git ls-files`, which prints one row per STAGE for an unmerged path
    — and both witnesses hit this while resolving a merge conflict in
    the file that carries the marker. That route is named rather than
    asserted (a test that needs a conflicted index would be measuring
    git); what is pinned is the function's own contract, which is where
    the repair belongs.

    WHY THIS IS GREEN. It asserts the CORRUPTION, not the fix, which is
    the `test_the_pipeline_really_does_not_settle` shape: the repair is
    one line in `refresh_files` (de-duplicate `paths`, or scan and write
    per unique file), it belongs to whoever owns this module, and the
    day it lands this class fails and asks to be rewritten as the
    guarantee instead. `check` catches the mangled value afterwards, so
    nothing could ship — but a repair tool that silently damages what it
    repairs is the "silence is a bug" shape and should not be
    undocumented. Origin: TASK-ELBOX concern 8 and curator batch 31
    concern 6; measured and re-diagnosed during curator batch 33,
    2026-08-18.
    """

    def test_a_sentinel_that_is_a_substring_of_its_answer_is_safe(self
                                                                  ) -> None:
        """The shape both reports blame, driven directly. It converges.

        Three sentinels, each genuinely a substring of the derived
        value and derived from it rather than hardcoded, so this cannot
        go stale when the suite grows: the first character, the tail,
        and the whole answer with its last character dropped.
        """
        fresh = livedoc.canvas_py_lines()
        for n, stored in enumerate((fresh[0], fresh[1:], fresh[:-1])):
            with self.subTest(stored=stored):
                self.assertIn(stored, fresh)
                self.assertNotEqual(stored, fresh)
                path = self.doc(
                    "canvas.py is %s lines.\n"
                    % self.marked("canvas_py_lines", stored),
                    name="substring-%d.md" % n)
                self.assertEqual(len(livedoc.refresh_files([path])), 1)
                self.assertEqual(livedoc.check_files([path]), [])
                self.assertIn(">%s<" % fresh,
                              path.read_text(encoding="utf-8"))

    def test_the_same_file_named_twice_is_written_into_itself(self) -> None:
        """A PREMISE PIN on the corruption, with its arithmetic.

        `n` copies of one path produce `n*n` splices and the value
        `fresh + fresh[1:] * (n*n - 1)`. Asserted for two and three
        copies rather than one, because a formula pinned at a single
        point is a coincidence.
        """
        fresh = livedoc.canvas_py_lines()
        for copies in (2, 3):
            with self.subTest(copies=copies):
                path = self.doc(
                    "canvas.py is %s lines.\n"
                    % self.marked("canvas_py_lines", "0"),
                    name="dup-%d.md" % copies)
                livedoc.refresh_files([path] * copies)
                got = path.read_text(encoding="utf-8").split("-->")[1] \
                                                      .split("<!--")[0]
                self.assertEqual(
                    got, fresh + fresh[1:] * (copies * copies - 1),
                    "`refresh_files` handed %d copies of one path wrote "
                    "%r. If it now writes %r, the de-duplication landed "
                    "— rewrite this class as the guarantee and delete "
                    "its diagnosis paragraph" % (copies, got, fresh))

    def test_the_damage_is_what_check_then_reports(self) -> None:
        """The system's own alarm, so the blast radius is named too.

        What kept this from shipping is that `check` fails loudly on the
        mangled value. Pinned so the pair reads honestly: the repair
        tool can damage a file, and the guard catches it, and those are
        two separate facts about the same module.
        """
        path = self.doc("canvas.py is %s lines.\n"
                        % self.marked("canvas_py_lines", "0"))
        livedoc.refresh_files([path, path])
        drifted = livedoc.check_files([path])
        self.assertEqual(len(drifted), 1, drifted)
        self.assertIn("live:canvas_py_lines", drifted[0])
        self.assertEqual(
            len(livedoc.refresh_files([path])), 1,
            "a refresh over the DAMAGED file, named once, must repair "
            "it — the tool is only dangerous on the duplicate")
        self.assertEqual(livedoc.check_files([path]), [])


class TestACalculatorNeverFallsBackToTheProse(LiveDocCase):
    """The failure mode that would make this tool worse than literals."""

    def test_a_calculator_whose_subject_is_gone_fails_loudly(self) -> None:
        """With `canvas.py` absent, the derivation raises and names it.

        This is the whole point of the file. A calculator that could not
        find its subject and returned the stored value would report every
        document current forever, and would do it in exactly the voice of a
        healthy run. The message has to name the missing path so the reader
        can tell "the repo moved" from "this calculator is stale".
        """
        with mock.patch.object(livedoc, "REPO", self.tmp), \
                self.assertRaises(AssertionError) as caught:
            livedoc.canvas_py_lines()
        self.assertIn("canvas.py", str(caught.exception))

    def test_check_propagates_that_failure_instead_of_passing(self) -> None:
        """`check` over a live document raises rather than reporting clean."""
        path = self.doc("canvas.py is %s lines.\n"
                        % self.marked("canvas_py_lines", "~5.4k"))
        with mock.patch.object(livedoc, "REPO", self.tmp), \
                self.assertRaises(AssertionError):
            livedoc.check_files([path])

    def test_an_empty_frontend_tree_fails_rather_than_summing_zero(
            self) -> None:
        """A moved `src/` must not read as a UI of `~0.0k` lines."""
        (self.tmp / "frontends" / "wysiwyg-grilling" / "src").mkdir(
            parents=True)
        with mock.patch.object(livedoc, "REPO", self.tmp), \
                self.assertRaises(AssertionError) as caught:
            livedoc.frontend_src_lines()
        self.assertIn("~0.0k", str(caught.exception))

    def test_a_discovery_pattern_matching_nothing_fails(self) -> None:
        """A renamed module must not report a suite of zero tests."""
        with self.assertRaises(AssertionError) as caught:
            livedoc._discovered_case_count("test_no_such_module.py")
        self.assertIn("measures nothing", str(caught.exception))


class TestTheParserRefusesRatherThanSkips(LiveDocCase):
    """Every malformed marker is an error; none of them is a silent skip."""

    def assertRefused(self, text: str, *needles: str) -> None:
        """Assert `scan` rejects `text` with a message containing `needles`.

        Args:
            text: The document to scan.
            needles: Substrings the failure must mention, so the message is
                pinned as diagnostic rather than merely present.
        """
        with self.assertRaises(AssertionError) as caught:
            livedoc.scan(text, "doc.md")
        for needle in needles:
            self.assertIn(needle, str(caught.exception))

    def test_an_unregistered_name_fails_loudly(self) -> None:
        """A marker nobody can compute is refused, and the known names listed.

        Not a skip, deliberately: prose claiming a derivation that does not
        exist looks maintained from the outside and is a plain literal.
        """
        path = self.doc("value: %s\n" % self.marked("no_such_value", "7"))
        with self.assertRaises(AssertionError) as caught:
            livedoc.check_files([path])
        self.assertIn("no_such_value", str(caught.exception))
        self.assertIn("canvas_py_lines", str(caught.exception))

    def test_an_unclosed_marker_fails_loudly(self) -> None:
        """An open with no close names the line it opened on."""
        self.assertRefused("a <!-- live:canvas_py_lines -->~5.4k\nb\n",
                           "doc.md:1", "never closed")

    def test_a_stray_close_fails_loudly(self) -> None:
        """A close with no open is refused rather than ignored."""
        self.assertRefused("a\nb <!-- /live:canvas_py_lines -->\n",
                           "doc.md:2", "never opened")

    def test_a_mismatched_close_fails_loudly(self) -> None:
        """The repeated name is what stops a typo swallowing a paragraph."""
        self.assertRefused(
            "<!-- live:canvas_py_lines -->x<!-- /live:test_backend_cases -->",
            "canvas_py_lines", "test_backend_cases")

    def test_a_nested_marker_fails_loudly(self) -> None:
        """Nesting would make the inner value part of the outer rewrite."""
        self.assertRefused(
            "<!-- live:canvas_py_lines --><!-- live:test_backend_cases -->"
            "x<!-- /live:test_backend_cases --><!-- /live:canvas_py_lines -->",
            "do not nest")

    def test_a_value_spanning_a_newline_fails_loudly(self) -> None:
        """One line, so a value fits a table cell and a diff stays readable."""
        self.assertRefused(
            "<!-- live:canvas_py_lines -->~5.4k\nmore<!-- "
            "/live:canvas_py_lines -->", "spans a newline")

    def test_an_uppercase_placeholder_is_not_a_marker(self) -> None:
        """Prose can spell the syntax with `NAME` without creating one.

        Names are `[a-z][a-z0-9_]*`, and AGENTS.md leans on that to document
        the feature inside the very file the scan reads. If the grammar ever
        accepts uppercase, that paragraph becomes a marker for a calculator
        that does not exist and the hook fails on the documentation.
        """
        self.assertEqual(
            livedoc.scan("<!-- live:NAME -->v<!-- /live:NAME -->", "doc.md"),
            [])

    def test_a_malformed_registration_is_refused(self) -> None:
        """A name the marker regex could not find is rejected at import.

        Registering it would put a value in the prose that looks live, is
        never scanned, and therefore never checked.
        """
        with self.assertRaises(ValueError):
            livedoc.calculator("Not-A-Name")(lambda: "x")

    def test_a_duplicate_registration_is_refused(self) -> None:
        """Two derivations under one name would race on import order."""
        with self.assertRaises(ValueError) as caught:
            livedoc.calculator("canvas_py_lines")(lambda: "x")
        self.assertIn("registered twice", str(caught.exception))


class TestTheRepoItself(LiveDocCase):
    """The markers actually placed in this tree, and the census boundary."""

    @unittest.skipUnless(_CHECKOUT, _NO_CHECKOUT)
    def test_the_markers_in_tracked_prose_are_well_formed(self) -> None:
        """Every marker in the repo parses and names a real calculator.

        Structure, deliberately not freshness. A stale count is repaired by
        `python3 tests/livedoc.py refresh` and is caught by the pre-commit
        hook that runs `check`; a red test in this repo means "stop and
        investigate", which is the wrong instruction for a number a command
        fixes. What this pins is the half no `refresh` can repair — a marker
        that has been hand-mangled, or that points at a calculator somebody
        deleted.
        """
        paths = livedoc.tracked_prose_files()
        self.assertTrue(paths, "git listed no tracked markdown")
        pairs = livedoc._scan_all(paths)
        self.assertTrue(pairs, "no live markers in tracked prose at all — "
                               "the tool is installed and watching nothing")
        for path, marker in pairs:
            self.assertIn(marker.name, livedoc.CALCULATORS,
                          "%s:%d" % (livedoc._rel(path), marker.line))

    @unittest.skipUnless(_CHECKOUT, _NO_CHECKOUT)
    def test_every_registered_calculator_is_placed(self) -> None:
        """A calculator no marker reads has stopped being watched."""
        self.assertEqual(
            livedoc.unplaced_calculators(livedoc.tracked_prose_files()), [])

    def test_the_census_sentences_stay_out_of_livedoc(self) -> None:
        """`SESSION-HANDOVER.md` carries no live marker, on purpose.

        The boundary between the two systems, held mechanically instead of
        by a paragraph nobody rereads. Five guards in `tests/test_mutants.py`
        (`handover_catalogue_reds`, `handover_coverage_totals`,
        `handover_durable_counts`, `handover_render_reds`, and the render-row
        derivation beside them) each read that file and each require their
        subject to appear EXACTLY ONCE. They are tripwires: a red count
        moving is a claim about the product and is meant to stop a human.
        `refresh` would repair the sentence the human was supposed to read
        the failure of, and the readers match on the numbers in place, so a
        marker inside `**N / N / N** for` + backticked filenames either
        breaks the guard or makes the calculator generate the guard's own
        anchor — a guard checking generated text against its generator,
        which is the calibration-literal defect written up beside
        `CATALOGUE_RED_IDS`.

        If this is ever deliberately reversed, delete this test in the same
        change and say why. Do not leave it passing over a file it no longer
        describes.
        """
        handover = REPO / "SESSION-HANDOVER.md"
        if not handover.is_file():
            self.skipTest("SESSION-HANDOVER.md is not in this tree, so the "
                          "census boundary cannot be checked here")
        self.assertEqual(
            livedoc.scan(handover.read_text(encoding="utf-8"),
                         "SESSION-HANDOVER.md"), [],
            "a live marker has appeared in the census's file; read this "
            "test's docstring before deciding it is fine")


class TestTheCheckoutGate(LiveDocCase):
    """`_CHECKOUT` itself, watched doing both of its jobs.

    THE MISSING POLE, and it is missing in the direction that cost real
    work. A `skipUnless` is invisible from inside a healthy checkout: the
    two tests it guards run and pass there whether the predicate is right,
    wrong, or attached to nothing at all. The condition it exists for lives
    somewhere nobody runs the suite by hand — a tree copied out of the index
    by `mutants_mortality._copy_tree` — so it has to be CONSTRUCTED here
    rather than assumed. At `71d5144` it was assumed, the two tests errored
    inside the sweep's pristine control, `_control` refused to measure a
    tier that is not green before instrumentation, and all 27 mortality rows
    died with it.

    Both directions, because one of them alone proves the wrong thing.
    Watching the tests skip proves only that SOMETHING skipped them, which a
    predicate hard-wired to `False` would also do — and that predicate would
    silently retire two live guards in the real repo forever. So the second
    test puts a `.git` back and watches the same two tests FAIL, which is
    what says the gate is reading the tree rather than switched off.
    """

    # Named rather than pattern-matched, so a rename shows up as `-k`
    # selecting nothing — the anchor discipline `census_probes.py` uses.
    GATED = ("test_every_registered_calculator_is_placed",
             "test_the_markers_in_tracked_prose_are_well_formed")

    def scratch(self, with_git: bool = False) -> Path:
        """Build the copied-tree condition the mortality sweep produces.

        Only the two modules are copied. The subprocess then imports nothing
        else and the probe stays under a second, and what is being
        reproduced is not the sweep's file list but its SHAPE: a `tests/`
        directory whose parent holds no git index. That is
        `_copy_tree`'s output exactly — it walks `git ls-files` and copies
        every tracked file, `.git` not being one of them — and
        `census_probes._scratch`'s too.

        Args:
            with_git: Put an (invalid) `.git` back, to flip `_CHECKOUT`
                true in the subprocess without making the tree usable.

        Returns:
            The tree's root, ready to run `unittest discover -s tests` in.
        """
        tree = self.tmp / ("with-git" if with_git else "copied")
        (tree / "tests").mkdir(parents=True)
        for name in ("livedoc.py", "test_livedoc.py"):
            shutil.copy2(Path(__file__).parent / name, tree / "tests" / name)
        if with_git:
            (tree / ".git").write_text("gitdir: /nonexistent\n",
                                       encoding="utf-8")
        return tree

    def run_gated(self, tree: Path) -> str:
        """Run only the two gated tests inside `tree`.

        Args:
            tree: A root built by `scratch`.

        Returns:
            Everything the run printed, verbosely, so the skip REASON is
            readable and not just the count.
        """
        argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests",
                "-p", "test_livedoc.py", "-v"]
        for name in self.GATED:
            argv += ["-k", name]
        done = subprocess.run(argv, cwd=str(tree), capture_output=True,
                              text=True, timeout=300)
        return done.stdout + done.stderr

    def test_the_gated_tests_skip_in_a_copied_tree(self) -> None:
        """The sweep's condition: both skip, neither errors.

        `Ran 2 tests` is asserted before the verdict, and is the half that
        keeps this honest over time. `-k` selecting one test, or none, would
        leave the skip count "right" while the gate had drifted off a
        renamed test — the vacuous green that `test_instruments_run_over_
        the_r5_fixture` guards against by asserting its corpus non-empty.
        """
        said = self.run_gated(self.scratch())
        self.assertIn("Ran 2 tests", said,
                      "the gated tests have been renamed; re-point GATED")
        self.assertIn("OK (skipped=2)", said, said[-2000:])
        self.assertIn("no index", said,
                      "they skipped for some reason other than the gate")

    def test_the_gate_reads_the_tree_rather_than_being_switched_off(
            self) -> None:
        """With a `.git` present, the same two tests fail loudly again.

        The tree is still unusable — the gitfile points nowhere — so `git
        ls-files` exits non-zero and `tracked_prose_files` refuses. That is
        the asymmetry `_CHECKOUT` is built for and the reason it is a
        presence check rather than a trial run of the command it gates: a
        broken git inside something that claims to be a checkout has to
        fail, not skip. A predicate that asked "does `git ls-files` work"
        would turn this exact case green.
        """
        said = self.run_gated(self.scratch(with_git=True))
        self.assertIn("Ran 2 tests", said,
                      "the gated tests have been renamed; re-point GATED")
        self.assertIn("FAILED", said, said[-2000:])
        self.assertIn("git could not list tracked markdown", said)


class TestTheCommandLine(LiveDocCase):
    """The two verbs, through `main`, with the scan surface stubbed."""

    def run_main(self, verb: str, paths: list[Path]) -> tuple[int, str]:
        """Run one verb over `paths` instead of over the real repo.

        Args:
            verb: `check` or `refresh`.
            paths: The files to stand in for tracked markdown.

        Returns:
            `main`'s exit code and everything it printed.
        """
        out = io.StringIO()
        with mock.patch.object(livedoc, "tracked_prose_files",
                               return_value=paths), \
                contextlib.redirect_stdout(out):
            code = livedoc.main([verb])
        return code, out.getvalue()

    def whole_surface(self, stored: dict[str, str]) -> Path:
        """Write one document carrying a marker for every calculator.

        `check` fails on a registered name no document uses, so a stub scan
        surface has to cover the registry or every command test would be
        measuring that rule instead of the one it names.

        Args:
            stored: Values to store, by name; a name left out is stored at
                its current derived value.

        Returns:
            The document written.
        """
        lines = [self.marked(name, stored.get(name, fn()))
                 for name, fn in sorted(livedoc.CALCULATORS.items())]
        return self.doc("\n".join(lines) + "\n")

    def test_check_exits_nonzero_on_drift_and_zero_when_clean(self) -> None:
        """The guard's contract as a hook: the exit code carries the verdict."""
        path = self.whole_surface({"canvas_py_lines": "~5.4k"})
        code, said = self.run_main("check", [path])
        self.assertEqual(code, 1)
        self.assertIn("live:canvas_py_lines", said)
        self.assertIn("livedoc.py refresh", said)
        self.assertEqual(self.run_main("refresh", [path])[0], 0)
        self.assertEqual(self.run_main("check", [path])[0], 0)

    def test_check_fails_on_a_calculator_no_document_uses(self) -> None:
        """An unplaced calculator fails `check` like a drifted value does."""
        path = self.doc("nothing live here\n")
        code, said = self.run_main("check", [path])
        self.assertEqual(code, 1)
        self.assertIn("live:canvas_py_lines", said)

    def test_an_unknown_verb_is_a_usage_error(self) -> None:
        """Exit 2, distinct from both a clean run and a real finding."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(livedoc.main([]), 2)
            self.assertEqual(livedoc.main(["fix"]), 2)
            self.assertEqual(livedoc.main(["check", "refresh"]), 2)
        self.assertIn("usage:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
