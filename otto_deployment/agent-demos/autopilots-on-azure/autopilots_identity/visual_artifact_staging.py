from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path


SCOPE = re.compile(r"^[0-9a-f]{32}$")
MAX_BYTES = 64 * 1024


def builder():
    root = Path(os.getenv("AUTOPILOTS_APPLICATION_ROOT", str(Path(__file__).resolve().parents[1])))
    path = root / "runtime_skills" / "visual-artifacts" / "build.py"
    if not path.is_file():
        path = root / "visual_artifacts_runtime" / "build.py"
    if not path.is_file():
        path = root / "runtimes" / "hermes" / "runtime_skills" / "visual-artifacts" / "build.py"
    spec = importlib.util.spec_from_file_location("otto_visual_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Visual artifact builder is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def staged_path(scope: str, *, create: bool = False) -> Path:
    if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
        raise ValueError("Invalid visual artifact scope")
    root = Path(os.getenv("HERMES_HOME", "/data/hermes")) / "workspace"
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Workspace is unavailable or unsafe")
    folder = root
    for component in ("visual-artifacts", scope):
        folder = folder / component
        if create:
            folder.mkdir(mode=0o700, exist_ok=True)
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Artifact workspace contains an unsafe directory")
    return folder / "draft.json"


def stage(scope: str, content: str) -> dict:
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Visual artifact input exceeds 64 KiB")
    value = builder().validate(json.loads(content))
    path = staged_path(scope, create=True)
    if path.is_symlink():
        raise ValueError("Artifact draft is a symlink")
    if path.exists() and not path.is_file():
        raise ValueError("Artifact draft is not a regular file")
    fd, temporary = tempfile.mkstemp(prefix=".draft-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"scope": scope, "marker": f'<VISUAL_DOCUMENT_REQUEST>{scope}</VISUAL_DOCUMENT_REQUEST>'}


def read(scope: str) -> dict:
    path = staged_path(scope)
    if path.is_symlink():
        raise ValueError("Artifact draft is a symlink")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Artifact draft is not a private regular file")
        data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Visual artifact input exceeds 64 KiB")
    return builder().validate(json.loads(data))


def cleanup_expired(max_age: int = 3600) -> int:
    root = Path(os.getenv("HERMES_HOME", "/data/hermes")) / "workspace" / "visual-artifacts"
    if root.is_symlink() or not root.is_dir():
        return 0
    removed = 0
    for folder in root.iterdir():
        if not SCOPE.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
            continue
        draft = folder / "draft.json"
        if draft.is_symlink() or not draft.is_file() or time.time() - draft.stat().st_mtime < max_age:
            continue
        draft.unlink()
        removed += 1
    return removed
