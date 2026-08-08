#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""canvas.py — WYSIWYG Grilling: local canvas server + agent CLI.

Single-file, stdlib-only, Python 3.9+ (RHEL9 floor). No hooks, no push
mechanisms — everything is file/HTTP pull (spec §6.6).

Commands:
  start       launch (or reuse) the detached local server; prints KEY=VALUE lines
  status      health + protocol version of any running server
  wait        long-poll the save-events log (self-terminates under Bash's 600s)
  stop        shut the server down
  apply       apply a typed op batch (agent write path)
  screenshot  ask the connected browser for a PNG of an artifact
  serve       (internal) run the server in the foreground — used by `start`

Canonical invocation:  uv run canvas.py <cmd>   (bare python3 works too)
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROTOCOL_VERSION = 1
SOURCE_NAME = "wysiwyg-grilling"
FONT_LEGIBLE = 6       # Nunito — the default (no cursive anywhere; ADR 0001)
FONT_HAND = 5          # Excalifont — ships, non-default
PAPER_GROUND = "#faf8f2"
IDLE_MINUTES = float(os.environ.get("WYSIWYG_IDLE_MINUTES", "120"))
SENTINEL_COORD = 2 ** 40   # Excalidraw parks rebinding arrows at ±2^56
DETERMINISTIC = bool(os.environ.get("WYSIWYG_TEST_DETERMINISTIC"))

VOLATILE_ATTRS = ("version", "versionNonce", "updated", "index")

# Attributes the differ considers significant (config `significant_attrs`
# overrides). `boundElements` is derived machinery and never diffed directly.
DEFAULT_SIGNIFICANT_ATTRS = [
    "type", "x", "y", "width", "height", "angle", "points", "text",
    "containerId", "frameId", "groupIds", "startBinding", "endBinding",
    "customData", "strokeColor", "backgroundColor", "fillStyle",
    "strokeWidth", "strokeStyle", "roughness", "opacity", "fontSize",
    "fontFamily", "textAlign", "arrowhead", "startArrowhead", "endArrowhead",
]
STYLE_ATTRS = {
    "strokeColor", "backgroundColor", "fillStyle", "strokeWidth",
    "strokeStyle", "roughness", "opacity", "fontSize", "fontFamily",
    "textAlign", "startArrowhead", "endArrowhead",
}
GEOMETRY_ATTRS = {"x", "y", "width", "height", "angle", "points"}

FIRST_CLASS_DEFAULTS = {
    "wireframe": {"tier": "first-class", "priority": 1},
    "flow": {"tier": "first-class", "priority": 2},
    "domain": {"tier": "first-class", "priority": 3},
    "sequence": {"tier": "first-class", "priority": 4},
    "er": {"tier": "extended", "priority": 11},
    "class": {"tier": "extended", "priority": 12},
    "swimlane": {"tier": "extended", "priority": 13},
    "dfd": {"tier": "extended", "priority": 14},
    "mindmap": {"tier": "extended", "priority": 15},
    "architecture": {"tier": "extended", "priority": 16},
}

DEFAULT_CONFIG = {
    "migrations": ["0001-baseline"],
    "artifact_types": FIRST_CLASS_DEFAULTS,
    "narration_altitude": "clusters",
    "canvas_updates": "per-round",
    "deletion_conversation": True,
    "nudge_after_minutes": 10,
    "significant_attrs": DEFAULT_SIGNIFICANT_ATTRS,
}

DEFAULT_REGISTRY = {
    "migrations": ["0001-baseline"],
    "revn": 0,
    "head": "main",
    "branches": [{"name": "main", "head": 0, "archived": False}],
    "round": 0,
    "whose_move": "agent",
    "concepts": [],
    "mappings": [],
    "declined": [],
    "pins": [],
    "tripwires": [],
    "divergence_policies": [],
}


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg, code=2):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(code)


def own_source_hash():
    """Hash of this very file — lets start/status detect a running daemon
    that predates a canvas.py update (behavior changes without a protocol
    bump would otherwise be invisible and actively misleading)."""
    try:
        return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
    except OSError:
        return "unknown"


def slugify(text, fallback="item"):
    """Semantic slug. Ids are identity anchors, so the one thing this may
    never do is map two different names onto one id: 'Émissions' losing
    its É is ugly, but '報告' and '分析' both landing on 'item' silently
    merges two concepts."""
    raw = (text or "").strip()
    # NFKD first: 'Émissions' → 'emissions', 'CO₂' → 'co2' — decompose and
    # drop the combining marks instead of dropping the whole letter
    norm = unicodedata.normalize("NFKD", raw)
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if s:
        return s
    # scripts with no ASCII decomposition (CJK, Cyrillic, Greek…) have
    # nothing legible to slug to — keep the id opaque, but STABLE for a
    # given name and distinct between names
    if raw:
        return "%s-%s" % (fallback,
                          hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6])
    return fallback


def atomic_write(path, data):
    """Write bytes/str to path atomically (tmp file + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def dump_json(obj):
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(path, obj):
    atomic_write(path, dump_json(obj))


def read_json(path):
    with io.open(str(path), "r", encoding="utf-8") as f:
        return json.load(f)


def scene_hash(elements):
    """Stable hash of a normalized element list (identity of a scene state)."""
    payload = json.dumps(elements, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def det_seed(element_id):
    """Deterministic sketchy-render seed derived from the element id."""
    return zlib.crc32(element_id.encode("utf-8")) & 0x7FFFFFFF


# ---------------------------------------------------------------------------
# project paths + runtime (state/events live OUTSIDE project_knowledge —
# project_knowledge holds only durable state; .backups/ is its only
# gitignored member)
# ---------------------------------------------------------------------------

class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.pk = self.root / "project_knowledge"
        self.artifacts_dir = self.pk / "artifacts"
        self.saves_dir = self.pk / "saves"
        self.backups_dir = self.pk / ".backups"
        self.registry_path = self.pk / "model.json"
        self.config_path = self.pk / "config.json"
        h = hashlib.sha1(str(self.root).encode("utf-8")).hexdigest()[:12]
        self.runtime_dir = Path(tempfile.gettempdir()) / "wysiwyg-grilling"
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.state_path = self.runtime_dir / ("%s.state.json" % h)
        self.events_path = self.runtime_dir / ("%s.events.jsonl" % h)
        self.log_path = self.runtime_dir / ("%s.server.log" % h)
        self.shots_dir = self.runtime_dir / ("%s.shots" % h)

    def ensure_tree(self):
        for d in (self.pk, self.artifacts_dir, self.saves_dir):
            d.mkdir(parents=True, exist_ok=True)
        gi = self.pk / ".gitignore"
        if not gi.exists():
            atomic_write(gi, ".backups/\n")

    def name(self):
        return self.root.name

    def read_state(self):
        try:
            return read_json(self.state_path)
        except (OSError, ValueError):
            return None


# ---------------------------------------------------------------------------
# validation + repair (error codes carry LLM-addressable hints; repairs are
# logged, never silent)
# ---------------------------------------------------------------------------

class Issue:
    def __init__(self, code, msg, hint, repaired=False):
        self.code = code
        self.msg = msg
        self.hint = hint
        self.repaired = repaired

    def to_dict(self):
        return {"code": self.code, "msg": self.msg, "hint": self.hint,
                "repaired": self.repaired}


def _require(obj, key, default, issues, code, kind):
    if key not in obj or obj[key] is None or not isinstance(obj[key], type(default)):
        obj[key] = default
        issues.append(Issue(
            code, "%s.%s missing or wrong type — reset to default" % (kind, key),
            "The %s file was repaired in place; mention it to the user only if "
            "their data was involved." % kind, repaired=True))


def validate_config(cfg):
    issues = []
    if not isinstance(cfg, dict):
        return dict(DEFAULT_CONFIG), [Issue(
            "CFG-000", "config.json is not an object — replaced with defaults",
            "Previous content was invalid JSON structure.", True)]
    _require(cfg, "migrations", ["0001-baseline"], issues, "CFG-001", "config")
    _require(cfg, "artifact_types", dict(FIRST_CLASS_DEFAULTS), issues, "CFG-002", "config")
    _require(cfg, "narration_altitude", "clusters", issues, "CFG-003", "config")
    _require(cfg, "canvas_updates", "per-round", issues, "CFG-004", "config")
    if not isinstance(cfg.get("deletion_conversation"), bool):
        cfg["deletion_conversation"] = True
        issues.append(Issue("CFG-005", "config.deletion_conversation reset to true",
                            "Boolean expected.", True))
    if not isinstance(cfg.get("nudge_after_minutes"), (int, float)):
        cfg["nudge_after_minutes"] = 10
        issues.append(Issue("CFG-006", "config.nudge_after_minutes reset to 10",
                            "Number expected.", True))
    _require(cfg, "significant_attrs", list(DEFAULT_SIGNIFICANT_ATTRS), issues,
             "CFG-007", "config")
    if cfg["canvas_updates"] not in ("per-round", "pulled"):
        cfg["canvas_updates"] = "per-round"
        issues.append(Issue("CFG-008", "config.canvas_updates reset to per-round",
                            "Allowed: per-round | pulled.", True))
    if cfg["narration_altitude"] not in ("clusters", "exhaustive", "headline"):
        cfg["narration_altitude"] = "clusters"
        issues.append(Issue("CFG-009", "config.narration_altitude reset to clusters",
                            "Allowed: clusters | exhaustive | headline.", True))
    return cfg, issues


def validate_registry(reg):
    issues = []
    if not isinstance(reg, dict):
        return json.loads(json.dumps(DEFAULT_REGISTRY)), [Issue(
            "REG-000", "model.json is not an object — replaced with defaults",
            "Previous content was invalid JSON structure.", True)]
    _require(reg, "migrations", ["0001-baseline"], issues, "REG-001", "registry")
    if not isinstance(reg.get("revn"), int):
        reg["revn"] = 0
        issues.append(Issue("REG-002", "registry.revn reset to 0",
                            "Integer expected; history may need re-anchoring.", True))
    _require(reg, "head", "main", issues, "REG-003", "registry")
    _require(reg, "branches", [{"name": "main", "head": reg.get("revn", 0),
                                "archived": False}], issues, "REG-004", "registry")
    if not isinstance(reg.get("round"), int):
        reg["round"] = 0
        issues.append(Issue("REG-005", "registry.round reset to 0", "", True))
    if reg.get("whose_move") not in ("user", "agent"):
        reg["whose_move"] = "agent"
        issues.append(Issue("REG-006", "registry.whose_move reset to agent",
                            "Allowed: user | agent.", True))
    for key in ("concepts", "mappings", "declined", "pins", "tripwires"):
        _require(reg, key, [], issues, "REG-007", "registry")
    names = set()
    for b in reg["branches"]:
        if not isinstance(b, dict) or "name" not in b:
            reg["branches"] = [x for x in reg["branches"]
                               if isinstance(x, dict) and "name" in x]
            issues.append(Issue("REG-008", "malformed branch entry dropped", "", True))
            break
        names.add(b["name"])
    if reg["head"] not in names and reg["branches"]:
        reg["head"] = reg["branches"][0]["name"]
        issues.append(Issue("REG-009", "registry.head pointed at a missing branch — "
                            "reset to %r" % reg["head"], "", True))
    return reg, issues


def validate_scene(doc, artifact_id):
    """Validate + repair a .excalidraw document. Returns (doc, issues)."""
    issues = []
    if not isinstance(doc, dict):
        return None, [Issue("ART-000", "%s: not a JSON object" % artifact_id,
                            "The artifact file is unreadable; it will be ignored "
                            "until repaired by hand or overwritten.", False)]
    doc.setdefault("type", "excalidraw")
    doc.setdefault("version", 2)
    doc.setdefault("source", SOURCE_NAME)
    doc.setdefault("appState", {"viewBackgroundColor": PAPER_GROUND})
    doc.setdefault("files", {})
    w = doc.setdefault("wysiwyg", {})
    w.setdefault("artifact", artifact_id)
    w.setdefault("name", artifact_id.replace("-", " ").title())
    w.setdefault("artifact_type", "flow")
    w.setdefault("migrations", ["0001-baseline"])
    els = doc.get("elements")
    if not isinstance(els, list):
        doc["elements"] = []
        issues.append(Issue("ART-001", "%s: elements was not a list — reset"
                            % artifact_id, "Drawing content was lost; if that is "
                            "surprising, restore from saves/ or git.", True))
        return doc, issues
    seen = set()
    kept = []
    for el in els:
        if not isinstance(el, dict) or not el.get("id") or not el.get("type"):
            issues.append(Issue("ART-002", "%s: malformed element dropped"
                                % artifact_id, "", True))
            continue
        if el["id"] in seen:
            issues.append(Issue("ART-003", "%s: duplicate element id %r dropped"
                                % (artifact_id, el["id"]), "", True))
            continue
        seen.add(el["id"])
        kept.append(el)
    # dangling references
    for el in kept:
        if el.get("containerId") and el["containerId"] not in seen:
            el["containerId"] = None
            issues.append(Issue("ART-004", "%s: %s pointed at a missing container — "
                                "detached" % (artifact_id, el["id"]), "", True))
        for battr in ("startBinding", "endBinding"):
            b = el.get(battr)
            if isinstance(b, dict) and b.get("elementId") not in seen:
                el[battr] = None
                issues.append(Issue("ART-005", "%s: %s had a dangling %s — cleared"
                                    % (artifact_id, el["id"], battr), "", True))
        if el.get("boundElements"):
            el["boundElements"] = [b for b in el["boundElements"]
                                   if isinstance(b, dict) and b.get("id") in seen]
    # text-in-text repair: a bound label whose container is ITSELF a text
    # element is illegal Excalidraw structure — the client re-wraps the
    # label to the container's ~10px width and renders a giant
    # one-character-wide tower (v0.1 live finding, ESG session). Merge
    # the label's content into the container and drop the label.
    by_id = {e["id"]: e for e in kept}
    t_in_t = set()
    for el in kept:
        cont = by_id.get(el.get("containerId") or "")
        if el.get("type") == "text" and cont is not None and \
                cont.get("type") == "text":
            content = el.get("originalText") or el.get("text") or ""
            if content and not (cont.get("text") or "").strip():
                cont["text"] = content
                cont["originalText"] = content
                cont["width"], cont["height"] = text_dims(
                    content, cont.get("fontSize", 16))
            cont["boundElements"] = [
                b for b in (cont.get("boundElements") or [])
                if b.get("id") != el["id"]]
            t_in_t.add(el["id"])
            issues.append(Issue(
                "ART-010", "%s: %s was a bound label inside TEXT element "
                "%s — merged into it" % (artifact_id, el["id"], cont["id"]),
                "Text elements cannot contain bound labels (the client "
                "renders them one character wide). Use `text` on text "
                "elements; `label` is for shapes, arrows, and frames.",
                True))
    if t_in_t:
        kept = [e for e in kept if e["id"] not in t_in_t]
        by_id = {e["id"]: e for e in kept}

    # a bound label lying entirely outside its container is detached — reglue
    for el in kept:
        cid = el.get("containerId")
        if not cid or el.get("type") != "text":
            continue
        c = by_id.get(cid)
        if c is None or c.get("type") in ("arrow", "line"):
            continue
        if (el.get("x", 0) + el.get("width", 0) < c.get("x", 0) or
                el.get("x", 0) > c.get("x", 0) + c.get("width", 0) or
                el.get("y", 0) + el.get("height", 0) < c.get("y", 0) or
                el.get("y", 0) > c.get("y", 0) + c.get("height", 0)):
            recenter_label(kept, c)
            issues.append(Issue("ART-007", "%s: label %s was detached from its "
                                "container — re-centered"
                                % (artifact_id, el["id"]), "", True))
    # a bound label wider than its container clips at the container's
    # bounds — refit (write-time rule applied retroactively; idempotent
    # because fit_label_in leaves width <= inner)
    for el in kept:
        cid = el.get("containerId")
        cont = by_id.get(cid) if cid else None
        if cont is None or el.get("type") != "text" or \
                cont.get("type") not in ("rectangle", "diamond", "ellipse",
                                         "frame"):
            continue
        if el.get("width", 0) > max(60, cont.get("width", 160) - 24):
            fit_label_in(cont, el)
            el["x"] = cont["x"] + max(
                (cont.get("width", 0) - el.get("width", 0)) / 2, 4)
            el["y"] = cont["y"] + max(
                (cont.get("height", 0) - el.get("height", 0)) / 2, 4)
            issues.append(Issue(
                "ART-011", "%s: label %s was wider than its container — "
                "refit to wrap inside it" % (artifact_id, el["id"]),
                "", True))
    doc["elements"] = kept
    return doc, issues


# ---------------------------------------------------------------------------
# migrations — named sets on every owned JSON kind; snapshot before migrating
# ---------------------------------------------------------------------------

# Each entry: (name, fn(doc) -> doc). Files record applied names in
# doc["migrations"] (artifacts: doc["wysiwyg"]["migrations"]).
MIGRATIONS = {
    "config": [("0001-baseline", lambda d: d)],
    "registry": [("0001-baseline", lambda d: d)],
    "save": [("0001-baseline", lambda d: d)],
    "artifact": [("0001-baseline", lambda d: d)],
}


def migration_list(doc, kind):
    if kind == "artifact":
        return doc.setdefault("wysiwyg", {}).setdefault("migrations", [])
    return doc.setdefault("migrations", [])


def apply_migrations(doc, kind, path, project, log):
    """Apply pending named migrations in order, snapshotting first."""
    applied = migration_list(doc, kind)
    pending = [(n, fn) for (n, fn) in MIGRATIONS.get(kind, []) if n not in applied]
    if not pending:
        return doc, False
    if path and Path(path).exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = project.backups_dir / stamp / Path(path).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(path).read_bytes())
        log("migration snapshot: %s -> %s" % (path, dest))
    for name, fn in pending:
        doc = fn(doc)
        migration_list(doc, kind).append(name)
        log("migration applied: %s %s %s" % (kind, name, path))
    return doc, True


# ---------------------------------------------------------------------------
# normalizer — every write funnels through here (spec §4.1)
# ---------------------------------------------------------------------------

def _round_geom(v):
    if isinstance(v, (int, float)):
        return int(round(v))
    return v


def normalize_element(el):
    """Normalize one element in place: pin volatile attrs, round geometry."""
    el = dict(el)
    for attr in VOLATILE_ATTRS:
        el.pop(attr, None)
    el["version"] = 1
    el["versionNonce"] = 0
    el["seed"] = det_seed(el.get("id", ""))
    el["updated"] = 1
    if el.get("type") == "text":
        # originalText is derived (unwrapped source) — pin it to text so
        # replayed state and disk state always agree
        el["originalText"] = el.get("text", "")
    for attr in ("x", "y", "width", "height"):
        if attr in el:
            el[attr] = _round_geom(el.get(attr) or 0)
    if isinstance(el.get("angle"), (int, float)):
        el["angle"] = round(el["angle"], 3)
    if isinstance(el.get("points"), list):
        el["points"] = [[_round_geom(p[0]), _round_geom(p[1])]
                        for p in el["points"] if isinstance(p, (list, tuple))]
    return el


def rebuild_bound_elements(els):
    """boundElements is derived bookkeeping — recompute it from containerId
    and arrow bindings so it never needs diffing and always reconstructs."""
    ix = {e["id"]: e for e in els}
    for e in els:
        e["boundElements"] = []
    for e in els:
        if e.get("type") == "text" and e.get("containerId") in ix:
            ix[e["containerId"]]["boundElements"].append(
                {"id": e["id"], "type": "text"})
        if e.get("type") in ("arrow", "line"):
            for battr in ("startBinding", "endBinding"):
                b = e.get(battr)
                if isinstance(b, dict) and b.get("elementId") in ix:
                    target = ix[b["elementId"]]
                    entry = {"id": e["id"], "type": "arrow"}
                    if entry not in target["boundElements"]:
                        target["boundElements"].append(entry)
    return els


def normalize_scene_doc(doc):
    doc = dict(doc)
    doc["type"] = "excalidraw"
    doc["version"] = 2
    doc["source"] = SOURCE_NAME
    doc["elements"] = rebuild_bound_elements(
        [normalize_element(e) for e in doc.get("elements", [])
         if not e.get("isDeleted")])
    app = doc.get("appState") or {}
    doc["appState"] = {"viewBackgroundColor": app.get("viewBackgroundColor",
                                                      PAPER_GROUND),
                       "gridSize": app.get("gridSize", 20)}
    doc.setdefault("files", {})
    return doc


# ---------------------------------------------------------------------------
# make_element — the single construction funnel (spec §6.2)
# ---------------------------------------------------------------------------

ELEMENT_TYPES = {"rectangle", "ellipse", "diamond", "arrow", "line", "text",
                 "frame", "freedraw"}

BASE_DEFAULTS = {
    "fillStyle": "solid",
    "strokeWidth": 1,
    "strokeStyle": "solid",
    "roughness": 1,
    "opacity": 100,
    "angle": 0,
    "strokeColor": "#1e1e1e",
    "backgroundColor": "transparent",
    "groupIds": [],
    "frameId": None,
    "roundness": None,
    "boundElements": [],
    "link": None,
    "locked": False,
    "isDeleted": False,
}


def mint_id(label, kind, existing):
    """Semantic slug id (`login-form`), never a nanoid; stable + unique."""
    base = slugify(label or "", fallback=kind or "el")
    cand = base
    n = 2
    while cand in existing:
        cand = "%s-%d" % (base, n)
        n += 1
    existing.add(cand)
    return cand


def _display_width(line):
    """Character cells, not codepoints — CJK and fullwidth forms occupy
    two. Counting them as one under-measures a label by ~half, which is
    how bound text ends up overflowing its container."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in line)


def text_dims(text, font_size):
    lines = (text or "").split("\n")
    width = max((_display_width(l) for l in lines), default=1) \
        * font_size * 0.6
    height = max(len(lines), 1) * font_size * 1.25
    return (max(int(width), 10), max(int(height), int(font_size * 1.25)))


def wrap_label_text(text, inner, fs):
    """Greedy word-wrap to an inner pixel width (text_dims estimate)."""
    words = (text or "").split()
    if not words:
        return text or ""
    lines, cur = [], words[0]
    for word in words[1:]:
        cand = cur + " " + word
        if text_dims(cand, fs)[0] <= inner:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return "\n".join(lines)


def fit_label_in(container, lbl):
    """A bound label wider than its container gets clipped at the
    container's bounds by the client (live finding: 'Labels: sparse,
    months late' spilling out both sides of its box). The client wraps
    bound text to the width we allot — so keep `text` UNWRAPPED (wrapped
    text would poison originalText, facts, and replay), size the label
    box to the wrapped line count, and grow the container's height.
    Shapes and frames only; arrow labels ride midpoints and are covered
    by the clear-run lint instead."""
    if container.get("type") not in ("rectangle", "diamond", "ellipse",
                                     "frame"):
        return
    fs = lbl.get("fontSize", 16)
    text = lbl.get("originalText") or lbl.get("text") or ""
    inner = max(60, container.get("width", 160) - 24)
    if text_dims(text, fs)[0] <= inner:
        return
    wrapped = wrap_label_text(text, inner, fs)
    lbl["width"] = min(text_dims(text, fs)[0], inner)
    lbl["height"] = text_dims(wrapped, fs)[1]
    lbl["autoResize"] = False  # keep the client wrapping inside this box
    if container.get("height", 0) < lbl["height"] + 16:
        container["height"] = lbl["height"] + 16


# strategic classification (references/domain.md) renders as muted fill
# emphasis — visible grouping without breaking the low-fi ceiling
STRATEGIC_FILLS = {"core": "#ffec99", "supporting": "#d0ebff",
                   "generic": "#e9ecef"}


def apply_strategic(el, value, errors, index_hint, explicit_bg=False):
    """Fold a strategic classification into customData and render it as
    fill emphasis (unless the op set backgroundColor itself)."""
    if value not in STRATEGIC_FILLS:
        errors.append("op %d: strategic must be one of %s, got %r"
                      % (index_hint, "/".join(sorted(STRATEGIC_FILLS)),
                         value))
        return
    cd = dict(el.get("customData") or {})
    cd["strategic"] = value
    el["customData"] = cd
    if not explicit_bg:
        el["backgroundColor"] = STRATEGIC_FILLS[value]
        el["fillStyle"] = "solid"


def make_element(spec, existing_ids, errors, index_hint=0):
    """Build a full Excalidraw element (and possibly a bound label element)
    from a terse spec. Returns a list of elements ([shape] or [shape, label]).
    Appends human/LLM-addressable strings to `errors` on invalid input."""
    etype = spec.get("type")
    if etype not in ELEMENT_TYPES:
        errors.append("op %d: unknown element type %r (allowed: %s)"
                      % (index_hint, etype, ", ".join(sorted(ELEMENT_TYPES))))
        return []
    label = spec.get("label")
    if etype == "text" and label:
        # a text element cannot contain a bound label — Excalidraw wraps
        # the label to the container's width and renders a giant
        # one-character-wide tower. Unambiguous intent folds; ambiguity
        # rejects.
        if spec.get("text"):
            errors.append(
                "op %d: text elements take `text`, not `label` (got both) "
                "— a text element cannot contain a bound label"
                % index_hint)
            return []
        spec = dict(spec)
        spec["text"] = label
        label = None
    el_id = spec.get("id") or mint_id(label or spec.get("text") or "",
                                      etype, existing_ids)
    if spec.get("id"):
        if el_id in existing_ids:
            errors.append("op %d: id %r already exists in this artifact"
                          % (index_hint, el_id))
            return []
        existing_ids.add(el_id)
    el = dict(BASE_DEFAULTS)
    el.update({
        "id": el_id,
        "type": etype,
        "x": spec.get("x", 0),
        "y": spec.get("y", 0),
        "width": spec.get("width", 160 if etype != "text" else 0),
        "height": spec.get("height", 60 if etype != "text" else 0),
    })
    custom = dict(spec.get("customData") or {})
    if spec.get("role"):
        custom["role"] = spec["role"]
    if spec.get("kind"):
        custom["kind"] = spec["kind"]
    if spec.get("intent"):
        custom["intent"] = spec["intent"]
    custom.setdefault("role", "annotation" if etype == "text" and not
                      spec.get("containerId") and spec.get("role") is None and
                      custom.get("role") is None else custom.get("role", "node"))
    if custom.get("role") is None:
        custom["role"] = "node"
    el["customData"] = custom
    for attr in ("strokeColor", "backgroundColor", "fillStyle", "strokeWidth",
                 "strokeStyle", "roughness", "opacity", "angle", "groupIds",
                 "frameId", "roundness"):
        if attr in spec:
            el[attr] = spec[attr]
    if spec.get("strategic") is not None:
        apply_strategic(el, spec["strategic"], errors, index_hint,
                        explicit_bg="backgroundColor" in spec)
    if etype == "text":
        fs = spec.get("fontSize", 16)
        el["text"] = spec.get("text", "")
        el["originalText"] = el["text"]
        el["fontSize"] = fs
        el["fontFamily"] = spec.get("fontFamily", FONT_LEGIBLE)
        el["textAlign"] = spec.get("textAlign", "left")
        el["verticalAlign"] = spec.get("verticalAlign", "top")
        el["lineHeight"] = 1.25
        el["containerId"] = spec.get("containerId")
        el["autoResize"] = True
        if not spec.get("width"):
            el["width"], el["height"] = text_dims(el["text"], fs)
    if etype == "frame":
        el["name"] = label or spec.get("name") or el_id
        label = None  # frames carry their name natively, not a bound label
    if etype in ("arrow", "line"):
        el["points"] = spec.get("points", [[0, 0], [el["width"] or 100, 0]])
        el["lastCommittedPoint"] = None
        el["startBinding"] = None
        el["endBinding"] = None
        el["startArrowhead"] = spec.get("startArrowhead")
        el["endArrowhead"] = spec.get("endArrowhead",
                                      "arrow" if etype == "arrow" else None)
        el["elbowed"] = False
    out = [el]
    if label:
        lbl_id = el_id + "-label"
        base = lbl_id
        n = 2
        while lbl_id in existing_ids:
            lbl_id = "%s-%d" % (base, n)
            n += 1
        existing_ids.add(lbl_id)
        fs = spec.get("fontSize", 16)
        lw, lh = text_dims(label, fs)
        label_color = "#1e1e1e"
        bg = el.get("backgroundColor") or ""
        # 3-, 6- and 8-digit hex: an agent batch passing "#000" or a
        # picker emitting alpha must not fall through to dark-on-dark
        m = re.match(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$", bg)
        if m:
            hx = m.group(1)
            if len(hx) == 3:
                hx = "".join(ch * 2 for ch in hx)
            r, g, b = (int(hx[i:i + 2], 16) for i in (0, 2, 4))
            if 0.299 * r + 0.587 * g + 0.114 * b < 110:  # dark fill
                label_color = "#ffffff"
        lbl = dict(BASE_DEFAULTS)
        lbl.update({
            "id": lbl_id, "type": "text",
            "x": el["x"] + max((el["width"] - lw) / 2, 4),
            "y": el["y"] + max((el["height"] - lh) / 2, 4),
            "width": lw, "height": lh,
            "text": label, "originalText": label,
            "fontSize": fs, "fontFamily": spec.get("fontFamily", FONT_LEGIBLE),
            "textAlign": "center", "verticalAlign": "middle",
            "lineHeight": 1.25, "containerId": el_id, "autoResize": True,
            "customData": {"role": "label"},
        })
        lbl["strokeColor"] = label_color
        fit_label_in(el, lbl)
        lbl["x"] = el["x"] + max((el["width"] - lbl["width"]) / 2, 4)
        lbl["y"] = el["y"] + max((el["height"] - lbl["height"]) / 2, 4)
        el["boundElements"] = list(el.get("boundElements") or [])
        el["boundElements"].append({"id": lbl_id, "type": "text"})
        out.append(lbl)
    return out


def edge_anchor(el, other_cx, other_cy):
    """Point on el's bounding box edge nearest to the other element's center."""
    cx = el["x"] + el.get("width", 0) / 2.0
    cy = el["y"] + el.get("height", 0) / 2.0
    dx, dy = other_cx - cx, other_cy - cy
    if dx == 0 and dy == 0:
        return (cx, cy)
    hw = max(el.get("width", 0) / 2.0, 1)
    hh = max(el.get("height", 0) / 2.0, 1)
    scale = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return (cx + dx * scale, cy + dy * scale)


def _route_sig(arrow):
    """Signature of an arrow's routed geometry. Stored in
    customData.routed; a mismatch means the USER reshaped the path since
    the server routed it — that geometry is theirs, never auto-touched.
    Values are FORMATTED, not dumped: int/float representation drift
    across the normalize/persist round-trip (150 vs 150.0) must not
    change the signature."""
    vals = [arrow.get("x", 0), arrow.get("y", 0)]
    for p in (arrow.get("points") or []):
        vals.extend(p[:2])
    basis = "|".join("%.1f" % float(v) for v in vals)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def server_owns_geometry(arrow):
    cd = arrow.get("customData") or {}
    mark = cd.get("routed")
    if mark is True:  # pre-signature mark (early v0.1): trust 2-point only
        return len(arrow.get("points") or []) <= 2
    if isinstance(mark, str):
        return mark == _route_sig(arrow)
    # unmarked (v0 scenes): straight 2-point bound arrows were only ever
    # server-routed; anything bent is user geometry
    return len(arrow.get("points") or []) <= 2


def _snap_geom(arrow):
    """Round routed geometry to whole pixels BEFORE stamping. At-rest
    normalization rounds to 1px; a signature computed on fractional
    coordinates (fan offsets L*k/(N+1), odd-width centers) dies on the
    first persist round-trip and the server disowns its own arrow
    (v0.1 acceptance finding: 'user-shaped' warnings on agent arrows,
    reroute-on-move silently off)."""
    arrow["x"] = _round_geom(arrow.get("x", 0))
    arrow["y"] = _round_geom(arrow.get("y", 0))
    arrow["points"] = [[_round_geom(p[0]), _round_geom(p[1])]
                       for p in (arrow.get("points") or [])]
    if arrow["points"]:
        arrow["width"] = max(abs(p[0]) for p in arrow["points"])
        arrow["height"] = max(abs(p[1]) for p in arrow["points"])


def _stamp_route(arrow):
    _snap_geom(arrow)
    cd = dict(arrow.get("customData") or {})
    cd["routed"] = _route_sig(arrow)
    arrow["customData"] = cd


def route_arrow(arrow, src, dst):
    """Compute explicit geometry for a bound arrow (bindings do NOT route —
    feel-prototype finding) and attach start/end bindings. Off-axis pairs
    get a two-segment orthogonal elbow (diagram-design §6.1 — a diagonal
    between off-axis nodes is an automatic fail); aligned pairs stay
    straight."""
    sx1, sy1 = src["x"], src["y"]
    sx2, sy2 = sx1 + src.get("width", 0), sy1 + src.get("height", 0)
    dx1, dy1 = dst["x"], dst["y"]
    dx2, dy2 = dx1 + dst.get("width", 0), dy1 + dst.get("height", 0)
    scx, scy = (sx1 + sx2) / 2.0, (sy1 + sy2) / 2.0
    dcx, dcy = (dx1 + dx2) / 2.0, (dy1 + dy2) / 2.0
    x_overlap = min(sx2, dx2) - max(sx1, dx1)
    y_overlap = min(sy2, dy2) - max(sy1, dy1)
    if x_overlap > 12 or y_overlap > 12:
        # roughly aligned on one axis: a straight line is honest
        x1, y1 = edge_anchor(src, dcx, dcy)
        x2, y2 = edge_anchor(dst, scx, scy)
        pts = [[0, 0], [x2 - x1, y2 - y1]]
        arrow["roundness"] = None
    else:
        # off-axis: L-elbow. Ports follow travel: dominant-horizontal
        # exits a side port and enters top/bottom; dominant-vertical the
        # reverse (references/layout.md port rule).
        tdx, tdy = dcx - scx, dcy - scy
        if abs(tdx) >= abs(tdy):
            x1 = sx2 if tdx > 0 else sx1
            y1 = scy
            x2 = dcx
            y2 = dy1 if tdy > 0 else dy2
        else:
            x1 = scx
            y1 = sy2 if tdy > 0 else sy1
            x2 = dx1 if tdx > 0 else dx2
            y2 = dcy
        pts = [[0, 0], [x2 - x1, 0] if abs(tdx) >= abs(tdy)
               else [0, y2 - y1], [x2 - x1, y2 - y1]]
        arrow["roundness"] = {"type": 2}  # rounded bend
    gap = 6
    arrow["x"], arrow["y"] = x1, y1
    arrow["width"] = max(abs(p[0]) for p in pts)
    arrow["height"] = max(abs(p[1]) for p in pts)
    arrow["points"] = pts
    arrow["startBinding"] = {"elementId": src["id"], "focus": 0, "gap": gap}
    arrow["endBinding"] = {"elementId": dst["id"], "focus": 0, "gap": gap}
    _stamp_route(arrow)
    for node in (src, dst):
        bl = [b for b in (node.get("boundElements") or [])
              if not (b.get("id") == arrow["id"] and b.get("type") == "arrow")]
        bl.append({"id": arrow["id"], "type": "arrow"})
        node["boundElements"] = bl


def _edge_side(el, px, py, eps=2.5):
    """Which bbox edge of el the point sits on: left/right/top/bottom."""
    if abs(px - el["x"]) <= eps:
        return "left"
    if abs(px - (el["x"] + el.get("width", 0))) <= eps:
        return "right"
    if abs(py - el["y"]) <= eps:
        return "top"
    if abs(py - (el["y"] + el.get("height", 0))) <= eps:
        return "bottom"
    return None


def fan_attach_points(els):
    """Spread server-routed arrows sharing one node edge along it at
    L*k/(N+1) (diagram-design §6.4 — see references/layout.md): N arrows
    converging on a single point read as one arrow. Only touches arrows
    route_arrow marked `routed` — user geometry is never respaced."""
    ix = {e["id"]: e for e in els}
    ends = {}  # arrow id -> {"start": (x,y), "end": (x,y)}
    per_side = {}  # (node_id, side) -> [(arrow_id, which_end)]
    for a in els:
        if a.get("type") not in ("arrow", "line") or \
                not server_owns_geometry(a) or \
                len(a.get("points") or []) not in (2, 3):
            continue
        sx, sy = a["x"], a["y"]
        exx = a["x"] + a["points"][-1][0]
        exy = a["y"] + a["points"][-1][1]
        ends[a["id"]] = {"start": (sx, sy), "end": (exx, exy)}
        for which, (px, py), key in (("start", (sx, sy), "startBinding"),
                                     ("end", (exx, exy), "endBinding")):
            node = ix.get((a.get(key) or {}).get("elementId"))
            if node is None:
                continue
            side = _edge_side(node, px, py)
            if side:
                per_side.setdefault((node["id"], side), []) \
                    .append((a["id"], which))
    for (nid, side), members in per_side.items():
        if len(members) < 2:
            continue
        node = ix[nid]
        horiz = side in ("top", "bottom")
        length = node.get("width", 0) if horiz else node.get("height", 0)
        # order by the far end's cross-coordinate so fanned arrows don't cross
        def far_coord(m):
            aid, which = m
            fx, fy = ends[aid]["end" if which == "start" else "start"]
            return fx if horiz else fy
        members = sorted(members, key=far_coord)
        n = len(members)
        for k, (aid, which) in enumerate(members, start=1):
            off = length * k / (n + 1)
            if side == "top":
                pt = (node["x"] + off, node["y"])
            elif side == "bottom":
                pt = (node["x"] + off, node["y"] + node.get("height", 0))
            elif side == "left":
                pt = (node["x"], node["y"] + off)
            else:
                pt = (node["x"] + node.get("width", 0), node["y"] + off)
            ends[aid][which] = pt
    for aid, e2 in ends.items():
        a = ix[aid]
        (sx, sy), (exx, exy) = e2["start"], e2["end"]
        old = a.get("points") or []
        a["x"], a["y"] = sx, sy
        if len(old) == 3:
            # preserve the elbow's orthogonality: the corner keeps sharing
            # its constant coordinate with whichever segment held it
            first_horizontal = abs(old[1][1] - old[0][1]) < 0.5
            corner = (exx, sy) if first_horizontal else (sx, exy)
            pts = [[0, 0], [corner[0] - sx, corner[1] - sy],
                   [exx - sx, exy - sy]]
        else:
            pts = [[0, 0], [exx - sx, exy - sy]]
        a["points"] = pts
        a["width"] = max(abs(p[0]) for p in pts)
        a["height"] = max(abs(p[1]) for p in pts)
        _stamp_route(a)
        recenter_label(els, a)


# ---------------------------------------------------------------------------
# op engine — one grammar, both directions (spec §6.2): the op vocabulary
# mirrors the save-record change vocabulary. validate-all-then-apply.
# ---------------------------------------------------------------------------

OP_KINDS = {"add", "mod", "del", "reorder", "pin", "resolve_pin", "registry"}


def recenter_label(els, el):
    """Keep a bound label glued to its container after geometry changes:
    centered in shapes, at the midpoint of arrows."""
    label = next((t for t in els if t.get("type") == "text"
                  and t.get("containerId") == el["id"]), None)
    if label is None:
        return
    if el.get("type") in ("arrow", "line"):
        pts = el.get("points") or [[0, 0]]
        # anchor on the LONGEST segment's midpoint (matters for elbows),
        # offset 8px perpendicular off the stroke, leaning up — a label
        # sitting ON its arrow hides both (diagram-design §6.2)
        best, mx, my, dx, dy = -1.0, el["x"], el["y"], 1.0, 0.0
        for i in range(1, len(pts)):
            sdx = pts[i][0] - pts[i - 1][0]
            sdy = pts[i][1] - pts[i - 1][1]
            ln = (sdx * sdx + sdy * sdy) ** 0.5
            if ln > best:
                best = ln
                dx, dy = sdx, sdy
                mx = el["x"] + (pts[i][0] + pts[i - 1][0]) / 2
                my = el["y"] + (pts[i][1] + pts[i - 1][1]) / 2
        run = (dx * dx + dy * dy) ** 0.5 or 1.0
        px, py = -dy / run, dx / run
        if py > 0:
            px, py = -px, -py  # prefer above the stroke
        lift = label.get("height", 16) / 2 + 8
        label["x"] = mx + px * lift - label.get("width", 0) / 2
        label["y"] = my + py * lift - label.get("height", 0) / 2
    else:
        label["x"] = el["x"] + max((el.get("width", 0) -
                                    label.get("width", 0)) / 2, 4)
        label["y"] = el["y"] + max((el.get("height", 0) -
                                    label.get("height", 0)) / 2, 4)


def apply_ops(elements, ops, errors, pin_registry=None):
    """Apply a validated op batch to an element list. Returns new list.
    All validation errors are collected (LLM-addressed); caller rejects the
    whole batch if any. Pure function: does not mutate input."""
    els = [dict(e) for e in elements]
    index = {e["id"]: e for e in els}
    existing = set(index.keys())

    def resolve(eid, opi, verb):
        el = index.get(eid)
        if el is None:
            errors.append("op %d (%s): no element with id %r in this artifact "
                          "(check the artifact's current elements via "
                          "`canvas.py status` or the state API)" % (opi, verb, eid))
        return el

    for i, op in enumerate(ops):
        kind = op.get("op")
        if kind not in OP_KINDS:
            errors.append("op %d: unknown op %r (allowed: %s)"
                          % (i, kind, ", ".join(sorted(OP_KINDS))))
            continue
        if kind in ("registry",):
            continue  # handled by the caller against the registry
        if kind == "add":
            spec = op.get("element") or {k: v for k, v in op.items() if k != "op"}
            made = make_element(spec, existing, errors, i)
            for m in made:
                els.append(m)
                index[m["id"]] = m
            if made and made[0]["type"] in ("arrow", "line"):
                arrow = made[0]
                src_id, dst_id = op.get("from") or spec.get("from"), \
                    op.get("to") or spec.get("to")
                if src_id or dst_id:
                    src = resolve(src_id, i, "arrow from") if src_id else None
                    dst = resolve(dst_id, i, "arrow to") if dst_id else None
                    if src is not None and dst is not None:
                        route_arrow(arrow, src, dst)
                        recenter_label(els, arrow)
        elif kind == "mod":
            el = resolve(op.get("id"), i, "mod")
            if el is None:
                continue
            attrs = op.get("attrs") or {}
            if not isinstance(attrs, dict) or not attrs:
                errors.append("op %d (mod %s): attrs must be a non-empty object "
                              "of {attribute: newValue}" % (i, op.get("id")))
                continue
            for attr, value in attrs.items():
                if attr == "label":
                    _set_label(els, index, existing, el, value)
                elif attr in ("from", "to"):
                    continue  # rewires are processed jointly after this loop
                elif attr == "text" and el.get("type") == "text":
                    el["text"] = value
                    el["originalText"] = value
                    el["width"], el["height"] = text_dims(value,
                                                          el.get("fontSize", 16))
                elif attr == "customData":
                    cd = dict(el.get("customData") or {})
                    cd.update(value or {})
                    el["customData"] = cd
                elif attr == "strategic":
                    apply_strategic(el, value, errors, i,
                                    explicit_bg="backgroundColor" in attrs)
                else:
                    el[attr] = value
                    if attr in ("x", "y", "width", "height"):
                        recenter_label(els, el)
            # rewires, processed jointly so one mod may set from AND to.
            # A rewire that cannot bind is a hard validation error — the
            # v0 behavior (silently accept, route nothing) burned rounds:
            # the agent read "saved without changing anything" and re-issued
            # the same rewire for eleven rounds (refinement audit F2).
            rewire = {k: attrs[k] for k in ("from", "to") if k in attrs}
            if rewire:
                if el.get("type") not in ("arrow", "line"):
                    errors.append("op %d: 'from'/'to' only apply to arrows"
                                  % i)
                else:
                    src = index.get((el.get("startBinding") or {})
                                    .get("elementId"))
                    dst = index.get((el.get("endBinding") or {})
                                    .get("elementId"))
                    for battr, key in (("from", "startBinding"),
                                       ("to", "endBinding")):
                        if battr not in rewire:
                            continue
                        endpoint = resolve(rewire[battr], i, "rewire")
                        if endpoint is None:
                            continue  # resolve() already recorded the error
                        old = src if battr == "from" else dst
                        if old is not None and old is not endpoint:
                            old["boundElements"] = [
                                b for b in (old.get("boundElements") or [])
                                if b.get("id") != el["id"]]
                        if battr == "from":
                            src = endpoint
                        else:
                            dst = endpoint
                    if src is not None and dst is not None:
                        route_arrow(el, src, dst)
                        recenter_label(els, el)
                    else:
                        missing = "start (`from`)" if src is None \
                            else "end (`to`)"
                        errors.append(
                            "op %d (mod %s %s): cannot rewire — the arrow's "
                            "%s endpoint is unbound; set both `from` and "
                            "`to` in one mod, or delete and re-add the "
                            "arrow with from/to"
                            % (i, op.get("id"),
                               ", ".join("%s=%s" % kv
                                         for kv in sorted(rewire.items())),
                               missing))
        elif kind == "del":
            el = resolve(op.get("id"), i, "del")
            if el is None:
                continue
            doomed = {el["id"]}
            for b in (el.get("boundElements") or []):
                if b.get("type") == "text":
                    doomed.add(b.get("id"))
            for other in els:
                if other["id"] in doomed:
                    continue
                if other.get("containerId") in doomed and other["type"] == "text":
                    doomed.add(other["id"])
            els = [e for e in els if e["id"] not in doomed]
            for e in els:
                for battr in ("startBinding", "endBinding"):
                    b = e.get(battr)
                    if isinstance(b, dict) and b.get("elementId") in doomed:
                        e[battr] = None
                if e.get("boundElements"):
                    e["boundElements"] = [b for b in e["boundElements"]
                                          if b.get("id") not in doomed]
                if e.get("frameId") in doomed:
                    e["frameId"] = None
            index = {e["id"]: e for e in els}
            existing = set(index.keys())
        elif kind == "reorder":
            el = resolve(op.get("id"), i, "reorder")
            if el is None:
                continue
            pos = op.get("index")
            if not isinstance(pos, int):
                errors.append("op %d (reorder %s): integer `index` required"
                              % (i, op.get("id")))
                continue
            els = [e for e in els if e["id"] != el["id"]]
            pos = max(0, min(pos, len(els)))
            els.insert(pos, el)
        elif kind == "pin":
            q = op.get("question")
            target = op.get("target")
            if not q:
                errors.append("op %d (pin): `question` text required" % i)
                continue
            detail = op.get("detail")
            examples = op.get("examples")
            if detail is not None and not isinstance(detail, str):
                errors.append("op %d (pin): detail must be a string" % i)
                continue
            if examples is not None and (
                    not isinstance(examples, list) or
                    any(not isinstance(x, str) for x in examples)):
                errors.append("op %d (pin): examples must be a list of "
                              "strings" % i)
                continue
            anchor = index.get(target) if target else None
            pid = op.get("id") or mint_id("pin-" + (target or q[:20]), "pin",
                                          existing)
            px = (anchor["x"] + anchor.get("width", 0) + 8) if anchor else 40
            py = (anchor["y"] - 8) if anchor else 40
            pin_el = dict(BASE_DEFAULTS)
            pin_el.update({
                "id": pid, "type": "text", "x": px, "y": py,
                "width": 26, "height": 26, "text": "❓",
                "originalText": "❓", "fontSize": 20,
                "fontFamily": FONT_LEGIBLE, "textAlign": "center",
                "verticalAlign": "top", "lineHeight": 1.25,
                "containerId": None, "autoResize": True,
                "strokeColor": "#b45309",
                "customData": {"role": "pin", "question": q,
                               "target": target, "status": "open",
                               "answer": None},
            })
            els.append(pin_el)
            index[pid] = pin_el
            if pin_registry is not None:
                pin_registry.append({"id": pid, "question": q,
                                     "target": target, "detail": detail,
                                     "examples": examples or []})
        elif kind == "resolve_pin":
            # tolerant of a missing element: the registry write-through in
            # apply_batch is the durable half; a pin whose ❓ was already
            # deleted must still be resolvable (never strand state)
            el = index.get(op.get("id"))
            if el is not None:
                cd = dict(el.get("customData") or {})
                cd["status"] = "resolved"
                el["customData"] = cd

    # F1 post-pass: re-route every bound arrow whose endpoint node moved or
    # resized in this batch. Without this, the agent's own layout tidying
    # strands its arrows mid-air and it cannot see it (refinement audit F1:
    # nine of seventeen demo revisions were spent re-issuing rewires this
    # bug kept undoing). Only server-routed geometry (2-point paths — the
    # only kind route_arrow emits) is rewritten: a user-shaped multi-point
    # path is never silently flattened; the detached-endpoint lint flags it
    # for a deliberate, narrated repair instead.
    moved = set()
    for op in ops:
        if op.get("op") == "mod" and \
                {"x", "y", "width", "height"} & set(op.get("attrs") or {}):
            moved.add(op.get("id"))
    if moved:
        index = {e["id"]: e for e in els}
        for e in els:
            if e.get("type") not in ("arrow", "line"):
                continue
            s = (e.get("startBinding") or {}).get("elementId")
            d = (e.get("endBinding") or {}).get("elementId")
            if not ((s in moved or d in moved)
                    and s in index and d in index):
                continue
            # a geometry-signature mismatch means the USER reshaped the
            # path since the server routed it — never flatten their
            # geometry; the detached-endpoint lint flags it for a
            # deliberate, narrated repair instead
            if server_owns_geometry(e):
                route_arrow(e, index[s], index[d])
                recenter_label(els, e)
    if any(op.get("op") in ("add", "mod", "del") for op in ops):
        fan_attach_points(els)
    return els


# ---------------------------------------------------------------------------
# differ — diff → mechanical summary → semantic facts (spec §5.1)
# ---------------------------------------------------------------------------

def _is_sentinel(el):
    for attr in ("x", "y"):
        v = el.get(attr)
        if isinstance(v, (int, float)) and abs(v) >= SENTINEL_COORD:
            return True
    for p in el.get("points") or []:
        if abs(p[0]) >= SENTINEL_COORD or abs(p[1]) >= SENTINEL_COORD:
            return True
    return False


def label_map(els):
    """container id -> bound label text. Prefers originalText: `text`
    may carry wrap-inserted newlines (fit_label_in) that would leak into
    facts and narration ('Cart→Guest or\\naccount?')."""
    out = {}
    for e in els:
        if e.get("type") == "text" and e.get("containerId"):
            out[e["containerId"]] = e.get("originalText") or e.get("text", "")
    return out


def display_label(el, labels):
    if el.get("type") == "frame":
        return el.get("name") or el["id"]
    if el.get("type") == "text":
        t = (el.get("text") or "").strip()
        return t[:40] if t else el["id"]
    return labels.get(el["id"]) or el["id"]


def diff_scenes(old_els, new_els, significant_attrs=None):
    """Bucketed diff (add/del/mod/move/reorder) with pre-built inverses.
    Returns {changes, inverse, suppressed, sentinel_suppressed}."""
    sig = significant_attrs or DEFAULT_SIGNIFICANT_ATTRS
    old_ix = {e["id"]: (i, e) for i, e in enumerate(old_els)}
    new_ix = {e["id"]: (i, e) for i, e in enumerate(new_els)}
    changes = []
    sentinel_suppressed = 0

    for i, el in enumerate(new_els):
        if el["id"] not in old_ix:
            changes.append({"op": "add", "element": el, "index": i})
    for i, el in enumerate(old_els):
        if el["id"] not in new_ix:
            changes.append({"op": "del", "element": el, "index": i})

    for eid in new_ix:
        if eid not in old_ix:
            continue
        old = old_ix[eid][1]
        new = new_ix[eid][1]
        if _is_sentinel(new) or _is_sentinel(old):
            # Excalidraw's mid-rebind park position: pure noise. Binding
            # changes still surface (REWIRED is the signal).
            sentinel_suppressed += 1
            geo_ok = False
        else:
            geo_ok = True
        attrs = []
        moved = None
        binding_changed = any(
            _norm_binding(old.get(b)) != _norm_binding(new.get(b))
            for b in ("startBinding", "endBinding"))
        for attr in sig:
            ov, nv = old.get(attr), new.get(attr)
            if attr in ("startBinding", "endBinding"):
                ov, nv = _norm_binding(ov), _norm_binding(nv)
            if ov == nv:
                continue
            derived_geo = _geometry_derived(new) or _geometry_derived(old)
            if attr in ("x", "y", "width", "height", "angle", "points"):
                if not geo_ok:
                    continue
                if attr in ("x", "y") and not derived_geo:
                    continue  # gathered below as one move op
                if attr == "points" and binding_changed:
                    continue  # rewire re-route noise
                entry = {"attr": attr, "from": ov, "to": nv}
                if derived_geo:
                    # kept for lossless replay, invisible to narration
                    entry["derived"] = True
                attrs.append(entry)
                continue
            entry = {"attr": attr, "from": ov, "to": nv}
            if attr == "text" and isinstance(ov, str) and isinstance(nv, str) \
                    and " ".join(ov.split()) == " ".join(nv.split()):
                # soft re-wrap (whitespace-only change) — not a rename
                entry["derived"] = True
            attrs.append(entry)
        if geo_ok and not binding_changed and \
                not _geometry_derived(new) and not _geometry_derived(old) and \
                (old.get("x") != new.get("x") or
                 old.get("y") != new.get("y")):
            moved = {"op": "move", "id": eid,
                     "from": [old.get("x", 0), old.get("y", 0)],
                     "to": [new.get("x", 0), new.get("y", 0)]}
        if attrs:
            changes.append({"op": "mod", "id": eid, "attrs": attrs})
        if moved:
            changes.append(moved)

    # reorder detection among surviving common ids. Recorded as ONE
    # whole-order op: per-element index ops don't replay deterministically
    # (each move shifts the others' positions), and a lossy replay poisons
    # state reconstruction with phantom reconciliations.
    common_old = [e["id"] for e in old_els if e["id"] in new_ix]
    common_new = [e["id"] for e in new_els if e["id"] in old_ix]
    if common_old != common_new:
        old_rank = {eid: i for i, eid in enumerate(common_old)}
        moved = [eid for i, eid in enumerate(common_new)
                 if old_rank.get(eid) != i]
        changes.append({"op": "reorder",
                        "order": [e["id"] for e in new_els],
                        "from_order": [e["id"] for e in old_els],
                        "moved": moved})

    inverse = []
    for ch in reversed(changes):
        if ch["op"] == "add":
            inverse.append({"op": "del", "element": ch["element"],
                            "index": ch["index"]})
        elif ch["op"] == "del":
            inverse.append({"op": "add", "element": ch["element"],
                            "index": ch["index"]})
        elif ch["op"] == "mod":
            inverse.append({"op": "mod", "id": ch["id"],
                            "attrs": [{"attr": a["attr"], "from": a["to"],
                                       "to": a["from"]} for a in ch["attrs"]]})
        elif ch["op"] == "move":
            inverse.append({"op": "move", "id": ch["id"],
                            "from": ch["to"], "to": ch["from"]})
        elif ch["op"] == "reorder":
            if "order" in ch:
                inverse.append({"op": "reorder", "order": ch["from_order"],
                                "from_order": ch["order"],
                                "moved": ch.get("moved", [])})
            else:
                inverse.append({"op": "reorder", "id": ch["id"],
                                "from_index": ch["to_index"],
                                "to_index": ch["from_index"]})
    return {"changes": changes, "inverse": inverse,
            "sentinel_suppressed": sentinel_suppressed}


def _norm_binding(b):
    if not isinstance(b, dict):
        return None
    return b.get("elementId")


def _geometry_derived(el):
    """Elements whose geometry is derived from an anchor, not user intent:
    bound labels (browser re-measures text), pins, and bound arrows (routing
    follows the endpoints). Their coordinate churn never narrates."""
    role = (el.get("customData") or {}).get("role")
    if role in ("label", "pin"):
        return True
    if el.get("type") == "text" and el.get("containerId"):
        return True
    if el.get("type") in ("arrow", "line") and (
            _norm_binding(el.get("startBinding")) or
            _norm_binding(el.get("endBinding"))):
        return True
    return False


def replay_changes(elements, changes):
    """Replay forward change ops (the save-record grammar) onto an element
    list — used for state reconstruction and inverse-replay revert."""
    els = [dict(e) for e in elements]
    for ch in changes:
        op = ch["op"]
        if op == "add":
            idx = min(ch.get("index", len(els)), len(els))
            els.insert(idx, dict(ch["element"]))
        elif op == "del":
            els = [e for e in els if e["id"] != ch["element"]["id"]]
        elif op == "mod":
            for e in els:
                if e["id"] == ch["id"]:
                    for a in ch["attrs"]:
                        val = a["to"]
                        # save records store bindings NORMALIZED to id
                        # strings; writing that back verbatim corrupts the
                        # scene (Excalidraw needs {elementId, focus, gap}).
                        # Inverse-replay is the revert path — a revert
                        # across any rewire hit this.
                        if a["attr"] in ("startBinding", "endBinding") \
                                and isinstance(val, str):
                            val = {"elementId": val, "focus": 0, "gap": 6}
                        e[a["attr"]] = val
                        if a["attr"] == "text" and e.get("type") == "text":
                            e["originalText"] = a["to"]
        elif op == "move":
            for e in els:
                if e["id"] == ch["id"]:
                    e["x"], e["y"] = ch["to"]
        elif op == "reorder":
            if "order" in ch:
                rank = {eid: i for i, eid in enumerate(ch["order"])}
                els.sort(key=lambda e: rank.get(e["id"], len(rank)))
            else:  # legacy per-element form (older save records)
                moving = [e for e in els if e["id"] == ch["id"]]
                if moving:
                    els = [e for e in els if e["id"] != ch["id"]]
                    pos = max(0, min(ch["to_index"], len(els)))
                    els.insert(pos, moving[0])
    return els


# ---------------------------------------------------------------------------
# consequence suppression + spatial language
# ---------------------------------------------------------------------------

def mark_consequences(diff):
    """One structural insertion producing N layout moves collapses to the
    insertion + 'layout adjusted' (spec §5.1)."""
    changes = diff["changes"]
    structural = [c for c in changes if c["op"] in ("add", "del")
                  and (c["element"].get("customData") or {}).get("role")
                  not in ("label", "pin")]
    if not structural:
        return {}
    modded = {c["id"] for c in changes if c["op"] == "mod"}
    moves = [c for c in changes if c["op"] == "move" and c["id"] not in modded]
    if len(moves) < 2:
        return {}
    # dominant shared direction ⇒ layout shift caused by the insertion
    def sgn(v):
        return 0 if abs(v) < 2 else (1 if v > 0 else -1)
    dirs = {}
    for m in moves:
        d = (sgn(m["to"][0] - m["from"][0]), sgn(m["to"][1] - m["from"][1]))
        dirs.setdefault(d, []).append(m)
    dominant = max(dirs.values(), key=len)
    if len(dominant) < 2:
        return {}
    cause = structural[0]["element"]["id"]
    return {m["id"]: cause for m in dominant}


def spatial_phrase(dx, dy):
    parts = []
    if abs(dx) >= 8:
        parts.append("right" if dx > 0 else "left")
    if abs(dy) >= 8:
        parts.append("down" if dy > 0 else "up")
    if not parts:
        return "nudged"
    return "moved " + "-".join(parts)


# ---------------------------------------------------------------------------
# semantic facts — universal + per-type tables (spec §5.3)
# ---------------------------------------------------------------------------

def role_of(el):
    return (el.get("customData") or {}).get("role") or "node"


def kind_of(el):
    return (el.get("customData") or {}).get("kind") or el.get("type")


def semantic_facts(old_els, new_els, diff, artifact_type, tier, consequences):
    """Derive typed facts from a bucketed diff. Extended-tier artifacts get
    universal facts only."""
    facts = []
    old_ix = {e["id"]: e for e in old_els}
    new_ix = {e["id"]: e for e in new_els}
    old_labels = label_map(old_els)
    new_labels = label_map(new_els)
    changes = diff["changes"]

    adds = [c["element"] for c in changes if c["op"] == "add"]
    dels = [c["element"] for c in changes if c["op"] == "del"]
    added_ids = {e["id"] for e in adds}
    deleted_ids = {e["id"] for e in dels}

    def F(_fact, _element, **kw):
        d = {"fact": _fact, "element": _element}
        d.update(kw)
        facts.append(d)

    # ---- type change detection: same id direct, or del+add same label ----
    for c in changes:
        if c["op"] == "mod":
            for a in c["attrs"]:
                if a["attr"] == "type":
                    F("type_changed", c["id"], **{"from": a["from"],
                                                  "to": a["to"]})
    matched_pairs = []
    for d in dels:
        dl = display_label(d, old_labels)
        for a in adds:
            if a["id"] in {p[1] for p in matched_pairs}:
                continue
            al = display_label(a, new_labels)
            near = (abs(a.get("x", 0) - d.get("x", 0)) < 80 and
                    abs(a.get("y", 0) - d.get("y", 0)) < 80)
            if dl and dl == al and a.get("type") != d.get("type") and near \
                    and role_of(a) not in ("label", "pin"):
                F("type_changed", a["id"], **{"from": d.get("type"),
                                              "to": a.get("type"),
                                              "label": al})
                matched_pairs.append((d["id"], a["id"]))
    converted = {p[0] for p in matched_pairs} | {p[1] for p in matched_pairs}

    # ---- label lifecycle (bound text mapped to its container) ----
    for c in changes:
        if c["op"] == "add" and c["element"].get("type") == "text" \
                and c["element"].get("containerId"):
            cid = c["element"]["containerId"]
            if cid in old_ix and cid not in old_labels:
                F("label_added", cid, text=c["element"].get("text", ""))
        if c["op"] == "mod" and c["id"] in new_ix \
                and new_ix[c["id"]].get("type") == "text":
            for a in c["attrs"]:
                if a["attr"] != "text" or a.get("derived"):
                    continue
                tel = new_ix[c["id"]]
                target = tel.get("containerId") or c["id"]
                if role_of(new_ix.get(target, tel)) == "pin":
                    continue
                F("renamed", target, **{"from": a["from"], "to": a["to"]})

    # ---- adds / deletes (skip bound labels + pins; those speak elsewhere) --
    for el in adds:
        r = role_of(el)
        if el["id"] in converted or r == "label" or \
                (el.get("type") == "text" and el.get("containerId")):
            continue
        if r == "pin":
            F("pin_added", el["id"],
              question=(el.get("customData") or {}).get("question", ""))
            continue
        if r == "annotation":
            F("annotated", el["id"], text=el.get("text", ""),
              target=_nearest_target(el, new_els))
            continue
        F("added", el["id"], kind=kind_of(el),
          label=display_label(el, new_labels))
    for el in dels:
        r = role_of(el)
        if el["id"] in converted or r == "label" or \
                (el.get("type") == "text" and el.get("containerId")):
            continue
        if r == "pin":
            F("pin_deleted", el["id"])
            continue
        if r == "annotation":
            F("annotation_deleted", el["id"], text=el.get("text", ""))
            continue
        F("deleted", el["id"],
          was={"kind": kind_of(el), "label": display_label(el, old_labels)})

    # ---- moves / resize / restyle / reorder ----
    for c in changes:
        if c["op"] == "move":
            el = new_ix.get(c["id"])
            if el is None or role_of(el) == "label":
                continue
            dx = c["to"][0] - c["from"][0]
            dy = c["to"][1] - c["from"][1]
            if c["id"] in consequences:
                F("moved", c["id"], dx=dx, dy=dy, spatial="layout adjusted",
                  consequence_of=consequences[c["id"]])
            else:
                F("moved", c["id"], dx=dx, dy=dy,
                  spatial=spatial_phrase(dx, dy),
                  label=display_label(el, new_labels))
        elif c["op"] == "mod":
            el = new_ix.get(c["id"])
            if el is None:
                continue
            names = {a["attr"] for a in c["attrs"] if not a.get("derived")}
            if names & {"width", "height"}:
                F("resized", c["id"], label=display_label(el, new_labels))
            styled = names & STYLE_ATTRS
            if styled and el.get("type") != "text":
                F("restyled", c["id"], attrs=sorted(styled), low_signal=True)
            if "frameId" in names:
                pass  # regrouped — handled by the wireframe table
            if "customData" in names and role_of(el) == "annotation":
                F("annotated", c["id"], text=el.get("text", ""),
                  target=_nearest_target(el, new_els))
        elif c["op"] == "reorder":
            for eid in (c.get("moved") or
                        ([c["id"]] if c.get("id") else [])):
                el = new_ix.get(eid)
                if el is not None and role_of(el) not in ("label", "pin"):
                    F("reordered", eid, label=display_label(el, new_labels))

    if not changes:
        F("saved_no_changes", None)

    if tier != "first-class":
        return facts

    if artifact_type == "flow":
        facts.extend(_flow_facts(old_ix, new_ix, old_labels, new_labels,
                                 changes, adds, dels, added_ids, deleted_ids,
                                 new_els))
    elif artifact_type == "wireframe":
        facts.extend(_wireframe_facts(old_ix, new_ix, old_labels, new_labels,
                                      changes, adds, dels))
    elif artifact_type == "domain":
        facts.extend(_domain_facts(old_ix, new_ix, old_labels, new_labels,
                                   changes, adds, dels, new_els))
    elif artifact_type == "sequence":
        facts.extend(_sequence_facts(old_ix, new_ix, old_labels, new_labels,
                                     changes, adds, dels))
    return facts


def _nearest_target(el, els):
    best, best_d = None, 1e18
    ex = el.get("x", 0) + el.get("width", 0) / 2
    ey = el.get("y", 0) + el.get("height", 0) / 2
    for other in els:
        if other["id"] == el["id"] or role_of(other) in ("annotation", "pin",
                                                         "label"):
            continue
        ox = other.get("x", 0) + other.get("width", 0) / 2
        oy = other.get("y", 0) + other.get("height", 0) / 2
        d = (ox - ex) ** 2 + (oy - ey) ** 2
        if d < best_d:
            best, best_d = other["id"], d
    return best if best_d < 400 ** 2 else None


def _arrow_ends(arrow, ix, labels):
    s = _norm_binding(arrow.get("startBinding"))
    e = _norm_binding(arrow.get("endBinding"))
    sl = display_label(ix[s], labels) if s in ix else (s or "?")
    el_ = display_label(ix[e], labels) if e in ix else (e or "?")
    return s, e, sl, el_


def _flow_facts(old_ix, new_ix, old_labels, new_labels, changes, adds, dels,
                added_ids, deleted_ids, new_els):
    facts = []

    def F(_fact, _element, **kw):
        d = {"fact": _fact, "element": _element}
        d.update(kw)
        facts.append(d)

    for el in adds:
        if el.get("type") in ("arrow",):
            s, e, sl, el_ = _arrow_ends(el, new_ix, new_labels)
            if s or e:
                F("transition_added", el["id"], between=[sl, el_])
        elif role_of(el) == "node":
            F("step_added", el["id"], label=display_label(el, new_labels))
            if el.get("type") == "diamond":
                F("branch_added", el["id"],
                  label=display_label(el, new_labels))
    for el in dels:
        if el.get("type") == "arrow":
            s, e, sl, el_ = _arrow_ends(el, old_ix, old_labels)
            if s or e:
                F("transition_deleted", el["id"], between=[sl, el_])
        elif role_of(el) == "node":
            F("step_deleted", el["id"], label=display_label(el, old_labels))

    rewires = []
    for c in changes:
        if c["op"] != "mod":
            continue
        el = new_ix.get(c["id"])
        if el is None or el.get("type") != "arrow":
            continue
        binding_mods = [a for a in c["attrs"]
                        if a["attr"] in ("startBinding", "endBinding")]
        if binding_mods:
            old_arrow = old_ix[c["id"]]
            os_, oe_ = _norm_binding(old_arrow.get("startBinding")), \
                _norm_binding(old_arrow.get("endBinding"))
            _, _, osl, oel = _arrow_ends(old_arrow, old_ix, old_labels)
            _, _, nsl, nel = _arrow_ends(el, new_ix, new_labels)
            # an endpoint vanishing because its node was deleted in this same
            # save is a consequence of the deletion, not a re-order decision
            lost_to_deletion = any(b in deleted_ids for b in (os_, oe_) if b)
            kw = {"from": "%s→%s" % (osl, oel), "to": "%s→%s" % (nsl, nel)}
            if lost_to_deletion:
                kw["consequence_of"] = (os_ if os_ in deleted_ids else oe_)
            F("rewired", c["id"], arrow=c["id"], **kw)
            if not lost_to_deletion:
                rewires.append(c["id"])
    if len(rewires) >= 2:
        F("sequence_reordered", None, arrows=rewires)

    # ---- lanes (ownership overlay): who owns which steps ----
    def lane_frame(el, ix):
        f = ix.get(el.get("frameId") or "")
        return f if f is not None and kind_of(f) == "lane" else None

    def lane_name(f):
        return (f or {}).get("name") or (f or {}).get("id")

    for el in adds:
        if el.get("type") == "frame" and kind_of(el) == "lane":
            F("lane_added", el["id"], owner=lane_name(el))
    for el in dels:
        if el.get("type") == "frame" and kind_of(el) == "lane":
            F("lane_deleted", el["id"], owner=lane_name(el))
    for c in changes:
        if c["op"] != "mod":
            continue
        el = new_ix.get(c["id"])
        old_el = old_ix.get(c["id"])
        if el is None or old_el is None or role_of(el) != "node":
            continue
        if any(a["attr"] == "frameId" for a in c["attrs"]):
            of, nf = lane_frame(old_el, old_ix), lane_frame(el, new_ix)
            if (of or nf) and of is not nf:
                F("ownership_changed", c["id"],
                  from_lane=lane_name(of), to_lane=lane_name(nf),
                  label=display_label(el, new_labels))
    for el in adds:
        if el.get("type") != "arrow":
            continue
        s = _norm_binding(el.get("startBinding"))
        e = _norm_binding(el.get("endBinding"))
        sf = lane_frame(new_ix.get(s, {}), new_ix) if s else None
        ef = lane_frame(new_ix.get(e, {}), new_ix) if e else None
        if sf is not None and ef is not None and sf is not ef:
            F("handoff_added", el["id"], from_lane=lane_name(sf),
              to_lane=lane_name(ef))

    # lint-style observation: a step with no incident transitions
    arrows = [e for e in new_els if e.get("type") == "arrow"]
    touched = set()
    for a in arrows:
        touched.add(_norm_binding(a.get("startBinding")))
        touched.add(_norm_binding(a.get("endBinding")))
    for el in new_els:
        if role_of(el) == "node" and el.get("type") in ("rectangle", "diamond",
                                                        "ellipse") \
                and el["id"] not in touched and el["id"] in added_ids:
            F("step_orphaned", el["id"], label=display_label(el, new_labels))
    return facts


PARTY_KINDS = ("actor", "system", "context")
_SYNC_NAMES = {"solid": "call", "dashed": "response", "dotted": "async"}


def _sequence_facts(old_ix, new_ix, old_labels, new_labels, changes,
                    adds, dels):
    facts = []

    def F(_fact, _element, **kw):
        d = {"fact": _fact, "element": _element}
        d.update(kw)
        facts.append(d)

    def is_party(el):
        return kind_of(el) in PARTY_KINDS

    for el in adds:
        if is_party(el):
            F("actor_added", el["id"], label=display_label(el, new_labels),
              party_kind=kind_of(el))
        elif el.get("type") == "arrow":
            s, e, sl, el_ = _arrow_ends(el, new_ix, new_labels)
            if s or e:
                F("message_added", el["id"], between=[sl, el_],
                  label=display_label(el, new_labels))
    for el in dels:
        if is_party(el):
            F("actor_deleted", el["id"],
              label=display_label(el, old_labels))
        elif el.get("type") == "arrow":
            s, e, sl, el_ = _arrow_ends(el, old_ix, old_labels)
            if s or e:
                F("message_deleted", el["id"], between=[sl, el_])

    moved_y = set()
    for c in changes:
        if c["op"] == "move" and c["from"][1] != c["to"][1]:
            moved_y.add(c["id"])
        if c["op"] != "mod":
            continue
        el = new_ix.get(c["id"])
        old_el = old_ix.get(c["id"])
        if el is None:
            continue
        names = {a["attr"] for a in c["attrs"]}
        if "y" in names:
            moved_y.add(c["id"])
        # crystallization: a party's kind flips (actor/system → context is
        # the boundary-forming event DMF mode narrates)
        if old_el is not None and is_party(el) and \
                kind_of(old_el) != kind_of(el) and \
                kind_of(old_el) in PARTY_KINDS:
            F("party_kind_changed", c["id"],
              **{"from": kind_of(old_el), "to": kind_of(el)},
              label=display_label(el, new_labels),
              crystallized=kind_of(el) == "context")
        if el.get("type") != "arrow":
            if "width" in names or "height" in names:
                if kind_of(el) == "activation":
                    F("activation_changed", c["id"])
            continue
        # actor_reassigned: a message endpoint moved to another lifeline
        if names & {"startBinding", "endBinding"} and old_el is not None:
            os_, oe_ = _norm_binding(old_el.get("startBinding")), \
                _norm_binding(old_el.get("endBinding"))
            ns_, ne_ = _norm_binding(el.get("startBinding")), \
                _norm_binding(el.get("endBinding"))
            gone = {d["id"] for d in dels}
            for which, o, n in (("from", os_, ns_), ("to", oe_, ne_)):
                if o != n and n and o not in gone:
                    F("actor_reassigned", c["id"], end=which,
                      from_actor=display_label(old_ix.get(o, {"id": o}),
                                               old_labels) if o else "?",
                      to_actor=display_label(new_ix.get(n, {"id": n}),
                                             new_labels),
                      message=display_label(el, new_labels))
        for a in c["attrs"]:
            if a["attr"] == "strokeStyle":
                F("sync_changed", c["id"],
                  **{"from": _SYNC_NAMES.get(a["from"] or "solid",
                                             a["from"]),
                     "to": _SYNC_NAMES.get(a["to"] or "solid", a["to"])},
                  message=display_label(el, new_labels))

    # message_reordered: vertical order among messages present in both
    # states changed — sequence's rewired; time IS the y axis here
    common = [i for i in old_ix
              if i in new_ix and new_ix[i].get("type") == "arrow"
              and old_ix[i].get("type") == "arrow"]
    if common:
        old_order = sorted(common, key=lambda i: old_ix[i].get("y", 0))
        new_order = sorted(common, key=lambda i: new_ix[i].get("y", 0))
        for i in common:
            op_, np_ = old_order.index(i), new_order.index(i)
            if op_ != np_ and i in moved_y:
                F("message_reordered", i,
                  message=display_label(new_ix[i], new_labels),
                  from_pos=op_ + 1, to_pos=np_ + 1)
    return facts


def _wireframe_facts(old_ix, new_ix, old_labels, new_labels, changes,
                     adds, dels):
    facts = []

    def F(_fact, _element, **kw):
        d = {"fact": _fact, "element": _element}
        d.update(kw)
        facts.append(d)

    for el in adds:
        if el.get("type") == "frame":
            F("screen_added", el["id"], name=el.get("name") or el["id"])
    for el in dels:
        if el.get("type") == "frame":
            F("screen_deleted", el["id"], name=el.get("name") or el["id"])

    for c in changes:
        if c["op"] == "mod":
            el = new_ix.get(c["id"])
            if el is None:
                continue
            for a in c["attrs"]:
                if a["attr"] == "frameId":
                    fr_from = old_ix.get(a["from"] or "")
                    fr_to = new_ix.get(a["to"] or "")
                    F("regrouped", c["id"],
                      from_screen=(fr_from or {}).get("name") or a["from"],
                      to_screen=(fr_to or {}).get("name") or a["to"],
                      label=display_label(el, new_labels))
                if a["attr"] == "text" and not a.get("derived") \
                        and el.get("containerId"):
                    container = new_ix.get(el["containerId"])
                    if container is not None and \
                            (container.get("customData") or {}).get("kind") in \
                            ("button", "nav", "link", "tab"):
                        F("label_renamed", container["id"],
                          **{"from": a["from"], "to": a["to"]})
        elif c["op"] == "move":
            el = new_ix.get(c["id"])
            if el is None or role_of(el) == "label":
                continue
            old_el = old_ix.get(c["id"], {})
            if el.get("frameId") and el.get("frameId") == old_el.get("frameId"):
                F("block_moved_within_screen", c["id"],
                  screen=(new_ix.get(el["frameId"]) or {}).get("name"),
                  label=display_label(el, new_labels))
            # fold_crossed (output mode): a block moving across the fold
            # line is an editorial decision about the first screenful —
            # never just a nudge (references/wireframe.md)
            fold = next((f for f in new_ix.values()
                         if kind_of(f) == "fold"
                         and f.get("frameId") == el.get("frameId")), None)
            old_fold = old_ix.get(fold["id"]) if fold else None
            if fold is not None and old_el:
                oy = c["from"][1] + old_el.get("height", 0) / 2
                ny = c["to"][1] + el.get("height", 0) / 2
                o_side = "above" if oy < (old_fold or fold).get("y", 0) \
                    else "below"
                n_side = "above" if ny < fold.get("y", 0) else "below"
                if o_side != n_side:
                    F("fold_crossed", c["id"],
                      **{"from": o_side, "to": n_side},
                      label=display_label(el, new_labels))

    # priority annotations: numbered notes whose number changed
    for c in changes:
        if c["op"] != "mod":
            continue
        el = new_ix.get(c["id"])
        if el is None or (el.get("customData") or {}).get("kind") != "priority":
            continue
        for a in c["attrs"]:
            if a["attr"] == "text":
                F("priority_changed", c["id"], **{"from": a["from"],
                                                  "to": a["to"]})
    return facts


# forgiving cardinality tokens — deliberately not a schema language
_CARD_RE = re.compile(
    r"\d+\.\.(?:\*|\d+)|\b0\.\.1\b|\b\d+\b|\bN\b|\bmany\b"
    r"|\bper-[\w-]+\b|\*", re.IGNORECASE)


def _domain_facts(old_ix, new_ix, old_labels, new_labels, changes, adds,
                  dels, new_els):
    facts = []

    def F(_fact, _element, **kw):
        d = {"fact": _fact, "element": _element}
        d.update(kw)
        facts.append(d)

    def is_entity(el):
        return role_of(el) == "node" and el.get("type") in ("rectangle",
                                                            "ellipse")

    for el in adds:
        if el.get("type") == "arrow":
            s, e, sl, el_ = _arrow_ends(el, new_ix, new_labels)
            if s or e:
                F("relationship_added", el["id"], between=[sl, el_])
        elif is_entity(el):
            lbl = display_label(el, new_labels)
            F("entity_added", el["id"], label=lbl)
            slug = slugify(lbl)
            for other in new_els:
                if other["id"] != el["id"] and is_entity(other):
                    oslug = slugify(display_label(other, new_labels))
                    if oslug and (oslug == slug or oslug == slug + "s"
                                  or slug == oslug + "s"):
                        F("possible_merge", el["id"],
                          with_element=other["id"],
                          labels=[lbl, display_label(other, new_labels)])
    for el in dels:
        if el.get("type") == "arrow":
            s, e, sl, el_ = _arrow_ends(el, old_ix, old_labels)
            if s or e:
                F("relationship_deleted", el["id"], between=[sl, el_])
        elif is_entity(el):
            F("entity_deleted", el["id"],
              label=display_label(el, old_labels))

    for c in changes:
        if c["op"] != "mod":
            continue
        el = new_ix.get(c["id"])
        if el is None:
            continue
        if el.get("type") == "arrow":
            binding_mods = [a for a in c["attrs"]
                            if a["attr"] in ("startBinding", "endBinding")]
            if binding_mods:
                old_arrow = old_ix[c["id"]]
                gone = {d["id"] for d in dels}
                os_, oe_ = _norm_binding(old_arrow.get("startBinding")), \
                    _norm_binding(old_arrow.get("endBinding"))
                _, _, osl, oel = _arrow_ends(old_arrow, old_ix, old_labels)
                _, _, nsl, nel = _arrow_ends(el, new_ix, new_labels)
                kw = {"from": "%s→%s" % (osl, oel),
                      "to": "%s→%s" % (nsl, nel)}
                if any(b in gone for b in (os_, oe_) if b):
                    kw["consequence_of"] = (os_ if os_ in gone else oe_)
                F("relationship_rewired", c["id"], **kw)
        if el.get("type") == "text" and el.get("containerId"):
            container = new_ix.get(el["containerId"])
            for a in c["attrs"]:
                if a["attr"] != "text" or a.get("derived") or container is None:
                    continue
                if container.get("type") == "arrow":
                    F("relationship_relabeled", container["id"],
                      **{"from": a["from"], "to": a["to"]})
                    # cardinality tokens are load-bearing: "holds 1" →
                    # "holds 1..*" is often the largest structural
                    # consequence available from a single label edit
                    o_toks = _CARD_RE.findall(a["from"] or "")
                    n_toks = _CARD_RE.findall(a["to"] or "")
                    if o_toks != n_toks and (o_toks or n_toks):
                        F("cardinality_changed", container["id"],
                          **{"from": o_toks or ["(none)"],
                             "to": n_toks or ["(none)"]})
                elif is_entity(container):
                    F("entity_renamed", container["id"],
                      **{"from": a["from"], "to": a["to"]},
                      glossary_challenge=True)
    return facts


# ---------------------------------------------------------------------------
# mechanical summary (verb counts, salience headline, suppression)
# ---------------------------------------------------------------------------

SALIENCE = ["rewired", "relationship_rewired", "actor_reassigned",
            "message_reordered", "cardinality_changed", "ownership_changed",
            "party_kind_changed", "fold_crossed", "type_changed",
            "screen_added", "screen_deleted", "entity_renamed", "renamed",
            "label_renamed", "branch_added", "step_added", "entity_added",
            "actor_added", "lane_added", "handoff_added", "added",
            "step_deleted", "entity_deleted", "actor_deleted",
            "lane_deleted", "deleted", "regrouped", "sync_changed",
            "label_added", "transition_added", "transition_deleted",
            "relationship_added", "relationship_deleted", "message_added",
            "message_deleted", "annotated", "pin_added", "priority_changed",
            "activation_changed", "moved", "resized", "reordered",
            "restyled", "saved_no_changes"]


def headline_for(fact):
    n = fact["fact"]
    if n in ("rewired", "relationship_rewired"):
        return "rewired %s to %s" % (fact.get("from"), fact.get("to"))
    if n == "type_changed":
        return "%s changed %s → %s" % (fact.get("label") or fact["element"],
                                       fact.get("from"), fact.get("to"))
    if n in ("renamed", "entity_renamed", "label_renamed"):
        return "renamed %r → %r" % (fact.get("from"), fact.get("to"))
    if n.endswith("_added") and n != "label_added":
        return "added %s" % (fact.get("label") or fact.get("name")
                             or (fact.get("between") and
                                 "→".join(fact["between"])) or fact["element"])
    if n == "label_added":
        return "labeled %s %r" % (fact["element"], fact.get("text"))
    if n.endswith("_deleted") or n == "deleted":
        was = fact.get("was") or {}
        return "deleted %s" % (was.get("label") or fact.get("label")
                               or fact.get("name") or fact["element"])
    if n == "regrouped":
        return "%s moved to %s" % (fact.get("label") or fact["element"],
                                   fact.get("to_screen"))
    if n == "annotated":
        return "annotated: %r" % (fact.get("text") or "")[:60]
    if n == "moved":
        return "%s %s" % (fact.get("label") or fact["element"],
                          fact.get("spatial"))
    if n == "saved_no_changes":
        return "saved without changing anything"
    if n == "actor_reassigned":
        return "%s now goes to %s (was %s)" % (
            fact.get("message") or fact["element"],
            fact.get("to_actor"), fact.get("from_actor"))
    if n == "message_reordered":
        return "%s moved to position %s (was %s)" % (
            fact.get("message") or fact["element"],
            fact.get("to_pos"), fact.get("from_pos"))
    if n == "cardinality_changed":
        return "cardinality %s → %s" % (
            "/".join(fact.get("from") or []), "/".join(fact.get("to") or []))
    if n == "ownership_changed":
        return "%s now owned by %s (was %s)" % (
            fact.get("label") or fact["element"],
            fact.get("to_lane"), fact.get("from_lane"))
    if n == "party_kind_changed":
        return "%s crystallized into a bounded context" % (
            fact.get("label") or fact["element"]) \
            if fact.get("crystallized") else \
            "%s is now a %s (was %s)" % (fact.get("label") or
                                         fact["element"], fact.get("to"),
                                         fact.get("from"))
    if n == "fold_crossed":
        return "%s moved %s the fold" % (fact.get("label") or
                                         fact["element"], fact.get("to"))
    if n == "sync_changed":
        return "%s became %s (was %s)" % (fact.get("message") or
                                          fact["element"], fact.get("to"),
                                          fact.get("from"))
    return n.replace("_", " ")


def mechanical_summary(facts, sentinel_suppressed):
    verb_counts = {}
    suppressed = sentinel_suppressed
    visible = []
    for f in facts:
        verb_counts[f["fact"]] = verb_counts.get(f["fact"], 0) + 1
        if f.get("consequence_of") or f.get("low_signal"):
            suppressed += 1
        else:
            visible.append(f)
    headline = "no changes"
    for name in SALIENCE:
        hit = next((f for f in visible if f["fact"] == name), None)
        if hit:
            headline = headline_for(hit)
            extra = len(visible) - 1
            if extra > 0:
                headline += " (+%d more)" % extra
            break
    return {"verb_counts": verb_counts, "headline": headline,
            "suppressed": suppressed}


# ---------------------------------------------------------------------------
# label mutation helper used by the op engine
# ---------------------------------------------------------------------------

def _set_label(els, index, existing, el, value):
    """Set, replace, or clear (value None/"") an element's bound label."""
    if el.get("type") == "text":
        # `mod label` on a text element means its text — never a bound
        # label (text-in-text renders one character wide; see ART-010)
        el["text"] = value or ""
        el["originalText"] = el["text"]
        el["width"], el["height"] = text_dims(el["text"],
                                              el.get("fontSize", 16))
        return
    label_el = None
    for e in els:
        if e.get("type") == "text" and e.get("containerId") == el["id"]:
            label_el = e
            break
    if value:
        if label_el is not None:
            label_el["text"] = value
            label_el["originalText"] = value
            label_el["width"], label_el["height"] = text_dims(
                value, label_el.get("fontSize", 16))
            fit_label_in(el, label_el)
            label_el["x"] = el["x"] + max(
                (el.get("width", 0) - label_el["width"]) / 2, 4)
            label_el["y"] = el["y"] + max(
                (el.get("height", 0) - label_el["height"]) / 2, 4)
        else:
            lbl_id = mint_id(el["id"] + "-label", "label", existing)
            fs = 16
            lw, lh = text_dims(value, fs)
            lbl = dict(BASE_DEFAULTS)
            lbl.update({
                "id": lbl_id, "type": "text",
                "x": el["x"] + max((el.get("width", 0) - lw) / 2, 4),
                "y": el["y"] + max((el.get("height", 0) - lh) / 2, 4),
                "width": lw, "height": lh, "text": value,
                "originalText": value, "fontSize": fs,
                "fontFamily": FONT_LEGIBLE, "textAlign": "center",
                "verticalAlign": "middle", "lineHeight": 1.25,
                "containerId": el["id"], "autoResize": True,
                "customData": {"role": "label"},
            })
            fit_label_in(el, lbl)
            lbl["x"] = el["x"] + max((el.get("width", 0) - lbl["width"]) / 2, 4)
            lbl["y"] = el["y"] + max((el.get("height", 0) - lbl["height"]) / 2, 4)
            els.append(lbl)
            index[lbl_id] = lbl
            el["boundElements"] = list(el.get("boundElements") or [])
            el["boundElements"].append({"id": lbl_id, "type": "text"})
    elif label_el is not None:
        els.remove(label_el)
        index.pop(label_el["id"], None)
        el["boundElements"] = [b for b in (el.get("boundElements") or [])
                               if b.get("id") != label_el["id"]]


# ---------------------------------------------------------------------------
# Store — registry, config, artifacts, save records, the commit DAG
# ---------------------------------------------------------------------------

def _svg_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _svg_dash(el):
    style = el.get("strokeStyle")
    if style == "dashed":
        return ' stroke-dasharray="8 4"'
    if style == "dotted":
        return ' stroke-dasharray="2 3"'
    return ""


def render_svg(els, title=""):
    """Deterministic stdlib SVG of an element array — the snapshot CLI's
    tier-3 fallback and the substrate tier 2 rasterizes. Geometry-faithful
    (drawn from the same coordinates the lint reads); text set in system
    fonts, so it is an approximation of Excalidraw's hand-drawn look, not
    a replica — good for legibility checks, never for style judgments."""
    live = [e for e in els if not e.get("isDeleted")]
    if not live:
        return ("<svg xmlns='http://www.w3.org/2000/svg' width='320' "
                "height='80'><text x='16' y='45' font-size='14'>"
                "(empty artifact)</text></svg>"), 320, 80
    xs, ys, x2s, y2s = [], [], [], []
    for e in live:
        if e.get("type") in ("arrow", "line"):
            for p in e.get("points") or [[0, 0]]:
                xs.append(e.get("x", 0) + p[0])
                ys.append(e.get("y", 0) + p[1])
                x2s.append(e.get("x", 0) + p[0])
                y2s.append(e.get("y", 0) + p[1])
        else:
            xs.append(e.get("x", 0))
            ys.append(e.get("y", 0))
            x2s.append(e.get("x", 0) + e.get("width", 0))
            y2s.append(e.get("y", 0) + e.get("height", 0))
    pad = 40
    minx, miny = min(xs) - pad, min(ys) - pad
    w = max(x2s) - min(xs) + 2 * pad
    h = max(y2s) - min(ys) + 2 * pad
    out = ["<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' "
           "viewBox='%f %f %f %f'>" % (min(w, 4000), min(h, 3000),
                                       minx, miny, w, h),
           "<rect x='%f' y='%f' width='%f' height='%f' fill='#fdfcf8'/>"
           % (minx, miny, w, h)]
    if title:
        out.append("<text x='%f' y='%f' font-size='13' fill='#999' "
                   "font-family='sans-serif'>%s</text>"
                   % (minx + 8, miny + 18, _svg_escape(title)))

    def paint(e):
        et = e.get("type")
        stroke = e.get("strokeColor") or "#1e1e1e"
        fill = e.get("backgroundColor") or "transparent"
        if fill == "transparent":
            fill = "none"
        sw = e.get("strokeWidth") or 2
        x, y = e.get("x", 0), e.get("y", 0)
        ew, eh = e.get("width", 0), e.get("height", 0)
        if et == "frame":
            out.append("<rect x='%f' y='%f' width='%f' height='%f' "
                       "fill='none' stroke='#94a3b8' stroke-width='1.5' "
                       "stroke-dasharray='8 4' rx='8'/>" % (x, y, ew, eh))
            out.append("<text x='%f' y='%f' font-size='12' fill='#64748b' "
                       "font-family='sans-serif'>%s</text>"
                       % (x + 4, y - 6, _svg_escape(e.get("name") or
                                                    e.get("id", ""))))
            return
        if et == "rectangle":
            out.append("<rect x='%f' y='%f' width='%f' height='%f' "
                       "fill='%s' stroke='%s' stroke-width='%s' rx='4'%s/>"
                       % (x, y, ew, eh, fill, stroke, sw, _svg_dash(e)))
        elif et == "ellipse":
            out.append("<ellipse cx='%f' cy='%f' rx='%f' ry='%f' fill='%s' "
                       "stroke='%s' stroke-width='%s'%s/>"
                       % (x + ew / 2, y + eh / 2, ew / 2, eh / 2, fill,
                          stroke, sw, _svg_dash(e)))
        elif et == "diamond":
            pts = "%f,%f %f,%f %f,%f %f,%f" % (
                x + ew / 2, y, x + ew, y + eh / 2,
                x + ew / 2, y + eh, x, y + eh / 2)
            out.append("<polygon points='%s' fill='%s' stroke='%s' "
                       "stroke-width='%s'%s/>" % (pts, fill, stroke, sw,
                                                  _svg_dash(e)))
        elif et in ("arrow", "line"):
            pts = e.get("points") or [[0, 0]]
            abs_pts = [(x + p[0], y + p[1]) for p in pts]
            path = " ".join("%f,%f" % p for p in abs_pts)
            out.append("<polyline points='%s' fill='none' stroke='%s' "
                       "stroke-width='%s'%s/>" % (path, stroke, sw,
                                                  _svg_dash(e)))
            if et == "arrow" and e.get("endArrowhead") and len(abs_pts) > 1:
                (x1, y1), (x2, y2) = abs_pts[-2], abs_pts[-1]
                dx, dy = x2 - x1, y2 - y1
                ln = (dx * dx + dy * dy) ** 0.5 or 1
                ux, uy = dx / ln, dy / ln
                px, py = -uy, ux
                out.append("<polygon points='%f,%f %f,%f %f,%f' fill='%s'/>"
                           % (x2, y2, x2 - 10 * ux + 4 * px,
                              y2 - 10 * uy + 4 * py,
                              x2 - 10 * ux - 4 * px,
                              y2 - 10 * uy - 4 * py, stroke))
        elif et == "text":
            fs = e.get("fontSize") or 16
            lines = str(e.get("text") or "").split("\n")
            anchor = "middle" if e.get("textAlign") == "center" else "start"
            tx = x + (ew / 2 if anchor == "middle" else 0)
            lh = fs * (e.get("lineHeight") or 1.25)
            for li, line in enumerate(lines):
                out.append("<text x='%f' y='%f' font-size='%s' fill='%s' "
                           "text-anchor='%s' font-family='sans-serif'>"
                           "%s</text>"
                           % (tx, y + fs * 0.85 + li * lh, fs, stroke,
                              anchor, _svg_escape(line)))

    for e in live:
        if e.get("type") == "frame":
            paint(e)
    for e in live:
        if e.get("type") in ("arrow", "line"):
            paint(e)
    for e in live:
        if e.get("type") not in ("frame", "arrow", "line", "text"):
            paint(e)
    for e in live:
        if e.get("type") == "text":
            paint(e)
    out.append("</svg>")
    return "\n".join(out), int(min(w, 4000)), int(min(h, 3000))


def validate_png(data, want_w=None, want_h=None, min_bpp=0.02):
    """Sanity-check a PNG: signature, IHDR dims, bytes-per-pixel floor.
    The demo's corrupted cold-start exports sat at ~0.03 bytes/px versus
    0.12+ for good renders of identical dimensions — but stripes compress
    unpredictably, so the floor is conservative and dimension mismatch is
    the stronger signal."""
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return False, "not a PNG"
    if data[12:16] != b"IHDR":
        return False, "malformed PNG (no IHDR)"
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    if not w or not h:
        return False, "zero-sized PNG"
    if want_w and abs(w - want_w) > max(64, want_w * 0.2):
        return False, "width %d far from requested %d" % (w, want_w)
    if want_h and abs(h - want_h) > max(64, want_h * 0.2):
        return False, "height %d far from requested %d" % (h, want_h)
    bpp = len(data) / float(w * h)
    if min_bpp and bpp < min_bpp:
        return False, "suspiciously thin (%.3f bytes/px)" % bpp
    return True, "%dx%d, %.3f bytes/px" % (w, h, bpp)


def find_browsers():
    """Chromium-family binaries for headless rasterization, best first —
    no playwright driver, no node; plain `--headless=new --screenshot`.
    Snap-confined builds (/snap/bin) go LAST: their mount namespace hides
    the real /tmp, so they only work with $HOME-based paths."""
    found, snaps = [], []
    for name in ("chromium", "chromium-browser", "google-chrome-stable",
                 "google-chrome", "chrome", "brave-browser", "msedge",
                 "microsoft-edge"):
        path = shutil.which(name)
        if not path:
            continue
        real = os.path.realpath(path)
        (snaps if ("/snap/" in path or "/snap/" in real) else
         found).append(path)
    # a playwright-managed chromium is a normal unconfined binary — using
    # it directly is not "pairing with playwright", just finding a browser
    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        for pat in ("chromium-*/chrome-linux*/chrome",
                    "chromium_headless_shell-*/chrome-linux*/"
                    "headless_shell"):
            for hit in sorted(cache.glob(pat), reverse=True):
                if os.access(str(hit), os.X_OK):
                    found.append(str(hit))
                    break
    return found + snaps


def intent_echo(ops, els):
    """One line per drawing op stating its OBSERVABLE consequence against
    the final scene — because `apply` accepting a batch is not the same as
    the batch having done what the agent meant (refinement audit §2.4).
    The agent reads the echo, not the success line."""
    ix = {e["id"]: e for e in els}
    labels = label_map(els)

    def describe(eid):
        el = ix.get(eid)
        if el is None:
            return "%s: gone" % eid
        if el.get("type") in ("arrow", "line"):
            s = (el.get("startBinding") or {}).get("elementId")
            d = (el.get("endBinding") or {}).get("elementId")
            return "arrow %s binds %s → %s" % (eid, s or "∅ (unbound)",
                                               d or "∅ (unbound)")
        lbl = labels.get(eid)
        return "%s%s at (%d,%d) %dx%d" % (
            eid, " (%r)" % lbl if lbl else "",
            el.get("x", 0), el.get("y", 0),
            el.get("width", 0), el.get("height", 0))

    lines = []
    for i, op in enumerate(ops):
        kind = op.get("op")
        if kind == "add":
            spec = op.get("element") or {}
            eid = spec.get("id") or slugify(spec.get("label", "") or "el")
            lines.append("op %d (add): %s" % (i, describe(eid)))
        elif kind == "mod":
            lines.append("op %d (mod %s): %s"
                         % (i, ",".join(sorted((op.get("attrs") or {}))),
                            describe(op.get("id"))))
        elif kind == "del":
            eid = op.get("id")
            lines.append("op %d (del): %s %s" % (
                i, eid, "deleted (with its bound label)"
                if eid not in ix else "STILL PRESENT"))
    return lines


FLOW_KINDS = {"source", "transform", "agent", "control", "sink"}


def _seg_hits_rect(x1, y1, x2, y2, el, inset=2):
    """Does segment (x1,y1)-(x2,y2) pass through el's (slightly shrunk)
    bounding box? Cohen–Sutherland-style reject, then edge intersection."""
    rx1, ry1 = el["x"] + inset, el["y"] + inset
    rx2 = el["x"] + el.get("width", 0) - inset
    ry2 = el["y"] + el.get("height", 0) - inset
    if rx2 <= rx1 or ry2 <= ry1:
        return False
    if max(x1, x2) < rx1 or min(x1, x2) > rx2 or \
            max(y1, y2) < ry1 or min(y1, y2) > ry2:
        return False
    # inside-the-box endpoints count as a hit; otherwise check each rect edge
    if rx1 <= x1 <= rx2 and ry1 <= y1 <= ry2:
        return True
    if rx1 <= x2 <= rx2 and ry1 <= y2 <= ry2:
        return True

    def ccw(ax, ay, bx, by, cx, cy):
        return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax)

    def crosses(ax, ay, bx, by, cx, cy, dx, dy):
        return ccw(ax, ay, cx, cy, dx, dy) != ccw(bx, by, cx, cy, dx, dy) \
            and ccw(ax, ay, bx, by, cx, cy) != ccw(ax, ay, bx, by, dx, dy)

    edges = [(rx1, ry1, rx2, ry1), (rx2, ry1, rx2, ry2),
             (rx2, ry2, rx1, ry2), (rx1, ry2, rx1, ry1)]
    return any(crosses(x1, y1, x2, y2, *e) for e in edges)


def lint_layout(els):
    """Layout lint for headless agents (who can't see their own drawing),
    tiered per references/layout.md:
      errors   — the drawing does not say what the agent meant; repair in
                 the same move, before narrating
      warnings — legibility defects worth a cosmetic repair
      notes    — style/budget observations
    Returns {"errors": [...], "warnings": [...], "notes": [...]}.
    (Sequence/lane/shell-specific checks land with the type promotion —
    task #7: time-reversal, activation-never-closes, lane-spanning,
    app-shell drift, cardinality-token mismatch.)"""
    errors, warnings, notes = [], [], []
    labels = label_map(els)
    ix = {e["id"]: e for e in els}

    def name(eid):
        lbl = labels.get(eid)
        return "%s (%r)" % (eid, lbl) if lbl else eid

    def bbox_pts(e):
        pts = e.get("points") or [[0, 0]]
        return (e.get("x", 0) + pts[0][0], e.get("y", 0) + pts[0][1],
                e.get("x", 0) + pts[-1][0], e.get("y", 0) + pts[-1][1])

    shapes = [e for e in els if e.get("type") in
              ("rectangle", "diamond", "ellipse")
              and role_of(e) not in ("label", "pin")]
    arrows = [e for e in els if e.get("type") in ("arrow", "line")
              and e.get("points")]
    nodes = [e for e in shapes if role_of(e) == "node" or
             (e.get("customData") or {}).get("kind")]

    # ---- ERROR: detached endpoints (server-routed) --------------------
    TOL = 14  # binding gap (6) + slack
    for a in arrows:
        x1, y1, x2, y2 = bbox_pts(a)
        for key, px, py in (("startBinding", x1, y1),
                            ("endBinding", x2, y2)):
            b = a.get(key)
            tgt = ix.get((b or {}).get("elementId"))
            if tgt is None:
                continue
            gx1, gy1 = tgt["x"] - TOL, tgt["y"] - TOL
            gx2 = tgt["x"] + tgt.get("width", 0) + TOL
            gy2 = tgt["y"] + tgt.get("height", 0) + TOL
            if not (gx1 <= px <= gx2 and gy1 <= py <= gy2):
                msg = ("arrow %s claims to bind %s but its %s point ends "
                       "%dpx away — re-route it (mod x/y on the node "
                       "re-routes 2-point arrows automatically)"
                       % (a["id"], name(tgt["id"]),
                          "start" if key == "startBinding" else "end",
                          int(((px - max(gx1, min(px, gx2))) ** 2 +
                               (py - max(gy1, min(py, gy2))) ** 2) ** 0.5)
                          or TOL))
                if not server_owns_geometry(a):
                    warnings.append(
                        "user-shaped " + msg +
                        " — not auto-routed (the path is the user's "
                        "geometry); re-route deliberately and narrate it")
                else:
                    errors.append(msg)

    # ---- ERROR: flow-kind structural invariants ----------------------
    kinds = {e["id"]: (e.get("customData") or {}).get("kind")
             for e in nodes}
    if any(k in FLOW_KINDS for k in kinds.values()):
        inbound = {eid: 0 for eid in kinds}
        outbound = {eid: 0 for eid in kinds}
        for a in arrows:
            s = (a.get("startBinding") or {}).get("elementId")
            d = (a.get("endBinding") or {}).get("elementId")
            if s in outbound:
                outbound[s] += 1
            if d in inbound:
                inbound[d] += 1
            if kinds.get(s) == "source" and kinds.get(d) == "sink":
                errors.append(
                    "arrow %s connects a source directly to a sink — "
                    "nothing transforms in between; route through the "
                    "step that does the work, or the diagram claims "
                    "there isn't one" % a["id"])
        for eid, k in kinds.items():
            if k not in FLOW_KINDS:
                continue
            if k not in ("source",) and inbound[eid] and not outbound[eid] \
                    and k != "sink":
                errors.append(
                    "%s is a black hole — flow enters and never leaves. "
                    "Every gap like this is a conversation not yet had: "
                    "ask it, don't just patch it" % name(eid))
            if k not in ("sink",) and outbound[eid] and not inbound[eid] \
                    and k != "source":
                errors.append(
                    "%s is a miracle — flow leaves it but nothing feeds "
                    "it. Same rule: this is a question, then a repair"
                    % name(eid))

    # ---- ERROR/NOTE: sequence invariants (gated on party/lifeline kinds)
    seq_kinds = {"actor", "system", "context", "lifeline"}
    if any(kind_of(e) in seq_kinds for e in els):
        msgs = [a for a in arrows if a.get("type") == "arrow"]
        for a in msgs:
            pts = a.get("points") or []
            if pts and pts[-1][1] < -8:
                errors.append(
                    "message %s travels UP the page — time flows down in "
                    "a sequence; if this is a reply, add it as a new "
                    "message below, not a back-arrow" % a["id"])
        if len(msgs) > 9:
            notes.append("%d messages (budget: 5-9 per scenario) — split "
                         "by scenario, one diagram each" % len(msgs))
        # activation-never-closes needs message pairing semantics —
        # deferred until activations carry their span links (task #7 tail)

    # ---- WARNING: legibility -----------------------------------------
    for e in arrows:
        run = (e["points"][-1][0] ** 2 + e["points"][-1][1] ** 2) ** 0.5
        lbl = next((t for t in els if t.get("containerId") == e["id"]
                    and t.get("type") == "text"), None)
        if lbl is not None and run < lbl.get("width", 0) + 24:
            warnings.append(
                "label %r is wider than its arrow's %dpx run (%s) — "
                "spread the endpoints or shorten the label"
                % (lbl.get("text", "")[:30], int(run), e["id"]))
        if e.get("type") == "arrow" and e.get("startArrowhead") \
                and e.get("endArrowhead"):
            warnings.append(
                "arrow %s points both ways — split it into two labeled "
                "arrows; a bidirectional arrow says nothing about who "
                "initiates" % e["id"])
        # diagonal between off-axis endpoints (server output elbows these;
        # a surviving diagonal is user-drawn or legacy)
        if len(e.get("points") or []) == 2 and \
                (e.get("startBinding") or e.get("endBinding")):
            ddx = abs(e["points"][-1][0])
            ddy = abs(e["points"][-1][1])
            if ddx > 40 and ddy > 40:
                warnings.append(
                    "arrow %s runs diagonally (%dx%dpx) — off-axis "
                    "connections read better as two-segment elbows; "
                    "re-route via mod from/to, or leave it if the "
                    "diagonal is deliberate" % (e["id"], int(ddx),
                                                int(ddy)))
        # through-node crossing: test each real segment, never the
        # first-to-last chord — a correctly-routed elbow's chord crosses
        # boxes its actual path misses (v0.1 acceptance false positive)
        pts = e.get("points") or [[0, 0]]
        ex0, ey0 = e.get("x", 0), e.get("y", 0)
        segs = [(ex0 + pts[i][0], ey0 + pts[i][1],
                 ex0 + pts[i + 1][0], ey0 + pts[i + 1][1])
                for i in range(len(pts) - 1)] or [bbox_pts(e)]
        ends = {(e.get("startBinding") or {}).get("elementId"),
                (e.get("endBinding") or {}).get("elementId")}
        for n in nodes:
            if n["id"] in ends:
                continue
            if any(_seg_hits_rect(sx1, sy1, sx2, sy2, n)
                   for sx1, sy1, sx2, sy2 in segs):
                warnings.append(
                    "arrow %s passes through %s, which is neither its "
                    "source nor destination — route around it"
                    % (e["id"], name(n["id"])))
    for i, a in enumerate(shapes):
        for b in shapes[i + 1:]:
            ox = min(a["x"] + a.get("width", 0), b["x"] + b.get("width", 0)) \
                - max(a["x"], b["x"])
            oy = min(a["y"] + a.get("height", 0),
                     b["y"] + b.get("height", 0)) - max(a["y"], b["y"])
            if ox > 0 and oy > 0:
                smaller = min(a.get("width", 1) * a.get("height", 1),
                              b.get("width", 1) * b.get("height", 1))
                if ox * oy > 0.25 * smaller:
                    warnings.append(
                        "%s and %s overlap — separate them"
                        % (name(a["id"]), name(b["id"])))
    # annotations were excluded from the v0 overlap loop entirely — the
    # demo shipped a note lying across a node for five rounds
    annos = [e for e in els if e.get("type") == "text"
             and role_of(e) == "annotation"]
    for t in annos:
        for n in nodes:
            ox = min(t["x"] + t.get("width", 0),
                     n["x"] + n.get("width", 0)) - max(t["x"], n["x"])
            oy = min(t["y"] + t.get("height", 0),
                     n["y"] + n.get("height", 0)) - max(t["y"], n["y"])
            if ox > 8 and oy > 4:
                warnings.append(
                    "annotation %r lies on top of %s — move it clear "
                    "(and give it customData.annotates so it stays "
                    "attached to its subject)"
                    % ((t.get("text") or "")[:30], name(n["id"])))
    for e in shapes:
        lbl = next((t for t in els if t.get("containerId") == e["id"]
                    and t.get("type") == "text"), None)
        if lbl is not None and lbl.get("width", 0) > e.get("width", 0) - 6:
            warnings.append(
                "label %r overflows its %dpx-wide box (%s) — bound labels "
                "render on ONE line; widen the box or shorten the label"
                % (lbl.get("text", "")[:30], int(e.get("width", 0)),
                   e["id"]))
    # shared attach points
    anchor_pts = {}
    for a in arrows:
        x1, y1, x2, y2 = bbox_pts(a)
        for key, px, py in (("startBinding", x1, y1),
                            ("endBinding", x2, y2)):
            tgt = ((a.get(key) or {}).get("elementId"))
            if tgt:
                anchor_pts.setdefault(tgt, []).append((a["id"], px, py))
    for tgt, pts in anchor_pts.items():
        for i, (id1, px1, py1) in enumerate(pts):
            for id2, px2, py2 in pts[i + 1:]:
                if abs(px1 - px2) < 12 and abs(py1 - py2) < 12:
                    warnings.append(
                        "arrows %s and %s share an attach point on %s — "
                        "fan them (focus ±0.5 steps, or offset anchors "
                        "≥12px)" % (id1, id2, name(tgt)))
    # stranded element: far outside everything else's bounding box
    if len(shapes) > 2:
        for e in shapes:
            others = [o for o in shapes if o is not e]
            ox1 = min(o["x"] for o in others)
            oy1 = min(o["y"] for o in others)
            ox2 = max(o["x"] + o.get("width", 0) for o in others)
            oy2 = max(o["y"] + o.get("height", 0) for o in others)
            gap_x = max(ox1 - (e["x"] + e.get("width", 0)), e["x"] - ox2, 0)
            gap_y = max(oy1 - (e["y"] + e.get("height", 0)), e["y"] - oy2, 0)
            if max(gap_x, gap_y) > 800:
                warnings.append(
                    "%s sits %dpx away from everything else — stranded "
                    "by a bad coordinate?" % (name(e["id"]),
                                              int(max(gap_x, gap_y))))

    # ---- NOTE: style & budgets ---------------------------------------
    offgrid = [e["id"] for e in shapes
               if any(isinstance(e.get(k), (int, float)) and e[k] % 4
                      for k in ("x", "y", "width", "height"))]
    if offgrid:
        notes.append("%d element(s) off the 4px grid (%s%s) — seed from "
                     "grid indices (references/layout.md)"
                     % (len(offgrid), ", ".join(offgrid[:3]),
                        "…" if len(offgrid) > 3 else ""))
    for e in els:
        if e.get("opacity") not in (None, 100):
            notes.append("%s has opacity %s — opacity is state, not "
                         "style; static elements stay at 100"
                         % (e["id"], e.get("opacity")))
    bound_ids = set()
    for a in arrows:
        for key in ("startBinding", "endBinding"):
            t = (a.get(key) or {}).get("elementId")
            if t:
                bound_ids.add(t)
    for d in shapes:
        if d.get("type") != "diamond":
            continue
        for a in arrows:
            if (a.get("startBinding") or {}).get("elementId") == d["id"] \
                    and not any(t.get("containerId") == a["id"]
                                for t in els if t.get("type") == "text"):
                notes.append("arrow %s leaves decision %s unlabeled — "
                             "name the branch" % (a["id"], name(d["id"])))
    # wireframe scenes (nodes living inside screen frames) connect by
    # geometry, not arrows — the unconnected note is flow vocabulary and
    # fired on every block by construction (v0.1 acceptance false
    # positive); same for the node budget, which is per-SCREEN there
    # because the state-variant convention draws normal+degraded pairs
    # in one artifact (references/wireframe.md)
    framed = {n["id"]: n.get("frameId") for n in nodes}
    in_frames = any(v for v in framed.values())
    orphans = [n["id"] for n in nodes
               if n["id"] not in bound_ids and not framed.get(n["id"])]
    if orphans and not in_frames:
        notes.append("unconnected node(s): %s" % ", ".join(
            name(o) for o in orphans[:4]))
    if in_frames:
        per_screen = {}
        for nid, fid in framed.items():
            if fid:
                per_screen[fid] = per_screen.get(fid, 0) + 1
        for fid, count in per_screen.items():
            if count > 9:
                notes.append("%d blocks in screen %s (budget: 9 per "
                             "screen) — split the screen rather than "
                             "shrink the font" % (count, name(fid)))
    elif len(nodes) > 9:
        notes.append("%d nodes (budget: 9) — split the view rather than "
                     "shrink the font" % len(nodes))
    real_arrows = [a for a in arrows if a.get("type") == "arrow"]
    if len(real_arrows) > 12:
        notes.append("%d arrows (budget: 12) — the arrow budget is the "
                     "one that triggers a second view: edges collide, "
                     "nodes don't" % len(real_arrows))
    return {"errors": errors, "warnings": warnings, "notes": notes}


# canonical CONTEXT-FORMAT is '**Term**:' at line start, but agents in the
# wild write '**Term** — definition' (v0.1 acceptance session) and Markdown
# bullet lists ('- **Term**: …', this project's own CONTEXT.md). A glossary
# that parses to zero terms is indistinguishable from no glossary at all —
# every downstream lint goes silently dark, which is how a rejected synonym
# stayed on the domain view unflagged.
TERM_RE = r"(?:[-*+]\s+)?\*\*(.+?)\*\*\s*(?::|—|–|-{1,2}\s)"


def parse_glossary_avoid(text):
    """CONTEXT.md (domain-modeling glossary format) → {rejected synonym
    (lowercased): canonical term}. Entries look like:
        **Provider**:
        <definition prose>
        _Avoid_: vendor, supplier
    """
    out = {}
    term = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(TERM_RE, line)
        if m:
            term = m.group(1).strip()
        # `_Avoid_` may sit on its own line (canonical) or trail the
        # definition on the term's own line (this project's CONTEXT.md
        # does) — searching instead of matching is what makes the
        # rejected-synonym lint fire at all on a one-line-per-term file
        m = re.search(r"_Avoid_\s*:\s*(.+)$", line)
        if m and term:
            for syn in m.group(1).split(","):
                # a trailing parenthetical or a following sentence is
                # commentary on the rejection, not another synonym
                syn = re.split(r"\(|\.\s", syn)[0]
                syn = syn.strip().strip(".").lower()
                if syn:
                    out[syn] = term
    return out


def lint_glossary(els, avoid_map=None, has_context_map=True):
    """Domain-language lint (references/domain.md), same tier shape as
    lint_layout. Pure: the caller resolves the glossary and whether a root
    CONTEXT-MAP.md exists (see project_lint)."""
    errors, warnings, notes = [], [], []
    labels = label_map(els)
    # WARNING: relabeling TO a rejected synonym — the glossary's _Avoid_
    # lists are live ammunition; never a silent accept
    if avoid_map:
        for e in els:
            if role_of(e) in ("label", "pin", "annotation"):
                continue
            lbl = (labels.get(e["id"]) or "").strip()
            hit = avoid_map.get(lbl.lower())
            if hit and lbl.lower() != hit.lower():
                warnings.append(
                    "%s is labeled %r — a rejected synonym (_Avoid_) of "
                    "glossary term %r in CONTEXT.md; relabel to %r or "
                    "challenge the glossary in chat, never accept silently"
                    % (e["id"], lbl, hit, hit))
    # context-frame checks fire only where entities exist (domain views);
    # lane frames (flow) and screen frames (wireframe, no entities in
    # scene) never qualify
    if any(kind_of(e) == "entity" for e in els):
        frame_ids = {e["id"] for e in els if e.get("type") == "frame"
                     and kind_of(e) != "lane"}
        if len(frame_ids) >= 2 and not has_context_map:
            notes.append(
                "%d bounded-context frames and no CONTEXT-MAP.md at the "
                "project root — worth offering one (each context, where "
                "it lives, how they relate)" % len(frame_ids))
        for a in els:
            if a.get("type") not in ("arrow", "line"):
                continue
            s = (a.get("startBinding") or {}).get("elementId")
            t = (a.get("endBinding") or {}).get("elementId")
            if s in frame_ids and t in frame_ids and s != t \
                    and not (labels.get(a["id"]) or "").strip():
                notes.append(
                    "arrow %s connects context frames %s and %s with no "
                    "relationship label — the arrow between two contexts "
                    "is itself a model (customer/supplier, conformist, "
                    "anticorruption layer, shared kernel…)"
                    % (a["id"], s, t))
    return {"errors": errors, "warnings": warnings, "notes": notes}


def parse_glossary_terms(text):
    """CONTEXT.md → ordered list of settled term names (canonical
    '**Term**:', the em-dash '**Term** — …' form, and bullet lists)."""
    terms = []
    for raw in text.splitlines():
        m = re.match(TERM_RE, raw.strip())
        if m:
            terms.append(m.group(1).strip())
    return terms


def lint_registry(terms, registry):
    """Registry-level discipline notes (NOTE tier, artifact-independent):
    settled glossary terms with no concept behind them (ADR 0007 — a term
    settling IS a concept being minted), unpaid view debt (ADR 0006 —
    `owed` types recorded at archetype time, cleared as views register),
    and views filed on the wrong concept (ADR 0010 — umbrella pile-up and
    the name-affinity misfile)."""
    notes = []
    concepts = registry.get("concepts") or []
    known = set()
    for c in concepts:
        for v in (c.get("name"), c.get("id"), c.get("glossary")):
            if v:
                known.add(str(v).lower())
    orphans = [t for t in terms
               if t.lower() not in known and slugify(t) not in known]
    if orphans:
        shown = ", ".join(repr(t) for t in orphans[:4])
        more = " (+%d more)" % (len(orphans) - 4) if len(orphans) > 4 else ""
        notes.append(
            "%d settled glossary term(s) have no registry concept: %s%s "
            "— a term settling IS a concept being minted (ADR 0007); add "
            "upsert_concept registry ops (glossary: the term) in your "
            "next revision" % (len(orphans), shown, more))
    for c in concepts:
        if c.get("owed"):
            notes.append(
                "view debt: concept %r owes %s view(s) — pay at most one "
                "per round, only when the question on the table needs it "
                "(drawing a view of that type clears the debt)"
                % (c.get("name") or c.get("id"), ", ".join(c["owed"])))
    # umbrella pile-up: minting concepts is pointless if the views still
    # hang off one concept — views belong to the most specific concept
    # they make tangible (live finding: 'report-wireframe' under the
    # project umbrella while concept 'Report' sat at 0 views). Two
    # triggers, because one alone is blind: every-view-on-one catches the
    # young project, and ≥4-on-one catches the project that reattached a
    # single view and went quiet while the pile kept growing. Unviewed
    # term-concepts are NOT a trigger — ADR 0007 mints one per glossary
    # term and most never earn a view; counting them would nag forever.
    viewful = [c for c in concepts if c.get("views")]
    top = max(concepts, key=lambda c: len(c.get("views") or []),
              default={})
    n_top = len(top.get("views") or [])
    if len(concepts) >= 4 and ((len(viewful) == 1 and n_top >= 2) or
                               n_top >= 4):
        notes.append(
            "%d views hang off one concept (%r) while %d concepts have "
            "none — a view belongs to the MOST SPECIFIC concept it "
            "makes tangible (an output wireframe of the report is a view "
            "of the report concept, not the umbrella); reattach with "
            "upsert_concept {\"id\": <concept>, \"views\": [<artifact>]} "
            "+ remove_view from the umbrella"
            % (n_top, top.get("name") or top.get("id"),
               len(concepts) - len(viewful)))
    # misfiled view: the artifact is named after a concept that does not
    # claim it. Highest-precision tell there is — 'report-wireframe' filed
    # under the umbrella while concept 'report' has no views is the whole
    # bug in one line. Full-id containment (all of the concept's slug
    # tokens present) keeps multi-token concepts from matching on one
    # shared word.
    for c in concepts:
        cid_tokens = [t for t in str(c.get("id") or "").split("-") if t]
        if not cid_tokens:
            continue
        for other in concepts:
            if other is c:
                continue
            for v in other.get("views") or []:
                vt = set(str(v).split("-"))
                if set(cid_tokens) <= vt and v not in (c.get("views") or []):
                    notes.append(
                        "view %r is named after concept %r but is "
                        "registered under %r — if it makes %r tangible, "
                        "reattach it (upsert_concept {\"id\": %r, "
                        "\"views\": [%r]} + remove_view from %r)"
                        % (v, c.get("name") or c["id"],
                           other.get("name") or other["id"],
                           c.get("name") or c["id"], c["id"], v,
                           other["id"]))
    return notes


def project_lint(project, els, registry=None):
    """lint_layout + lint_glossary (+ registry discipline when the caller
    passes the registry) with the project context resolved: the
    co-authored glossary is project_knowledge/CONTEXT.md; the
    multi-context map convention is a root-level CONTEXT-MAP.md."""
    lint = lint_layout(els)
    avoid, terms = {}, []
    ctx = project.pk / "CONTEXT.md"
    try:
        if ctx.exists():
            text = ctx.read_text(encoding="utf-8")
            avoid = parse_glossary_avoid(text)
            terms = parse_glossary_terms(text)
    except OSError:
        pass
    has_map = (project.root / "CONTEXT-MAP.md").exists()
    extra = lint_glossary(els, avoid, has_map)
    out = {k: lint[k] + extra[k] for k in ("errors", "warnings", "notes")}
    if registry is not None:
        out["notes"] = out["notes"] + lint_registry(terms, registry)
    return out


class BatchError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("invalid op batch:\n" + "\n".join(errors))


class StaleError(Exception):
    def __init__(self, base_revn, head_revn):
        self.base_revn = base_revn
        self.head_revn = head_revn
        super().__init__(
            "base_revn %s is stale — the project is now at revn %s. Re-read "
            "the current state (canvas.py status / GET /api/state) and rebase "
            "your ops before retrying." % (base_revn, head_revn))


class Store:
    def __init__(self, project, log=None):
        self.p = project
        self.log = log or (lambda msg: None)
        self.lock = threading.RLock()
        self.issues = []
        self.rollback = None          # {"matches_revn": N} when git-revert seen
        self.checkout_revn = None     # detached checkout point (memory only)
        self.reconciliation = None    # last catch-up record revn
        self.load()

    # -- loading ----------------------------------------------------------
    def load(self):
        self.p.ensure_tree()
        # config
        if self.p.config_path.exists():
            try:
                cfg = read_json(self.p.config_path)
            except ValueError:
                cfg = None
        else:
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg, cfg_issues = validate_config(cfg)
        cfg, migrated = apply_migrations(cfg, "config", self.p.config_path,
                                         self.p, self.log)
        self.config = cfg
        if cfg_issues or migrated or not self.p.config_path.exists():
            write_json(self.p.config_path, cfg)
        # registry
        if self.p.registry_path.exists():
            try:
                reg = read_json(self.p.registry_path)
            except ValueError:
                reg = None
        else:
            reg = json.loads(json.dumps(DEFAULT_REGISTRY))
        reg, reg_issues = validate_registry(reg)
        reg, migrated = apply_migrations(reg, "registry", self.p.registry_path,
                                         self.p, self.log)
        self.registry = reg
        if reg_issues or migrated or not self.p.registry_path.exists():
            write_json(self.p.registry_path, reg)
        for i in cfg_issues + reg_issues:
            self.issues.append(i.to_dict())
            self.log("repair: %s %s" % (i.code, i.msg))
        # save records
        self.records = {}
        for f in sorted(self.p.saves_dir.glob("*.json")):
            try:
                rec = read_json(f)
                rec, _ = apply_migrations(rec, "save", f, self.p, self.log)
                self.records[rec["revn"]] = rec
            except (ValueError, KeyError):
                self.issues.append(Issue(
                    "SAV-001", "unreadable save record %s" % f.name,
                    "The file is skipped; history before it may not "
                    "reconstruct. Restore it from git if possible.").to_dict())
        # artifacts on disk
        self.scenes = {}
        self.artifact_meta = {}
        for f in sorted(self.p.artifacts_dir.glob("*.excalidraw")):
            aid = f.stem
            try:
                doc = read_json(f)
            except ValueError:
                self.issues.append(Issue(
                    "ART-006", "%s is not valid JSON — ignored" % f.name,
                    "Fix or delete the file; the last committed state is "
                    "still in saves/.").to_dict())
                continue
            doc, art_issues = validate_scene(doc, aid)
            if doc is None:
                continue
            doc, _ = apply_migrations(doc, "artifact", f, self.p, self.log)
            doc = normalize_scene_doc(doc)
            for i in art_issues:
                self.issues.append(i.to_dict())
                self.log("repair: %s %s" % (i.code, i.msg))
            self.scenes[aid] = doc["elements"]
            self.artifact_meta[aid] = doc.get("wysiwyg", {})
        # heal orphaned pins: a registry pin whose ❓ element no longer
        # exists anywhere is unresolvable through ops — prune it at load so
        # corrupted state always has a repair path
        all_ids = {e["id"] for els in self.scenes.values() for e in els}
        for p in self.registry.get("pins", []):
            if p.get("status") in ("open", "answered") \
                    and p["id"] not in all_ids:
                p["status"] = "pruned"
                self.issues.append(Issue(
                    "PIN-001", "pin %r had no canvas element — pruned"
                    % p["id"], "Its question/answer text is preserved in "
                    "the registry entry.", True).to_dict())
                self.log("repair: PIN-001 orphaned pin %s pruned" % p["id"])

    # -- basic accessors --------------------------------------------------
    def head_branch(self):
        for b in self.registry["branches"]:
            if b["name"] == self.registry["head"]:
                return b
        return self.registry["branches"][0]

    def head_revn(self):
        return self.head_branch()["head"]

    def artifact_type(self, aid):
        return (self.artifact_meta.get(aid) or {}).get("artifact_type", "flow")

    def tier_of(self, atype):
        entry = (self.config.get("artifact_types") or {}).get(atype) or {}
        return entry.get("tier", "extended")

    def concept_of(self, aid):
        for c in self.registry["concepts"]:
            if aid in (c.get("views") or []):
                return c
        return None

    # -- lineage / reconstruction ----------------------------------------
    def lineage(self, revn):
        chain = []
        seen = set()
        cur = revn
        while cur and cur in self.records and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self.records[cur].get("base_revn") or 0
        chain.reverse()
        return chain

    def state_at(self, revn):
        """{artifact_id: {"elements": [...], "meta": {...}}} reconstructed by
        forward replay from the empty root along the lineage."""
        state = {}
        meta = {}
        for r in self.lineage(revn):
            rec = self.records[r]
            for aid, part in (rec.get("artifacts") or {}).items():
                els = state.get(aid, [])
                state[aid] = replay_changes(els, part.get("changes") or [])
                if part.get("meta"):
                    meta[aid] = part["meta"]
            for gone in rec.get("artifacts_removed") or []:
                state.pop(gone, None)
        return {aid: {"elements": rebuild_bound_elements(
                          [dict(e) for e in els]),
                      "meta": meta.get(aid, {})}
                for aid, els in state.items()}

    # -- commit -----------------------------------------------------------
    def commit(self, author, new_scenes, base_revn=None, selection=None,
               user_note=None, fork_name=None, registry_ops=None,
               new_meta=None, reconciliation=False):
        """The one write path. new_scenes: {artifact_id: element list}
        (only changed artifacts need be present). Returns the save record."""
        with self.lock:
            head = self.head_revn()
            branch = self.registry["head"]
            forked = False
            if self.checkout_revn is not None and author == "user":
                base = self.checkout_revn
                forked = True
                name = fork_name or ("alt-%04d" % (self.registry["revn"] + 1))
                if any(b["name"] == name for b in self.registry["branches"]):
                    name = "%s-%d" % (name, self.registry["revn"] + 1)
                branch = name
            else:
                base = head
                if base_revn is not None and base_revn != head:
                    raise StaleError(base_revn, head)

            base_state = self.state_at(base) if base else {}
            revn = self.registry["revn"] + 1
            artifacts = {}
            all_tripwires = []
            changed_elements = set()
            sig = self.config.get("significant_attrs")
            for aid, els in sorted(new_scenes.items()):
                new_norm = [normalize_element(e) for e in els
                            if not e.get("isDeleted")]
                old = (base_state.get(aid) or {}).get("elements", [])
                diff = diff_scenes(old, new_norm, sig)
                if not diff["changes"] and aid in base_state and not \
                        (new_meta or {}).get(aid):
                    continue
                consequences = mark_consequences(diff)
                atype = ((new_meta or {}).get(aid) or {}).get(
                    "artifact_type") or self.artifact_type(aid)
                facts = semantic_facts(old, new_norm, diff, atype,
                                       self.tier_of(atype), consequences)
                for f in facts:
                    if f.get("consequence_of") is None and \
                            f["fact"] != "saved_no_changes" and f["element"]:
                        changed_elements.add("%s#%s" % (aid, f["element"]))
                by_element = self._by_element(diff, facts, consequences,
                                              new_norm, old)
                artifacts[aid] = {
                    "changes": diff["changes"],
                    "inverse": diff["inverse"],
                    "by_element": by_element,
                    "facts": facts,
                    "meta": (new_meta or {}).get(aid) or
                            dict(self.artifact_meta.get(aid) or {}),
                }
            if not artifacts and author == "user":
                # explicit empty save — still a commit ("you saved without
                # changing anything")
                artifacts = {}

            # pin lifecycle on deletion: a deleted pin element = "not worth
            # explaining", never re-raised; a pin whose TARGET was deleted
            # follows the prune rules (spec §5.2)
            deleted_all = set()
            for part in artifacts.values():
                for c in part["changes"]:
                    if c["op"] == "del":
                        deleted_all.add(c["element"]["id"])
            for p in self.registry["pins"]:
                if p.get("status") in ("open", "answered"):
                    if p["id"] in deleted_all:
                        p["status"] = "dismissed"
                    elif p.get("element") and p["element"] in deleted_all:
                        p["status"] = "pruned"

            facts_flat = [f for a in artifacts.values() for f in a["facts"]]
            if not facts_flat:
                facts_flat = [{"fact": "saved_no_changes", "element": None}]
            sentinel = 0
            summary = mechanical_summary(facts_flat, sentinel)
            tripwires = self._check_tripwires(changed_elements, revn)
            all_tripwires.extend(tripwires)

            slug = slugify(next(iter(artifacts), "empty"))
            record = {
                "migrations": ["0001-baseline"],
                "revn": revn,
                "base_revn": base,
                "branch": branch,
                "author": author,
                # the round this save lands in — an agent move OPENS the
                # next round, and one move may span several commits, so
                # only the FIRST agent commit after a non-agent one bumps
                # (v0.1 acceptance finding F-A2: per-commit bumping read
                # "round 10" during conversational round 2). Rounds are
                # the session's unit of time and ADRs cite them as
                # evidence; unstamped records made that unverifiable.
                "round": self.registry.get("round", 0) +
                         (1 if author == "agent" and
                          (self.records.get(base) or {}).get("author")
                          != "agent" else 0),
                "saved_at": now_iso(),
                "selection_at_save": selection or [],
                "user_note": user_note,
                "reconciliation": bool(reconciliation),
                "artifacts": artifacts,
                "registry_changes": [],
                "summary": summary,
                "tripwires": all_tripwires,
            }
            if registry_ops:
                reg_errors = []
                record["registry_changes"] = self._apply_registry_ops(
                    registry_ops, reg_errors)
                if reg_errors:
                    raise BatchError(reg_errors)
            record["short_id"] = hashlib.sha1(
                json.dumps(record, sort_keys=True, default=str)
                .encode("utf-8")).hexdigest()[:7]

            # persist: record file, artifact files, registry
            rec_path = self.p.saves_dir / ("%04d-%s.json" % (revn, slug))
            write_json(rec_path, record)
            self.records[revn] = record
            for aid in artifacts:
                if aid in new_scenes:
                    self._write_artifact(aid, new_scenes[aid],
                                         artifacts[aid]["meta"])
            self.registry["revn"] = revn
            if forked:
                self.registry["branches"].append(
                    {"name": branch, "head": revn, "archived": False})
                self.registry["head"] = branch
                self.checkout_revn = None
            else:
                self.head_branch()["head"] = revn
            if author == "user":
                self.registry["whose_move"] = "agent"
            elif author == "agent":
                self.registry["round"] = record["round"]
                self.registry["whose_move"] = "user"
            self._save_registry()
            return record

    def _write_artifact(self, aid, els, meta):
        doc = normalize_scene_doc({
            "elements": els,
            "wysiwyg": {
                "artifact": aid,
                "name": meta.get("name") or aid.replace("-", " ").title(),
                "artifact_type": meta.get("artifact_type", "flow"),
                "migrations": meta.get("migrations") or ["0001-baseline"],
            },
        })
        doc["wysiwyg"] = {
            "artifact": aid,
            "name": meta.get("name") or aid.replace("-", " ").title(),
            "artifact_type": meta.get("artifact_type", "flow"),
            "migrations": meta.get("migrations") or ["0001-baseline"],
        }
        write_json(self.p.artifacts_dir / (aid + ".excalidraw"), doc)
        self.scenes[aid] = doc["elements"]
        self.artifact_meta[aid] = doc["wysiwyg"]

    def _by_element(self, diff, facts, consequences, new_els, old_els):
        new_ix = {e["id"]: e for e in new_els}
        old_ix = {e["id"]: e for e in old_els}
        labels_new = label_map(new_els)
        labels_old = label_map(old_els)
        entries = {}
        for f in facts:
            eid = f.get("element")
            if not eid:
                continue
            el = new_ix.get(eid) or old_ix.get(eid) or {}
            e = entries.setdefault(eid, {
                "id": eid,
                "kind": kind_of(el) if el else "unknown",
                "label": display_label(el, labels_new if eid in new_ix
                                       else labels_old) if el else eid,
                "verb": f["fact"],
                "attrs_changed": [],
                "ops": [],
                "semantic": {},
                "consequence_of": consequences.get(eid),
            })
            e["semantic"][f["fact"]] = {k: v for k, v in f.items()
                                        if k not in ("fact", "element")}
        for i, c in enumerate(diff["changes"]):
            eid = c.get("id") or (c.get("element") or {}).get("id")
            if eid in entries:
                entries[eid]["ops"].append(i)
                if c["op"] == "mod":
                    entries[eid]["attrs_changed"].extend(
                        a["attr"] for a in c["attrs"])
        return list(entries.values())

    # -- tripwires --------------------------------------------------------
    def _check_tripwires(self, changed_elements, revn):
        out = []
        if not changed_elements:
            return out
        for m in self.registry["mappings"]:
            note = m.get("note") or ""
            members = m.get("elements") or []
            hits = [e for e in members if e in changed_elements]
            siblings = [e for e in members if e not in changed_elements]
            if not hits or not siblings:
                # both sides edited together = convergent edit; resolve any
                # open tripwire on this mapping
                if hits and not siblings:
                    for t in self.registry["tripwires"]:
                        if t.get("status") == "open" and \
                                t.get("mapping") == self._mapping_key(m):
                            t["status"] = "resolved"
                            t["resolved_by"] = revn
                continue
            if note.startswith("intentionally-divergent"):
                continue
            if self._policy_covers(m):
                continue
            for h in hits:
                for s in siblings:
                    entry = {"mapping": self._mapping_key(m), "changed": h,
                             "sibling": s, "kind": "divergence"}
                    out.append(entry)
                    reg_entry = dict(entry)
                    ch = h.replace("#", " › ")
                    sb = s.replace("#", " › ")
                    reg_entry.update({
                        "id": "tw-%d-%d" % (revn, len(out)),
                        "save": revn, "status": "open",
                        # answerable in place (like pins) — defaults the
                        # agent may sharpen via annotate_tripwire
                        "question": "%s changed but its mapped sibling %s "
                                    "didn't. Divergence, or should it "
                                    "propagate?" % (ch, sb),
                        "choices": ["Intentional divergence — keep both",
                                    "Propagate to the sibling"],
                        "detail": (
                            "These two elements are declared views of the "
                            "same thing (mapping %s). At save %d, %s "
                            "changed while %s stayed put — so right now "
                            "the two views disagree.\n\n"
                            "• 'Intentional divergence' records the "
                            "difference as deliberate: the mapping stays, "
                            "annotated so this pair never trips again for "
                            "this reason.\n"
                            "• 'Propagate' asks the agent to carry "
                            "the change into %s in its next revision — "
                            "narrated, and nothing is touched until you "
                            "answer.\n\n"
                            "Free-text works too: name a third option, or "
                            "explain what the two views actually mean."
                            % (self._mapping_key(m), revn, ch, sb, sb)),
                        "examples": [],
                        "answer": None,
                    })
                    self.registry["tripwires"].append(reg_entry)
        return out

    def _policy_covers(self, m):
        """Does a class-level divergence policy cover this mapping? One
        ruling ('wireframe blocks name report sections, flow steps name the
        work — meant to differ') silences the whole class, instead of the
        N identical per-mapping notes the demo session had to write."""
        member_types = {self.artifact_type(e.split("#", 1)[0])
                        for e in (m.get("elements") or [])}
        for pol in self.registry.get("divergence_policies") or []:
            if pol.get("concept") and pol["concept"] != m.get("concept"):
                continue
            if pol.get("types") and not member_types <= set(pol["types"]):
                continue
            return True
        return False

    @staticmethod
    def _mapping_key(m):
        return "%s:%s" % (m.get("concept"), "+".join(m.get("elements") or []))

    # -- registry ops (co-authored: these only exist inside a commit) -----
    def _apply_registry_ops(self, ops, errors):
        applied = []
        for i, op in enumerate(ops):
            if op.get("op") != "registry":
                continue
            action = op.get("action")
            reg = self.registry
            if action == "upsert_concept":
                cid = op.get("id") or slugify(op.get("name", ""))
                if not cid:
                    errors.append("registry op %d: upsert_concept needs id or "
                                  "name" % i)
                    continue
                c = next((c for c in reg["concepts"] if c["id"] == cid), None)
                if c is None:
                    c = {"id": cid, "name": op.get("name") or cid,
                         "views": [], "glossary": op.get("glossary"),
                         "unviewed": False, "owed": []}
                    reg["concepts"].append(c)
                elif (not op.get("id") and op.get("name") and c.get("name")
                        and op["name"] != c["name"]):
                    # minting BY NAME onto an id that already belongs to a
                    # different name is a collision, not an update — the
                    # silent version of this swallowed a whole concept
                    errors.append(
                        "registry op %d: concept name %r slugs to id %r, "
                        "which already belongs to %r — pass an explicit "
                        "distinct `id` (renaming? pass the existing `id` "
                        "with the new `name`, ids stay stable across "
                        "renames)" % (i, op["name"], cid, c["name"]))
                    continue
                if op.get("name"):
                    c["name"] = op["name"]
                if op.get("glossary") is not None:
                    c["glossary"] = op["glossary"]
                # view debt (ADR 0006): `owed` lists artifact types the
                # archetype says this concept still owes — recorded at
                # naming time, paid below as views of that type register
                if op.get("owed") is not None:
                    if not isinstance(op["owed"], list) or \
                            any(not isinstance(t, str) for t in op["owed"]):
                        errors.append("registry op %d: owed must be a list "
                                      "of artifact-type strings" % i)
                        continue
                    c["owed"] = list(op["owed"])
                for v in op.get("views") or []:
                    if v not in c["views"]:
                        c["views"].append(v)
                    # paying debt at create time: artifact_meta lags the
                    # registry ops inside one commit, so the auto-op from
                    # a `create` carries the type in view_types
                    vtype = (self.artifact_meta.get(v) or {}) \
                        .get("artifact_type") or \
                        (op.get("view_types") or {}).get(v)
                    # `owed` is ARCHETYPE debt — the view set the PROJECT
                    # owes (ADR 0010) — so a view of an owed type pays it
                    # wherever it attaches. Scoping payment to the concept
                    # the view landed on made the debt unpayable unless
                    # every view piled onto the umbrella that declared it,
                    # which is exactly what happened: 4 views on the
                    # umbrella, 9 term-concepts unviewed, VIEW_DEBT=none.
                    if vtype:
                        for other in reg["concepts"]:
                            if vtype in (other.get("owed") or []):
                                other["owed"] = [t for t in other["owed"]
                                                 if t != vtype]
                c["unviewed"] = len(c["views"]) == 0
            elif action == "remove_view":
                cid, view = op.get("concept"), op.get("view")
                c = next((c for c in reg["concepts"] if c["id"] == cid), None)
                if c is None:
                    errors.append("registry op %d: unknown concept %r"
                                  % (i, cid))
                    continue
                c["views"] = [v for v in c["views"] if v != view]
                c["unviewed"] = len(c["views"]) == 0
            elif action == "add_mapping":
                if not op.get("concept") or not op.get("elements"):
                    errors.append("registry op %d: add_mapping needs concept "
                                  "+ elements" % i)
                    continue
                reg["mappings"].append({"concept": op["concept"],
                                        "elements": op["elements"],
                                        "note": op.get("note")})
            elif action == "annotate_mapping":
                pattern = op.get("pattern")
                if pattern:
                    # class-level ruling: ONE record covers every mapping
                    # matching the pattern, now and in the future — the demo
                    # session recorded the identical ruling 8 times because
                    # only per-index annotation existed (audit §7.2)
                    pol = {"types": sorted(pattern.get("types") or []),
                           "concept": pattern.get("concept"),
                           "note": op.get("note") or "intentionally-divergent"}
                    if not pol["types"] and not pol["concept"]:
                        errors.append(
                            "registry op %d: annotate_mapping pattern needs "
                            "`types` (e.g. [\"wireframe\",\"flow\"]) and/or "
                            "`concept`" % i)
                        continue
                    reg.setdefault("divergence_policies", []).append(pol)
                    applied.append({"action": "divergence_policy_added",
                                    "policy": pol})
                    continue
                idx = op.get("index")
                if not isinstance(idx, int) or idx >= len(reg["mappings"]):
                    errors.append("registry op %d: annotate_mapping needs a "
                                  "valid mapping `index` (see model.json "
                                  "mappings order) or a `pattern` for a "
                                  "class-level ruling" % i)
                    continue
                reg["mappings"][idx]["note"] = op.get("note")
            elif action == "remove_mapping":
                idx = op.get("index")
                if not isinstance(idx, int) or idx >= len(reg["mappings"]):
                    errors.append("registry op %d: remove_mapping needs a "
                                  "valid `index`" % i)
                    continue
                tomb = reg["mappings"].pop(idx)
                applied.append({"action": "mapping_tombstoned",
                                "mapping": tomb})
            elif action == "resolve_tripwire":
                tid = op.get("id")
                t = next((t for t in reg["tripwires"] if t.get("id") == tid),
                         None)
                if t is None:
                    errors.append("registry op %d: unknown tripwire %r"
                                  % (i, tid))
                    continue
                t["status"] = "resolved"
            elif action == "annotate_tripwire":
                # enrich an open tripwire's in-place question: sharper
                # question text, custom answer choices (empty list = free
                # text), the modal's what/why detail, concrete examples
                tid = op.get("id")
                t = next((t for t in reg["tripwires"] if t.get("id") == tid),
                         None)
                if t is None:
                    errors.append("registry op %d: unknown tripwire %r"
                                  % (i, tid))
                    continue
                bad = False
                for key, want in (("question", str), ("detail", str)):
                    if op.get(key) is not None and \
                            not isinstance(op[key], want):
                        errors.append("registry op %d: %s must be a string"
                                      % (i, key))
                        bad = True
                for key in ("choices", "examples"):
                    if op.get(key) is not None and (
                            not isinstance(op[key], list) or
                            any(not isinstance(x, str) for x in op[key])):
                        errors.append("registry op %d: %s must be a list "
                                      "of strings" % (i, key))
                        bad = True
                if bad:
                    continue
                for key in ("question", "detail", "choices", "examples"):
                    if op.get(key) is not None:
                        t[key] = op[key]
                applied.append({"action": "tripwire_annotated", "id": tid})
            elif action == "decline":
                reg["declined"].append({
                    "concept": op.get("concept"),
                    "view_type": op.get("view_type"),
                    "kind": op.get("kind", "suggestion"),
                    "reason": op.get("reason"),
                    "when": now_iso()[:10]})
            elif action == "set_round":
                if isinstance(op.get("round"), int):
                    reg["round"] = op["round"]
                if op.get("whose_move") in ("user", "agent"):
                    reg["whose_move"] = op["whose_move"]
            else:
                errors.append("registry op %d: unknown action %r (allowed: "
                              "upsert_concept, remove_view, add_mapping, "
                              "annotate_mapping, remove_mapping, "
                              "resolve_tripwire, annotate_tripwire, "
                              "decline, set_round)"
                              % (i, action))
                continue
            applied.append({k: v for k, v in op.items() if k != "op"})
        return applied

    def _save_registry(self):
        write_json(self.p.registry_path, self.registry)

    # -- the agent write path --------------------------------------------
    def apply_batch(self, batch):
        """Validate-all-then-apply. Returns (record, pin_only)."""
        with self.lock:
            errors = []
            base_revn = batch.get("base_revn")
            if not isinstance(base_revn, int):
                errors.append("batch: integer base_revn required (current "
                              "head revn is %d)" % self.head_revn())
            aid = batch.get("artifact")
            create = batch.get("create")
            ops = batch.get("ops") or []
            if not isinstance(ops, list) or (not ops and not create):
                errors.append("batch: non-empty `ops` list (or a `create` "
                              "artifact block) required")
            new_meta = {}
            if create:
                aid = aid or create.get("id") or slugify(create.get("name", ""))
                if not aid:
                    errors.append("create: artifact id or name required")
                elif aid in self.scenes:
                    errors.append("create: artifact %r already exists" % aid)
                else:
                    atype = create.get("type", "flow")
                    known = set((self.config.get("artifact_types") or {}))
                    if atype not in known:
                        errors.append("create: unknown artifact type %r "
                                      "(configured types: %s)"
                                      % (atype, ", ".join(sorted(known))))
                    if ((self.config.get("artifact_types") or {}).get(atype)
                            or {}).get("disabled"):
                        errors.append("create: artifact type %r is disabled "
                                      "in config.json" % atype)
                    new_meta[aid] = {
                        "artifact_type": atype,
                        "name": create.get("name") or aid,
                        "migrations": ["0001-baseline"]}
            elif aid is None:
                if len(self.scenes) == 1:
                    aid = next(iter(self.scenes))
                else:
                    errors.append("batch: `artifact` id required (known: %s)"
                                  % (", ".join(sorted(self.scenes)) or "none"))
            elif aid not in self.scenes:
                errors.append("batch: unknown artifact %r (known: %s). To "
                              "create one, pass a `create` block."
                              % (aid, ", ".join(sorted(self.scenes)) or "none"))
            if errors:
                raise BatchError(errors)

            base_els = self.scenes.get(aid, [])
            known_pins = {p["id"] for p in self.registry["pins"]}
            scene_ids = {e["id"] for e in base_els}
            for i, o in enumerate(ops):
                if o.get("op") == "resolve_pin" and \
                        o.get("id") not in known_pins and \
                        o.get("id") not in scene_ids:
                    errors.append(
                        "op %d (resolve_pin): unknown pin %r (known: %s)"
                        % (i, o.get("id"),
                           ", ".join(sorted(known_pins)) or "none"))
            pin_reg = []
            new_els = apply_ops(base_els, ops, errors, pin_reg)
            registry_ops = [o for o in ops if o.get("op") == "registry"]
            if errors:
                raise BatchError(errors)

            drawing_ops = [o for o in ops if o.get("op") in
                           ("add", "mod", "del", "reorder")]
            pin_only = not drawing_ops and not create

            if create and create.get("concept"):
                registry_ops = list(registry_ops)
                registry_ops.append({"op": "registry",
                                     "action": "upsert_concept",
                                     "id": create["concept"],
                                     "name": create.get("concept_name"),
                                     "views": [aid],
                                     "view_types":
                                         {aid: create.get("type", "flow")}})

            record = self.commit(
                author="agent",
                new_scenes={aid: new_els},
                base_revn=base_revn,
                user_note=batch.get("note"),
                registry_ops=registry_ops,
                new_meta=new_meta)
            for p in pin_reg:
                self.registry["pins"].append({
                    "id": p["id"], "artifact": aid, "element": p["target"],
                    "question": p["question"], "status": "open",
                    "answer": None, "asked_at_revn": record["revn"],
                    "round": self.registry.get("round", 0),
                    "detail": p.get("detail"),
                    "examples": p.get("examples") or []})
            # resolve_pin writes through to the registry — the canvas element
            # and model.json must never disagree about a pin's status
            resolved = {o.get("id") for o in ops if o.get("op") == "resolve_pin"}
            for p in self.registry["pins"]:
                if p["id"] in resolved and p.get("status") in ("open",
                                                               "answered"):
                    p["status"] = "resolved"
            # and deletion writes through too: a pin whose ❓ element no
            # longer exists in any scene is unanswerable — prune it NOW,
            # not at next session's load-heal (v0.1 acceptance finding:
            # `del` on a pin element left the registry pin open all
            # session). resolve_pin wins if both landed in one batch.
            live_ids = {e["id"] for els2 in self.scenes.values()
                        for e in els2}
            for p in self.registry["pins"]:
                if p.get("status") in ("open", "answered") and \
                        p["id"] not in resolved and \
                        p["id"] not in live_ids:
                    p["status"] = "pruned"
            self._save_registry()
            return record, pin_only

    # -- pins -------------------------------------------------------------
    def answer_tripwire(self, tw_id, answer):
        """In-place tripwire answer (buttons or free text) — same
        first-class wake semantics as a pin answer. The agent acts on it
        (propagate / annotate the mapping / converse) and resolves."""
        with self.lock:
            t = next((t for t in self.registry["tripwires"]
                      if t.get("id") == tw_id), None)
            if t is None:
                raise BatchError(["unknown tripwire %r" % tw_id])
            if t.get("status") not in ("open", "answered"):
                raise BatchError(["tripwire %r is %s — not answerable"
                                  % (tw_id, t.get("status"))])
            t["status"] = "answered"
            t["answer"] = answer
            t["answered_at"] = now_iso()
            self._save_registry()
            return t

    def answer_pin(self, pin_id, answer):
        with self.lock:
            pin = next((p for p in self.registry["pins"]
                        if p["id"] == pin_id), None)
            if pin is None:
                raise BatchError(["unknown pin %r" % pin_id])
            pin["status"] = "answered"
            pin["answer"] = answer
            pin["answered_at"] = now_iso()
            aid = pin.get("artifact")
            # mechanical customData update on the pin element — machinery,
            # not a Save (the differ ignores pin-role customData churn)
            els = self.scenes.get(aid)
            if els:
                for el in els:
                    if el["id"] == pin_id:
                        cd = dict(el.get("customData") or {})
                        cd["status"] = "answered"
                        cd["answer"] = answer
                        el["customData"] = cd
                self._write_artifact(aid, els,
                                     self.artifact_meta.get(aid) or {})
            self._save_registry()
            return pin

    # -- branches ---------------------------------------------------------
    def switch_branch(self, name):
        with self.lock:
            b = next((b for b in self.registry["branches"]
                      if b["name"] == name), None)
            if b is None:
                raise BatchError(["unknown branch %r (branches: %s)"
                                  % (name, ", ".join(
                                      x["name"] for x in
                                      self.registry["branches"]))])
            state = self.state_at(b["head"])
            current = set(self.scenes.keys())
            for aid, part in state.items():
                self._write_artifact(aid, part["elements"],
                                     part["meta"] or
                                     self.artifact_meta.get(aid) or {})
            for aid in current - set(state.keys()):
                f = self.p.artifacts_dir / (aid + ".excalidraw")
                if f.exists():
                    f.unlink()
                self.scenes.pop(aid, None)
                self.artifact_meta.pop(aid, None)
            self.registry["head"] = name
            self.checkout_revn = None
            self._save_registry()
            return b

    def set_archived(self, name, archived):
        with self.lock:
            b = next((b for b in self.registry["branches"]
                      if b["name"] == name), None)
            if b is None:
                raise BatchError(["unknown branch %r" % name])
            if name == self.registry["head"] and archived:
                raise BatchError(["cannot archive the current branch — "
                                  "switch to another branch first"])
            b["archived"] = bool(archived)
            self._save_registry()
            return b

    # -- catch-up narration (out-of-session changes) ----------------------
    def catch_up(self):
        """Diff disk artifacts against the head state; out-of-session edits
        become a reconciliation record; a rollback becomes a question."""
        with self.lock:
            head = self.head_revn()
            expected = self.state_at(head) if head else {}
            exp_scenes = {aid: p["elements"] for aid, p in expected.items()}
            disk = self.scenes
            same = (set(exp_scenes.keys()) == set(disk.keys()) and all(
                scene_hash(exp_scenes[a]) == scene_hash(disk[a])
                for a in disk))
            if same:
                return None
            # rollback? disk matches some ancestor state exactly
            for r in reversed(self.lineage(head)[:-1]):
                st = self.state_at(r)
                st_scenes = {aid: p["elements"] for aid, p in st.items()}
                if set(st_scenes.keys()) == set(disk.keys()) and all(
                        scene_hash(st_scenes[a]) == scene_hash(disk[a])
                        for a in disk):
                    self.rollback = {"matches_revn": r, "head_revn": head}
                    self.log("rollback detected: disk state matches revn %d"
                             % r)
                    return None
            new_meta = {aid: dict(self.artifact_meta.get(aid) or {})
                        for aid in disk}
            record = self.commit(author="out-of-session",
                                 new_scenes=dict(disk),
                                 new_meta=new_meta,
                                 reconciliation=True)
            self.reconciliation = record["revn"]
            self.log("reconciliation record %d written" % record["revn"])
            return record

    def accept_rollback(self):
        """Re-anchor: commit the rolled-back disk state as a new record."""
        with self.lock:
            if not self.rollback:
                return None
            record = self.commit(author="out-of-session",
                                 new_scenes=dict(self.scenes),
                                 new_meta={aid: dict(
                                     self.artifact_meta.get(aid) or {})
                                     for aid in self.scenes},
                                 reconciliation=True)
            self.rollback = None
            return record

    def revert_to(self, revn):
        """Append-only 'undo': replay inverses of head..revn as a NEW commit."""
        with self.lock:
            head = self.head_revn()
            target = self.state_at(revn)
            scenes = {aid: p["elements"] for aid, p in target.items()}
            meta = {aid: p["meta"] or self.artifact_meta.get(aid) or {}
                    for aid, p in target.items()}
            for gone in set(self.scenes.keys()) - set(scenes.keys()):
                scenes[gone] = []
                meta[gone] = dict(self.artifact_meta.get(gone) or {})
            return self.commit(author="agent", new_scenes=scenes,
                               base_revn=head, new_meta=meta,
                               user_note="revert to revn %d" % revn)

    # -- state for the frontend ------------------------------------------
    def public_state(self):
        with self.lock:
            saves = []
            for revn in sorted(self.records):
                r = self.records[revn]
                saves.append({
                    "revn": r["revn"], "base_revn": r.get("base_revn", 0),
                    "branch": r.get("branch", "main"),
                    "author": r.get("author", "user"),
                    "short_id": r.get("short_id", ""),
                    "saved_at": r.get("saved_at", ""),
                    "headline": (r.get("summary") or {}).get("headline", ""),
                    "reconciliation": r.get("reconciliation", False),
                    "tripwires": len(r.get("tripwires") or []),
                    "artifacts": sorted((r.get("artifacts") or {}).keys()),
                })
            artifacts = {}
            for aid, els in self.scenes.items():
                m = self.artifact_meta.get(aid) or {}
                c = self.concept_of(aid)
                artifacts[aid] = {
                    "name": m.get("name") or aid,
                    "artifact_type": m.get("artifact_type", "flow"),
                    "tier": self.tier_of(m.get("artifact_type", "flow")),
                    "concept": c["id"] if c else None,
                    "elements": els,
                }
            return {
                "protocol_version": PROTOCOL_VERSION,
                "project": self.p.name(),
                "revn": self.registry["revn"],
                "head": self.registry["head"],
                "head_revn": self.head_revn(),
                "branches": self.registry["branches"],
                "round": self.registry["round"],
                "whose_move": self.registry["whose_move"],
                "concepts": self.registry["concepts"],
                "mappings": self.registry["mappings"],
                "pins": self.registry["pins"],
                "tripwires": self.registry["tripwires"],
                "declined": self.registry["declined"],
                "config": self.config,
                "artifacts": artifacts,
                "saves": saves,
                "checkout_revn": self.checkout_revn,
                "rollback": self.rollback,
                "issues": self.issues[-20:],
            }


# ---------------------------------------------------------------------------
# save-events log — one line per event, seq + pointer, never the payload.
# Monitor-friendly (tier-1 wait target) and long-pollable (tier-3).
# ---------------------------------------------------------------------------

class EventLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cond = threading.Condition()
        self.events = []
        if self.path.exists():
            try:
                with io.open(str(self.path), "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                self.events.append(json.loads(line))
                            except ValueError:
                                pass
            except OSError:
                pass
        # a well-formed line missing `seq` must not make the server
        # unstartable — the log lives in a world-writable temp dir
        self.seq = self.events[-1].get("seq", 0) if self.events else 0

    def append(self, etype, **fields):
        with self.cond:
            self.seq += 1
            ev = {"seq": self.seq, "ts": now_iso(), "type": etype}
            ev.update(fields)
            self.events.append(ev)
            try:
                with io.open(str(self.path), "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    f.flush()
            except OSError:
                pass
            self.cond.notify_all()
            return ev

    def since(self, seq):
        with self.cond:
            return [e for e in self.events if e["seq"] > seq]

    def wait_since(self, seq, timeout):
        deadline = time.time() + timeout
        with self.cond:
            while True:
                out = [e for e in self.events if e["seq"] > seq]
                if out:
                    return out
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self.cond.wait(remaining)


# ---------------------------------------------------------------------------
# the HTTP server
# ---------------------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json",
    ".txt": "text/plain; charset=utf-8",
}


class ServerApp:
    def __init__(self, project):
        self.project = project
        log_f = io.open(str(project.log_path), "a", encoding="utf-8")

        def log(msg):
            try:
                log_f.write("%s %s\n" % (now_iso(), msg))
                log_f.flush()
            except (OSError, ValueError):
                pass
        self.log = log
        self.store = Store(project, log)
        self.events = EventLog(project.events_path)
        self.web_root = (Path(__file__).resolve().parent / "web")
        self.pending = []
        self.pending_seq = 0
        self.dirty = False
        self.last_activity = time.time()
        self.lock = threading.RLock()
        self.shot_requests = {}
        self.shot_seq = 0
        self.httpd = None
        self.catchup_record = None

    def start_catch_up(self):
        try:
            rec = self.store.catch_up()
            if rec is not None:
                self.catchup_record = rec["revn"]
                self.events.append("reconciliation", revn=rec["revn"],
                                   short_id=rec["short_id"],
                                   headline=rec["summary"]["headline"])
        except Exception as e:  # noqa: BLE001 — surfaced, never fatal
            self.log("catch-up failed: %s" % e)
            self.log(traceback.format_exc())

    # -- pending agent revisions -----------------------------------------
    def queue_pending(self, batch, pin_only):
        with self.lock:
            self.pending_seq += 1
            entry = {"id": self.pending_seq, "batch": batch,
                     "pin_only": pin_only, "deferred": False,
                     "queued_at": now_iso()}
            self.pending.append(entry)
            return entry

    def sanitize_pending(self):
        with self.lock:
            return [{"id": p["id"], "pin_only": p["pin_only"],
                     "deferred": p["deferred"],
                     "note": p["batch"].get("note"),
                     "artifact": p["batch"].get("artifact") or
                     (p["batch"].get("create") or {}).get("id"),
                     "ops": p["batch"].get("ops") or [],
                     "queued_at": p["queued_at"]} for p in self.pending]

    def commit_pending(self, entry):
        batch = dict(entry["batch"])
        batch["base_revn"] = self.store.head_revn()
        record, pin_only = self.store.apply_batch(batch)
        with self.lock:
            self.pending = [p for p in self.pending
                            if p["id"] != entry["id"]]
        self.events.append("agent_revision", revn=record["revn"],
                           short_id=record["short_id"], pin_only=pin_only,
                           headline=record["summary"]["headline"],
                           from_pending=entry["id"])
        return record

    def flush_deferred(self):
        for entry in [p for p in self.pending if p["deferred"]]:
            try:
                self.commit_pending(entry)
            except (BatchError, StaleError) as e:
                with self.lock:
                    self.pending = [p for p in self.pending
                                    if p["id"] != entry["id"]]
                self.events.append(
                    "agent_revision_failed", pending_id=entry["id"],
                    error="Deferred revision could not be applied after the "
                          "user's Save: %s Re-read state and redraw." % e)

    # -- request handling --------------------------------------------------
    def api_state(self):
        st = self.store.public_state()
        st.update({
            "pending": self.sanitize_pending(),
            "dirty": self.dirty,
            "events_seq": self.events.seq,
            "events_log": str(self.project.events_path),
            "screenshot_requests": [
                {"id": k, "artifact": v["artifact"]}
                for k, v in self.shot_requests.items()
                if v.get("status") == "waiting"],
        })
        return st

    def handle_post(self, path, body):
        if path == "/api/heartbeat":
            self.dirty = bool(body.get("dirty"))
            return {"ok": True, "pending": self.sanitize_pending(),
                    "head_revn": self.store.head_revn(),
                    "events_seq": self.events.seq}
        if path == "/api/save":
            scenes = body.get("scenes") or {}
            if not isinstance(scenes, dict):
                return err(400, "scenes must be {artifact_id: elements[]}")
            try:
                record = self.store.commit(
                    author="user", new_scenes=scenes,
                    base_revn=body.get("base_revn"),
                    selection=body.get("selection"),
                    user_note=body.get("note"),
                    fork_name=body.get("fork_name"))
            except StaleError as e:
                return err(409, str(e), stale=True,
                           head_revn=e.head_revn)
            self.dirty = False
            forked = record["branch"] != "main" and \
                record["revn"] == self.store.head_revn() and \
                any(b["name"] == record["branch"] and b["head"] ==
                    record["revn"] for b in self.store.registry["branches"])
            self.events.append("save", revn=record["revn"],
                               short_id=record["short_id"],
                               branch=record["branch"],
                               headline=record["summary"]["headline"],
                               tripwires=len(record["tripwires"]),
                               forked=bool(body.get("fork_name")) or
                               (forked and record["branch"] != "main"))
            self.flush_deferred()
            return {"ok": True, "revn": record["revn"],
                    "short_id": record["short_id"],
                    "branch": record["branch"],
                    "summary": record["summary"],
                    "tripwires": record["tripwires"]}
        if path == "/api/apply":
            cadence = self.store.config.get("canvas_updates", "per-round")
            ops = body.get("ops") or []
            drawing = [o for o in ops if o.get("op") in
                       ("add", "mod", "del", "reorder")]
            pin_only = not drawing and not body.get("create")
            # pin-only revisions change nothing visual — they never hold
            # behind the banner (feel-test finding, Appendix A row 13)
            hold = (self.dirty or cadence == "pulled") and not pin_only
            if hold:
                entry = self.queue_pending(body, pin_only)
                self.events.append("agent_pending", pending_id=entry["id"],
                                   pin_only=pin_only,
                                   reason="dirty canvas" if self.dirty
                                   else "pulled cadence")
                return {"ok": True, "queued": True,
                        "pending_id": entry["id"],
                        "reason": ("the user has unsaved edits"
                                   if self.dirty else
                                   "cadence is set to pulled"),
                        "hint": "The revision will land behind the pending-"
                                "revision banner; the user chooses when."}
            try:
                record, pin_only = self.store.apply_batch(body)
            except BatchError as e:
                return err(422, "\n".join(e.errors))
            except StaleError as e:
                return err(409, str(e), stale=True, head_revn=e.head_revn)
            self.events.append("agent_revision", revn=record["revn"],
                               short_id=record["short_id"],
                               pin_only=pin_only,
                               headline=record["summary"]["headline"])
            aid = body.get("artifact") or (body.get("create") or {}).get("id")
            scene = self.store.scenes.get(aid, []) if aid else []
            lint = project_lint(self.store.p, scene,
                                self.store.registry) if aid else \
                {"errors": [], "warnings": [], "notes": []}
            return {"ok": True, "revn": record["revn"],
                    "short_id": record["short_id"],
                    "summary": record["summary"],
                    "pin_only": pin_only,
                    "intent_echo": intent_echo(body.get("ops") or [], scene),
                    "layout_errors": lint["errors"],
                    "layout_warnings": lint["warnings"],
                    "layout_notes": lint["notes"]}
        if path == "/api/pending/resolve":
            pid = body.get("id")
            action = body.get("action")
            with self.lock:
                entry = next((p for p in self.pending if p["id"] == pid),
                             None)
            if entry is None:
                return err(404, "no pending revision %r" % pid)
            if action == "apply_now":
                try:
                    record = self.commit_pending(entry)
                except (BatchError, StaleError) as e:
                    return err(422, str(e))
                return {"ok": True, "revn": record["revn"],
                        "changes": {aid: part.get("changes") or []
                                    for aid, part in
                                    (record.get("artifacts") or {}).items()}}
            if action == "after_save":
                with self.lock:
                    entry["deferred"] = True
                return {"ok": True, "deferred": True}
            return err(400, "action must be apply_now or after_save")
        if path == "/api/pins/answer":
            try:
                pin = self.store.answer_pin(body.get("id"),
                                            body.get("answer") or "")
            except BatchError as e:
                return err(404, "\n".join(e.errors))
            self.events.append("pin_answer", pin=pin["id"],
                               question=pin["question"],
                               answer=pin["answer"],
                               artifact=pin.get("artifact"))
            return {"ok": True, "pin": pin}
        if path == "/api/tripwires/answer":
            try:
                tw = self.store.answer_tripwire(body.get("id"),
                                                body.get("answer") or "")
            except BatchError as e:
                return err(404, "\n".join(e.errors))
            self.events.append("tripwire_answer", tripwire=tw["id"],
                               question=tw.get("question"),
                               answer=tw["answer"],
                               changed=tw.get("changed"),
                               sibling=tw.get("sibling"))
            return {"ok": True, "tripwire": tw}
        if path == "/api/checkout":
            revn = body.get("revn")
            if revn is None:
                self.store.checkout_revn = None
                self.events.append("checkout_live")
                return {"ok": True, "checkout_revn": None}
            if revn not in self.store.records:
                return err(404, "no save record with revn %r" % revn)
            self.store.checkout_revn = revn
            self.events.append("checkout", revn=revn)
            return {"ok": True, "checkout_revn": revn}
        if path == "/api/branch/switch":
            try:
                b = self.store.switch_branch(body.get("name"))
            except BatchError as e:
                return err(404, "\n".join(e.errors))
            self.events.append("branch_switch", branch=b["name"],
                               head=b["head"])
            return {"ok": True, "branch": b}
        if path == "/api/branch/archive":
            try:
                b = self.store.set_archived(body.get("name"),
                                            body.get("archived", True))
            except BatchError as e:
                return err(400, "\n".join(e.errors))
            self.events.append("branch_archive", branch=b["name"],
                               archived=b["archived"])
            return {"ok": True, "branch": b}
        if path == "/api/rollback/accept":
            rec = self.store.accept_rollback()
            if rec is None:
                return err(400, "no rollback pending")
            self.events.append("reconciliation", revn=rec["revn"],
                               short_id=rec["short_id"],
                               headline=rec["summary"]["headline"])
            return {"ok": True, "revn": rec["revn"]}
        if path == "/api/suggest-view":
            self.events.append("suggest_view",
                               text=body.get("text") or "",
                               concept=body.get("concept"))
            return {"ok": True}
        if path == "/api/config":
            patch = body.get("patch") or {}
            allowed = {"canvas_updates", "narration_altitude",
                       "deletion_conversation", "nudge_after_minutes"}
            bad = set(patch.keys()) - allowed
            if bad:
                return err(400, "config keys not settable here: %s"
                           % ", ".join(sorted(bad)))
            self.store.config.update(patch)
            cfg, _ = validate_config(self.store.config)
            self.store.config = cfg
            write_json(self.project.config_path, cfg)
            self.events.append("config_changed", patch=patch)
            return {"ok": True, "config": cfg}
        if path == "/api/screenshot/request":
            with self.lock:
                self.shot_seq += 1
                sid = self.shot_seq
                self.shot_requests[sid] = {
                    "artifact": body.get("artifact"),
                    "status": "waiting", "path": None}
            return {"ok": True, "id": sid}
        if path == "/api/screenshot/complete":
            sid = body.get("id")
            req = self.shot_requests.get(sid)
            if req is None:
                return err(404, "no screenshot request %r" % sid)
            data_url = body.get("data_url") or ""
            m = re.match(r"data:image/png;base64,(.+)", data_url)
            if not m:
                return err(400, "data_url must be a base64 PNG data URL")
            import base64
            self.project.shots_dir.mkdir(parents=True, exist_ok=True)
            out = self.project.shots_dir / ("shot-%d.png" % sid)
            out.write_bytes(base64.b64decode(m.group(1)))
            req["status"] = "done"
            req["path"] = str(out)
            return {"ok": True, "path": str(out)}
        if path == "/api/shutdown":
            threading.Thread(target=self._shutdown, daemon=True).start()
            return {"ok": True, "bye": True}
        return err(404, "unknown endpoint %s" % path)

    def _shutdown(self):
        time.sleep(0.2)
        try:
            if self.project.state_path.exists():
                self.project.state_path.unlink()
        except OSError:
            pass
        if self.httpd is not None:
            self.httpd.shutdown()

    def watchdog(self):
        while True:
            time.sleep(30)
            idle = time.time() - self.last_activity
            if idle > IDLE_MINUTES * 60:
                self.log("idle watchdog: no activity for %.0f min — "
                         "shutting down" % (idle / 60))
                self._shutdown()
                return


class _Err(Exception):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        super().__init__(payload.get("error", ""))


def err(status, message, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    raise _Err(status, payload)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "wysiwyg-grilling/%d" % PROTOCOL_VERSION

        def log_message(self, fmt, *args):
            app.log("http: " + (fmt % args))

        def _send_json(self, obj, status=200):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            app.last_activity = time.time()
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            q = urllib.parse.parse_qs(parsed.query)
            try:
                if path == "/api/health":
                    return self._send_json({
                        "ok": True, "protocol_version": PROTOCOL_VERSION,
                        "project": app.project.name(),
                        "pid": os.getpid()})
                if path == "/api/state":
                    return self._send_json(app.api_state())
                if path == "/api/events":
                    since = int(q.get("since", ["0"])[0])
                    timeout = min(float(q.get("timeout", ["0"])[0]), 50.0)
                    evs = (app.events.wait_since(since, timeout)
                           if timeout > 0 else app.events.since(since))
                    return self._send_json({"ok": True, "events": evs,
                                            "seq": app.events.seq})
                if path.startswith("/api/artifact/"):
                    aid = path[len("/api/artifact/"):]
                    at = q.get("at", [None])[0]
                    if at is None:
                        els = app.store.scenes.get(aid)
                        if els is None:
                            return self._send_json(
                                {"ok": False,
                                 "error": "unknown artifact %r" % aid}, 404)
                        return self._send_json({"ok": True, "elements": els})
                    state = app.store.state_at(int(at))
                    part = state.get(aid)
                    return self._send_json(
                        {"ok": True,
                         "elements": (part or {}).get("elements", [])})
                if path.startswith("/api/save-record/"):
                    revn = int(path.rsplit("/", 1)[1])
                    rec = app.store.records.get(revn)
                    if rec is None:
                        return self._send_json(
                            {"ok": False, "error": "no revn %d" % revn}, 404)
                    return self._send_json({"ok": True, "record": rec})
                if path.startswith("/render/"):
                    # minimal rasterization surface for the snapshot CLI's
                    # headless tier: no app, no fonts race — just the SVG
                    aid = path[len("/render/"):]
                    raw = aid.endswith(".svg")
                    if raw:
                        aid = aid[:-4]
                    els = app.store.scenes.get(aid)
                    if els is None:
                        return self._send_json(
                            {"ok": False,
                             "error": "unknown artifact %r" % aid}, 404)
                    svg, _, _ = render_svg(
                        els, title=(app.store.artifact_meta.get(aid) or
                                    {}).get("name") or aid)
                    if raw:
                        body = svg.encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "image/svg+xml")
                    else:
                        body = ("<!doctype html><html><head><meta "
                                "charset='utf-8'><style>body{margin:0;"
                                "background:#fdfcf8}</style></head><body>"
                                + svg + "</body></html>").encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type",
                                         "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return None
                return self._serve_static(path)
            except _Err as e:
                return self._send_json(e.payload, e.status)
            except BrokenPipeError:
                pass
            except Exception as e:  # noqa: BLE001
                app.log("GET %s failed: %s\n%s"
                        % (path, e, traceback.format_exc()))
                return self._send_json({"ok": False, "error": str(e)}, 500)

        def do_POST(self):
            app.last_activity = time.time()
            path = urllib.parse.urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 50 * 1024 * 1024:
                    return self._send_json(
                        {"ok": False, "error": "body too large"}, 413)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    return self._send_json(
                        {"ok": False, "error": "invalid JSON body"}, 400)
                result = app.handle_post(path, body)
                return self._send_json(result)
            except _Err as e:
                return self._send_json(e.payload, e.status)
            except BrokenPipeError:
                pass
            except Exception as e:  # noqa: BLE001
                app.log("POST %s failed: %s\n%s"
                        % (path, e, traceback.format_exc()))
                return self._send_json({"ok": False, "error": str(e)}, 500)

        def _serve_static(self, path):
            if path == "/":
                path = "/index.html"
            root = app.web_root.resolve()
            target = (root / path.lstrip("/")).resolve()
            if root not in target.parents and target != root:
                return self._send_json(
                    {"ok": False, "error": "not found"}, 404)
            if not target.is_file():
                # SPA fallback
                target = root / "index.html"
                if not target.is_file():
                    return self._send_json(
                        {"ok": False,
                         "error": "web bundle missing — run the maintainer "
                                  "build (see README) or reinstall the "
                                  "skill"}, 404)
            data = target.read_bytes()
            mime = MIME.get(target.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            if target.name == "index.html":
                self.send_header("Cache-Control", "no-store")
            else:
                self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def run_server(project, port=0):
    app = ServerApp(project)
    app.start_catch_up()
    handler = make_handler(app)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    app.httpd = httpd
    actual_port = httpd.server_address[1]
    url = "http://127.0.0.1:%d/" % actual_port
    state = {
        "pid": os.getpid(),
        "port": actual_port,
        "url": url,
        "source_hash": own_source_hash(),
        "protocol_version": PROTOCOL_VERSION,
        "project": str(project.root),
        "started_at": now_iso(),
        "catchup_revn": app.catchup_record,
        "rollback": app.store.rollback,
        "events_log": str(project.events_path),
    }
    write_json(project.state_path, state)
    app.events.append("server_started", url=url,
                      catchup_revn=app.catchup_record)
    app.log("serving %s for %s" % (url, project.root))
    threading.Thread(target=app.watchdog, daemon=True).start()
    try:
        httpd.serve_forever()
    finally:
        try:
            if project.state_path.exists():
                project.state_path.unlink()
        except OSError:
            pass
    return app


# ---------------------------------------------------------------------------
# CLI — the agent's surface. All errors here are LLM-addressed: they say
# what went wrong and what to do next.
# ---------------------------------------------------------------------------

def http_json(url, payload=None, timeout=10.0):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def server_alive(state):
    if not state:
        return False
    try:
        health = http_json(state["url"] + "api/health", timeout=2.0)
        return bool(health.get("ok"))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def print_kv(**kw):
    for k, v in kw.items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        print("%s=%s" % (k.upper(), v))


def frontend_stamp_warning(project):
    """Maintainer convenience: if frontends/wysiwyg-grilling/ source exists
    beside the skill, compare its hash against the committed build stamp."""
    scripts = Path(__file__).resolve().parent
    stamp_path = scripts / "web" / "build-stamp.json"
    repo_root = scripts.parent.parent.parent
    src = repo_root / "frontends" / "wysiwyg-grilling" / "src"
    if not stamp_path.exists():
        return "web bundle has no build-stamp.json — rebuild the bundle"
    if not src.is_dir():
        return None
    try:
        stamp = read_json(stamp_path)
        h = hashlib.sha1()
        for f in sorted(src.rglob("*")):
            if f.is_file():
                h.update(f.name.encode("utf-8"))
                h.update(f.read_bytes())
        current = h.hexdigest()
        if current != stamp.get("source_hash"):
            return ("committed web bundle is STALE (frontend source changed "
                    "since the last build) — run `npm run build` in "
                    "frontends/wysiwyg-grilling/ and re-copy the bundle")
    except (OSError, ValueError):
        return "build-stamp.json unreadable"
    return None


def cmd_start(args):
    project = Project(args.project)
    project.ensure_tree()
    state = project.read_state()
    reused = False
    if server_alive(state):
        if state.get("protocol_version") != PROTOCOL_VERSION:
            # protocol mismatch: LLM-addressed (spec §6.1)
            die("ERROR=protocol mismatch: running server speaks v%s, this "
                "canvas.py speaks v%d. Stop the old server (canvas.py stop) "
                "and start again; if this repeats, tell the user to update "
                "the skill and restart their session."
                % (state.get("protocol_version"), PROTOCOL_VERSION), 4)
        reused = True
    else:
        if project.state_path.exists():
            try:
                project.state_path.unlink()
            except OSError:
                pass
        logf = io.open(str(project.log_path), "a")
        kwargs = {}
        if os.name == "nt":
            # detach on Windows: new process group, no console window
            kwargs["creationflags"] = 0x00000200 | 0x00000008
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()),
             "--project", str(project.root), "serve"],
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            close_fds=True, **kwargs)
        deadline = time.time() + 20
        state = None
        while time.time() < deadline:
            state = project.read_state()
            if server_alive(state):
                break
            time.sleep(0.25)
        else:
            die("ERROR=server did not come up within 20s. Read the log at "
                "%s and report the failure; grilling continues verbally."
                % project.log_path, 3)
    url = state["url"]
    if not args.no_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — best-effort everywhere
            pass
    warning = frontend_stamp_warning(project)
    if reused and state.get("source_hash") not in (None, own_source_hash()):
        print("SERVER_CODE_STALE=the running server was started from an "
              "older canvas.py — run `canvas.py stop` then `start` to pick "
              "up the update (safe: all state is on disk)")
    print_kv(url=url, port=state["port"], pid=state["pid"],
             protocol_version=state["protocol_version"],
             project=str(project.root),
             project_knowledge=str(project.pk),
             events_log=state.get("events_log", str(project.events_path)),
             reused=str(reused).lower(),
             catchup_revn=state.get("catchup_revn"),
             rollback=state.get("rollback"),
             stamp_warning=warning)
    return 0


def cmd_status(args):
    project = Project(args.project)
    state = project.read_state()
    if not server_alive(state):
        print_kv(running="false", project=str(project.root),
                 protocol_version=PROTOCOL_VERSION)
        return 1
    try:
        st = http_json(state["url"] + "api/state", timeout=5.0)
    except (OSError, ValueError, urllib.error.URLError) as e:
        print_kv(running="false", error=str(e))
        return 1
    print_kv(running="true", url=state["url"], pid=state["pid"],
             protocol_version=st.get("protocol_version"),
             project=st.get("project"), revn=st.get("revn"),
             head=st.get("head"), head_revn=st.get("head_revn"),
             round=st.get("round"), whose_move=st.get("whose_move"),
             artifacts=",".join(sorted((st.get("artifacts") or {}).keys())),
             dirty=str(st.get("dirty", False)).lower(),
             pending=len(st.get("pending") or []),
             open_pins=len([p for p in st.get("pins") or []
                            if p.get("status") == "open"]),
             open_tripwires=len([t for t in st.get("tripwires") or []
                                 if t.get("status") == "open"]),
             view_debt="; ".join(
                 "%s owes %s" % (c.get("name") or c.get("id"),
                                 ",".join(c["owed"]))
                 for c in st.get("concepts") or [] if c.get("owed"))
                 or "none",
             checkout_revn=st.get("checkout_revn"),
             rollback=st.get("rollback"),
             events_seq=st.get("events_seq"),
             events_log=st.get("events_log"))
    if state.get("protocol_version") != PROTOCOL_VERSION:
        print("WARNING=protocol mismatch: server v%s vs CLI v%d — restart "
              "the server (canvas.py stop && canvas.py start)"
              % (state.get("protocol_version"), PROTOCOL_VERSION))
    if state.get("source_hash") not in (None, own_source_hash()):
        print("SERVER_CODE_STALE=the running server was started from an "
              "older canvas.py — `canvas.py stop && canvas.py start` to "
              "pick up the update (safe: all state is on disk)")
    return 0


def cmd_stop(args):
    project = Project(args.project)
    state = project.read_state()
    if not server_alive(state):
        print_kv(stopped="false", reason="no running server")
        return 0
    try:
        http_json(state["url"] + "api/shutdown", payload={}, timeout=3.0)
    except (OSError, ValueError, urllib.error.URLError):
        pass
    # wait until it is actually gone — a `start` right after `stop` must
    # never "reuse" a dying server
    deadline = time.time() + 6
    while time.time() < deadline and server_alive(state):
        time.sleep(0.2)
    if server_alive(state):
        try:
            os.kill(state["pid"], 15)
        except (OSError, ProcessLookupError):
            pass
        time.sleep(0.5)
    try:
        if project.state_path.exists():
            project.state_path.unlink()
    except OSError:
        pass
    print_kv(stopped="true")
    return 0


def cmd_wait(args):
    """Tier-3 bounded long-poll. Self-terminates strictly under Bash's 600s
    ceiling. Exit 0 = events printed; exit 3 = timed out with none."""
    project = Project(args.project)
    state = project.read_state()
    if not server_alive(state):
        die("ERROR=no running server — run canvas.py start first. Grilling "
            "can continue verbally in the meantime.", 3)
    since = args.since
    if since is None:
        try:
            st = http_json(state["url"] + "api/events?since=0&timeout=0",
                           timeout=5.0)
            since = st.get("seq", 0)
        except (OSError, ValueError, urllib.error.URLError):
            since = 0
    deadline = time.time() + min(args.timeout, 540)
    got = False
    while time.time() < deadline:
        chunk = min(50.0, max(deadline - time.time(), 0.1))
        try:
            resp = http_json(
                "%sapi/events?since=%d&timeout=%.0f"
                % (state["url"], since, chunk), timeout=chunk + 10)
        except (OSError, ValueError, urllib.error.URLError) as e:
            die("ERROR=server went away mid-wait (%s). Run canvas.py start "
                "to relaunch; state is safe on disk." % e, 3)
        for ev in resp.get("events") or []:
            print(json.dumps(ev, ensure_ascii=False))
            since = max(since, ev["seq"])
            got = True
        if got:
            return 0
    print("TIMEOUT=no user activity in %ds — waiting is working time: do "
          "queued doc work, or ask via chat/AskUserQuestion. Never treat "
          "silence as consent." % args.timeout)
    return 3


def cmd_apply(args):
    project = Project(args.project)
    if args.file:
        try:
            batch = read_json(args.file)
        except (OSError, ValueError) as e:
            die("ERROR=could not read ops file %s: %s" % (args.file, e), 2)
    else:
        try:
            batch = json.loads(sys.stdin.read())
        except ValueError as e:
            die("ERROR=stdin was not valid JSON: %s. Pass a batch like "
                "{\"base_revn\": N, \"artifact\": \"id\", \"ops\": [...]}"
                % e, 2)
    state = project.read_state()
    if server_alive(state):
        try:
            resp = http_json(state["url"] + "api/apply", payload=batch,
                             timeout=30.0)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except ValueError:
                payload = {"error": str(e)}
            die("ERROR=%s" % payload.get("error", str(e)), 5)
        except (OSError, ValueError, urllib.error.URLError) as e:
            die("ERROR=server unreachable (%s) — run canvas.py start" % e, 3)
        if resp.get("queued"):
            print_kv(queued="true", pending_id=resp.get("pending_id"),
                     reason=resp.get("reason"), hint=resp.get("hint"))
            return 0
        print_kv(revn=resp.get("revn"), short_id=resp.get("short_id"),
                 pin_only=str(resp.get("pin_only", False)).lower(),
                 headline=(resp.get("summary") or {}).get("headline"))
        for line in resp.get("intent_echo") or []:
            print("ECHO=%s" % line)
        for e in resp.get("layout_errors") or []:
            print("LAYOUT_ERROR=%s" % e)
        for w in resp.get("layout_warnings") or []:
            print("LAYOUT_WARNING=%s" % w)
        for n in resp.get("layout_notes") or []:
            print("LAYOUT_NOTE=%s" % n)
        return 0
    # degraded path: no server — apply directly against the files
    store = Store(project)
    try:
        record, pin_only = store.apply_batch(batch)
    except (BatchError, StaleError) as e:
        die("ERROR=%s" % e, 5)
    events = EventLog(project.events_path)
    events.append("agent_revision", revn=record["revn"],
                  short_id=record["short_id"], pin_only=pin_only,
                  headline=record["summary"]["headline"], offline=True)
    print_kv(revn=record["revn"], short_id=record["short_id"],
             pin_only=str(pin_only).lower(),
             headline=record["summary"]["headline"], offline="true")
    aid = batch.get("artifact") or (batch.get("create") or {}).get("id")
    scene = store.scenes.get(aid, []) if aid else []
    for line in intent_echo(batch.get("ops") or [], scene):
        print("ECHO=%s" % line)
    if aid:
        lint = project_lint(project, scene, store.registry)
        for e in lint["errors"]:
            print("LAYOUT_ERROR=%s" % e)
        for w in lint["warnings"]:
            print("LAYOUT_WARNING=%s" % w)
        for n in lint["notes"]:
            print("LAYOUT_NOTE=%s" % n)
    return 0


def cmd_snapshot(args):
    """Tiered snapshot: connected tab → self-launched headless system
    browser → stdlib SVG. Always yields something; exit 0 only with a
    validated image (or the explicit SVG fallback). Prints KEY=VALUE:
    TIER, PNG or SVG, VALID, plus NOTE lines."""
    project = Project(args.project)
    store = Store(project)
    aid = args.artifact
    if aid is None:
        if len(store.scenes) == 1:
            aid = next(iter(store.scenes))
        else:
            die("ERROR=--artifact required (known: %s)"
                % (", ".join(sorted(store.scenes)) or "none"), 2)
    if aid not in store.scenes:
        die("ERROR=unknown artifact %r (known: %s)"
            % (aid, ", ".join(sorted(store.scenes)) or "none"), 2)
    els = store.scenes[aid]
    svg, want_w, want_h = render_svg(
        els, title=(store.artifact_meta.get(aid) or {}).get("name") or aid)
    outdir = Path(args.out).parent if args.out else project.runtime_dir
    outdir.mkdir(parents=True, exist_ok=True)
    out_png = Path(args.out) if args.out else \
        outdir / ("%s-r%d.png" % (aid, store.head_revn()))

    state = project.read_state()
    alive = server_alive(state)

    # ---- tier 1: connected browser tab (true Excalidraw rendering) ----
    if alive and not args.no_tab:
        for attempt in (1, 2):
            try:
                resp = http_json(state["url"] + "api/screenshot/request",
                                 payload={"artifact": aid}, timeout=5.0)
            except (OSError, ValueError, urllib.error.URLError):
                break
            sid = resp.get("id")
            deadline = time.time() + args.tab_timeout
            shot = None
            while time.time() < deadline:
                time.sleep(0.4)
                st = http_json(state["url"] + "api/state", timeout=5.0)
                if not [r for r in st.get("screenshot_requests") or []
                        if r["id"] == sid]:
                    shots = sorted(project.shots_dir.glob(
                        "shot-%d.png" % sid))
                    if shots:
                        shot = shots[0]
                    break
            if shot is None:
                break  # no client answered — go headless, don't retry
            data = shot.read_bytes()
            # bpp floor applies to TIER 1 ONLY: the corruption it guards
            # against (fonts-race noise in the tab's exportToBlob, audit
            # Part 3: bad ≤0.032, good ≥0.124) can't occur on the
            # deterministic tier-2 route — and sparse-but-valid renders
            # legitimately sit below any floor that would catch it
            ok, why = validate_png(data, min_bpp=0.05)
            if ok:
                shutil.copyfile(str(shot), str(out_png))
                print_kv(tier="1", png=str(out_png), valid="true",
                         detail=why)
                return 0
            print("NOTE=tab export attempt %d invalid (%s)%s"
                  % (attempt, why,
                     " — retrying once" if attempt == 1 else ""))
        print("NOTE=no valid tab export — falling back to headless render")

    # ---- tier 2: system browser, headless, against the SVG surface ----
    browsers = find_browsers() if not args.no_headless else []
    if browsers:
        # work in $HOME so snap-confined browsers (private /tmp) can see
        # both the input html and the output png
        workdir = Path.home() / ".cache" / "wysiwyg-grilling"
        workdir.mkdir(parents=True, exist_ok=True)
        work_png = workdir / out_png.name
        if alive:
            url = state["url"] + "render/" + aid
        else:
            html = ("<!doctype html><html><head><meta charset='utf-8'>"
                    "<style>body{margin:0;background:#fdfcf8}</style>"
                    "</head><body>" + svg + "</body></html>")
            tmp_html = workdir / ("%s-render.html" % aid)
            tmp_html.write_text(html, encoding="utf-8")
            url = tmp_html.resolve().as_uri()
        win_w = max(min(want_w, 3000), 320)
        win_h = max(min(want_h, 2000), 200)
        for browser in browsers:
            if work_png.exists():
                work_png.unlink()
            cmd = [browser, "--headless=new", "--disable-gpu",
                   "--no-sandbox", "--disable-dev-shm-usage",
                   "--hide-scrollbars", "--force-device-scale-factor=1",
                   "--screenshot=%s" % work_png,
                   "--window-size=%d,%d" % (win_w, win_h), url]
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=40)
                if work_png.exists():
                    data = work_png.read_bytes()
                    ok, why = validate_png(data, win_w, win_h, min_bpp=0)
                    if ok:
                        if work_png != out_png:
                            shutil.copyfile(str(work_png), str(out_png))
                        print_kv(tier="2", png=str(out_png), valid="true",
                                 detail=why,
                                 browser=os.path.basename(browser))
                        return 0
                    print("NOTE=%s render invalid (%s)"
                          % (os.path.basename(browser), why))
                else:
                    print("NOTE=%s produced no file (rc=%d%s)"
                          % (os.path.basename(browser), proc.returncode,
                             (", " + proc.stderr.decode("utf-8", "replace")
                              .strip()[:120]) if proc.stderr else ""))
            except (subprocess.TimeoutExpired, OSError) as e:
                print("NOTE=%s failed: %s" % (os.path.basename(browser), e))
    elif not args.no_headless:
        print("NOTE=no chromium/chrome/edge/brave found — SVG fallback")

    # ---- tier 3: the SVG itself, honestly labeled ---------------------
    out_svg = out_png.with_suffix(".svg")
    out_svg.write_text(svg, encoding="utf-8")
    print_kv(tier="3", svg=str(out_svg), valid="true",
             note="approximate rendering (system fonts, no sketch style) — "
                  "geometry-faithful, good for legibility only")
    return 0


def cmd_serve(args):
    project = Project(args.project)
    project.ensure_tree()
    run_server(project, port=args.port)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="canvas.py",
        description="WYSIWYG Grilling — local canvas server + agent CLI")
    parser.add_argument("--project", default=".",
                        help="target project root (default: cwd)")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("start", help="launch or reuse the detached server")
    p.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("status", help="server health + project state")
    p = sub.add_parser("stop", help="shut the server down")
    p = sub.add_parser("wait", help="long-poll for save/answer events")
    p.add_argument("--since", type=int, default=None,
                   help="event seq to wait after (default: now)")
    p.add_argument("--timeout", type=int, default=540,
                   help="max seconds to wait (hard-capped at 540)")
    p = sub.add_parser("apply", help="apply a typed op batch (agent draws)")
    p.add_argument("--file", help="JSON batch file (default: stdin)")
    for name, hlp in (("snapshot", "PNG/SVG of an artifact — tiered: "
                       "connected tab → headless system browser → SVG"),
                      ("screenshot", "alias of snapshot (back-compat)")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--artifact", default=None)
        p.add_argument("--out", default=None,
                       help="output PNG path (default: runtime dir)")
        p.add_argument("--tab-timeout", type=int, default=8,
                       help="seconds to wait for a connected tab (tier 1)")
        p.add_argument("--no-tab", action="store_true",
                       help="skip tier 1 (deterministic headless render)")
        p.add_argument("--no-headless", action="store_true",
                       help="skip tier 2 (no browser launch)")
    p = sub.add_parser("serve", help="(internal) run server in foreground")
    p.add_argument("--port", type=int, default=0)

    args = parser.parse_args(argv)
    handlers = {
        "start": cmd_start, "status": cmd_status, "stop": cmd_stop,
        "wait": cmd_wait, "apply": cmd_apply, "screenshot": cmd_snapshot,
        "snapshot": cmd_snapshot,
        "serve": cmd_serve,
    }
    if args.cmd not in handlers:
        parser.print_help()
        return 2
    return handlers[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
