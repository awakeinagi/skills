# CLAUDE.md

Read **[AGENTS.md](AGENTS.md)** — it is the canonical engineering standard for
this repo and covers everything below in detail.

The four things most likely to trip you up:

1. **`canvas.py` is stdlib-only and single-file.** Never add a third-party
   import or split it into a package. `dependencies = []` is load-bearing.
2. **Python floor is 3.9.** Use `from __future__ import annotations` so you can
   still write `list[str]` and `X | None`.
3. **Docstrings on every module, class, function, and method** — Google style
   with `Args:` / `Returns:` / `Raises:`. Ruff enforces this everywhere except
   `canvas.py` and `tests/test_backend.py`, which hold standing exemptions
   while their existing debt burns down. Those are the two files you are most
   likely to be editing, so write to standard there by hand — the gate will
   not catch you.
4. **No autoformatter.** Don't run `ruff format` or `black`; match the
   surrounding packed style by hand. See AGENTS.md § Formatting.

Before you claim work is done:

```bash
uvx pre-commit run --all-files
```
