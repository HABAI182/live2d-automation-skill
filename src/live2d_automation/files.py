from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .capabilities import route
from .models import AutomationError, Result
from .storage import safe_path


def load_json(path: Path):
    def constant(value):
        raise ValueError(f"Non-finite JSON constant: {value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        path.read_text(encoding="utf-8-sig"), parse_constant=constant, object_pairs_hook=pairs
    )

    def finite(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON number is outside finite range")
        if isinstance(item, dict):
            for child in item.values():
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)

    finite(value)
    return value


def validate_bundle(model_path: str) -> Result:
    route("bundle.validate")
    path = Path(model_path).resolve()
    checked, errors = [], []
    try:
        if not path.name.endswith(".model3.json"):
            raise AutomationError("INVALID_BUNDLE", "Expected a .model3.json file")
        model = load_json(path)
        if (
            not isinstance(model, dict)
            or model.get("Version") != 3
            or not isinstance(model.get("FileReferences"), dict)
        ):
            raise AutomationError(
                "INVALID_BUNDLE", "model3 Version=3 and FileReferences are required"
            )
        refs = model["FileReferences"]
        if (
            not isinstance(refs.get("Moc"), str)
            or not isinstance(refs.get("Textures"), list)
            or not refs["Textures"]
        ):
            raise AutomationError(
                "INVALID_BUNDLE", "Moc and nonempty Textures references are required"
            )
        entries = [("Moc", refs["Moc"]), *(("Texture", s) for s in refs["Textures"])]
        for field in ("Physics", "Pose", "UserData", "DisplayInfo"):
            if field in refs:
                entries.append((field, refs[field]))
        for item in refs.get("Expressions", []):
            entries.append(("Expression", item["File"]))
        for motions in refs.get("Motions", {}).values():
            for item in motions:
                entries.append(("Motion", item["File"]))
                if item.get("Sound"):
                    entries.append(("Sound", item["Sound"]))
        for kind, name in entries:
            try:
                target = safe_path(path.parent, name)
                if not target.is_file() or target.stat().st_size == 0:
                    raise ValueError("Missing or empty resource")
                if kind == "Moc" and target.suffix.lower() != ".moc3":
                    raise ValueError("Moc resource must have .moc3 extension")
                if target.suffix.lower() == ".json":
                    data = load_json(target)
                    if not isinstance(data, dict):
                        raise ValueError("Resource JSON must be an object")
                    if kind == "Expression":
                        if data.get("Type") != "Live2D Expression" or not isinstance(
                            data.get("Parameters"), list
                        ):
                            raise ValueError("Invalid expression structure")
                        ids = set()
                        for p in data["Parameters"]:
                            if (
                                p["Id"] in ids
                                or p.get("Blend", "Add") not in {"Add", "Multiply", "Overwrite"}
                                or not isinstance(p["Value"], (int, float))
                                or isinstance(p["Value"], bool)
                                or not math.isfinite(p["Value"])
                            ):
                                raise ValueError("Invalid expression parameter")
                            ids.add(p["Id"])
                    if kind == "Motion":
                        if (
                            data.get("Version") != 3
                            or not isinstance(data.get("Meta"), dict)
                            or not isinstance(data.get("Curves"), list)
                        ):
                            raise ValueError("Invalid motion structure")
                        duration = data["Meta"].get("Duration")
                        if (
                            not isinstance(duration, (int, float))
                            or isinstance(duration, bool)
                            or not math.isfinite(duration)
                            or duration < 0
                        ):
                            raise ValueError("Invalid motion duration")
                checked.append(
                    {
                        "kind": kind,
                        "file": name,
                        "size": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    }
                )
            except (AutomationError, ValueError, OSError, KeyError, TypeError) as exc:
                errors.append({"kind": kind, "file": name, "message": str(exc)})
    except (AutomationError, ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        errors.append({"message": str(exc)})
    return Result(
        status="failed" if errors else "review_required",
        operation="validate_bundle",
        backend="file",
        tool_call_succeeded=not errors,
        postconditions_passed=False,
        data={
            "resource_checks_passed": not errors,
            "checked": checked,
            "errors": errors,
            "core_loaded": False,
            "visual_verified": False,
        },
        warnings=[
            "Partial JSON/resource validation only; MOC3 binary, parameter IDs and motion curves require real SDK/Core validation"
        ],
        next_action="fix_resources" if errors else "load_in_real_cubism_core",
    )
