import json

from live2d_automation.models import AutomationError


async def uncertain_job(engine, blink_ops):
    plan = await engine.run("plan", expected_uid=engine.ws.uid, operations=blink_ops)
    engine.ws.fail_commit = True
    return await engine.run(
        "apply",
        expected_uid=engine.ws.uid,
        operations=blink_ops,
        idempotency_key="recovery",
        dry_run=False,
        confirm=True,
        expected_snapshot_sha256=plan.data["snapshot_sha256"],
    )


async def test_reopened_matching_native_backup_can_clear_recovery(engine, blink_ops):
    result = await uncertain_job(engine, blink_ops)
    assert result.status == "blocked" and engine.marker.exists()
    engine.ws.uid = "new-uid-after-reopen"
    engine.ws.params = {}
    engine.ws.groups = {}
    restored = await engine.run("rollback", job_id=result.job_id, expected_uid=engine.ws.uid)
    assert restored.status == "rolled_back" and restored.postconditions_passed
    assert not engine.marker.exists()


async def test_same_structure_in_wrong_native_file_cannot_clear_marker(engine, blink_ops):
    result = await uncertain_job(engine, blink_ops)
    engine.ws.uid = "wrong-file-uid"
    engine.ws.params = {}
    engine.ws.groups = {}
    engine.ws.project.write_bytes(b"DIFFERENT PROTOCOL FIXTURE FILE")
    restored = await engine.run("rollback", job_id=result.job_id, expected_uid=engine.ws.uid)
    assert restored.status == "blocked"
    assert restored.error["code"] == "RESTORE_IDENTITY_UNCONFIRMED"
    assert engine.marker.exists()


async def test_no_allow_and_unexpected_uid_block_without_writes(engine):
    engine.ws.allowed = False
    denied = await engine.run("inspect")
    assert denied.error["code"] == "READ_PERMISSION_DENIED"
    engine.ws.allowed = True
    changed = await engine.run("inspect", expected_uid="another-model")
    assert changed.error["code"] == "MODEL_UID_CHANGED"
    assert not any(m == "EditBegin" for m, _ in engine.ws.calls)


async def test_legacy_editor_only_reports_partial_read_information(engine):
    request = engine.ws.request

    async def legacy(method, data):
        if method in {"GetIsEditApproval", "GetParameterStructure"}:
            raise AutomationError("EDITOR_API_ERROR", "Legacy API", editor_error="MethodNotFound")
        return await request(method, data)

    engine.ws.request = legacy
    result = await engine.run("inspect")
    assert result.status == "review_required"
    assert result.data["complete_structure"] is False
    assert not any(m == "EditBegin" for m, _ in engine.ws.calls)


async def test_recovery_record_is_explicitly_non_successful(engine, blink_ops):
    result = await uncertain_job(engine, blink_ops)
    record = json.loads(
        (engine.settings.state_dir / "jobs" / result.job_id / "job.json").read_text()
    )
    assert record["result"]["status"] == "blocked"
    assert record["result"]["postconditions_passed"] is False
