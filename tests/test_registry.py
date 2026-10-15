import json
from pathlib import Path

import pytest

from live2d_automation.capabilities import registry, route
from live2d_automation.models import AutomationError


def test_native_pilot_has_real_passed_evidence_and_is_not_stable():
    native = [c for c in registry()["capabilities"].values() if c["backend"] == "cubism_websocket"]
    assert native
    for name in ("parameter.edit", "structure.create", "transaction.rollback"):
        c = registry()["capabilities"][name]
        assert c["level"] == "pilot" and c["real_editor_evidence"]
        assert c["verified_versions"] == ["5.4.00 alpha2 / API 1.1.0"]
        for evidence in c["real_editor_evidence"]:
            data = json.loads(
                (Path(__file__).resolve().parents[1] / evidence).read_text(encoding="utf-8-sig")
            )
            assert data.get("postconditions_passed") is True or data.get("passed") is True
    connection = registry()["capabilities"]["editor.connect"]
    assert connection["level"] == "verified" and connection["real_editor_evidence"]


def test_unknown_or_geometry_backend_is_a_capability_gap():
    for name in ("geometry.edit", "psd.prepare", "unknown"):
        with pytest.raises(AutomationError) as error:
            route(name)
        assert error.value.code == "CAPABILITY_GAP"
