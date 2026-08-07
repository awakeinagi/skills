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
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
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
    "sequence": {"tier": "extended", "priority": 10},
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
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s or fallback


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
    # a bound label lying entirely outside its container is detached — reglue
    by_id = {e["id"]: e for e in kept}
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


def text_dims(text, font_size):
    lines = (text or "").split("\n")
    width = max((len(l) for l in lines), default=1) * font_size * 0.6
    height = max(len(lines), 1) * font_size * 1.25
    return (max(int(width), 10), max(int(height), int(font_size * 1.25)))


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
        m = re.match(r"#([0-9a-fA-F]{6})$", bg)
        if m:
            r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
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


def route_arrow(arrow, src, dst):
    """Compute explicit geometry for a bound arrow (bindings do NOT route —
    feel-prototype finding) and attach start/end bindings."""
    scx = src["x"] + src.get("width", 0) / 2.0
    scy = src["y"] + src.get("height", 0) / 2.0
    dcx = dst["x"] + dst.get("width", 0) / 2.0
    dcy = dst["y"] + dst.get("height", 0) / 2.0
    x1, y1 = edge_anchor(src, dcx, dcy)
    x2, y2 = edge_anchor(dst, scx, scy)
    gap = 6
    arrow["x"], arrow["y"] = x1, y1
    arrow["width"] = abs(x2 - x1)
    arrow["height"] = abs(y2 - y1)
    arrow["points"] = [[0, 0], [x2 - x1, y2 - y1]]
    arrow["startBinding"] = {"elementId": src["id"], "focus": 0, "gap": gap}
    arrow["endBinding"] = {"elementId": dst["id"], "focus": 0, "gap": gap}
    for node in (src, dst):
        bl = [b for b in (node.get("boundElements") or [])
              if not (b.get("id") == arrow["id"] and b.get("type") == "arrow")]
        bl.append({"id": arrow["id"], "type": "arrow"})
        node["boundElements"] = bl


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
        mx = el["x"] + sum(p[0] for p in pts) / len(pts)
        my = el["y"] + sum(p[1] for p in pts) / len(pts)
        label["x"] = mx - label.get("width", 0) / 2
        label["y"] = my - label.get("height", 0) / 2
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
                    if el.get("type") not in ("arrow", "line"):
                        errors.append("op %d: %r only applies to arrows" % (i, attr))
                        continue
                    endpoint = resolve(value, i, "rewire")
                    if endpoint is None:
                        continue
                    sb = el.get("startBinding") or {}
                    eb = el.get("endBinding") or {}
                    src = index.get((sb or {}).get("elementId"))
                    dst = index.get((eb or {}).get("elementId"))
                    old = src if attr == "from" else dst
                    if old is not None:
                        old["boundElements"] = [
                            b for b in (old.get("boundElements") or [])
                            if b.get("id") != el["id"]]
                    if attr == "from":
                        src = endpoint
                    else:
                        dst = endpoint
                    if src is not None and dst is not None:
                        route_arrow(el, src, dst)
                        recenter_label(els, el)
                elif attr == "text" and el.get("type") == "text":
                    el["text"] = value
                    el["originalText"] = value
                    el["width"], el["height"] = text_dims(value,
                                                          el.get("fontSize", 16))
                elif attr == "customData":
                    cd = dict(el.get("customData") or {})
                    cd.update(value or {})
                    el["customData"] = cd
                else:
                    el[attr] = value
                    if attr in ("x", "y", "width", "height"):
                        recenter_label(els, el)
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
                pin_registry.append({"id": pid, "question": q, "target": target})
        elif kind == "resolve_pin":
            # tolerant of a missing element: the registry write-through in
            # apply_batch is the durable half; a pin whose ❓ was already
            # deleted must still be resolvable (never strand state)
            el = index.get(op.get("id"))
            if el is not None:
                cd = dict(el.get("customData") or {})
                cd["status"] = "resolved"
                el["customData"] = cd
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
    """container id -> bound label text"""
    out = {}
    for e in els:
        if e.get("type") == "text" and e.get("containerId"):
            out[e["containerId"]] = e.get("text", "")
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
                        e[a["attr"]] = a["to"]
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
                elif is_entity(container):
                    F("entity_renamed", container["id"],
                      **{"from": a["from"], "to": a["to"]},
                      glossary_challenge=True)
    return facts


# ---------------------------------------------------------------------------
# mechanical summary (verb counts, salience headline, suppression)
# ---------------------------------------------------------------------------

SALIENCE = ["rewired", "relationship_rewired", "type_changed", "screen_added",
            "screen_deleted", "entity_renamed", "renamed", "label_renamed",
            "branch_added", "step_added", "entity_added", "added",
            "step_deleted", "entity_deleted", "deleted", "regrouped",
            "label_added", "transition_added", "transition_deleted",
            "relationship_added", "relationship_deleted", "annotated",
            "pin_added", "priority_changed", "moved", "resized", "reordered",
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

def lint_layout(els):
    """Cheap legibility lint for headless agents (who can't see their own
    drawing): arrow labels wider than their arrow's run, and overlapping
    shapes. Returns warning strings — advisory, never blocking."""
    warnings = []
    labels = label_map(els)
    ix = {e["id"]: e for e in els}
    for e in els:
        if e.get("type") in ("arrow", "line") and e.get("points"):
            run = (e["points"][-1][0] ** 2 + e["points"][-1][1] ** 2) ** 0.5
            lbl = next((t for t in els if t.get("containerId") == e["id"]
                        and t.get("type") == "text"), None)
            if lbl is not None and run < lbl.get("width", 0) + 24:
                warnings.append(
                    "label %r is wider than its arrow's %dpx run (%s) — "
                    "spread the endpoints or shorten the label"
                    % (lbl.get("text", "")[:30], int(run), e["id"]))
    shapes = [e for e in els if e.get("type") in
              ("rectangle", "diamond", "ellipse")
              and role_of(e) not in ("label", "pin")]
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
                        "%s (%s) and %s (%s) overlap — separate them"
                        % (a["id"], labels.get(a["id"], a["id"]),
                           b["id"], labels.get(b["id"], b["id"])))
    return warnings


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
                self.registry["round"] = self.registry.get("round", 0) + 1
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
            for h in hits:
                for s in siblings:
                    entry = {"mapping": self._mapping_key(m), "changed": h,
                             "sibling": s, "kind": "divergence"}
                    out.append(entry)
                    reg_entry = dict(entry)
                    reg_entry.update({"id": "tw-%d-%d" % (revn, len(out)),
                                      "save": revn, "status": "open"})
                    self.registry["tripwires"].append(reg_entry)
        return out

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
                         "unviewed": False}
                    reg["concepts"].append(c)
                if op.get("name"):
                    c["name"] = op["name"]
                if op.get("glossary") is not None:
                    c["glossary"] = op["glossary"]
                for v in op.get("views") or []:
                    if v not in c["views"]:
                        c["views"].append(v)
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
                idx = op.get("index")
                if not isinstance(idx, int) or idx >= len(reg["mappings"]):
                    errors.append("registry op %d: annotate_mapping needs a "
                                  "valid mapping `index` (see model.json "
                                  "mappings order)" % i)
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
                              "resolve_tripwire, decline, set_round)"
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
                                     "views": [aid]})

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
                    "round": self.registry.get("round", 0)})
            # resolve_pin writes through to the registry — the canvas element
            # and model.json must never disagree about a pin's status
            resolved = {o.get("id") for o in ops if o.get("op") == "resolve_pin"}
            for p in self.registry["pins"]:
                if p["id"] in resolved and p.get("status") in ("open",
                                                               "answered"):
                    p["status"] = "resolved"
            self._save_registry()
            return record, pin_only

    # -- pins -------------------------------------------------------------
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
        self.seq = self.events[-1]["seq"] if self.events else 0

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
            lint = lint_layout(self.store.scenes.get(aid, [])) if aid else []
            return {"ok": True, "revn": record["revn"],
                    "short_id": record["short_id"],
                    "summary": record["summary"],
                    "pin_only": pin_only,
                    "layout_warnings": lint}
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
        for w in resp.get("layout_warnings") or []:
            print("LAYOUT_WARNING=%s" % w)
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
    for w in lint_layout(store.scenes.get(aid, [])) if aid else []:
        print("LAYOUT_WARNING=%s" % w)
    return 0


def cmd_screenshot(args):
    project = Project(args.project)
    state = project.read_state()
    if not server_alive(state):
        die("ERROR=no running server — run canvas.py start first.", 3)
    try:
        resp = http_json(state["url"] + "api/screenshot/request",
                         payload={"artifact": args.artifact}, timeout=5.0)
    except (OSError, ValueError, urllib.error.URLError) as e:
        die("ERROR=request failed: %s" % e, 3)
    sid = resp.get("id")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(0.5)
        st = http_json(state["url"] + "api/state", timeout=5.0)
        waiting = [r for r in st.get("screenshot_requests") or []
                   if r["id"] == sid]
        if not waiting:
            shots = sorted(project.shots_dir.glob("shot-%d.png" % sid))
            if shots:
                print_kv(screenshot=str(shots[0]))
                return 0
    die("ERROR=no browser answered the screenshot request within %ds — the "
        "canvas page is probably not open. The canvas is at %s; ask the "
        "user to open it, or skip the screenshot (it is context, never "
        "truth)." % (args.timeout, state["url"]), 3)


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
    p = sub.add_parser("screenshot", help="PNG of an artifact via the browser")
    p.add_argument("--artifact", default=None)
    p.add_argument("--timeout", type=int, default=20)
    p = sub.add_parser("serve", help="(internal) run server in foreground")
    p.add_argument("--port", type=int, default=0)

    args = parser.parse_args(argv)
    handlers = {
        "start": cmd_start, "status": cmd_status, "stop": cmd_stop,
        "wait": cmd_wait, "apply": cmd_apply, "screenshot": cmd_screenshot,
        "serve": cmd_serve,
    }
    if args.cmd not in handlers:
        parser.print_help()
        return 2
    return handlers[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
