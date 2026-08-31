#!/usr/bin/env python3
"""R6 per-turn measurement probe.

Every number this prints is derived HERE from the project's own files or the
skill's own CLI. Nothing is carried between turns, and nothing is reimplemented
that the product already computes -- geometry goes through `x-geometry --diff`,
never through arithmetic written in this file.

THE CACHED-DERIVATION INSTRUMENT is `--lint-drift`: it asks the running server
for its lint (`GET /api/state`) and, in the same breath, asks a cold CLI
process for the same lint off disk. R5's carried prediction for R6 is that the
headline will be a value computed once and never invalidated; the two readings
disagreeing IS that defect, and nothing else in the harness can see it.

Usage:
    probe.py census            element/arrow/locked counts per artifact
    probe.py saves [N]         the last N saves with their verb_counts
    probe.py events [N]        the last N typed events off the runtime log
    probe.py lint-drift        server lint vs cold CLI lint, compared
    probe.py all               census + saves + events + lint-drift
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import urllib.request

PROJECT = os.path.expanduser("~/docs/optimization/r6/projects/argus6")
CANVAS = ("/home/cognizac/Projects/wysiwyg_grilling_skill.worktrees/"
          "capability_assessment/skills/wysiwyg-grilling/scripts/canvas.py")
ARTIFACTS = os.path.join(PROJECT, "project_knowledge", "artifacts")
SAVES = os.path.join(PROJECT, "project_knowledge", "saves")


def _cli(*args: str) -> str:
    """Run the skill's own CLI against the project and return its stdout.

    Args:
        *args: Subcommand and flags, e.g. ``("lint",)``.

    Returns:
        Combined stdout/stderr as text, or an error marker on failure.
    """
    try:
        out = subprocess.run([sys.executable, CANVAS, "--project", PROJECT, *args],
                             capture_output=True, text=True, timeout=120)
        return (out.stdout or "") + (out.stderr or "")
    except Exception as exc:
        return "CLI-FAILED: %r" % (exc,)


def census() -> None:
    """Print per-artifact element, arrow, ownership and pin counts."""
    sys.path.insert(0, os.path.dirname(CANVAS))
    import canvas  # the product's own predicate
    print("%-34s %5s %6s %6s %8s %7s" % ("artifact", "els", "arrows", "owned",
                                         "authored", "pinned"))
    tot = [0, 0, 0, 0, 0]
    for path in sorted(glob.glob(ARTIFACTS + "/*.excalidraw")):
        with open(path, encoding='utf-8') as handle:
            doc = json.load(handle)
        els = doc.get("elements", [])
        arrows = [e for e in els if e.get("type") == "arrow"]
        authored = [a for a in arrows
                    if (a.get("customData") or {}).get("routed") == "authored"]
        owned = [a for a in arrows if canvas.server_owns_geometry(a)]
        pinned = [e for e in els if e.get("locked")]
        row = (len(els), len(arrows), len(owned), len(authored), len(pinned))
        tot = [t + r for t, r in zip(tot, row)]
        print("%-34s %5d %6d %6d %8d %7d" % (os.path.basename(path), *row))
    print("%-34s %5d %6d %6d %8d %7d" % ('TOTAL', *tot))


def saves(n: int = 5) -> None:
    """Print the last `n` saves with the facts each one narrated.

    Args:
        n: How many saves to show, newest last.

    """
    files = sorted(glob.glob(SAVES + "/*.json"))[-n:]
    for path in files:
        with open(path, encoding='utf-8') as handle:
            doc = json.load(handle)
        summ = doc.get("summary") or {}
        print("%-40s %-22s %s" % (os.path.basename(path),
                                  (summ.get("headline") or "")[:22],
                                  json.dumps(summ.get("verb_counts") or {})))


def events(n: int = 15) -> None:
    """Print the tail of the server's typed event log."""
    state = _cli("status")
    log = None
    for line in state.splitlines():
        if line.startswith("EVENTS_LOG="):
            log = line.split("=", 1)[1].strip()
    if not log:
        import hashlib
        import tempfile
        h = hashlib.sha1(PROJECT.encode("utf-8")).hexdigest()[:12]
        log = os.path.join(tempfile.gettempdir(), "wysiwyg-grilling",
                           "%s.events.jsonl" % h)
    if not os.path.exists(log):
        print("no event log at %s" % log)
        return
    with open(log) as handle:
        rows = [json.loads(x) for x in handle if x.strip()]
    for ev in rows[-n:]:
        print("%5s %-20s %s" % (ev.get("seq"), ev.get("type"),
                                json.dumps({k: v for k, v in ev.items()
                                            if k not in ("seq", "ts", "type")})[:110]))


def lint_drift() -> None:
    """Compare the running server's lint against a cold CLI lint.

    The server's reading comes from `GET /api/state`; the CLI's comes from a
    fresh process reading the same project off disk. They describe the same
    artifacts at the same moment, so any disagreement is a staleness defect
    rather than a difference of opinion.

    """
    status = _cli("status")
    url = None
    for line in status.splitlines():
        if line.startswith("URL="):
            url = line.split("=", 1)[1].strip()
    if not url:
        print("server not running -- lint-drift needs a live server")
        print(status.strip()[:300])
        return
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=15) as resp:
            state = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print("server lint unreadable: %r" % (exc,))
        return
    served = state.get("lint") or state.get("findings") or state.get("checks")
    if served is None:
        print("PROBE IS A NO-OP: /api/state carries no lint key; keys=%s"
              % sorted(state.keys())[:15])
        return
    # Count STRUCTURALLY on both sides -- the first draft of this counted the
    # substrings "ERROR"/"WARN"/"NOTE" in each side's text, and the served JSON
    # spells them `errors`/`warnings`, so it scored 0/0/0 and reported a DRIFT
    # that was entirely the instrument's. Compare lists to line-prefixes.
    s = {"E": 0, "W": 0, "N": 0}
    per_artifact = {}
    for aid, block in (served or {}).items():
        if not isinstance(block, dict):
            continue
        got = (len(block.get("errors") or []), len(block.get("warnings") or []),
               len(block.get("notes") or []))
        per_artifact[aid] = got
        s["E"] += got[0]
        s["W"] += got[1]
        s["N"] += got[2]
    cold = _cli("lint")
    c = {"E": 0, "W": 0, "N": 0}
    for line in cold.splitlines():
        if line.startswith("LAYOUT_ERROR="):
            c["E"] += 1
        elif line.startswith("LAYOUT_WARNING="):
            c["W"] += 1
        elif line.startswith("LAYOUT_NOTE="):
            c["N"] += 1
    if s == {"E": 0, "W": 0, "N": 0} and c == {"E": 0, "W": 0, "N": 0}:
        print("PROBE IS A NO-OP: both sides read zero -- nothing was compared")
        return
    print("SERVED (GET /api/state) : %s   per-artifact %s" % (s, per_artifact))
    print("COLD  (fresh CLI lint)  : %s" % c)
    print("VERDICT: %s" % ("AGREE" if s == c else "DRIFT  <-- cached derivation"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    arg = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if cmd in ("census", "all"):
        print("== CENSUS ==")
        census()
        print()
    if cmd in ("saves", "all"):
        print("== SAVES ==")
        saves(arg if arg is not None and cmd == "saves" else 5)
        print()
    if cmd in ("events", "all"):
        print("== EVENTS ==")
        events(arg if arg is not None and cmd == "events" else 15)
        print()
    if cmd in ("lint-drift", "all"):
        print("== LINT DRIFT (the cached-derivation instrument) ==")
        lint_drift()
