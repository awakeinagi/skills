# Awake Inagi skills

A collection of independent skills for coding agents. Each directory under
[`skills/`](skills/) is an installable unit with a `SKILL.md` entrypoint that
describes when to use it and routes to any deeper instructions.

## Skills

| Skill | What it does | Start here |
| --- | --- | --- |
| **silence-is-a-bug** | Finds defects that tests, linters, validators, and monitors fail to report. It uses failing pins, positive controls, mutation-style probes, and independent fix ownership to prove that checks actually speak. | [Skill entrypoint](skills/silence-is-a-bug/SKILL.md) |
| **unlazy** | Makes substantial autonomous work testable with acceptance ledgers, a depth-tree decomposition, explicit command approval, and independent re-verification. | [Overview](skills/unlazy/README.md) · [Skill entrypoint](skills/unlazy/SKILL.md) |
| **wysiwyg-grilling** | Runs a collaborative design loop on a local Excalidraw canvas: the agent draws wireframes, flows, and domain diagrams, then interprets the user's edits. | [Overview](skills/wysiwyg-grilling/README.md) · [Skill entrypoint](skills/wysiwyg-grilling/SKILL.md) |

The skill directories are the source of truth. Their entrypoints contain the
current activation rules, runtime requirements, and links to focused reference
material.

## Install and discover

Install from the repository with the
[`skills` CLI](https://github.com/vercel-labs/skills):

```text
npx skills add awakeinagi/skills
```

The CLI discovers the `SKILL.md` entrypoints and lets you choose supported
agents and skills. Use `-g` for a user-level install or `--all` for every
detected agent.

For a manual installation, clone this repository and copy or link only the
skill directories you want into your agent's skill directory:

```text
Claude Code:  ~/.claude/skills/<skill-name>
Codex CLI:    ~/.codex/skills/<skill-name>
```

Claude Code users can also install the WYSIWYG Grilling plugin, which bundles
its prebuilt canvas application:

```text
/plugin marketplace add awakeinagi/skills
/plugin install wysiwyg-grilling
```

After installation, invoke a skill by name where the agent supports explicit
skill calls, or describe a task that matches its `SKILL.md` description.

## Contributing

Read [AGENTS.md](AGENTS.md) for collection-wide guidance, then follow the
documentation owned by the skill you are changing:

- [silence-is-a-bug entrypoint](skills/silence-is-a-bug/SKILL.md)
- [unlazy contributor guide](skills/unlazy/CONTRIBUTING.md)
- [WYSIWYG Grilling engineering guide](skills/wysiwyg-grilling/AGENTS.md)

Tests and build commands differ by skill. Run the focused checks documented by
the owner before using the repository-wide pre-commit gate.

## Licensing

See the [repository license](LICENSE.md) and any skill-local license or notice,
including
[WYSIWYG Grilling's notice](skills/wysiwyg-grilling/NOTICE.md) and
[unlazy's license](skills/unlazy/LICENSE).
