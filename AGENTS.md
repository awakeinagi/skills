# AGENTS.md

Engineering standards for **wysiwyg-grilling-skill**. This is the canonical
file; `CLAUDE.md` points here. Read it before writing code.

## What this repo is

| Path | What | Language |
| --- | --- | --- |
| `skills/wysiwyg-grilling/scripts/canvas.py` | The whole backend: HTTP server, op applier, scene differ, fact generator, CLI. <!-- live:canvas_py_lines -->~28.2k<!-- /live:canvas_py_lines --> lines. | Python 3.9+ |
| `skills/wysiwyg-grilling/` | The shipped Claude Code skill (`SKILL.md` + `references/`). | Markdown |
| `frontends/wysiwyg-grilling/src/` | Excalidraw canvas UI. <!-- live:frontend_src_lines -->~4.9k<!-- /live:frontend_src_lines --> lines. | TypeScript / React 18 |
| `tests/test_backend.py` | `unittest` suite against `canvas.py`. <!-- live:test_backend_cases -->1008<!-- /live:test_backend_cases --> tests — the most of any module in a `tests/` tree that also holds the mutation harness and the render tier. (Most *tests*, not most lines: `test_mutants.py` is the bigger file.) | Python |
| `tests/fixtures/` | The frozen corpus every check is measured against: real drawings from past sessions, byte-exact. Across <!-- live:corpus_projects -->5<!-- /live:corpus_projects --> projects it currently lints to <!-- live:corpus_census -->artifacts=24 scopes=28 errors=0 warnings=56 notes=88<!-- /live:corpus_census -->. | Excalidraw JSON |

The frontend builds *into* the skill (`vite.config.ts` → `scripts/web/`), so a
released skill is self-contained.

**`artifacts` and `scopes` are different numbers and the row says both.**
`scopes` is what got LINTED and runs higher, because the registry is a scope
with findings of its own and no artifact behind it (r5-1 — `cmd_lint` carries
the ruling). `scopes - artifacts` is the count of projects that currently have
a registry finding, so it moves when one is settled while `artifacts` does
not: delete one project's glossary and `scopes` drops by one with nothing
removed. Read both off the row — this paragraph deliberately restates
neither, because a literal beside a live value is the half-derived sentence
that goes quietly self-contradictory.

**Quote the corpus census from that row, never from a measurement you took.**
Two reviewers reading the same corpus published `0/46/27` and `0/38/20`. The
warnings half of that gap turned out not to be a difference of convention at
all: one reader asked for each artifact's type as
`registry["artifacts"][aid]["type"]`, and the registry has **no `artifacts`
key** — so the type came back `None` for all 24, silently, switching off every
type-gated check. Use `Store.artifact_type()`. The notes half *is* structural
(registry-scope and cross-artifact findings a per-artifact walk cannot reach).
The calculator's docstring in `tests/livedoc.py` carries the full derivation,
including the one figure nobody has reproduced.

Every number in that table is a **live value**, not a literal — and the count
of them is deliberately not stated here, because that sentence read "Four"
and would have needed editing to add a fifth. An
`<!-- live:NAME -->` / `<!-- /live:NAME -->` comment pair wraps a value that
`tests/livedoc.py` computes, and `python3 tests/livedoc.py check` runs as a
pre-commit hook so they cannot go stale. Every one of them had — this table
said "~5.4k lines" at 18k and "~120 tests" at 628. When a hook tells you a
live value drifted, the repair is `python3 tests/livedoc.py refresh`, not an
edit. Two things worth knowing before you add one: marker names are
lowercase, which is what lets this paragraph spell the syntax with an
uppercase `NAME` without creating a marker; and the suite's *runtime* is
deliberately still a literal, because a value that moves on its own would
make `refresh` rewrite the file forever. That module's docstring has the
rest, including which of the census numbers in `SESSION-HANDOVER.md` may be
live and which may not — the two **totals** are, the **rosters** of mutant
ids and test names beside them are not, and the difference is whether a
human is supposed to stop and read the change.

## Hard constraints — do not violate

1. **`canvas.py` is stdlib-only.** `dependencies = []` in `pyproject.toml` is
   load-bearing: the skill is run by users as `uv run canvas.py` with no
   install step. Never add a third-party import to it.
2. **`canvas.py` is a single file.** Do not split it into a package. The skill
   ships one script.
3. **Python floor is 3.9** (RHEL9). No `match`, no PEP 604 `X | Y` at runtime,
   no `tomllib`. See *Type hints* for how to write modern annotations anyway.
4. **Dev tooling must not become a runtime dependency.** Linters and type
   checkers run via `uvx`/`pre-commit`, never via `pyproject.toml`
   `dependencies`.
5. **The stdlib restriction is scoped to the skill bundle
   (`skills/wysiwyg-grilling/`) — nothing else** (ruled 2026-08-14).
   Tests, instruments, and repo tooling MAY take third-party dependencies,
   entered through a `pyproject.toml` dev dependency group (so `uv run`
   picks them up and `dependencies = []` stays empty), never through
   anything the skill ships and never as an import in `canvas.py`. Two
   things this ruling does NOT change: the test framework stays stdlib
   `unittest` (the mutation harness's flip contract is built on
   `expectedFailure`'s unexpected-success semantics — do not migrate to
   pytest), and any test file that gains a third-party import should
   degrade loudly (a clear skip naming the missing package), since bare
   `python3 -m unittest` on an uninstalled clone is no longer guaranteed.

## Documentation standard

Every **module, class, function, and method** carries a docstring — public and
private alike. This is enforced (`ruff` rule set `D`, Google convention).

### Python — Google style

Sections, in order, only when they apply: `Args`, `Returns`, `Yields`,
`Raises`, `Attributes` (classes), `Example`.

**The code blocks in this file show the target, not the tree.** `slugify`,
`diff_scenes` and `fingerprint` are real symbols and all three are shown here
rewritten to standard; the shipped versions are unannotated or out of Google
shape and are counted in *Known debt* below. Do not read them as quotations,
and do not conclude from grepping one that the standard is aspirational — it
is what the retrofit rule at the end of this file exists to converge on.

```python
def slugify(text: str, fallback: str = "item") -> str:
    """Derive a stable, collision-free slug from a display name.

    Ids are identity anchors, so the one thing this may never do is map two
    different names onto one id. `'Émissions'` losing its `É` is ugly, but
    `'報告'` and `'分析'` both landing on `'item'` silently merges two
    concepts, so scripts with no ASCII decomposition get a hash suffix.

    Args:
        text: Display name to slug. May be empty or non-Latin.
        fallback: Stem used when `text` yields no ASCII characters.

    Returns:
        A lowercase `[a-z0-9-]` slug. Stable for a given `text` and distinct
        between different `text` values.
    """
```

Rules:

- **Summary line**: one line, imperative mood ("Derive", not "Derives"), ends
  with a period, fits on the first line after `"""`.
- **Blank line** after the summary whenever more follows (`D205`).
- **Why, not what.** The body explains the decision the code encodes — the
  failure it prevents, the invariant it holds. The existing prose comments in
  `canvas.py` are the model; keep that voice, just move it into the Google
  frame. Do not narrate control flow the reader can see.
- **`Args`**: every parameter, by name, no type in the text (the annotation is
  the type). Omit the whole section for zero-arg functions. Skip `self`/`cls`.
- **`Returns`**: required whenever the function returns a value (`DOC201`).
  Describe the meaning, not the type. Omit for `-> None`.
- **`Raises`**: every exception raised deliberately (`DOC501`).
- **Classes** document purpose in the class docstring with `Attributes:`;
  `__init__` documents its `Args`.
- **Module docstrings** state what the module is for and its contract. The one
  in `canvas.py` (commands + invocation) is the standard to match.

### TypeScript — TSDoc

Every exported symbol, React component, and non-trivial internal helper gets a
`/** … */` block. Use `@param`, `@returns`, `@throws`.

```ts
/**
 * Fingerprint the semantically-significant parts of a scene.
 *
 * Excalidraw re-derives bound-arrow endpoint geometry on every load, drag,
 * and undo, so hashing raw geometry made drag-then-undo read as dirty
 * forever. Bound arrows therefore hash binding topology plus interior
 * points; bound labels hash text, never their re-centered position.
 *
 * @param elements - Scene elements, including soft-deleted ones.
 * @returns A hash that changes only on user-meaningful edits.
 */
export function fingerprint(elements: readonly ExcalidrawElement[]): string {
```

- Do **not** restate types in prose — the signature carries them.
- React components document what they render and their props contract.
- Props interfaces document each field with a `/** … */` on the member.

## Type hints

### Python

Annotate **every** parameter and return (`ruff` rule set `ANN`).

Put `from __future__ import annotations` at the top of the module (directly
after the docstring). It makes annotations strings at runtime, so 3.9 can use
modern syntax:

```python
from __future__ import annotations

def diff_scenes(
    old_els: list[dict[str, Any]],          # not typing.List — future import
    new_els: list[dict[str, Any]],
    significant_attrs: list[str] | None = None,   # not Optional[...]
) -> list[dict[str, Any]]:
```

- Use `list`/`dict`/`tuple`/`set` builtins and `X | None`, never
  `typing.List` / `Optional`.
- `-> None` on procedures. Never leave a return unannotated.
- Excalidraw elements are loose JSON: `dict[str, Any]` is correct and honest.
  Do not invent `TypedDict`s that drift from the real payloads.
- Prefer `Sequence`/`Mapping` (from `collections.abc`) for read-only params.
- `Path` for filesystem paths, not `str`.

### TypeScript

- `strict: true` is the target (see *Known debt*). Write new code to pass it.
- Ban new `: any`. Use `unknown` plus narrowing at boundaries. All existing
  `any`s are debt, not precedent.
- Type the API boundary (`api.ts`) — that is where `unknown` should be
  converted once, not sprinkled.

## Commands

```bash
# Tests. ~220s wall (measured 2026-08-20; it said ~80s from before the
# mutation harness and render tier moved in here). Machine-dependent, and
# quoted only so you size the timeout to minutes — do not kill it at 90s.
python3 -m unittest discover -s tests -q

# Live values in tracked prose: report drift / write the current answers
python3 tests/livedoc.py check
python3 tests/livedoc.py refresh

# Lint + docstrings + annotations
uvx ruff check .

# Type check
uvx mypy skills/wysiwyg-grilling/scripts/canvas.py

# Frontend (requires: npm --prefix frontends/wysiwyg-grilling ci)
npm --prefix frontends/wysiwyg-grilling run lint
npm --prefix frontends/wysiwyg-grilling run typecheck

# The gate — the <!-- live:gate_hooks_automatic -->18<!-- /live:gate_hooks_automatic --> hooks that fire on a commit.
# NOBODY RUNS THIS FOR YOU. There is no CI in this repo.
uvx pre-commit run --all-files

# The line above is not "everything": <!-- live:gate_hooks_manual -->2<!-- /live:gate_hooks_manual --> hooks are manual-stage,
# and `--all-files` leaves them out without saying so.
uvx pre-commit run guard-mutants-sweep --hook-stage manual  # ~30s, rewrites canvas.py
uvx pre-commit run e2e-tests --hook-stage manual            # Playwright
```

**That first comment said "Everything, as CI runs it" until 2026-08-20, and
was wrong twice.** There is no CI — no workflow file, no runner, no hosted
anything; `git ls-files` matches nothing under `.github/` or any sibling. And
`--all-files` is not everything: it runs the automatic stage only, so the
guard-mutant sweep and the Playwright suite were both outside a sentence
claiming to cover them. Recorded rather than quietly deleted, because the two
halves fail differently. A wrong count is read and corrected; **a wrong claim
about a safety net is never read at all** — it works by stopping the reader
from looking, and it had been doing that for an unknown length of time.

Both counts are live values, so "18" and "2" cannot drift out from under this
paragraph the way "Everything" did. What CI would take to become true: a
runner, a workflow file invoking both stages, and a status this file could
point at. None exists. Until one does, the safety net is you.

## Pre-commit

Install the hook once:

```bash
uvx pre-commit install
```

Then it runs on every `git commit` against staged files. To run every
automatic hook over the whole tree, `uvx pre-commit run --all-files` — that is
the gate, and it is still not the manual stage above. To update pinned tool
versions, `uvx pre-commit autoupdate`.

Two ways a hook here passes without checking anything, both worth knowing
because both print green. A `files:`-scoped hook whose pattern matches nothing
prints `Skipped` and exits 0 — on a commit, that is correct behaviour and is
why `mypy`, `backend-tests`, `frontend-deps`, `eslint` and `tsc` carry
patterns; under `--all-files` every pattern matches, so the gate is the run
that closes it. A manual-stage hook is skipped by `--all-files` outright.
Neither is a defect, and neither is visible to a reader who was told
"everything".

The frontend hooks need `node_modules`; run
`npm --prefix frontends/wysiwyg-grilling ci` once, or those hooks will fail
with a clear message rather than silently passing.

That sentence was **not true until 2026-08-18** (v0.9 whole-branch review,
M-6): a fresh clone got `sh: 1: eslint: not found` and exit 127, which is
loud but names neither the cause nor the fix. The `frontend-deps` hook now
runs first and prints the `npm ci` line, so the promise above is kept by a
check rather than by this paragraph. Its `files:` pattern is the **union** of
the `eslint` and `tsc` patterns, not a copy of either — `tsconfig.json` fires
only `tsc`, `eslint.config.js` only `eslint` — which is what makes the remedy
arrive first whenever either of them would have run. Recorded rather than
quietly corrected, because "the docs described a behaviour the tool did not
have" is the class this repo keeps finding and the useful part is the
pattern.

## Formatting — no autoformatter

**`ruff format` is deliberately not used.** `canvas.py` packs data tables into
filled lines:

```python
DEFAULT_SIGNIFICANT_ATTRS = [
    "type", "x", "y", "width", "height", "angle", "points", "text",
    "containerId", "frameId", "groupIds", "startBinding", "endBinding",
    # … the rest in canvas.py; excerpted here for the packing, not quoted
]
```

Ruff and Black have no "fill" mode: any collection that does not fit on one
line is exploded to one item per line. That inflates the file and destroys
`git blame`, with no readability gain for tables like this one. Line length is
policed by the linter (`E501`, 88 cols) instead of a formatter.

Re-measure the blast radius rather than quoting it — it grows with the tree,
and the figure here was "~6,000 lines across the two Python files" when the
repo had two:

```bash
uvx ruff format --check .            # how many files it would rewrite
uvx ruff format --diff . | grep -c '^-[^-]'   # how many existing lines
```

Measured 2026-08-20: **19 files, 19,793 lines** repo-wide; **11,985** of those
in `canvas.py` and `tests/test_backend.py` alone.

Match surrounding style by hand: ~79-col target, packed collections, aligned
trailing comments.

## Known debt

Tracked in `pyproject.toml` under `[tool.ruff.lint.per-file-ignores]`, most
lines annotated with a measured count (`D105` and `D401` carry none, and
`ANN` is annotated by the comment block above it rather than in line). Delete
a line once that code is clean — the ignore list is a burn-down chart, not a
permanent exemption.

**Re-measure before you quote any of this, and use the command below, not
`uvx ruff check --statistics`.** A bare `ruff check` is silent about every row
in this table by construction: the per-file-ignores *are* the table, so the
gate's own invocation prints nothing and exits 0. That is the correct
behaviour for a gate and a useless one for a burn-down, and it is why the
"re-measure rather than trusting these" instruction here sat broken while the
numbers under it went stale. You have to switch the ignores off:

```bash
# Python — the ignore list is what is being measured, so clear it
uvx ruff check --config 'lint.per-file-ignores={}' --statistics \
  skills/wysiwyg-grilling/scripts/canvas.py tests/test_backend.py

# Frontend — needs `npm --prefix frontends/wysiwyg-grilling ci` first
npm --prefix frontends/wysiwyg-grilling run lint
```

Snapshot measured 2026-08-20. Every Python row moves with the tree, so treat
these as a reading with a date on it, not as a fact:

| Debt | Count | Where |
| --- | --- | --- |
| Missing docstrings (`D101`/`D102`/`D103`/`D107`) | 61 | `canvas.py` |
| Docstrings not in Google shape (`D205`/`D209`/`D4xx`) | 75 | `canvas.py` |
| Missing `Returns:` / `Raises:` (`DOC201`/`DOC501`) | 55 | `canvas.py` |
| Missing annotations (`ANN`) | 1317 | `canvas.py` |
| Code-quality findings (`SIM`/`PERF`/`B`/`UP`…) | 97 | `canvas.py`, tests |
| Missing TSDoc (`jsdoc/require-*`) | 30 | `src/` |
| `: any` in TypeScript | 205 | `src/` |
| `react-hooks/exhaustive-deps` | 29 | `src/` |
| `strict: false` in `tsconfig.json` | — | frontend |

Three of the code-quality findings are defect-shaped rather than stylistic and
are worth reading before they are auto-fixed: `B023` (a closure capturing a
loop variable), `SIM115` ×2 (a file opened without a context manager), and
`E741` (an ambiguous `l`/`I`/`O` name). 34 of the 97 clear with
`uvx ruff check --fix`.

**These nine stay literals by decision, not by omission** — along with the
suite runtime and the `ruff format` blast radius above, which are the only
other measured numbers here that a marker does not cover. Three reasons, each
of which alone would be enough. They are a function of an *unpinned* external
tool: a new `ruff`
release that adds a rule moves them with nothing in this repo having changed,
and `refresh` would rewrite the table on somebody else's release schedule —
the same objection that keeps the suite runtime a literal. The frontend rows
need `node_modules`, which the gate itself proves may be absent, so a
calculator for them would fail `livedoc` on a clean clone and take the other
eight live values down with it. And pinning `ruff` inside `tests/livedoc.py`
would put the version in a second place, next to
`.pre-commit-config.yaml`'s `rev:`, where the two can drift. So: dated
literals with a working derivation printed above them, and no marker to imply
otherwise.

Retrofit rule: **when you touch a function, bring it to standard** — full
Google docstring and full annotations — even if the change was one line. That
is how the counts above go down without a stop-the-world commit.

New code has no exemption. Write it to standard the first time.
