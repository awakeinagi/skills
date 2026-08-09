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
import contextlib
import copy
import hashlib
import io
import json
import math
import os
import re
import shutil
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
# render_svg's canvas ground; also the backing painted under arrow
# labels, which ride the stroke since v0.6 (see arrow_label_anchor)
SVG_GROUND = "#fdfcf8"
# Facts that can put two mapped views out of agreement about MEANING,
# and so are worth asking "divergence, or should it propagate?" about.
# Presentation-only verbs are deliberately absent: in the v0.5
# assessment every one of the session's 20 divergence tripwires was the
# agent's own tooltip and layout edits, and it eventually spent two
# `kinds` annotations scoping mappings to `moved` just to stop the
# recurrence. Moving a box 40px is not a disagreement (R2-6).
DIVERGENCE_VERBS = frozenset({
    # naming
    "renamed", "label_renamed", "entity_renamed", "relationship_relabeled",
    # wiring
    "rewired", "relationship_rewired", "actor_reassigned",
    "cardinality_changed",
    # content and state
    "value_changed", "state_toggled", "attribute_added",
    "attribute_removed", "type_changed", "party_kind_changed",
    "ownership_changed", "sync_changed", "priority_changed",
    "activation_changed",
    # loss
    "deleted", "entity_deleted", "relationship_deleted", "step_deleted",
    "transition_deleted", "actor_deleted", "message_deleted",
    "lane_deleted", "screen_deleted",
})
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
    # autoResize records the client's intent flip on a deliberate text
    # width-drag (without it the flip hits disk but never the record →
    # replay/disk divergence); name is a frame's native label; link
    # carries in-canvas navigation; locked is the settled-structure
    # guard — all four must replay or catch_up mints phantom records.
    "autoResize", "name", "link", "locked",
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
    "budgets": {},
    "waives": {},
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
    _require(reg, "budgets", {}, issues, "REG-007", "registry")
    _require(reg, "waives", {}, issues, "REG-007", "registry")
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


def content_fingerprint(els):
    """Order-insensitive scene fingerprint for repair attribution (WP2).

    ``scene_hash`` hashes elements in list order, but disk order and
    replayed-history order legitimately differ (z-order normalization),
    so raw-vs-history comparison needs identity by CONTENT: elements
    sorted by id, deleted ones dropped, dumped canonically.

    Args:
        els: Scene elements (non-dicts tolerated and skipped).

    Returns:
        Hex digest, or None when the scene is too corrupt to fingerprint.
    """
    def canon(v):
        # 150 vs 150.0 must fingerprint identically — the same int/float
        # representation drift _route_sig formats away (dict equality
        # says equal; json.dumps says different)
        if isinstance(v, bool):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, dict):
            return {k: canon(x) for k, x in v.items()}
        if isinstance(v, list):
            return [canon(x) for x in v]
        return v

    try:
        # roundness rides with the derived set: the write path computes
        # it from point count while replay keeps creation-time values
        # (the re-stamp never lands in records — customData is
        # deliberately non-significant), so disk and history disagree
        # about it on every re-routed arrow, permanently. The system
        # already declares it non-significant for diffs; drift detection
        # must agree, or every such project phantoms a reconciliation.
        skip = set(VOLATILE_ATTRS) | {"boundElements", "roundness"}
        live = []
        for e in els:
            if not isinstance(e, dict) or e.get("isDeleted"):
                continue
            live.append((str(e.get("id")),
                         canon({k: v for k, v in e.items()
                                if k not in skip})))
        live.sort(key=lambda t: t[0])
        blob = json.dumps([d for _, d in live], sort_keys=True,
                          default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 — corrupt raw scene
        return None


def referential_findings(raw_scenes, registry, artifact_ids=None):
    """Report every reference whose target is gone — BEFORE any repair.

    The r4 headline: *nothing validates a reference after the thing it
    refers to is gone*. Three silences compounded — the dangling-binding
    lint was unreachable because ``validate_scene`` repaired the scene in
    memory first (r4-7), ``cross_lint`` skipped mapping members it could
    not resolve, and orphaned notes had no check at all. This pass runs
    on the RAW on-disk scenes at load, so the report survives the repair
    (report-and-repair: the loader may fix, but never silently).

    Args:
        raw_scenes: {artifact_id: elements} as read from disk, un-repaired.
            Non-dict elements are tolerated and skipped (corrupt scenes
            still get the findings their readable parts support).
        registry: The registry (mappings are read; may be None).
        artifact_ids: Known artifact ids for ``links_to`` checks; defaults
            to ``raw_scenes``' keys.

    Returns:
        {scope: {"errors": [...], "warnings": [...], "notes": [...]}} —
        scope is an artifact id or ``"registry"``; only scopes with
        findings appear.
    """
    out = {}
    known_aids = set(artifact_ids if artifact_ids is not None
                     else raw_scenes.keys())

    def add(scope, tier, msg):
        out.setdefault(scope, {"errors": [], "warnings": [],
                               "notes": []})[tier].append(msg)

    ids_by_aid = {}
    for aid, els in raw_scenes.items():
        ids_by_aid[aid] = {e.get("id") for e in els
                           if isinstance(e, dict) and e.get("id")}
    for aid, els in sorted(raw_scenes.items()):
        ids = ids_by_aid[aid]
        for e in els:
            if not isinstance(e, dict):
                continue
            if e.get("type") in ("arrow", "line"):
                for battr in ("startBinding", "endBinding"):
                    b = e.get(battr)
                    tgt = (b or {}).get("elementId")
                    if tgt and tgt not in ids:
                        side = "start" if battr == "startBinding" else "end"
                        add(aid, "errors",
                            "arrow %s binds %s at its %s point and that "
                            "element no longer exists — re-target the "
                            "binding, or delete the arrow with it"
                            % (e.get("id"), tgt, side))
            cd = e.get("customData") or {}
            anchor = cd.get("annotates")
            if anchor and anchor not in ids:
                add(aid, "notes",
                    "note %s annotates %s, which no longer exists — a "
                    "tombstone left on purpose is fine; say so, or "
                    "re-anchor/delete it (it will not survive tidy where "
                    "it stands)" % (e.get("id"), anchor))
            link = e.get("link") or ""
            if link.startswith("artifact:") and \
                    link[len("artifact:"):] not in known_aids:
                add(aid, "notes",
                    "%s links_to artifact %r, which does not exist"
                    % (e.get("id"), link[len("artifact:"):]))
    for m in (registry or {}).get("mappings") or []:
        for ref in m.get("elements") or []:
            if "#" not in ref:
                continue
            aid, eid = ref.split("#", 1)
            if aid in ids_by_aid and eid not in ids_by_aid[aid]:
                add("registry", "warnings",
                    "mapping %r member %s points at a deleted element — "
                    "re-map it, or tombstone the mapping"
                    % (m.get("concept"), ref))
    return out


# ---------------------------------------------------------------------------
# migrations — named sets on every owned JSON kind; snapshot before migrating
# ---------------------------------------------------------------------------

# Each entry: (name, fn(doc) -> doc). Files record applied names in
# doc["migrations"] (artifacts: doc["wysiwyg"]["migrations"]).
def _mig_config_0002(d):
    """Append autoResize/name/link/locked to significant_attrs
    (append-only — never clobber a customized list)."""
    sig = d.get("significant_attrs")
    if isinstance(sig, list):
        for a in ("autoResize", "name", "link", "locked"):
            if a not in sig:
                sig.append(a)
    return d


# Registry sections that belong to the BRANCH you are standing on, not
# to the project. Scenes have always been per-branch — `switch_branch`
# materialises them and deletes the artifacts the target lacks — while
# these stayed one global blob, so on a branch the registry asserted
# views that do not exist and printed VIEW_DEBT=none: the debt mechanism
# was branch-blind in the direction that SUPPRESSES work (v0.6
# assessment r3-17). Everything here is keyed on something branched:
# concepts own views, mappings and tripwires reference elements, pins ARE
# elements, and every waive/budget key names an artifact.
#
# What stays project-level: migrations, revn, head, branches — the shape
# of history itself, which no branch may disagree about.
BRANCH_SCOPED = ("concepts", "mappings", "declined", "pins", "tripwires",
                 "divergence_policies", "budgets", "waives")


def _mig_registry_0002(reg):
    """Stash the head branch's scope, so v0.6 projects open unchanged.

    Lossless: the sections stay at top level as the working copy of the
    branch you are on, and this only records them against that branch so
    a later switch has something to come back to.

    Args:
        reg: The registry document.

    Returns:
        The same document, with the head branch carrying a `scope`.
    """
    head = reg.get("head") or "main"
    for b in reg.get("branches") or []:
        if b.get("name") == head and "scope" not in b:
            b["scope"] = {k: copy.deepcopy(reg.get(k))
                          for k in BRANCH_SCOPED if k in reg}
    return reg


MIGRATIONS = {
    "config": [("0001-baseline", lambda d: d),
               ("0002-significant-attrs", _mig_config_0002)],
    "registry": [("0001-baseline", lambda d: d),
                 ("0002-branch-scoped-registry", _mig_registry_0002)],
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
        # The client owns `text` as the RENDERED (wrapped) string and
        # `originalText` as the unwrapped source — the opposite of the
        # server doctrine (store unwrapped, let the client wrap). De-wrap:
        # when the two agree modulo whitespace, the unwrapped form wins,
        # so wrap-inserted newlines never persist into scenes, facts, or
        # headlines. Then pin originalText := text so replayed state and
        # disk state always agree (this stays a pure function of the
        # input element — the replay/scene-hash invariant depends on it).
        orig = el.get("originalText")
        txt = el.get("text") or ""
        if isinstance(orig, str) and orig != txt and \
                " ".join(orig.split()) == " ".join(txt.split()):
            el["text"] = orig
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
    and arrow bindings so it never needs diffing and always reconstructs.

    Since v0.8 the same rule covers a server-routed arrow's ``roundness``
    (None for a straight 2-point path, curved otherwise — route_arrow's
    own rule): it was derived at WRITE time but replay kept the
    creation-time value, so the file and its own replayed history
    disagreed permanently — every fixture with a re-routed arrow minted a
    phantom out-of-session reconciliation at load (WP2; the r4 headline
    shape, found in remediation)."""
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
                 "frame", "freedraw", "image"}

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


# Per-character advance widths (ems) for the canvas font, measured from
# the vendored Nunito Latin subset with the CLIENT'S OWN engine (headless
# Chromium canvas.measureText at 100px, 2dp). The flat 0.6-em estimate
# this replaces truncated '62' at 24px to a 28px box the live editor
# wraps (needs ceil(28.8)) — the agent's snapshot then showed one line
# while the user's browser showed two, and both said VALID=true (r4-12,
# the R3-4 recurrence). Digits are exactly 0.6; W is 1.10; lowercase
# averages 0.51, so the flat factor was wrong in both directions.
NUNITO_ADVANCE = {
    " ": 0.26, "!": 0.23, '"': 0.4, "#": 0.6, "$": 0.6, "%": 0.93,
    "&": 0.7, "'": 0.23, "(": 0.33, ")": 0.33, "*": 0.45, "+": 0.6,
    ",": 0.23, "-": 0.43, ".": 0.23, "/": 0.29, "0": 0.6, "1": 0.6,
    "2": 0.6, "3": 0.6, "4": 0.6, "5": 0.6, "6": 0.6, "7": 0.6,
    "8": 0.6, "9": 0.6, ":": 0.23, ";": 0.23, "<": 0.6, "=": 0.6,
    ">": 0.6, "?": 0.45, "@": 0.95, "A": 0.73, "B": 0.68, "C": 0.67,
    "D": 0.75, "E": 0.59, "F": 0.55, "G": 0.73, "H": 0.76, "I": 0.26,
    "J": 0.33, "K": 0.63, "L": 0.55, "M": 0.86, "N": 0.74, "O": 0.77,
    "P": 0.64, "Q": 0.77, "R": 0.67, "S": 0.62, "T": 0.61, "U": 0.73,
    "V": 0.69, "W": 1.1, "X": 0.65, "Y": 0.6, "Z": 0.59, "[": 0.32,
    "\\": 0.29, "]": 0.32, "^": 0.6, "_": 0.5, "`": 0.36, "a": 0.53,
    "b": 0.59, "c": 0.46, "d": 0.59, "e": 0.53, "f": 0.34, "g": 0.59,
    "h": 0.57, "i": 0.24, "j": 0.24, "k": 0.51, "l": 0.3, "m": 0.86,
    "n": 0.57, "o": 0.56, "p": 0.59, "q": 0.59, "r": 0.36, "s": 0.48,
    "t": 0.36, "u": 0.56, "v": 0.52, "w": 0.84, "x": 0.53, "y": 0.52,
    "z": 0.47, "{": 0.36, "|": 0.27, "}": 0.36, "~": 0.6,
}
_ADVANCE_FALLBACK = 0.62


def _nunito_face_css(web_root):
    """@font-face CSS for the vendored Nunito Latin subset, if present.

    Args:
        web_root: The served web bundle root (fonts live under it).

    Returns:
        A ``@font-face`` rule with a server-relative URL, or "" when the
        bundle has no Nunito files (tier 2 then keeps sans-serif).
    """
    try:
        fonts = sorted((web_root / "fonts" / "Nunito")
                       .glob("Nunito-Regular-*.woff2"),
                       key=lambda p: -p.stat().st_size)
    except OSError:
        fonts = []
    if not fonts:
        return ""
    return ("@font-face{font-family:'Nunito';"
            "src:url('/fonts/Nunito/%s') format('woff2');}"
            % fonts[0].name)


def _display_width(line):
    """Advance width of a line in EMs against the vendored canvas font.

    CJK and fullwidth forms count 1.2em (their old two-cell treatment at
    the 0.6 factor — unchanged so wide scripts keep their headroom);
    unknown characters fall back to 0.62em.

    Args:
        line: One line of text (no newlines).

    Returns:
        The line's advance width in ems.
    """
    w = 0.0
    for ch in line:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 1.2
        else:
            w += NUNITO_ADVANCE.get(ch, _ADVANCE_FALLBACK)
    return w


def text_dims(text, font_size):
    lines = (text or "").split("\n")
    width = max((_display_width(l) for l in lines), default=0.4) \
        * font_size
    height = max(len(lines), 1) * font_size * 1.25
    # ceil + 2px: int() truncation is what wrapped '62' (28 < 28.8);
    # the pad absorbs sub-pixel rendering at autoResize:false widths
    width_px = int(width + 0.999) + 2
    return (max(width_px, 10), max(int(height), int(font_size * 1.25)))


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
        grown = lbl["height"] + 16 - container.get("height", 0)
        container["height"] = lbl["height"] + 16
        # growth can shove the box into a sibling below; nothing reflows
        # (that would need a constraint solver), so the overlap lint drops
        # its size threshold for auto-grown boxes and warns instead
        cd = container.setdefault("customData", {})
        cd["auto_grown"] = cd.get("auto_grown", 0) + grown


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
    if etype == "image":
        # images arrive from the canvas (paste/drop, with their file
        # blob) — an op-made image element would have no fileId to render
        errors.append("op %d: image elements arrive via the canvas "
                      "(paste/drop an image in the browser), not ops"
                      % index_hint)
        return []
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
    if spec.get("parent"):
        # declared containment (a card inside a shelf, a chart inside a
        # body region) — the overlap lint treats parent/child as nesting,
        # not collision
        custom["parent"] = spec["parent"]
    if spec.get("document"):
        # a readable document behind this element (report reader):
        # project_knowledge-relative markdown path, served via /api/doc
        custom["document"] = spec["document"]
    if spec.get("annotates"):
        # what a note is ABOUT (WP2): the anchor the orphan checks read.
        # The lint always advised setting it; nothing ever wrote it.
        custom["annotates"] = spec["annotates"]
    if spec.get("tooltip"):
        # hover-only markdown detail (v0.3): rendered by the client on
        # hover, managed from the element's right-click menu or ops;
        # never rendered into SVG/PNG exports
        custom["tooltip"] = str(spec["tooltip"])
    if spec.get("links_to"):
        # in-canvas navigation: clicking follows Excalidraw's native link
        # affordance; the client intercepts artifact: URIs
        el["link"] = "artifact:%s" % spec["links_to"]
    custom.setdefault("role", "annotation" if etype == "text" and not
                      spec.get("containerId") and spec.get("role") is None and
                      custom.get("role") is None else custom.get("role", "node"))
    if custom.get("role") is None:
        custom["role"] = "node"
    # every op-made element is agent work — user elements arrive via the
    # canvas and carry author:"user" (sticky notes, user pins) or nothing
    custom.setdefault("author", "agent")
    el["customData"] = custom
    for attr in ("strokeColor", "backgroundColor", "fillStyle", "strokeWidth",
                 "strokeStyle", "roughness", "opacity", "angle", "groupIds",
                 "frameId", "roundness"):
        if attr in spec:
            el[attr] = spec[attr]
    if spec.get("strategic") is not None:
        apply_strategic(el, spec["strategic"], errors, index_hint,
                        explicit_bg="backgroundColor" in spec)
    if etype == "text" and custom.get("role") == "annotation" and \
            "strokeColor" not in spec:
        # agent notes read green, user sticky notes read yellow (the demo's
        # authorship color language, v0.3) — explicit strokeColor wins
        el["strokeColor"] = "#5c8a5f"
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
        else:
            # Server-authored fixed-width text: pin autoResize off so the
            # client wraps INSIDE this box instead of re-measuring it out
            # to its natural single-line width (the 200→745px note bug).
            # `text` stays unwrapped (doctrine); height grows to hold the
            # wrapped line count.
            el["autoResize"] = False
            wrapped = wrap_label_text(el["text"],
                                      max(el["width"] - 8, 40), fs)
            el["height"] = max(el.get("height") or 0,
                               text_dims(wrapped, fs)[1])
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
            "customData": {"role": "label", "author": "agent"},
        })
        lbl["strokeColor"] = label_color
        fit_label_in(el, lbl)
        # Clamp the container to the client's minimum for bound text
        # (label height + 2×5px padding) — otherwise the browser bumps
        # it (28→30) on first load and the bump reads as a user resize.
        if el.get("height", 0) < lbl["height"] + 10:
            el["height"] = lbl["height"] + 10
        lbl["x"] = el["x"] + max((el["width"] - lbl["width"]) / 2, 4)
        lbl["y"] = el["y"] + max((el["height"] - lbl["height"]) / 2, 4)
        if spec.get("verticalAlign") in ("top", "bottom"):
            # titled panels pin the label to the header band; KPI tiles
            # pin it to the footer (v0.3) — recenter_label preserves both
            lbl["verticalAlign"] = spec["verticalAlign"]
            recenter_label([el, lbl], el)
        el["boundElements"] = list(el.get("boundElements") or [])
        el["boundElements"].append({"id": lbl_id, "type": "text"})
        out.append(lbl)
    # ---- composites -----------------------------------------------------
    if custom.get("kind") == "image" and etype == "rectangle":
        # low-fi image slot: the X-box (wireframe.md) — one op in, a
        # grouped rect + two decoration strokes out
        gid = el_id + "-grp"
        el["groupIds"] = [*(el.get("groupIds") or []), gid]
        w, h = el.get("width", 160), el.get("height", 60)
        for n, oy, pts in (("x1", 0, [[0, 0], [w, h]]),
                           ("x2", h, [[0, 0], [w, -h]])):
            lid = "%s-%s" % (el_id, n)
            existing_ids.add(lid)
            ln = dict(BASE_DEFAULTS)
            ln.update({
                "id": lid, "type": "line",
                "x": el["x"], "y": el["y"] + oy,
                "width": w, "height": h,
                "points": pts, "lastCommittedPoint": None,
                "startBinding": None, "endBinding": None,
                "startArrowhead": None, "endArrowhead": None,
                "elbowed": False, "strokeColor": "#b8b2a5",
                "groupIds": [gid],
                "customData": {"role": "decoration", "x_of": el_id,
                               "author": "agent"},
            })
            out.append(ln)
    attrs_list = spec.get("attributes")
    if isinstance(attrs_list, list) and attrs_list and \
            etype == "rectangle" and custom.get("kind") == "entity":
        # domain entity attribute rows (demo parity): the bound label
        # stays the EXACT glossary term (identity anchor); rows render
        # muted beneath it, one group with the entity
        gid = el_id + "-grp"
        el["groupIds"] = [*(el.get("groupIds") or []), gid]
        header_h, row_h = 32, 20
        need = header_h + row_h * len(attrs_list) + 8
        if el.get("height", 0) < need:
            el["height"] = need
        if len(out) > 1 and out[1].get("containerId") == el_id:
            out[1]["verticalAlign"] = "top"
            out[1]["y"] = el["y"] + 6
        for irow, attr_text in enumerate(attrs_list):
            rid = "%s-attr-%d" % (el_id, irow + 1)
            n2 = 2
            while rid in existing_ids:
                rid = "%s-attr-%d-%d" % (el_id, irow + 1, n2)
                n2 += 1
            existing_ids.add(rid)
            row = dict(BASE_DEFAULTS)
            row.update({
                "id": rid, "type": "text",
                "x": el["x"] + 10,
                "y": el["y"] + header_h + irow * row_h,
                "width": max(el.get("width", 160) - 20, 40),
                "height": row_h - 4,
                "text": str(attr_text), "originalText": str(attr_text),
                "fontSize": 12, "fontFamily": FONT_LEGIBLE,
                "textAlign": "left", "verticalAlign": "top",
                "lineHeight": 1.25, "containerId": None,
                "autoResize": False, "strokeColor": "#5c584d",
                "groupIds": [gid],
                "customData": {"role": "decoration", "attr_of": el_id,
                               "author": "agent"},
            })
            out.append(row)
    if custom.get("kind") == "kpi" and etype == "rectangle":
        # KPI/stat tile (v0.3): big value above, small name below — the
        # semantic NAME stays the bound label (renames narrate "Alpha",
        # never "+3.1% Alpha"); the value is a composed decoration text
        gid = el_id + "-grp"
        el["groupIds"] = [*(el.get("groupIds") or []), gid]
        if el.get("height", 0) < 64:
            el["height"] = 64
        if len(out) > 1 and out[1].get("containerId") == el_id:
            out[1]["verticalAlign"] = "bottom"
            recenter_label([el, out[1]], el)
        custom["value"] = str(spec.get("value") or "")
        out.append(_compose_kpi_value(el, custom["value"], existing_ids))
    if custom.get("kind") in ("checkbox", "toggle") and \
            etype == "rectangle":
        # form-control stand-ins (v0.3): a composed glyph carries the
        # state; customData.checked is the truth, `mod checked` flips it
        gid = el_id + "-grp"
        el["groupIds"] = [*(el.get("groupIds") or []), gid]
        if el.get("height", 0) < 28:
            el["height"] = 28
        custom["checked"] = bool(spec.get("checked", False))
        if len(out) > 1 and out[1].get("containerId") == el_id:
            out[1]["textAlign"] = "left"
        out.extend(_compose_control_glyph(el, custom["kind"],
                                          custom["checked"], existing_ids))
    if custom.get("kind") == "input" and etype == "rectangle" and \
            spec.get("value") is not None:
        # a settled value belongs ON the control (v0.5). `value` used to
        # be read only by kpi and slider, so an input was handed one and
        # silently dropped it — the admin console's schedule field read
        # "Run at" with no time in it, and nothing complained
        gid = el_id + "-grp"
        el["groupIds"] = [*(el.get("groupIds") or []), gid]
        if len(out) > 1 and out[1].get("containerId") == el_id:
            out[1]["textAlign"] = "left"
        custom["value"] = str(spec["value"])
        out.append(_compose_input_value(el, custom["value"], existing_ids))
    if custom.get("kind") == "slider" and etype == "rectangle":
        # slider stand-in (v0.3): track + thumb; customData.value (0–100)
        # positions the thumb, `mod value` moves it
        gid = el_id + "-grp"
        el["groupIds"] = [*(el.get("groupIds") or []), gid]
        if el.get("height", 0) < 44:
            el["height"] = 44
        if len(out) > 1 and out[1].get("containerId") == el_id:
            out[1]["verticalAlign"] = "top"
            recenter_label([el, out[1]], el)
        try:
            v = float(spec.get("value", 50))
        except (TypeError, ValueError):
            errors.append("op %d: slider `value` must be a number 0–100"
                          % index_hint)
            v = 50.0
        custom["value"] = max(0.0, min(100.0, v))
        out.extend(_compose_slider_glyph(el, custom["value"],
                                         existing_ids))
    if custom.get("kind") == "body" and etype == "rectangle":
        # wavy body-text stand-in (WP4/E-4): wireframe.md promised it in
        # three places across three versions and nothing ever drew a
        # wave — decoration lines rendered as ruler lines (v0.2 gap #12)
        out.extend(_compose_body_lines(el, existing_ids))
    return out


def _compose_body_lines(el, existing_ids):
    """Build a body-text block's wavy stand-in lines.

    Multi-point sine polylines (amplitude 1.5px, ~14px wavelength), one
    per ~16px of height, the last one short like a paragraph's final
    line. They carry ``body_of`` so ``reconcile_composed`` re-derives
    them when the block resizes.

    Args:
        el: The owner rectangle (`kind: "body"`).
        existing_ids: Live id set for id registration.

    Returns:
        List of decoration line elements.
    """
    gid = el["id"] + "-grp"
    w = max(el.get("width", 160) - 16, 24)
    h = max(el.get("height", 48), 16)
    n = max(2, int(h // 16))
    out = []
    for i in range(n):
        lw = w * (0.62 if i == n - 1 else 1.0)
        pts = []
        x = 0.0
        k = 0
        while x < lw:
            pts.append([round(x, 1), 1.5 if k % 2 else -1.5])
            x += 7.0
            k += 1
        pts.append([round(lw, 1), 0])
        ly = el["y"] + 8 + (h - 16) * (i / max(n - 1, 1))
        out.append(_deco(
            "%s-body%d" % (el["id"], i + 1), "body_of", el["id"], gid,
            existing_ids, type="line", x=el["x"] + 8, y=ly,
            width=lw, height=3, points=pts, lastCommittedPoint=None,
            startBinding=None, endBinding=None, startArrowhead=None,
            endArrowhead=None, elbowed=False,
            strokeColor="#b8b2a5", strokeWidth=1))
    return out


def _deco(el_id, owner_key, owner_id, gid, existing_ids, **props):
    """Build one composed decoration element for a kind composite.

    Args:
        el_id: Decoration element id (registered into existing_ids).
        owner_key: customData key naming the owner (e.g. "value_of").
        owner_id: The owner element's id (the key's value).
        gid: Group id shared with the owner element.
        existing_ids: Live id set — el_id is added to it.
        **props: Element fields merged over the decoration defaults.

    Returns:
        The decoration element dict.
    """
    existing_ids.add(el_id)
    d = dict(BASE_DEFAULTS)
    d.update({"id": el_id, "groupIds": [gid],
              "customData": {"role": "decoration", "author": "agent",
                             owner_key: owner_id}})
    d.update(props)
    return d


def _compose_kpi_value(el, value_text, existing_ids):
    """Build the big value row of a kind:"kpi" tile.

    Args:
        el: The owner rectangle (already positioned/sized).
        value_text: The value string ("" renders an empty slot).
        existing_ids: Live id set for id registration.

    Returns:
        The value text element (role: decoration, value_of: owner).
    """
    vw, vh = text_dims(value_text or " ", 24)
    return _deco(
        el["id"] + "-value", "value_of", el["id"],
        el["id"] + "-grp", existing_ids,
        type="text",
        x=el["x"] + max((el.get("width", 160) - vw) / 2, 4),
        y=el["y"] + 8, width=vw, height=vh,
        text=value_text, originalText=value_text,
        fontSize=24, fontFamily=FONT_LEGIBLE,
        textAlign="center", verticalAlign="top", lineHeight=1.25,
        containerId=None, autoResize=False, strokeColor="#1e1e1e")


def _compose_input_value(el, value_text, existing_ids):
    """Build the value row of a kind:"input" field.

    Right-aligned so the label reads on the left and the answer on the
    right, the way a settings row does — an input drawn with no value is
    a box whose whole point (what is it set to?) is missing.

    Args:
        el: The owner rectangle (already positioned/sized).
        value_text: The value string.
        existing_ids: Live id set for id registration.

    Returns:
        The value text element (role: decoration, value_of: owner).
    """
    fs = 14
    vw, vh = text_dims(value_text or " ", fs)
    return _deco(
        el["id"] + "-value", "value_of", el["id"],
        el["id"] + "-grp", existing_ids,
        type="text",
        x=el["x"] + max(el.get("width", 160) - vw - 10, 6),
        y=el["y"] + max((el.get("height", 40) - vh) / 2, 2),
        width=vw, height=vh,
        text=value_text, originalText=value_text,
        fontSize=fs, fontFamily=FONT_LEGIBLE,
        textAlign="left", verticalAlign="top", lineHeight=1.25,
        containerId=None, autoResize=False, strokeColor="#1e1e1e")


def _recompose_input_value(els, el, value_text):
    """Retext an input's composed value row in place (id stays stable).

    Args:
        els: Live element list (searched for the value row).
        el: The owner input rectangle.
        value_text: The new value string.
    """
    for t in els:
        if (t.get("customData") or {}).get("value_of") == el["id"]:
            t["text"] = value_text
            t["originalText"] = value_text
            vw, vh = text_dims(value_text or " ", t.get("fontSize", 14))
            t["width"], t["height"] = vw, vh
            t["x"] = el["x"] + max(el.get("width", 160) - vw - 10, 6)
            return
    els.append(_compose_input_value(el, value_text,
                                    {e["id"] for e in els}))


def _recompose_xbox(els, el):
    """Re-derive an image placeholder's X strokes from its box (v0.6).

    The `kind: image` composite mints two grouped `-x1`/`-x2` decoration
    lines whose points come from the rectangle's width/height AT CREATION
    TIME. A later `mod` that resizes the box left them at their old span,
    so the X overshot the box it belonged to and crossed whatever sat
    below — invisible to every lint, because `role: decoration` elements
    are filtered out of the geometry checks by construction. Found in the
    v0.5 assessment only because a human looked at a screenshot (R2-10).

    Args:
        els: The full element list.
        el: The placeholder rectangle whose geometry just changed.
    """
    w, h = el.get("width", 0), el.get("height", 0)
    for suffix, oy, pts in (("-x1", 0, [[0, 0], [w, h]]),
                            ("-x2", h, [[0, 0], [w, -h]])):
        ln = next((e for e in els if e["id"] == el["id"] + suffix), None)
        if ln is None or (ln.get("customData") or {}).get("role") != \
                "decoration":
            continue
        ln["x"], ln["y"] = el.get("x", 0), el.get("y", 0) + oy
        ln["width"], ln["height"] = w, h
        ln["points"] = pts


def _recompose_kpi_value(els, el, value_text):
    """Retext a KPI tile's composed value row in place (id stays stable —
    a delete/re-add would narrate phantom churn).

    Args:
        els: Live element list (searched for the value row).
        el: The owner KPI rectangle.
        value_text: The new value string.
    """
    for t in els:
        if (t.get("customData") or {}).get("value_of") == el["id"]:
            t["text"] = value_text
            t["originalText"] = value_text
            vw, vh = text_dims(value_text or " ", t.get("fontSize", 24))
            t["width"], t["height"] = vw, vh
            t["x"] = el["x"] + max((el.get("width", 160) - vw) / 2, 4)
            return
    # a kpi minted without a value has no row yet — compose one now
    els.append(_compose_kpi_value(el, value_text,
                                  {e["id"] for e in els}))


def _compose_control_glyph(el, kind, checked, existing_ids):
    """Build the state glyph of a checkbox or toggle.

    Args:
        el: The owner rectangle.
        kind: "checkbox" or "toggle".
        checked: Current state — checkbox gains a check stroke when True,
            a toggle's thumb sits right when True.
        existing_ids: Live id set for id registration.

    Returns:
        List of decoration elements (box/pill, optional check, thumb).
    """
    gid = el["id"] + "-grp"
    cy = el["y"] + el.get("height", 28) / 2.0
    out = []
    if kind == "checkbox":
        out.append(_deco(
            el["id"] + "-box", "box_of", el["id"], gid, existing_ids,
            type="rectangle", x=el["x"] + 8, y=cy - 8,
            width=16, height=16))
        if checked:
            out.append(_check_stroke(el, existing_ids))
    else:  # toggle: pill + thumb
        out.append(_deco(
            el["id"] + "-box", "box_of", el["id"], gid, existing_ids,
            type="rectangle", x=el["x"] + 8, y=cy - 8,
            width=TOGGLE_PILL_W, height=16, roundness={"type": 3}))
        out.append(_deco(
            el["id"] + "-thumb", "thumb_of", el["id"], gid,
            existing_ids, type="ellipse",
            x=_toggle_thumb_x(el, checked), y=cy - 6,
            width=12, height=12, backgroundColor="#1e1e1e",
            fillStyle="solid"))
    return out


def _check_stroke(el, existing_ids):
    """Build the check-mark stroke of a checked checkbox."""
    cy = el["y"] + el.get("height", 28) / 2.0
    return _deco(
        el["id"] + "-chk", "chk_of", el["id"], el["id"] + "-grp",
        existing_ids, type="line",
        x=el["x"] + 11, y=cy - 1, width=10, height=8,
        points=[[0, 0], [4, 4], [10, -6]], lastCommittedPoint=None,
        startBinding=None, endBinding=None,
        startArrowhead=None, endArrowhead=None, elbowed=False,
        strokeWidth=2)


# Toggle pill width. 28px gave the thumb 12px of travel — "merely
# subtle at export scale" (r4b's withdrawn-to-residue P3); 36px gives
# 20px, readable in a printed SVG.
TOGGLE_PILL_W = 36


def _toggle_thumb_x(el, checked):
    """X of a toggle thumb: left when off, right when on."""
    return el["x"] + (8 + TOGGLE_PILL_W - 12 - 2 if checked else 10)


def _compose_slider_glyph(el, value, existing_ids):
    """Build the track + thumb of a kind:"slider".

    Args:
        el: The owner rectangle.
        value: 0–100 position of the thumb along the track.
        existing_ids: Live id set for id registration.

    Returns:
        List of [track line, thumb rect] decoration elements.
    """
    gid = el["id"] + "-grp"
    w = el.get("width", 160)
    ty = el["y"] + el.get("height", 44) - 14
    track = _deco(
        el["id"] + "-track", "track_of", el["id"], gid, existing_ids,
        type="line", x=el["x"] + 10, y=ty, width=w - 20, height=0,
        points=[[0, 0], [w - 20, 0]], lastCommittedPoint=None,
        startBinding=None, endBinding=None,
        startArrowhead=None, endArrowhead=None, elbowed=False,
        strokeColor="#b8b2a5", strokeWidth=2)
    thumb = _deco(
        el["id"] + "-thumb", "thumb_of", el["id"], gid, existing_ids,
        type="rectangle", x=_slider_thumb_x(el, value), y=ty - 6,
        width=12, height=12, backgroundColor="#1e1e1e",
        fillStyle="solid")
    return [track, thumb]


def _slider_thumb_x(el, value):
    """X of a slider thumb for a 0–100 value along the inset track."""
    w = el.get("width", 160)
    return el["x"] + 10 + (w - 20 - 12) * (float(value) / 100.0)


def _interpret_user_composites(new_els, old_els):
    """Read a user's composite-part edits as STATE, then normalize.

    Delta-based, never state-based (a state-only rule would emit
    phantom unchecks on every copy-pasted control): a part counts as a
    gesture only when the BASE scene had it, the new scene changed it,
    and the host persists. The live case: a user deleted a checkbox's
    check stroke, the box rendered empty while ``checked`` stayed True,
    and the save narrated "no changes" (r4-8) — the agent called the
    message "actively misleading" and repaired it by hand. Now the
    gesture becomes ``checked: False`` + the stroke stays gone, the
    diff emits the high-signal ``state_toggled`` fact, and
    ``reconcile_composed`` re-derives the parts. Undo round-trips: the
    stroke restored flips ``checked`` back, with a fact.

    Args:
        new_els: The incoming normalized user scene, mutated in place.
        old_els: The base-state elements (replayed history).
    """
    old_ix = {e["id"]: e for e in old_els}
    new_ix = {e["id"]: e for e in new_els
              if isinstance(e, dict) and not e.get("isDeleted")}

    def part_of(els_ix, tag, host_id):
        return next((t for t in els_ix.values()
                     if (t.get("customData") or {}).get(tag) == host_id),
                    None)

    for el in list(new_els):
        if not isinstance(el, dict) or el.get("isDeleted"):
            continue
        cd = el.get("customData") or {}
        kind = cd.get("kind")
        if el["id"] not in old_ix:
            # a NEW host (paste, template insert, pre-compose add): parts
            # absent in both scenes → recompose silently, no gesture
            if kind in ("checkbox", "toggle", "slider", "kpi", "input",
                        "image", "entity"):
                reconcile_composed(new_els, None, None, el)
            continue
        if kind == "checkbox":
            had = part_of(old_ix, "chk_of", el["id"]) is not None
            has = part_of(new_ix, "chk_of", el["id"]) is not None
            if had != has and bool(cd.get("checked")) == had:
                el["customData"] = dict(cd, checked=has)
        elif kind == "toggle":
            thumb = part_of(new_ix, "thumb_of", el["id"])
            old_thumb = part_of(old_ix, "thumb_of", el["id"])
            if thumb is not None and old_thumb is not None and \
                    thumb.get("x") != old_thumb.get("x"):
                # flip only when the thumb centre crosses the track
                # midpoint — a 2px sloppy drag recomposes back instead
                # of toggling
                mid = el["x"] + 8 + TOGGLE_PILL_W / 2.0
                now_on = (thumb.get("x", 0) + 6) > mid
                if now_on != bool(cd.get("checked")):
                    el["customData"] = dict(cd, checked=now_on)
        elif kind == "slider":
            thumb = part_of(new_ix, "thumb_of", el["id"])
            old_thumb = part_of(old_ix, "thumb_of", el["id"])
            if thumb is not None and old_thumb is not None and \
                    thumb.get("x") != old_thumb.get("x"):
                w = el.get("width", 160)
                span = max(w - 20 - 12, 1)
                v = (thumb.get("x", 0) - el["x"] - 10) / span * 100.0
                v = round(max(0.0, min(100.0, v)), 1)
                el["customData"] = dict(cd, value=v)
        reconcile_composed(new_els, None, None, el)


def reconcile_composed(els, index, existing, el):
    """Re-derive a composed host's parts from its geometry and state.

    Composed elements were assembled once at creation and never
    maintained (WP3, r4-8/r4-10): only the X-box re-derived on resize
    and only bound labels re-centred on move — sliders, checkboxes, KPI
    centring, input insets and attribute rows all went stale, and a
    check stroke could contradict ``customData.checked`` with no
    invariant anywhere. This is the one invariant: parts are DERIVED —
    geometry from the host, presence from the state — so any path that
    changes either (agent mod, user save, the x-as-user driver) calls
    this and gets the same composite back.

    Args:
        els: The scene's element list, mutated in place.
        index: id -> element, kept in sync (may be None).
        existing: Set of ids in use, kept in sync (may be None).
        el: The composed host whose geometry or state changed.
    """
    if index is None:
        index = {e["id"]: e for e in els}
    if existing is None:
        existing = set(index.keys())
    cd = el.get("customData") or {}
    kind = cd.get("kind")
    if el.get("type") == "rectangle" and el.get("groupIds"):
        _recompose_xbox(els, el)
    if kind == "kpi":
        _recompose_kpi_value(els, el, str(cd.get("value") or ""))
    elif kind == "input":
        _recompose_input_value(els, el, str(cd.get("value") or ""))
    elif kind in ("checkbox", "toggle"):
        cy = el["y"] + el.get("height", 28) / 2.0
        box = next((t for t in els if (t.get("customData") or {})
                    .get("box_of") == el["id"]), None)
        if box is not None:
            box["x"], box["y"] = el["x"] + 8, cy - 8
        if kind == "checkbox":
            chk = next((t for t in els if (t.get("customData") or {})
                        .get("chk_of") == el["id"]), None)
            if bool(cd.get("checked")) and chk is None:
                made = _check_stroke(el, existing)
                els.append(made)
                index[made["id"]] = made
            elif not cd.get("checked") and chk is not None:
                els.remove(chk)
                index.pop(chk["id"], None)
                existing.discard(chk["id"])
            elif chk is not None:
                chk["x"], chk["y"] = el["x"] + 11, cy - 1
        else:
            for t in els:
                if (t.get("customData") or {}).get("thumb_of") == \
                        el["id"]:
                    t["x"] = _toggle_thumb_x(el, bool(cd.get("checked")))
                    t["y"] = cy - 6
    elif kind == "slider":
        try:
            v = max(0.0, min(100.0, float(cd.get("value") or 0)))
        except (TypeError, ValueError):
            v = 0.0
        w = el.get("width", 160)
        ty = el["y"] + el.get("height", 44) - 14
        for t in els:
            tcd = t.get("customData") or {}
            if tcd.get("track_of") == el["id"]:
                t["x"], t["y"] = el["x"] + 10, ty
                t["width"], t["height"] = w - 20, 0
                t["points"] = [[0, 0], [w - 20, 0]]
            elif tcd.get("thumb_of") == el["id"]:
                t["x"], t["y"] = _slider_thumb_x(el, v), ty - 6
    elif kind == "entity":
        rows = [t.get("text", "") for t in sorted(
                    (t for t in els if (t.get("customData") or {})
                     .get("attr_of") == el["id"]),
                    key=lambda t: t.get("y", 0))]
        if rows:
            _reset_attribute_rows(els, index, existing, el, rows)
    elif kind == "body":
        gone = [t for t in els if (t.get("customData") or {})
                .get("body_of") == el["id"]]
        for t in gone:
            els.remove(t)
            index.pop(t["id"], None)
            existing.discard(t["id"])
        for t in _compose_body_lines(el, existing):
            els.append(t)
            index[t["id"]] = t


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
    if mark == "authored":
        # hand-authored waypoints (mod points, v0.3): explicitly the
        # agent's/user's geometry — never re-routed, re-fanned, or
        # flattened. A rewire (mod from/to) re-routes because that is a
        # new path request.
        return False
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


def _route_candidates(src, dst):
    """Candidate polylines (absolute coords) from src's edge to dst's
    edge: straight (when roughly axis-aligned), both L-elbow
    orientations, and bounded Z-detours whose middle segment slides past
    an obstacle. Ordered by preference before scoring."""
    sx1, sy1 = src["x"], src["y"]
    sx2, sy2 = sx1 + src.get("width", 0), sy1 + src.get("height", 0)
    dx1, dy1 = dst["x"], dst["y"]
    dx2, dy2 = dx1 + dst.get("width", 0), dy1 + dst.get("height", 0)
    scx, scy = (sx1 + sx2) / 2.0, (sy1 + sy2) / 2.0
    dcx, dcy = (dx1 + dx2) / 2.0, (dy1 + dy2) / 2.0
    x_overlap = min(sx2, dx2) - max(sx1, dx1)
    y_overlap = min(sy2, dy2) - max(sy1, dy1)
    tdx, tdy = dcx - scx, dcy - scy
    cands = []
    if x_overlap > 12 or y_overlap > 12:
        x1, y1 = edge_anchor(src, dcx, dcy)
        x2, y2 = edge_anchor(dst, scx, scy)
        cands.append([(x1, y1), (x2, y2)])
    if abs(tdx) > 4 and abs(tdy) > 4:
        hx = sx2 if tdx > 0 else sx1            # side exit port
        vy = sy2 if tdy > 0 else sy1            # top/bottom exit port
        ex = dx1 if tdx > 0 else dx2            # side entry face
        ey = dy1 if tdy > 0 else dy2            # top/bottom entry face
        L_h = [(hx, scy), (dcx, scy), (dcx, ey)]
        L_v = [(scx, vy), (scx, dcy), (ex, dcy)]
        cands += [L_h, L_v] if abs(tdx) >= abs(tdy) else [L_v, L_h]
        for mx in ((hx + ex) / 2.0,
                   (dx1 - 24) if tdx > 0 else (dx2 + 24),
                   (sx2 + 24) if tdx > 0 else (sx1 - 24)):
            cands.append([(hx, scy), (mx, scy), (mx, dcy), (ex, dcy)])
        for my in ((vy + ey) / 2.0,
                   (dy1 - 24) if tdy > 0 else (dy2 + 24),
                   (sy2 + 24) if tdy > 0 else (sy1 - 24)):
            cands.append([(scx, vy), (scx, my), (dcx, my), (dcx, ey)])
    if not cands:  # degenerate (concentric) — straight fallback
        x1, y1 = edge_anchor(src, dcx, dcy)
        x2, y2 = edge_anchor(dst, scx, scy)
        cands.append([(x1, y1), (x2, y2)])
    # drop zero-length segments left by coincident waypoints
    out = []
    for path in cands:
        clean = [path[0]]
        for p in path[1:]:
            if abs(p[0] - clean[-1][0]) > 0.5 or abs(p[1] - clean[-1][1]) > 0.5:
                clean.append(p)
        if len(clean) >= 2:
            out.append(clean)
    if not out:
        # r4-11: coincident/concentric boxes collapse EVERY candidate —
        # including the degenerate fallback above, whose two edge anchors
        # resolve to the same point and get dropped by the zero-length
        # cleanup. The guard protected `cands`; the cleanup empties `out`
        # (a mirror-direction miss). Emit a non-degenerate stub so routing
        # is total: two boxes sharing a spot LOOK wrong, which is the
        # honest rendering, and the endpoint lint takes over once layout
        # separates them.
        ax, ay = edge_anchor(src, dcx, dcy)
        bx, by = edge_anchor(dst, scx, scy)
        if abs(ax - bx) <= 0.5 and abs(ay - by) <= 0.5:
            bx = ax + 24.0
        out.append([(ax, ay), (bx, by)])

    # A candidate whose ELBOW lands strictly inside either endpoint box
    # draws its final approach through the box interior — the L_v shape
    # did exactly that whenever the source centre sat within the
    # destination's x-span (found by the first mermaid-seeded dagre
    # layout, v0.8: the "no" branch entered its target through the top
    # face and ran 74px inside it to reach the left face). Endpoints
    # stay exempt: they live ON the border by construction. Straight
    # candidates and the degenerate stub carry no intermediates, so the
    # filter cannot empty the list for overlapping boxes; the `or out`
    # keeps routing total regardless.
    def interior(p, x1, y1, x2, y2):
        return x1 + 1 < p[0] < x2 - 1 and y1 + 1 < p[1] < y2 - 1

    routed = [path for path in out
              if not any(interior(p, sx1, sy1, sx2, sy2) or
                         interior(p, dx1, dy1, dx2, dy2)
                         for p in path[1:-1])]
    return routed or out


def _segs_cross(ax, ay, bx, by, cx, cy, dx, dy):
    """Do open segments AB and CD properly intersect? (orientation test)"""
    def orient(px, py, qx, qy, rx, ry):
        v = (qx - px) * (ry - py) - (qy - py) * (rx - px)
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    o1 = orient(ax, ay, bx, by, cx, cy)
    o2 = orient(ax, ay, bx, by, dx, dy)
    o3 = orient(cx, cy, dx, dy, ax, ay)
    o4 = orient(cx, cy, dx, dy, bx, by)
    return o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4)


def _self_loop_path(node):
    """Waypoints for a reflexive arrow: out the right edge, around the
    top-right corner, back in through the top edge.

    Deterministic — no candidates, no scoring — so every routing pass
    (add, rewire, F1, the obstacle pass, tidy) reproduces the same loop
    instead of collapsing it: before v0.8 a ``from == to`` arrow crashed
    apply outright (r4-11 — the straight "candidate" had zero length and
    the cleanup dropped it, leaving ``min()`` an empty sequence), so an
    entire relationship class ("a PipelineRun is a rerun of another
    PipelineRun") could not be drawn through the documented write path.

    Args:
        node: The element the arrow leaves and re-enters.

    Returns:
        Absolute ``[(x, y), ...]`` waypoints, right-edge exit to
        top-edge entry.
    """
    x1, y1 = node["x"], node["y"]
    w, h = node.get("width", 0), node.get("height", 0)
    x2 = x1 + w
    r = 28.0
    exit_y = y1 + max(h * 0.33, 8.0)
    entry_x = x2 - min(24.0, max(w * 0.25, 8.0))
    return [(x2, exit_y), (x2 + r, exit_y), (x2 + r, y1 - r),
            (entry_x, y1 - r), (entry_x, y1)]


def route_arrow(arrow, src, dst, obstacles=None, soft_obstacles=None,
                other_arrows=None):
    """Compute explicit geometry for a bound arrow (bindings do NOT route —
    feel-prototype finding) and attach start/end bindings. Off-axis pairs
    get orthogonal elbows (diagram-design §6.1); when the direct path
    crosses a foreign box, alternate orientations and bounded Z-detours
    are tried and the cleanest path wins (Phase-4 router — before this,
    dense fan-outs routed straight through neighbors and only the lint
    noticed). Total by design since v0.8: a reflexive pair takes the
    deterministic self-loop, and degenerate pairs route a stub rather
    than raising (r4-11).

    Args:
        arrow: The arrow element to (re)route in place.
        src: Source node element.
        dst: Destination node element.
        obstacles: Hard obstacles (foreign boxes) — dominant score term.
        soft_obstacles: Label/annotation bboxes (v0.3) — a path over a
            label is legal but reads badly; penalized below hard hits.
        other_arrows: [(id, x, y, points)] of other routed arrows —
            each crossing costs like a soft hit.
    """
    def hits(path):
        n = 0
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            for ob in obstacles or []:
                if ob.get("id") in (src.get("id"), dst.get("id")):
                    continue
                if _seg_hits_rect(ax, ay, bx, by, ob):
                    n += 1
        return n

    def soft(path):
        n = 0
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            for ob in soft_obstacles or []:
                if _seg_hits_rect(ax, ay, bx, by, ob):
                    n += 1
            for oid, ox, oy, opts in other_arrows or []:
                if oid == arrow.get("id"):
                    continue
                for p1, p2 in zip(opts, opts[1:]):
                    if _segs_cross(ax, ay, bx, by,
                                   ox + p1[0], oy + p1[1],
                                   ox + p2[0], oy + p2[1]):
                        n += 1
        return n

    def score(path):
        bends = len(path) - 2
        length = sum(abs(bx - ax) + abs(by - ay)
                     for (ax, ay), (bx, by) in zip(path, path[1:]))
        diag = any(abs(bx - ax) > 12 and abs(by - ay) > 12
                   for (ax, ay), (bx, by) in zip(path, path[1:]))
        # a true diagonal reads worse than one clean bend (layout.md §6.1)
        return (hits(path), soft(path), bends + (2 if diag else 0), length)

    if src is dst or src.get("id") == dst.get("id"):
        # reflexive relationship (v0.8) — deterministic, obstacle-blind
        path = _self_loop_path(src)
    else:
        path = min(_route_candidates(src, dst), key=score)
    x1, y1 = path[0]
    pts = [[px - x1, py - y1] for px, py in path]
    arrow["roundness"] = None if len(pts) == 2 else {"type": 2}
    gap = 6
    arrow["x"], arrow["y"] = x1, y1
    arrow["width"] = max(abs(p[0]) for p in pts)
    arrow["height"] = max(abs(p[1]) for p in pts)
    arrow["points"] = pts
    arrow["startBinding"] = {"elementId": src["id"],
                             "focus": binding_focus(src, x1, y1),
                             "gap": gap}
    arrow["endBinding"] = {"elementId": dst["id"],
                           "focus": binding_focus(dst, *path[-1]),
                           "gap": gap}
    _stamp_route(arrow)
    for node in (src, dst):
        bl = [b for b in (node.get("boundElements") or [])
              if not (b.get("id") == arrow["id"] and b.get("type") == "arrow")]
        bl.append({"id": arrow["id"], "type": "arrow"})
        node["boundElements"] = bl


def binding_focus(node, px, py):
    """Excalidraw-style focus for an attach point: signed center-offset
    ratio along the attached edge's cross axis, clamped to ±0.9. Focus 0
    means "aim at the center" — which is why every fanned endpoint used
    to snap back to one shared point on the first client re-render
    (v0.3 assessment: the lint's own advice was unfollowable)."""
    cx = node["x"] + node.get("width", 0) / 2.0
    cy = node["y"] + node.get("height", 0) / 2.0
    side = _edge_side(node, px, py)
    if side in ("left", "right"):
        half = max(node.get("height", 0) / 2.0, 1.0)
        f = (py - cy) / half
    elif side in ("top", "bottom"):
        half = max(node.get("width", 0) / 2.0, 1.0)
        f = (px - cx) / half
    else:
        return 0
    return max(-0.9, min(0.9, round(f, 3)))


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


def _fan_point(node, side, off):
    """Absolute attach point at offset `off` along a node's bbox side."""
    if side == "top":
        return (node["x"] + off, node["y"])
    if side == "bottom":
        return (node["x"] + off, node["y"] + node.get("height", 0))
    if side == "left":
        return (node["x"], node["y"] + off)
    return (node["x"] + node.get("width", 0), node["y"] + off)


def fan_attach_points(els):
    """Spread server-routed arrows sharing one node edge along it at
    L*k/(N+1) (diagram-design §6.4 — see references/layout.md): N arrows
    converging on a single point read as one arrow. Only touches arrows
    route_arrow marked `routed` — user geometry is never respaced."""
    ix = {e["id"]: e for e in els}
    ends = {}  # arrow id -> {"start": (x,y), "end": (x,y)}
    per_side = {}  # (node_id, side) -> [(arrow_id, which_end)]
    fan_slides = {}  # (arrow_id, which) -> (node, side, off, length)
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
        slide_of = {}
        for k, (aid, which) in enumerate(members, start=1):
            off = length * k / (n + 1)
            slide_of[aid, which] = (node, side, off, length)
            ends[aid][which] = _fan_point(node, side, off)
        fan_slides.update(slide_of)
    # obstacle set for the crossing check below (the router avoids foreign
    # boxes; the fan must not undo that work by sliding a segment into one)
    fan_obstacles = [e for e in els
                     if e.get("type") in ("rectangle", "diamond", "ellipse")
                     and (e.get("customData") or {}).get("role")
                     not in ("label", "pin", "decoration", "annotation")]

    def _fan_hits(a, ax, ay, pts):
        n = 0
        keep = {(a.get("startBinding") or {}).get("elementId"),
                (a.get("endBinding") or {}).get("elementId")}
        for p1, p2 in zip(pts, pts[1:]):
            for ob in fan_obstacles:
                if ob.get("id") in keep:
                    continue
                if _seg_hits_rect(ax + p1[0], ay + p1[1],
                                  ax + p2[0], ay + p2[1], ob):
                    n += 1
        return n

    def _elbowed_pts(a, sx, sy, exx, exy):
        old = a.get("points") or []
        if len(old) == 3:
            # preserve the elbow's orthogonality: the corner keeps sharing
            # its constant coordinate with whichever segment held it
            first_horizontal = abs(old[1][1] - old[0][1]) < 0.5
            corner = (exx, sy) if first_horizontal else (sx, exy)
            return [[0, 0], [corner[0] - sx, corner[1] - sy],
                    [exx - sx, exy - sy]]
        return [[0, 0], [exx - sx, exy - sy]]

    def _corner_interior(a, ax, ay, pts):
        # v0.8: the invariant router candidates already obey — an elbow
        # strictly inside either endpoint box draws the approach through
        # the box. The fan could undo the router's work: sliding one end
        # of an L along its edge drags the corner sideways, and on the
        # argus mermaid seed that put it 16px inside the SOURCE (the
        # `keep` exemption in _fan_hits hides endpoint boxes, rightly,
        # so this needs its own check).
        if len(pts) != 3:
            return False
        cx, cy = ax + pts[1][0], ay + pts[1][1]
        for key in ("startBinding", "endBinding"):
            node = ix.get((a.get(key) or {}).get("elementId"))
            if node is None:
                continue
            if node["x"] + 1 < cx < node["x"] + node.get("width", 0) - 1 \
                    and node["y"] + 1 < cy < \
                    node["y"] + node.get("height", 0) - 1:
                return True
        return False

    for aid, e2 in ends.items():
        a = ix[aid]
        (sx, sy), (exx, exy) = e2["start"], e2["end"]
        old = a.get("points") or []
        old_x, old_y = a["x"], a["y"]
        pts = _elbowed_pts(a, sx, sy, exx, exy)
        base_hits = _fan_hits(a, old_x, old_y, old)
        if _fan_hits(a, sx, sy, pts) > base_hits or \
                _corner_interior(a, sx, sy, pts):
            # the ideal L*k/(N+1) slot pushes a segment through a foreign
            # box — slide the fanned end along its edge in 12px steps
            # toward safety before giving up (v0.3: the old bail left a
            # shared attach point the lint then complained about with
            # advice nothing could execute)
            placed = False
            for which in ("start", "end"):
                slide = fan_slides.get((aid, which))
                if slide is None:
                    continue
                node, side, off, length = slide
                for delta in (12, -12, 24, -24):
                    off2 = off + delta
                    if not 8 <= off2 <= length - 8:
                        continue
                    p2 = _fan_point(node, side, off2)
                    s2, e2b = ((p2, (exx, exy)) if which == "start"
                               else ((sx, sy), p2))
                    pts2 = _elbowed_pts(a, s2[0], s2[1], e2b[0], e2b[1])
                    if _fan_hits(a, s2[0], s2[1], pts2) <= base_hits and \
                            not _corner_interior(a, s2[0], s2[1], pts2):
                        sx, sy = s2
                        exx, exy = e2b
                        pts = pts2
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                continue
        a["x"], a["y"] = sx, sy
        a["points"] = pts
        a["width"] = max(abs(p[0]) for p in pts)
        a["height"] = max(abs(p[1]) for p in pts)
        # focus follows the fanned point — focus 0 aims at the CENTER, so
        # the client's first re-render used to snap every fanned endpoint
        # straight back onto the shared anchor (v0.3). REPLACE the binding
        # dict: apply_ops copies elements shallowly, so an in-place write
        # would leak into the caller's scene even on a rejected batch.
        for which, key in (("start", "startBinding"), ("end", "endBinding")):
            b = a.get(key)
            node = ix.get((b or {}).get("elementId"))
            if b and node is not None:
                px, py = (sx, sy) if which == "start" else (exx, exy)
                nb = dict(b)
                nb["focus"] = binding_focus(node, px, py)
                a[key] = nb
        _stamp_route(a)
        recenter_label(els, a)


# ---------------------------------------------------------------------------
# op engine — one grammar, both directions (spec §6.2): the op vocabulary
# mirrors the save-record change vocabulary. validate-all-then-apply.
# ---------------------------------------------------------------------------

OP_KINDS = {"add", "mod", "del", "reorder", "pin", "resolve_pin", "registry"}

# Top-level element attributes `mod` may set directly. Everything else is
# either a special-cased attr (label, from/to, text, customData, strategic,
# kind, role, intent, points, name) or an error — the silent
# `el[attr] = value` catch-all was how `mod attrs.kind` no-oped with a
# success echo (live_test_2 B1).
MOD_ATTRS = {
    "x", "y", "width", "height", "angle", "frameId", "groupIds",
    "strokeColor", "backgroundColor", "fillStyle", "strokeWidth",
    "strokeStyle", "roughness", "opacity", "fontSize", "fontFamily",
    "textAlign", "startArrowhead", "endArrowhead", "roundness",
    "locked", "link", "containerId", "autoResize",
}


def arrow_label_anchor(arrow, label):
    """Where a bound label on an arrow actually lands, as (x, y).

    The client owns this placement and we cannot argue with it: a text
    whose `containerId` is an arrow is re-centred by Excalidraw on the
    **arc-length midpoint** of the path, discarding whatever x/y we
    stored. Until v0.6 the seeder anchored on the LONGEST SEGMENT's
    midpoint and lifted the label 8px perpendicular off the stroke
    (diagram-design §6.2). Those two rules agree on a straight arrow and
    diverge without bound on an elbow — which is how a label came to sit
    inside a foreign box on a canvas whose stored geometry showed no
    overlap at all, with the lint reading the stored copy and staying
    silent (v0.5 assessment R2-8). `render_svg` draws text at its stored
    position, so the exported SVG and the live canvas disagreed too.

    Everything that places or checks an arrow label now goes through
    here, so stored position, SVG and canvas agree by construction. The
    label rides the stroke: the client already breaks the arrow behind a
    bound label, and `render_svg` paints a ground-coloured backing to
    match — the "opaque background" half of connector rule 2, rather
    than the perpendicular offset the client will not honour. Note that
    a label's `backgroundColor` is NOT the lever here: it sits in
    `significant_attrs`, so writing it would narrate a style change on
    every reroute.

    Args:
        arrow: The arrow/line element, with `x`, `y` and `points`.
        label: The bound text element, read for `width`/`height`.

    Returns:
        `(x, y)` for the label's top-left corner.
    """
    pts = arrow.get("points") or [[0, 0]]
    segs, total = [], 0.0
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        ln = (dx * dx + dy * dy) ** 0.5
        segs.append((pts[i - 1], pts[i], ln))
        total += ln
    if not segs:
        mx, my = arrow.get("x", 0), arrow.get("y", 0)
    else:
        want, mx, my = total / 2, None, None
        for (a, b, ln) in segs:
            if want <= ln or ln <= 0:
                t = (want / ln) if ln else 0.0
                mx = arrow["x"] + a[0] + (b[0] - a[0]) * t
                my = arrow["y"] + a[1] + (b[1] - a[1]) * t
                break
            want -= ln
        if mx is None:                       # float drift past the end
            mx = arrow["x"] + segs[-1][1][0]
            my = arrow["y"] + segs[-1][1][1]
    return (mx - label.get("width", 0) / 2,
            my - label.get("height", 0) / 2)


def recenter_label(els, el):
    """Keep a bound label glued to its container after geometry changes:
    centered in shapes, on the arc midpoint of arrows.

    Args:
        els: The full element list (searched for the bound label).
        el: The container whose geometry changed.
    """
    label = next((t for t in els if t.get("type") == "text"
                  and t.get("containerId") == el["id"]), None)
    if label is None:
        return
    if el.get("type") in ("arrow", "line"):
        label["x"], label["y"] = arrow_label_anchor(el, label)
    else:
        label["x"] = el["x"] + max((el.get("width", 0) -
                                    label.get("width", 0)) / 2, 4)
        va = label.get("verticalAlign")
        if va == "top":
            # top-anchored labels (entities, titled panels) stay pinned to
            # the header band — centering them buries the term under its
            # attribute rows (v0.3 assessment bug)
            label["y"] = el["y"] + 6
        elif va == "bottom":
            # bottom-anchored labels (KPI tiles: big value above, small
            # name below) stay pinned to the footer band
            label["y"] = el["y"] + max(el.get("height", 0) -
                                       label.get("height", 0) - 6, 4)
        else:
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

    def obstacles():
        return [e for e in els
                if e.get("type") in ("rectangle", "diamond", "ellipse")
                and (e.get("customData") or {}).get("role")
                not in ("label", "pin", "decoration")]

    def soft_obstacles():
        # labels + annotations: legal to cross, ugly to cross (v0.3)
        return [e for e in els if e.get("type") == "text"
                and (e.get("containerId")
                     or role_of(e) == "annotation")]

    def arrow_paths():
        return [(e["id"], e.get("x", 0), e.get("y", 0),
                 e.get("points") or [])
                for e in els if e.get("type") == "arrow"
                and len(e.get("points") or []) >= 2]

    def route_ctx(arrow, src, dst, opi=None):
        # Envelope (v0.8, E-9): a routing failure must surface as an
        # ERROR naming the offending op — the r4-11 crash reached the
        # agent as a bare traceback, breaking SKILL.md's promise.
        try:
            route_arrow(arrow, src, dst, obstacles(),
                        soft_obstacles=soft_obstacles(),
                        other_arrows=arrow_paths())
        except Exception as e:  # noqa: BLE001 — totality backstop
            where = "op %d" % opi if opi is not None else "post-pass"
            errors.append(
                "%s: internal routing error on arrow %r (%s -> %s) — "
                "%s: %s. The batch was rejected whole; file this, and "
                "work around it by placing the endpoints apart before "
                "connecting them."
                % (where, arrow.get("id"), src.get("id"), dst.get("id"),
                   type(e).__name__, e))

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
                        route_ctx(arrow, src, dst, opi=i)
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
            old_x, old_y = el.get("x"), el.get("y")
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
                elif attr == "attributes":
                    # a domain entity's attribute rows. Accepted on `add`
                    # and nowhere else until v0.7, so the only way to
                    # amend them was delete + re-add — which mints a NEW
                    # element id and therefore drops that element's
                    # mappings and pins, and breaks the rename-keeps-the
                    # -id rule entity_renamed detection depends on. A
                    # domain model's attributes are exactly what a
                    # grilling session revises repeatedly (BUG-04).
                    if not isinstance(value, list) or \
                            any(not isinstance(v, str) for v in value):
                        errors.append("op %d (mod %s): `attributes` must "
                                      "be a list of strings"
                                      % (i, op.get("id")))
                        continue
                    if (el.get("customData") or {}).get("kind") != "entity":
                        errors.append("op %d (mod %s): `attributes` applies "
                                      "to domain entities (kind: entity)"
                                      % (i, op.get("id")))
                        continue
                    _reset_attribute_rows(els, index, existing, el, value)
                elif attr == "customData":
                    cd = dict(el.get("customData") or {})
                    cd.update(value or {})
                    el["customData"] = cd
                elif attr == "strategic":
                    apply_strategic(el, value, errors, i,
                                    explicit_bg="backgroundColor" in attrs)
                elif attr in ("kind", "role", "intent", "parent",
                              "document", "annotates"):
                    # terse customData keys, add-time parity — a top-level
                    # write here is read by nothing (B1's silent no-op)
                    cd = dict(el.get("customData") or {})
                    cd[attr] = value
                    el["customData"] = cd
                elif attr == "tooltip":
                    # hover-only markdown detail (v0.3); ""/null removes
                    cd = dict(el.get("customData") or {})
                    if value:
                        cd["tooltip"] = str(value)
                    else:
                        cd.pop("tooltip", None)
                    el["customData"] = cd
                elif attr == "verticalAlign":
                    if value not in ("top", "middle", "bottom"):
                        errors.append(
                            "op %d (mod %s): verticalAlign must be top, "
                            "middle, or bottom" % (i, op.get("id")))
                    elif el.get("type") == "text":
                        el["verticalAlign"] = value
                    else:
                        # on a shape this aligns its BOUND LABEL — the
                        # titled-panel / KPI pattern (v0.3)
                        lab = next((t for t in els
                                    if t.get("type") == "text" and
                                    t.get("containerId") == el["id"]),
                                   None)
                        if lab is None:
                            errors.append(
                                "op %d (mod %s): verticalAlign needs a "
                                "bound label — set `label` first"
                                % (i, op.get("id")))
                        else:
                            lab["verticalAlign"] = value
                            recenter_label(els, el)
                elif attr == "value":
                    k2 = (el.get("customData") or {}).get("kind")
                    if k2 == "kpi":
                        cd = dict(el.get("customData") or {})
                        cd["value"] = str(value or "")
                        el["customData"] = cd
                        _recompose_kpi_value(els, el, cd["value"])
                    elif k2 == "slider":
                        try:
                            v2 = max(0.0, min(100.0, float(value)))
                        except (TypeError, ValueError):
                            errors.append(
                                "op %d (mod %s): slider `value` must be "
                                "a number 0–100" % (i, op.get("id")))
                            continue
                        cd = dict(el.get("customData") or {})
                        cd["value"] = v2
                        el["customData"] = cd
                        for t in els:
                            if (t.get("customData") or {}) \
                                    .get("thumb_of") == el["id"]:
                                t["x"] = _slider_thumb_x(el, v2)
                    elif k2 == "input":
                        cd = dict(el.get("customData") or {})
                        cd["value"] = str(value)
                        el["customData"] = cd
                        _recompose_input_value(els, el, cd["value"])
                    else:
                        errors.append(
                            "op %d (mod %s): `value` only applies to "
                            "kind:kpi, kind:input and kind:slider elements "
                            "(this is %r)" % (i, op.get("id"), k2))
                elif attr == "checked":
                    k2 = (el.get("customData") or {}).get("kind")
                    if k2 not in ("checkbox", "toggle"):
                        errors.append(
                            "op %d (mod %s): `checked` only applies to "
                            "kind:checkbox and kind:toggle elements "
                            "(this is %r)" % (i, op.get("id"), k2))
                    else:
                        cd = dict(el.get("customData") or {})
                        cd["checked"] = bool(value)
                        el["customData"] = cd
                        if k2 == "checkbox":
                            chk = next(
                                (t for t in els
                                 if (t.get("customData") or {})
                                 .get("chk_of") == el["id"]), None)
                            if cd["checked"] and chk is None:
                                made = _check_stroke(el, existing)
                                els.append(made)
                                index[made["id"]] = made
                            elif not cd["checked"] and chk is not None:
                                els.remove(chk)
                                index.pop(chk["id"], None)
                        else:
                            for t in els:
                                if (t.get("customData") or {}) \
                                        .get("thumb_of") == el["id"]:
                                    t["x"] = _toggle_thumb_x(
                                        el, cd["checked"])
                elif attr == "links_to":
                    el["link"] = ("artifact:%s" % value) if value else None
                elif attr == "points":
                    if el.get("type") not in ("arrow", "line"):
                        errors.append("op %d (mod %s): `points` only applies "
                                      "to arrows/lines" % (i, op.get("id")))
                    elif not (isinstance(value, list) and len(value) >= 2 and
                              all(isinstance(p, (list, tuple)) and
                                  len(p) == 2 and
                                  all(isinstance(c, (int, float)) for c in p)
                                  for p in value)):
                        errors.append(
                            "op %d (mod %s): `points` must be ≥2 [x,y] "
                            "pairs, relative to the arrow's x,y"
                            % (i, op.get("id")))
                    else:
                        el["points"] = [[p[0], p[1]] for p in value]
                        # axis-aligned hand paths render as SHARP elbows
                        # (curved waypoints read as freehand)
                        if all(p1[0] == p2[0] or p1[1] == p2[1]
                               for p1, p2 in zip(el["points"],
                                                 el["points"][1:])):
                            el["roundness"] = None
                        # hand-authored paths are the author's (v0.3):
                        # marked "authored" so no later pass re-routes,
                        # re-fans, or re-stamps them back onto the anchor
                        # they were steered away from
                        _snap_geom(el)
                        cd = dict(el.get("customData") or {})
                        cd["routed"] = "authored"
                        el["customData"] = cd
                        recenter_label(els, el)
                elif attr == "name":
                    if el.get("type") != "frame":
                        errors.append("op %d (mod %s): `name` only applies "
                                      "to frames — use `label` for other "
                                      "elements" % (i, op.get("id")))
                    else:
                        el["name"] = value or el["id"]
                elif attr == "text":
                    errors.append("op %d (mod %s): `text` only applies to "
                                  "text elements — use `label`"
                                  % (i, op.get("id")))
                elif attr not in MOD_ATTRS:
                    errors.append(
                        "op %d (mod %s): unknown attribute %r — allowed: "
                        "label, from, to, text, customData, strategic, "
                        "kind, role, intent, points, name, %s"
                        % (i, op.get("id"), attr,
                           ", ".join(sorted(MOD_ATTRS))))
                else:
                    el[attr] = value
                    if attr in ("x", "y", "width", "height"):
                        recenter_label(els, el)
            # composite integrity: grouped decorations (X-box strokes,
            # attribute rows) travel with their element on x/y mods —
            # and EVERY part kind is re-derived on width/height mods
            # (WP3). Until v0.8 only the X-box re-derived: an image
            # placeholder shrunk from 116 to 72 high kept 116-high
            # diagonals and its X overshot into the panel below (R2-10)
            # — sliders, checkboxes, KPI centring, input insets and
            # attribute rows all had the same stale-on-resize hole.
            if ("width" in attrs or "height" in attrs):
                reconcile_composed(els, index, existing, el)
            dx = (el.get("x", 0) - old_x) if isinstance(old_x, (int, float)) \
                else 0
            dy = (el.get("y", 0) - old_y) if isinstance(old_y, (int, float)) \
                else 0
            if (dx or dy) and el.get("groupIds"):
                gset = set(el["groupIds"])
                for other in els:
                    if other is el:
                        continue
                    if set(other.get("groupIds") or []) & gset and \
                            (other.get("customData") or {}).get("role") == \
                            "decoration":
                        other["x"] = other.get("x", 0) + dx
                        other["y"] = other.get("y", 0) + dy
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
                        route_ctx(el, src, dst, opi=i)
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
            # composite cleanup: decorations grouped with the deleted
            # element (X-box strokes, entity attribute rows) go with it
            gids = set(el.get("groupIds") or [])
            if gids:
                for other in els:
                    if other["id"] in doomed:
                        continue
                    if set(other.get("groupIds") or []) & gids and \
                            (other.get("customData") or {}).get("role") == \
                            "decoration":
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
            px, py = pin_spot(anchor, els) if anchor else (40, 40)
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
                               "answer": None, "author": "agent"},
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
            # deleted must still be resolvable (never strand state).
            # Resolution DELETES the ❓ element (live_test_2 B3: settled
            # things leave the canvas; the glyph is tombstoned in the
            # save record like any deletion).
            el = index.get(op.get("id"))
            if el is not None:
                els = [e for e in els if e["id"] != el["id"]]
                index.pop(el["id"], None)

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
                route_ctx(e, index[s], index[d])
                recenter_label(els, e)
    if any(op.get("op") in ("add", "mod", "del") for op in ops):
        # final routing pass with the COMPLETE obstacle set: an arrow
        # added early in a batch was routed blind to nodes added after it
        obs = obstacles()
        ix2 = {e["id"]: e for e in els}
        for e in els:
            if e.get("type") != "arrow" or not server_owns_geometry(e):
                continue
            sN = ix2.get((e.get("startBinding") or {}).get("elementId"))
            dN = ix2.get((e.get("endBinding") or {}).get("elementId"))
            if sN is None or dN is None:
                continue
            pts = e.get("points") or []
            hit = any(
                _seg_hits_rect(e["x"] + p1[0], e["y"] + p1[1],
                               e["x"] + p2[0], e["y"] + p2[1], ob)
                for p1, p2 in zip(pts, pts[1:])
                for ob in obs if ob.get("id") not in (sN["id"], dN["id"]))
            if hit:
                route_ctx(e, sN, dN)
                recenter_label(els, e)
        fan_attach_points(els)
        els = normalize_z_order(els)
    return els


def normalize_z_order(els):
    """Paint order (layout.md): frames → decorations → arrows/lines →
    nodes → bound labels & pins. Excalidraw renders array order, so an
    arrow appended after its nodes paints ON TOP of them — every diagram
    in the capability assessment had all arrows z-above all nodes. The
    sort is stable: explicit `reorder` ops survive within their band;
    cross-band placement rides `role: decoration`."""
    def band(e):
        role = (e.get("customData") or {}).get("role")
        if e.get("type") == "frame":
            return 0
        if role == "decoration":
            return 1
        if e.get("type") in ("arrow", "line"):
            return 2
        if role == "pin":
            return 4
        if e.get("type") == "text" and e.get("containerId"):
            return 4
        return 3
    return sorted(els, key=band)


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
        # originalText first, whitespace-joined: legacy scenes may still
        # carry wrap-poisoned `text` and it must never reach narration
        t = " ".join((el.get("originalText") or el.get("text") or "").split())
        return t[:40] if t else el["id"]
    return labels.get(el["id"]) or el["id"]


def _anno_text(el):
    """Annotation text for facts: unwrapped source, whitespace-joined."""
    return " ".join((el.get("originalText") or el.get("text") or "").split())


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
                if _norm_binding(ov) == _norm_binding(nv):
                    if ov != nv:
                        # same endpoint, focus/gap drift (fan, v0.3):
                        # routing churn to narration, but replay must be
                        # lossless — record the full dicts, derived
                        attrs.append({"attr": attr, "from": ov, "to": nv,
                                      "derived": True})
                    continue
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
                if derived_geo or _text_metric_derived(attr, old, new):
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


def _text_metric_derived(attr, old, new):
    """Client font-metric churn on text elements is measurement, not
    intent: the browser re-measures every text element on load against
    real font metrics (the server only estimates). Height is always
    client-computed from content; width is client-computed while
    autoResize is on — a DELIBERATE user width-drag flips autoResize
    off, which keeps that width change narratable. Also covers the
    one-time min-height settle (≤12px growth) on a labeled container."""
    if attr == "height":
        if new.get("type") == "text" or old.get("type") == "text":
            return True
        ov, nv = old.get("height"), new.get("height")
        if isinstance(ov, (int, float)) and isinstance(nv, (int, float)) \
                and 0 <= nv - ov <= 12 and any(
                    isinstance(b, dict) and b.get("type") == "text"
                    for b in (new.get("boundElements") or [])):
            return True
    if attr == "width" and (new.get("type") == "text"
                            or old.get("type") == "text") \
            and old.get("autoResize", True) and new.get("autoResize", True):
        return True
    return False


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
                # facts are narration material — never carry wrap newlines
                # (records keep the raw attrs for replay)
                F("renamed", target,
                  **{"from": " ".join(str(a["from"] or "").split()),
                     "to": " ".join(str(a["to"] or "").split())})

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
            # sticky-note rects carry their text in a bound label
            F("annotated", el["id"],
              text=_anno_text(el) or new_labels.get(el["id"], ""),
              author=(el.get("customData") or {}).get("author"),
              target=_nearest_target(el, new_els))
            continue
        if r == "decoration":
            # X-box strokes, attribute rows, backdrops: counted, never
            # narrated individually (their composite parent speaks)
            F("added", el["id"], kind=kind_of(el),
              label=display_label(el, new_labels), low_signal=True)
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
            F("annotation_deleted", el["id"], text=_anno_text(el))
            continue
        if r == "decoration":
            F("deleted", el["id"], low_signal=True,
              was={"kind": kind_of(el),
                   "label": display_label(el, old_labels)})
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
            elif role_of(el) == "decoration":
                # attribute rows, X-box strokes, glyphs travel with their
                # composite parent — the parent's move is the story (v0.3
                # assessment: "qty, cost basis moved right" headlined a
                # Position drag)
                F("moved", c["id"], dx=dx, dy=dy,
                  spatial=spatial_phrase(dx, dy),
                  label=display_label(el, new_labels), low_signal=True)
            else:
                F("moved", c["id"], dx=dx, dy=dy,
                  spatial=spatial_phrase(dx, dy),
                  label=display_label(el, new_labels))
        elif c["op"] == "mod":
            el = new_ix.get(c["id"])
            if el is None:
                continue
            names = {a["attr"] for a in c["attrs"] if not a.get("derived")}
            if "name" in names and el.get("type") == "frame":
                for a in c["attrs"]:
                    if a["attr"] == "name":
                        F("renamed", c["id"],
                          **{"from": a["from"], "to": a["to"]})
            if names & {"width", "height"}:
                F("resized", c["id"], label=display_label(el, new_labels))
            styled = names & STYLE_ATTRS
            if styled and el.get("type") != "text":
                F("restyled", c["id"], attrs=sorted(styled), low_signal=True)
            if "link" in names:
                # WP3 (r4-9): links_to changes produced NO fact, so a
                # batch that only wired click-throughs narrated as
                # "saved without changing anything" while the links
                # landed — the agent's own advice afterward was "if it
                # says nothing happened, check before you redo it"
                for a in c["attrs"]:
                    if a["attr"] == "link":
                        F("links_changed", c["id"],
                          label=display_label(el, new_labels),
                          **{"from": a.get("from"), "to": a.get("to")})
            if "frameId" in names:
                pass  # regrouped — handled by the wireframe table
            if "customData" in names and role_of(el) == "annotation":
                F("annotated", c["id"], text=_anno_text(el),
                  target=_nearest_target(el, new_els))
            if "customData" in names:
                for a in c["attrs"]:
                    if a["attr"] != "customData":
                        continue
                    oldc = a.get("from") or {}
                    newc = a.get("to") or {}
                    if not isinstance(oldc, dict) or \
                            not isinstance(newc, dict):
                        continue
                    lbl2 = display_label(el, new_labels)
                    k2 = newc.get("kind") or oldc.get("kind")
                    if k2 in ("kpi", "slider", "input") and \
                            oldc.get("value") != newc.get("value"):
                        F("value_changed", c["id"], label=lbl2,
                          **{"from": oldc.get("value"),
                             "to": newc.get("value")})
                    if k2 in ("checkbox", "toggle") and \
                            bool(oldc.get("checked")) != \
                            bool(newc.get("checked")):
                        F("state_toggled", c["id"], label=lbl2,
                          to=bool(newc.get("checked")))
                    if oldc.get("tooltip") != newc.get("tooltip"):
                        if not oldc.get("tooltip"):
                            F("tooltip_added", c["id"], label=lbl2,
                              text=str(newc.get("tooltip"))[:80])
                        elif not newc.get("tooltip"):
                            F("tooltip_removed", c["id"], label=lbl2)
                        else:
                            F("tooltip_changed", c["id"], label=lbl2,
                              text=str(newc.get("tooltip"))[:80])
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
            # Excalidraw rewrites the binding OBJECT (focus, gap) when an
            # endpoint is dragged and dropped back on the node it came
            # from, so the attribute shows in the diff while nothing was
            # re-pointed. That produced `rewired` facts whose from and to
            # are identical, and two of them tripped
            # `sequence_reordered` — the one fact the flow reference
            # tells the agent to LEAD WITH — so a user who nudged two
            # arrowheads was told they had re-sequenced the process
            # (brownfield BUG-01). Compare the normalized bindings.
            ns_, ne_ = _norm_binding(el.get("startBinding")), \
                _norm_binding(el.get("endBinding"))
            if (os_, oe_) == (ns_, ne_):
                continue
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


def frame_reading_order(els, frame_id, row_tol=6):
    """Linearise a screen frame's content into reading order (v0.4 U1).

    A wireframe has no DOM, so the only reading-order claim it can make
    is geometric — which is what WCAG 1.3.2 asks about. Sort the frame's
    content shapes by y then x, merging rows whose tops differ by no
    more than ``row_tol`` (half the 12px gutter, ruling Q30).

    Args:
        els: The artifact's elements.
        frame_id: The frame whose children to linearise.
        row_tol: Max y-delta (px) for two elements to share a row.

    Returns:
        The frame's content elements (shapes minus labels, pins,
        decorations and annotations), in reading order.
    """
    kids = [e for e in els
            if e.get("frameId") == frame_id
            and e.get("type") in ("rectangle", "diamond", "ellipse")
            and role_of(e) not in ("label", "pin", "decoration",
                                   "annotation")]
    kids.sort(key=lambda e: e.get("y", 0))
    rows, row, row_y = [], [], 0
    for e in kids:
        y = e.get("y", 0)
        if row and y - row_y > row_tol:
            rows.append(row)
            row = []
        if not row:
            row_y = y
        row.append(e)
    if row:
        rows.append(row)
    for r in rows:
        r.sort(key=lambda e: (e.get("x", 0), e.get("y", 0)))
    return [e for r in rows for e in r]


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
                            ("button", "nav", "link", "tab",
                             "checkbox", "toggle", "kpi", "slider",
                             "help", "feedback", "sticky-bar"):
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

    # reading order (v0.4 U1): narrate a screen's linearised sequence on
    # first draw and whenever an edit changes the computed order — the
    # cadence ruling; quiet rounds stay quiet, an unnoticed reorder
    # always surfaces
    new_els_l = list(new_ix.values())
    old_els_l = list(old_ix.values())
    added_frames = {el["id"] for el in adds if el.get("type") == "frame"}
    for el in new_els_l:
        if el.get("type") != "frame" or kind_of(el) in ("lane", "fold"):
            continue
        order = frame_reading_order(new_els_l, el["id"])
        if len(order) < 2:
            continue
        seq = [display_label(e, new_labels) for e in order]
        if el["id"] in added_frames:
            F("reading_order_set", el["id"],
              screen=el.get("name") or el["id"], order=seq)
        elif el["id"] in old_ix:
            old_order = frame_reading_order(old_els_l, el["id"])
            if [e["id"] for e in old_order] != [e["id"] for e in order]:
                F("reading_order_changed", el["id"],
                  screen=el.get("name") or el["id"], order=seq,
                  was=[display_label(e, old_labels) for e in old_order])
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

    # entity attribute rows (composite decorations tagged attr_of)
    for el in adds:
        target = (el.get("customData") or {}).get("attr_of")
        if target:
            F("attribute_added", target, attribute=_anno_text(el),
              entity=display_label(new_ix.get(target, el), new_labels))
    for el in dels:
        target = (el.get("customData") or {}).get("attr_of")
        if target and target in new_ix:
            F("attribute_removed", target, attribute=_anno_text(el),
              entity=display_label(new_ix.get(target, el), new_labels))

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

def _tw_names(refs, cap=3):
    """Render mapping members for a tripwire question.

    Args:
        refs: `artifact#element` refs.
        cap: How many to name before summarising the rest.

    Returns:
        A readable list — all of them up to `cap`, else the first `cap`
        and a count, so a wide mapping does not produce a paragraph.
    """
    shown = [r.replace("#", " › ") for r in refs[:cap]]
    if len(refs) > cap:
        shown.append("and %d more" % (len(refs) - cap))
    return ", ".join(shown)


SALIENCE = ["user_route_replaced",
            "rewired", "relationship_rewired", "rerouted",
            "actor_reassigned",
            "message_reordered", "cardinality_changed", "ownership_changed",
            "party_kind_changed", "fold_crossed", "reading_order_changed",
            "type_changed", "value_changed", "state_toggled",
            "screen_added", "screen_deleted", "reading_order_set",
            "entity_renamed", "renamed",
            "label_renamed", "branch_added", "step_added", "entity_added",
            "actor_added", "lane_added", "handoff_added",
            "attribute_added", "attribute_removed", "added",
            "step_deleted", "entity_deleted", "actor_deleted",
            "lane_deleted", "deleted", "regrouped", "sync_changed",
            "label_added", "transition_added", "transition_deleted",
            "relationship_added", "relationship_deleted", "message_added",
            "message_deleted", "arrow_orphaned", "mapping_dangling",
            "note_orphaned", "links_changed", "annotated",
            "annotation_deleted",
            "pin_added", "pin_deleted", "priority_changed",
            "tooltip_added", "tooltip_changed", "tooltip_removed",
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
    # WP2 reference-impact facts: a deletion names what it broke
    if n == "arrow_orphaned":
        return "arrow %s lost its %s target %s — it points at nothing" \
            % (fact["element"], fact.get("side"), fact.get("target"))
    if n == "mapping_dangling":
        return "mapping %r lost member %s" \
            % (fact.get("concept"), fact.get("ref"))
    if n == "note_orphaned":
        return "the note %s lost its anchor (%s deleted)" \
            % (fact["element"], fact.get("target"))
    if n == "links_changed":
        to = str(fact.get("to") or "").replace("artifact:", "")
        if to:
            return "%s now links to %s" % (
                fact.get("label") or fact["element"], to)
        return "%s no longer links anywhere" % (
            fact.get("label") or fact["element"])
    # explicit branches for facts the suffix rules would mangle — no
    # fact may fall through to a bare verb ("resized (+3 more)")
    if n == "added":
        return "added %s" % (fact.get("label") or fact["element"])
    if n == "resized":
        return "resized %s" % (fact.get("label") or fact["element"])
    if n == "restyled":
        return "restyled %s (%s)" % (fact["element"],
                                     ", ".join(fact.get("attrs") or []))
    if n == "reordered":
        return "reordered %s" % (fact.get("label") or fact["element"])
    if n == "rerouted":
        return "rerouted %s" % (fact.get("arrow") or fact["element"])
    if n == "user_route_replaced":
        return ("re-routed %s, replacing the path you drew by hand — a "
                "rewire is a new path request; say so, or re-issue "
                "`mod points` to put your shape back"
                % (fact.get("arrow") or fact["element"]))
    if n == "attribute_added":
        return "%s gained attribute %r" % (fact.get("entity")
                                           or fact["element"],
                                           fact.get("attribute"))
    if n == "attribute_removed":
        return "%s lost attribute %r" % (fact.get("entity")
                                         or fact["element"],
                                         fact.get("attribute"))
    if n == "annotation_added":
        who = "my" if fact.get("author") == "agent" else "your"
        return "%s note: %r" % (who, (fact.get("text") or "")[:60])
    if n == "annotation_deleted":
        return "removed note: %r" % (fact.get("text") or "")[:60]
    if n == "tooltip_added":
        return "added a tooltip to %s" % (fact.get("label")
                                          or fact["element"])
    if n == "tooltip_changed":
        return "reworded the tooltip on %s" % (fact.get("label")
                                               or fact["element"])
    if n == "tooltip_removed":
        return "removed the tooltip from %s" % (fact.get("label")
                                                or fact["element"])
    if n == "pin_added":
        return "asked: %r" % (fact.get("question") or "")[:60]
    if n == "pin_deleted":
        return "removed pin %s" % fact["element"]
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
        if fact.get("author"):
            who = "my" if fact.get("author") == "agent" else "your"
            return "%s note: %r" % (who, (fact.get("text") or "")[:60])
        return "annotated: %r" % (fact.get("text") or "")[:60]
    if n == "value_changed":
        return "%s is now %s (was %s)" % (fact.get("label") or
                                          fact["element"],
                                          fact.get("to"), fact.get("from"))
    if n == "state_toggled":
        return "%s switched %s" % (fact.get("label") or fact["element"],
                                   "on" if fact.get("to") else "off")
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
    if n in ("reading_order_set", "reading_order_changed"):
        seq = fact.get("order") or []
        shown = " / ".join(seq[:6]) + ("…" if len(seq) > 6 else "")
        verb = "reads" if n == "reading_order_set" else "now reads"
        return "screen %s %s: %s" % (fact.get("screen"), verb, shown)
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
    real = [f for f in facts if f["fact"] != "saved_no_changes"]
    if not visible and real:
        # WP3 (r4-9): every fact suppressed is NOT "no changes" — that
        # message taught an agent to re-issue a batch that had landed
        # ("if it says nothing happened, check before you redo it")
        headline = "housekeeping only (%d low-signal change%s)" % (
            len(real), "" if len(real) == 1 else "s")
    for name in SALIENCE:
        hit = next((f for f in visible if f["fact"] == name), None)
        if hit:
            headline = headline_for(hit)
            extra = len(visible) - 1
            if extra > 0:
                headline += " (+%d more)" % extra
            break
    else:
        # a fact type SALIENCE doesn't know must still headline itself
        if visible:
            headline = headline_for(visible[0])
            if len(visible) > 1:
                headline += " (+%d more)" % (len(visible) - 1)
    return {"verb_counts": verb_counts, "headline": headline,
            "suppressed": suppressed}


def _registry_headline(reg_changes):
    """Headline for a batch whose only recorded work was registry ops."""
    def describe(ch):
        action = str(ch.get("action") or "registry change").replace("_", " ")
        ident = ch.get("id") or ch.get("concept") or ch.get("name") or ""
        return ("%s %s" % (action, ident)).strip()
    head = "registry: " + describe(reg_changes[0])
    if len(reg_changes) > 1:
        head += " (+%d more)" % (len(reg_changes) - 1)
    return head


# ---------------------------------------------------------------------------
# label mutation helper used by the op engine
# ---------------------------------------------------------------------------

def marker_inset(etype):
    """Fraction of the half-diagonal at which a shape's outline sits.

    A rectangle fills its bounding box; a diamond and an ellipse do not,
    so the box's corner is empty canvas for them.

    Args:
        etype: The Excalidraw element type.

    Returns:
        0 for box-filling shapes, else the inset fraction.
    """
    if etype == "diamond":
        return 0.5
    if etype == "ellipse":
        return 1 - 0.5 ** 0.5
    return 0.0


def marker_anchor(el, dx=0, dy=0, corner="br"):
    """Where a marker should sit to hug a shape's edge at one corner.

    Markers hug the shape. Anchoring at the bounding-box corner puts a
    marker 22px off a rectangle and 79px off a DIAMOND — a clearance
    that varies threefold with shape type, which no intentional design
    would do (v0.6 assessment r3-1). v0.6 fixed exactly this for the
    tooltip dot in App.tsx and nobody grepped for the shape, so it was
    still live in the pin seeder AND in the tripwire mark.

    Mirrored by `markerAnchor` in App.tsx: stored geometry and canvas
    overlay cannot share code, so they share this rule and a test.

    Args:
        el: The target element.
        dx: Horizontal nudge applied after the inset.
        dy: Vertical nudge applied after the inset.
        corner: `"br"` bottom-right (the tooltip dot) or `"tr"`
            top-right (the pin glyph and the tripwire mark).

    Returns:
        `(x, y)` for the marker.
    """
    w, h = el.get("width", 0), el.get("height", 0)
    inset = marker_inset(el.get("type"))
    x = el.get("x", 0) + w - w * inset / 2 + dx
    if corner == "tr":
        return (x, el.get("y", 0) + h * inset / 2 + dy)
    return (x, el.get("y", 0) + h - h * inset / 2 + dy)


def pin_spot(anchor, els, size=26):
    """Where a ❓ glyph sits: hugging the target, never in a neighbour.

    The constant top-right offset is layout-density-blind (r4b-3): on a
    12px-gutter wireframe grid the glyph touched no part of its target
    and its centre landed inside the NEXT panel's column, so three pins
    read as belonging to the heat map, the drawdown and the Weekly
    Brief. When the hug spot collides with a foreign element, fall back
    to inside the target's own top-right corner — on flows (hundreds of
    px of air) nothing changes.

    Args:
        anchor: The target element.
        els: The scene (collision candidates).
        size: Glyph bbox edge in px.

    Returns:
        `(x, y)` for the glyph.
    """
    px, py = marker_anchor(anchor, dx=8, dy=-8, corner="tr")
    gx1, gy1, gx2, gy2 = px, py, px + size, py + size
    for e in els:
        if e is anchor or e.get("id") == anchor.get("id"):
            continue
        if e.get("type") not in ("rectangle", "diamond", "ellipse",
                                 "frame"):
            continue
        if role_of(e) in ("label", "pin", "decoration", "annotation"):
            continue
        ex1, ey1 = e.get("x", 0), e.get("y", 0)
        ex2 = ex1 + e.get("width", 0)
        ey2 = ey1 + e.get("height", 0)
        if gx1 < ex2 and gx2 > ex1 and gy1 < ey2 and gy2 > ey1:
            return (anchor.get("x", 0) + anchor.get("width", 0) -
                    size - 2, anchor.get("y", 0) + 2)
    return (px, py)


def _reset_attribute_rows(els, index, existing, el, rows):
    """Replace a domain entity's attribute rows, keeping the entity's id.

    Mirrors the minting in the `add` seeder — same geometry, same
    `attr_of` stamp — so the differ's existing `attribute_added` /
    `attribute_removed` facts narrate the change without any new
    vocabulary. The entity element itself is never re-created, which is
    the whole point: its id carries the mappings, the pins and the
    rename detection (BUG-04).

    Args:
        els: The scene's element list, mutated in place.
        index: id -> element, mutated to match.
        existing: Set of ids in use, mutated to match.
        el: The entity element.
        rows: The new attribute strings, in order.
    """
    eid = el["id"]
    gone = [e for e in els
            if (e.get("customData") or {}).get("attr_of") == eid]
    for e in gone:
        els.remove(e)
        index.pop(e["id"], None)
        existing.discard(e["id"])
    gid = eid + "-grp"
    if gid not in (el.get("groupIds") or []):
        el["groupIds"] = [*(el.get("groupIds") or []), gid]
    header_h, row_h = 32, 20
    el["height"] = max(el.get("height", 0),
                       header_h + row_h * len(rows) + 8)
    for irow, text in enumerate(rows):
        rid = "%s-attr-%d" % (eid, irow + 1)
        n2 = 2
        while rid in existing:
            rid = "%s-attr-%d-%d" % (eid, irow + 1, n2)
            n2 += 1
        existing.add(rid)
        row = dict(BASE_DEFAULTS)
        row.update({
            "id": rid, "type": "text",
            "x": el["x"] + 10,
            "y": el["y"] + header_h + irow * row_h,
            "width": max(el.get("width", 160) - 20, 40),
            "height": row_h - 4,
            "text": text, "originalText": text,
            "fontSize": 12, "fontFamily": FONT_LEGIBLE,
            "textAlign": "left", "verticalAlign": "top",
            "lineHeight": 1.25, "containerId": None,
            "autoResize": False, "strokeColor": "#5c584d",
            "groupIds": [gid],
            "customData": {"role": "decoration", "attr_of": eid,
                           "author": "agent"},
        })
        els.append(row)
        index[rid] = row


def _set_label(els, index, existing, el, value):
    """Set, replace, or clear (value None/"") an element's bound label."""
    if el.get("type") == "frame":
        # Frames carry their name natively (make_element parity). The
        # missing branch here was the frame-rename bug: `mod label` on a
        # frame minted a bound text element floating over the frame's
        # members while `name` stayed stale (capability assessment
        # 2026-08-08, the '?' frame captioned 'Notifications').
        el["name"] = value or el.get("name") or el["id"]
        return
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


def _plain(md):
    """Flatten markdown emphasis for a renderer with no rich text.

    Tooltips are authored as markdown because the app renders them that
    way; SVG has no such luxury, and printing the asterisks is worse than
    losing the emphasis.

    Args:
        md: Tooltip or definition text.

    Returns:
        One-line plain text.
    """
    s = " ".join(str(md or "").split())
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", s)
    return s.replace("`", "")


def collect_footnotes(els):
    """Number the tooltips on an artifact, in reading order.

    Per-element detail lives in `customData.tooltip`, which is hover-only
    and therefore survives nothing: not a PNG, not a print, not being
    handed to someone who was not in the session. A session that ends
    "I'm giving these to my analyst on Monday" ends with the detail
    stripped out (v0.4 capability assessment).

    Args:
        els: The artifact's elements.

    Returns:
        A list of `(number, label, tooltip, element)` in top-to-bottom,
        left-to-right order.
    """
    labels = label_map(els)
    owners = [e for e in els
              if not e.get("isDeleted") and (e.get("customData") or {})
              .get("tooltip")]
    owners.sort(key=lambda e: (round(e.get("y", 0) / 40), e.get("x", 0)))
    return [(i + 1, (labels.get(e["id"]) or e.get("name") or e["id"]),
             str((e.get("customData") or {})["tooltip"]), e)
            for i, e in enumerate(owners)]


def render_svg(els, title="", footnotes=False, glossary=None):
    """Deterministic stdlib SVG of an element array — the snapshot CLI's
    tier-3 fallback and the substrate tier 2 rasterizes. Geometry-faithful
    (drawn from the same coordinates the lint reads); text set in system
    fonts, so it is an approximation of Excalidraw's hand-drawn look, not
    a replica — good for legibility checks, never for style judgments.

    Args:
        els: The artifact's elements.
        title: Optional caption drawn top-left.
        footnotes: Mark tooltip-bearing elements and print their text
            below the drawing, so an exported artifact carries its own
            detail instead of losing it with the hover.
        glossary: Optional `(term, definition)` pairs appended under the
            footnotes — the words the drawing assumes.

    Returns:
        `(svg_text, width, height)`.
    """
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
            ew2, eh2 = e.get("width", 0), e.get("height", 0)
            if e.get("type") == "text":
                # stored text extents are estimates; real glyph advance
                # regularly overhangs them and the overflow was cropped
                # out of exports (v0.3 assessment) — bound by the larger
                # of stored and estimated extents
                tw, th = text_dims(e.get("text") or "",
                                   e.get("fontSize", 16))
                ew2, eh2 = max(ew2, tw), max(eh2, th)
            x2s.append(e.get("x", 0) + ew2)
            y2s.append(e.get("y", 0) + eh2)
    pad = 40
    notes = collect_footnotes(live) if footnotes else []
    gloss = list(glossary or []) if footnotes else []
    minx, miny = min(xs) - pad, min(ys) - pad
    w = max(x2s) - min(xs) + 2 * pad
    h = max(y2s) - min(ys) + 2 * pad
    # room under the drawing for the notes block, wrapped to the width
    note_lines = []
    for n, lbl, tip, _e in notes:
        body = _plain(tip)
        for j, line in enumerate(
                wrap_label_text(body, max(w - 80, 240), 13).split("\n")):
            note_lines.append(("%d. %s — %s" % (n, lbl, line)) if j == 0
                              else "      " + line)
    for term, definition in gloss:
        body = _plain(definition)
        for j, line in enumerate(
                wrap_label_text(body, max(w - 80, 240), 13).split("\n")):
            note_lines.append(("%s: %s" % (term, line)) if j == 0
                              else "      " + line)
    foot_h = (48 + 19 * len(note_lines)) if note_lines else 0
    h += foot_h
    # cap raster size UNIFORMLY: the old independent min(w,4000)/min(h,3000)
    # clamp squashed the aspect ratio of anything wider than 4000px
    scale = min(1.0, 4000.0 / w, 3000.0 / h)
    out = ["<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' "
           "viewBox='%f %f %f %f'>" % (int(w * scale), int(h * scale),
                                       minx, miny, w, h),
           "<rect x='%f' y='%f' width='%f' height='%f' fill='%s'/>"
           % (minx, miny, w, h, SVG_GROUND)]
    # labels bound to arrows need the stroke painted back out from under
    # them (v0.6) — collected once rather than per text element
    arrow_ids = {e["id"] for e in live
                 if e.get("type") in ("arrow", "line")}
    if title:
        out.append("<text x='%f' y='%f' font-size='13' fill='#999' "
                   "font-family='Nunito, sans-serif'>%s</text>"
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
                       "font-family='Nunito, sans-serif'>%s</text>"
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
            # fixed-width text WRAPS on the live canvas, but this renderer
            # only ever split on \n — so a label the app lays out in two
            # lines was exported as one long line spilling out of its box.
            # The snapshot is the agent's only way to see its own drawing,
            # so it was being lied to about legibility (v0.4 assessment).
            if e.get("autoResize") is False and ew > 0:
                lines = [wl for line in lines
                         for wl in wrap_label_text(line, ew, fs).split("\n")]
            anchor = "middle" if e.get("textAlign") == "center" else "start"
            tx = x + (ew / 2 if anchor == "middle" else 0)
            lh = fs * (e.get("lineHeight") or 1.25)
            # a label bound to an arrow rides its stroke (v0.6 — see
            # arrow_label_anchor). The client breaks the arrow behind it;
            # this renderer has no such notion, so paint the ground back
            # in or the export shows a line struck through its own label.
            if arrow_ids and e.get("containerId") in arrow_ids:
                twid = max(text_dims(ln2, fs)[0] for ln2 in lines) \
                    if lines else 0
                out.append("<rect x='%f' y='%f' width='%f' height='%f' "
                           "fill='%s' stroke='none'/>"
                           % (tx - (twid / 2 if anchor == "middle" else 0)
                              - 4, y - 2, twid + 8,
                              max(len(lines), 1) * lh + 4, SVG_GROUND))
            for li, line in enumerate(lines):
                out.append("<text x='%f' y='%f' font-size='%s' fill='%s' "
                           "text-anchor='%s' font-family='Nunito, sans-serif'>"
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
    for n, _lbl, _tip, e in notes:
        mx = e.get("x", 0) + e.get("width", 0) - 4
        my = e.get("y", 0) + 4
        out.append("<circle cx='%f' cy='%f' r='8' fill='#fdfcf8' "
                   "stroke='#b45309' stroke-width='1'/>" % (mx, my))
        out.append("<text x='%f' y='%f' font-size='10' fill='#b45309' "
                   "text-anchor='middle' font-family='Nunito, sans-serif'>%d</text>"
                   % (mx, my + 3.5, n))
    if note_lines:
        fy = miny + h - foot_h + 12
        out.append("<line x1='%f' y1='%f' x2='%f' y2='%f' stroke='#ccc' "
                   "stroke-width='1'/>" % (minx + 20, fy, minx + w - 20, fy))
        for k, line in enumerate(note_lines):
            out.append("<text x='%f' y='%f' font-size='13' fill='#444' "
                       "font-family='Nunito, sans-serif'>%s</text>"
                       % (minx + 24, fy + 24 + k * 19, _svg_escape(line)))
    out.append("</svg>")
    return "\n".join(out), int(w * scale), int(h * scale)


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
            if not s and not d and (
                    el.get("type") == "line" or
                    (el.get("customData") or {}).get("role") ==
                    "decoration"):
                return "line %s at (%d,%d), %d points" % (
                    eid, el.get("x", 0), el.get("y", 0),
                    len(el.get("points") or []))
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
            # flat-form adds (attrs at op level) must echo like nested ones
            spec = op.get("element") or \
                {k: v for k, v in op.items() if k != "op"}
            eid = spec.get("id") or slugify(spec.get("label", "")
                                            or spec.get("text", "") or "el")
            lines.append("op %d (add): %s" % (i, describe(eid)))
        elif kind == "mod":
            # post-apply state for the v0.3 stateful attrs — "accepted"
            # is not "did what you meant"
            extra = ""
            mel = ix.get(op.get("id"))
            mattrs = op.get("attrs") or {}
            if mel is not None:
                mcd = mel.get("customData") or {}
                if "tooltip" in mattrs:
                    extra += " — tooltip %s" % (
                        "set (%d chars)" % len(mcd.get("tooltip") or "")
                        if mcd.get("tooltip") else "cleared")
                if "value" in mattrs:
                    extra += " — value now %r" % (mcd.get("value"),)
                if "checked" in mattrs:
                    extra += " — now %s" % (
                        "checked" if mcd.get("checked") else "unchecked")
            lines.append("op %d (mod %s): %s%s"
                         % (i, ",".join(sorted(mattrs)),
                            describe(op.get("id")), extra))
        elif kind == "del":
            eid = op.get("id")
            lines.append("op %d (del): %s %s" % (
                i, eid, "deleted (with its bound label)"
                if eid not in ix else "STILL PRESENT"))
        elif kind == "reorder":
            eid = op.get("id")
            order = [e["id"] for e in els]
            pos = order.index(eid) if eid in order else -1
            lines.append("op %d (reorder): %s now at z-index %d of %d"
                         % (i, eid, pos, len(order)))
        elif kind == "pin":
            eid = op.get("id")
            state = "❓ on canvas" if eid in ix else "NOT on canvas"
            lines.append("op %d (pin): %s targets %s — %r (%s)"
                         % (i, eid, op.get("target"),
                            (op.get("question") or "")[:50], state))
        elif kind == "resolve_pin":
            eid = op.get("id")
            glyph = "❓ glyph removed from canvas" if eid not in ix \
                else "❓ glyph STILL on canvas"
            lines.append("op %d (resolve_pin): %s resolved (%s)"
                         % (i, eid, glyph))
        elif kind == "registry":
            ident = op.get("id") or op.get("concept") or op.get("name") or ""
            lines.append(("op %d (registry): %s %s"
                          % (i, op.get("action"), ident)).rstrip())
    return lines


FLOW_KINDS = {"source", "transform", "agent", "control", "sink", "store"}

# wireframe kinds a user is expected to hit/tap — the 2.5.8-shaped
# target-size question only applies to these (v0.4 U11)
INTERACTIVE_KINDS = {"button", "input", "checkbox", "toggle", "slider",
                     "nav", "link", "tab"}

# progress-indicator tells (v0.4 U5/Q25) — label evidence, plus the
# status vocabulary the task-list archetype uses, which is exempt
# A bare percentage is NOT a progress indicator: `VaR alert 2.5%` on a
# threshold slider drew a GDS citation about 12-step wizards and cost the
# agent a waive to silence (v0.5 assessment R2-1). Every KPI delta and
# every threshold in a wireframe carries one. `% complete` still counts —
# that is a progress bar wearing a number. The dot-row geometry tell
# below is what actually finds unlabelled indicators.
_PROGRESS_RE = re.compile(
    r"\bstep\s+\d+\s+of\s+\d+\b|\bprogress\b|"
    r"\b\d+\s*%\s*(?:complete|done|uploaded|finished)\b", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"^(in progress|not started|completed|done)$", re.IGNORECASE)


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


def lint_layout(els, artifact_type=None, budget=None, waives=None,
                aid=None):
    """Layout lint for headless agents (who can't see their own drawing),
    tiered per references/layout.md:
      errors   — the drawing does not say what the agent meant; repair in
                 the same move, before narrating
      warnings — legibility defects worth a cosmetic repair
      notes    — style/budget observations
    Returns {"errors": [...], "warnings": [...], "notes": [...]}.
    (Sequence/lane/shell-specific checks land with the type promotion —
    task #7: time-reversal, activation-never-closes, lane-spanning,
    app-shell drift, cardinality-token mismatch.)

    Args:
        els: The artifact's elements.
        artifact_type: Optional type ("domain" lowers the node budget to
            8 entities per references/domain.md).
        budget: Optional per-artifact override
            {"nodes": N, "arrows": M, "reason": str} from
            registry["budgets"] — recorded intent beats the default.
        waives: Optional registry["waives"] dict — one-time questions
            (e.g. the Q25 progress-indicator note) go quiet once a
            waive keyed "<check>:<artifact>" is recorded (v0.4).
        aid: Optional artifact id, needed to resolve waive keys.
    """
    errors, warnings, notes = [], [], []
    node_budget = 8 if artifact_type == "domain" else 9
    node_unit = "entities" if artifact_type == "domain" else "nodes"
    arrow_budget = 12
    if budget:
        node_budget = int(budget.get("nodes") or node_budget)
        arrow_budget = int(budget.get("arrows") or arrow_budget)
        notes.append(
            "budget override on this artifact: %d %s / %d arrows — %s"
            % (node_budget, node_unit, arrow_budget,
               budget.get("reason") or "no reason recorded"))
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
              and role_of(e) not in ("label", "pin", "decoration",
                                     "annotation")]
    arrows = [e for e in els if e.get("type") in ("arrow", "line")
              and e.get("points")
              and role_of(e) != "decoration"]
    nodes = [e for e in shapes if role_of(e) == "node" or
             (e.get("customData") or {}).get("kind")]
    # decorations are exempt from budgets and connector routing — that
    # exemption is right, furniture is not a connector. But it swallowed
    # one thing worth knowing: a decoration that has drifted OFF the
    # element it is grouped with. An image placeholder resized without
    # its X strokes being re-derived spilled 44px into the panel below
    # and no check could see it (v0.5 assessment R2-10).
    for deco in els:
        if role_of(deco) != "decoration" or not deco.get("groupIds"):
            continue
        gset = set(deco["groupIds"])
        host = next((s for s in shapes
                     if set(s.get("groupIds") or []) & gset), None)
        if host is None:
            continue
        # extent from the POINTS, not x/y/width/height: the X's second
        # stroke is stored at the box's bottom edge with negative points
        # (bottom-left → top-right), so a naive bbox puts it a full box
        # height below where it is drawn
        if deco.get("points"):
            pxs = [deco.get("x", 0) + p[0] for p in deco["points"]]
            pys = [deco.get("y", 0) + p[1] for p in deco["points"]]
            dx1, dx2, dy1, dy2 = min(pxs), max(pxs), min(pys), max(pys)
        else:
            dx1, dy1 = deco.get("x", 0), deco.get("y", 0)
            dx2 = dx1 + deco.get("width", 0)
            dy2 = dy1 + deco.get("height", 0)
        hx1, hy1 = host.get("x", 0), host.get("y", 0)
        hx2 = hx1 + host.get("width", 0)
        hy2 = hy1 + host.get("height", 0)
        spill = max(hx1 - dx1, dx2 - hx2, hy1 - dy1, dy2 - hy2)
        if spill > 4:
            notes.append(
                "decoration %s extends %dpx past %s, the element it is "
                "grouped with — it was sized for an older geometry; "
                "re-issue the shape or drop the stroke"
                % (deco["id"], int(spill), name(host["id"])))

    # ---- ERROR: detached endpoints (server-routed) --------------------
    # An arrow that LOOKS like a relationship but binds nothing (v0.8,
    # D8): the r4-11 workaround hand-authored a labeled domain loop with
    # both bindings null — it rendered perfectly, followed nothing, and
    # no check named it. The agent then reported it "properly bound".
    for a in arrows:
        if (a.get("startBinding") or {}).get("elementId") or \
                (a.get("endBinding") or {}).get("elementId"):
            continue
        a_lbl = next((t for t in els if t.get("containerId") == a["id"]
                      and t.get("type") == "text"), None)
        if a_lbl is None and artifact_type != "domain":
            continue  # an unlabeled sketch arrow outside a domain view
        warnings.append(
            "arrow %s%s binds nothing — it reads as a relationship but "
            "will not follow either endpoint when they move. Bind it "
            "with from/to (self-loops route automatically), or mark it "
            "role: decoration if it is only furniture"
            % (a["id"], (" (%r)" % a_lbl.get("text", "")[:24])
               if a_lbl is not None else ""))

    TOL = 14  # binding gap (6) + slack
    for a in arrows:
        x1, y1, x2, y2 = bbox_pts(a)
        for key, px, py in (("startBinding", x1, y1),
                            ("endBinding", x2, y2)):
            b = a.get(key)
            side = "start" if key == "startBinding" else "end"
            # a binding to an element that no longer exists. Deleting a
            # node leaves its arrows pointing at a corpse: on screen the
            # drawing asserts a flow out of nothing, and this loop used
            # to `continue` past exactly the broken ones while raising an
            # ERROR for a binding merely 26px off (r3-13). Deletion does
            # NOT cascade — Excalidraw keeps the arrows and eating the
            # user's geometry is the worse bug — but the silence goes.
            if b and b.get("elementId") and b["elementId"] not in ix:
                errors.append(
                    "arrow %s binds %s at its %s point and that element no "
                    "longer exists — re-target the binding, or delete the "
                    "arrow with it" % (a["id"], b["elementId"], side))
                continue
            tgt = ix.get((b or {}).get("elementId"))
            if tgt is None:
                continue        # genuinely unbound: nothing to check
            sb_el = (a.get("startBinding") or {}).get("elementId")
            eb_el = (a.get("endBinding") or {}).get("elementId")
            if sb_el is not None and sb_el == eb_el:
                continue  # self-loop (v0.8): both ends live on the border
            gx1, gy1 = tgt["x"], tgt["y"]
            gx2 = tgt["x"] + tgt.get("width", 0)
            gy2 = tgt["y"] + tgt.get("height", 0)
            # Two distinct failure shapes (r4-1). Border distance says
            # whether the endpoint sits ON the perimeter — fanned attach
            # points land exactly on an edge and must stay legal. The
            # CHORD says whether the arrow entered through one edge and
            # kept going: an endpoint 12px from the FAR edge scored as
            # barely-inside under border distance alone, so deeper
            # penetration produced LESS warning — the opposite of a
            # tolerance.
            outside = ((max(gx1 - px, px - gx2, 0)) ** 2 +
                       (max(gy1 - py, py - gy2, 0)) ** 2) ** 0.5
            inside = 0 if outside else max(
                min(px - gx1, gx2 - px, py - gy1, gy2 - py), 0)
            run = 0.0
            if not outside:
                # Interior run adjacent to the endpoint: walk the path
                # from the bound end inward, summing each segment's
                # STRICTLY-interior portion (1px in from the border, so
                # fanned attach points and boundary-running approaches
                # stay at zero — the r4-1 over-fire), stopping once the
                # path leaves the box. The single-segment chord this
                # replaces was blind to multi-elbow approaches and gated
                # on a strictly-interior endpoint, so an arrow entering
                # the top face and running 74px inside to a side-face
                # endpoint measured clean (first mermaid-seeded dagre
                # layout, v0.8).
                pts = a.get("points") or []
                seq = [(a.get("x", 0) + p[0], a.get("y", 0) + p[1])
                       for p in pts]
                if key == "endBinding":
                    seq = seq[::-1]
                bx1, by1, bx2, by2 = gx1 + 1, gy1 + 1, gx2 - 1, gy2 - 1
                for (p0x, p0y), (p1x, p1y) in zip(seq, seq[1:]):
                    ddx, ddy = p1x - p0x, p1y - p0y
                    t0, t1 = 0.0, 1.0
                    for lo, hi, dv, sv in ((bx1, bx2, ddx, p0x),
                                           (by1, by2, ddy, p0y)):
                        if abs(dv) < 1e-9:
                            if sv < lo or sv > hi:
                                t0, t1 = 1.0, 0.0
                            continue
                        ta, tb = (lo - sv) / dv, (hi - sv) / dv
                        if ta > tb:
                            ta, tb = tb, ta
                        t0, t1 = max(t0, ta), min(t1, tb)
                    if t1 > t0:
                        run += ((ddx * ddx + ddy * ddy) ** 0.5) * (t1 - t0)
                    if not (bx1 <= p1x <= bx2 and by1 <= p1y <= by2):
                        break   # the path has left the box
            if run > TOL * 2 and inside <= TOL:
                # crosses THROUGH: endpoint on or near the border, long
                # interior approach — the r4-1 silent case and its
                # multi-elbow sibling
                msg = ("arrow %s enters %s and runs %dpx inside it "
                       "before stopping (%s point) — it reads as "
                       "crossing through the box; pull the endpoint "
                       "back to the border"
                       % (a["id"], name(tgt["id"]), int(run), side))
                if not server_owns_geometry(a):
                    warnings.append(
                        "user-shaped " + msg +
                        " — not auto-routed (the path is the user's "
                        "geometry); re-route deliberately and narrate it")
                else:
                    errors.append(msg)
                continue
            if max(outside, inside) > TOL:
                msg = ("arrow %s claims to bind %s but its %s point ends "
                       "%dpx %s — re-route it (mod x/y on the node "
                       "re-routes 2-point arrows automatically)"
                       % (a["id"], name(tgt["id"]), side,
                          int(outside or inside) or TOL,
                          "away" if outside else "inside the shape"))
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
            if s is not None and s == d:
                # a reflexive arrow (v0.8) is commentary on the node, not
                # flow progress — counting it both ways made a looped node
                # its own source AND sink
                continue
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
                    and k not in ("sink", "store"):
                errors.append(
                    "%s is a black hole — flow enters and never leaves. "
                    "Every gap like this is a conversation not yet had: "
                    "ask it, don't just patch it" % name(eid))
            if k not in ("sink", "store") and outbound[eid] \
                    and not inbound[eid] and k != "source":
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

    # ---- wireframe frame checks (v0.4 U-round) -----------------------
    # reading-order, form and WCAG-shaped questions. Epistemics rule:
    # these are questions a criterion will ask later, never verdicts —
    # a wireframe cannot "fail WCAG" (NOTICE.md, refused absorptions)
    if artifact_type == "wireframe":
        def waived(check):
            return bool(waives) and aid is not None and \
                ("%s:%s" % (check, aid)) in waives

        frames = [e for e in els if e.get("type") == "frame"
                  and kind_of(e) not in ("lane", "fold")]
        fname = {f["id"]: f.get("name") or f["id"] for f in frames}
        # duplicate frame titles (GOV.UK question-pages)
        titles = {}
        for f in frames:
            t = (f.get("name") or "").strip()
            if t:
                titles.setdefault(t, []).append(f["id"])
        for t, fids in titles.items():
            if len(fids) > 1:
                notes.append(
                    "screens %s share the title %r — which one is the "
                    "user on? (GOV.UK: every question page gets its own "
                    "title)" % (", ".join(fids), t))
        # ---- state-variant frames: the same control, two labels ------
        # A rename that lands on one frame of a screen and not its twin
        # was invisible to everything: 3.2.4 joins a WIREFRAME element to
        # a FLOW element through a mapping, and tripwires compare mapped
        # siblings ACROSS artifacts — so two frames of one screen inside
        # one artifact were compared by nothing. That is the demo's
        # flagship beat, and in the v0.5 assessment it passed only
        # because the agent re-read the canvas by hand (R2-4).
        #
        # The pairing key already exists. references/wireframe.md: "in a
        # variant set, sibling screens share row baselines and text
        # alignment" — so positionally-corresponding blocks in two frames
        # of equal shape ARE the same control. An explicit
        # customData.variant_of wins when the author declares one.
        orders = {f["id"]: frame_reading_order(els, f["id"])
                  for f in frames}
        pairs_v = []
        declared = {f["id"]: (f.get("customData") or {}).get("variant_of")
                    for f in frames}
        for f in frames:
            base = declared.get(f["id"])
            if base and base in orders:
                pairs_v.append((base, f["id"]))
        if not pairs_v and len(frames) == 2:
            a_, b_ = frames[0]["id"], frames[1]["id"]
            if len(orders[a_]) == len(orders[b_]) and orders[a_]:
                pairs_v.append((a_, b_))
        for a_, b_ in pairs_v:
            oa, ob = orders.get(a_) or [], orders.get(b_) or []
            if len(oa) != len(ob):
                continue          # not a variant set after all
            for ea, eb in zip(oa, ob):
                la = (labels.get(ea["id"]) or "").strip()
                lb = (labels.get(eb["id"]) or "").strip()
                if not la or not lb or la == lb:
                    continue
                key = "var:%s:%s" % (aid or "<artifact>", slugify(ea["id"]))
                if waives and key in waives:
                    continue
                warnings.append(
                    "%s says %r and %s says %r for the same block — "
                    "state variants of one screen, so a rename that "
                    "landed on one and not the other reads as two "
                    "different controls. Same thing? Rename both. "
                    "Deliberately different (a held state, an error "
                    "copy)? waive {action: waive, key: %r, reason: ...}"
                    % (fname.get(a_, a_), la, fname.get(b_, b_), lb, key))
        for f in frames:
            order = frame_reading_order(els, f["id"])
            okinds = [(e.get("customData") or {}).get("kind")
                      for e in order]
            inputs = [e for e, k in zip(order, okinds) if k == "input"]
            # submit before its inputs (the 1.3.2-shaped structural WARN)
            for pos, k in enumerate(okinds):
                if k != "button":
                    continue
                after = sum(1 for kk in okinds[pos + 1:] if kk == "input")
                if after:
                    warnings.append(
                        "%s precedes %d of the inputs it submits in "
                        "reading order (screen %s) — linearised, the "
                        "action comes before its fields; move it below "
                        "the inputs, or say the layout intends it"
                        % (name(order[pos]["id"]), after,
                           fname[f["id"]]))
            # input labels: missing (3.3.2) and asterisk-marked (GOV.UK)
            for e in inputs:
                lbl = (labels.get(e["id"]) or "").strip()
                if not lbl:
                    warnings.append(
                        "input %s has no label — what is it asking "
                        "for? (label above the box, sentence case, no "
                        "colon)" % e["id"])
                elif "*" in lbl:
                    warnings.append(
                        "input %s marks required-ness with %r — write "
                        "\"(optional)\" in the optional fields' labels "
                        "instead; asterisks make everyone guess "
                        "(GOV.UK question-pages)" % (e["id"], lbl))
            # uniform input widths (Q4: width hints the expected answer)
            if len(inputs) >= 3:
                widths = {int(e.get("width", 0)) for e in inputs}
                if len(widths) == 1:
                    notes.append(
                        "all %d inputs on screen %s are %dpx wide — is "
                        "every answer the same length? (width table: "
                        "references/wireframe.md)"
                        % (len(inputs), fname[f["id"]],
                           widths.pop()))
            # declared sticky bar over inputs (Q7, 2.4.11-shaped)
            for s in els:
                if s.get("frameId") == f["id"] and inputs and \
                        (s.get("customData") or {}).get("kind") == \
                        "sticky-bar":
                    notes.append(
                        "%s is pinned and screen %s has %d input(s) — "
                        "when they tab to the last field, is it under "
                        "the bar? (the 2.4.11 question, cheapest to "
                        "answer here)" % (name(s["id"]),
                                          fname[f["id"]], len(inputs)))
        # help presence + slot drift (Q9, 3.2.6-shaped)
        helps = [e for e in els if e.get("frameId")
                 and (e.get("customData") or {}).get("kind") == "help"]
        if helps and len(frames) > 1:
            have = {e["frameId"] for e in helps}
            missing = [fname[f["id"]] for f in frames
                       if f["id"] not in have]
            if missing:
                notes.append(
                    "help lives on %d of %d screens — where does it "
                    "live on %s? (3.2.6: help in the same relative "
                    "slot on every screen)"
                    % (len(have), len(frames), ", ".join(missing[:3])))
            quads = {}
            for e in helps:
                fr = ix.get(e.get("frameId"))
                if fr is None:
                    continue
                cx = e.get("x", 0) + e.get("width", 0) / 2 - fr.get("x", 0)
                cy = e.get("y", 0) + e.get("height", 0) / 2 - fr.get("y", 0)
                q = ("top" if cy < fr.get("height", 1) / 2 else "bottom") \
                    + "-" + ("left" if cx < fr.get("width", 1) / 2
                             else "right")
                quads.setdefault(q, []).append(
                    fname.get(e["frameId"], e["frameId"]))
            if len(quads) > 1:
                notes.append(
                    "help drifts across screens (%s) — 3.2.6 wants the "
                    "same slot everywhere"
                    % "; ".join("%s on %s" % (q, ", ".join(v[:2]))
                                for q, v in sorted(quads.items())))
        # target size (Q11, 2.5.8-shaped; legal because frames are
        # declared 1:1 CSS px — references/wireframe.md). NOTE forever,
        # never a verdict: four of five exceptions are semantic
        inter = {}
        for e in els:
            if e.get("frameId") and \
                    (e.get("customData") or {}).get("kind") in \
                    INTERACTIVE_KINDS:
                inter.setdefault(e["frameId"], []).append(e)
        def centre(t):
            return (t.get("x", 0) + t.get("width", 0) / 2,
                    t.get("y", 0) + t.get("height", 0) / 2)

        for fid, group in inter.items():
            for i2, a2 in enumerate(group):
                for b2 in group[i2 + 1:]:
                    small = [t for t in (a2, b2)
                             if min(t.get("width", 0),
                                    t.get("height", 0)) < 24]
                    if not small:
                        continue
                    # W3C spacing test: a 24px circle centred on each
                    # undersized target must clear other targets, and
                    # two undersized circles must clear each other
                    hits = []
                    for s2 in small:
                        o2 = b2 if s2 is a2 else a2
                        scx, scy = centre(s2)
                        qx = max(o2.get("x", 0),
                                 min(scx, o2.get("x", 0) +
                                     o2.get("width", 0)))
                        qy = max(o2.get("y", 0),
                                 min(scy, o2.get("y", 0) +
                                     o2.get("height", 0)))
                        d2 = ((scx - qx) ** 2 + (scy - qy) ** 2) ** 0.5
                        if d2 < 12:
                            hits.append(d2)
                    if len(small) == 2:
                        (acx, acy), (bcx, bcy) = centre(a2), centre(b2)
                        cc = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
                        if cc < 24:
                            hits.append(cc)
                    if hits:
                        notes.append(
                            "%s and %s are closer than a thumb (%dpx "
                            "centre-to-neighbour) — 2.5.8 will ask for "
                            "24px targets or spacing; intentional? "
                            "(inline / essential / equivalent-control "
                            "exceptions are yours to claim)"
                            % (name(a2["id"]), name(b2["id"]),
                               int(min(hits))))
        # progress indicator (Q25) — question, never a rule; fires once
        # per artifact, then a registry waive keeps it quiet
        if not waived("q25"):
            prog = None
            for e in els:
                if e.get("type") == "text":
                    txt = (e.get("originalText") or e.get("text")
                           or "").strip()
                    if _PROGRESS_RE.search(txt) and \
                            not _STATUS_RE.match(txt):
                        prog = e.get("containerId") or e["id"]
                        break
            if prog is None:
                # dot-row tell: ≥3 small same-size shapes in one row
                # near a frame's top edge
                for fid, group in (
                        (f["id"], [e for e in els
                                   if e.get("frameId") == f["id"]
                                   and e.get("type") in
                                   ("rectangle", "ellipse")
                                   and e.get("width", 99) <= 40
                                   and e.get("height", 99) <= 16])
                        for f in frames):
                    fr = ix.get(fid)
                    if fr is None or len(group) < 3:
                        continue
                    top = fr.get("y", 0) + fr.get("height", 0) * 0.25
                    row2 = [e for e in group if e.get("y", 0) <= top]
                    row2.sort(key=lambda e: e.get("y", 0))
                    run2 = [e for e in row2
                            if abs(e.get("y", 0) -
                                   row2[0].get("y", 0)) <= 4] \
                        if row2 else []
                    sizes = {(int(e.get("width", 0)),
                              int(e.get("height", 0))) for e in run2}
                    if len(run2) >= 3 and len(sizes) == 1:
                        prog = run2[0]["id"]
                        break
            if prog is not None:
                notes.append(
                    "a progress indicator (%s) — does the *user* need "
                    "to know where they are, or do *you* need them to? "
                    "GDS removed a 12-step indicator from a live "
                    "service; completion, time and volume were "
                    "unchanged. Settle it, then waive with registry op "
                    "{action: waive, key: \"q25:%s\", reason: ...}"
                    % (name(prog), aid or "<artifact>"))

    # ---- WARNING: legibility -----------------------------------------
    for e in arrows:
        sb_id = (e.get("startBinding") or {}).get("elementId")
        eb_id = (e.get("endBinding") or {}).get("elementId")
        self_loop = sb_id is not None and sb_id == eb_id
        run = (e["points"][-1][0] ** 2 + e["points"][-1][1] ** 2) ** 0.5
        lbl = next((t for t in els if t.get("containerId") == e["id"]
                    and t.get("type") == "text"), None)
        # a self-loop has no straight run — its endpoints are close by
        # definition, so this check would false-fire on every reflexive
        # relationship ("rerun of" demoted its cardinality to a tooltip
        # for exactly this reason, r4). Long labels there go in tooltips.
        if lbl is not None and not self_loop and \
                run < lbl.get("width", 0) + 24:
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
            # declared containment (customData.parent) is nesting, not
            # collision — a card inside its shelf never lints
            pa = (a.get("customData") or {}).get("parent")
            pb = (b.get("customData") or {}).get("parent")
            if pa == b["id"] or pb == a["id"] or (pa and pa == pb):
                continue
            # same screen + near-full containment reads as nesting too
            # (v0.3): a shelf and its cards inside one frame shouldn't
            # need `parent` spelled out — but PARTIAL overlap between
            # frame siblings is exactly the bug this lint catches
            if a.get("frameId") and a.get("frameId") == b.get("frameId"):
                ox2 = min(a["x"] + a.get("width", 0),
                          b["x"] + b.get("width", 0)) - max(a["x"], b["x"])
                oy2 = min(a["y"] + a.get("height", 0),
                          b["y"] + b.get("height", 0)) - max(a["y"], b["y"])
                if ox2 > 0 and oy2 > 0:
                    inner = min(a.get("width", 1) * a.get("height", 1),
                                b.get("width", 1) * b.get("height", 1))
                    if ox2 * oy2 >= 0.9 * inner:
                        continue
            ox = min(a["x"] + a.get("width", 0), b["x"] + b.get("width", 0)) \
                - max(a["x"], b["x"])
            oy = min(a["y"] + a.get("height", 0),
                     b["y"] + b.get("height", 0)) - max(a["y"], b["y"])
            if ox > 0 and oy > 0:
                grown = (a.get("customData") or {}).get("auto_grown") or \
                    (b.get("customData") or {}).get("auto_grown")
                smaller = min(a.get("width", 1) * a.get("height", 1),
                              b.get("width", 1) * b.get("height", 1))
                if grown:
                    # a box that grew to fit its label gets no size
                    # slack: ANY overlap it causes is real (nothing
                    # reflows siblings — v0.3 assessment)
                    warnings.append(
                        "%s grew to fit its label and now overlaps %s — "
                        "move one clear or widen the box"
                        % (name(a["id"]), name(b["id"])))
                elif ox * oy > 0.25 * smaller:
                    warnings.append(
                        "%s and %s overlap — separate them"
                        % (name(a["id"]), name(b["id"])))
    # annotations were excluded from the v0 overlap loop entirely — the
    # demo shipped a note lying across a node for five rounds
    annos = [e for e in els if e.get("type") == "text"
             and role_of(e) == "annotation"]
    if len(annos) > 2:
        notes.append(
            "%d annotation callouts (budget: 2 per artifact) — fold the "
            "rest into chat or docs (references/layout.md)" % len(annos))
    # label↔label collision (live_test_2 B6): a label can be clear of its
    # own stroke and run yet sit on another arrow's label — stacked labels
    # read as one caption.
    #
    # Both this and the label↔node check below measure where the label is
    # DRAWN, not where it is stored. For a label bound to an arrow those
    # differ: the client re-centres it on the path (arrow_label_anchor),
    # and reading `la["x"]` instead is why a label sitting inside a
    # foreign box linted clean in the v0.5 assessment (R2-8).
    ix_all = {e["id"]: e for e in els}

    def drawn_box(t):
        """The label's on-canvas rect as `(x1, y1, x2, y2)`."""
        cont = ix_all.get(t.get("containerId"))
        lx, ly = t["x"], t["y"]
        if cont is not None and cont.get("type") in ("arrow", "line"):
            lx, ly = arrow_label_anchor(cont, t)
        return (lx, ly, lx + t.get("width", 0), ly + t.get("height", 0))

    bound_labels = [e for e in els if e.get("type") == "text"
                    and e.get("containerId")]
    boxes = {t["id"]: drawn_box(t) for t in bound_labels}
    for i_, la in enumerate(bound_labels):
        ax1, ay1, ax2, ay2 = boxes[la["id"]]
        for lb in bound_labels[i_ + 1:]:
            bx1, by1, bx2, by2 = boxes[lb["id"]]
            ox = min(ax2, bx2) - max(ax1, bx1)
            oy = min(ay2, by2) - max(ay1, by1)
            if ox > 6 and oy > 4:
                warnings.append(
                    "labels %r and %r overlap — nudge one clear"
                    % ((la.get("text") or "")[:24],
                       (lb.get("text") or "")[:24]))
    # label↔node: an arrow label landing on a box that is neither its
    # source nor its destination. Nothing checked this before v0.6 —
    # only free annotations were tested against nodes — so a connector
    # label could sit squarely inside a foreign entity and lint clean.
    for la in bound_labels:
        cont = ix_all.get(la.get("containerId"))
        if cont is None or cont.get("type") not in ("arrow", "line"):
            continue
        ends = {(cont.get("startBinding") or {}).get("elementId"),
                (cont.get("endBinding") or {}).get("elementId")}
        ax1, ay1, ax2, ay2 = boxes[la["id"]]
        for n in nodes:
            if n["id"] in ends:
                continue
            ox = min(ax2, n["x"] + n.get("width", 0)) - max(ax1, n["x"])
            oy = min(ay2, n["y"] + n.get("height", 0)) - max(ay1, n["y"])
            if ox > 8 and oy > 4:
                warnings.append(
                    "arrow label %r lands on %s, which is neither end of "
                    "its arrow — the label reads as that box's caption. "
                    "Re-route the arrow or shorten the label"
                    % ((la.get("text") or "")[:24], name(n["id"])))
                break
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
    # text that does not fit the box it is drawn in. MEASURED, not read
    # off the stored width: a stored extent is an estimate the client
    # re-derives, and trusting it is why three overflows shipped in the
    # v0.4 assessment — including a footnote that spilled 100px past its
    # frame. The agent cannot see any of this, so the lint is its only
    # tell. Covers composed rows (KPI values, entity attributes) too,
    # which the old containerId-only check never looked at.
    owners = {e["id"]: e for e in shapes}
    for t in els:
        if t.get("type") != "text":
            continue
        cd = t.get("customData") or {}
        oid = t.get("containerId") or cd.get("value_of") or \
            cd.get("attr_of") or cd.get("parent")
        owner = owners.get(oid or "")
        if owner is None:
            continue
        txt = t.get("text") or ""
        if not txt.strip():
            continue
        fs = t.get("fontSize") or 16
        # composed rows (KPI values, entity attributes) are emitted one
        # per line and never wrap, so width alone decides. Everything
        # else — bound labels, sticky notes, fixed-width text — is
        # wrapped by the renderer, so judge the WRAPPED height and only
        # call it too wide when a single word cannot fit.
        single_line = bool(cd.get("attr_of") or cd.get("value_of"))
        pad = 16 if single_line else 8
        room_w = int(owner.get("width") or 0) - pad
        room_h = int(owner.get("height") or 0) - 4
        if single_line:
            tw, th = text_dims(txt, fs)
            over_w, over_h = tw > room_w, False
        else:
            wrapped = wrap_label_text(txt.replace("\n", " "), room_w, fs)
            tw, th = text_dims(wrapped, fs)
            longest = max((text_dims(w, fs)[0] for w in txt.split()),
                          default=0)
            over_w, over_h = longest > room_w, th > room_h
        if over_w or over_h:
            warnings.append(
                "%r does not fit %s: needs ~%dx%dpx, the box gives %dx%dpx "
                "(%s) — widen the box, shorten the text, or move the detail "
                "to a tooltip"
                % (txt.replace("\n", " ")[:34], name(owner["id"]),
                   tw, th, max(room_w, 0), max(room_h, 0),
                   "too wide" if over_w and not over_h else
                   "too tall" if over_h and not over_w else
                   "too wide and too tall"))
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
                    # the apply post-pass auto-fans, so if they are still
                    # together the fan did not move them. It used to say
                    # "obstacles in every slot" — a cause it never
                    # measured and, on the one case anyone reproduced,
                    # not the cause at all: deleting every decoration
                    # left the warning standing, and what actually
                    # cleared it was an arrow dropping from 4 points to
                    # 3, because fan_attach_points only touches 2- and
                    # 3-point server-routed paths (brownfield BUG-05).
                    # Name what disqualified them, or nothing.
                    why = []
                    for aid in (id1, id2):
                        arr = ix.get(aid) or {}
                        npts = len(arr.get("points") or [])
                        if not server_owns_geometry(arr):
                            why.append("%s is user-shaped" % aid)
                        elif npts > 3:
                            why.append("%s has %d waypoints (the fan only "
                                       "moves 2- and 3-point paths)"
                                       % (aid, npts))
                    warnings.append(
                        "arrows %s and %s share an attach point on %s — "
                        "%s. Author waypoints via `mod points`, simplify "
                        "the path, or move the nodes apart"
                        % (id1, id2, name(tgt),
                           "the auto-fan could not move them: "
                           + "; ".join(why) if why else
                           "the auto-fan ran and left them together"))
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
    # the user's own elements are not the agent's to re-grid: nagging
    # about where someone dropped their sticky note is noise it can only
    # answer by moving their work (v0.4 capability assessment)
    offgrid = [e["id"] for e in shapes
               if (e.get("customData") or {}).get("author") != "user"
               and any(isinstance(e.get(k), (int, float)) and e[k] % 4
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
            if count > node_budget:
                notes.append("%d blocks in screen %s (budget: %d per "
                             "screen) — split the screen rather than "
                             "shrink the font"
                             % (count, name(fid), node_budget))
    elif len(nodes) > node_budget:
        notes.append("%d %s (budget: %d) — split the view rather than "
                     "shrink the font"
                     % (len(nodes), node_unit, node_budget))
    real_arrows = [a for a in arrows if a.get("type") == "arrow"]
    if len(real_arrows) > arrow_budget:
        notes.append("%d arrows (budget: %d) — the arrow budget is the "
                     "one that triggers a second view: edges collide, "
                     "nodes don't" % (len(real_arrows), arrow_budget))
    return {"errors": errors, "warnings": warnings, "notes": notes}


# canonical CONTEXT-FORMAT is '**Term**:' at line start, but agents in the
# wild write '**Term** — definition' (v0.1 acceptance session) and Markdown
# bullet lists ('- **Term**: …', this project's own CONTEXT.md). A glossary
# that parses to zero terms is indistinguishable from no glossary at all —
# every downstream lint goes silently dark, which is how a rejected synonym
# stayed on the domain view unflagged.
# A term name cannot itself contain `**`. Without that exclusion the
# non-greedy group backtracks straight through an alias entry —
# `**Excess Return** / **alpha**: …` captured `Excess Return** / **alpha`
# as ONE term, raw markdown and all, and the registry then reported both
# "settled term has no concept" and "concept references an undefined
# term" about the same line (v0.5 assessment R2-7). The agent had
# written that entry deliberately, as one term with two names, so nobody
# could read them as two metrics — exactly what the skill asks for.
TERM_RE = r"(?:[-*+]\s+)?\*\*((?:(?!\*\*).)+)\*\*\s*(?::|—|–|-{1,2}\s)"
# `**Term** / **alias**:` — one concept, two names, split by audience.
TERM_ALIAS_RE = (r"(?:[-*+]\s+)?\*\*((?:(?!\*\*).)+)\*\*\s*/\s*"
                 r"\*\*((?:(?!\*\*).)+)\*\*\s*(?::|—|–|-{1,2}\s)")


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


def parse_glossary_aliases(text):
    """CONTEXT.md → `{alias (lowercased): canonical term}`.

    One concept with two names, split by audience —
    `**Excess Return** / **alpha**: one number, two names` — is a single
    entry on purpose: two entries would read as two metrics. Both names
    are then legal on the canvas, and neither is an orphan term.

    Args:
        text: CONTEXT.md contents.

    Returns:
        Alias → canonical term, both stripped, the key lowercased.
    """
    out = {}
    for raw in text.splitlines():
        m = re.match(TERM_ALIAS_RE, raw.strip())
        if m:
            out[m.group(2).strip().lower()] = m.group(1).strip()
    return out


def _plausible_term(name):
    """Is a harvested bold span actually a TERM, not a sentence?

    A bolded clause inside an entry body ("**Switched off by default
    since Aug 2026**:") parses identically to an entry heading, and the
    minted "term" then nags as a concept-less glossary entry after the
    agent's last lint pass (r4, arm 3's parting false alarm). Terms are
    noun phrases: cap the word count.

    Args:
        name: The captured bold text.

    Returns:
        True when the span is term-shaped.
    """
    name = name.strip()
    return bool(name) and len(name) <= 48 and len(name.split()) <= 6


def parse_glossary_terms(text):
    """CONTEXT.md → ordered list of settled term names (canonical
    '**Term**:', the em-dash '**Term** — …' form, and bullet lists).

    An alias entry (`**Term** / **alias**:`) contributes its canonical
    name only — the alias is reachable through
    `parse_glossary_aliases`.
    """
    terms = []
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(TERM_ALIAS_RE, line) or re.match(TERM_RE, line)
        if m and _plausible_term(m.group(1)):
            terms.append(m.group(1).strip())
    return terms


def parse_glossary_pairs(text):
    """CONTEXT.md → ordered `(term, definition)` pairs.

    `parse_glossary_terms` returns names only, which is enough to lint
    with and not enough to hand someone: an exported diagram carrying the
    words but not what they mean explains nothing.

    Args:
        text: CONTEXT.md contents.

    Returns:
        `(term, definition)` for every settled term, definition flattened
        to one line (empty string when the entry is just a name).
    """
    pairs, term, body = [], None, []
    for raw in text.splitlines():
        line_ = raw.strip()
        m = re.match(TERM_ALIAS_RE, line_) or re.match(TERM_RE, line_)
        if m:
            if term is not None:
                pairs.append((term, " ".join(" ".join(body).split())))
            term = m.group(1).strip()
            rest = raw.strip()[m.end():].lstrip(":—- ").strip()
            body = [rest] if rest else []
        elif term is not None:
            line = raw.strip()
            if line.startswith("#"):
                pairs.append((term, " ".join(" ".join(body).split())))
                term, body = None, []
            elif line:
                body.append(line)
    if term is not None:
        pairs.append((term, " ".join(" ".join(body).split())))
    return pairs


def lint_registry(terms, registry, context_exists=False, aliases=None):
    """Registry-level discipline notes (NOTE tier, artifact-independent):
    settled glossary terms with no concept behind them (ADR 0007 — a term
    settling IS a concept being minted), unpaid view debt (ADR 0006 —
    `owed` types recorded at archetype time, cleared as views register),
    and views filed on the wrong concept (ADR 0010 — umbrella pile-up and
    the name-affinity misfile).

    Args:
        terms: Settled glossary term names.
        registry: The project registry (`model.json`).
        context_exists: Whether CONTEXT.md exists and is non-empty.
        aliases: `{alias: canonical}` from an audience-split entry, so a
            concept linked to either name resolves (v0.6).
    """
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
    # reverse direction (capability assessment): a concept CLAIMING a
    # glossary term the glossary doesn't hold is drift too
    term_set = {t.lower() for t in terms}
    term_set |= set(aliases or {})
    ghosts = [c for c in concepts if c.get("glossary")
              and str(c["glossary"]).lower() not in term_set]
    if ghosts and (terms or context_exists):
        shown = ", ".join(repr(c["glossary"]) for c in ghosts[:4])
        notes.append(
            "%d concept(s) reference glossary terms CONTEXT.md doesn't "
            "define: %s — settle the term into the glossary or drop the "
            "concept's glossary link" % (len(ghosts), shown))
    # ADR 0010 residual risk: a glossary that parses to nothing must be
    # loud, not indistinguishable from no glossary
    if context_exists and not terms:
        notes.append(
            "CONTEXT.md exists but zero glossary terms parsed — use the "
            "CONTEXT-FORMAT term shape (`**Term**:` or `**Term** — `) or "
            "the glossary discipline is running on air")
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
            other_tokens = [t for t in str(other.get("id") or "").split("-")
                            if t]
            for v in other.get("views") or []:
                vt = set(str(v).split("-"))
                # only nag when the claiming concept matches MORE of the
                # artifact id than its current holder does. Without this
                # the umbrella wins any tie on a project-prefixed id, so
                # `argus-dashboard` — correctly filed under `Dashboard` —
                # was told to move to `Argus`, the exact opposite of the
                # most-specific-concept rule (v0.4 capability assessment)
                if len(cid_tokens) <= len(set(other_tokens) & vt):
                    continue
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


def flow_reachable(els, cap=200):
    """Forward reachability over a flow scene's bound arrows (v0.4 WP2).

    Adjacency comes from startBinding/endBinding; cycles are cut by the
    visited set; ``cap`` bounds the walk far above any real artifact so
    a pathological scene can't stall an apply.

    Args:
        els: The flow artifact's elements.
        cap: Max visited nodes per start node.

    Returns:
        Dict mapping node id -> set of node ids reachable from it.
    """
    adj = {}
    for a in els:
        if a.get("type") != "arrow":
            continue
        s = _norm_binding(a.get("startBinding"))
        d = _norm_binding(a.get("endBinding"))
        if s and d and s != d:
            # self-edges (v0.8 reflexive arrows) are excluded: a node
            # must never count as reachable via its own loop
            adj.setdefault(s, set()).add(d)
    out = {}
    for start in adj:
        seen, stack = set(), [start]
        while stack and len(seen) < cap:
            cur = stack.pop()
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out[start] = seen
    return out


def cross_lint(scenes, artifact_types, registry, glossary_terms=None):
    """Cross-artifact lints (v0.4 WP2) — the checks that need more than
    one scene: 3.3.7 redundant entry along a mapped flow path, 3.2.4
    consistent identification over mapped elements, and the Q12
    whose-word check against domain entities and the glossary. All
    joins ride the registry mappings ("aid#element" refs); unmapped
    elements never fire 3.3.7/3.2.4 (ruling Q29+).

    Args:
        scenes: {artifact_id: elements} for every artifact.
        artifact_types: {artifact_id: type} (wireframe/flow/domain/...).
        registry: The registry (mappings + waives are read).
        glossary_terms: Optional list of settled CONTEXT.md terms.

    Returns:
        {artifact_id: {"errors": [...], "warnings": [...],
        "notes": [...]}} — only artifacts with findings appear.
    """
    out = {}

    def add(aid, tier, msg):
        out.setdefault(aid, {"errors": [], "warnings": [],
                             "notes": []})[tier].append(msg)

    waives = (registry or {}).get("waives") or {}
    label_maps = {aid: label_map(els) for aid, els in scenes.items()}
    policies = (registry or {}).get("divergence_policies") or []

    def declared_divergent(m, member_types):
        """Has this mapping's naming divergence already been ruled on?

        The tripwire check honours `intentionally-divergent`; this lint
        never did, so it went on asking 'same action, N names; pick one?'
        about a divergence the agent had explained rounds earlier (v0.4
        capability assessment). A scope that names no label-ish verb does
        not cover a naming complaint.
        """
        naming = {"renamed", "label_renamed", "entity_renamed"}

        def scoped(kinds):
            return (not kinds) or bool(naming & set(kinds))
        if str(m.get("note") or "").startswith("intentionally-divergent"):
            if scoped(m.get("kinds")):
                return True
        for pol in policies:
            if pol.get("concept") and pol["concept"] != m.get("concept"):
                continue
            if pol.get("types") and not member_types <= set(pol["types"]):
                continue
            if scoped(pol.get("kinds")):
                return True
        return False

    # mapping joins: every (wireframe member, flow member) pair
    pairs, divergent = [], set()
    for mi, m in enumerate((registry or {}).get("mappings") or []):
        wf, fl = [], []
        for ref in m.get("elements") or []:
            if "#" not in ref:
                continue
            aid, eid = ref.split("#", 1)
            t = artifact_types.get(aid)
            if t == "wireframe":
                wf.append((aid, eid))
            elif t == "flow":
                fl.append((aid, eid))
        mine = [(wa, we, fa, fe, mi) for wa, we in wf for fa, fe in fl]
        pairs.extend(mine)
        if declared_divergent(m, {artifact_types.get(a.split("#", 1)[0])
                                  for a in m.get("elements") or []}):
            divergent.update(mine)

    # ---- 3.2.4 consistent identification (mapped elements only) ------
    # Keyed on (flow step, MAPPING), not the flow step alone. A single
    # mapping declaring N wireframe members against one flow step is a
    # compression the user asserted — three real toggles switched
    # individually, one box at a lower resolution — and asking it to
    # "pick one name" would delete two controls while splitting it into
    # three mappings would assert three steps that do not exist. Both
    # remedies the check proposed were destructive, so its premise was
    # wrong (v0.6 assessment r3-8). Disagreement ACROSS mappings is
    # still 3.2.4 and still fires: that is two parties naming the same
    # step differently, which is the case worth catching.
    by_flow, by_label = {}, {}
    for wa, we, fa, fe, mi in pairs:
        lbl = (label_maps.get(wa, {}).get(we) or "").strip()
        if not lbl or (wa, we, fa, fe, mi) in divergent:
            continue
        by_flow.setdefault((fa, fe), {}).setdefault(mi, set()) \
               .add((wa, we, lbl))
        by_label.setdefault(lbl.lower(), set()).add((wa, we, fa, fe))
    for (fa, fe), by_mapping in sorted(by_flow.items()):
        members = {x for grp in by_mapping.values() for x in grp}
        lbls = sorted({lbl for _, _, lbl in members})
        aid0 = sorted(members)[0][0]
        if len(by_mapping) > 1 and len(lbls) > 1 and \
                "324:%s:%s" % (aid0, slugify(fe)) not in waives:
            add(aid0, "notes",
                "%s all map to %s#%s through %d separate mappings — same "
                "action, %d names; pick one? (3.2.4: one function, one "
                "label). Deliberate? annotate a mapping "
                "intentionally-divergent, or waive "
                "{action: waive, key: '324:%s:%s', reason: ...}"
                % (" / ".join(repr(x) for x in lbls), fa, fe,
                   len(by_mapping), len(lbls), aid0, slugify(fe)))
    for lbl, refs in sorted(by_label.items()):
        flows = sorted({(fa, fe) for _, _, fa, fe in refs})
        if len(flows) > 1:
            add(sorted(refs)[0][0], "warnings",
                "%r maps to different flow steps (%s) — same word, "
                "different consequences; the more dangerous 3.2.4 "
                "case: rename one"
                % (lbl, ", ".join("%s#%s" % f for f in flows)))

    # ---- 3.3.7 redundant entry (Q6) along reachable mapped frames ----
    reach = {fa: flow_reachable(scenes.get(fa) or [])
             for fa in {p[2] for p in pairs}}
    ixs = {aid: {e["id"]: e for e in els}
           for aid, els in scenes.items()}
    frame_nodes = {}
    for wa, we, fa, fe, _mi in pairs:
        el = ixs.get(wa, {}).get(we)
        if el is None:
            continue
        fid = el["id"] if el.get("type") == "frame" else el.get("frameId")
        if fid:
            frame_nodes.setdefault((wa, fid), set()).add((fa, fe))

    def frame_inputs(wa, fid):
        return {(label_maps[wa].get(e["id"]) or "").strip().lower()
                for e in scenes.get(wa) or []
                if e.get("frameId") == fid
                and (e.get("customData") or {}).get("kind") == "input"} \
            - {""}

    seen_337 = set()
    for (wa, fida), nodes_a in sorted(frame_nodes.items()):
        for (wb, fidb), nodes_b in sorted(frame_nodes.items()):
            if (wa, fida) == (wb, fidb):
                continue
            hops = any(fa == fb and nb in (reach.get(fa, {}).get(na)
                                           or ())
                       for fa, na in nodes_a for fb, nb in nodes_b)
            if not hops:
                continue
            for lbl in sorted(frame_inputs(wa, fida) &
                              frame_inputs(wb, fidb)):
                key = (*sorted([(wa, fida), (wb, fidb)]), lbl)
                if key in seen_337:
                    continue
                seen_337.add(key)
                na = (ixs[wa].get(fida) or {}).get("name") or fida
                nb = (ixs[wb].get(fidb) or {}).get("name") or fidb
                add(wa, "notes",
                    "%r is asked on %s and again on %s (same flow "
                    "path) — why twice? (3.3.7's own exceptions — "
                    "essential, security, stale data — are yours to "
                    "claim)" % (lbl, na, nb))

    # ---- Q12: whose word is this — yours or theirs? ------------------
    entity_labels = set()
    for aid, t in artifact_types.items():
        if t == "domain":
            lm = label_maps.get(aid) or {}
            for e in scenes.get(aid) or []:
                if (e.get("customData") or {}).get("kind") == "entity":
                    lbl = (lm.get(e["id"]) or "").strip()
                    if lbl:
                        entity_labels.add(lbl)
    terms = entity_labels | {t.strip() for t in (glossary_terms or [])
                             if t and t.strip()}
    seen_q12 = set()
    for aid, t in sorted(artifact_types.items()):
        if t != "wireframe":
            continue
        lm = label_maps.get(aid) or {}
        for e in scenes.get(aid) or []:
            if e.get("type") == "frame" or \
                    role_of(e) in ("label", "pin", "annotation",
                                   "decoration"):
                continue
            lbl = (lm.get(e["id"]) or "").strip()
            if not lbl or lbl not in terms:
                continue
            key = "q12:%s:%s" % (aid, slugify(lbl))
            # one key, one question. A screen drawn in three states —
            # normal / stale / disabled — repeats every label three
            # times, and all three share this key, so one waive already
            # silenced all three: they were never three findings (v0.4
            # capability assessment; the skill's own "N tripwires with
            # one cause is one tripwire")
            if key in waives or key in seen_q12:
                continue
            seen_q12.add(key)
            add(aid, "notes",
                "label %r on %s matches the domain term — whose word "
                "is this, yours or theirs? Do users say %r? Settle "
                "it, then waive with registry op {action: waive, "
                "key: %r, reason: ...}" % (lbl, e["id"], lbl, key))

    # ---- unmapped KPIs (WP5/D9) --------------------------------------
    # Tripwires are exactly as good as the mapping discipline: the
    # flagship rename fired NOTHING in both run-4 arms because kpi-alpha
    # was in no mapping, and nothing nagged. A KPI is a number some node
    # computes; while a flow exists, an unmapped tile is a drift
    # detector that was never armed.
    if any(t == "flow" for t in artifact_types.values()):
        mapped_refs = set()
        for m in (registry or {}).get("mappings") or []:
            mapped_refs.update(m.get("elements") or [])
        for aid, t in sorted(artifact_types.items()):
            if t != "wireframe":
                continue
            if "kpimap:%s" % aid in waives:
                continue
            lm = label_maps.get(aid) or {}
            loose = [(e["id"], lm.get(e["id"]) or e["id"])
                     for e in scenes.get(aid) or []
                     if (e.get("customData") or {}).get("kind") == "kpi"
                     and "%s#%s" % (aid, e["id"]) not in mapped_refs]
            if loose:
                add(aid, "notes",
                    "%d KPI tile(s) unmapped: %s — map each to the node "
                    "that computes it (that mapping IS the drift "
                    "detector; a rename on either side then trips), or "
                    "waive {action: waive, key: %r, reason: ...}"
                    % (len(loose),
                       ", ".join(repr(lbl) for _, lbl in loose[:4]),
                       "kpimap:%s" % aid))
    return out


def project_lint(project, els, registry=None, artifact_type=None,
                 aid=None):
    """lint_layout + lint_glossary (+ registry discipline when the caller
    passes the registry) with the project context resolved: the
    co-authored glossary is project_knowledge/CONTEXT.md; the
    multi-context map convention is a root-level CONTEXT-MAP.md.

    Args:
        project: The Project (path context).
        els: The artifact's elements.
        registry: Optional registry — enables registry lints and the
            per-artifact budget override lookup.
        artifact_type: Optional type for type-aware budgets (v0.3).
        aid: Optional artifact id for the budget override lookup.
    """
    budget, waives = None, None
    if registry is not None and aid:
        budget = (registry.get("budgets") or {}).get(aid)
        waives = registry.get("waives") or {}
    lint = lint_layout(els, artifact_type=artifact_type, budget=budget,
                       waives=waives, aid=aid)
    avoid, terms, aliases = {}, [], {}
    ctx = project.pk / "CONTEXT.md"
    ctx_exists = False
    try:
        if ctx.exists():
            text = ctx.read_text(encoding="utf-8")
            ctx_exists = bool(text.strip())
            avoid = parse_glossary_avoid(text)
            terms = parse_glossary_terms(text)
            aliases = parse_glossary_aliases(text)
    except OSError:
        pass
    has_map = (project.root / "CONTEXT-MAP.md").exists()
    extra = lint_glossary(els, avoid, has_map)
    out = {k: lint[k] + extra[k] for k in ("errors", "warnings", "notes")}
    # registry findings are project-scope: lint_registry never sees `els`
    # or `aid`, so appending them to every per-artifact call copied the
    # same note into every bucket AND the registry bucket — one finding
    # about one concept read as four problems across four artifacts, and
    # inflated every LINT_DEBT count (v0.4 capability assessment). The
    # registry-scope call is the one made without an artifact.
    if registry is not None and aid is None:
        out["notes"] = out["notes"] + lint_registry(terms, registry,
                                                    ctx_exists, aliases)
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
        self._dry_run = False         # inside _sandbox(): writers are no-ops
        # WP2 (report-and-repair): findings computed on the RAW disk
        # scenes before validate_scene repairs them, per-load and never
        # persisted — a persisted copy would go stale the moment a commit
        # fixes the reference
        self.referential = {}
        self.raw_hashes = {}          # {aid: scene_hash of the raw disk scene}
        self.scene_repairs = []       # load-time ART-* repairs, this load
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
        self.artifact_files = {}   # image blobs (fileId -> dataURL entry)
        self.referential = {}
        self.raw_hashes = {}
        self.scene_repairs = []
        raw_scenes = {}
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
            # WP2: hold the RAW scene before any repair — the referential
            # pass reports against it, and catch_up uses its hash to tell
            # a genuine outside edit from the loader's own repair work.
            # Deep-copied: validate_scene repairs the SAME dicts in
            # place, and a shared reference would silently hand the pass
            # the repaired scene — the exact unreachability being fixed.
            raw_els = doc.get("elements") if isinstance(doc, dict) else None
            if isinstance(raw_els, list):
                raw_scenes[aid] = json.loads(json.dumps(raw_els))
                self.raw_hashes[aid] = content_fingerprint(raw_scenes[aid])
            doc, art_issues = validate_scene(doc, aid)
            if doc is None:
                continue
            doc, _ = apply_migrations(doc, "artifact", f, self.p, self.log)
            doc = normalize_scene_doc(doc)
            for i in art_issues:
                self.issues.append(i.to_dict())
                self.scene_repairs.append(i.to_dict())
                self.log("repair: %s %s" % (i.code, i.msg))
            self.scenes[aid] = doc["elements"]
            self.artifact_meta[aid] = doc.get("wysiwyg", {})
            if doc.get("files"):
                self.artifact_files[aid] = doc["files"]
        self.referential = referential_findings(raw_scenes, reg,
                                                set(self.scenes.keys()))
        for scope, tiers in sorted(self.referential.items()):
            for tier, msgs in tiers.items():
                for msg in msgs:
                    self.log("referential %s [%s]: %s"
                             % (tier[:-1].upper(), scope, msg))
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
               new_meta=None, reconciliation=False, extra_facts=None,
               resolved_pins=None, new_files=None):
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
            changed_elements = {}
            new_norm_by_aid = {}
            sentinel_by_aid = {}
            sig = self.config.get("significant_attrs")
            for aid, els in sorted(new_scenes.items()):
                old = (base_state.get(aid) or {}).get("elements", [])
                if author == "user":
                    # mutate the RAW scene list itself — _write_artifact
                    # persists new_scenes[aid], and a state change that
                    # reached only the record would be the record-vs-file
                    # divergence this campaign kills
                    _interpret_user_composites(els, old)
                new_norm = [normalize_element(e) for e in els
                            if not e.get("isDeleted")]
                new_norm_by_aid[aid] = new_norm
                diff = diff_scenes(old, new_norm, sig)
                if not diff["changes"] and aid in base_state and not \
                        (new_meta or {}).get(aid):
                    continue
                consequences = mark_consequences(diff)
                atype = ((new_meta or {}).get(aid) or {}).get(
                    "artifact_type") or self.artifact_type(aid)
                facts = semantic_facts(old, new_norm, diff, atype,
                                       self.tier_of(atype), consequences)
                # intent facts the differ cannot see (e.g. `rerouted` from
                # an explicit points mod — its attr entries are derived,
                # so without this the batch narrates as an empty save)
                for f in (extra_facts or {}).get(aid, []):
                    facts.append(dict(f))
                for f in facts:
                    if f.get("consequence_of") is None and \
                            f["fact"] != "saved_no_changes" and f["element"] \
                            and f["fact"] in DIVERGENCE_VERBS:
                        # keep the VERB, not just the identity: a mapping
                        # annotated "intentionally divergent" for one kind
                        # of change used to go deaf to every other kind
                        changed_elements.setdefault(
                            "%s#%s" % (aid, f["element"]), set()
                        ).add(f["fact"])
                by_element = self._by_element(diff, facts, consequences,
                                              new_norm, old)
                sentinel_by_aid[aid] = diff.get("sentinel_suppressed", 0)
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

            # WP2 (the r4 headline): a deletion must NAME what it broke.
            # Nothing validated a reference after its target was gone —
            # bindings dangled on disk, mapping members pointed at
            # corpses, notes floated — and every silence compounded.
            for aid, part in artifacts.items():
                deleted = {c["element"]["id"] for c in part["changes"]
                           if c["op"] == "del"}
                if not deleted:
                    continue
                old_els = (base_state.get(aid) or {}).get("elements", [])
                old_by_id = {e["id"]: e for e in old_els}
                for e in new_norm_by_aid.get(aid, []):
                    if e.get("type") in ("arrow", "line"):
                        oldb = old_by_id.get(e["id"]) or {}
                        for battr in ("startBinding", "endBinding"):
                            was = ((oldb.get(battr) or {})
                                   .get("elementId"))
                            now = ((e.get(battr) or {}).get("elementId"))
                            if was in deleted and \
                                    (now is None or now in deleted):
                                part["facts"].append(
                                    {"fact": "arrow_orphaned",
                                     "element": e["id"], "target": was,
                                     "side": "start" if battr ==
                                             "startBinding" else "end"})
                    anchor = (e.get("customData") or {}).get("annotates")
                    if anchor in deleted:
                        part["facts"].append(
                            {"fact": "note_orphaned", "element": e["id"],
                             "target": anchor})
                for m in self.registry.get("mappings") or []:
                    for ref in m.get("elements") or []:
                        if "#" not in ref:
                            continue
                        maid, meid = ref.split("#", 1)
                        if maid == aid and meid in deleted:
                            part["facts"].append(
                                {"fact": "mapping_dangling", "element": None,
                                 "concept": m.get("concept"), "ref": ref})

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
                        # resolve_pin deletes the glyph too — that deletion
                        # is resolution, not the user's "not worth
                        # explaining" dismissal
                        p["status"] = "resolved" if p["id"] in \
                            (resolved_pins or ()) else "dismissed"
                    elif p.get("element") and p["element"] in deleted_all:
                        p["status"] = "pruned"
            # user comment pins (Phase 5): a ❓ element the USER drew that
            # the registry doesn't know becomes a user-authored question
            # in the agent's queue (PIN_DEBT surfaces it)
            if author == "user":
                known_pin_ids = {p["id"] for p in self.registry["pins"]}
                for aid2, part in artifacts.items():
                    for c in part["changes"]:
                        if c["op"] != "add":
                            continue
                        elx = c["element"]
                        cdx = elx.get("customData") or {}
                        if cdx.get("role") == "pin" and \
                                elx["id"] not in known_pin_ids:
                            self.registry["pins"].append({
                                "id": elx["id"], "artifact": aid2,
                                "element": cdx.get("target"),
                                "question": cdx.get("question") or "",
                                "status": "open", "answer": None,
                                "direction": "user",
                                "asked_at_revn": revn,
                                "round": self.registry.get("round", 0),
                                "detail": None, "examples": []})
            # PIN_DEBT bookkeeping: an open question whose TARGET keeps
            # changing is aging badly — count the edits
            for p in self.registry["pins"]:
                if p.get("status") in ("open", "answered") and \
                        p.get("element"):
                    key = "%s#%s" % (p.get("artifact"), p["element"])
                    if key in changed_elements:
                        p["target_edits"] = p.get("target_edits", 0) + 1

            # registry ops apply BEFORE the summary so a registry-only
            # batch headlines its registry work instead of "saved without
            # changing anything" (capability assessment finding)
            # a registry op naming the artifact its OWN batch creates was
            # rejected outright — "set_budget needs an existing artifact"
            # — because a create does not reach artifact_meta until the
            # write below, after the ops. So the documented "over budget
            # → record it with a reason" workflow could not be done in
            # the batch that drew the thing: it cost a second revision
            # plus a false NOTE for the overrun it was in the act of
            # justifying (brownfield BUG-03). Publish first; r3-10's
            # re-read below still picks up what the ops then did.
            seeded = self._seed_created_meta(new_meta)
            reg_changes = []
            if registry_ops:
                reg_errors = []
                try:
                    reg_changes = self._apply_registry_ops(registry_ops,
                                                           reg_errors)
                    if reg_errors:
                        raise BatchError(reg_errors)
                except BatchError:
                    # a rejected batch must not leave a phantom artifact
                    # with no scene and no file
                    for aid2 in seeded:
                        self.artifact_meta.pop(aid2, None)
                    raise
                # a registry op can change artifact META, and `meta` was
                # snapshotted above — BEFORE these ran. A rename landing
                # in the same batch as a drawing was therefore written
                # back stale by the artifact write below, AND stored
                # stale in this record, so checking out the very
                # revision that renamed the artifact restored the old
                # name (v0.6 assessment r3-10). Re-read what the ops
                # actually touched; reg_changes already names it.
                for ch in reg_changes:
                    aid2 = ch.get("artifact")
                    if ch.get("action") == "artifact_renamed" and \
                            aid2 in artifacts:
                        artifacts[aid2]["meta"] = dict(
                            self.artifact_meta.get(aid2) or {})

            facts_flat = [f for a in artifacts.values() for f in a["facts"]]
            if not facts_flat:
                facts_flat = [{"fact": "saved_no_changes", "element": None}]
            # WP3: the real sentinel count — this was hardcoded 0, so
            # `suppressed` under-reported every sentinel-suppressed diff
            sentinel = sum(sentinel_by_aid.get(a, 0) for a in artifacts)
            summary = mechanical_summary(facts_flat, sentinel)
            if reg_changes and all(f["fact"] == "saved_no_changes"
                                   for f in facts_flat):
                summary["headline"] = _registry_headline(reg_changes)
            new_map_keys = {
                self._mapping_key(ch)
                for ch in reg_changes
                if ch.get("action") == "add_mapping" and ch.get("elements")}
            # a batch that RESOLVES a tripwire and executes its answer
            # (propagating the change to the sibling) must not re-trip the
            # same mapping in the other direction — that's convergence,
            # not divergence (found by the Argus acceptance run)
            resolved_tw = {o.get("id") for o in (registry_ops or [])
                           if o.get("action") == "resolve_tripwire"}
            for t in self.registry["tripwires"]:
                if t.get("id") in resolved_tw and t.get("mapping"):
                    new_map_keys.add(t["mapping"])
            tripwires = self._check_tripwires(changed_elements, revn,
                                              new_map_keys)
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
                # origin stamp (live_test_2 B7): multi-session writes must
                # stay attributable
                "origin": {"pid": os.getpid()},
                "selection_at_save": selection or [],
                "user_note": user_note,
                "reconciliation": bool(reconciliation),
                "artifacts": artifacts,
                "registry_changes": reg_changes,
                "summary": summary,
                "tripwires": all_tripwires,
                "tripwires_muted": list(
                    getattr(self, "_muted_renames", [])),
            }
            record["short_id"] = hashlib.sha1(
                json.dumps(record, sort_keys=True, default=str)
                .encode("utf-8")).hexdigest()[:7]

            # persist: record file, artifact files, registry
            rec_path = self.p.saves_dir / ("%04d-%s.json" % (revn, slug))
            write_json(rec_path, record)
            self.records[revn] = record
            for aid, fmap in (new_files or {}).items():
                if isinstance(fmap, dict) and fmap:
                    merged = dict(self.artifact_files.get(aid) or {})
                    merged.update(fmap)
                    self.artifact_files[aid] = merged
            for aid in artifacts:
                if aid in new_scenes:
                    self._write_artifact(aid, new_scenes[aid],
                                         artifacts[aid]["meta"])
            self.registry["revn"] = revn
            if forked:
                # the outgoing branch keeps its own registry; the new one
                # inherits the live sections, which are a copy of the
                # drawing at this point (r3-17)
                self._stash_scope(self.registry["head"])
                self.registry["branches"].append(
                    {"name": branch, "head": revn, "archived": False,
                     # `head` ADVANCES, so without these a branch stops
                     # identifying where it began after one more save,
                     # and no surface joined it to its creating record
                     # (r3-11). The rationale is NOT copied here: it
                     # lives on that save's headline, and a second copy
                     # would drift from the words the user can edit.
                     "forked_from": self.registry["head"],
                     "forked_at_revn": base,
                     "origin_revn": revn})
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

    def _seed_created_meta(self, new_meta):
        """Publish a batch's about-to-be-created artifacts into the cache.

        Registry ops run before the artifact write, so without this a
        `set_budget` or `rename_artifact` naming the artifact its own
        batch creates is rejected as unknown (brownfield BUG-03).
        `upsert_concept` has carried its own workaround for this shape
        since v0.6 — `view_types`, which stays for back-compat but is no
        longer the only way an op can learn a new artifact's type.

        Args:
            new_meta: `{artifact_id: meta}` for the batch, or None.

        Returns:
            The ids actually seeded, so a rejected batch can un-publish
            them rather than leave a phantom artifact behind.
        """
        seeded = []
        for aid, meta in (new_meta or {}).items():
            if aid not in self.artifact_meta:
                self.artifact_meta[aid] = dict(meta or {})
                seeded.append(aid)
        return seeded

    @contextlib.contextmanager
    def _sandbox(self):
        """Run ops against throwaway state — nothing reaches disk.

        `check_batch` used to guard `self.registry` alone, so
        `rename_artifact` escaped it twice over: it mutates
        `self.artifact_meta[aid]` AND calls `_write_artifact`, and both
        live outside that guard. A dry run therefore renamed an artifact
        on disk with no revn, no save record and nothing to revert
        (v0.6 assessment r3-12).

        The guard is on the WRITER, not the caller: `_write_artifact` is
        a no-op while this is open, so the next registry action that
        touches disk is safe without anyone remembering a flag.

        Yields:
            None. State is restored on the way out, including on the
            error paths — a leaked `_dry_run` would silently swallow
            real writes.
        """
        saved_reg, saved_meta = self.registry, self.artifact_meta
        saved_scenes = self.scenes
        self.registry = copy.deepcopy(saved_reg)
        self.artifact_meta = copy.deepcopy(saved_meta)
        self.scenes = dict(saved_scenes)
        self._dry_run = True
        try:
            yield
        finally:
            self.registry, self.artifact_meta = saved_reg, saved_meta
            self.scenes = saved_scenes
            self._dry_run = False

    def _write_artifact(self, aid, els, meta):
        """Persist one artifact's scene + meta, and refresh the caches.

        Args:
            aid: Artifact id (also the filename stem).
            els: The scene's elements, pre-normalization.
            meta: The artifact's `wysiwyg` block — name, type, migrations.
        """
        if self._dry_run:
            # inside _sandbox(): the caller is measuring, not committing
            return
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
        if self.artifact_files.get(aid):
            doc["files"] = self.artifact_files[aid]
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
    @staticmethod
    def _annotation_covers(scope, verbs):
        """Does a divergence annotation excuse THIS change?

        An unscoped annotation (no `kinds`) keeps the original blanket
        behaviour. A scoped one excuses only the verbs it names, which is
        the whole point: a mapping annotated because three KPI tiles fan
        into one store was also silently excusing a later rename of one of
        them, so the dashboard and the pipeline drifted apart under an
        annotation that had nothing to say about labels (v0.4 capability
        assessment — the demo's flagship tripwire never fired).

        Args:
            scope: The annotation's `kinds` list, or None/empty.
            verbs: Fact names that fired on the changed element.

        Returns:
            True when the annotation covers every verb that fired.
        """
        if not scope:
            return True
        return bool(verbs) and set(verbs) <= set(scope)

    @staticmethod
    def _parse_kinds(op, i, errors):
        """Read an optional `kinds` scope off a registry op.

        Args:
            op: The registry op.
            i: Its index in the batch, for the error message.
            errors: Collector appended to when `kinds` is malformed.

        Returns:
            The verb list, or None when the op carries no scope.
        """
        kinds = op.get("kinds")
        if kinds is None:
            return None
        if not isinstance(kinds, list) or \
                not all(isinstance(k, str) and k for k in kinds):
            errors.append(
                "registry op %d: `kinds` must be a list of fact names, e.g. "
                "[\"cardinality_changed\"] — the divergence this annotation "
                "excuses. Omit it to excuse every kind of change." % i)
            return None
        return sorted(set(kinds))

    def _check_tripwires(self, changed_elements, revn, new_mapping_keys=()):
        self._muted_renames = []
        out = []
        if not changed_elements:
            return out
        for m in self.registry["mappings"]:
            if self._mapping_key(m) in new_mapping_keys:
                # a mapping declared in THIS batch cannot have diverged in
                # it — the elements it joins usually land in the same
                # commit (registry ops now apply before this check)
                continue
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
            fired = set()
            for e in hits:
                fired |= set(changed_elements.get(e) or ())
            naming = fired & {"renamed", "label_renamed", "entity_renamed",
                              "relationship_relabeled"}
            if note.startswith("intentionally-divergent") and \
                    self._annotation_covers(m.get("kinds"), fired):
                if naming:
                    # WP5 (D10/E-7): a RENAME muted by a ruling made
                    # about value/display drift is armed silence — the
                    # scope the agent chose for "a tile's wording belongs
                    # to the screen" is exactly the set the flagship
                    # rename beat needs. Say it happened; the ruling
                    # still holds.
                    self._muted_renames.append(
                        "%s changed (%s) but its divergence ruling %r "
                        "scopes naming out — deliberate?"
                        % (hits[0], "/".join(sorted(naming)),
                           note[:60]))
                continue
            if self._policy_covers(m, fired):
                if naming:
                    self._muted_renames.append(
                        "%s changed (%s) under a divergence policy that "
                        "scopes naming out — deliberate?"
                        % (hits[0], "/".join(sorted(naming))))
                continue
            # ONE question per mapping per save. This used to be a nested
            # loop over (changed × sibling), so renaming one element of a
            # four-member mapping asked three questions — while every
            # suppression path above (intentionally-divergent,
            # _annotation_covers, _policy_covers) already reasons once
            # per mapping, so the code only disagreed with itself on the
            # emitting side. The agent that met it said so: "fired three
            # tripwires — all one cause, so I've answered the cause
            # rather than the count", then wrote ONE annotation that
            # resolved all three (v0.6 assessment r3-7).
            #
            # `changed`/`sibling` stay singular and populated with the
            # first of each: the UI anchors its ? mark on an element, and
            # a v0.6 registry still reads.
            entry = {"mapping": self._mapping_key(m), "changed": hits[0],
                     "sibling": siblings[0], "changed_all": list(hits),
                     "siblings": list(siblings), "kind": "divergence"}
            out.append(entry)
            reg_entry = dict(entry)
            ch = _tw_names(hits)
            sb = _tw_names(siblings)
            reg_entry.update({
                "id": "tw-%d-%d" % (revn, len(out)),
                # a fired tripwire must be visible at fire time —
                # the record entry mirrors id+question so the apply
                # response can name it (v0.3 assessment bug: fired
                # silently, agent found it rounds later via status)
                "save": revn, "status": "open",
                # answerable in place (like pins) — defaults the
                # agent may sharpen via annotate_tripwire
                "question": "%s changed but %s %s didn't. Divergence, or "
                            "should it propagate?"
                            % (ch, "its mapped sibling" if
                               len(siblings) == 1 else "its %d mapped "
                               "siblings" % len(siblings), sb),
                "choices": ["Intentional divergence — keep both",
                            "Propagate to the sibling"],
                "detail": (
                    "These elements are declared views of the "
                    "same thing (mapping %s). At save %d, %s "
                    "changed while %s stayed put — so right now "
                    "the views disagree.\n\n"
                    "• 'Intentional divergence' records the "
                    "difference as deliberate: the mapping stays, "
                    "annotated so this mapping never trips again for "
                    "this reason.\n"
                    "• 'Propagate' asks the agent to carry "
                    "the change into %s in its next revision — "
                    "narrated, and nothing is touched until you "
                    "answer.\n\n"
                    "Free-text works too: name a third option, or "
                    "explain what the views actually mean."
                    % (self._mapping_key(m), revn, ch, sb, sb)),
                "examples": [],
                "answer": None,
            })
            self.registry["tripwires"].append(reg_entry)
            entry["id"] = reg_entry["id"]
            entry["question"] = reg_entry["question"]
        return out

    def _policy_covers(self, m, verbs=()):
        """Does a class-level divergence policy cover this change?

        One ruling ('wireframe blocks name report sections, flow steps
        name the work — meant to differ') silences the whole class,
        instead of the N identical per-mapping notes the demo session had
        to write. A policy carrying `kinds` silences only those verbs, on
        the same reasoning as `_annotation_covers`.

        Args:
            m: The mapping being tested.
            verbs: Fact names that fired on the changed side.

        Returns:
            True when a policy excuses this divergence.
        """
        member_types = {self.artifact_type(e.split("#", 1)[0])
                        for e in (m.get("elements") or [])}
        for pol in self.registry.get("divergence_policies") or []:
            if pol.get("concept") and pol["concept"] != m.get("concept"):
                continue
            if pol.get("types") and not member_types <= set(pol["types"]):
                continue
            if not self._annotation_covers(pol.get("kinds"), verbs):
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
                                        "note": op.get("note"),
                                        "kinds": self._parse_kinds(
                                            op, i, errors)})
            elif action == "annotate_mapping":
                pattern = op.get("pattern")
                if pattern is not None and not isinstance(pattern, dict):
                    # a string pattern used to CRASH the server
                    # ('str' has no attribute 'get' — live_test_2 B2)
                    errors.append(
                        "registry op %d: `pattern` must be an object, e.g. "
                        "{\"types\": [\"wireframe\", \"flow\"], "
                        "\"concept\": \"checkout\"} — got %r" % (i, pattern))
                    continue
                if pattern:
                    # class-level ruling: ONE record covers every mapping
                    # matching the pattern, now and in the future — the demo
                    # session recorded the identical ruling 8 times because
                    # only per-index annotation existed (audit §7.2)
                    pol = {"types": sorted(pattern.get("types") or []),
                           "concept": pattern.get("concept"),
                           "kinds": self._parse_kinds(op, i, errors),
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
                reg["mappings"][idx]["kinds"] = self._parse_kinds(
                    op, i, errors)
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
                # WP5 (r4-3): the escape hatch for chat-only user turns —
                # and it validated NOTHING, so a typo'd round was a
                # silent no-op on the one mechanism keeping pin ageing
                # alive
                if "round" in op and not isinstance(op.get("round"), int):
                    errors.append(
                        "registry op %d (set_round): round must be an "
                        "integer (got %r)" % (i, op.get("round")))
                elif isinstance(op.get("round"), int):
                    reg["round"] = op["round"]
                if "whose_move" in op and \
                        op.get("whose_move") not in ("user", "agent"):
                    errors.append(
                        "registry op %d (set_round): whose_move must be "
                        "'user' or 'agent' (got %r)"
                        % (i, op.get("whose_move")))
                elif op.get("whose_move") in ("user", "agent"):
                    reg["whose_move"] = op["whose_move"]
            elif action == "rename_artifact":
                # a view's SCOPE legitimately narrows — splitting a domain
                # model leaves one half being something new. Until v0.6
                # `name` was writable only inside `create`, so the rail
                # kept the old title forever and the only workaround was
                # re-creating the artifact, which discards its history
                # (v0.5 assessment R2-5). The id never moves: saves,
                # mappings and pins are all keyed on it.
                aid2 = op.get("artifact")
                new_name = (op.get("name") or "").strip()
                if not aid2 or aid2 not in self.artifact_meta:
                    errors.append("registry op %d: rename_artifact needs "
                                  "an existing artifact (got %r)"
                                  % (i, aid2))
                    continue
                if not new_name:
                    errors.append("registry op %d: rename_artifact needs a "
                                  "non-empty `name`" % i)
                    continue
                old_name = self.artifact_meta[aid2].get("name") or aid2
                self.artifact_meta[aid2]["name"] = new_name
                # the name lives in the artifact FILE, and a registry-only
                # batch never touches scenes — so write it through here or
                # the rail keeps the old title until the next drawing
                self._write_artifact(aid2, self.scenes.get(aid2) or [],
                                     self.artifact_meta[aid2])
                applied.append({"action": "artifact_renamed",
                                "artifact": aid2, "from": old_name,
                                "to": new_name})
            elif action == "set_budget":
                # per-artifact complexity-budget override (v0.3): recorded
                # intent — a raise without a reason is exactly the drift
                # the budget exists to catch
                aid2 = op.get("artifact")
                if not aid2 or aid2 not in self.artifact_meta:
                    errors.append("registry op %d: set_budget needs an "
                                  "existing artifact (got %r)" % (i, aid2))
                    continue
                if op.get("clear"):
                    reg.setdefault("budgets", {}).pop(aid2, None)
                    applied.append({"action": "budget_cleared",
                                    "artifact": aid2})
                    continue
                if not (op.get("nodes") or op.get("arrows")):
                    errors.append("registry op %d: set_budget needs nodes "
                                  "and/or arrows (or clear: true)" % i)
                    continue
                bad = False
                for key in ("nodes", "arrows"):
                    v = op.get(key)
                    if v is not None and (not isinstance(v, int) or v < 1):
                        errors.append(
                            "registry op %d: %s must be a positive "
                            "integer — an arrow-free screen still sets "
                            "arrows: 1, the budget floor" % (i, key))
                        bad = True
                if bad:
                    continue
                if not (op.get("reason") or "").strip():
                    errors.append(
                        "registry op %d: set_budget requires a `reason` — "
                        "a budget override is recorded intent, not a "
                        "silencer" % i)
                    continue
                entry = {"reason": op["reason"].strip()}
                if op.get("nodes"):
                    entry["nodes"] = op["nodes"]
                if op.get("arrows"):
                    entry["arrows"] = op["arrows"]
                reg.setdefault("budgets", {})[aid2] = entry
            elif action == "waive":
                # one-time-question suppression (v0.4): a waived
                # question is an answered question — the reason IS the
                # answer, recorded where the lint will look
                key = (op.get("key") or "").strip()
                if not key or ":" not in key:
                    errors.append(
                        "registry op %d: waive needs a key like "
                        "'q25:<artifact>' (got %r)" % (i, key))
                    continue
                if op.get("clear"):
                    reg.setdefault("waives", {}).pop(key, None)
                    applied.append({"action": "waive_cleared",
                                    "key": key})
                    continue
                if not (op.get("reason") or "").strip():
                    errors.append(
                        "registry op %d: waive requires a `reason` — "
                        "it is the recorded answer, not a silencer" % i)
                    continue
                reg.setdefault("waives", {})[key] = {
                    "reason": op["reason"].strip(),
                    "when": now_iso()[:10]}
            else:
                errors.append("registry op %d: unknown action %r (allowed: "
                              "upsert_concept, remove_view, add_mapping, "
                              "annotate_mapping, remove_mapping, "
                              "resolve_tripwire, annotate_tripwire, "
                              "decline, set_round, set_budget, waive, "
                              "rename_artifact)"
                              % (i, action))
                continue
            applied.append({k: v for k, v in op.items() if k != "op"})
        return applied

    def _save_registry(self):
        """Persist the registry, unless we are measuring a dry run.

        The other writer `_sandbox` has to stop: it swaps `self.registry`
        for a throwaway copy, so an unguarded save here would persist the
        sandbox. No registry action calls this today — the guard is here
        because enumerating what to protect is what shipped r3-12.
        """
        if self._dry_run:
            return
        write_json(self.p.registry_path, self.registry)

    # -- the agent write path --------------------------------------------
    def _validate_batch(self, batch):
        """Check a batch against current state, changing nothing.

        Split out of `apply_batch` so the same work can back a dry run.
        A held revision used to reach the queue unvalidated, so the agent
        was answered `queued: true` for batches that could never apply and
        narrated the drawing as done; the user met the validator error
        minutes later, for a mistake it was too late to repair (v0.4
        capability assessment).

        Args:
            batch: Op-batch envelope — `base_revn`, `artifact` or `create`,
                `ops`, and the optional `note`.

        Returns:
            The work already done while validating, so the caller need not
            repeat it: `artifact`, `new_els`, `new_meta`, `pin_reg`,
            `registry_ops`, `pin_only` and `ops`.

        Raises:
            BatchError: The envelope is malformed, names an unknown or
                duplicate artifact, resolves an unknown pin, or carries ops
                that do not apply to the current scene.
        """
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
                    if "artifact_type" in create and "type" not in create:
                        # WP5: the meta STORES this as artifact_type, so
                        # the wrong spec key defaulted silently to a
                        # flow — even this campaign's own tests made the
                        # mistake and never noticed
                        errors.append(
                            "create: use `type`, not `artifact_type` — "
                            "the artifact would silently default to a "
                            "flow")
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
            try:
                new_els = apply_ops(base_els, ops, errors, pin_reg)
            except Exception as e:  # noqa: BLE001 — E-9 backstop
                # No traceback ever reaches the agent: SKILL.md promises
                # "errors name the offending op", and the r4-11 crash
                # arrived as a raw ValueError instead.
                raise BatchError([*errors,
                    "internal error applying ops — %s: %s (the batch was "
                    "rejected whole; nothing partial landed)"
                    % (type(e).__name__, e)]) from e
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
            return {"artifact": aid, "new_els": new_els, "new_meta": new_meta,
                    "pin_reg": pin_reg, "registry_ops": registry_ops,
                    "pin_only": pin_only, "ops": ops}

    def check_batch(self, batch):
        """Dry-run a batch: would it land, and what would it say?

        The agent cannot see its own drawing, so `apply` answers with an
        intent echo and layout findings and the skill tells it to read
        those rather than the success line. A held batch never got that
        far. This runs the whole read-only half — envelope, ops, registry
        ops, echo and lint — against throwaway state, so a queued revision
        can be answered with the same evidence as an applied one.

        Staleness is deliberately not checked: `commit_pending` rebases a
        held batch onto the head it eventually lands on, so a `base_revn`
        that has since moved is not an error here.

        Args:
            batch: The same op-batch envelope `apply_batch` takes.

        Returns:
            `{"ok": bool, "errors": [str], "artifact": str | None,
            "intent_echo": [str], "layout_errors": [str],
            "layout_warnings": [str], "layout_notes": [str]}`. Never
            raises for a rejected batch — the errors come back in the
            payload so a caller can report them without a try/except.
        """
        with self.lock:
            try:
                checked = self._validate_batch(batch)
            except BatchError as e:
                return {"ok": False, "errors": list(e.errors),
                        "artifact": batch.get("artifact"), "intent_echo": [],
                        "layout_errors": [], "layout_warnings": [],
                        "layout_notes": [], "elements": []}
            aid = checked["artifact"]
            # registry ops are the other half of a batch and they failed
            # this way in the field (`set_budget` with arrows: 0). The
            # dispatch mutates state as it validates, so it runs here
            # inside _sandbox() — which throws away the registry, the
            # artifact meta AND any disk write. Enumerating what to
            # protect is what shipped r3-12: the old guard listed
            # self.registry and rename_artifact wrote past it.
            saved = self.registry
            reg_errors = []
            with self._sandbox():
                # same publish-before-ops as commit, so --check accepts
                # exactly what apply accepts (BUG-03). No un-seeding
                # needed: the sandbox throws the whole cache away.
                self._seed_created_meta(checked["new_meta"])
                try:
                    self._apply_registry_ops(checked["registry_ops"],
                                             reg_errors)
                    reg_after = self.registry
                except BatchError as e:
                    reg_errors, reg_after = list(e.errors), saved
            if reg_errors:
                return {"ok": False, "errors": reg_errors, "artifact": aid,
                        "intent_echo": [], "layout_errors": [],
                        "layout_warnings": [], "layout_notes": [], "elements": []}
            atype = (checked["new_meta"].get(aid) or
                     self.artifact_meta.get(aid) or {}).get("artifact_type")
            lint = project_lint(self.p, checked["new_els"], reg_after,
                                artifact_type=atype, aid=aid)
            return {"ok": True, "errors": [], "artifact": aid,
                    "intent_echo": intent_echo(checked["ops"],
                                               checked["new_els"]),
                    "layout_errors": lint["errors"],
                    "layout_warnings": lint["warnings"],
                    "layout_notes": lint["notes"],
                    # the would-be scene, so `apply --check --render` can
                    # draw a batch nobody has committed (v0.6)
                    "elements": checked["new_els"]}

    def apply_batch(self, batch):
        """Validate-all-then-apply.

        Args:
            batch: Op-batch envelope — see `_validate_batch`.

        Returns:
            `(record, pin_only)` — the save record, and whether the batch
            drew nothing (pin-only revisions never hold behind the banner).
            Propagates `BatchError` from `_validate_batch`/`commit` and
            `StaleError` from `commit`.
        """
        with self.lock:
            checked = self._validate_batch(batch)
            aid = checked["artifact"]
            new_els, new_meta = checked["new_els"], checked["new_meta"]
            pin_reg, registry_ops = checked["pin_reg"], checked["registry_ops"]
            pin_only, ops = checked["pin_only"], checked["ops"]
            base_revn = batch.get("base_revn")

            # explicit points mods are intent, but their diff entries are
            # derived (bound-arrow geometry) — surface them as facts so
            # a hand-reroute never narrates as an empty save
            reroutes = [{"fact": "rerouted", "element": o.get("id"),
                         "arrow": o.get("id")}
                        for o in ops if o.get("op") == "mod"
                        and isinstance(o.get("attrs"), dict)
                        and "points" in o["attrs"]]
            # a rewire re-routes by design — "a rewire is a new path
            # request" — but when the path being replaced is one the USER
            # drew by hand, nothing said so. The session that found this
            # shows one arrow re-dragged four times: to the agent that
            # reads as indecision, when it is the user redoing work the
            # agent keeps undoing (brownfield BUG-06). Measured against
            # the PRE-op scene, because the ops have already re-routed it.
            was = {e["id"]: e for e in (self.scenes.get(aid) or [])}
            reroutes += [
                {"fact": "user_route_replaced", "element": o.get("id"),
                 "arrow": o.get("id")}
                for o in ops if o.get("op") == "mod"
                and isinstance(o.get("attrs"), dict)
                and ({"from", "to"} & set(o["attrs"]))
                and o.get("id") in was
                and not server_owns_geometry(was[o["id"]])]
            resolved_now = {o.get("id") for o in ops
                            if o.get("op") == "resolve_pin"}
            record = self.commit(
                author="agent",
                new_scenes={aid: new_els},
                base_revn=base_revn,
                user_note=batch.get("note"),
                registry_ops=registry_ops,
                new_meta=new_meta,
                extra_facts={aid: reroutes} if reroutes else None,
                resolved_pins=resolved_now)
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
    def _stash_scope(self, name):
        """Record the live branch-scoped sections against a branch.

        Args:
            name: The branch to stash them under.
        """
        for b in self.registry["branches"]:
            if b["name"] == name:
                b["scope"] = {k: copy.deepcopy(self.registry.get(k))
                              for k in BRANCH_SCOPED if k in self.registry}
                return

    def _load_scope(self, b):
        """Make a branch's stashed sections the live ones.

        A branch with no stash keeps what is already live — that is the
        first switch after the 0002 migration, and inheriting is right:
        the alternative is silently emptying a registry.

        Args:
            b: The branch record being switched to.
        """
        for k, v in (b.get("scope") or {}).items():
            if k in BRANCH_SCOPED:
                self.registry[k] = copy.deepcopy(v)

    def switch_branch(self, name):
        with self.lock:
            b = next((b for b in self.registry["branches"]
                      if b["name"] == name), None)
            if b is None:
                raise BatchError(["unknown branch %r (branches: %s)"
                                  % (name, ", ".join(
                                      x["name"] for x in
                                      self.registry["branches"]))])
            # the registry follows the branch, like the scenes below.
            # Without this, switching to a branch that lacks an artifact
            # deleted its file and the load-time healer then PRUNED the
            # pins on it — permanently, for the branch you came from
            # (reproduced: open -> pruned -> still pruned back on main).
            self._stash_scope(self.registry["head"])
            self._load_scope(b)
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
            # Content comparison, deliberately order-insensitive (WP2):
            # z-order is derived machinery the replay cannot reconstruct
            # (normalize_z_order runs at apply; user saves carry client
            # order), so an order-sensitive hash here diagnosed derived
            # noise as an outside edit and minted a fresh phantom
            # reconciliation on EVERY load — a standing generator of
            # "0 changes differ from history" records.
            same = (set(exp_scenes.keys()) == set(disk.keys()) and all(
                content_fingerprint(exp_scenes[a]) ==
                content_fingerprint(disk[a])
                for a in disk))
            if same:
                return None
            # rollback? disk matches some ancestor state exactly
            for r in reversed(self.lineage(head)[:-1]):
                st = self.state_at(r)
                st_scenes = {aid: p["elements"] for aid, p in st.items()}
                if set(st_scenes.keys()) == set(disk.keys()) and all(
                        content_fingerprint(st_scenes[a]) ==
                        content_fingerprint(disk[a])
                        for a in disk):
                    self.rollback = {"matches_revn": r, "head_revn": head}
                    self.log("rollback detected: disk state matches revn %d"
                             % r)
                    return None
            new_meta = {aid: dict(self.artifact_meta.get(aid) or {})
                        for aid in disk}
            # WP2: is this divergence a genuine outside edit, or only the
            # loader's own repair work? The raw disk hashes (captured
            # before validate_scene touched anything) settle it: raw ==
            # replayed history means nobody edited outside the session —
            # every fixture with load repairs used to mint a phantom
            # "out-of-session" record here, arm 4's reading "saved
            # without changing anything" while committing a revision.
            repair_only = bool(self.scene_repairs) and \
                set(self.raw_hashes.keys()) == set(exp_scenes.keys()) and \
                all(self.raw_hashes.get(a) is not None and
                    self.raw_hashes[a] == content_fingerprint(
                        exp_scenes[a])
                    for a in exp_scenes)
            record = self.commit(author="out-of-session",
                                 new_scenes=dict(disk),
                                 new_meta=new_meta,
                                 reconciliation=True)
            self.reconciliation = record["revn"]
            rewrite = False
            if repair_only:
                counts = {}
                for i in self.scene_repairs:
                    counts[i["code"]] = counts.get(i["code"], 0) + 1
                record["summary"]["headline"] = (
                    "load-time repair: %s — no outside edits" % ", ".join(
                        "%s ×%d" % (c, n) for c, n in sorted(counts.items())))
                rewrite = True
            elif "saved without changing anything" in \
                    (record["summary"].get("headline") or ""):
                # a committed reconciliation may never claim nothing
                # happened (arm 4's live phantom did exactly that)
                n_changed = sum(len(p.get("changes") or [])
                                for p in record["artifacts"].values())
                record["summary"]["headline"] = (
                    "out-of-session drift reconciled: %d change(s) differ "
                    "from history" % n_changed)
                rewrite = True
            if self.scene_repairs:
                record["repairs"] = list(self.scene_repairs)
                rewrite = True
            if rewrite:
                for pth in self.p.saves_dir.glob("%04d-*.json"
                                                 % record["revn"]):
                    write_json(pth, record)
            # live_test_2 B4: reconciliation passes the same lint gate as
            # any apply — the revn-17 restart rerouted an arrow into a
            # 45×176 diagonal with zero warnings fired
            lint_all = {}
            for aid in record["artifacts"]:
                li = project_lint(self.p, self.scenes.get(aid, []),
                                  self.registry,
                                  artifact_type=self.artifact_type(aid),
                                  aid=aid)
                if li["errors"] or li["warnings"] or li["notes"]:
                    lint_all[aid] = li
            if lint_all:
                record["lint"] = lint_all
                for pth in self.p.saves_dir.glob("%04d-*.json"
                                                 % record["revn"]):
                    write_json(pth, record)
                for aid, li in lint_all.items():
                    for tier, items in (("ERROR", li["errors"]),
                                        ("WARNING", li["warnings"])):
                        for msg in items:
                            self.log("reconciliation lint %s [%s]: %s"
                                     % (tier, aid, msg))
            self.log("reconciliation record %d written" % record["revn"])
            return record

    def lint_debt(self):
        """Standing cross-artifact lint summary (live_test_2 B5 — apply
        reports only the touched artifact, so drift elsewhere stayed
        invisible). Cached per head revn; the standing-nag principle:
        any invariant that can drift through a side channel must be
        recomputed on every apply, not checked at write time."""
        with self.lock:
            key = self.head_revn()
            cached = getattr(self, "_lint_debt_cache", None)
            if cached and cached[0] == key:
                return cached[1]
            debt, lines = {}, {}
            types = {aid: self.artifact_type(aid) for aid in self.scenes}
            terms = []
            try:
                ctx = self.p.pk / "CONTEXT.md"
                if ctx.exists():
                    terms = parse_glossary_terms(
                        ctx.read_text(encoding="utf-8"))
            except OSError:
                pass
            cross = cross_lint(self.scenes, types, self.registry, terms)
            for aid, els in sorted(self.scenes.items()):
                li = project_lint(self.p, els, self.registry,
                                  artifact_type=types[aid], aid=aid)
                x = cross.get(aid)
                if x:
                    li = {k: li[k] + x[k]
                          for k in ("errors", "warnings", "notes")}
                # WP2: load-time referential findings (computed on the
                # RAW disk scene, before repairs) ride the same debt —
                # the r4-7 unreachability was exactly these never
                # reaching any surface the agent reads
                r = self.referential.get(aid)
                if r:
                    li = {k: li[k] + r[k]
                          for k in ("errors", "warnings", "notes")}
                lines[aid] = li
                counts = {k: len(li[k])
                          for k in ("errors", "warnings", "notes")}
                if any(counts.values()):
                    debt[aid] = counts
            reg = project_lint(self.p, [], self.registry)
            rref = self.referential.get("registry")
            if rref:
                reg = {k: reg[k] + rref[k]
                       for k in ("errors", "warnings", "notes")}
            if any(reg[k] for k in ("errors", "warnings", "notes")):
                debt["registry"] = {k: len(reg[k])
                                    for k in ("errors", "warnings",
                                              "notes")}
                lines["registry"] = reg
            self._lint_debt_cache = (key, debt, lines)
            return debt

    def lint_lines(self):
        """Per-artifact lint LINES (project + cross-artifact merged) —
        what the debt counts count. Cached with lint_debt; the client
        renders these in the rail (v0.4 WP4)."""
        with self.lock:
            self.lint_debt()
            cached = getattr(self, "_lint_debt_cache", None)
            return cached[2] if cached and len(cached) > 2 else {}

    def effective_round(self, queued=False):
        """The round the session is actually in, queue included.

        `round` advances inside `commit`, so under `pulled` cadence —
        where nothing commits until the user applies — it froze, and
        took pin ageing with it: `age_rounds` is `round - pin.round`, so
        a question could sit open across four turns still reading
        "age 0r" while the standing-nag mechanism exists precisely to
        make an ageing question harder to ignore (v0.6 assessment r3-2).

        Derived rather than written, so discarding the queue reverses it
        with no arithmetic and `registry.json` never records a round in
        which nothing committed.

        Args:
            queued: Whether a non-pin batch is waiting behind the banner.

        Returns:
            The committed round, plus one for an uncommitted agent turn.
        """
        return self.registry.get("round", 0) + (1 if queued else 0)

    def effective_whose_move(self, queued=False):
        """Whose turn it is, queue included — see `effective_round`.

        Args:
            queued: Whether a non-pin batch is waiting behind the banner.

        Returns:
            `"user"` while a revision waits on them, else the committed
            value.
        """
        return "user" if queued else self.registry["whose_move"]

    def open_tripwires(self):
        """Standing unresolved tripwires, full question text.

        Lint and pin debt are pushed onto every apply response and this
        was the only nag you had to PULL, through `GET api/state`
        (r3-9). A tripwire persists until resolved, so it is standing by
        construction and belongs in the same block.

        Returns:
            One dict per open tripwire — id, mapping and question.
        """
        with self.lock:
            return [{"id": t.get("id"), "mapping": t.get("mapping"),
                     "question": t.get("question") or ""}
                    for t in self.registry["tripwires"]
                    if t.get("status") == "open"]

    def round_stall(self):
        """The chat-only round freeze, made visible (WP5, r4-3).

        The round advances only on canvas authorship alternation, but the
        user may legitimately move by chat alone — argus4 sat at round 1
        through revn 10 while its agent hand-counted "open two rounds"
        and every pin read age 0r. The tool cannot auto-advance (a chat
        turn is invisible to it); it CAN notice the smell: a long run of
        non-user commits while questions sit open.

        Returns:
            ``{"commits": n, "round": r}`` when >= 4 consecutive commits
            landed without a user save while pins are open, else None.
        """
        with self.lock:
            head = self.head_revn()
            n = 0
            for r in reversed(self.lineage(head)):
                rec = self.records.get(r) or {}
                if rec.get("author") == "user":
                    break
                n += 1
            has_open = any(p.get("status") == "open"
                           for p in self.registry["pins"])
            if n >= 4 and has_open:
                return {"commits": n,
                        "round": self.registry.get("round", 0)}
            return None

    def pin_debt(self, queued=False):
        """Open/answered pins with age in rounds and how often their
        target changed since asking (v0.2 PIN_DEBT — clone of the
        VIEW_DEBT standing-nag mechanism)."""
        with self.lock:
            rnd = self.effective_round(queued)
            return [{"id": p["id"], "artifact": p.get("artifact"),
                     "status": p.get("status"),
                     "direction": p.get("direction", "agent"),
                     "age_rounds": max(0, rnd - (p.get("round") or 0)),
                     "target_edits": p.get("target_edits", 0)}
                    for p in self.registry["pins"]
                    if p.get("status") in ("open", "answered")]

    TIDY_MAX_PASSES = 4

    @staticmethod
    def _tidy_hash(els):
        """Identity of a scene AS IT WILL BE STORED.

        `scene_hash` on raw pass output is not that: `_tidy_pass`
        rebuilds `boundElements`, which is derived bookkeeping, in a
        different order from the one `normalize_scene_doc` writes. So a
        tidy that changed nothing real still hashed differently, the
        no-op guard missed, and every press wrote a revision headlined
        "saved without changing anything" (brownfield BUG-02).

        Args:
            els: An element list.

        Returns:
            The hash of its normalized form.
        """
        return scene_hash(normalize_scene_doc({"elements": els})["elements"])

    def _tidy_pass(self, base):
        """One tidy pass: snap, re-route, re-fan, normalize z-order.

        Args:
            base: The artifact's elements. Not mutated.

        Returns:
            `(elements, snapped, rerouted)` for the tidied copy.
        """
        els = [dict(e) for e in base]
        index = {e["id"]: e for e in els}
        snapped = 0
        for e in els:
            if e.get("type") in ("rectangle", "diamond", "ellipse",
                                 "frame") and \
                    role_of(e) not in ("label", "pin"):
                nx = int(round(e.get("x", 0) / 4.0)) * 4
                ny = int(round(e.get("y", 0) / 4.0)) * 4
                if nx != e.get("x") or ny != e.get("y"):
                    e["x"], e["y"] = nx, ny
                    snapped += 1
                    recenter_label(els, e)
        obstacles = [e for e in els
                     if e.get("type") in ("rectangle", "diamond",
                                          "ellipse")
                     and (e.get("customData") or {}).get("role")
                     not in ("label", "pin", "decoration",
                             "annotation")]
        rerouted = 0
        for e in els:
            if e.get("type") != "arrow":
                continue
            s = index.get((e.get("startBinding") or {})
                          .get("elementId"))
            d = index.get((e.get("endBinding") or {}).get("elementId"))
            if s is not None and d is not None and \
                    server_owns_geometry(e):
                route_arrow(
                    e, s, d, obstacles,
                    soft_obstacles=[t for t in els
                                    if t.get("type") == "text"
                                    and (t.get("containerId") or
                                         role_of(t) == "annotation")],
                    other_arrows=[(t["id"], t.get("x", 0),
                                   t.get("y", 0),
                                   t.get("points") or [])
                                  for t in els
                                  if t.get("type") == "arrow"
                                  and len(t.get("points") or []) >= 2])
                recenter_label(els, e)
                rerouted += 1
        fan_attach_points(els)
        return normalize_z_order(els), snapped, rerouted

    def tidy(self, aid):
        """One-click repair (Phase 6): snap nodes to the 4px grid,
        re-route server-owned arrows, re-fan attach points, normalize
        z-order — committed as an ordinary agent revision the user can
        revert.

        Run to a FIXED POINT, because one pass is not stable. Routing
        reads the other arrows' current paths and the fan then moves
        them, so route-then-fan hands the next pass different input and
        can oscillate: measured on a real project, tidy flip-flopped
        between two states with period 2 and every press wrote a
        revision headlined "saved without changing anything". The user
        pressed it five times, which reads as "nothing is happening"
        (brownfield BUG-02 — whose stated cause, a no-op write, was not
        what was happening: every press changed the drawing).

        Args:
            aid: Artifact id.

        Returns:
            The save record, or a `noop: True` stand-in when there was
            nothing to repair or when the passes would not settle.

        Raises:
            BatchError: If `aid` names no artifact.
        """
        with self.lock:
            base = self.scenes.get(aid)
            if base is None:
                raise BatchError(["tidy: unknown artifact %r" % aid])

            def noop(headline):
                return {"revn": self.head_revn(), "noop": True,
                        "summary": {"headline": headline,
                                    "verb_counts": {}, "suppressed": 0}}

            els, snapped, rerouted = self._tidy_pass(base)
            seen = {self._tidy_hash(base), self._tidy_hash(els)}
            for _ in range(self.TIDY_MAX_PASSES - 1):
                nxt, s2, r2 = self._tidy_pass(els)
                h = self._tidy_hash(nxt)
                if h == self._tidy_hash(els):
                    break               # settled
                if h in seen:
                    # a cycle: every state in it is one the next press
                    # undoes, so committing any of them just moves the
                    # problem. Say what is happening instead.
                    return noop(
                        "tidy could not settle — re-routing these arrows "
                        "keeps undoing the attach-point fan. Author the "
                        "paths with `mod points`, or move the nodes apart")
                seen.add(h)
                els, snapped, rerouted = nxt, snapped + s2, rerouted + r2
            if self._tidy_hash(els) == self._tidy_hash(base):
                # nothing to repair — committing anyway would write an
                # empty "saved without changing anything" revision (v0.3
                # assessment bug)
                return noop("already tidy — nothing to change")
            return self.commit(
                author="agent", new_scenes={aid: els},
                base_revn=self.head_revn(),
                user_note="tidy: snapped %d node(s) to grid, re-routed "
                          "%d arrow(s), normalized z-order"
                          % (snapped, rerouted))

    def label_save(self, revn, label):
        """Bookmark a save (Phase 6): a short human name shown in the
        timeline and graph. Stored on the record; content-addressing
        (short_id) is unaffected."""
        with self.lock:
            rec = self.records.get(revn)
            if rec is None:
                raise BatchError(["save-label: no revn %r" % revn])
            rec["label"] = (label or "").strip()[:60] or None
            for pth in self.p.saves_dir.glob("%04d-*.json" % revn):
                write_json(pth, rec)
            return rec

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
    def public_state(self, queued=False):
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
                    "label": r.get("label"),
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
                    "files": self.artifact_files.get(aid) or {},
                }
            return {
                "protocol_version": PROTOCOL_VERSION,
                "project": self.p.name(),
                "revn": self.registry["revn"],
                "head": self.registry["head"],
                "head_revn": self.head_revn(),
                "branches": self.registry["branches"],
                # derived, not stored: under `pulled` nothing commits, so
                # the committed pair froze until the user applied (r3-2)
                "round": self.effective_round(queued),
                "whose_move": self.effective_whose_move(queued),
                "committed_round": self.registry["round"],
                "concepts": self.registry["concepts"],
                "mappings": self.registry["mappings"],
                "pins": self.registry["pins"],
                "tripwires": self.registry["tripwires"],
                "lint_debt": self.lint_debt(),
                "pin_debt": self.pin_debt(queued),
                "budgets": self.registry.get("budgets") or {},
                "waives": self.registry.get("waives") or {},
                "lint": self.lint_lines(),
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
        self.log_file = log_f      # held so a short-lived app can close it
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
        self.mermaid_requests = {}
        self.mermaid_seq = 0
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
    def drop_pending(self, pending_id):
        """Remove a queued revision from the banner.

        Args:
            pending_id: Queue entry id.

        Returns:
            True when an entry was removed.
        """
        with self.lock:
            before = len(self.pending)
            self.pending = [p for p in self.pending
                            if p["id"] != pending_id]
            return len(self.pending) != before

    def queue_pending(self, batch, pin_only, supersedes=None):
        """Hold a revision behind the banner for the user to pull.

        Args:
            batch: The validated op-batch envelope.
            pin_only: Whether the batch draws nothing.
            supersedes: Queue entry this batch replaces. A corrected retry
                used to stack a second banner beside the broken original,
                leaving the user two entries for one intent with no way to
                tell them apart (v0.4 capability assessment).

        Returns:
            The new queue entry.
        """
        with self.lock:
            if supersedes is not None:
                self.drop_pending(supersedes)
            self.pending_seq += 1
            entry = {"id": self.pending_seq, "batch": batch,
                     "pin_only": pin_only, "deferred": False,
                     "supersedes": supersedes, "queued_at": now_iso()}
            self.pending.append(entry)
            return entry

    def sanitize_pending(self):
        """Project the queue to the shape the web app renders.

        Returns:
            One dict per queued revision — id, pin_only, deferred, note,
            artifact, ops and queued_at.
        """
        with self.lock:
            return [{"id": p["id"], "pin_only": p["pin_only"],
                     "deferred": p["deferred"],
                     "note": p["batch"].get("note"),
                     "artifact": p["batch"].get("artifact") or
                     (p["batch"].get("create") or {}).get("id"),
                     "ops": p["batch"].get("ops") or [],
                     "queued_at": p["queued_at"]} for p in self.pending]

    def commit_pending(self, entry, trigger="the banner"):
        """Apply a held revision, rebased onto the current head.

        A failing entry is evicted rather than left armed. It used to stay
        in the queue — the de-queue ran only after a successful apply — so
        a batch that could never land re-offered the identical error on
        every click and could not be got rid of (v0.4 capability
        assessment).

        Args:
            entry: The queue entry to apply.
            trigger: What pulled it, for the failure event's message.

        Returns:
            The save record.

        Raises:
            BatchError: The batch does not validate against the head it
                landed on. The entry is dropped first.
            StaleError: Propagated from `commit`; the entry is dropped.
        """
        batch = dict(entry["batch"])
        batch["base_revn"] = self.store.head_revn()
        try:
            record, pin_only = self.store.apply_batch(batch)
        except (BatchError, StaleError) as e:
            self.drop_pending(entry["id"])
            self.events.append(
                "agent_revision_failed", pending_id=entry["id"],
                error="Revision pulled from %s could not be applied: %s "
                      "Re-read state and redraw." % (trigger, e))
            raise
        self.drop_pending(entry["id"])
        self.events.append("agent_revision", revn=record["revn"],
                           short_id=record["short_id"], pin_only=pin_only,
                           headline=record["summary"]["headline"],
                           from_pending=entry["id"])
        return record

    def flush_deferred(self):
        """Apply every revision the user parked until after their Save."""
        for entry in [p for p in self.pending if p["deferred"]]:
            try:
                self.commit_pending(entry, trigger="your Save")
            except (BatchError, StaleError):
                pass    # commit_pending evicted it and said so

    # -- request handling --------------------------------------------------
    def queued_turn(self):
        """Whether a drawing revision is waiting behind the banner.

        The agent has moved and the user has not answered, so round and
        whose_move should say so even though nothing has committed —
        which under `pulled` cadence is the whole span (r3-2). Pin-only
        revisions never hold behind the banner, so they never count.

        Returns:
            True when at least one non-pin revision is queued.
        """
        return any(not p["pin_only"] for p in self.pending)

    def api_state(self):
        st = self.store.public_state(queued=self.queued_turn())
        st.update({
            "pending": self.sanitize_pending(),
            "dirty": self.dirty,
            "events_seq": self.events.seq,
            "events_log": str(self.project.events_path),
            "screenshot_requests": [
                {"id": k, "artifact": v["artifact"]}
                for k, v in self.shot_requests.items()
                if v.get("status") == "waiting"],
            "mermaid_requests": [
                {"id": k, "definition": v["definition"]}
                for k, v in self.mermaid_requests.items()
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
                    fork_name=body.get("fork_name"),
                    new_files=body.get("files"))
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
                    "tripwires": record["tripwires"],
                    "tripwires_muted": record.get("tripwires_muted") or [],
                    "round_stall": self.store.round_stall()}
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
                # a held batch used to skip validation entirely and only
                # fail when the USER clicked Apply — so the agent was told
                # it had drawn, and narrated as much (v0.4 assessment)
                check = self.store.check_batch(body)
                if not check["ok"]:
                    return err(422, "\n".join(check["errors"]))
                entry = self.queue_pending(body, pin_only,
                                           supersedes=body.get("supersedes"))
                self.events.append("agent_pending", pending_id=entry["id"],
                                   pin_only=pin_only,
                                   reason="dirty canvas" if self.dirty
                                   else "pulled cadence")
                return {"ok": True, "queued": True,
                        "pending_id": entry["id"],
                        "reason": ("the user has unsaved edits"
                                   if self.dirty else
                                   "cadence is set to pulled"),
                        "intent_echo": check["intent_echo"],
                        "layout_errors": check["layout_errors"],
                        "layout_warnings": check["layout_warnings"],
                        "layout_notes": check["layout_notes"],
                        # the standing nags ride EVERY apply response, as
                        # ops-reference promises, and this one carried
                        # none at all — so the CLI had nothing to print
                        # even once its early return was fixed (r3-6).
                        # Computed against current HEAD on purpose: debt
                        # is about artifacts this batch did not touch.
                        "branch": self.store.registry["head"],
                        "open_tripwires": self.store.open_tripwires(),
                        "lint_debt": self.store.lint_debt(),
                        "pin_debt": self.store.pin_debt(self.queued_turn()),
                        "hint": "The revision will land behind the pending-"
                                "revision banner; the user chooses when. It "
                                "validates and lints clean against the "
                                "current head — read the echo, not this line."}
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
            # lint_lines carries cross-artifact findings too (v0.4) —
            # and reuses the lint_debt cache this response also ships
            lint = (self.store.lint_lines().get(aid) if aid else None) or \
                {"errors": [], "warnings": [], "notes": []}
            return {"ok": True, "revn": record["revn"],
                    "short_id": record["short_id"],
                    "summary": record["summary"],
                    "pin_only": pin_only,
                    "intent_echo": intent_echo(body.get("ops") or [], scene),
                    "layout_errors": lint["errors"],
                    "layout_warnings": lint["warnings"],
                    "layout_notes": lint["notes"],
                    # tripwires fired by THIS batch — visible at fire time,
                    # not rounds later via status (v0.3 assessment bug)
                    "tripwires": record.get("tripwires") or [],
                    "tripwires_muted": record.get("tripwires_muted") or [],
                    "round_stall": self.store.round_stall(),
                    # a user save toasts its branch and an agent revision
                    # named none, so the product already held that a
                    # non-main write should say so and applied it to one
                    # of the two write paths (r3-14)
                    "branch": record.get("branch"),
                    "open_tripwires": self.store.open_tripwires(),
                    "lint_debt": self.store.lint_debt(),
                    "pin_debt": self.store.pin_debt(self.queued_turn())}
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
            if action == "discard":
                # without this a revision the user does not want can only
                # be applied or parked — there was no way to say no
                self.drop_pending(pid)
                self.events.append("agent_revision_discarded",
                                   pending_id=pid,
                                   note=entry["batch"].get("note"))
                return {"ok": True, "discarded": True}
            return err(400, "action must be apply_now, after_save or discard")
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
        if path == "/api/tidy":
            aid = body.get("artifact")
            try:
                record = self.store.tidy(aid)
            except (BatchError, StaleError) as e:
                return err(400, str(e))
            if record.get("noop"):
                # nothing changed — no revision, no event
                return {"ok": True, "revn": record["revn"], "noop": True,
                        "headline": record["summary"]["headline"]}
            self.events.append("agent_revision", revn=record["revn"],
                               short_id=record["short_id"],
                               headline=record["summary"]["headline"],
                               tidy=True)
            return {"ok": True, "revn": record["revn"],
                    "headline": record["summary"]["headline"]}
        if path == "/api/save-label":
            try:
                rec = self.store.label_save(int(body.get("revn") or 0),
                                            body.get("label"))
            except (BatchError, ValueError) as e:
                return err(400, str(e))
            return {"ok": True, "revn": rec["revn"],
                    "label": rec.get("label")}
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
            if not isinstance(patch, dict) or not patch:
                # an unwrapped body used to no-op silently AND log a
                # config_changed event with an empty patch, so the agent
                # could read a cadence change that never happened
                return err(400, "config needs a non-empty `patch` object, "
                                "e.g. {\"patch\": {\"canvas_updates\": "
                                "\"pulled\"}} — settable keys: %s"
                           % ", ".join(sorted(allowed)))
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
        if path == "/api/mermaid/request":
            # WP9 seeding handshake — a clone of the screenshot one: the
            # server cannot run the mermaid converter (stdlib-only), so a
            # connected tab (or a self-launched headless one) does, and
            # posts the element SKELETONS back for the CLI to map to ops
            definition = (body.get("definition") or "").strip()
            if not definition:
                return err(400, "definition required — the mermaid text")
            with self.lock:
                self.mermaid_seq += 1
                mid = self.mermaid_seq
                self.mermaid_requests[mid] = {
                    "definition": definition, "status": "waiting",
                    "elements": None, "error": None}
            return {"ok": True, "id": mid}
        if path == "/api/mermaid/complete":
            mid = body.get("id")
            req = self.mermaid_requests.get(mid)
            if req is None:
                return err(404, "no mermaid request %r" % mid)
            if body.get("error"):
                req["status"] = "error"
                req["error"] = str(body["error"])
                return {"ok": True}
            els = body.get("elements")
            if not isinstance(els, list):
                return err(400, "elements must be a skeleton list")
            req["status"] = "done"
            req["elements"] = els
            return {"ok": True}
        if path == "/api/mermaid/poll":
            req = self.mermaid_requests.get(body.get("id"))
            if req is None:
                return err(404, "no mermaid request %r" % body.get("id"))
            return {"ok": True, "status": req["status"],
                    "elements": req["elements"], "error": req["error"]}
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
                if path == "/api/docs":
                    # attachable-document listing for the inspector
                    # (v0.3): every markdown under project_knowledge,
                    # minus machinery directories
                    base = app.project.pk.resolve()
                    docs = []
                    for f in sorted(base.rglob("*.md")):
                        rel = f.relative_to(base).as_posix()
                        if rel.startswith(("saves/", "artifacts/")):
                            continue
                        docs.append(rel)
                    return self._send_json({"ok": True, "docs": docs})
                if path.startswith("/api/doc/"):
                    # report reader: markdown from project_knowledge only,
                    # path-sandboxed (never serve outside the project)
                    rel = urllib.parse.unquote(path[len("/api/doc/"):])
                    base = app.project.pk.resolve()
                    target = (base / rel).resolve()
                    if base not in target.parents and target != base:
                        return self._send_json(
                            {"ok": False, "error": "path escapes "
                             "project_knowledge"}, 403)
                    if not target.is_file() or target.suffix.lower() not in \
                            (".md", ".txt"):
                        return self._send_json(
                            {"ok": False,
                             "error": "no such document %r" % rel}, 404)
                    return self._send_json({
                        "ok": True, "path": rel,
                        "content": target.read_text(encoding="utf-8")})
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
                        # tier parity (WP4/r4-12): the headless render
                        # used to resolve `sans-serif` to whatever the
                        # system had while the live tab wrapped in real
                        # Nunito — two wrap engines, one file, both
                        # "VALID". The fonts are already served locally.
                        face = _nunito_face_css(app.web_root)
                        body = ("<!doctype html><html><head><meta "
                                "charset='utf-8'><style>" + face +
                                "body{margin:0;"
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
             lint_debt="; ".join(
                 "%s %s" % (aid, "/".join(
                     "%d%s" % (v, k[0].upper())
                     for k, v in c.items() if v))
                 for aid, c in sorted(
                     (st.get("lint_debt") or {}).items())) or "none",
             pin_debt="; ".join(
                 "%s(%s, age %dr, target edited %d×)"
                 % (p["id"], p["status"], p["age_rounds"],
                    p["target_edits"])
                 for p in st.get("pin_debt") or []) or "none",
             checkout_revn=st.get("checkout_revn"),
             rollback=st.get("rollback"),
             events_seq=st.get("events_seq"),
             events_log=st.get("events_log"))
    # WP2 (report-and-repair): load-time repairs used to reach exactly
    # one surface — /api/state, which nothing in the loop reads. Silent
    # repair is how the r4-7 lint became unreachable.
    for i in st.get("issues") or []:
        if i.get("repaired"):
            print("REPAIR=%s: %s" % (i.get("code"), i.get("msg")))
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


def cmd_export(args):
    """Write an artifact to SVG, optionally carrying its own detail.

    The handover case: the drawings outlive the session and get read by
    someone who was not in it. Hover-only tooltips do not survive that,
    so `--with-footnotes` prints them under the diagram, numbered against
    markers on the elements they belong to, with the glossary appended.

    Args:
        args: Parsed CLI args — `project`, `artifact`, `out`,
            `with_footnotes`, `no_glossary`.

    Returns:
        Process exit code: 0 on success.
    """
    project = Project(args.project)
    store = Store(project)
    aid = args.artifact
    if aid is None:
        if len(store.scenes) != 1:
            die("ERROR=--artifact required (known: %s)"
                % (", ".join(sorted(store.scenes)) or "none"), 2)
        aid = next(iter(store.scenes))
    if aid not in store.scenes:
        die("ERROR=unknown artifact %r (known: %s)"
            % (aid, ", ".join(sorted(store.scenes)) or "none"), 2)
    gloss = []
    if args.with_footnotes and not args.no_glossary:
        ctx = project.pk / "CONTEXT.md"
        try:
            if ctx.exists():
                gloss = parse_glossary_pairs(ctx.read_text(encoding="utf-8"))
        except OSError:
            gloss = []
    name = (store.artifact_meta.get(aid) or {}).get("name") or aid
    svg, w, h = render_svg(store.scenes[aid], title=name,
                           footnotes=bool(args.with_footnotes),
                           glossary=gloss)
    out = Path(args.out) if args.out else (project.pk / ("%s.svg" % aid))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg, encoding="utf-8")
    except OSError as e:
        die("ERROR=could not write %s: %s" % (out, e), 2)
    print_kv(artifact=aid, path=str(out), width=w, height=h,
             footnotes=len(collect_footnotes(store.scenes[aid]))
             if args.with_footnotes else 0,
             glossary_terms=len(gloss))
    return 0


def cmd_lint(args):
    """Print the standing lint findings, bodies and all.

    `status` reports only counts ("argus-dashboard 9N"), so the only way
    to read what a note actually says was to apply a batch and watch the
    response — which is a poor reason to draw something (v0.4 capability
    assessment).

    Args:
        args: Parsed CLI args — `project`, optional `artifact`.

    Returns:
        Process exit code: 0 always (findings are not failures).
    """
    project = Project(args.project)
    store = Store(project)
    lines = store.lint_lines()
    aids = [args.artifact] if args.artifact else sorted(lines)
    if args.artifact and args.artifact not in lines:
        die("ERROR=unknown artifact %r (known: %s)"
            % (args.artifact, ", ".join(sorted(lines)) or "none"), 2)
    total = 0
    for aid in aids:
        li = lines.get(aid) or {}
        for tier, prefix in (("errors", "LAYOUT_ERROR"),
                             ("warnings", "LAYOUT_WARNING"),
                             ("notes", "LAYOUT_NOTE")):
            for msg in li.get(tier) or []:
                total += 1
                print("%s=%s: %s" % (prefix, aid, msg))
    # WP2: what the loader fixed on the way in, named — a repair the
    # agent never hears about is a defect the next session re-inherits
    for i in store.issues:
        if i.get("repaired"):
            print("REPAIR=%s: %s" % (i.get("code"), i.get("msg")))
    print_kv(artifacts=len(aids), findings=total)
    return 0


def cmd_pending(args):
    """List the revisions waiting behind the user's banner.

    Args:
        args: Parsed CLI args — `project`, optional `discard` id.

    Returns:
        Process exit code: 0, or 3 when no server is running.
    """
    project = Project(args.project)
    state = project.read_state()
    if not server_alive(state):
        die("ERROR=server unreachable — the pending queue lives in the "
            "running server; run canvas.py start", 3)
    if args.discard is not None:
        try:
            http_json(state["url"] + "api/pending/resolve",
                      payload={"id": args.discard, "action": "discard"})
        except urllib.error.HTTPError as e:
            die("ERROR=could not discard %r (%s)" % (args.discard, e), 2)
        print_kv(discarded=args.discard)
        return 0
    try:
        st = http_json(state["url"] + "api/state", timeout=10.0)
    except (OSError, ValueError, urllib.error.URLError) as e:
        die("ERROR=server unreachable (%s)" % e, 3)
    entries = st.get("pending") or []
    for p in entries:
        print_kv(pending_id=p.get("id"), artifact=p.get("artifact"),
                 ops=len(p.get("ops") or []),
                 deferred=str(bool(p.get("deferred"))).lower(),
                 queued_at=p.get("queued_at"), note=p.get("note"))
    print_kv(pending=len(entries))
    return 0


# Event types by originator (WP5/r4-6). The live log was 87.5% the
# agent's own `agent_revision` echoes — undocumented — and an agent that
# does not filter narrates its own drawing back as the user's move.
USER_EVENT_TYPES = frozenset({
    "save", "pin_answer", "tripwire_answer", "checkout", "checkout_live",
    "branch_switch", "branch_archive", "suggest_view", "config_changed"})
AGENT_EVENT_TYPES = frozenset({
    "agent_revision", "agent_pending", "agent_revision_discarded",
    "agent_revision_failed"})


def cmd_wait(args):
    """Tier-3 bounded long-poll. Self-terminates strictly under Bash's 600s
    ceiling. Exit 0 = events printed; exit 3 = timed out with none.

    ``--for user`` (the default an agent wants) skips the agent's own
    echoes; ``--types a,b`` pins exact types. Unfiltered, the first
    event a fresh watch reported in run 4 was the agent's own revision.
    """
    project = Project(args.project)
    state = project.read_state()
    if not server_alive(state):
        die("ERROR=no running server — run canvas.py start first. Grilling "
            "can continue verbally in the meantime.", 3)
    wanted = None
    if getattr(args, "types", None):
        wanted = {t.strip() for t in args.types.split(",") if t.strip()}
    elif getattr(args, "for_whom", "any") == "user":
        wanted = set(USER_EVENT_TYPES) | {"reconciliation"}
    elif getattr(args, "for_whom", "any") == "agent":
        wanted = set(AGENT_EVENT_TYPES)
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
            since = max(since, ev["seq"])
            if wanted is not None and ev.get("type") not in wanted:
                continue
            print(json.dumps(ev, ensure_ascii=False))
            got = True
        if got:
            return 0
    print("TIMEOUT=no user activity in %ds — waiting is working time: do "
          "queued doc work, or ask via chat/AskUserQuestion. Never treat "
          "silence as consent." % args.timeout)
    return 3


def _print_layout(resp):
    """Print the echo and layout findings of an apply/check response.

    Args:
        resp: Any payload carrying `intent_echo` and `layout_*` keys.
    """
    for line in resp.get("intent_echo") or []:
        print("ECHO=%s" % line)
    for e in resp.get("layout_errors") or []:
        print("LAYOUT_ERROR=%s" % e)
    for w in resp.get("layout_warnings") or []:
        print("LAYOUT_WARNING=%s" % w)
    for n in resp.get("layout_notes") or []:
        print("LAYOUT_NOTE=%s" % n)


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
    if getattr(args, "check", False):
        # dry run: says whether the batch WOULD land, and what it would
        # say, without committing. Reads the files directly so it works
        # with or without a server.
        result = Store(project).check_batch(batch)
        if not result["ok"]:
            for line in result["errors"]:
                print("ERROR=%s" % line)
            print_kv(would_apply="false")
            return 5
        print_kv(would_apply="true", artifact=result.get("artifact"))
        _print_layout(result)
        if getattr(args, "render", False):
            # under `pulled` cadence a queued revision is invisible to
            # the agent that wrote it, and legibility is the one class of
            # defect it cannot reason about from the response. Draw the
            # uncommitted scene so it can look before it queues (v0.6).
            aid = result.get("artifact") or "batch"
            svg, w, h = render_svg(result.get("elements") or [],
                                   title="%s (proposed)" % aid)
            outdir = project.runtime_dir
            outdir.mkdir(parents=True, exist_ok=True)
            out_png = outdir / ("%s-check.png" % aid)
            ok, why = rasterize_svg(svg, out_png, w, h, aid + "-check")
            if ok:
                print_kv(png=str(out_png), detail=why)
            else:
                out_svg = out_png.with_suffix(".svg")
                out_svg.write_text(svg, encoding="utf-8")
                print_kv(svg=str(out_svg))
                print("NOTE=%s — SVG only" % why)
        return 0
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
            # a queued revision still owes its echo — swallowing it left
            # the agent with a success line and nothing to check it
            # against (v0.4 capability assessment) — and it owes the
            # standing nags too, which this branch skipped by returning
            # early for two more versions after that (r3-6)
            print_kv(queued="true", pending_id=resp.get("pending_id"),
                     reason=resp.get("reason"), hint=resp.get("hint"))
            _print_layout(resp)
            _print_standing(resp)
            return 0
        print_kv(revn=resp.get("revn"), short_id=resp.get("short_id"),
                 pin_only=str(resp.get("pin_only", False)).lower(),
                 headline=(resp.get("summary") or {}).get("headline"))
        _print_layout(resp)
        _print_standing(resp)
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
    # lint_lines, not project_lint: the offline path used to miss every
    # cross-artifact finding the server path reports
    lint = (store.lint_lines().get(aid) if aid else None) or \
        {"errors": [], "warnings": [], "notes": []}
    _print_layout({"intent_echo": intent_echo(batch.get("ops") or [], scene),
                   "layout_errors": lint["errors"],
                   "layout_warnings": lint["warnings"],
                   "layout_notes": lint["notes"]})
    _print_standing({"branch": record.get("branch"),
                     "tripwires": record.get("tripwires"),
                     "tripwires_muted": record.get("tripwires_muted"),
                     "round_stall": store.round_stall(),
                     "open_tripwires": store.open_tripwires(),
                     "lint_debt": store.lint_debt(),
                     "pin_debt": store.pin_debt()})
    return 0


STANDING_TRIPWIRE_CAP = 5


def _print_standing(resp):
    """Print everything that must ride EVERY apply response.

    Split out and called from all three of `cmd_apply`'s exits because
    the queued branch used to `return 0` before the nag block — the
    third defect on those same eight lines in three assessments (v0.4
    patched one; v0.6 assessment r3-6 found the rest). A shared epilogue
    behind a single call is the point: an early return can no longer
    take a subset of the contract with it.

    What rides, and why each is here:

    - `BRANCH` when it is not main. A user save toasts its branch and an
      agent revision named none, so the product already held the
      position and applied it to one of the two write paths (r3-14).
    - `TRIPWIRE` — fired by THIS batch, visible at fire time rather than
      rounds later via a status count (v0.3).
    - `OPEN_TRIPWIRE` — standing, unresolved. Tripwire debt was the only
      nag you had to PULL, via `GET api/state`; lint and pin debt are
      pushed. ops-reference.md draws no such distinction, and a tripwire
      persists until resolved, so it is standing by construction (r3-9).
    - `LINT_DEBT` / `PIN_DEBT` — cross-artifact drift in artifacts this
      batch did not touch, and pins ageing (v0.2).

    Args:
        resp: An apply response — server, queued or offline-shaped.
    """
    if resp.get("branch") and resp["branch"] != "main":
        print_kv(branch=resp["branch"])
    for t in resp.get("tripwires") or []:
        print("TRIPWIRE=%s %s" % (t.get("id", "?"), t.get("question", "")))
    rs = resp.get("round_stall")
    if rs:
        print("ROUND_STALL=%d commits since the user's last canvas save, "
              "with questions open — if their moves arrived in chat, "
              "advance the round: {\"op\": \"registry\", \"action\": "
              "\"set_round\", \"round\": %d}"
              % (rs["commits"], rs["round"] + 1))
    for msg in resp.get("tripwires_muted") or []:
        # WP5 (D10): a rename swallowed by a divergence ruling is armed
        # silence — visible, while the ruling still holds
        print("TRIPWIRE_MUTED=%s" % msg)
    standing = resp.get("open_tripwires") or []
    for t in standing[:STANDING_TRIPWIRE_CAP]:
        print("OPEN_TRIPWIRE=%s %s" % (t.get("id", "?"),
                                       t.get("question", "")))
    if len(standing) > STANDING_TRIPWIRE_CAP:
        print("OPEN_TRIPWIRE=+%d more — canvas.py status, or GET api/state"
              % (len(standing) - STANDING_TRIPWIRE_CAP))
    lint_debt, pin_debt = resp.get("lint_debt"), resp.get("pin_debt")
    if lint_debt:
        print("LINT_DEBT=" + "; ".join(
            "%s %s" % (aid, "/".join("%d%s" % (v, k[0].upper())
                                     for k, v in c.items() if v))
            for aid, c in sorted(lint_debt.items())))
    if pin_debt:
        print("PIN_DEBT=" + "; ".join(
            "%s(%s, age %dr, target edited %d×)"
            % (p["id"], p["status"], p["age_rounds"], p["target_edits"])
            for p in pin_debt))


def rasterize_svg(svg, out_png, want_w, want_h, tag, url=None):
    """Render an SVG to PNG with a headless system browser.

    `cmd_snapshot`'s tier 2, extracted so a batch that has not been
    committed can be looked at too (v0.6 `apply --check --render`). Under
    `pulled` cadence a queued revision is invisible to its author — the
    agent said so twice in the v0.5 assessment and then hand-rolled a
    copy-the-project workaround, as the v0.4 agent had before it. Nothing
    third-party: it drives whatever chromium/chrome/edge/brave exists.

    Args:
        svg: SVG source to rasterize.
        out_png: Destination path.
        want_w: Intended pixel width (clamped to a sane window).
        want_h: Intended pixel height.
        tag: Short slug used to name the scratch HTML file.
        url: Render straight from this URL instead of the SVG source,
            when a live server can serve it.

    Returns:
        `(ok, detail)` — `detail` names the browser, or why it failed.
    """
    browsers = find_browsers()
    if not browsers:
        return False, "no chromium/chrome/edge/brave found"
    # work in $HOME so snap-confined browsers (private /tmp) can see both
    # the input html and the output png
    workdir = Path.home() / ".cache" / "wysiwyg-grilling"
    workdir.mkdir(parents=True, exist_ok=True)
    work_png = workdir / out_png.name
    if url is None:
        html = ("<!doctype html><html><head><meta charset='utf-8'>"
                "<style>body{margin:0;background:%s}</style>"
                "</head><body>%s</body></html>" % (SVG_GROUND, svg))
        tmp_html = workdir / ("%s-render.html" % tag)
        tmp_html.write_text(html, encoding="utf-8")
        url = tmp_html.resolve().as_uri()
    win_w = max(min(want_w, 3000), 320)
    win_h = max(min(want_h, 2000), 200)
    why = "no browser produced a file"
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
                ok, detail = validate_png(work_png.read_bytes(),
                                          win_w, win_h, min_bpp=0)
                if ok:
                    if work_png != out_png:
                        shutil.copyfile(str(work_png), str(out_png))
                    return True, "%s, %s" % (os.path.basename(browser),
                                             detail)
                why = "%s render invalid (%s)" % (
                    os.path.basename(browser), detail)
            else:
                why = "%s produced no file (rc=%d%s)" % (
                    os.path.basename(browser), proc.returncode,
                    (", " + proc.stderr.decode("utf-8", "replace")
                     .strip()[:120]) if proc.stderr else "")
        except (subprocess.TimeoutExpired, OSError) as e:
            why = "%s failed: %s" % (os.path.basename(browser), e)
    return False, why


# ---------------------------------------------------------------------
# Mermaid seeding (v0.8, WP9). Two mapped types, chosen semantically:
# flowchart → flow (the library's dagre layout is the prize — the
# connected tab, or a headless one we launch, converts the text to
# element skeletons and the mapper turns those into ordinary ops) and
# erDiagram → domain (a pure text parse — attributes and cardinality
# survive as grammar, which no geometry dump could carry, and no browser
# is needed at all). Everything else is refused by name: sequence,
# class and state convert natively in the library, but their output is
# dead geometry to this skill's op grammar. A seed flows through apply
# — lints, budgets, registry, save record, narration — never a raw
# element dump, and it is seed-ONLY: once the batch lands, the drawing
# is the truth and the mermaid text is never re-applied over it.
# ---------------------------------------------------------------------

MERMAID_MAPPED = {"flowchart": "flow", "graph": "flow",
                  "erdiagram": "domain"}


def mermaid_kind(text):
    """First significant mermaid keyword, lowercased.

    Skips ``---`` frontmatter blocks, ``%%`` comments and ``%%{...}``
    directives, then reads the first word of the first real line —
    ``flowchart TD`` → ``flowchart``, ``erDiagram`` → ``erdiagram``.

    Args:
        text: Raw mermaid source.

    Returns:
        The keyword, or "" for empty/comment-only input.
    """
    in_front = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "---":
            in_front = not in_front
            continue
        if in_front or line.startswith("%%"):
            continue
        return line.split()[0].lower()
    return ""


def _sk_label(sk):
    """Bound-label text of a converted skeleton, or "".

    Args:
        sk: One skeleton dict from ``parseMermaidToExcalidraw``.

    Returns:
        The stripped label text ("" when unlabeled).
    """
    lbl = sk.get("label")
    if isinstance(lbl, dict):
        return (lbl.get("text") or "").strip()
    return ""


# erDiagram grammar (the symbol form): `A ||--o{ B : label`, entity
# attribute blocks `NAME { type name PK "comment" }`, quoted and
# `key[Display]`-aliased entity names. The verbose word form ("one to
# many") is deliberately unparsed — an unmappable line errors by text.
_ER_NAME = r'(?:"[^"]+"|[A-Za-z][\w-]*(?:\[[^\]]+\])?)'
_ER_REL = re.compile(
    r'^(?P<a>%s)\s+(?P<lc>\|o|\|\||\}o|\}\|)(?:--|\.\.)'
    r'(?P<rc>o\||\|\||o\{|\|\{)\s+(?P<b>%s)\s*:\s*'
    r'(?P<lbl>"[^"]*"|[^\s{]+)\s*$' % (_ER_NAME, _ER_NAME))
_ER_HEAD = re.compile(r'^(?P<n>%s)\s*\{$' % _ER_NAME)
_ER_BARE = re.compile(r'^(?P<n>%s)$' % _ER_NAME)
_ER_ATTR = re.compile(
    r'^(?P<type>[\w()\[\]<>,.]+)\s+(?P<name>[\w-]+)'
    r'(?P<keys>(?:\s+(?:PK|FK|UK))*)\s*(?:"(?P<comment>[^"]*)")?\s*$')
_ER_CARD_L = {"|o": "0..1", "||": "1", "}o": "0..*", "}|": "1..*"}
_ER_CARD_R = {"o|": "0..1", "||": "1", "o{": "0..*", "|{": "1..*"}


def _er_name(tok):
    """Split an erDiagram entity token into (reference key, display).

    Args:
        tok: The raw token — ``CUSTOMER``, ``"Line Item"`` or
            ``p[Person]`` (alias form: relations reference ``p``).

    Returns:
        ``(key, display)`` strings.
    """
    if tok.startswith('"'):
        inner = tok.strip('"')
        return inner, inner
    m = re.match(r"^([A-Za-z][\w-]*)\[([^\]]+)\]$", tok)
    if m:
        return m.group(1), m.group(2)
    return tok, tok


def _parse_mermaid_er(text):
    """Parse erDiagram text into entities + relations, stdlib-only.

    Args:
        text: Raw mermaid source (first keyword ``erDiagram``).

    Returns:
        ``{"entities": {key: {"display", "attrs"}}, "relations": [...],
        "errors": [...]}`` — attrs are dicts (type/name/keys/comment),
        relations carry a/b keys, lc/rc cardinality tokens and label.
        Any line the grammar can't map lands in errors verbatim; order
        of first mention is preserved (it becomes the layout order).
    """
    entities = {}
    relations = []
    errors = []

    def ensure(tok):
        key, display = _er_name(tok)
        if key not in entities:
            entities[key] = {"display": display, "attrs": []}
        elif display != key:
            entities[key]["display"] = display
        return key

    block = None
    in_front = False
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if line == "---":
            in_front = not in_front
            continue
        if in_front:
            continue
        if block is not None:
            if line == "}":
                block = None
                continue
            m = _ER_ATTR.match(line)
            if m:
                entities[block]["attrs"].append({
                    "type": m.group("type"), "name": m.group("name"),
                    "keys": " ".join(m.group("keys").split()),
                    "comment": m.group("comment") or ""})
            else:
                errors.append("line %d: not an attribute row I can "
                              "map: %r" % (lineno, line))
            continue
        if line.lower() == "erdiagram":
            continue
        m = _ER_REL.match(line)
        if m:
            lbl = m.group("lbl").strip('"').strip()
            relations.append({
                "a": ensure(m.group("a")), "b": ensure(m.group("b")),
                "lc": _ER_CARD_L[m.group("lc")],
                "rc": _ER_CARD_R[m.group("rc")], "label": lbl})
            continue
        m = _ER_HEAD.match(line)
        if m:
            block = ensure(m.group("n"))
            continue
        m = _ER_BARE.match(line)
        if m:
            ensure(m.group("n"))
            continue
        errors.append("line %d: not an erDiagram statement I can map: "
                      "%r (supported: `A ||--o{ B : label` relations, "
                      "attribute blocks, entity declarations)"
                      % (lineno, line))
    return {"entities": entities, "relations": relations,
            "errors": errors}


def _er_seed_ops(parsed):
    """Map a parsed erDiagram to domain-artifact ops.

    Entities land on a declaration-order grid (the drawing is the truth
    — drag to taste); ≤3 attribute rows stay visible per domain.md's
    budget, the full typed list moves to the entity's tooltip.
    Cardinality follows the reference: arrowhead on the many end only,
    the many-side token joins the label ("places 0..*"), reflexive
    relations keep the verb alone with cardinality in the tooltip.

    Args:
        parsed: Output of `_parse_mermaid_er` (errors already empty).

    Returns:
        A list of add ops (entities first, then relations).
    """
    existing = set()
    ops = []
    slug = {}
    display_of = {}
    ents = parsed["entities"]
    cols = max(1, int(math.ceil(math.sqrt(len(ents)))))
    for i, (key, ent) in enumerate(ents.items()):
        eid = mint_id(ent["display"], "entity", existing)
        slug[key] = eid
        display_of[eid] = ent["display"]
        spec = {"id": eid, "type": "rectangle", "role": "node",
                "kind": "entity", "label": ent["display"],
                "x": 80 + (i % cols) * 340, "y": 80 + (i // cols) * 260,
                "width": 220, "height": 64}
        attrs = ent["attrs"]
        if attrs:
            rows = [a["name"] + (" (%s)" % a["keys"] if a["keys"]
                                 else "") for a in attrs]
            spec["attributes"] = rows[:3]
            spec["tooltip"] = "attributes:\n" + "\n".join(
                "- %s %s%s%s" % (
                    a["type"], a["name"],
                    " " + a["keys"] if a["keys"] else "",
                    ' — "%s"' % a["comment"] if a["comment"] else "")
                for a in attrs)
        ops.append({"op": "add", "element": spec})
    for rel in parsed["relations"]:
        a, b = slug[rel["a"]], slug[rel["b"]]
        label = rel["label"]
        if a != b and rel["rc"] != "1":
            label = ("%s %s" % (label, rel["rc"])).strip()
        spec = {"id": mint_id("r %s %s" % (a, rel["label"] or b),
                              "arrow", existing),
                "type": "arrow",
                "startArrowhead": "arrow" if "*" in rel["lc"] else None,
                "endArrowhead": "arrow" if "*" in rel["rc"] else None,
                "tooltip": "cardinality: %s %s — %s %s"
                           % (display_of[a], rel["lc"], rel["rc"],
                              display_of[b])}
        if label:
            spec["label"] = label
        ops.append({"op": "add", "element": spec, "from": a, "to": b})
    return ops


def _flow_seed_ops(skeletons):
    """Map converted flowchart skeletons to flow-artifact ops.

    The dagre positions are the point of the exercise — node geometry
    is kept (shifted so the diagram starts at (80, 80)); edges become
    bound arrows and re-route through this skill's own router, whose
    obstacle passes and self-loop path supersede the source polylines.
    Ids are minted as semantic slugs from labels, never the mermaid
    one-letter ids. One level of subgraphs maps to frames.

    Args:
        skeletons: Skeleton list from ``parseMermaidToExcalidraw``.

    Returns:
        ``(ops, notes, errors)`` — errors non-empty means don't seed.
    """
    ops, notes, errors = [], [], []
    existing = set()
    slug, frame_slug = {}, {}
    frames, nodes, arrows = [], [], []
    dropped = 0
    for sk in skeletons or []:
        t = sk.get("type")
        if t == "image":
            # the library's failure mode is not an exception but a
            # SILENT downgrade of the whole diagram to one picture
            errors.append(
                "the converter degraded this diagram to a picture (its "
                "flowchart parser threw) — simplify the mermaid text "
                "and retry; known trigger: subgraph blocks (library "
                "2.2.2)")
            return ops, notes, errors
        if t == "arrow":
            arrows.append(sk)
        elif t in ("rectangle", "diamond", "ellipse"):
            if not sk.get("id"):
                dropped += 1     # e.g. a doublecircle's inner ring
                continue
            gids = sk.get("groupIds") or []
            if gids and gids[0] == "subgraph_group_%s" % sk["id"]:
                if len(gids) > 1:
                    errors.append(
                        "subgraph %r is nested — v0.8 maps one level "
                        "of subgraphs to frames; flatten the diagram "
                        "or drop the outer grouping"
                        % (_sk_label(sk) or sk["id"]))
                    continue
                frames.append(sk)
            else:
                nodes.append(sk)
        else:
            dropped += 1
    keep = frames + nodes
    if not keep and not errors:
        errors.append("the diagram converted to no mappable nodes")
    if errors:
        return ops, notes, errors
    dx = 80 - min(s.get("x", 0) for s in keep)
    dy = 80 - min(s.get("y", 0) for s in keep)

    def snap(v):
        # dagre emits sub-pixel float positions; the skill's layout
        # doctrine (and its lint) wants the 4px grid
        return int(round(v / 4.0) * 4)

    for sk in frames:
        fid = mint_id(_sk_label(sk) or sk["id"], "frame", existing)
        frame_slug[sk["id"]] = fid
        ops.append({"op": "add", "element": {
            "id": fid, "type": "frame",
            "label": _sk_label(sk) or sk["id"],
            "x": snap(sk.get("x", 0) + dx),
            "y": snap(sk.get("y", 0) + dy),
            "width": snap(sk.get("width") or 200),
            "height": snap(sk.get("height") or 120)}})
    for sk in nodes:
        label = _sk_label(sk)
        nid = mint_id(label or sk["id"], "node", existing)
        slug[sk["id"]] = nid
        spec = {"id": nid, "type": sk["type"], "role": "node",
                "x": snap(sk.get("x", 0) + dx),
                "y": snap(sk.get("y", 0) + dy),
                "width": snap(sk.get("width") or 160),
                "height": snap(sk.get("height") or 60)}
        if label:
            spec["label"] = label
        if sk.get("roundness"):     # round/stadium vertices stay rounded
            spec["roundness"] = sk["roundness"]
        gids = sk.get("groupIds") or []
        if gids and gids[0].startswith("subgraph_group_"):
            parent = gids[0][len("subgraph_group_"):]
            if parent in frame_slug:
                spec["frameId"] = frame_slug[parent]
        ops.append({"op": "add", "element": spec})
    for sk in arrows:
        src = slug.get((sk.get("start") or {}).get("id"))
        dst = slug.get((sk.get("end") or {}).get("id"))
        if not src or not dst:
            dropped += 1
            continue
        spec = {"id": mint_id("%s %s" % (src, dst), "arrow", existing),
                "type": "arrow"}
        label = _sk_label(sk)
        if label:
            spec["label"] = label
        if sk.get("strokeStyle") == "dashed":
            spec["strokeStyle"] = "dashed"
        ops.append({"op": "add", "element": spec,
                    "from": src, "to": dst})
    if dropped:
        notes.append("%d source elements had no mapping and were "
                     "dropped" % dropped)
    return ops, notes, errors


def _flow_to_mermaid(els):
    """Render an existing flow artifact as mermaid text for re-layout.

    Node ids are the element ids prefixed with ``n_`` (a bare slug like
    ``end`` is a mermaid keyword), so the returned skeletons carry the
    element identity and no label matching is ever needed.

    Args:
        els: The artifact's element list.

    Returns:
        ``(text, node_count)``.
    """
    ix = {e["id"]: e for e in els}
    labels = {e["containerId"]: e.get("text", "") for e in els
              if e.get("type") == "text" and e.get("containerId")}

    def ref(eid):
        e = ix[eid]
        lbl = (labels.get(eid) or eid).replace('"', "'").replace("\n", " ")
        shape = {"diamond": '{"%s"}', "ellipse": '(["%s"])'}.get(
            e.get("type"), '["%s"]')
        return "n_%s%s" % (eid, shape % lbl)

    nodes = [e for e in els
             if e.get("type") in ("rectangle", "diamond", "ellipse")
             and (e.get("customData") or {}).get("role") == "node"]
    node_ids = {e["id"] for e in nodes}
    lines = ["flowchart TD"]
    declared = set()

    def side(eid):
        out = ref(eid) if eid not in declared else "n_%s" % eid
        declared.add(eid)
        return out

    for a in els:
        if a.get("type") != "arrow":
            continue
        s = (a.get("startBinding") or {}).get("elementId")
        d = (a.get("endBinding") or {}).get("elementId")
        if s not in node_ids or d not in node_ids:
            continue
        lbl = (labels.get(a["id"]) or "").replace("|", "/").strip()
        left = side(s)
        lines.append("  %s -->%s %s"
                     % (left, ("|%s|" % lbl) if lbl else "", side(d)))
    for e in nodes:
        if e["id"] not in declared:
            lines.append("  %s" % ref(e["id"]))
    return "\n".join(lines) + "\n", len(nodes)


def _mermaid_poll(state, mid, timeout):
    """Poll the server until a mermaid conversion settles or times out.

    Args:
        state: Live runtime-state dict (carries the server URL).
        mid: Request id from ``/api/mermaid/request``.
        timeout: Seconds to wait.

    Returns:
        ``{"status", "elements", "error"}`` — status stays "waiting"
        on timeout or an unreachable server.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            st = http_json(state["url"] + "api/mermaid/poll",
                           payload={"id": mid}, timeout=5.0)
        except (OSError, ValueError, urllib.error.URLError):
            break
        if st.get("status") in ("done", "error"):
            return {"status": st["status"],
                    "elements": st.get("elements"),
                    "error": st.get("error")}
    return {"status": "waiting", "elements": None, "error": None}


def _mermaid_convert(project, definition, tab_timeout, allow_headless):
    """Convert mermaid text to skeletons via the web client.

    Tier 1 is a connected tab (the same handshake as screenshots);
    tier 2 launches a headless chromium-family tab of our own on the
    app URL — its service effect performs the conversion during page
    life and posts the result back. A conversion ERROR from either
    tier (bad mermaid syntax) aborts rather than falling through: the
    next tier would fail identically.

    Args:
        project: The Project.
        definition: Mermaid source text.
        tab_timeout: Tier-1 wait in seconds.
        allow_headless: Whether tier 2 may launch a browser.

    Returns:
        ``(skeletons, None)`` on success, ``(None, why)`` otherwise.
    """
    state = project.read_state()
    if not server_alive(state):
        return None, ("server not running — flowchart conversion runs "
                      "in the web client; canvas.py start first, or "
                      "pass --from-skeletons")
    try:
        resp = http_json(state["url"] + "api/mermaid/request",
                         payload={"definition": definition}, timeout=5.0)
    except (OSError, ValueError, urllib.error.URLError) as e:
        return None, "server unreachable (%s)" % e
    mid = resp.get("id")
    got = _mermaid_poll(state, mid, tab_timeout)
    if got["status"] == "error":
        return None, "conversion failed: %s" % got["error"]
    if got["status"] == "done":
        return got["elements"], None
    if not allow_headless:
        return None, ("no connected tab serviced the conversion in %ds "
                      "— open the canvas, or drop --no-headless"
                      % tab_timeout)
    browsers = find_browsers()
    if not browsers:
        return None, ("no connected tab answered and no chromium-"
                      "family browser is installed for a headless one")
    workdir = Path.home() / ".cache" / "wysiwyg-grilling"
    workdir.mkdir(parents=True, exist_ok=True)
    why = "headless tab did not answer"
    for browser in browsers:
        cmd = [browser, "--headless=new", "--disable-gpu",
               "--no-sandbox", "--disable-dev-shm-usage",
               "--hide-scrollbars",
               "--screenshot=%s" % (workdir / "mermaid-headless.png"),
               "--virtual-time-budget=20000",
               "--window-size=1000,700", state["url"]]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as e:
            why = "%s failed: %s" % (os.path.basename(browser), e)
            continue
        got = _mermaid_poll(state, mid, 25)
        if proc.poll() is None:
            proc.kill()
        if got["status"] == "done":
            return got["elements"], None
        if got["status"] == "error":
            return None, "conversion failed: %s" % got["error"]
        why = "%s tab did not answer" % os.path.basename(browser)
    return None, why


def _cmd_mermaid_relayout(args, project, store):
    """Re-lay an existing flow with dagre, as an ordinary revision.

    The artifact is rendered to mermaid text (`_flow_to_mermaid` — the
    generated ids carry element identity, so nothing is matched by
    label), converted in the browser, and the dagre positions come back
    as plain ``mod x/y`` ops through apply: bound 2-point arrows
    re-route, the revision queues behind the banner under `pulled`
    cadence (the user's consent gate), and reverting the save restores
    the old placement exactly. Prints the checkpoint revn it would
    revert to.

    Args:
        args: Parsed CLI args (artifact, tab_timeout, no_headless,
            check/render passthrough).
        project: The Project.
        store: A loaded Store.

    Returns:
        Process exit code (delegates to `cmd_apply`).
    """
    aid = args.artifact
    if aid not in store.scenes:
        die("ERROR=unknown artifact %r (known: %s)"
            % (aid, ", ".join(sorted(store.scenes)) or "none"), 2)
    if store.artifact_type(aid) != "flow":
        die("ERROR=--relayout drives dagre over FLOW artifacts only "
            "(%r is %s)" % (aid, store.artifact_type(aid)), 2)
    els = store.scenes[aid]
    text, node_count = _flow_to_mermaid(els)
    if node_count < 3:
        die("ERROR=%d node(s) — nothing to re-lay" % node_count, 2)
    skeletons, why = _mermaid_convert(
        project, text, args.tab_timeout,
        allow_headless=not args.no_headless)
    if skeletons is None:
        die("ERROR=%s" % why, 3)
    ix = {e["id"]: e for e in els}
    # keep the drawing roughly where it lives: pin dagre's min corner to
    # the current layout's min corner
    cur_x = min(e["x"] for e in els
                if e.get("type") in ("rectangle", "diamond", "ellipse"))
    cur_y = min(e["y"] for e in els
                if e.get("type") in ("rectangle", "diamond", "ellipse"))
    hits = [(sk, sk["id"][2:]) for sk in skeletons
            if sk.get("type") in ("rectangle", "diamond", "ellipse")
            and str(sk.get("id") or "").startswith("n_")
            and str(sk.get("id"))[2:] in ix]
    if not hits:
        die("ERROR=the conversion returned nothing matchable — file "
            "this with the artifact id", 3)
    min_sx = min(sk.get("x", 0) for sk, _ in hits)
    min_sy = min(sk.get("y", 0) for sk, _ in hits)
    ops = []
    for sk, eid in hits:
        nx = int(round((sk.get("x", 0) - min_sx + cur_x) / 4.0) * 4)
        ny = int(round((sk.get("y", 0) - min_sy + cur_y) / 4.0) * 4)
        if abs(nx - ix[eid]["x"]) < 2 and abs(ny - ix[eid]["y"]) < 2:
            continue
        ops.append({"op": "mod", "id": eid, "attrs": {"x": nx, "y": ny}})
    if not ops:
        print_kv(relayout="noop",
                 note="dagre agrees with the current placement")
        return 0
    moved_user = [o["id"] for o in ops
                  if (ix[o["id"]].get("customData") or {})
                  .get("author") == "user"]
    if moved_user:
        print("NOTE=this would move %d user-placed node(s): %s — their "
              "placement is theirs; under pulled cadence the banner asks "
              "first, otherwise narrate it"
              % (len(moved_user), ", ".join(moved_user[:5])))
    print_kv(relayout=aid, moves=len(ops),
             checkpoint="revert save #%d to restore the current "
                        "placement" % store.head_revn())
    batch = {"base_revn": store.head_revn(), "artifact": aid, "ops": ops,
             "note": "re-layout via mermaid/dagre (%d nodes moved) — "
                     "revert this save to restore the old placement"
                     % len(ops)}
    outdir = project.runtime_dir
    outdir.mkdir(parents=True, exist_ok=True)
    bpath = outdir / ("mermaid-relayout-%s.json" % aid)
    bpath.write_text(json.dumps(batch, indent=1), encoding="utf-8")
    sub = argparse.Namespace(project=args.project, file=str(bpath),
                             check=getattr(args, "check", False),
                             render=getattr(args, "render", False))
    return cmd_apply(sub)


def cmd_mermaid(args):
    """Seed a NEW artifact from mermaid text, through apply.

    flowchart/graph → a flow artifact (browser-converted, dagre
    layout kept); erDiagram → a domain artifact (pure text parse);
    anything else refuses by name. Prints SEED_KIND/NODES/ARROWS/BATCH
    KEY=VALUE lines, then apply's own echo — or ERROR= lines and a
    non-zero exit.
    """
    project = Project(args.project)
    if getattr(args, "relayout", False):
        # no input text: the artifact itself is the source
        return _cmd_mermaid_relayout(args, project, Store(project))
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            die("ERROR=could not read %s: %s" % (args.file, e), 2)
    else:
        text = sys.stdin.read()
    kind = mermaid_kind(text)
    if kind not in MERMAID_MAPPED:
        die("ERROR=mermaid seeding maps flowchart→flow and erDiagram→"
            "domain (v0.8); %s isn't mappable — its converted output "
            "would be dead geometry carrying none of this skill's "
            "grammar (bindings, kinds, semantic facts). Draw it with "
            "ops instead (references/ops-reference.md)."
            % (repr(kind) if kind else "empty input"), 2)
    target_type = MERMAID_MAPPED[kind]
    if not args.concept:
        die("ERROR=--concept is required when seeding (which concept "
            "does this artifact answer for?)", 2)
    store = Store(project)
    aid = args.artifact
    if aid in store.scenes:
        die("ERROR=artifact %r already exists — mermaid seeds NEW "
            "artifacts only; once a seed lands, the drawing is the "
            "truth and mermaid text is never re-applied over it" % aid,
            2)
    notes = []
    if target_type == "domain":
        parsed = _parse_mermaid_er(text)
        for line in parsed["errors"]:
            print("ERROR=%s" % line)
        if parsed["errors"]:
            return 2
        if not parsed["entities"]:
            die("ERROR=no entities found in the erDiagram", 2)
        ops = _er_seed_ops(parsed)
    else:
        if re.search(r"^\s*subgraph\b", text, re.MULTILINE):
            # verified live, v0.8: the vendored converter (2.2.2) throws
            # on subgraph blocks and silently degrades the WHOLE diagram
            # to a picture — the playground's own example set carries no
            # subgraph case. The mapper's frame support stays for the day
            # upstream fixes it.
            die("ERROR=this flowchart carries subgraph blocks and the "
                "vendored converter (mermaid-to-excalidraw 2.2.2) fails "
                "on them, degrading the whole diagram to a picture. "
                "Seed it without the subgraphs, then add the lanes as "
                "frames with ops (references/flow.md, Lanes).", 2)
        if args.from_skeletons:
            try:
                skeletons = read_json(args.from_skeletons)
            except (OSError, ValueError) as e:
                die("ERROR=could not read skeletons %s: %s"
                    % (args.from_skeletons, e), 2)
        else:
            skeletons, why = _mermaid_convert(
                project, text, args.tab_timeout,
                allow_headless=not args.no_headless)
            if skeletons is None:
                die("ERROR=%s" % why, 3)
        if args.capture:
            Path(args.capture).write_text(
                json.dumps(skeletons, indent=1), encoding="utf-8")
            notes.append("captured %d raw skeletons to %s"
                         % (len(skeletons), args.capture))
        ops, map_notes, errors = _flow_seed_ops(skeletons)
        notes.extend(map_notes)
        for line in errors:
            print("ERROR=%s" % line)
        if errors:
            return 2
    n_nodes = sum(1 for o in ops
                  if (o.get("element") or {}).get("type")
                  in ("rectangle", "diamond", "ellipse"))
    n_arrows = sum(1 for o in ops
                   if (o.get("element") or {}).get("type") == "arrow")
    node_budget = 8 if target_type == "domain" else 9
    if n_nodes > node_budget or n_arrows > 12:
        ops.append({"op": "registry", "action": "set_budget",
                    "artifact": aid, "nodes": max(n_nodes, node_budget),
                    "arrows": max(n_arrows, 12),
                    "reason": "mermaid seed: the source diagram "
                              "carries %d nodes / %d arrows"
                              % (n_nodes, n_arrows)})
    batch = {"base_revn": store.head_revn(),
             "create": {"id": aid, "type": target_type,
                        "concept": args.concept,
                        "name": args.name or aid.replace("-", " ")
                        .replace("_", " ").title()},
             "ops": ops,
             "note": "seeded from mermaid (%s: %d nodes, %d arrows)"
                     % (kind, n_nodes, n_arrows)}
    for line in notes:
        print("NOTE=%s" % line)
    outdir = project.runtime_dir
    outdir.mkdir(parents=True, exist_ok=True)
    bpath = outdir / ("mermaid-seed-%s.json" % aid)
    bpath.write_text(json.dumps(batch, indent=1), encoding="utf-8")
    print_kv(seed_kind=kind, artifact=aid, nodes=n_nodes,
             arrows=n_arrows, batch=str(bpath))
    sub = argparse.Namespace(project=args.project, file=str(bpath),
                             check=getattr(args, "check", False),
                             render=getattr(args, "render", False))
    return cmd_apply(sub)


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
    if not args.no_headless:
        ok, why = rasterize_svg(
            svg, out_png, want_w, want_h, aid,
            url=(state["url"] + "render/" + aid) if alive else None)
        if ok:
            print_kv(tier="2", png=str(out_png), valid="true", detail=why)
            return 0
        print("NOTE=%s — SVG fallback" % why)

    # ---- tier 3: the SVG itself, honestly labeled ---------------------
    out_svg = out_png.with_suffix(".svg")
    out_svg.write_text(svg, encoding="utf-8")
    print_kv(tier="3", svg=str(out_svg), valid="true",
             note="approximate rendering (system fonts, no sketch style) — "
                  "geometry-faithful, good for legibility only")
    return 0


# ---------------------------------------------------------------------
# Assessor namespace (`x-*`). Deliberately absent from `--help`, from
# SKILL.md and from every reference file.
#
# The capability assessment plays a human against the agent for a whole
# session, and its findings only mean anything if the agent behaves as it
# would in a real one — the best signals of the v0.5 round were an agent
# noticing a silently-flipped cadence and superseding a queued batch
# rather than stacking one. An agent that can see the harness is an agent
# performing for it. These commands exist so the assessor stops
# hand-rolling the save protocol (and silently drifting from what the
# real client posts), not to give the agent new powers: `x-pending
# --apply` in particular must never be public, because applying a held
# revision is the USER's decision and a self-applying agent voids the
# whole `pulled` cadence contract.
# ---------------------------------------------------------------------

def _x_client(args):
    """Resolve the running server for an assessor command.

    Args:
        args: Parsed CLI args carrying `project`.

    Returns:
        `(project, state)` for a live server; exits 3 otherwise.
    """
    project = Project(args.project)
    state = project.read_state()
    if not server_alive(state):
        die("ERROR=server unreachable — run canvas.py start", 3)
    return project, state


def cmd_x_pending(args):
    """Resolve a held revision the way the banner's buttons do.

    `pending --discard` shipped in v0.5; Apply-now and After-I-save did
    not, so the assessment drove them by hunting the button through a
    real browser — the slowest and flakiest step in the loop, and one
    where a failed click and a failed apply look identical.

    Args:
        args: Parsed CLI args — `project`, `apply`, `defer`.

    Returns:
        Process exit code.
    """
    _, state = _x_client(args)
    pid = args.apply if args.apply is not None else args.defer
    action = "apply_now" if args.apply is not None else "after_save"
    if pid is None:
        die("ERROR=pass --apply ID or --defer ID", 2)
    try:
        resp = http_json(state["url"] + "api/pending/resolve",
                         payload={"id": pid, "action": action})
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except ValueError:
            payload = {"error": str(e)}
        die("ERROR=%s" % payload.get("error", str(e)), 5)
    print_kv(resolved=pid, action=action, revn=resp.get("revn"))
    return 0


def cmd_x_geometry(args):
    """Print each element's box, and where bound labels are really drawn.

    Falls out of `arrow_label_anchor`. The stored position of a label
    bound to an arrow is not where the client paints it, and measuring
    the wrong one is how a label sitting inside a foreign box linted
    clean for a whole session — then how the assessor's own hand-rolled
    remeasurement produced two confident false overlaps. Derive it from
    the same helper the renderer and the lints use, or don't derive it.

    Args:
        args: Parsed CLI args — `project`, `artifact`, `diff`.

    Returns:
        Process exit code.
    """
    project = Project(args.project)
    store = Store(project)
    aid = args.artifact or (next(iter(store.scenes))
                            if len(store.scenes) == 1 else None)
    if aid not in store.scenes:
        die("ERROR=--artifact required (known: %s)"
            % (", ".join(sorted(store.scenes)) or "none"), 2)
    els = store.scenes[aid]
    ix = {e["id"]: e for e in els}
    for e in els:
        cont = ix.get(e.get("containerId"))
        drawn = None
        wrap_note = None
        if e.get("type") == "text" and cont is not None and \
                cont.get("type") in ("arrow", "line"):
            drawn = arrow_label_anchor(cont, e)
        if e.get("type") == "text" and e.get("autoResize") is False:
            # WP4 (r4-12): composed value texts were silently out of this
            # command's scope — x-geometry printed NOTHING about the '62'
            # tile whose stored width the live editor wrapped. Compare
            # stored width against the measured need.
            need, _ = text_dims(e.get("text") or "", e.get("fontSize", 16))
            if e.get("width", 0) + 0.5 < need:
                wrap_note = ("stored width %d < needs %d — the editor "
                             "WRAPS this" % (e.get("width", 0), need))
        if args.diff and drawn is None and wrap_note is None:
            continue
        row = "%-24s %-10s (%d,%d) %dx%d" % (
            e["id"][:24], e.get("type", "?"), e.get("x", 0), e.get("y", 0),
            e.get("width", 0), e.get("height", 0))
        if drawn is not None:
            dx = ((e["x"] - drawn[0]) ** 2 + (e["y"] - drawn[1]) ** 2) ** 0.5
            row += "  drawn=(%d,%d) drift=%dpx" % (drawn[0], drawn[1], dx)
        if wrap_note:
            row += "  " + wrap_note
        print(row)
    return 0


def _x_user_note(text, x, y, w=230, h=90):
    """A user's sticky note, shaped exactly as the client posts one.

    Args:
        text: Note body.
        x: Left edge.
        y: Top edge.
        w: Width.
        h: Height.

    Returns:
        `[rectangle, bound text]`, both stamped `author: "user"`.
    """
    nid = "usernote-" + hashlib.sha1(
        text.encode("utf-8")).hexdigest()[:8]
    rect = dict(BASE_DEFAULTS)
    rect.update({
        "id": nid, "type": "rectangle", "x": x, "y": y, "width": w,
        "height": h, "strokeColor": "#b8860b",
        "backgroundColor": "#fff8dc", "fillStyle": "solid",
        "boundElements": [{"id": nid + "-t", "type": "text"}],
        "customData": {"role": "note", "author": "user"}})
    lbl = dict(BASE_DEFAULTS)
    lbl.update({
        "id": nid + "-t", "type": "text", "x": x + 8, "y": y + 8,
        "width": w - 16, "height": h - 16, "text": text,
        "originalText": text, "fontSize": 14, "fontFamily": FONT_LEGIBLE,
        "textAlign": "left", "verticalAlign": "top", "lineHeight": 1.25,
        "containerId": nid, "autoResize": False,
        "customData": {"role": "note-text", "author": "user"}})
    return [rect, lbl]


def _x_user_pin(target, question, x, y):
    """A user-authored `❓ ask` pin, as the rail's button posts it.

    Args:
        target: Element id the question is about.
        question: The question text.
        x: Left edge of the glyph.
        y: Top edge of the glyph.

    Returns:
        A single text element carrying the pin's `customData`.
    """
    el = dict(BASE_DEFAULTS)
    el.update({
        "id": "pin-user-" + hashlib.sha1(
            question.encode("utf-8")).hexdigest()[:8],
        "type": "text", "x": x, "y": y, "width": 26, "height": 26,
        "text": "❓", "originalText": "❓", "fontSize": 20,
        "fontFamily": FONT_LEGIBLE, "textAlign": "center",
        "strokeColor": "#b45309", "autoResize": True,
        "customData": {"role": "pin", "author": "user", "target": target,
                       "question": question, "status": "open",
                       "answer": None}})
    return el


def cmd_x_as_user(args):
    """Edit the canvas as the user, through the client's own save path.

    The assessment has rebuilt this by hand every run: fetch the scene,
    mutate the element list, re-post it with `base_revn`. That is fine
    until the real client starts stamping a field the hand-rolled version
    does not, at which point the "user edits" driving every behavioural
    finding quietly stop being user edits and nothing says so. It is also
    the missing test-fixture builder — the backend suite covers agent op
    batches heavily and user-authored edits barely at all, yet every
    interesting v0.6 result (a nudge that must not fire a tripwire, a
    tooltip edit that must not either) is a user edit.

    Args:
        args: Parsed CLI args — `project`, `verb`, and the verb's operands.

    Returns:
        Process exit code.
    """
    _, state = _x_client(args)
    url = state["url"]
    verb = args.verb
    if verb == "answer":
        http_json(url + "api/pins/answer",
                  payload={"id": args.target, "answer": args.text})
        print_kv(answered=args.target)
        return 0
    if verb == "config":
        key, _, val = (args.text or "").partition("=")
        if not key or not val:
            die("ERROR=config wants key=value", 2)
        http_json(url + "api/config", payload={"patch": {key: val}})
        print_kv(config=key, value=val)
        return 0
    if verb == "checkout":
        http_json(url + "api/checkout", payload={"revn": int(args.target)})
        print_kv(checked_out=args.target)
        return 0
    aid = args.artifact
    if not aid:
        die("ERROR=--artifact required", 2)
    try:
        els = http_json(url + "api/artifact/" + aid,
                        timeout=10.0)["elements"]
    except (OSError, ValueError, urllib.error.URLError) as e:
        die("ERROR=could not read artifact %r (%s)" % (aid, e), 2)
    ix = {e["id"]: e for e in els}
    if verb == "rename":
        # Fidelity (WP6/D28): the real client re-measures on rename.
        # This driver assigned text and left the OLD width/height, so it
        # produced state the product itself never writes — the exact
        # drift assessor-adapter capability 1 exists to prevent, in its
        # own reference implementation.
        lbl = next((t for t in els if t.get("type") == "text"
                    and t.get("containerId") == args.target), None)
        host = ix.get(args.target)
        if lbl is None and host is not None and \
                host.get("type") == "text":
            lbl, host = host, None
        if lbl is None:
            die("ERROR=no label on %r" % args.target, 2)
        lbl["text"] = lbl["originalText"] = args.text
        if lbl.get("autoResize", True):
            lbl["width"], lbl["height"] = text_dims(
                args.text, lbl.get("fontSize", 16))
        if host is not None and host.get("type") in (
                "rectangle", "diamond", "ellipse", "frame"):
            fit_label_in(host, lbl)
            recenter_label(els, host)
    elif verb == "move":
        # The real client drags whole GROUPS: composed decoration parts
        # travel with their host. This driver moved only the host and
        # its bound label — which manufactured half of r4-10 (the
        # "orphaned X strokes" observations came through here, not
        # through a real user gesture).
        el = ix.get(args.target)
        if el is None:
            die("ERROR=no element %r" % args.target, 2)
        gset = set(el.get("groupIds") or [])
        for other in els:
            grouped = gset and (set(other.get("groupIds") or []) & gset)
            if other is el or other.get("containerId") == el["id"] or \
                    other.get("frameId") == el["id"] or grouped:
                other["x"] = other.get("x", 0) + args.dx
                other["y"] = other.get("y", 0) + args.dy
    elif verb == "delete":
        # Parity with the real client: deleting a host takes its whole
        # group (Excalidraw selects groups); a part alone still works by
        # naming the part id directly.
        drop = set(args.target.split(","))
        group_drop = set()
        for tid in drop:
            t = ix.get(tid)
            for g in (t.get("groupIds") or []) if t else []:
                if (t.get("customData") or {}).get("kind"):
                    group_drop.add(g)
        els = [e for e in els if e["id"] not in drop
               and e.get("containerId") not in drop
               and not (group_drop and
                        set(e.get("groupIds") or []) & group_drop)]
    elif verb == "toggle":
        # The uncheck gesture the benchmark scripts and no run could
        # perform (D26): flip customData.checked ONLY — commit-time
        # reconciliation composes the glyph, which makes this verb a
        # standing proof of the WP3 invariant.
        el = ix.get(args.target)
        if el is None:
            die("ERROR=no element %r" % args.target, 2)
        cd = dict(el.get("customData") or {})
        if cd.get("kind") not in ("checkbox", "toggle"):
            die("ERROR=%r is not a checkbox/toggle" % args.target, 2)
        cd["checked"] = not cd.get("checked")
        el["customData"] = cd
    elif verb == "tooltip":
        el = ix.get(args.target)
        if el is None:
            die("ERROR=no element %r" % args.target, 2)
        cd = dict(el.get("customData") or {})
        cd["tooltip"] = args.text
        el["customData"] = cd
    elif verb == "note":
        els = els + _x_user_note(args.text, args.dx or 600, args.dy or 700)
    elif verb == "ask":
        el = ix.get(args.target)
        if el is None:
            die("ERROR=no element %r" % args.target, 2)
        els = [*els, _x_user_pin(args.target, args.text,
                                 el.get("x", 0) + el.get("width", 0) + 8,
                                 el.get("y", 0) - 8)]
    else:
        die("ERROR=unknown verb %r" % verb, 2)
    body = {"scenes": {aid: els},
            "base_revn": http_json(url + "api/state")["head_revn"]}
    if args.note:
        body["note"] = args.note
    try:
        r = http_json(url + "api/save", payload=body, timeout=30.0)
    except urllib.error.HTTPError as e:
        die("ERROR=%s" % e, 5)
    print_kv(revn=r.get("revn"), headline=(r.get("summary") or {})
             .get("headline"), tripwires=len(r.get("tripwires") or []))
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
    # metavar, not the default brace-list: argparse.SUPPRESS keeps a
    # subcommand out of the help LISTING but still prints it in the usage
    # line, which would advertise the assessor namespace it exists to hide
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")

    p = sub.add_parser("start", help="launch or reuse the detached server")
    p.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("status", help="server health + project state")
    p = sub.add_parser("stop", help="shut the server down")
    p = sub.add_parser("wait", help="long-poll for save/answer events")
    p.add_argument("--since", type=int, default=None,
                   help="event seq to wait after (default: now)")
    p.add_argument("--timeout", type=int, default=540,
                   help="max seconds to wait (hard-capped at 540)")
    p.add_argument("--for", dest="for_whom", default="user",
                   choices=("user", "agent", "any"),
                   help="whose events wake you (default user — your own "
                        "agent_revision echoes are skipped)")
    p.add_argument("--types", default=None,
                   help="comma-separated exact event types (overrides "
                        "--for)")
    p = sub.add_parser("export", help="write an artifact to SVG, optionally "
                                      "carrying its tooltips as footnotes")
    p.add_argument("--artifact", default=None)
    p.add_argument("--out", default=None,
                   help="output .svg path (default: project_knowledge/)")
    p.add_argument("--with-footnotes", action="store_true",
                   help="number tooltip-bearing elements and print their "
                        "text under the drawing, plus the glossary — for "
                        "handing the artifact to someone who wasn't here")
    p.add_argument("--no-glossary", action="store_true",
                   help="footnotes without the glossary appendix")
    p = sub.add_parser("lint", help="print standing lint findings in full "
                                    "(status shows only the counts)")
    p.add_argument("--artifact", default=None,
                   help="limit to one artifact id")
    p = sub.add_parser("pending", help="list revisions held behind the "
                                       "user's banner")
    p.add_argument("--discard", type=int, default=None,
                   help="drop a queued revision by id")
    # assessor namespace. Omitting `help=` is what hides a subcommand:
    # argparse only lists parsers that were given one, and passing
    # argparse.SUPPRESS prints the literal "==SUPPRESS==" rather than
    # hiding anything. See the block above cmd_x_pending for why it
    # matters that these stay invisible.
    p = sub.add_parser("x-pending")
    p.add_argument("--apply", type=int, default=None)
    p.add_argument("--defer", type=int, default=None)
    p = sub.add_parser("x-geometry")
    p.add_argument("--artifact")
    p.add_argument("--diff", action="store_true")
    p = sub.add_parser("x-as-user")
    p.add_argument("verb", choices=["rename", "move", "delete", "toggle",
                                    "note", "ask", "tooltip", "answer",
                                    "config", "checkout"])
    p.add_argument("--artifact")
    p.add_argument("--target", default="")
    p.add_argument("--text", default="")
    p.add_argument("--dx", type=int, default=0)
    p.add_argument("--dy", type=int, default=0)
    p.add_argument("--note", default=None)

    p = sub.add_parser("apply", help="apply a typed op batch (agent draws)")
    p.add_argument("--file", help="JSON batch file (default: stdin)")
    p.add_argument("--check", action="store_true",
                   help="dry run: would it apply, and what would it say? "
                        "Prints ECHO/LAYOUT lines, commits nothing, exits 5 "
                        "if the batch would be rejected")
    p.add_argument("--render", action="store_true",
                   help="with --check: also draw the proposed scene to a "
                        "PNG and print its path — the only way to LOOK at "
                        "a revision before it is committed or queued")
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
    p = sub.add_parser("mermaid", help="seed a NEW flow/domain artifact "
                       "from mermaid text (flowchart → flow, erDiagram "
                       "→ domain; other types refused by name)")
    p.add_argument("--file", help="mermaid text file (default: stdin)")
    p.add_argument("--artifact", required=True,
                   help="id for the artifact the seed creates (or, with "
                        "--relayout, the existing flow to re-lay)")
    p.add_argument("--concept", default=None,
                   help="concept the artifact belongs to (seeding only)")
    p.add_argument("--name", default=None, help="display name")
    p.add_argument("--from-skeletons", default=None,
                   help="pre-converted skeleton JSON (offline/CI path, "
                        "flowcharts only — skips the browser)")
    p.add_argument("--capture", default=None,
                   help="also write the raw converted skeletons here "
                        "(fixture material)")
    p.add_argument("--tab-timeout", type=int, default=8,
                   help="seconds to wait for a connected tab before "
                        "launching a headless one")
    p.add_argument("--no-headless", action="store_true",
                   help="never launch a headless tab; require a "
                        "connected one")
    p.add_argument("--check", action="store_true",
                   help="dry run: map + validate through apply --check, "
                        "commit nothing")
    p.add_argument("--render", action="store_true",
                   help="with --check: draw the proposed seed to a PNG")
    p.add_argument("--relayout", action="store_true",
                   help="re-lay an EXISTING flow with dagre instead of "
                        "seeding: mod x/y ops through apply, revertable, "
                        "queued behind the banner under pulled cadence")
    p = sub.add_parser("serve", help="(internal) run server in foreground")
    p.add_argument("--port", type=int, default=0)

    args = parser.parse_args(argv)
    handlers = {
        "start": cmd_start, "status": cmd_status, "stop": cmd_stop,
        "wait": cmd_wait, "apply": cmd_apply, "lint": cmd_lint,
        "export": cmd_export,
        "pending": cmd_pending, "screenshot": cmd_snapshot,
        "snapshot": cmd_snapshot, "mermaid": cmd_mermaid,
        "serve": cmd_serve,
        # assessor namespace, undocumented on purpose
        "x-pending": cmd_x_pending, "x-geometry": cmd_x_geometry,
        "x-as-user": cmd_x_as_user,
    }
    if args.cmd not in handlers:
        parser.print_help()
        return 2
    return handlers[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
