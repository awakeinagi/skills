"""Live values in tracked prose: counts a function derives, not literals.

WHY THIS EXISTS. Numbers written into this repo's prose rot silently, and
the rot is invisible because a stale sentence reads exactly like a fresh
one. `frontends/wysiwyg-grilling/tests/e2e/README.md` needed a commit of
its own (`cb533ab`, "sync the triage README's suite count") to move one
number by ten, and had drifted by nearly eight hundred again by the time
this file was written. AGENTS.md called `canvas.py` "~5.4k lines" at 18k
and `tests/test_backend.py` "~120 tests" at 628. Every one of those
sentences was true when it was typed, and none of them says when that was.

The fix is not more diligence. It is to stop writing the number down: a
marker names a FUNCTION, the function derives the value from the thing it
describes, and `refresh` writes the answer back into the prose.

THE MARKER — one HTML-comment pair, on one line, wrapping the value:

    <!-- live:canvas_py_lines -->~18.2k<!-- /live:canvas_py_lines -->

Chosen for three properties. It is invisible in rendered markdown, so a
converted sentence still reads as a sentence. It is greppable in one
pattern that cannot collide with anything else in this tree
(`grep -rn 'live:' -- '*.md'`). And the name is repeated on the close, so
a hand edit that mangles one half fails loudly instead of swallowing the
rest of the paragraph into a value. The value itself is one line and may
not contain a comment delimiter; anything else is a parse error, never a
silent skip.

NO EXPRESSIONS, ONLY NAMES. `CALCULATORS` maps a name to a zero-argument
Python function and the resolver looks up nothing else. There is no eval,
so a marker in a file can never become an execution surface, and every
value has one testable derivation with a docstring saying where it comes
from.

WHAT IS NOT A VALID LIVE VALUE. A derivation must be deterministic on a
clean checkout: `refresh` is idempotent, and a value that moved on its own
would make the second run rewrite the file. So wall-clock timings ("~2s"),
anything that reads the network, and anything that needs a browser stay
literals. AGENTS.md's suite-runtime figure is deliberately one of these.

THE BOUNDARY WITH THE CENSUS — read this before adding a calculator that
counts reds, mutants or catalogue rows, and read it as a line that MOVED
rather than one that was always here. `SESSION-HANDOVER.md` has five
guards over it (`handover_catalogue_reds`, `handover_coverage_totals`,
`handover_durable_counts`, `handover_render_reds`, and the render-row
derivation beside them, all in `tests/test_mutants.py`), each requiring
its subject to appear EXACTLY ONCE. Until 2026-08-18 all five subjects
were hand-typed literals and this section said so at length. Two of them
are now live and three are not, and WHICH two is the whole rule:

  - a livedoc marker promises "you never have to think about this number";
  - a census guard promises "when this number moves, STOP AND LOOK",
    because a red flipping is a claim about the product, not a count.

A TOTAL CANNOT KEEP THE SECOND PROMISE, which is what the seven recorded
staleness events of the durable-count sentence were actually saying.
Nobody ever read `2 / 0 / 0` and stopped: the guard fired, a human
retyped three digits, and the wave went on. That is a chore wearing a
tripwire's clothes, and it is the neither-side disease — a fact derived
by code, restated by hand, and owned by neither. So the two pure TOTALS
are now live values: `durable_red_counts` and `detector_coverage_totals`.

THE THREE THAT STAYED LITERALS NAME INDIVIDUALS. `handover_catalogue_reds`
is a table of mutant IDS, `handover_render_reds` a row of test NAMES, and
the derivation beside them the same. A red arriving or leaving by name is
the event a human must read, the sentence is not a number, and `refresh`
would repair exactly the paragraph the failure was meant to make somebody
open.

WHAT THE SECOND LOCK STILL PROVES, said plainly because it is less than
it was. The two guards now compare a marker's content to the same
function that wrote it, so they no longer catch a WRONG DERIVATION — if
`durable_red_counts()` broke, both sides would move together. They do
still catch a STALE file (the marker is written by `refresh`, not by the
guard, so a commit that flips a red without refreshing fails), a
DUPLICATED sentence, and a sentence REWORDED past its anchor. The
markers wrap the guards' anchors WHOLE — the value includes its own `**`
— so no guard regex was widened to tolerate a comment inside a number,
which is the failure mode this section used to predict.
`test_the_census_sentences_carry_only_the_two_live_totals` in
`tests/test_livedoc.py` holds the new line mechanically, and refuses a
third marker in that file as loudly as the old one refused the first.

Everything else — line counts, suite totals, catalogue row counts nobody
guards — is fair game and is what this tool is for.

    python3 tests/livedoc.py check      # recompute, report drift, change
                                        # nothing, exit 1 if anything moved
    python3 tests/livedoc.py refresh    # recompute and write the answers back

`check` runs as a pre-commit hook on every commit; `refresh` is the repair
and is what its failure message tells you to run. Deliberately NOT a suite
test: a red test in this repo means "investigate", and a stale count means
"run the repair", which are different instructions.

TO ADD A VALUE: write a `@calculator("name")` function that derives it
from the source of truth and raises if that source is gone, wrap the
literal in the marker pair, and run `refresh`. A registered name that no
tracked file uses fails `check` — a calculator nothing reads is a
derivation that has stopped being watched.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]

# The scan surface: tracked markdown, minus the byte-exact scene fixtures
# that `.pre-commit-config.yaml` excludes globally for the same reason.
# `git ls-files` and not a glob, on purpose — it is what makes "tracked"
# true rather than asserted, and it is why the gitignored `docs/` tree can
# never be read or written by this tool even by accident.
_FIXTURES = "tests/fixtures/"

CALCULATORS: dict[str, Callable[[], str]] = {}


def calculator(name: str) -> Callable[[Callable[[], str]], Callable[[], str]]:
    """Register a derivation under the name markers spell it with.

    The returned decorator raises `ValueError` for a malformed name and for
    one already taken — a silent second registration would make the winning
    derivation depend on import order, which is the class of bug this whole
    module exists to refuse.

    Args:
        name: The marker name, `[a-z][a-z0-9_]*`, matching `_OPEN`. It is
            what appears in the prose, so it is read by humans hunting the
            derivation behind a number — spell it after the thing measured.

    Returns:
        The decorator, which returns the function unchanged so a calculator
        stays directly callable (and directly testable) as itself.
    """
    def register(fn: Callable[[], str]) -> Callable[[], str]:
        """Put `fn` in the registry under the enclosing `name`.

        Args:
            fn: The zero-argument derivation.

        Returns:
            `fn`, unchanged.

        Raises:
            ValueError: If `name` is malformed or already taken.
        """
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(
                "live-value name %r is not [a-z][a-z0-9_]* — the marker "
                "regex would not find it, so the value would sit in the "
                "prose looking live and never be checked" % name)
        if name in CALCULATORS:
            raise ValueError(
                "live-value name %r is registered twice (%s and %s): which "
                "one wins would depend on import order"
                % (name, CALCULATORS[name].__name__, fn.__name__))
        CALCULATORS[name] = fn
        return fn
    return register


# --------------------------------------------------------------------------
# The calculators. Each one derives its value from the file it describes and
# fails loudly when that file is gone — a derivation that cannot find its
# subject must never fall through to the stored value, because agreeing with
# the prose is exactly what a broken guard looks like from outside.
# --------------------------------------------------------------------------

def _count_lines(path: Path, what: str) -> int:
    """Count the lines in one file, the way `wc -l` does.

    Args:
        path: The file to measure.
        what: How to name it in the failure, for a reader who has to decide
            whether the miss is a repo problem or a stale calculator.

    Returns:
        The number of lines.

    Raises:
        AssertionError: If the file is absent. The stale value stored in the
            prose is never an acceptable answer to "I could not measure".
    """
    if not path.is_file():
        raise AssertionError(
            "%s: %s is not in this tree, so the count cannot be derived. "
            "If the file moved, re-point this calculator; do not delete the "
            "marker and leave the number behind as a literal"
            % (what, path.relative_to(REPO) if path.is_absolute() else path))
    return len(path.read_text(encoding="utf-8").splitlines())


def _kloc(lines: int) -> str:
    """Render a line count the way this repo's prose already says it.

    Rounded to a hundred lines rather than stated exactly, and the rounding
    is the point: AGENTS.md's table is describing scale, and an exact count
    would mark the file dirty on every commit that touches the subject,
    across every agent working in parallel. A tenth of a thousand still
    catches the failure this exists for — being wrong by a factor of three.

    Args:
        lines: The measured line count.

    Returns:
        The `~N.Nk` form, matching the literals it replaces.
    """
    return "~%.1fk" % (lines / 1000.0)


def _discovered_case_count(pattern: str) -> int:
    """Count the tests `unittest discover -s tests` would run.

    The runner and not a `def test_` grep, deliberately, and the opposite
    choice from `durable_red_counts` in `tests/test_mutants.py`: that one
    counts decorator LINES because the sentence it guards is about the
    source. These counts are quoted as "the suite is N tests", which is a
    claim about what runs, so a gated class or a data-driven generator has
    to count the way the runner counts it.

    Args:
        pattern: The discovery pattern, e.g. `test*.py` for the whole suite
            or `test_backend.py` for one module.

    Returns:
        The number of test cases discovery yields.

    Raises:
        AssertionError: If `tests/` is absent, if the pattern matches no
            module at all, or if any module failed to import. Discovery
            turns an import error into a single synthetic `_FailedTest`
            case, so an unnoticed one would read as a suite that merely
            shrank — a wrong number reported with full confidence.
    """
    start = REPO / "tests"
    if not start.is_dir():
        raise AssertionError(
            "tests/ is not in this tree, so the suite size cannot be "
            "derived. This is not a finding about the suite")
    suite = unittest.defaultTestLoader.discover(
        str(start), pattern=pattern, top_level_dir=str(start))
    total, pending = 0, [suite]
    while pending:
        item = pending.pop()
        if isinstance(item, unittest.TestSuite):
            pending.extend(item)
            continue
        if type(item).__name__ == "_FailedTest":
            raise AssertionError(
                "%r failed to import under pattern %r, so the count below "
                "it is missing a whole module's tests. Fix the import "
                "before trusting any suite total" % (item.id(), pattern))
        total += 1
    if not total:
        raise AssertionError(
            "discovery pattern %r matched no tests under tests/ — the "
            "module was renamed and this calculator now measures nothing"
            % pattern)
    return total


@calculator("canvas_py_lines")
def canvas_py_lines() -> str:
    """Size of the single-file backend, as AGENTS.md's table states it.

    Returns:
        `~N.Nk`, the line count of `canvas.py` rounded to a hundred lines.
    """
    path = REPO / "skills" / "wysiwyg-grilling" / "scripts" / "canvas.py"
    return _kloc(_count_lines(path, "canvas.py line count"))


@calculator("frontend_src_lines")
def frontend_src_lines() -> str:
    """Size of the canvas UI, counting the sources AGENTS.md's row names.

    `.ts` and `.tsx` only, because the row it fills says "TypeScript /
    React 18" — `styles.css` is a real seven hundred lines and is honestly
    outside the claim, not an oversight.

    Returns:
        `~N.Nk`, the summed line count rounded to a hundred lines.

    Raises:
        AssertionError: If the source tree is absent, or holds no `.ts` or
            `.tsx` file at all, which means the frontend moved and this
            calculator is summing an empty list into a confident zero.
    """
    root = REPO / "frontends" / "wysiwyg-grilling" / "src"
    if not root.is_dir():
        raise AssertionError(
            "%s is not in this tree, so the UI line count cannot be derived"
            % root.relative_to(REPO))
    files = sorted(p for p in root.rglob("*")
                   if p.suffix in (".ts", ".tsx") and p.is_file())
    if not files:
        raise AssertionError(
            "no .ts or .tsx under %s — the frontend has moved and this "
            "calculator would report ~0.0k rather than fail"
            % root.relative_to(REPO))
    return _kloc(sum(_count_lines(p, "frontend source") for p in files))


@calculator("test_backend_cases")
def test_backend_cases() -> str:
    """How many tests `tests/test_backend.py` contributes to the suite.

    Returns:
        The count, exactly. Unlike the line counts this one is not rounded:
        it is the number AGENTS.md's table quotes as the size of the backend
        suite, it was five times wrong when this was written, and a reader
        deciding whether a change is covered wants the real figure.
    """
    return str(_discovered_case_count("test_backend.py"))


@calculator("unittest_suite_cases")
def unittest_suite_cases() -> str:
    """How many tests `python3 -m unittest discover -s tests` runs in all.

    Returns:
        The count, exactly — the whole in-process suite, mutation harness
        and render tier included.
    """
    return str(_discovered_case_count("test*.py"))


@calculator("pin_guard_sites")
def pin_guard_sites() -> str:
    """How many pin guards `tests/guard_mutants.py` mutates, in words.

    SKILL.md's pin section promises the reader a number of guard sites,
    and promised "Twenty" from the day it was written until 2026-08-20,
    by which point the roster held twenty-three — three guards added
    (`_tidy_pass.group_cascade`, `reroute_and_confess` and the composed
    reconciler's postcondition among them) with the sentence untouched.
    A count of sites is exactly livedoc's half of the boundary above: it
    is a total, nobody stops and looks when it moves, and it is derived
    from a list in this repo.

    THE SWEEP RESULT IS NOT LIVE AND MUST NOT BE. "23/23 observed" is
    what happens when somebody runs a 32-second sweep that rewrites
    canvas.py; a marker would let `refresh` repair the one sentence
    whose staleness should send a reader to the command instead. The
    prose beside this value names that command.

    Returns:
        The site count as an English word where one exists, so the
        sentence still reads as a sentence, else the digits.

    Raises:
        AssertionError: If the roster is empty — a count of zero would
            be published as a fact rather than as the missing instrument
            it would actually be.
    """
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import guard_mutants
    finally:
        sys.path.remove(str(REPO / "tests"))
    n = len(guard_mutants.GUARDS)
    if not n:
        raise AssertionError(
            "tests/guard_mutants.py lists no guards, so there is no site "
            "count to publish — the instrument is gone, not empty")
    words = {20: "Twenty", 21: "Twenty-one", 22: "Twenty-two",
             23: "Twenty-three", 24: "Twenty-four", 25: "Twenty-five",
             26: "Twenty-six", 27: "Twenty-seven", 28: "Twenty-eight",
             29: "Twenty-nine", 30: "Thirty"}
    return words.get(n, str(n))


def _canvas() -> ModuleType:
    """`canvas.py`, imported on demand.

    SKILL.md is the file an agent reads to learn how the tool works, and
    the numbers in it are the tool's own — budgets, artifact tiers,
    config defaults. Importing the backend is how those stop being
    transcriptions. Same shape as `_harness()` below and for the same
    reason: a calculator that cannot reach its subject refuses rather
    than agreeing with the prose.

    Returns:
        The imported module.

    Raises:
        AssertionError: If it cannot be imported, with the original
            attached.
    """
    scripts = str(REPO / "skills" / "wysiwyg-grilling" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import canvas
    except Exception as exc:
        raise AssertionError(
            "skills/wysiwyg-grilling/scripts/canvas.py could not be "
            "imported (%s), so the values SKILL.md derives from it cannot "
            "be recomputed. That is a finding about the backend, not about "
            "the prose holding the marker" % exc) from exc
    return canvas


# The budget probes below MEASURE `lint_layout` rather than read the two
# literals inside it. Deliberate, and it is the difference between the two
# kinds of wrong this repo keeps meeting: a constant transcribed into prose
# goes stale when somebody edits the constant, and a constant transcribed
# into a TEST goes stale in lockstep with it and says nothing. What SKILL.md
# promises is "the lint fires past 9 nodes", so the derivation asks the lint.
_PROBE_CEILING = 40


def _first_over_budget(kind: str, artifact_type: str | None,
                       word: str) -> int:
    """The smallest population `lint_layout` calls over budget.

    Builds scenes of growing size and asks the real check, so the answer
    is the behaviour SKILL.md describes and not a literal read off the
    same line the prose was copied from.

    Args:
        kind: `"nodes"` to grow the node population, `"arrows"` to hold
            nodes at a legal count and grow the arrows.
        artifact_type: What to lint the scene as — `"domain"` lowers the
            node budget, `None` takes the default.
        word: The noun the budget note must contain (`"nodes"`,
            `"entities"` or `"arrows"`), so a note about some OTHER
            budget can never be mistaken for this one.

    Returns:
        The population at which the note first appears. The budget itself
        is one less.

    Raises:
        AssertionError: If no scene up to `_PROBE_CEILING` trips the note.
            A budget that cannot be provoked is a check that has stopped
            firing, and publishing a number for it would be worse than
            failing.
    """
    canvas = _canvas()
    for n in range(2, _PROBE_CEILING):
        n_nodes = n if kind == "nodes" else 9
        n_arrows = n if kind == "arrows" else 0
        els: list[dict[str, object]] = [
            {"id": "n%d" % i, "type": "rectangle", "x": i * 300, "y": 0,
             "width": 100, "height": 60, "customData": {"role": "node"}}
            for i in range(n_nodes)]
        els.extend(
            {"id": "a%d" % i, "type": "arrow", "x": 0, "y": 0,
             "points": [[0, 0], [50, 0]],
             "startBinding": {"elementId": "n%d" % (i % n_nodes)},
             "endBinding": {"elementId": "n%d" % ((i + 1) % n_nodes)}}
            for i in range(n_arrows))
        notes = canvas.lint_layout(els, artifact_type=artifact_type)["notes"]
        if any("budget: " in x and word in x for x in notes):
            return n
    raise AssertionError(
        "no scene up to %d %s tripped a %r budget note in lint_layout — "
        "the budget SKILL.md publishes is no longer enforced, and a "
        "number derived from a silent check is a fiction"
        % (_PROBE_CEILING, kind, word))


@calculator("node_budget")
def node_budget() -> str:
    """The per-artifact node ceiling `lint_layout` enforces.

    Returns:
        The largest node count that draws no budget note, as digits.
    """
    return str(_first_over_budget("nodes", None, "nodes") - 1)


@calculator("arrow_budget")
def arrow_budget() -> str:
    """The per-artifact arrow ceiling `lint_layout` enforces.

    Counted over arrows alone: `lint_layout` filters `line` elements out
    before this note, which is why SKILL.md's sentence says arrows and
    means it.

    Returns:
        The largest arrow count that draws no budget note, as digits.
    """
    return str(_first_over_budget("arrows", "flow", "arrows") - 1)


@calculator("domain_node_budget")
def domain_node_budget() -> str:
    """The lowered node ceiling a `domain` artifact is linted against.

    Returns:
        The largest entity count that draws no budget note, as digits.
    """
    return str(_first_over_budget("nodes", "domain", "entities") - 1)


def _types_in_tier(tier: str) -> list[str]:
    """Artifact type names in one tier, in the priority order they ship in.

    Args:
        tier: `"first-class"` or `"extended"`, as spelled in
            `canvas.FIRST_CLASS_DEFAULTS`.

    Returns:
        The type names, ordered by their configured priority.

    Raises:
        AssertionError: If the tier holds nothing. An empty tier would
            publish an empty list into a sentence that reads as a
            complete enumeration.
    """
    defaults = _canvas().FIRST_CLASS_DEFAULTS
    got = sorted((v.get("priority", 0), k) for k, v in defaults.items()
                 if v.get("tier") == tier)
    if not got:
        raise AssertionError(
            "canvas.FIRST_CLASS_DEFAULTS lists no %r type, so the "
            "enumeration SKILL.md publishes would be an empty list "
            "wearing a complete sentence" % tier)
    return [k for _p, k in got]


# `er` is the one type whose prose spelling is not its config key: the
# sentence has said "ER" since the tier split, and lowercasing it to match
# a dict key would be prose damage in the name of derivation. One entry,
# not a general case-mapping table, so the exception stays visible.
_TYPE_PROSE = {"er": "ER"}


@calculator("first_class_types")
def first_class_types() -> str:
    """The types that narrate with typed facts, named in priority order.

    Returns:
        Comma-separated type names.
    """
    return ", ".join(_TYPE_PROSE.get(t, t)
                     for t in _types_in_tier("first-class"))


@calculator("mermaid_dropped")
def mermaid_dropped() -> str:
    """The categories a `--format mermaid` export leaves behind.

    THE THIRD COPY. This rule was written by hand in three places — the
    filter, the runtime `NOTE=` line, and SKILL.md's export paragraph.
    The `NOTE=` line had drifted; SKILL.md had not, which made it the
    dangerous one: correct prose with nothing holding it there is one
    edit from being wrong and reads identically either way. Both are
    now derived from `canvas.MERMAID_DROP_LABELS` via the same function
    the CLI prints with, so the sentence and the filter cannot disagree.

    `mermaid` and not `er` because SKILL.md states the wider set and
    then names the `er` exception in prose; the exception is one term
    (`MERMAID_DROP_ROLES_NON_ER`) and stating it twice would reintroduce
    exactly the transcription this removes.

    Returns:
        The comma-joined category list, as the CLI would print it.
    """
    canvas = _canvas()
    return canvas._mermaid_dropped_names(canvas.mermaid_dropped_roles(
        "mermaid"))


@calculator("cross_lint_join")
def cross_lint_join() -> str:
    """The artifact-type pair `cross_lint`'s mapped-element checks join.

    A live value precisely because the sentence it fills is the one that
    rotted: SKILL.md said "default-mapped pairs" and named three, while
    3.2.4 and 3.3.7 have only ever joined one — a set stated in prose
    with nothing binding it, which is the shape two sweeps found 28 times
    this week. `canvas.CROSS_LINT_JOIN` is load-bearing (the collector
    reads it), so widening the join in a future version rewrites this
    sentence or fails the hook, and neither can happen alone.

    This deliberately does NOT cover the tripwire half, which is
    type-blind and therefore has no pair to name.

    Returns:
        e.g. `wireframe × flow`.

    Raises:
        AssertionError: If the constant is not a 2-tuple of type names.
            Anything else means the join stopped being one pair, and
            publishing its first two entries would print a narrower
            claim than the code makes.
    """
    join = _canvas().CROSS_LINT_JOIN
    if not isinstance(join, tuple) or len(join) != 2 or \
            not all(isinstance(t, str) and t for t in join):
        raise AssertionError(
            "canvas.CROSS_LINT_JOIN is %r, not a 2-tuple of type names — "
            "the prose it fills says the strict checks join exactly one "
            "pair, and that is no longer what the code says" % (join,))
    return "%s × %s" % join


@calculator("extended_types")
def extended_types() -> str:
    """The types that draw fine and narrate generically, in priority order.

    Returns:
        Comma-separated type names.
    """
    return ", ".join(_TYPE_PROSE.get(t, t)
                     for t in _types_in_tier("extended"))


# SKILL.md's EVENT-TAXONOMY NUMERAL IS NOT LIVE, AND MUST NOT BE — this
# comment is here because it was, briefly, and the suite caught it. The
# sentence "all sixteen types" is already DERIVED-AND-COMPARED by
# `test_the_skill_taxonomy_table_is_the_emitted_set` in
# `tests/test_backend.py`, which walks the emit sites, checks the three
# table cells against `USER_EVENT_TYPES` / `AGENT_EVENT_TYPES` / the
# system pair, and only then compares the spelled numeral. That is the
# census side of this module's header boundary and it is the right side:
# the table says WHO REACTS to each type, so a type arriving or leaving
# is an event a human must read, not a number nobody should think about.
# A marker here would let `refresh` quietly repair the one sentence whose
# staleness is supposed to make somebody open the table.


@calculator("nudge_after_minutes")
def nudge_after_minutes() -> str:
    """How long the config lets a silence run before the one nudge.

    Returns:
        The default from `canvas.DEFAULT_CONFIG`, as digits.

    Raises:
        AssertionError: If the key is gone or is not a number, which
            means the config shape moved under the sentence.
    """
    got = _canvas().DEFAULT_CONFIG.get("nudge_after_minutes")
    if not isinstance(got, (int, float)) or isinstance(got, bool):
        raise AssertionError(
            "canvas.DEFAULT_CONFIG has no numeric nudge_after_minutes "
            "(got %r), so SKILL.md's default cannot be derived" % (got,))
    return str(int(got))


# NOT LIVE, AND THE REASON IS NOT LAZINESS: SKILL.md's `wait` ceiling
# (540s) is pinned to something OUTSIDE this tree — Bash's 600s tool
# timeout — and the prose says so. Its two occurrences are an argparse
# default and a `min()` inside `cmd_wait`, neither reachable without
# building the parser `main()` builds inline or re-reading the literal a
# marker would exist to stop trusting. A derivation that just relocated
# the literal would make the sentence LOOK derived while watching
# nothing, which is worse than the honest literal it replaced.


def _corpus_roots() -> list[Path]:
    """The fixture projects the census walks, found once for both readers.

    ONE FINDER, TWO CALLERS, on purpose. `corpus_census` and
    `corpus_projects` publish numbers that only mean anything together —
    "across N projects it lints to ..." — so a second copy of this glob
    would be free to drift from the first and the sentence would go
    quietly self-contradictory rather than loudly wrong. The repo's own
    phrasing for this, in `.pre-commit-config.yaml`, is that a remedy
    spelled twice is a remedy that drifts.

    Returns:
        Sorted project roots under `tests/fixtures/` that hold an
        `artifacts/` directory. `mermaid/` has none and is not a project
        by this definition, which is why the count is not "directories
        under fixtures".

    Raises:
        AssertionError: If none is found, which would otherwise let both
            callers report a confident zero about a corpus that has moved
            rather than emptied.
    """
    roots = sorted(p for p in (REPO / "tests" / "fixtures").glob("*")
                   if p.is_dir() and (p / "artifacts").is_dir())
    if not roots:
        raise AssertionError(
            "no fixture project under tests/fixtures holds an artifacts/ "
            "directory — the corpus has moved and this calculator would "
            "report 0/0/0 rather than fail")
    return roots


@calculator("corpus_projects")
def corpus_projects() -> str:
    """How many fixture projects the corpus census is taken across.

    AGENTS.md's row read "Across 5 projects" as a literal beside a live
    census, which is the half-derived shape this module exists to remove:
    the four numbers after it could not go stale and the number before
    them could, so adding a sixth fixture project would have made one
    sentence disagree with itself and passed the gate.

    Returns:
        The project count, exactly.
    """
    return str(len(_corpus_roots()))


def _gate_hooks() -> tuple[list[str], list[str]]:
    """Split `.pre-commit-config.yaml`'s hooks into automatic and manual.

    READ AS TEXT, NOT AS YAML, because `canvas.py`'s stdlib-only rule has
    no yaml parser behind it and this module is imported by the hook it
    describes. The shape relied on is the one pre-commit itself requires:
    a hook opens with a `- id:` item, and a hook that is manual-only says
    so with a `stages:` line naming `manual` before the next `- id:`.
    Full-line comments are stripped first, so the prose above the manual
    hooks — which quotes `--hook-stage manual` at length — cannot be
    mistaken for configuration.

    Returns:
        `(automatic, manual)` hook ids. `automatic` is what
        `pre-commit run --all-files` executes; `manual` is what it does
        NOT, which is the whole reason this is derived rather than
        counted by hand.

    Raises:
        AssertionError: If the config is missing or declares no hook at
            all — either way the gate's size is unknown, and an unknown
            gate reported as a number is the defect this calculator was
            added for.
    """
    path = REPO / ".pre-commit-config.yaml"
    if not path.is_file():
        raise AssertionError(
            "%s is not in this tree, so the gate's hook census cannot be "
            "derived" % path.name)
    body = [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")]
    automatic: list[str] = []
    manual: list[str] = []
    current = ""
    staged = False

    def close() -> None:
        """File the hook just finished under the stage it declared."""
        if current:
            (manual if staged else automatic).append(current)
    for line in body:
        found = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if found:
            close()
            current, staged = found.group(1), False
        elif current and re.match(r"\s*stages:.*\bmanual\b", line):
            staged = True
    close()
    if not automatic and not manual:
        raise AssertionError(
            "%s declares no `- id:` hook, so the gate is either empty or "
            "no longer readable as text — refusing to publish a zero"
            % path.name)
    return automatic, manual


@calculator("gate_hooks_automatic")
def gate_hooks_automatic() -> str:
    """How many hooks `uvx pre-commit run --all-files` actually runs.

    Returns:
        The count, exactly.
    """
    return str(len(_gate_hooks()[0]))


@calculator("gate_hooks_manual")
def gate_hooks_manual() -> str:
    """How many hooks the gate does NOT run without `--hook-stage manual`.

    THE NUMBER THIS EXISTS TO KEEP HONEST is the one in the sentence
    beside it. AGENTS.md called `pre-commit run --all-files` "Everything,
    as CI runs it" — wrong twice, and the half that mattered is this
    half: the guard-mutant sweep and the Playwright suite are manual-only
    and a reader who believed "Everything" had no reason to run either.

    Returns:
        The count, exactly. Zero is a legitimate answer and is published
        as one: it would mean the manual stage had been retired, which is
        a change a reader should see rather than a broken measurement.
    """
    return str(len(_gate_hooks()[1]))


@calculator("corpus_census")
def corpus_census() -> str:
    """What the frozen corpus lints to, by a convention that is CODE.

    THE DISAGREEMENT THIS SETTLES (v0.9 whole-branch review, N-3). Two
    readers measured the same 24 artifacts and published `0/46/27` and
    `0/38/20`.

    THE FIRST EXPLANATION OF THAT GAP, SHIPPED HERE, WAS WRONG, and it
    is corrected rather than quietly replaced because the wrong version
    was the more comfortable one. It said the gap was the registry
    pseudo-scope — a scope with findings of its own and no artifact
    behind it, which a per-artifact walk cannot see. That is a real
    thing, and it is not the cause: measured at `10dc4bf` the registry
    scope carries **0 warnings**, so it cannot account for a warnings
    gap at all. "Two legitimate conventions" was a plausible story that
    nobody had measured, which is the exact failure this whole review
    round has been about.

    WHAT ACTUALLY PRODUCES THE TWO NUMBERS, re-derived at `10dc4bf`:

      * WARNINGS, 46 vs 38 — not a convention. A per-artifact reader
        asked for the artifact's type as
        `registry["artifacts"][aid]["type"]`, and **the registry has no
        `artifacts` key**: `.get("artifacts", {})` returns `{}` and the
        type comes back `None` for all 24, silently. That switches off
        every type-gated check (the wireframe reading-order family), and
        it is worth exactly the 8 warnings in question. The type lives
        in `artifact_meta` and is reached by `Store.artifact_type()`,
        which defaults to `"flow"`. Hand the per-artifact walk the RIGHT
        type and it reports 46 — and the message SETS are identical, not
        merely the totals, which is the check that distinguishes "same
        answer" from "two errors cancelling".

      * NOTES, 27 vs 18 — structural, and genuinely two things a
        per-artifact call cannot reach: 5 registry-scope notes (settled
        glossary terms with no concept, view debt) and 4 cross-artifact
        notes from `cross_lint` (unmapped KPI tiles, a wireframe label
        colliding with a domain term). Those 9 are the whole gap.

    So there is ONE right answer for warnings and one reader that was
    misreading a key, and a real structural difference for notes only.

    WHAT I CANNOT REPRODUCE, SAID PLAINLY. The published `0/38/20` has
    38 warnings, which the mis-keyed reading gives exactly, and **20
    notes, which no reading I have found produces** — the mis-keyed walk
    gives 18. I have not identified what makes 20 and I am not going to
    guess at it: an unexplained two-note discrepancy labelled as
    unexplained is worth more than a second plausible cause, which is
    how the first version of this docstring went wrong.

    THE CLI'S READING IS THE ONE PINNED — every line `lint` prints, over
    each project in turn — because it is what a human reproduces by
    running the tool, and because the alternative is missing findings
    rather than counting them differently. Derived through
    `Store.lint_lines()`, the same call `cmd_lint` prints from, not a
    re-implementation of it, for the reason `_harness` gives about
    second copies of a derivation.

    ARTIFACTS AND SCOPES ARE DIFFERENT NUMBERS, and this shipped calling
    28 of them "artifacts" until the pin-pruning stream caught it before
    the fold. `Store.lint_lines()` is keyed by SCOPE, and the registry is
    a scope with findings of its own and no artifact behind it — so the
    24-artifact corpus answers 28. That is r5-1, and the ruling against
    it is in `cmd_lint`, in the very function this derives from: "SCOPES,
    not ARTIFACTS … a project with one artifact printed `ARTIFACTS=2`".
    Re-committing it here would have been worse than in the CLI, for two
    reasons that are the whole argument for naming both:

      * TRACKED PROSE IS QUOTED. N-3 exists because two readers already
        published different counts off this corpus; a marker reading
        "28 artifacts" hands the next one a fourth number with an
        authoritative label on it.
      * SCOPES MOVES FOR REASONS ARTIFACTS DO NOT. `lint_debt` adds the
        registry key only `if any(reg[k] …)`, so the total is 24 plus
        however many projects currently HAVE a registry finding —
        `argus-r4-arm3` has none and contributes no scope. Measured:
        delete one project's CONTEXT.md glossary and the count goes 28
        -> 27 with no artifact gone. A live value that moves for a
        reason unrelated to its own name is worse than a stale one,
        because `refresh` will dutifully rewrite it and nobody will ask
        why.

    Both are emitted under the names the repo already owns, and they
    close arithmetically — `scopes - artifacts` is the number of
    projects carrying registry findings — which is the same shape as the
    `SCOPES + QUARANTINED` identity `cmd_lint` documents.

    Returns:
        `artifacts=A scopes=S errors=E warnings=W notes=N`.

    Raises:
        AssertionError: If the fixture corpus is absent or holds no
            project, which would otherwise report a confident zero
            about a corpus that is not there; or if scopes ever falls
            BELOW artifacts, which would mean an artifact was linted
            under no scope and the two readings have stopped being
            reconcilable.
    """
    sys.path.insert(0, str(REPO / "skills" / "wysiwyg-grilling" / "scripts"))
    import canvas
    roots = _corpus_roots()
    tally = {"errors": 0, "warnings": 0, "notes": 0}
    arts = scopes = 0
    tmp = Path(tempfile.mkdtemp(prefix="livedoc-census-"))
    try:
        for src in roots:
            # COPIED, NOT READ IN PLACE. `Store` writes — it repairs on
            # load and lays down a runtime tree — and a calculator that
            # mutated the corpus it measures would make `refresh`
            # non-idempotent, which this module's header forbids.
            root = tmp / src.name
            shutil.copytree(src, root / "project_knowledge")
            store = canvas.Store(canvas.Project(root))
            lines = store.lint_lines()
            # ARTIFACTS from the loaded scenes, SCOPES from what was
            # linted. Two reads because they are two facts; taking the
            # second and calling it the first is the defect this
            # docstring records.
            arts += len(store.scenes)
            scopes += len(lines)
            for row in lines.values():
                for tier in tally:
                    tally[tier] += len(row.get(tier) or [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if scopes < arts:
        raise AssertionError(
            "the corpus linted %d scopes over %d artifacts — fewer scopes "
            "than artifacts means an artifact was linted under no scope, "
            "and the two readings have stopped being reconcilable"
            % (scopes, arts))
    return ("artifacts=%d scopes=%d errors=%d warnings=%d notes=%d"
            % (arts, scopes, tally["errors"], tally["warnings"],
               tally["notes"]))


def _harness() -> ModuleType:
    """`tests/test_mutants.py`, imported on demand.

    The two census calculators below CALL the harness's own derivations
    rather than re-implementing them, and that is deliberate: a second
    copy of "count the decorator lines" or "walk `DETECTORS`" would be a
    third statement of a fact this repo has already watched go stale in
    its second. The cost is measured and small — 0.17s to import, against
    the hook's ~0.5s — and it is paid only when a marker names one of
    them.

    Returns:
        The imported module.

    Raises:
        AssertionError: If it cannot be imported. A calculator that could
            not reach its subject must never fall through to the stored
            value; the exception is re-raised as the refusal every other
            calculator here makes, with the original attached.
    """
    tests_dir = str(Path(__file__).resolve().parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    try:
        import test_mutants
    except Exception as exc:
        raise AssertionError(
            "tests/test_mutants.py could not be imported (%s), so the "
            "census values it derives cannot be recomputed. This is a "
            "finding about that module, not about the prose holding the "
            "marker — do not delete the marker to make it go away" % exc
        ) from exc
    return test_mutants


@calculator("durable_red_counts")
def durable_red_counts() -> str:
    """Red decorator lines per file, as SESSION-HANDOVER.md states them.

    THE SENTENCE THAT DRIFTED SEVEN TIMES, and the one whose own prose
    prints the grep that derives it — everything needed to keep it honest
    was on the page for six of those seven, which is the argument that
    being cheap to derive is not the same as being derived. Guarded since
    2026-08-16 by `TestCoverage.test_the_handover_transcribes_the_durable
    _red_counts`, which stays: see this module's header on what the two
    locks now each promise.

    The value carries its own `**` so the guard's anchor
    (`_HANDOVER_DURABLE`, which matches `**N / N / N** for` + the three
    backticked filenames as one span) is spliced whole and needs no
    widening to tolerate a comment inside it.

    Returns:
        `**N / N / N**` for `DURABLE_RED_FILES` in that order.
    """
    return "**%d / %d / %d**" % _harness().durable_red_counts()


@calculator("detector_coverage_totals")
def detector_coverage_totals() -> str:
    """The coverage table's totals, as SESSION-HANDOVER.md states them.

    Derived from `coverage_table()` — the same function the `--coverage`
    CLI prints and the same one
    `TestCoverage.test_the_handover_transcribes_the_coverage_totals`
    compares this sentence against. It went stale once and badly: it read
    "18 detectors ... 15 proven" from a batch-21 measurement while the
    live table said 19 and 16.

    The value carries its own `**` for the reason `durable_red_counts`
    gives.

    Returns:
        `**N detectors, N proven, N render-tier, N UNCOVERED**`.

    Raises:
        AssertionError: If the harness cannot be imported, or if
            `coverage_table()` reports a status this sentence has no word
            for — a fourth state would otherwise be silently dropped from
            a total that still read as complete.
    """
    rows = _harness().coverage_table()
    tally = {"proven": 0, "render-tier": 0, "UNCOVERED": 0}
    for _name, status, _evidence in rows:
        if status not in tally:
            raise AssertionError(
                "coverage_table() reports the status %r, which this "
                "sentence has no word for. The table grew a state and the "
                "prose would have gone on quoting three" % status)
        tally[status] += 1
    return ("**%d detectors, %d proven, %d render-tier, %d UNCOVERED**"
            % (len(rows), tally["proven"], tally["render-tier"],
               tally["UNCOVERED"]))


# --------------------------------------------------------------------------
# The marker: parse, check, repair.
# --------------------------------------------------------------------------

_OPEN = re.compile(r"<!--\s*live:([a-z][a-z0-9_]*)\s*-->")
_CLOSE = re.compile(r"<!--\s*/live:([a-z][a-z0-9_]*)\s*-->")


class Marker(NamedTuple):
    """One live value found in a file, located precisely enough to rewrite.

    Attributes:
        name: The registered calculator name the pair spells.
        stored: The text currently sitting between the two comments.
        start: Index of the first character of `stored` in the file text.
        end: Index one past its last character.
        line: 1-based line number of the opening comment, for messages.
    """

    name: str
    stored: str
    start: int
    end: int
    line: int


def scan(text: str, where: str) -> list[Marker]:
    """Find every live marker in one file's text.

    Strict by construction. An unpaired comment, a close whose name does not
    match its open, a nested pair or a value spanning a newline is an error
    and not a skip: the only way this tool can be worse than the literals it
    replaces is by looking like it is watching a number it never found.

    Args:
        text: The whole file.
        where: The file's name, for failure messages.

    Returns:
        Every marker, in document order.

    Raises:
        AssertionError: On any malformed marker, naming the file, the line
            and what was expected.
    """
    tokens = sorted(
        [(m.start(), m.end(), True, m.group(1)) for m in _OPEN.finditer(text)]
        + [(m.start(), m.end(), False, m.group(1))
           for m in _CLOSE.finditer(text)])
    found, open_at = [], None
    for start, end, is_open, name in tokens:
        line = text.count("\n", 0, start) + 1
        if is_open:
            if open_at is not None:
                raise AssertionError(
                    "%s:%d: live:%s opens inside live:%s. Markers do not "
                    "nest — the inner value would be rewritten as part of "
                    "the outer one" % (where, line, name, open_at[3]))
            open_at = (start, end, is_open, name)
            continue
        if open_at is None:
            raise AssertionError(
                "%s:%d: /live:%s closes a marker that never opened"
                % (where, line, name))
        if name != open_at[3]:
            raise AssertionError(
                "%s:%d: live:%s is closed by /live:%s. The name is repeated "
                "on the close so exactly this typo cannot swallow the rest "
                "of the paragraph into a value"
                % (where, line, open_at[3], name))
        value = text[open_at[1]:start]
        if "\n" in value:
            raise AssertionError(
                "%s:%d: live:%s spans a newline. A live value is one line "
                "so it can sit in a markdown table cell and so a diff of a "
                "refreshed value is one line" % (where, line, name))
        found.append(Marker(name, value, open_at[1], start,
                            text.count("\n", 0, open_at[0]) + 1))
        open_at = None
    if open_at is not None:
        raise AssertionError(
            "%s:%d: live:%s is never closed"
            % (where, text.count("\n", 0, open_at[0]) + 1, open_at[3]))
    return found


def compute(name: str, where: str, line: int) -> str:
    """Resolve one marker name to its current value.

    Args:
        name: The name spelled in the marker.
        where: The file it was found in, for the failure message.
        line: Its line number there.

    Returns:
        What the registered calculator derives right now.

    Raises:
        AssertionError: If nothing is registered under `name`. An unknown
            name is the one case where doing nothing would be defensible
            and is refused anyway: a marker nobody can compute is a number
            that looks watched and is not.
    """
    fn = CALCULATORS.get(name)
    if fn is None:
        raise AssertionError(
            "%s:%d: no calculator registered as %r. Known: %s. Register it "
            "in tests/livedoc.py or take the marker off the literal — do "
            "not leave prose claiming a derivation that does not exist"
            % (where, line, name, ", ".join(sorted(CALCULATORS)) or "none"))
    return fn()


def tracked_prose_files() -> list[Path]:
    """Every tracked markdown file, which is the whole scan surface.

    Asks git rather than globbing the disk. That is what keeps the promise
    in the pre-commit hook's name — untracked scratch files and the
    gitignored `docs/` tree are not merely skipped, they are never listed.

    DE-DUPLICATED HERE, AT THE SOURCE, as well as defensively inside
    `refresh_files`. `git ls-files` prints one row per STAGE for an
    UNMERGED path, so a conflicted file carrying a marker appears two or
    three times — and the corruption `refresh` was reported for is only
    the loudest of three things that follow. The other two are quieter
    and were both measured: `check_files` emits n identical drift lines
    for one file, so a reader resolving one conflict is told three
    numbers have moved; and `main`'s "N tracked markdown file(s)"
    over-counts by one per extra stage, which is a census this repo
    would otherwise have to guard. Fixing only the writer left the
    reader lying. BOTH and not either: this is the route in, and
    `refresh_files` keeps its own line because it is a public function
    that takes any list a caller hands it.

    Returns:
        Absolute paths, sorted and distinct, fixtures excluded.

    Raises:
        AssertionError: If git cannot list the index, or lists nothing.
            Both mean the scan surface is unknown rather than empty, and
            an unknown surface is the one input that makes every live
            value pass by measuring nothing.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z", "--", "*.md"],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AssertionError(
            "git could not list tracked markdown under %s (%s). An empty "
            "scan surface would let every live value pass by measuring "
            "nothing" % (REPO, exc)) from exc
    names = [n for n in out.stdout.split("\0") if n]
    files = sorted({REPO / n for n in names if not n.startswith(_FIXTURES)})
    # THE SUCCESS PATH HAD THE HOLE THE FAILURE PATH DID NOT. The `except`
    # above already names the consequence — "would let every live value
    # pass by measuring nothing" — but `git ls-files` exits 0 and prints
    # nothing for a checkout whose markdown is gone, untracked or excluded
    # by a mis-set pathspec, and that reached here as a quiet empty list.
    # `check` survived it downstream (`unplaced_calculators` complains that
    # every registered name is unplaced); `refresh` did not, and answered
    # "refreshed 0 value(s) across 0 tracked markdown file(s)", exit 0 —
    # an instrument reporting success for having found nothing to do.
    # The sibling that got this right is `census_probes.py`'s scratch-tree
    # builder, which refuses "to build a scratch tree that would make every
    # guard look silent" on the same condition.
    if not files:
        raise AssertionError(
            "git tracks no markdown under %s outside %s, so the live-value "
            "scan surface is empty. Refusing to report every value current "
            "against nothing — this is a broken checkout, not a clean one"
            % (REPO, _FIXTURES))
    return files


def _values(markers: Iterable[tuple[Path, Marker]]) -> dict[str, str]:
    """Derive each distinct marker name once.

    `AssertionError` propagates from `compute` for an unregistered name, and
    from the calculator itself when its subject is missing.

    Args:
        markers: The `(path, marker)` pairs about to be checked or written.

    Returns:
        Name to freshly computed value.
    """
    values: dict[str, str] = {}
    for path, marker in markers:
        if marker.name not in values:
            values[marker.name] = compute(
                marker.name, _rel(path), marker.line)
    return values


def _rel(path: Path) -> str:
    """Name a file the way a message should print it.

    Args:
        path: Any path.

    Returns:
        The repo-relative form when it is inside the repo, else the path.
    """
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _scan_all(paths: Sequence[Path]) -> list[tuple[Path, Marker]]:
    """Scan several files, keeping each marker with the file it came from.

    `AssertionError` propagates from `scan` on any malformed marker.

    Args:
        paths: Files to read.

    Returns:
        Every `(path, marker)` pair, files in the given order.
    """
    found = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        found.extend((path, m) for m in scan(text, _rel(path)))
    return found


def check_files(paths: Sequence[Path]) -> list[str]:
    """Recompute every marker in `paths` and report what has drifted.

    Changes nothing. This is the guard half; `refresh_files` is the repair.

    A malformed marker, an unregistered name or a calculator whose subject
    is gone raises `AssertionError` out of this instead of appearing in the
    returned list — louder than drift on purpose, because drift is what
    `refresh` repairs and those three are not.

    Args:
        paths: Files to check.

    Returns:
        One line per drifted marker, naming the file, the line, the marker
        and both values. Empty means every stored value is current.
    """
    pairs = _scan_all(paths)
    values = _values(pairs)
    drifted = []
    for path, marker in pairs:
        fresh = values[marker.name]
        if marker.stored != fresh:
            drifted.append(
                "%s:%d: live:%s stores %r, %s() derives %r"
                % (_rel(path), marker.line, marker.name, marker.stored,
                   marker.name, fresh))
    return drifted


def refresh_files(paths: Sequence[Path]) -> list[str]:
    """Recompute every marker in `paths` and write the answers back.

    Idempotent: a file whose values are already current is not rewritten at
    all, so a second run reports nothing and touches nothing.

    ONE FILE IS ONE FILE HOWEVER OFTEN IT IS NAMED, which is the first
    line of the body and is a repair rather than a nicety. Markers are
    spliced by OFFSET into text read once per PATH ENTRY, so a path
    appearing n times had its markers found n times and its text rewritten
    n times from a snapshot taken before any of them — n*n splices, each
    laying the fresh value over the first character of the value the
    previous splice wrote, and a stored `0` came back as
    `1410410410410410…` (`fresh + fresh[1:] * (n*n - 1)`). Two agents
    reported that corruption and both diagnosed substring replacement,
    which this function does not do; the real route in is
    `tracked_prose_files`, which reads `git ls-files` — one row per STAGE
    for an UNMERGED path, so resolving a merge conflict in a file carrying
    a marker is enough. Pinned by `TestRefreshCannotEatItsOwnAnswer`.
    `dict.fromkeys` and not `set`, so the order files are repaired in stays
    the order they were handed in.

    Raises `AssertionError` on a malformed marker, an unregistered name or a
    calculator whose subject is gone — the same refusals `check` makes, and
    made BEFORE anything is written, because a file half-rewritten from a
    broken derivation is worse than one left stale.

    Args:
        paths: Files to repair. Duplicates are collapsed.

    Returns:
        One line per marker whose stored value was replaced.
    """
    paths = list(dict.fromkeys(paths))
    pairs = _scan_all(paths)
    values = _values(pairs)
    changed = []
    for path in paths:
        mine = [m for p, m in pairs if p == path]
        if not mine:
            continue
        before = path.read_text(encoding="utf-8")
        text = before
        for marker in reversed(mine):        # right to left: offsets hold
            fresh = values[marker.name]
            if marker.stored == fresh:
                continue
            text = text[:marker.start] + fresh + text[marker.end:]
            changed.append(
                "%s:%d: live:%s %r -> %r"
                % (_rel(path), marker.line, marker.name, marker.stored,
                   fresh))
        if text != before:                   # untouched files stay untouched
            path.write_text(text, encoding="utf-8")
    return changed


def unplaced_calculators(paths: Sequence[Path]) -> list[str]:
    """Registered names that no marker in `paths` uses.

    A calculator nothing reads is a derivation that has stopped being
    watched — the same shape as a guard whose subject was reworded away,
    which this repo has caught five times in one file. Cheaper to refuse
    than to discover. `AssertionError` propagates from `scan` on a
    malformed marker.

    Args:
        paths: The files that constitute the whole scan surface.

    Returns:
        The unused names, sorted.
    """
    used = {m.name for _, m in _scan_all(paths)}
    return sorted(set(CALCULATORS) - used)


_USAGE = """\
usage: python3 tests/livedoc.py {check|refresh}

  check     recompute every live marker in tracked markdown, report drift,
            change nothing, exit 1 if anything moved
  refresh   recompute and write the current values back
"""


def main(argv: Sequence[str]) -> int:
    """Run one verb over every tracked markdown file.

    Args:
        argv: Command-line arguments after the program name.

    Returns:
        0 when everything is current (or was repaired), 1 when `check`
        found drift or an unplaced calculator, 2 on a usage error.
    """
    if len(argv) != 1 or argv[0] not in ("check", "refresh"):
        sys.stderr.write(_USAGE)
        return 2
    paths = tracked_prose_files()
    if argv[0] == "refresh":
        changed = refresh_files(paths)
        for line in changed:
            print(line)
        print("refreshed %d value(s) across %d tracked markdown file(s)"
              % (len(changed), len(paths)))
        return 0
    problems = check_files(paths)
    problems += ["no marker in tracked markdown uses live:%s — place it or "
                 "delete the calculator" % n
                 for n in unplaced_calculators(paths)]
    for line in problems:
        print(line)
    if problems:
        print("\n%d live value(s) have drifted from their derivation. "
              "Repair: python3 tests/livedoc.py refresh" % len(problems))
        return 1
    print("%d live value(s) current across %d tracked markdown file(s)"
          % (len(_scan_all(paths)), len(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
