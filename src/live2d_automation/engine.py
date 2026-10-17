from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from platformdirs import user_data_path
from pydantic import ValidationError

from .capabilities import registry, route
from .environment import environment_report, listener_info
from .models import AutomationError, Evidence, Operation, Result, Settings, Status
from .storage import Journal, atomic_json, digest, safe_path
from .structure import check_desired, normalize, plan_operations
from .transport import CubismWS


def adapter_date(now: datetime | None = None) -> date:
    """Use the vendor's JST expiry date, independent of the host timezone."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=9))).date()


class Engine:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.journal = Journal(settings.state_dir / "evidence")
        self.ws = transport or CubismWS(settings, self.journal)
        self.session_lock = asyncio.Lock()
        self.lock_root = user_data_path("live2d-automation") / "locks"
        self.lock_root.mkdir(parents=True, exist_ok=True)
        self.marker = self.lock_root / f"editor-{settings.port}.recovery.json"
        self.editor: dict[str, Any] | None = None
        self.today = adapter_date
        self.listener_probe = listener_info

    async def run(self, method: str, **kwargs) -> Result:
        # Serializes read + write tool calls within a long-lived MCP session too.
        async with self.session_lock:
            try:
                if method in {
                    "blink",
                    "editor_file",
                    "generate_animation",
                    "verify_runtime",
                    "snapshot_bindings",
                }:
                    from .workflows import dispatch

                    return await dispatch(self, method, **kwargs)
                handler = getattr(self, method, None)
                if (
                    method
                    not in {
                        "doctor",
                        "connect",
                        "list_documents",
                        "snapshot",
                        "inspect",
                        "plan",
                        "apply",
                        "verify",
                        "rollback",
                    }
                    or handler is None
                ):
                    raise AutomationError("UNKNOWN_OPERATION", "Unknown automation operation")
                return await handler(**kwargs)
            except (AutomationError, ValidationError, OSError, ValueError, Timeout) as exc:
                error = (
                    exc.as_dict()
                    if isinstance(exc, AutomationError)
                    else {
                        "code": "EDITOR_BUSY"
                        if isinstance(exc, Timeout)
                        else "INVALID_INPUT"
                        if isinstance(exc, (ValidationError, ValueError))
                        else "FILE_ERROR",
                        "message": str(exc),
                    }
                )
                status: Status = (
                    "failed"
                    if error["code"] in {"POSTCONDITION_FAILED", "INVALID_STRUCTURE"}
                    else "blocked"
                )
                return Result(
                    status=status, operation=method, error=error, next_action="resolve_precondition"
                )

    async def close(self):
        await self.ws.close()

    async def doctor(self):
        report = environment_report(self.settings.port)
        report["pending_recovery"] = self.marker.exists()
        try:
            connection = await self.connect()
            report["connection"] = connection.model_dump(mode="json")
            return Result(
                status=connection.status,
                operation="doctor",
                editor_version=connection.editor_version,
                tool_call_succeeded=connection.tool_call_succeeded,
                postconditions_passed=connection.postconditions_passed,
                evidence=connection.evidence,
                data=report,
                error=connection.error,
                next_action=connection.next_action,
            )
        except AutomationError as exc:
            report["connection"] = {"connected": False}
            evidence = self.journal.save(
                f"doctor-{uuid.uuid4().hex}.json", report, "environment_probe"
            )
            return Result(
                status="blocked",
                operation="doctor",
                evidence=[evidence],
                data=report,
                error=exc.as_dict(),
                next_action="start_editor_and_enable_external_api",
            )

    async def connect(self):
        route("editor.connect")
        await self.ws.open()
        self.editor = self.listener_probe(self.settings.port)
        approval = await self.ws.request("GetIsApproval", {})
        if approval.get("Result") is True:
            await self.ws.negotiate()
        data: dict[str, Any] = {
            "connected": True,
            "api_version": self.ws.version if approval.get("Result") is True else None,
            "editor_process": self.editor,
            "approved": approval.get("Result") is True,
            "edit_approved": False,
            "mode": None,
        }
        if data["approved"]:
            data["mode"] = (await self.ws.request("GetCurrentEditMode", {})).get("EditMode")
            try:
                data["edit_approved"] = (await self.ws.request("GetIsEditApproval", {})).get(
                    "Result"
                ) is True
            except AutomationError as exc:
                if exc.details.get("editor_error") not in {"MethodNotFound", "UnsupportedVersion"}:
                    raise
                data["edit_api_error"] = exc.as_dict()
        evidence = self.journal.save(f"connection-{uuid.uuid4().hex}.json", data, "editor_probe")
        return Result(
            status="verified" if data["approved"] else "blocked",
            operation="connect",
            editor_version=self.editor.get("version"),
            tool_call_succeeded=True,
            postconditions_passed=data["approved"],
            data=data,
            evidence=[evidence],
            error=None
            if data["approved"]
            else {
                "code": "READ_PERMISSION_DENIED",
                "message": "Grant Allow to live2d-automation in Cubism external integration settings",
            },
            warnings=["Connection verification is separate from native-edit or visual acceptance"],
            next_action=None if data["approved"] else "grant_allow_in_editor",
        )

    async def ready(self, expected_uid: str | None = None):
        connection = await self.connect()
        if connection.status == "blocked":
            raise AutomationError("READ_PERMISSION_DENIED", "Cubism Allow permission required")
        uid = (await self.ws.request("GetCurrentModelUID", {})).get("ModelUID")
        if not isinstance(uid, str) or not uid:
            raise AutomationError("NO_MODEL", "Open a model in Cubism")
        if expected_uid is not None and uid != expected_uid:
            raise AutomationError(
                "MODEL_UID_CHANGED",
                "Active model differs from the selected target",
                expected=expected_uid,
                actual=uid,
            )
        return uid, connection.data

    async def list_documents(self):
        await self.ready()
        docs = await self.ws.request("GetDocuments", {})
        return Result(
            status="verified",
            operation="list_documents",
            tool_call_succeeded=True,
            postconditions_passed=True,
            data=docs,
        )

    async def take_snapshot(self, uid: str):
        route("model.inspect")
        if (await self.ws.request("GetCurrentModelUID", {})).get("ModelUID") != uid:
            raise AutomationError("MODEL_UID_CHANGED", "Model changed before snapshot")
        parameter = await self.ws.request("GetParameterStructure", {"ModelUID": uid})
        parts = await self.ws.request("GetPartStructure", {"ModelUID": uid})
        deformers = await self.ws.request("GetDeformerStructure", {"ModelUID": uid})
        required = [
            (parameter, "ParameterStructure"),
            (parts, "PartStructure"),
            (deformers, "DeformerStructure"),
        ]
        if any(
            not isinstance(item.get(key), (dict, list) if key == "DeformerStructure" else dict)
            for item, key in required
        ):
            raise AutomationError(
                "INVALID_STRUCTURE", "Editor returned incomplete structural trees"
            )
        normalized = normalize(*(item[key] for item, key in required))
        if any("KeyValues" not in p for p in normalized["parameters"].values()):
            # The shipped Alpha2 omits global KeyValues despite the manual.
            # Read every object's bindings; absence is never assumed to mean unbound.
            keys: dict[str, set[float]] = {p: set() for p in normalized["parameters"]}
            for object_id in normalized["parts"]:
                bindings = await self.ws.request(
                    "GetParameterKeys", {"ModelUID": uid, "ObjectId": object_id}
                )
                entries = bindings.get("Parameters")
                if not isinstance(entries, list):
                    raise AutomationError("INVALID_STRUCTURE", "Missing object parameter bindings")
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("Id") not in keys:
                        raise AutomationError("INVALID_STRUCTURE", "Unknown bound parameter")
                    values = entry.get("KeyValues")
                    if not isinstance(values, list) or any(
                        not isinstance(v, (int, float)) or isinstance(v, bool) for v in values
                    ):
                        raise AutomationError("INVALID_STRUCTURE", "Invalid object parameter keys")
                    keys[entry["Id"]].update(values)
            for parameter_id, values in keys.items():
                param = normalized["parameters"][parameter_id]
                if any(
                    not math.isfinite(v) or not param["Min"] <= v <= param["Max"] for v in values
                ):
                    raise AutomationError("INVALID_STRUCTURE", "Out-of-range object parameter keys")
                normalized["parameters"][parameter_id]["KeyValues"] = sorted(values)
        if (await self.ws.request("GetCurrentModelUID", {})).get("ModelUID") != uid:
            raise AutomationError("MODEL_UID_CHANGED", "Model changed during snapshot")
        return {
            "model_uid": uid,
            "editor_version": (self.editor or {}).get("version"),
            "api_version": self.ws.version,
            "normalized": normalized,
            "raw": {key: item[key] for item, key in required},
        }

    async def snapshot(self, expected_uid: str | None = None):
        uid, _ = await self.ready(expected_uid)
        snap = await self.take_snapshot(uid)
        evidence = self.journal.save(f"snapshot-{uuid.uuid4().hex}.json", snap)
        return Result(
            status="verified",
            operation="snapshot",
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            evidence=[evidence],
            data={
                "snapshot": snap,
                "snapshot_sha256": digest(snap["normalized"]),
                "scope": "structure_only",
            },
        )

    async def inspect(self, expected_uid: str | None = None):
        uid, info = await self.ready(expected_uid)
        try:
            snap = await self.take_snapshot(uid)
        except AutomationError as exc:
            if exc.details.get("editor_error") not in {"MethodNotFound", "UnsupportedVersion"}:
                raise
            values = await self.ws.request("GetParameterValues", {"ModelUID": uid})
            return Result(
                status="review_required",
                operation="inspect",
                editor_version=self.editor.get("version") if self.editor else None,
                model_uid=uid,
                tool_call_succeeded=True,
                data={"parameter_values": values, "connection": info, "complete_structure": False},
                warnings=[
                    "Base API exposes current values only; no complete editable structure was obtained"
                ],
                next_action="use_compatible_edit_api",
            )
        evidence = self.journal.save(f"inspect-{uuid.uuid4().hex}.json", snap)
        return Result(
            status="verified",
            operation="inspect",
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            evidence=[evidence],
            data={
                "counts": {k: len(v) for k, v in snap["normalized"].items()},
                "normalized": snap["normalized"],
                "snapshot_sha256": digest(snap["normalized"]),
                "complete_structure": True,
            },
            warnings=["Mesh geometry, keyform bindings and visual quality were not verified"],
        )

    @staticmethod
    def parse_ops(operations):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
            raise AutomationError("INVALID_INPUT", "Provide 1 to 100 operations")
        return [
            op if isinstance(op, Operation) else Operation.model_validate(op) for op in operations
        ]

    async def plan(self, expected_uid: str, operations: list[dict]):
        uid, _ = await self.ready(expected_uid)
        before = await self.take_snapshot(uid)
        plan = plan_operations(before, self.parse_ops(operations))
        plan.update(
            model_uid=uid,
            snapshot_sha256=digest(before["normalized"]),
            scope="structure_only",
            dry_run=True,
        )
        evidence = self.journal.save(f"plan-{uuid.uuid4().hex}.json", plan, "change_plan")
        return Result(
            status="review_required" if plan["requests"] else "verified",
            operation="plan",
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=not plan["requests"],
            evidence=[evidence],
            data=plan,
            next_action="apply_with_snapshot_hash" if plan["requests"] else None,
        )

    def edit_gate(self, info: dict):
        profile = registry()["adapter"]
        route("parameter.edit")
        version = (self.editor or {}).get("version", "") or ""
        if not (self.editor or {}).get("cubism_process"):
            raise AutomationError(
                "EDITOR_VERSION_UNVERIFIED",
                "Cannot bind API port to a real Cubism install; writes disabled",
            )
        if not re.fullmatch(profile["editor_version_pattern"], version, re.I):
            raise AutomationError(
                "UNSUPPORTED_EDITOR_VERSION",
                "Only locally identified Cubism 5.4 alpha2 is eligible for this adapter",
                actual=version,
            )
        if self.today() > date.fromisoformat(profile["available_through"]):
            raise AutomationError(
                "EDITOR_ALPHA_EXPIRED", "5.4 alpha2 adapter expired; file tools remain available"
            )
        if self.ws.version != profile["api_version"]:
            raise AutomationError(
                "UNSUPPORTED_API_VERSION",
                "Edit adapter is pinned to API 1.1.0",
                actual=self.ws.version,
            )
        if not info.get("edit_approved"):
            raise AutomationError("EDIT_PERMISSION_DENIED", "Grant Edit permission in Cubism")
        if info.get("mode") != "Modeling":
            raise AutomationError("INVALID_EDITOR_MODE", "Switch Cubism to Modeling")

    async def backup(self, uid: str, job_id: str):
        docs = await self.ws.request("GetDocuments", {})
        matches = [
            doc
            for doc in docs.get("ModelingDocuments", [])
            if any(v.get("ModelUID") == uid for v in doc.get("Views", []))
        ]
        if len(matches) != 1:
            raise AutomationError(
                "BACKUP_UNAVAILABLE", "Cannot identify one native document for this UID"
            )
        source = Path(matches[0].get("DocumentFilePath", ""))
        if source.suffix.lower() != ".cmo3" or not source.is_file() or source.stat().st_size == 0:
            raise AutomationError(
                "BACKUP_UNAVAILABLE", "Save a nonempty .cmo3 test-project copy before editing"
            )
        target = safe_path(self.settings.state_dir, f"jobs/{job_id}/original.cmo3")
        target.parent.mkdir(parents=True, exist_ok=True)
        before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copy2(source, target)
        after_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if (
            before_hash != after_hash
            or before_hash != hashlib.sha256(source.read_bytes()).hexdigest()
        ):
            raise AutomationError("BACKUP_CHANGED", "Native source changed while being backed up")
        return Evidence(kind="last_saved_native_backup", path=str(target), sha256=after_hash)

    async def write_ok(self, method, data):
        result = await self.ws.request(method, data)
        if result.get("Result") is not True:
            raise AutomationError(
                "EDITOR_REJECTED_WRITE", "Editor write returned no true Result", method=method
            )
        return result

    async def verify_objects(self, uid, plan):
        for check in plan["object_checks"]:
            got = await self.ws.request("GetObject", {"ModelUID": uid, "Id": check["Id"]})
            properties = dict(got.get("Data", {}))
            if properties.get("ParentId") == "%Root":
                properties["ParentId"] = ""
            if (
                got.get("Result") is not True
                or got.get("Type") != check["Type"]
                or any(properties.get(k) != v for k, v in check["fields"].items())
            ):
                raise AutomationError(
                    "POSTCONDITION_FAILED",
                    "Object properties do not match the plan",
                    id=check["Id"],
                    expected=check,
                    actual=got,
                )

    async def apply(
        self,
        expected_uid: str,
        operations: list[dict],
        idempotency_key: str,
        dry_run: bool = True,
        confirm: bool = False,
        expected_snapshot_sha256: str | None = None,
    ):
        ops = self.parse_ops(operations)
        if dry_run:
            return await self.plan(expected_uid, operations)
        if not confirm or not expected_snapshot_sha256:
            raise AutomationError(
                "APPLY_NOT_CONFIRMED",
                "Real apply requires confirm=true and the reviewed snapshot_sha256",
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", idempotency_key):
            raise AutomationError("INVALID_INPUT", "Use a 1-80 character idempotency key")
        fingerprint = digest({"uid": expected_uid, "ops": [op.model_dump() for op in ops]})
        job_id = "job-" + idempotency_key
        job_path = safe_path(self.settings.state_dir, f"jobs/{job_id}/job.json")
        lock = FileLock(self.lock_root / f"editor-{self.settings.port}.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as exc:
            raise AutomationError(
                "EDITOR_BUSY", "Another automation process holds the Editor write lock"
            ) from exc
        evidence, before, active, commit_sent = [], None, False, False
        try:
            if job_path.is_file():
                old = json.loads(job_path.read_text(encoding="utf-8"))
                if old.get("fingerprint") != fingerprint:
                    raise AutomationError(
                        "IDEMPOTENCY_CONFLICT",
                        "Idempotency key already belongs to different operations",
                    )
                if "result" in old:
                    # Return historical evidence, not an assertion about current Editor state.
                    result = Result.model_validate(old["result"])
                    result.data["historical_replay"] = True
                    result.warnings.append(
                        "Recorded job result; use verify to check current model state"
                    )
                    return result
                raise AutomationError(
                    "RECOVERY_REQUIRED", "Previous attempt has no confirmed terminal result"
                )
            if self.marker.exists():
                raise AutomationError(
                    "RECOVERY_REQUIRED",
                    "Uncertain prior Editor transaction blocks new writes",
                    report=str(self.marker),
                )
            uid, info = await self.ready(expected_uid)
            self.edit_gate(info)
            before = await self.take_snapshot(uid)
            if digest(before["normalized"]) != expected_snapshot_sha256:
                raise AutomationError(
                    "STALE_PLAN", "Model changed since the reviewed plan; plan again"
                )
            plan = plan_operations(before, ops)
            if plan["object_checks"]:
                route("structure.create")
            evidence.append(self.journal.save(f"{job_id}/before.json", before))
            evidence.append(self.journal.save(f"{job_id}/plan.json", plan, "change_plan"))
            if not plan["requests"]:
                result = Result(
                    job_id=job_id,
                    status="verified",
                    operation="apply",
                    model_uid=uid,
                    tool_call_succeeded=True,
                    postconditions_passed=True,
                    evidence=evidence,
                    data={"outcome": "no_change", "scope": "structure_only"},
                )
                atomic_json(
                    job_path,
                    {
                        "fingerprint": fingerprint,
                        "result": result.model_dump(mode="json"),
                        "before": before,
                        "plan": plan,
                    },
                )
                return result
            evidence.append(await self.backup(uid, job_id))
            record = {
                "fingerprint": fingerprint,
                "before": before,
                "plan": plan,
                "model_uid": uid,
                "phase": "begin_pending",
            }
            atomic_json(job_path, record)
            atomic_json(
                self.marker,
                {
                    "job_id": job_id,
                    "job_path": str(job_path),
                    "model_uid": uid,
                    "phase": "begin_pending",
                },
            )
            active = True  # Even a timed-out EditBegin can have acquired the native lock.
            self.ws.edit_cancelled = False
            await self.write_ok("EditBegin", {"Silent": False})
            for request in plan["requests"]:
                await self.ready(expected_uid)
                if self.ws.edit_cancelled:
                    raise AutomationError(
                        "USER_CANCELLED", "User cancelled the Editor edit transaction"
                    )
                await self.write_ok(request["method"], {"ModelUID": uid, **request["data"]})
            after = await self.take_snapshot(uid)
            check_desired(before, after, plan)
            await self.verify_objects(uid, plan)
            evidence.append(self.journal.save(f"{job_id}/before-commit.json", after))
            if self.ws.edit_cancelled:
                raise AutomationError("USER_CANCELLED", "User cancelled before commit")
            await self.ready(expected_uid)
            atomic_json(
                self.marker,
                {
                    "job_id": job_id,
                    "job_path": str(job_path),
                    "model_uid": uid,
                    "phase": "commit_pending",
                },
            )
            commit_sent = True
            await self.write_ok("EditEnd", {"Cancel": False})
            active = False
            final = await self.take_snapshot(uid)
            check_desired(before, final, plan)
            await self.verify_objects(uid, plan)
            evidence.append(self.journal.save(f"{job_id}/after.json", final))
            result = Result(
                job_id=job_id,
                status="review_required" if plan["object_checks"] else "verified",
                operation="apply",
                editor_version=(self.editor or {}).get("version"),
                model_uid=uid,
                tool_call_succeeded=True,
                postconditions_passed=True,
                evidence=evidence,
                data={
                    "scope": "structure_only",
                    "saved_to_disk": False,
                    "snapshot_sha256": digest(final["normalized"]),
                },
                warnings=[
                    "Changes are in Editor memory; native project save/reopen and visual verification remain separate",
                    "Backup contains last saved native project; structural snapshot covers API edits, not all unsaved artwork",
                ],
                next_action="visual_review_and_save_copy",
            )
            atomic_json(
                job_path, {**record, "phase": "complete", "result": result.model_dump(mode="json")}
            )
            self.marker.unlink()
            return result
        except BaseException as exc:
            error = (
                exc.as_dict()
                if isinstance(exc, AutomationError)
                else {
                    "code": "INTERRUPTED"
                    if isinstance(exc, asyncio.CancelledError)
                    else "EXECUTION_FAILED",
                    "message": type(exc).__name__,
                }
            )
            status: Status = "blocked"
            if active and not commit_sent and before is not None:
                try:
                    # Shield cancellation cleanup; never reconnect and replay the transaction.
                    async def cancel_and_verify():
                        await self.write_ok("EditEnd", {"Cancel": True})
                        restored = await self.take_snapshot(before["model_uid"])
                        if restored["normalized"] != before["normalized"]:
                            raise AutomationError(
                                "ROLLBACK_UNCONFIRMED", "Rollback readback differs from original"
                            )
                        return restored

                    restored = await asyncio.shield(cancel_and_verify())
                    evidence.append(
                        self.journal.save(f"{job_id}/rollback.json", restored, "rollback_readback")
                    )
                    status = "rolled_back"
                except BaseException as rollback_error:
                    error["rollback_error"] = type(rollback_error).__name__
            if not active and not commit_sent:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if isinstance(exc, AutomationError):
                    raise
            result = Result(
                job_id=job_id,
                status=status,
                operation="apply",
                model_uid=expected_uid,
                error=error,
                evidence=evidence,
                next_action="recover_saved_test_copy" if status == "blocked" else "inspect_failure",
            )
            if before is not None and self.marker.exists():
                atomic_json(
                    job_path,
                    {
                        "fingerprint": fingerprint,
                        "before": before,
                        "plan": locals().get("plan"),
                        "result": result.model_dump(mode="json"),
                    },
                )
                self.journal.save(
                    f"{job_id}/recovery_report.json",
                    result.model_dump(mode="json"),
                    "recovery_report",
                )
                if status == "rolled_back":
                    self.marker.unlink()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return result
        finally:
            lock.release()

    async def verify(self, job_id: str):
        if job_id.startswith("binding-"):
            from .bindings import load_job

            return await load_job(self, job_id)
        record = self.load_job(job_id)
        uid, _ = await self.ready(record["before"]["model_uid"])
        current = await self.take_snapshot(uid)
        check_desired(record["before"], current, record["plan"])
        await self.verify_objects(uid, record["plan"])
        evidence = self.journal.save(f"{job_id}/verify-{uuid.uuid4().hex}.json", current)
        return Result(
            job_id=job_id,
            status="verified",
            operation="verify",
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            evidence=[evidence],
            data={"scope": "structure_only"},
            warnings=["Current structure verified; geometry and saved project were not verified"],
        )

    def load_job(self, job_id: str):
        if not re.fullmatch(r"job-[A-Za-z0-9_-]{1,80}", job_id):
            raise AutomationError("INVALID_INPUT", "Invalid job ID")
        return json.loads(
            safe_path(self.settings.state_dir, f"jobs/{job_id}/job.json").read_text(
                encoding="utf-8"
            )
        )

    async def rollback(self, job_id: str, expected_uid: str | None = None):
        if job_id.startswith(("binding-", "ui-")):
            from .recovery import recover

            return await recover(self, job_id, expected_uid)
        # No guessed inverse edits or overwriting a native binary after commit.
        record = self.load_job(job_id)
        lock = FileLock(self.lock_root / f"editor-{self.settings.port}.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as exc:
            raise AutomationError("EDITOR_BUSY", "Another writer is active") from exc
        try:
            original_uid = record["before"]["model_uid"]
            uid, _ = await self.ready(expected_uid or original_uid)
            if uid != original_uid:
                docs = await self.ws.request("GetDocuments", {})
                paths = [
                    Path(doc.get("DocumentFilePath", ""))
                    for doc in docs.get("ModelingDocuments", [])
                    if any(v.get("ModelUID") == uid for v in doc.get("Views", []))
                ]
                backup_path = safe_path(self.settings.state_dir, f"jobs/{job_id}/original.cmo3")
                if (
                    len(paths) != 1
                    or not paths[0].is_file()
                    or not backup_path.is_file()
                    or hashlib.sha256(paths[0].read_bytes()).hexdigest()
                    != hashlib.sha256(backup_path.read_bytes()).hexdigest()
                ):
                    raise AutomationError(
                        "RESTORE_IDENTITY_UNCONFIRMED",
                        "New UID must identify the explicitly reopened native backup",
                    )
            current = await self.take_snapshot(uid)
            if current["normalized"] != record["before"]["normalized"]:
                return Result(
                    job_id=job_id,
                    status="blocked",
                    operation="rollback",
                    model_uid=uid,
                    error={
                        "code": "MANUAL_RESTORE_REQUIRED",
                        "message": "Committed/uncertain changes remain. Reopen the recorded original.cmo3 test backup, then inspect. No inverse edits were attempted.",
                    },
                    next_action="reopen_native_backup",
                )
            if self.marker.is_file():
                marker = json.loads(self.marker.read_text(encoding="utf-8"))
                if marker.get("job_id") == job_id:
                    self.marker.unlink()
            evidence = self.journal.save(
                f"{job_id}/recovery-confirmed.json", current, "rollback_readback"
            )
            return Result(
                job_id=job_id,
                status="rolled_back",
                operation="rollback",
                model_uid=uid,
                tool_call_succeeded=True,
                postconditions_passed=True,
                evidence=[evidence],
                data={"outcome": "original_structure_already_restored"},
            )
        finally:
            lock.release()
