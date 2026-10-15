import json

import pytest

from live2d_automation.files import load_json, validate_bundle
from live2d_automation.models import AutomationError
from live2d_automation.storage import atomic_json, safe_path


@pytest.mark.parametrize(
    "name",
    [
        "../outside.json",
        "C:/secret.json",
        "//server/share/a.json",
        "a\\b.json",
        "/outside.json",
        "",
    ],
)
def test_output_confinement(tmp_path, name):
    with pytest.raises(AutomationError):
        safe_path(tmp_path, name)


@pytest.mark.parametrize(
    "text", ['{"a": NaN}', '{"a": Infinity}', '{"a": 1, "a": 2}', '{"a": 1e999}']
)
def test_strict_json(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_json(path)


def test_fake_binary_is_never_certified_as_loadable(tmp_path):
    model = tmp_path / "contract.model3.json"
    (tmp_path / "contract.moc3").write_bytes(b"CONTRACT TEST ONLY; NOT REAL MOC3")
    (tmp_path / "texture.png").write_bytes(b"CONTRACT TEST ONLY; NOT A PNG")
    atomic_json(
        model,
        {"Version": 3, "FileReferences": {"Moc": "contract.moc3", "Textures": ["texture.png"]}},
    )
    result = validate_bundle(str(model))
    assert result.status == "review_required" and not result.postconditions_passed
    assert not result.data["core_loaded"] and not result.data["visual_verified"]


def test_resource_traversal_and_missing_file_fail(tmp_path):
    model = tmp_path / "contract.model3.json"
    atomic_json(
        model, {"Version": 3, "FileReferences": {"Moc": "../bad.moc3", "Textures": ["missing.png"]}}
    )
    result = validate_bundle(str(model))
    assert result.status == "failed" and len(result.data["errors"]) == 2


def test_no_source_changes_during_bundle_check(tmp_path):
    model = tmp_path / "invalid.model3.json"
    model.write_text(json.dumps({"Version": 2}))
    before = model.read_bytes()
    assert validate_bundle(str(model)).status == "failed"
    assert model.read_bytes() == before
