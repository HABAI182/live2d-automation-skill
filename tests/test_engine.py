import asyncio
import copy
from datetime import date

import pytest
from filelock import FileLock

from live2d_automation.storage import atomic_json


async def apply(engine, ops, key="test"):
    plan = await engine.run("plan", expected_uid=engine.ws.uid, operations=ops)
    assert plan.status in {"review_required", "verified"}, plan
    return await engine.run(
        "apply",
        expected_uid=engine.ws.uid,
        operations=ops,
        idempotency_key=key,
        dry_run=False,
        confirm=True,
        expected_snapshot_sha256=plan.data["snapshot_sha256"],
    )


async def test_atomic_read_edit_verify_and_idempotency(engine, blink_ops):
    result = await apply(engine, blink_ops)
    assert result.status == "verified" and result.postconditions_passed
    assert len(engine.ws.params) == 1 and len(engine.ws.groups) == 1
    calls = copy.deepcopy(engine.ws.calls)
    again = await apply(engine, blink_ops)
    assert again.data["historical_replay"]
    assert sum(m == "AddParameter" for m, _ in engine.ws.calls) == 1
    assert engine.ws.project.read_bytes().startswith(b"CONTRACT FIXTURE ONLY")
    assert not engine.marker.exists()
    assert (await engine.run("verify", job_id=result.job_id)).status == "verified"
    assert calls


async def test_default_is_dry_run(engine, blink_ops):
    result = await engine.run(
        "apply", expected_uid=engine.ws.uid, operations=blink_ops, idempotency_key="dry"
    )
    assert result.data["dry_run"] and not engine.ws.params
    assert all(m != "EditBegin" for m, _ in engine.ws.calls)


async def test_second_half_failure_restores_first_half(engine, blink_ops):
    engine.ws.fail_method = "AddParameter"
    result = await apply(engine, blink_ops)
    assert result.status == "rolled_back" and not result.postconditions_passed
    assert engine.ws.groups == {} and engine.ws.params == {}
    assert any(e.kind == "rollback_readback" for e in result.evidence)
    assert not engine.marker.exists()


async def test_successful_response_without_effect_is_rolled_back(engine, blink_ops):
    engine.ws.no_effect = True
    result = await apply(engine, blink_ops)
    assert result.status == "rolled_back"
    assert result.error["code"] == "POSTCONDITION_FAILED"


async def test_unplanned_change_is_rolled_back(engine, blink_ops):
    engine.ws.unplanned_change = True
    result = await apply(engine, blink_ops)
    assert result.status == "rolled_back"
    assert engine.ws.groups == {}


async def test_user_cancel_is_not_success(engine, blink_ops):
    engine.ws.cancel_on_write = True
    result = await apply(engine, blink_ops)
    assert result.status == "rolled_back"
    assert result.error["code"] == "USER_CANCELLED"
    assert not engine.ws.params and not engine.ws.groups


async def test_uncertain_rollback_blocks_future_writes(engine, blink_ops):
    engine.ws.fail_method = "AddParameter"
    engine.ws.fail_cancel = True
    result = await apply(engine, blink_ops)
    assert result.status == "blocked" and engine.marker.exists()
    again = await apply(engine, [{"kind": "create_group", "id": "Another"}], key="next")
    assert again.error["code"] == "RECOVERY_REQUIRED"


async def test_uncertain_commit_is_never_automatically_replayed(engine, blink_ops):
    engine.ws.fail_commit = True
    result = await apply(engine, blink_ops)
    assert result.status == "blocked" and engine.marker.exists()
    assert not any(m == "EditEnd" and d["Cancel"] for m, d in engine.ws.calls)
    rollback = await engine.run("rollback", job_id=result.job_id)
    assert rollback.status == "blocked"
    assert rollback.error["code"] == "MANUAL_RESTORE_REQUIRED"


async def test_uid_change_never_edits_new_model(engine, blink_ops):
    engine.ws.uid_after_write = "different-model"
    result = await apply(engine, blink_ops)
    assert result.status == "blocked" and engine.marker.exists()
    assert all(
        d.get("ModelUID", "fixture-model") != "different-model"
        for m, d in engine.ws.calls
        if m.startswith("Add")
    )


@pytest.mark.parametrize(
    "case,code",
    [
        ("no_edit", "EDIT_PERMISSION_DENIED"),
        ("wrong_mode", "INVALID_EDITOR_MODE"),
        ("expired", "EDITOR_ALPHA_EXPIRED"),
        ("stable", "UNSUPPORTED_EDITOR_VERSION"),
        ("unknown", "UNSUPPORTED_EDITOR_VERSION"),
        ("unbound", "EDITOR_VERSION_UNVERIFIED"),
        ("new_api", "UNSUPPORTED_API_VERSION"),
    ],
)
async def test_preconditions_stop_before_native_transaction(engine, blink_ops, case, code):
    if case == "no_edit":
        engine.ws.edit_allowed = False
    elif case == "wrong_mode":
        engine.ws.mode = "Animation"
    elif case == "expired":
        engine.today = lambda: date(2026, 10, 19)
    elif case in {"stable", "unknown"}:
        engine.listener_probe = lambda port: {
            "cubism_process": True,
            "version": "5.3.04" if case == "stable" else "6.0.00",
        }
    elif case == "unbound":
        engine.listener_probe = lambda port: {"cubism_process": False}
    else:
        engine.ws.version = "1.2.0"
    result = await apply(engine, blink_ops)
    assert result.status == "blocked" and result.error["code"] == code
    assert not any(m == "EditBegin" for m, _ in engine.ws.calls)


async def test_stale_plan_requires_replan(engine, blink_ops):
    plan = await engine.run("plan", expected_uid=engine.ws.uid, operations=blink_ops)
    engine.ws.groups["ExternalChange"] = {
        "Id": "ExternalChange",
        "Name": "other",
        "EntryType": "ParameterGroup",
    }
    result = await engine.run(
        "apply",
        expected_uid=engine.ws.uid,
        operations=blink_ops,
        idempotency_key="stale",
        dry_run=False,
        confirm=True,
        expected_snapshot_sha256=plan.data["snapshot_sha256"],
    )
    assert result.error["code"] == "STALE_PLAN"
    assert not any(m == "EditBegin" for m, _ in engine.ws.calls)


async def test_cross_process_write_lock(engine, blink_ops):
    plan = await engine.run("plan", expected_uid=engine.ws.uid, operations=blink_ops)
    with FileLock(engine.lock_root / f"editor-{engine.settings.port}.lock"):
        result = await engine.run(
            "apply",
            expected_uid=engine.ws.uid,
            operations=blink_ops,
            idempotency_key="busy",
            dry_run=False,
            confirm=True,
            expected_snapshot_sha256=plan.data["snapshot_sha256"],
        )
    assert result.error["code"] == "EDITOR_BUSY"


async def test_key_collision_rejected(engine, blink_ops):
    await apply(engine, blink_ops)
    result = await apply(engine, [{"kind": "create_group", "id": "Other"}])
    assert result.error["code"] == "IDEMPOTENCY_CONFLICT"


async def test_range_change_preserves_bound_keys(engine, blink_ops):
    await apply(engine, blink_ops)
    engine.ws.params["ParamPetTap"]["KeyValues"] = [0, 0.5, 1]
    result = await engine.run(
        "plan",
        expected_uid=engine.ws.uid,
        operations=[{"kind": "update_parameter", "id": "ParamPetTap", "max": 0.3}],
    )
    assert result.error["code"] == "INVALID_RANGE"
    result = await engine.run(
        "plan",
        expected_uid=engine.ws.uid,
        operations=[{"kind": "delete_parameter", "id": "ParamPetTap"}],
    )
    assert result.error["code"] == "BOUND_PARAMETER"


async def test_empty_part_warp_rotation_properties(engine):
    ops = [
        {"kind": "create_part", "id": "PetRoot"},
        {"kind": "create_warp", "id": "PetWarp", "parent_id": "PetRoot", "warp_div_h": 3},
        {"kind": "create_rotation", "id": "PetRotation", "parent_id": "PetRoot"},
    ]
    result = await apply(engine, ops)
    assert result.status == "review_required" and result.postconditions_passed
    assert engine.ws.objects["PetWarp"]["ParentId"] == "PetRoot"


async def test_restart_marker_blocks_without_replaying(engine, blink_ops):
    atomic_json(engine.marker, {"job_id": "job-crashed", "phase": "begin_pending"})
    result = await apply(engine, blink_ops)
    assert result.error["code"] == "RECOVERY_REQUIRED"
    assert not any(m == "EditBegin" for m, _ in engine.ws.calls)


async def test_no_native_source_no_edit(engine, blink_ops):
    engine.ws.project.unlink()
    result = await apply(engine, blink_ops)
    assert result.error["code"] == "BACKUP_UNAVAILABLE"


async def test_input_rejects_raw_commands_and_bad_values(engine):
    for op in [
        {"kind": "ExecuteShell", "id": "X"},
        {"kind": "create_group", "id": "X", "raw": "danger"},
        {"kind": "create_parameter", "id": "X", "min": 0, "default": float("nan"), "max": 1},
    ]:
        result = await engine.run("plan", expected_uid=engine.ws.uid, operations=[op])
        assert result.status == "blocked" and result.error["code"] == "INVALID_INPUT"


async def test_task_cancellation_rolls_back(engine, blink_ops):
    request = engine.ws.request

    async def interrupt(method, data):
        if method == "AddParameter":
            raise asyncio.CancelledError()
        return await request(method, data)

    engine.ws.request = interrupt
    with pytest.raises(asyncio.CancelledError):
        await apply(engine, blink_ops)
    assert not engine.ws.params and not engine.ws.groups
    assert not engine.marker.exists()
