"""Bounded native binding recipes with key/property readback and cancellation."""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import math
import re
import uuid
from typing import Literal

from filelock import FileLock
from pydantic import Field

from .engine import Engine
from .models import AutomationError, Result, StrictModel
from .storage import atomic_json, digest, safe_path


class BlinkSide(StrictModel):
    parameter: str = Field(max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    eye_mesh: str = Field(max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    lid_mesh: str = Field(max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class BlinkRecipe(StrictModel):
    kind: Literal["blink"] = "blink"
    sides: list[BlinkSide] = Field(min_length=1, max_length=2)
    closed_eye_opacity: float = Field(default=0, ge=0, le=100)
    closed_eye_draw_order: int | None = Field(default=None, ge=0, le=1000)


async def capture(engine: Engine, uid: str) -> dict:
    structure = await engine.take_snapshot(uid)
    objects = {}
    for oid in structure["normalized"]["parts"]:
        keys = (
            await engine.ws.request("GetParameterKeys", {"ModelUID": uid, "ObjectId": oid})
        ).get("Parameters")
        if not isinstance(keys, list):
            raise AutomationError("BINDINGS_UNKNOWN", "Object bindings are unavailable")
        if math.prod(len(p["KeyValues"]) for p in keys) > 64:
            raise AutomationError(
                "BINDING_LIMIT", "More than 64 forms per object requires a narrower recipe"
            )
        combinations = itertools.product(*(p["KeyValues"] for p in keys)) if keys else [()]
        forms = []
        for values in combinations:
            parameters = [{"Id": p["Id"], "Value": v} for p, v in zip(keys, values)]
            obj = await engine.ws.request(
                "GetObject", {"ModelUID": uid, "Id": oid, "Parameters": parameters}
            )
            if obj.get("Result") is not True:
                raise AutomationError("OBJECT_UNREADABLE", "Object form could not be read", id=oid)
            forms.append({"parameters": parameters, "object": obj})
        objects[oid] = {"keys": keys, "forms": forms}
    return {"structure": structure, "objects": objects}


def plan_blink(before: dict, recipe: BlinkRecipe) -> dict:
    normalized = before["structure"]["normalized"]
    seen, requests, checks, targets = set(), [], [], {}
    for side in recipe.sides:
        param = normalized["parameters"].get(side.parameter)
        if not param or param["Min"] != 0 or param["Max"] != 1 or param["Default"] != 1:
            raise AutomationError(
                "INVALID_BLINK_RANGE", "Blink recipe requires min=0, default=max=1"
            )
        for oid, closed, opened in [
            (side.eye_mesh, recipe.closed_eye_opacity, 100.0),
            (side.lid_mesh, 100.0, 0.0),
        ]:
            if oid in seen or normalized["parts"].get(oid, {}).get("Type") != "ArtMesh":
                raise AutomationError(
                    "INVALID_BLINK_MESH", "Use distinct existing ArtMeshes", id=oid
                )
            seen.add(oid)
            keys = before["objects"][oid]["keys"]
            if any(k["Id"] != side.parameter for k in keys):
                raise AutomationError(
                    "COMPLEX_BINDING",
                    "Blink recipe will not replace other parameter bindings",
                    id=oid,
                )
            existing: list[float] = next(
                (k["KeyValues"] for k in keys if k["Id"] == side.parameter), []
            )
            if any(v not in (0, 1) for v in existing):
                raise AutomationError(
                    "COMPLEX_BINDING", "Existing intermediate blink forms need explicit review"
                )
            for value in [0.0, 1.0]:
                if value not in existing:
                    requests.append(
                        {
                            "method": "AddParameterKey",
                            "data": {
                                "ObjectId": oid,
                                "ParameterId": side.parameter,
                                "KeyValue": value,
                            },
                        }
                    )
                opacity = closed if value == 0 else opened
                parameters = [{"Id": side.parameter, "Value": value}]
                properties = {"Opacity": opacity}
                if oid == side.eye_mesh and recipe.closed_eye_draw_order is not None:
                    opened_form = next(
                        (
                            f
                            for f in before["objects"][oid]["forms"]
                            if f["parameters"] == [{"Id": side.parameter, "Value": 1.0}]
                        ),
                        before["objects"][oid]["forms"][0],
                    )
                    properties["DrawOrder"] = (
                        recipe.closed_eye_draw_order
                        if value == 0
                        else opened_form["object"]["Data"]["DrawOrder"]
                    )
                requests.append(
                    {
                        "method": "EditArtMesh",
                        "data": {
                            "Id": oid,
                            "Parameters": parameters,
                            "IsExactMatch": True,
                            **properties,
                        },
                    }
                )
                checks.append({"Id": oid, "Parameters": parameters, "properties": properties})
            targets[oid] = side.parameter
    desired = copy.deepcopy(normalized)
    for side in recipe.sides:
        desired["parameters"][side.parameter]["KeyValues"] = sorted(
            set(desired["parameters"][side.parameter]["KeyValues"] + [0.0, 1.0])
        )
    return {"requests": requests, "checks": checks, "targets": targets, "desired": desired}


async def verify_blink(engine: Engine, uid: str, before: dict, after: dict, plan: dict):
    if after["structure"]["normalized"] != plan["desired"]:
        raise AutomationError(
            "POSTCONDITION_FAILED", "Binding structure changed outside the recipe"
        )
    for oid, original in before["objects"].items():
        actual = after["objects"][oid]
        if oid not in plan["targets"]:
            if actual != original:
                raise AutomationError("POSTCONDITION_FAILED", "Non-target object changed", id=oid)
            continue
        if actual["keys"] != [{"Id": plan["targets"][oid], "KeyValues": [0.0, 1.0]}]:
            raise AutomationError("POSTCONDITION_FAILED", "Blink object keys differ", id=oid)
        for form in actual["forms"]:
            reference = next(
                (f["object"] for f in original["forms"] if f["parameters"] == form["parameters"]),
                original["forms"][0]["object"],
            )
            a, b = copy.deepcopy(form["object"]), copy.deepcopy(reference)
            allowed = {
                field
                for check in plan["checks"]
                if check["Id"] == oid
                for field in check["properties"]
            }
            for field in allowed:
                a["Data"].pop(field, None)
                b["Data"].pop(field, None)
            if a != b:
                raise AutomationError(
                    "POSTCONDITION_FAILED", "Unexpected non-opacity property change", id=oid
                )
    for check in plan["checks"]:
        obj = await engine.ws.request(
            "GetObject", {"ModelUID": uid, "Id": check["Id"], "Parameters": check["Parameters"]}
        )
        if any(obj.get("Data", {}).get(k) != v for k, v in check["properties"].items()):
            raise AutomationError(
                "POSTCONDITION_FAILED", "Blink key opacity differs", expected=check, actual=obj
            )


async def apply_blink(
    engine: Engine,
    uid: str,
    recipe: BlinkRecipe,
    *,
    confirm: bool = False,
    dry_run: bool = True,
    cancel_trial: bool = False,
    expected_hash: str | None = None,
) -> Result:
    job = "binding-" + uuid.uuid4().hex
    job_path = safe_path(engine.settings.state_dir, f"jobs/{job}/binding.json")
    lock = FileLock(engine.lock_root / f"editor-{engine.settings.port}.lock", timeout=0)
    before, active, committed, evidence = None, False, False, []
    try:
        lock.acquire()
        if engine.marker.exists():
            raise AutomationError(
                "RECOVERY_REQUIRED", "Resolve the previous transaction before binding"
            )
        uid, info = await engine.ready(uid)
        engine.edit_gate(info)
        before = await capture(engine, uid)
        plan = plan_blink(before, recipe)
        current_hash = digest(before)
        if dry_run:
            return Result(
                status="review_required",
                operation="plan_blink",
                model_uid=uid,
                tool_call_succeeded=True,
                data={"snapshot_sha256": current_hash, "plan": plan},
            )
        if not confirm or expected_hash != current_hash:
            raise AutomationError(
                "STALE_PLAN", "Binding requires confirmation and the current full snapshot hash"
            )
        evidence.append(engine.journal.save(f"{job}/before.json", before, "binding_snapshot"))
        evidence.append(await engine.backup(uid, job))
        record = {
            "model_uid": uid,
            "before": before,
            "plan": plan,
            "recipe": recipe.model_dump(),
            "phase": "begin_pending",
        }
        atomic_json(job_path, record)
        atomic_json(
            engine.marker,
            {"job_id": job, "job_path": str(job_path), "model_uid": uid, "phase": "binding"},
        )
        active = True
        engine.ws.edit_cancelled = False
        await engine.write_ok("EditBegin", {"Silent": False})
        for request in plan["requests"]:
            await engine.ready(uid)
            if engine.ws.edit_cancelled:
                raise AutomationError("USER_CANCELLED", "Binding transaction cancelled")
            await engine.write_ok(request["method"], {"ModelUID": uid, **request["data"]})
        changed = await capture(engine, uid)
        await verify_blink(engine, uid, before, changed, plan)
        evidence.append(engine.journal.save(f"{job}/changed.json", changed, "binding_readback"))
        await engine.ready(uid)
        if engine.ws.edit_cancelled:
            raise AutomationError("USER_CANCELLED", "Binding transaction cancelled before commit")
        atomic_json(
            engine.marker,
            {
                "job_id": job,
                "job_path": str(job_path),
                "model_uid": uid,
                "phase": "cancel_pending" if cancel_trial else "commit_pending",
            },
        )
        committed = not cancel_trial
        await engine.write_ok("EditEnd", {"Cancel": cancel_trial})
        active = False
        final = await capture(engine, uid)
        if cancel_trial:
            if final != before:
                raise AutomationError(
                    "ROLLBACK_UNCONFIRMED", "Binding cancel did not restore properties and keys"
                )
        else:
            await verify_blink(engine, uid, before, final, plan)
        evidence.append(
            engine.journal.save(
                f"{job}/final.json",
                final,
                "binding_rollback" if cancel_trial else "binding_readback",
            )
        )
        result = Result(
            job_id=job,
            status="rolled_back" if cancel_trial else "verified",
            operation="blink_binding",
            editor_version=(engine.editor or {}).get("version"),
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            evidence=evidence,
            data={
                "cancel_trial": cancel_trial,
                "saved_to_disk": False,
                "vertices_authored": False,
                "snapshot_sha256": digest(final),
            },
        )
        atomic_json(
            job_path, {**record, "phase": "complete", "result": result.model_dump(mode="json")}
        )
        engine.marker.unlink()
        return result
    except BaseException as exc:
        restored = False
        if active and not committed and before:
            try:

                async def cancel_and_read():
                    await engine.ready(uid)
                    await engine.write_ok("EditEnd", {"Cancel": True})
                    return (await capture(engine, uid)) == before

                restored = await asyncio.shield(cancel_and_read())
                if restored:
                    engine.marker.unlink()
            except Exception:
                pass
        error = (
            exc.as_dict()
            if isinstance(exc, AutomationError)
            else {"code": "BINDING_FAILED", "message": str(exc)}
        )
        result = Result(
            job_id=job,
            status="rolled_back" if restored else "blocked",
            operation="blink_binding",
            error=error,
            evidence=evidence,
            data={"restored": restored},
        )
        if before:
            atomic_json(
                job_path,
                {
                    "model_uid": uid,
                    "before": before,
                    "plan": locals().get("plan"),
                    "result": result.model_dump(mode="json"),
                },
            )
        if not isinstance(exc, Exception):
            raise
        return result
    finally:
        if lock.is_locked:
            lock.release()


async def load_job(engine: Engine, job_id: str) -> Result:
    if not re.fullmatch(r"binding-[a-f0-9]{32}", job_id):
        raise AutomationError("INVALID_JOB", "Expected a binding job ID")
    job = json.loads(
        safe_path(engine.settings.state_dir, f"jobs/{job_id}/binding.json").read_text(
            encoding="utf-8"
        )
    )
    uid, _ = await engine.ready(job["model_uid"])
    after = await capture(engine, uid)
    if job.get("result", {}).get("data", {}).get("cancel_trial"):
        if after != job["before"]:
            raise AutomationError("POSTCONDITION_FAILED", "Cancelled binding is not restored")
    else:
        await verify_blink(engine, uid, job["before"], after, job["plan"])
    return Result(
        job_id=job_id,
        status="verified",
        operation="verify_binding",
        model_uid=uid,
        tool_call_succeeded=True,
        postconditions_passed=True,
    )
