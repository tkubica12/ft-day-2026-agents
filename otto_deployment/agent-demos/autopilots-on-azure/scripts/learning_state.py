"""Pure logic for the Otto learned-state viewer: read-only collector, redaction, snapshots and timelines.

Nothing here talks to Azure. The collector snippet is the only code that runs inside the Hermes sandbox, and it
reads an explicit allowlist under ``/data/hermes`` (memories, skills, skill metadata, learning receipts). It never
opens ``.env``, ``config.yaml``, ``state.db`` or any other credential or transcript store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import zlib
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA = "otto-learning-snapshot/1"
TIMELINE_SCHEMA = "otto-learning-timeline/1"
ENTRY_DELIMITER = "\n§\n"
# Hermes defaults. The deployment's config.yaml is deliberately never read, so these are labelled as defaults.
MEMORY_LIMITS = {"memory": 2200, "user": 1375}
NAMESPACES = ("private", "role", "candidates")
ORIGINS = {"agent": "foreground", "curator": "background", "user": "owner"}
USAGE_FIELDS = (
    "use_count", "view_count", "patch_count", "state", "pinned", "created_by",
    "created_at", "last_used_at", "last_viewed_at", "last_patched_at",
)
MAX_RECEIPT_CHARACTERS = 4000
VIEWER_DIR = Path(__file__).resolve().parents[3] / "memory-viewer"
VIEWER_SHELL = "otto-memory-viewer.html"
INLINE_MARKER = re.compile(r"<!--\s*@inline\s+(css|js|data)(?:\s+([\w.-]+))?\s*-->")

COLLECT_SCRIPT = r"""
import base64, hashlib, json, os, pathlib, time, zlib

HOME = pathlib.Path("/data/hermes")
ROOT = HOME.resolve()
MAX_TEXT = 96 * 1024
MAX_FILES = 12
MAX_OVERSIZE = 2 * 1024 * 1024
LEDGER_TAIL = 2 * 1024 * 1024
MAX_LEDGER = 500
MAX_RECEIPTS = 40
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}


def regular(path):
    try:
        return not path.is_symlink() and path.is_file() and ROOT in path.resolve().parents
    except OSError:
        return False


def load(path, limit=MAX_TEXT):
    if not regular(path):
        return None
    info = path.stat()
    item = {"bytes": info.st_size, "mtime": int(info.st_mtime)}
    if info.st_size > MAX_OVERSIZE:
        item["oversize"] = True
        return item
    raw = path.read_bytes()
    item["sha256"] = hashlib.sha256(raw).hexdigest()
    if b"\0" in raw[:4096]:
        item["binary"] = True
        return item
    item["truncated"] = len(raw) > limit
    item["text"] = raw[:limit].decode("utf-8", "replace")
    return item


def readable(item):
    return item is not None and "text" in item


def load_json(path, limit=256 * 1024):
    item = load(path, limit)
    if not readable(item) or item["truncated"]:
        return None
    try:
        return {"bytes": item["bytes"], "mtime": item["mtime"], "sha256": item["sha256"], "value": json.loads(item["text"])}
    except ValueError:
        return None


out = {"collectedAt": int(time.time())}

memory = []
memories = HOME / "memories"
if memories.is_dir() and not memories.is_symlink():
    for path in sorted(memories.glob("*.md"))[:6]:
        item = load(path)
        if readable(item):
            item["file"] = path.name
            memory.append(item)
out["memory"] = memory

skills_root = HOME / "skills"
skills = []
if skills_root.is_dir() and not skills_root.is_symlink():
    for directory, dirs, files in os.walk(skills_root):
        here = pathlib.Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and not (here / d).is_symlink())
        if "SKILL.md" not in files or len(skills) >= 200:
            continue
        dirs[:] = []
        main = load(here / "SKILL.md")
        if not readable(main):
            continue
        extra = []
        for sub, subdirs, subfiles in os.walk(here):
            sub_path = pathlib.Path(sub)
            subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS and not (sub_path / d).is_symlink())
            if len(sub_path.relative_to(here).parts) >= 3:
                subdirs[:] = []
            extra.extend(sub_path / name for name in sorted(subfiles) if sub_path / name != here / "SKILL.md")
        files_out = []
        for path in extra[:MAX_FILES]:
            item = load(path, 64 * 1024)
            if item is None:
                continue
            item["path"] = path.relative_to(here).as_posix()
            files_out.append(item)
        main["path"] = here.relative_to(skills_root).as_posix()
        main["files"] = files_out
        main["fileCount"] = len(extra)
        skills.append(main)
out["skills"] = skills

out["usage"] = load_json(skills_root / ".usage.json")
out["promptIndex"] = load_json(HOME / ".skills_prompt_snapshot.json", 128 * 1024)
manifest = load(skills_root / ".bundled_manifest", 16 * 1024)
out["bundledManifest"] = manifest["text"] if readable(manifest) else ""

ledger = {"total": 0, "truncated": False, "entries": []}
ledger_path = skills_root / ".curator_ledger.jsonl"
if regular(ledger_path):
    size = ledger_path.stat().st_size
    with open(ledger_path, "rb") as handle:
        if size > LEDGER_TAIL:
            handle.seek(size - LEDGER_TAIL)
            handle.readline()
            ledger["truncated"] = True
        lines = handle.read().decode("utf-8", "replace").splitlines()
    ledger["total"] = len(lines)
    for line in lines[-MAX_LEDGER:]:
        try:
            ledger["entries"].append(json.loads(line))
        except ValueError:
            continue
out["ledger"] = ledger

receipts = []
receipt_dir = HOME / "learning" / "receipts"
if receipt_dir.is_dir() and not receipt_dir.is_symlink():
    for path in sorted(receipt_dir.glob("*.json"), key=lambda item: item.stat().st_mtime)[-MAX_RECEIPTS:]:
        item = load_json(path, 64 * 1024)
        if item:
            item["name"] = path.name
            receipts.append(item)
out["receipts"] = receipts

identity = load(HOME / "SOUL.md", 1)
out["identity"] = {key: identity[key] for key in ("bytes", "mtime", "sha256") if key in identity} if identity else None

payload = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
print(base64.b64encode(zlib.compress(payload.encode("utf-8"), 9)).decode("ascii"))
"""

REDACTIONS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
REDACTED_PAIR = re.compile(
    r"(?i)\b(AccountKey|SharedAccessSignature|client[_-]?secret|api[_-]?key|password|passwd|secret|token)"
    r"(\s*[:=]\s*[\"']?)([A-Za-z0-9+/_=.~-]{20,})"
)
REDACTED_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{20,}")
PLACEHOLDER = "[hidden]"
FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
ENTRY_SPLIT = re.compile(r"(?:\A|\n)§(?:\n|\Z)")


def redact(text: str) -> tuple[str, int]:
    """Hide credential-shaped values before anything reaches a projector or a snapshot file."""
    count = 0
    for pattern in REDACTIONS:
        text, hits = pattern.subn(PLACEHOLDER, text)
        count += hits
    text, hits = REDACTED_PAIR.subn(lambda match: f"{match.group(1)}{match.group(2)}{PLACEHOLDER}", text)
    count += hits
    text, hits = REDACTED_BEARER.subn(lambda match: f"{match.group(1)}{PLACEHOLDER}", text)
    return text, count + hits


def decode_collected(stdout: str) -> dict[str, Any]:
    return json.loads(zlib.decompress(base64.b64decode(stdout.strip())).decode("utf-8"))


def digest(value: Any, length: int = 16) -> str:
    payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def split_entries(text: str) -> list[str]:
    return [part.strip() for part in ENTRY_SPLIT.split(text.replace("\r\n", "\n")) if part.strip()]


def _scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(item) for item in value[1:-1].split(",") if item.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block(lines: list[str], start: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        current = _indent(line)
        if current < indent:
            break
        if current > indent:
            index += 1
            continue
        key, separator, rest = line.strip().partition(":")
        index += 1
        if not separator:
            continue
        rest = rest.strip()
        if rest in {">", "|", ">-", "|-"}:
            folded: list[str] = []
            while index < len(lines) and (not lines[index].strip() or _indent(lines[index]) > current):
                folded.append(lines[index].strip())
                index += 1
            result[key] = (" " if rest.startswith(">") else "\n").join(part for part in folded if part)
        elif rest:
            result[key] = _scalar(rest)
        else:
            following = index
            while following < len(lines) and not lines[following].strip():
                following += 1
            if following >= len(lines):
                result[key] = ""
            elif lines[following].lstrip().startswith("- ") and _indent(lines[following]) >= current:
                items = []
                while following < len(lines) and (not lines[following].strip() or lines[following].lstrip().startswith("- ")):
                    if lines[following].strip():
                        items.append(_scalar(lines[following].strip()[2:]))
                    following += 1
                result[key], index = items, following
            elif _indent(lines[following]) > current:
                result[key], index = _block(lines, following, _indent(lines[following]))
            else:
                result[key] = ""
    return result, index


def parse_front_matter(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by SKILL.md front matter; never raises."""
    match = FRONT_MATTER.match(text)
    if not match:
        return {}
    try:
        return _block(match.group(1).splitlines(), 0, 0)[0]
    except (IndexError, ValueError):
        return {}


def skill_meta(text: str, fallback_name: str) -> dict[str, Any]:
    front = parse_front_matter(text)
    hermes = ((front.get("metadata") or {}).get("hermes") or {}) if isinstance(front.get("metadata"), dict) else {}
    tags = hermes.get("tags") if isinstance(hermes, dict) else None
    return {
        "name": str(front.get("name") or fallback_name),
        "description": str(front.get("description") or ""),
        "version": str(front.get("version") or ""),
        "author": str(front.get("author") or ""),
        "license": str(front.get("license") or ""),
        "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
        "category": str(hermes.get("category") or "") if isinstance(hermes, dict) else "",
    }


def bundled_names(manifest_text: str) -> set[str]:
    return {line.split(":", 1)[0].strip() for line in manifest_text.splitlines() if ":" in line}


def namespace_of(path: str, bundled: set[str]) -> str:
    parts = path.split("/")
    if len(parts) > 1 and parts[0] in NAMESPACES:
        return parts[0]
    return "bundled" if parts[-1] in bundled or parts[0] in bundled else "local"


def _relative_skill_path(path: str) -> str:
    marker = "/skills/"
    return path.split(marker, 1)[1] if marker in path else path.rsplit("/", 1)[-1]


def _ledger_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    actor = str(entry.get("actor") or "")
    session = str((entry.get("evidence") or {}).get("session_id") or "") if isinstance(entry.get("evidence"), dict) else ""
    touched = sorted({
        _relative_skill_path(str(item.get("path") or ""))
        for section in ("before", "after") for item in (entry.get(section) or []) if isinstance(item, dict)
    })
    return {
        "id": str(entry["id"]), "ts": str(entry.get("ts") or ""), "actor": actor,
        "origin": ORIGINS.get(actor, actor or "unknown"), "action": str(entry.get("action") or ""),
        "skill": str(entry.get("skill") or "").replace("\\", "/").rsplit("/", 1)[-1],
        "session": session, "files": touched,
    }


def build_snapshot(
    raw: dict[str, Any], *, label: str, snapshot_id: str, source: str = "live", sandbox_state: str = "", note: str = "",
) -> dict[str, Any]:
    """Normalise one collector result into a self-contained snapshot (texts live in ``blobs``)."""
    redactions = 0
    blobs: dict[str, str] = {}

    def clean(text: str) -> str:
        nonlocal redactions
        text, count = redact(text)
        redactions += count
        return text

    def store(text: str) -> str:
        text = clean(text)
        key = digest(text)
        blobs.setdefault(key, text)
        return key

    memory: dict[str, Any] = {}
    for item in raw.get("memory") or []:
        slot = {"MEMORY.md": "memory", "USER.md": "user"}.get(item.get("file"))
        if slot is None or "text" not in item:
            continue
        entries = split_entries(clean(item["text"]))
        memory[slot] = {
            "file": item["file"], "entries": entries, "chars": len(ENTRY_DELIMITER.join(entries)),
            "bytes": item.get("bytes", 0), "mtime": item.get("mtime", 0), "truncated": bool(item.get("truncated")),
        }

    bundled = bundled_names(str(raw.get("bundledManifest") or ""))
    usage_all = ((raw.get("usage") or {}).get("value") or {}) if isinstance(raw.get("usage"), dict) else {}
    skills = []
    for item in raw.get("skills") or []:
        if "text" not in item:
            continue
        path = str(item["path"])
        meta = skill_meta(item["text"], path.rsplit("/", 1)[-1])
        main_key = store(item["text"])
        files = []
        for extra in item.get("files") or []:
            has_text = "text" in extra
            files.append({
                "path": str(extra["path"]), "bytes": extra.get("bytes", 0),
                "sha": store(extra["text"]) if has_text else None,
                "binary": bool(extra.get("binary") or extra.get("oversize")), "truncated": bool(extra.get("truncated")),
            })
        usage = usage_all.get(meta["name"]) if isinstance(usage_all, dict) else None
        skills.append({
            "path": path, "name": meta["name"], "namespace": namespace_of(path, bundled), "meta": meta,
            "bytes": item.get("bytes", 0), "mtime": item.get("mtime", 0), "sha": main_key,
            "truncated": bool(item.get("truncated")), "files": files, "fileCount": item.get("fileCount", len(files)),
            "fp": digest([main_key, [(entry["path"], entry["sha"]) for entry in files]], 12),
            "usage": {key: usage[key] for key in USAGE_FIELDS if key in usage} if isinstance(usage, dict) else None,
        })
    skills.sort(key=lambda skill: (skill["path"]))

    ledger_raw = raw.get("ledger") or {}
    ledger = [entry for entry in map(_ledger_entry, ledger_raw.get("entries") or []) if entry]
    receipts = []
    for receipt in raw.get("receipts") or []:
        value = receipt.get("value")
        if isinstance(value, dict) and len(json.dumps(value)) <= MAX_RECEIPT_CHARACTERS:
            receipts.append({"name": str(receipt.get("name") or ""), "mtime": receipt.get("mtime", 0), "value": value})

    prompt_index = []
    index_value = ((raw.get("promptIndex") or {}).get("value") or {}) if isinstance(raw.get("promptIndex"), dict) else {}
    index_entries = index_value.get("skills") if isinstance(index_value, dict) else None
    for entry in index_entries if isinstance(index_entries, list) else []:
        if isinstance(entry, dict) and entry.get("skill_name"):
            prompt_index.append({
                "name": str(entry["skill_name"]), "category": str(entry.get("category") or ""),
                "description": clean(str(entry.get("description") or "")),
            })

    snapshot = {
        "schema": SNAPSHOT_SCHEMA, "id": snapshot_id, "label": label.strip() or "Snapshot", "note": note.strip(),
        "source": source, "capturedAt": int(raw.get("collectedAt") or time.time()), "sandboxState": sandbox_state,
        "memory": memory, "skills": skills, "ledger": ledger,
        "ledgerTotal": int(ledger_raw.get("total") or len(ledger)), "receipts": receipts,
        "promptIndex": prompt_index, "redactions": redactions, "blobs": blobs,
    }
    snapshot["contentHash"] = content_hash(snapshot)
    return snapshot


def content_hash(snapshot: dict[str, Any]) -> str:
    """Identity of what is visible in the viewer; used to skip automatic captures that changed nothing."""
    return digest({
        "memory": {slot: item["entries"] for slot, item in sorted(snapshot["memory"].items())},
        "skills": [
            [skill["path"], skill["fp"], {key: (skill["usage"] or {}).get(key) for key in ("use_count", "view_count", "patch_count")}]
            for skill in snapshot["skills"]
        ],
        "ledger": [entry["id"] for entry in snapshot["ledger"]],
        "receipts": [receipt["name"] for receipt in snapshot["receipts"]],
    })


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:48] or "snapshot"


def next_snapshot_id(directory: Path) -> str:
    numbers = [int(match.group(1)) for path in directory.glob("*.json") if (match := re.match(r"(\d{3,})-", path.name))]
    return f"{max(numbers, default=0) + 1:03d}"


def save_snapshot(directory: Path, snapshot: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot['id']}-{slugify(snapshot['label'])}.json"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)
    return path


def load_snapshots(directory: Path) -> list[dict[str, Any]]:
    snapshots = []
    for path in sorted(directory.glob("[0-9][0-9][0-9]*-*.json")) if directory.is_dir() else []:
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(snapshot, dict) and snapshot.get("schema") == SNAPSHOT_SCHEMA:
            snapshot["file"] = path.name
            snapshots.append(snapshot)
    return snapshots


def rename_snapshot(directory: Path, snapshot_id: str, label: str) -> bool:
    for snapshot in load_snapshots(directory):
        if snapshot["id"] == snapshot_id:
            old = directory / snapshot.pop("file")
            snapshot["label"] = label.strip()[:120] or snapshot["label"]
            save_snapshot(directory, snapshot)
            if old.name != f"{snapshot['id']}-{slugify(snapshot['label'])}.json":
                old.unlink(missing_ok=True)
            return True
    return False


def assemble_timeline(snapshots: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
    """Merge snapshots into the document the viewer embeds: shared blobs, one ledger with first-seen steps."""
    ordered = sorted(snapshots, key=lambda snapshot: (str(snapshot["id"]), snapshot["capturedAt"]))
    blobs: dict[str, str] = {}
    ledger: dict[str, dict[str, Any]] = {}
    steps = []
    for position, snapshot in enumerate(ordered):
        blobs.update(snapshot.get("blobs") or {})
        for entry in snapshot.get("ledger") or []:
            ledger.setdefault(entry["id"], {**entry, "seen": position})
        step = {key: value for key, value in snapshot.items() if key not in {"blobs", "ledger", "file", "schema"}}
        steps.append(step)
    return {
        "schema": TIMELINE_SCHEMA, "source": source, "generatedAt": int(time.time()), "limits": MEMORY_LIMITS,
        "agent": {"name": "Otto Autopilot", "role": "Budget Season Coordinator"},
        "snapshots": steps, "blobs": blobs,
        "ledger": sorted(ledger.values(), key=lambda entry: (entry["ts"], entry["id"])),
    }


def timeline_summary(timeline: dict[str, Any]) -> str:
    lines = [f"{timeline['source']} timeline: {len(timeline['snapshots'])} snapshot(s), {len(timeline['blobs'])} text blob(s)"]
    previous: dict[str, Any] | None = None
    for step in timeline["snapshots"]:
        entries = sum(len(item["entries"]) for item in step["memory"].values())
        by_namespace: dict[str, int] = {}
        for skill in step["skills"]:
            by_namespace[skill["namespace"]] = by_namespace.get(skill["namespace"], 0) + 1
        changed = ""
        if previous is not None:
            before = {skill["path"]: skill["fp"] for skill in previous["skills"]}
            added = sum(1 for skill in step["skills"] if skill["path"] not in before)
            modified = sum(1 for skill in step["skills"] if skill["path"] in before and before[skill["path"]] != skill["fp"])
            changed = f"  (+{added} new, ~{modified} changed skills)"
        shelves = ", ".join(f"{name} {count}" for name, count in sorted(by_namespace.items()))
        lines.append(f"  {step['id']}  {step['label']}: memory entries {entries}; skills {shelves or 'none'}{changed}")
        previous = step
    return "\n".join(lines)


def json_for_script(value: Any) -> str:
    """JSON that is safe inside an inline ``<script type="application/json">`` element."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(character, escaped)
    return text


def render_html(timeline: dict[str, Any], *, live: bool = False, viewer_dir: Path = VIEWER_DIR) -> str:
    """Assemble the single-file viewer by inlining the stylesheet, scripts and the timeline data."""
    shell = (viewer_dir / VIEWER_SHELL).read_text(encoding="utf-8")

    def inline(match: re.Match[str]) -> str:
        kind, name = match.groups()
        if kind == "data":
            return f'<script id="otto-data" type="application/json">{json_for_script({"live": live, "timeline": timeline})}</script>'
        text = (viewer_dir / str(name)).read_text(encoding="utf-8")
        if kind == "css":
            return f"<style>\n{text}\n</style>"
        if "</script" in text.lower() or "<!--" in text:
            raise ValueError(f"{name} contains a sequence that cannot be inlined in a script element.")
        return f"<script>\n{text}\n</script>"

    html, count = INLINE_MARKER.subn(inline, shell)
    if count < 3 or 'id="otto-data"' not in html:
        raise ValueError("The viewer shell is missing its @inline markers.")
    return html
