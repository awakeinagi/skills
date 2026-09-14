# WYSIWYG Grilling

A Claude Code skill and bundled local web app that extends grilling-with-docs:
during design conversations the agent externalizes its understanding as
**visual artifacts** (wireframes, flows, domain diagrams) on an editable
Excalidraw canvas, you edit them directly, and the agent narrates its reading
of every change — closing the alignment loop faster than prose alone.

The core cycle is the **Refinement Loop**: the agent revises the canvas and
asks for feedback → you edit and **Save** → the agent narrates its
understanding, hypothesizing reasoning and implications → you correct →
repeat. The project glossary is [CONTEXT.md](../../CONTEXT.md).

## Install

Prerequisite: **uv on PATH** (https://docs.astral.sh/uv/ — it provisions
Python itself), or any Python ≥ 3.9. No Node, no build step, no network
after install — the web app ships prebuilt with fonts.

**As a plugin** (versioned, auto-updating):

```text
/plugin marketplace add <this-repo-url-or-owner/repo>
/plugin install wysiwyg-grilling
```

**Or plain git** (zero-infrastructure fallback):

```text
git clone <this-repo> ~/.claude/skills/wysiwyg-grilling-repo
ln -s ~/.claude/skills/wysiwyg-grilling-repo/skills/wysiwyg-grilling ~/.claude/skills/wysiwyg-grilling
```

Windows: use `scripts\canvas.ps1` / `scripts\canvas.cmd` (never `.sh`).

## First run

Open Claude Code in any project and start a design conversation (or say
"open the canvas"). The skill launches a local server on `127.0.0.1` (random
port), opens the browser, creates `project_knowledge/` in your project, and
the loop teaches itself by doing one round. Everything durable — artifacts,
save history, the registry, and config — lives in `project_knowledge/`; commit
it.

## Repository layout

```text
.claude-plugin/                    marketplace and plugin manifests
skills/wysiwyg-grilling/           skill entrypoint, references, and scripts
  scripts/canvas.py                single stdlib-only server and CLI (3.9+)
  scripts/web/                     committed prebuilt frontend and fonts
frontends/wysiwyg-grilling/        web app source (maintainers only)
tests/                             backend and integration tests
```

Maintainers should read the [engineering guide](AGENTS.md) before changing
these paths.

## Maintainer build

When you change `frontends/wysiwyg-grilling/src`, rebuild the shipped frontend:

```text
cd frontends/wysiwyg-grilling && npm install && npm run build
```

That rebuilds `skills/wysiwyg-grilling/scripts/web/`, copies Excalidraw's
fonts, and writes `build-stamp.json` (a source hash — `canvas.py start` warns
when the committed bundle is stale). Commit the result.

**A rebuild renames every minified symbol, so it also invalidates every
citation.** This tree's prose cites the client by symbol and offset to justify
arithmetic it transcribes, and two hand sweeps have already found those
citations rotted onto live, unrelated functions. After the build,
`TestACitedClientSymbolStillMeansWhatTheProseSaysItMeans` in
`tests/test_mutants.py` goes red at each stale one and names the symbol that
replaced it; re-derive each by anchoring on a rename-invariant slice of the
code and reading the name back out, never by keeping the name and moving the
offset. Include those repairs in the same change as the bundle.

## Tests

```text
python3 tests/test_backend.py
python3 -c "import ast; ast.parse(open('skills/wysiwyg-grilling/scripts/canvas.py').read(), feature_version=(3,9))"
```

## Provenance

See [NOTICE.md](NOTICE.md).
