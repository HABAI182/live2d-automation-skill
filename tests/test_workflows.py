import copy

import pytest
from pydantic import ValidationError

from live2d_automation.animation import MotionSpec, compile_animation
from live2d_automation.bindings import BlinkRecipe, BlinkSide, capture, plan_blink
from live2d_automation.files import load_json
from live2d_automation.storage import atomic_json


def seed(engine):
    engine.ws.params["Eye"] = {
        "Id": "Eye",
        "Name": "Eye",
        "EntryType": "Parameter",
        "Min": 0.0,
        "Default": 1.0,
        "Max": 1.0,
        "GroupId": "",
        "IsRepeat": False,
        "IsBlendShape": False,
        "KeyValues": [],
    }


def motion():
    return {
        "kind": "motion",
        "name": "Blink",
        "duration": 1.0,
        "curves": [
            {
                "id": "Eye",
                "points": [
                    {"time": 0.0, "value": 1.0},
                    {"time": 0.5, "value": 0.0},
                    {"time": 1.0, "value": 1.0},
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "points",
    [
        [{"time": 0.0, "value": 1.0}, {"time": 0.0, "value": 0.0}],
        [{"time": 0.1, "value": 1.0}, {"time": 1.0, "value": 0.0}],
        [{"time": 0.0, "value": float("nan")}, {"time": 1.0, "value": 0.0}],
    ],
)
def test_motion_rejects_invalid_time_or_nonfinite(points):
    spec = motion()
    spec["curves"][0]["points"] = points
    with pytest.raises(ValidationError):
        MotionSpec.model_validate(spec)


async def test_generate_links_bundle_checks_range_hash_and_never_overwrites(engine):
    seed(engine)
    root = engine.settings.output_dir
    root.mkdir()
    (root / "model.moc3").write_bytes(b"FILE CONTRACT ONLY")
    (root / "texture.png").write_bytes(b"FILE CONTRACT ONLY")
    original = {"Version": 3, "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]}}
    atomic_json(root / "test.model3.json", original)
    kwargs = dict(
        expected_uid=engine.ws.uid,
        spec=motion(),
        relative_path="motions/blink.motion3.json",
        model_relative_path="test.model3.json",
    )
    planned = await engine.run("generate_animation", **kwargs)
    assert planned.status == "review_required"
    assert not (root / "motions").exists()
    stale = await engine.run(
        "generate_animation", **kwargs, dry_run=False, confirm=True, expected_hash="stale"
    )
    assert stale.error["code"] == "STALE_PLAN"
    applied = await engine.run(
        "generate_animation",
        **kwargs,
        dry_run=False,
        confirm=True,
        expected_hash=planned.data["snapshot_sha256"],
    )
    assert applied.postconditions_passed
    data = load_json(root / "motions/blink.motion3.json")
    assert data["Meta"]["TotalSegmentCount"] == 2 and data["Meta"]["TotalPointCount"] == 3
    assert load_json(root / "test.model3.json")["FileReferences"]["Motions"]["Blink"]
    again = await engine.run("generate_animation", **kwargs)
    assert again.error["code"] == "INVALID_OUTPUT"
    bad = motion()
    bad["curves"][0]["points"][0]["value"] = 2.0
    result = await engine.run(
        "generate_animation", expected_uid=engine.ws.uid, spec=bad, relative_path="bad.motion3.json"
    )
    assert result.error["code"] == "OUT_OF_RANGE"
    assert not (root / "bad.motion3.json").exists()


async def test_ui_plan_validates_paths_without_loading_windows_or_writing(engine):
    seed(engine)
    good = await engine.run(
        "editor_file", action="save", uid=engine.ws.uid, relative_path="copy.cmo3"
    )
    assert good.status == "review_required" and good.data["requires_focus"]
    for name in ("../escape.cmo3", "C:/escape.cmo3", "file.moc3"):
        result = await engine.run(
            "editor_file", action="save", uid=engine.ws.uid, relative_path=name
        )
        assert result.status == "blocked"
    assert not engine.marker.exists()
    assert not engine.settings.output_dir.exists()


async def test_binding_refuses_combination_explosion_before_allocating(engine, monkeypatch):
    seed(engine)
    original = engine.take_snapshot
    original_request = engine.ws.request

    async def snapshot(uid):
        result = await original(uid)
        result["normalized"]["parts"]["EyeMesh"] = {"Type": "ArtMesh"}
        return result

    monkeypatch.setattr(engine, "take_snapshot", snapshot)

    async def request(method, data):
        if method == "GetParameterKeys":
            return {"Parameters": [{"Id": "A", "KeyValues": list(range(1000))}] * 20}
        return await original_request(method, data)

    monkeypatch.setattr(engine.ws, "request", request)
    with pytest.raises(Exception, match="64 forms"):
        await capture(engine, engine.ws.uid)


def test_blink_refuses_other_binding_and_non_target_changes():
    recipe = BlinkRecipe(sides=[BlinkSide(parameter="Eye", eye_mesh="E", lid_mesh="L")])
    before = {
        "structure": {
            "normalized": {
                "parameters": {"Eye": {"Min": 0, "Default": 1, "Max": 1, "KeyValues": []}},
                "parts": {"E": {"Type": "ArtMesh"}, "L": {"Type": "ArtMesh"}},
            }
        },
        "objects": {"E": {"keys": [{"Id": "Other", "KeyValues": [0, 1]}]}, "L": {"keys": []}},
    }
    with pytest.raises(Exception, match="other parameter"):
        plan_blink(before, recipe)
    clean = copy.deepcopy(before)
    clean["objects"]["E"]["keys"] = []
    unchanged = copy.deepcopy(clean)
    planned = plan_blink(clean, recipe)
    assert len(planned["requests"]) == 8
    assert clean == unchanged


async def test_binding_failed_write_cancels_and_confirms_properties(engine, monkeypatch):
    from live2d_automation import bindings
    from live2d_automation.models import AutomationError

    before = {
        "structure": {
            "model_uid": engine.ws.uid,
            "normalized": {
                "parameters": {"Eye": {"Min": 0, "Default": 1, "Max": 1, "KeyValues": []}},
                "parts": {"E": {"Type": "ArtMesh"}, "L": {"Type": "ArtMesh"}},
            },
        },
        "objects": {"E": {"keys": []}, "L": {"keys": []}},
    }

    async def capture_contract(*args):
        return copy.deepcopy(before)

    monkeypatch.setattr(bindings, "capture", capture_contract)
    calls = []

    async def write_contract(method, data):
        calls.append((method, data))
        if method == "EditArtMesh":
            raise AutomationError("INJECTED_WRITE_FAILURE", "Contract fault")

    monkeypatch.setattr(engine, "write_ok", write_contract)
    recipe = {"sides": [{"parameter": "Eye", "eye_mesh": "E", "lid_mesh": "L"}]}
    plan = await engine.run("blink", uid=engine.ws.uid, recipe=recipe)
    result = await engine.run(
        "blink",
        uid=engine.ws.uid,
        recipe=recipe,
        dry_run=False,
        confirm=True,
        expected_hash=plan.data["snapshot_sha256"],
    )
    assert result.status == "rolled_back" and result.error["code"] == "INJECTED_WRITE_FAILURE"
    assert calls[-1] == ("EditEnd", {"Cancel": True})
    assert not engine.marker.exists()


async def test_ui_unknown_dialog_leaves_marker_and_recovery_requires_clean_ui(engine, monkeypatch):
    from live2d_automation import editor_ui
    from live2d_automation.models import AutomationError

    class ContractUI:
        def __init__(self, pid):
            self.bridge = self

        def shortcut(self, name):
            raise AutomationError("UI_CONTROL_MISSING", "Injected unexpected dialog")

        def windows(self):
            return [{"title": "Unexpected dialog"}]

        def close(self):
            pass

    monkeypatch.setattr(editor_ui, "EditorUI", ContractUI)
    kwargs = dict(action="save", uid=engine.ws.uid, relative_path="copy.cmo3")
    plan = await engine.run("editor_file", **kwargs)
    result = await engine.run(
        "editor_file",
        **kwargs,
        dry_run=False,
        confirm=True,
        expected_hash=plan.data["snapshot_sha256"],
    )
    assert result.status == "blocked" and engine.marker.exists()
    blocked = await engine.run("editor_file", **kwargs)
    assert blocked.error["code"] == "RECOVERY_REQUIRED"
    recover = await engine.run("rollback", job_id=result.job_id)
    assert recover.error["code"] == "UI_DIALOG_PENDING" and engine.marker.exists()
    monkeypatch.setattr(
        ContractUI, "windows", lambda self: [{"title": "Live2D Cubism Editor contract"}]
    )
    recovered = await engine.run("rollback", job_id=result.job_id)
    assert recovered.postconditions_passed and not engine.marker.exists()


def test_atomic_exclusive_publish_never_replaces_competing_file(tmp_path):
    path = tmp_path / "animation.json"
    atomic_json(path, {"owner": "first"}, overwrite=False)
    with pytest.raises(FileExistsError):
        atomic_json(path, {"owner": "second"}, overwrite=False)
    assert load_json(path) == {"owner": "first"}


def test_expression_default_based_range_is_explicit():
    from live2d_automation.animation import ExpressionSpec

    parameters = {"Eye": {"Min": 0, "Default": 1, "Max": 1}}
    spec = ExpressionSpec.model_validate(
        {
            "kind": "expression",
            "name": "Close",
            "parameters": [{"id": "Eye", "value": -1.0, "blend": "Add"}],
        }
    )
    assert compile_animation(spec, parameters)["Parameters"][0]["Blend"] == "Add"
    with pytest.raises(Exception, match="range"):
        compile_animation(
            spec.model_copy(
                update={"parameters": [spec.parameters[0].model_copy(update={"value": 1.0})]}
            ),
            parameters,
        )
