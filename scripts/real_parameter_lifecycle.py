"""Opt-in real create/commit/reconnect/update/move/delete test on a disposable model."""

import argparse
import asyncio
import json
import time
import uuid

from live2d_automation.cli import settings_from_env
from live2d_automation.engine import Engine
from live2d_automation.models import AutomationError


async def lifecycle(uid):
    settings = settings_from_env()
    suffix = uuid.uuid4().hex[:8]
    group, parameter = "TestLifecycleGroup_" + suffix, "TestLifecycleParam_" + suffix
    results = []
    started = time.perf_counter()

    async def snapshot():
        engine = Engine(settings)
        try:
            result = await engine.run("snapshot", expected_uid=uid)
            if not result.postconditions_passed:
                raise AutomationError("SNAPSHOT_FAILED", str(result.error))
            return result
        finally:
            await engine.close()

    async def apply(operations):
        # Every stage starts a fresh authorized connection and uses Engine's
        # backup, UID/hash checks, shared lock, transaction and readback path.
        engine = Engine(settings)
        try:
            before = await snapshot()
            result = await engine.run(
                "apply",
                expected_uid=uid,
                operations=operations,
                idempotency_key="lifecycle-" + uuid.uuid4().hex,
                expected_snapshot_sha256=before.data["snapshot_sha256"],
                dry_run=False,
                confirm=True,
            )
            results.append(result.model_dump(mode="json"))
            if not result.postconditions_passed:
                raise AutomationError("LIFECYCLE_STAGE_FAILED", str(result.error))
            verification = await engine.run("verify", job_id=result.job_id)
            results.append(verification.model_dump(mode="json"))
            if not verification.postconditions_passed:
                raise AutomationError("LIFECYCLE_VERIFY_FAILED", str(verification.error))
        finally:
            await engine.close()

    before = await snapshot()
    error = None
    try:
        await apply(
            [
                {"kind": "create_group", "id": group},
                {
                    "kind": "create_parameter",
                    "id": parameter,
                    "group_id": group,
                    "min": 0,
                    "default": 0,
                    "max": 1,
                },
            ]
        )
        await apply(
            [
                {"kind": "update_group", "id": group, "name": "Lifecycle modified group"},
                {
                    "kind": "update_parameter",
                    "id": parameter,
                    "name": "Lifecycle modified parameter",
                    "min": -1,
                    "default": 0,
                    "max": 2,
                },
                {"kind": "move_parameter", "id": parameter, "group_id": ""},
            ]
        )
    except Exception as exc:
        error = str(exc)
    finally:
        current = await snapshot()
        normalized = current.data["snapshot"]["normalized"]
        cleanup = []
        if parameter in normalized["parameters"]:
            cleanup.append({"kind": "delete_parameter", "id": parameter})
        if group in normalized["groups"]:
            cleanup.append({"kind": "delete_group", "id": group})
        if cleanup:
            try:
                await apply(cleanup)
            except Exception as exc:
                error = str(exc)
    restored = await snapshot()
    return {
        "real_editor": True,
        "computer_use_calls": 0,
        "model_uid": uid,
        "editor_version": before.data["snapshot"]["editor_version"],
        "api_version": before.data["snapshot"]["api_version"],
        "passed": error is None
        and restored.data["snapshot_sha256"] == before.data["snapshot_sha256"],
        "restored": restored.data["snapshot_sha256"] == before.data["snapshot_sha256"],
        "before_sha256": before.data["snapshot_sha256"],
        "after_sha256": restored.data["snapshot_sha256"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "operations": [
            "create_group",
            "create_parameter",
            "update_group",
            "update_parameter",
            "move_parameter",
            "delete_parameter",
            "delete_group",
        ],
        "stages": results,
        "error": error,
        "native_file_saved": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True)
    parser.add_argument("--execute-on-test-copy", required=True, action="store_true")
    args = parser.parse_args()
    result = asyncio.run(lifecycle(args.uid))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 2)


if __name__ == "__main__":
    main()
