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
counts reds, mutants or catalogue rows. `SESSION-HANDOVER.md` already has
five guards over it (`handover_catalogue_reds`, `handover_coverage_totals`,
`handover_durable_counts`, `handover_render_reds`, and the render-row
derivation beside them, all in `tests/test_mutants.py`), each requiring
its subject to appear EXACTLY ONCE. Those numbers are deliberately NOT
live, and the difference is not stylistic:

  - a livedoc marker promises "you never have to think about this number";
  - a census guard promises "when this number moves, STOP AND LOOK",
    because a red flipping is a claim about the product, not a count.

`refresh` would disarm the second promise by repairing the sentence a
human was supposed to read the failure of. Worse, the census readers match
on the numbers in place — `_HANDOVER_DURABLE` wants `**N / N / N** for
` + backticked filenames as one span — so a marker either breaks the guard
outright or has to generate the guard's own anchor, at which point the
guard checks generated text against its generator and can no longer fail.
That is the calibration-literal defect this repo already wrote up at
length beside `CATALOGUE_RED_IDS`. `test_the_census_sentences_stay_out_of
_livedoc` in `tests/test_livedoc.py` holds the line mechanically.

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
import subprocess
import sys
import unittest
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
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

    Returns:
        Absolute paths, sorted, fixtures excluded.

    Raises:
        AssertionError: If git cannot list the index, which means this is
            not a checkout and the scan surface is unknown rather than
            empty.
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
    return sorted(REPO / n for n in names if not n.startswith(_FIXTURES))


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
