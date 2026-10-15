from importlib.resources import files

import yaml

from .models import AutomationError


def registry() -> dict:
    value = yaml.safe_load(
        files("live2d_automation").joinpath("capabilities.yaml").read_text(encoding="utf-8")
    )
    if value.get("schema_version") != 1:
        raise AutomationError("INVALID_REGISTRY", "Unsupported capability registry version")
    for name, item in value["capabilities"].items():
        if item["level"] not in {"reference_only", "pilot", "verified", "stable", "blocked"}:
            raise AutomationError("INVALID_REGISTRY", "Invalid capability level", capability=name)
        if (
            item["level"] in {"verified", "stable"}
            and item["backend"] == "cubism_websocket"
            and not item.get("real_editor_evidence")
        ):
            raise AutomationError(
                "INVALID_REGISTRY",
                "Native verified capability requires real Editor evidence",
                capability=name,
            )
    return value


def route(name: str):
    item = registry()["capabilities"].get(name)
    if not item or not item["implemented"] or item["level"] == "blocked":
        raise AutomationError(
            "CAPABILITY_GAP", "Requested capability has no implemented backend", capability=name
        )
    return item
