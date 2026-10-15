"""Opt-in native create/read/cancel/read smoke test on a saved disposable .cmo3.

No fixture backend is available here. Missing Editor or version returns blocked.
"""

import argparse
import asyncio
import json
import time
import uuid

from filelock import FileLock

from live2d_automation.cli import settings_from_env
from live2d_automation.engine import Engine
from live2d_automation.models import AutomationError, Result
from live2d_automation.storage import atomic_json, safe_path
from live2d_automation.structure import check_desired, plan_operations


async def smoke(uid):
    started = time.perf_counter()
    engine = Engine(settings_from_env())
    key = "real-" + uuid.uuid4().hex[:12]
    job_id = "job-" + key
    lock = FileLock(engine.lock_root / f"editor-{engine.settings.port}.lock", timeout=0)
    evidence, active, before, plan = [], False, None, None
    job_path = safe_path(engine.settings.state_dir, f"jobs/{job_id}/job.json")
    try:
        lock.acquire()
        if engine.marker.exists():
            raise AutomationError(
                "RECOVERY_REQUIRED", "Resolve previous uncertain transaction first"
            )
        uid, info = await engine.ready(uid)
        engine.edit_gate(info)
        before = await engine.take_snapshot(uid)
        suffix = uuid.uuid4().hex[:8]
        operations = engine.parse_ops(
            [
                {"kind": "create_group", "id": "TestGroup_" + suffix},
                {
                    "kind": "create_parameter",
                    "id": "TestParameter_" + suffix,
                    "group_id": "TestGroup_" + suffix,
                    "min": 0,
                    "default": 0,
                    "max": 1,
                },
                {"kind": "create_part", "id": "TestPart_" + suffix},
                {
                    "kind": "create_warp",
                    "id": "TestWarp_" + suffix,
                    "parent_id": "TestPart_" + suffix,
                },
                {
                    "kind": "create_rotation",
                    "id": "TestRotation_" + suffix,
                    "parent_id": "TestPart_" + suffix,
                },
            ]
        )
        plan = plan_operations(before, operations)
        evidence.append(engine.journal.save(f"{job_id}/before.json", before))
        evidence.append(await engine.backup(uid, job_id))
        atomic_json(
            job_path, {"before": before, "plan": plan, "model_uid": uid, "phase": "begin_pending"}
        )
        atomic_json(
            engine.marker,
            {
                "job_id": job_id,
                "job_path": str(job_path),
                "model_uid": uid,
                "phase": "native_smoke",
            },
        )
        active = True
        await engine.write_ok("EditBegin", {"Silent": False})
        for request in plan["requests"]:
            await engine.ready(uid)
            await engine.write_ok(request["method"], {"ModelUID": uid, **request["data"]})
        changed = await engine.take_snapshot(uid)
        check_desired(before, changed, plan)
        await engine.verify_objects(uid, plan)
        evidence.append(engine.journal.save(f"{job_id}/changed.json", changed))
        await engine.write_ok("EditEnd", {"Cancel": True})
        active = False
        restored = await engine.take_snapshot(uid)
        if restored["normalized"] != before["normalized"]:
            raise AutomationError(
                "ROLLBACK_UNCONFIRMED",
                "Cancelled native smoke did not restore the original structure",
            )
        evidence.append(
            engine.journal.save(f"{job_id}/restored.json", restored, "rollback_readback")
        )
        result = Result(
            job_id=job_id,
            status="rolled_back",
            operation="real_editor_smoke",
            editor_version=engine.editor.get("version"),
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            evidence=evidence,
            data={
                "real_editor": True,
                "outcome": "native_create_read_cancel_read_passed",
                "geometry_verified": False,
                "computer_use_calls": 0,
                "operations": [op.kind for op in operations],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        atomic_json(
            job_path, {"before": before, "plan": plan, "result": result.model_dump(mode="json")}
        )
        engine.marker.unlink()
        return result
    except BaseException as exc:
        restored_ok = False
        if active and before:
            try:
                await engine.write_ok("EditEnd", {"Cancel": True})
                restored = await engine.take_snapshot(uid)
                restored_ok = restored["normalized"] == before["normalized"]
                if restored_ok:
                    evidence.append(
                        engine.journal.save(
                            f"{job_id}/failure-restored.json", restored, "rollback_readback"
                        )
                    )
                    engine.marker.unlink()
            except BaseException:
                pass
        error = (
            exc.as_dict()
            if isinstance(exc, AutomationError)
            else {"code": "SMOKE_FAILED", "message": type(exc).__name__}
        )
        result = Result(
            job_id=job_id,
            status="rolled_back" if restored_ok else "blocked",
            operation="real_editor_smoke",
            error=error,
            evidence=evidence,
            data={
                "mode": "real_editor_only",
                "native_trial_passed": False,
                "outcome": "not_passed",
            },
        )
        if before is not None:
            atomic_json(
                job_path, {"before": before, "plan": plan, "result": result.model_dump(mode="json")}
            )
        return result
    finally:
        if lock.is_locked:
            lock.release()
        await engine.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True)
    parser.add_argument("--execute-on-test-copy", action="store_true", required=True)
    args = parser.parse_args()
    result = asyncio.run(smoke(args.uid))
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.postconditions_passed else 2)


if __name__ == "__main__":
    main()
