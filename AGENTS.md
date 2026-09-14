# Agent guide

This repository is a collection of independent agent skills. Keep changes
scoped to the skill being worked on and read that skill's entrypoint and local
contributor documentation before editing it.

## Repository map

- `skills/<name>/SKILL.md` is the discoverable runtime entrypoint for each
  skill. Its frontmatter decides when the skill is selected.
- Supporting instructions, scripts, templates, and assets live beside the
  entrypoint that owns them. Keep detailed or conditional guidance out of the
  root documentation.
- `skills/silence-is-a-bug/` contains the check-validation doctrine and its
  focused references.
- `skills/unlazy/` is a self-contained Node-based skill. Read its
  `CONTRIBUTING.md` before changing it and use its package-local tests.
- `skills/wysiwyg-grilling/` owns the visual-grilling skill and its bundled
  backend. Its source frontend and test infrastructure also live at
  `frontends/wysiwyg-grilling/` and `tests/`. Read
  `skills/wysiwyg-grilling/AGENTS.md` before changing any of those paths or
  their repository configuration.

## Contribution rules

- Preserve each skill's `name` and keep its frontmatter `description` concise
  and discriminating: say what the skill does and when it should activate.
- Use progressive disclosure. Keep the essential workflow in `SKILL.md` and
  route substantial conditional detail to focused references. Do not create
  support files without a concrete need.
- Keep relative links valid and give detailed guidance one canonical owner
  instead of copying it between root and skill scope.
- Do not rewrite historical plans, handovers, assessments, or archives unless
  the task specifically concerns them. Update live pointers when their target
  moves.
- Match the conventions and dependency boundary of the skill being changed.
  A dependency acceptable in one skill is not automatically acceptable in
  another.
- Do not hand-edit generated artifacts. Follow the owning skill's documented
  build or refresh workflow.

## Validation

Run the narrowest checks owned by the changed skill, plus repository hygiene
for cross-cutting changes. Useful collection-level checks are:

```bash
git diff --check
python3 tests/livedoc.py check
uvx pre-commit run --all-files
```

The pre-commit command runs the automatic stage only. Skill-local docs name
any additional tests, provisioning, browser checks, or manual-stage hooks.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
