# WYSIWYG Grilling

A Claude Code skill + bundled local web app that extends grilling-with-docs:
during design conversations the agent externalizes its understanding as
**visual artifacts** (wireframes, flows, domain diagrams) on an editable
Excalidraw canvas, you edit them directly, and the agent narrates its reading
of every change — closing the alignment loop faster than prose alone.

The core cycle is the **Refinement Loop**: the agent revises the canvas and
asks for feedback → you edit and **Save** → the agent narrates its
understanding, hypothesizing reasoning and implications → you correct →
repeat. Full design: [spec-v0.1.md](spec-v0.1.md) (v0 archive:
[spec.md](spec.md)) · glossary: [CONTEXT.md](CONTEXT.md)
· decisions: [docs/adr/](docs/adr/).

## Install

Prerequisite: **uv on PATH** (https://docs.astral.sh/uv/ — it provisions
Python itself), or any Python ≥ 3.9. No Node, no build step, no network
after install — the web app ships prebuilt with fonts.

**As a plugin** (versioned, auto-updating):

```
/plugin marketplace add <this-repo-url-or-owner/repo>
/plugin install wysiwyg-grilling
```

**Or plain git** (zero-infrastructure fallback):

```
git clone <this-repo> ~/.claude/skills/wysiwyg-grilling-repo
ln -s ~/.claude/skills/wysiwyg-grilling-repo/skills/wysiwyg-grilling ~/.claude/skills/wysiwyg-grilling
```

Windows: use `scripts\canvas.ps1` / `scripts\canvas.cmd` (never `.sh`).

## First run

Open Claude Code in any project and start a design conversation (or say
"open the canvas"). The skill launches a local server on `127.0.0.1` (random
port), opens the browser, creates `project_knowledge/` in your project, and
the loop teaches itself by doing one round. Everything durable —
artifacts, save history, the registry, config — lives in
`project_knowledge/`; commit it.

## Repo layout

```
.claude-plugin/            marketplace + plugin manifests
skills/wysiwyg-grilling/   the skill: SKILL.md, references/, scripts/
  scripts/canvas.py        single stdlib-only server + CLI (3.9+)
  scripts/web/             committed prebuilt frontend (+ fonts, build stamp)
frontends/wysiwyg-grilling/  web app source (maintainers only)
tests/                     backend test suite
docs/adr/                  architecture decision records
spec-v0.1.md               the current product spec
spec.md                    the v0 spec (frozen archive)
```

## Maintainer build (frontend)

Strangers never build; you do, when you touch `frontends/wysiwyg-grilling/src`:

```
cd frontends/wysiwyg-grilling && npm install && npm run build
```

That rebuilds `skills/wysiwyg-grilling/scripts/web/`, copies Excalidraw's
fonts, and writes `build-stamp.json` (a source hash — `canvas.py start`
warns when the committed bundle is stale). Commit the result.

## Tests

```
python3 tests/test_backend.py        # backend: differ, DAG, durability…
python3 -c "import ast; ast.parse(open('skills/wysiwyg-grilling/scripts/canvas.py').read(), feature_version=(3,9))"
```

## Provenance

See [skills/wysiwyg-grilling/NOTICE.md](skills/wysiwyg-grilling/NOTICE.md).
