from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AutomationError, Evidence


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic_json(path: Path, value: Any, *, overwrite: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".live2d-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            # Atomic exclusive publication: a competing file is never replaced.
            os.link(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def safe_path(root: Path, name: str) -> Path:
    # A drive path or backslash is unsafe even on a non-Windows host.
    if not name or "\\" in name or ":" in name:
        raise AutomationError("UNSAFE_PATH", "Use a relative path inside the output directory")
    root = root.resolve()
    path = (root / name).resolve()
    if path == root or not path.is_relative_to(root):
        raise AutomationError("UNSAFE_PATH", "Output escaped its configured directory")
    return path


def redact(value: Any):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if k.lower() in {"token", "authorization", "secret"} else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Journal:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def trace(self, event: str, **data):
        item = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **redact(data)}
        with (self.root / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(canonical(item).decode() + "\n")

    def save(self, name: str, value: Any, kind="structure_snapshot") -> Evidence:
        path = safe_path(self.root, name)
        atomic_json(path, redact(value))
        return Evidence(
            kind=kind, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        )
