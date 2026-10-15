"""Generate bounded Cubism motion/expression JSON against a real model snapshot."""

from __future__ import annotations

import copy
import uuid
from typing import Literal

from filelock import FileLock
from pydantic import Field, model_validator

from .engine import Engine
from .files import load_json, validate_bundle
from .models import AutomationError, Result, StrictModel
from .storage import atomic_json, digest, safe_path


class Point(StrictModel):
    time: float = Field(ge=0, le=3600)
    value: float


class Curve(StrictModel):
    id: str = Field(max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    points: list[Point] = Field(min_length=2, max_length=256)


class MotionSpec(StrictModel):
    kind: Literal["motion"] = "motion"
    name: str = Field(min_length=1, max_length=128)
    duration: float = Field(gt=0, le=3600)
    fps: float = Field(default=30, gt=0, le=240)
    loop: bool = False
    fade_in: float = Field(default=0, ge=0, le=60)
    fade_out: float = Field(default=0, ge=0, le=60)
    curves: list[Curve] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_curves(self):
        if len({c.id for c in self.curves}) != len(self.curves):
            raise ValueError("Duplicate motion parameter")
        if sum(len(c.points) for c in self.curves) > 4096:
            raise ValueError("Motion exceeds 4096 points")
        for curve in self.curves:
            times = [p.time for p in curve.points]
            if (
                times[0] != 0
                or times[-1] != self.duration
                or any(b <= a for a, b in zip(times, times[1:]))
            ):
                raise ValueError("Each curve needs strictly increasing times from 0 to duration")
        return self


class ExpressionParameter(StrictModel):
    id: str = Field(max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: float
    blend: Literal["Add", "Multiply", "Overwrite"] = "Overwrite"


class ExpressionSpec(StrictModel):
    kind: Literal["expression"] = "expression"
    name: str = Field(min_length=1, max_length=128)
    fade_in: float = Field(default=0, ge=0, le=60)
    fade_out: float = Field(default=0, ge=0, le=60)
    parameters: list[ExpressionParameter] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_parameters(self):
        if len({p.id for p in self.parameters}) != len(self.parameters):
            raise ValueError("Duplicate expression parameter")
        return self


AnimationSpec = MotionSpec | ExpressionSpec


def compile_animation(spec: AnimationSpec, parameters: dict) -> dict:
    def check(pid, value):
        if pid not in parameters:
            raise AutomationError("UNKNOWN_PARAMETER", "Parameter is absent from the model", id=pid)
        p = parameters[pid]
        if not p["Min"] <= value <= p["Max"]:
            raise AutomationError("OUT_OF_RANGE", "Animation value exceeds model range", id=pid)

    if isinstance(spec, MotionSpec):
        curves, segments, points = [], 0, 0
        for curve in spec.curves:
            for point in curve.points:
                check(curve.id, point.value)
            values: list[float | int] = [curve.points[0].time, curve.points[0].value]
            for point in curve.points[1:]:
                values += [0, point.time, point.value]  # Official linear segment tag.
            segments += len(curve.points) - 1
            points += len(curve.points)
            curves.append({"Target": "Parameter", "Id": curve.id, "Segments": values})
        return {
            "Version": 3,
            "Meta": {
                "Duration": spec.duration,
                "Fps": spec.fps,
                "Loop": spec.loop,
                "AreBeziersRestricted": True,
                "CurveCount": len(curves),
                "TotalSegmentCount": segments,
                "TotalPointCount": points,
                "UserDataCount": 0,
                "TotalUserDataSize": 0,
                "FadeInTime": spec.fade_in,
                "FadeOutTime": spec.fade_out,
            },
            "Curves": curves,
            "UserData": [],
        }
    result = []
    for p in spec.parameters:
        if p.id not in parameters:
            raise AutomationError(
                "UNKNOWN_PARAMETER", "Parameter is absent from the model", id=p.id
            )
        default = parameters[p.id]["Default"]
        check(
            p.id,
            p.value
            if p.blend == "Overwrite"
            else default + p.value
            if p.blend == "Add"
            else default * p.value,
        )
        result.append({"Id": p.id, "Value": p.value, "Blend": p.blend})
    return {
        "Type": "Live2D Expression",
        "FadeInTime": spec.fade_in,
        "FadeOutTime": spec.fade_out,
        "Parameters": result,
    }


async def generate(
    engine: Engine,
    expected_uid: str,
    spec: AnimationSpec,
    relative_path: str,
    model_relative_path: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
    expected_hash: str | None = None,
) -> Result:
    job_id = "file-" + uuid.uuid4().hex
    with FileLock(engine.lock_root / f"editor-{engine.settings.port}.lock", timeout=0):
        uid, _ = await engine.ready(expected_uid)
        snapshot = await engine.take_snapshot(uid)
        data = compile_animation(spec, snapshot["normalized"]["parameters"])
        output = safe_path(engine.settings.output_dir, relative_path)
        extension = ".motion3.json" if spec.kind == "motion" else ".exp3.json"
        if not output.name.endswith(extension) or output.exists():
            raise AutomationError("INVALID_OUTPUT", "Use a new output ending in " + extension)
        model, updated, model_path = None, None, None
        if model_relative_path:
            model_path = safe_path(engine.settings.output_dir, model_relative_path)
            if not model_path.name.endswith(".model3.json") or not output.is_relative_to(
                model_path.parent
            ):
                raise AutomationError(
                    "UNSAFE_PATH", "Animation must be inside the model3 directory"
                )
            checked = validate_bundle(str(model_path))
            if not checked.data.get("resource_checks_passed"):
                raise AutomationError("INVALID_BUNDLE", "Fix exported bundle resources first")
            model = load_json(model_path)
            updated = copy.deepcopy(model)
            refs = updated["FileReferences"]
            relative = output.relative_to(model_path.parent).as_posix()
            if spec.kind == "motion":
                motions = refs.setdefault("Motions", {})
                if spec.name in motions:
                    raise AutomationError("NAME_EXISTS", "Motion group already exists")
                motions[spec.name] = [{"File": relative}]
            else:
                expressions = refs.setdefault("Expressions", [])
                if any(e["Name"] == spec.name for e in expressions):
                    raise AutomationError("NAME_EXISTS", "Expression name already exists")
                expressions.append({"Name": spec.name, "File": relative})
        fingerprint = digest(
            {
                "model_uid": uid,
                "parameters": snapshot["normalized"]["parameters"],
                "spec": spec.model_dump(),
                "relative_path": relative_path,
                "model_relative_path": model_relative_path,
                "model": model,
            }
        )
        if dry_run:
            return Result(
                status="review_required",
                operation="plan_animation",
                backend="file",
                model_uid=uid,
                tool_call_succeeded=True,
                data={"snapshot_sha256": fingerprint, "file": data, "model_update": updated},
            )
        if not confirm or expected_hash != fingerprint:
            raise AutomationError(
                "STALE_PLAN", "Animation needs confirmation and the current plan hash"
            )
        evidence = [
            engine.journal.save(
                f"{job_id}/plan.json",
                {
                    "snapshot": snapshot,
                    "spec": spec.model_dump(),
                    "model_before": model,
                    "model_after": updated,
                },
                "animation_plan",
            )
        ]
        if model_path:
            evidence.append(
                engine.journal.save(f"{job_id}/model3-before.json", model, "model3_backup")
            )
        # Cross-process lock serializes our writers. Reject externally changed JSON before replacement.
        if model_path and load_json(model_path) != model:
            raise AutomationError("STALE_PLAN", "model3 changed during generation")
        atomic_json(output, data, overwrite=False)
        try:
            if model_path:
                atomic_json(model_path, updated)
            if load_json(output) != data or model_path and load_json(model_path) != updated:
                raise AutomationError("POSTCONDITION_FAILED", "Generated JSON readback differs")
        except Exception:
            if model_path and load_json(model_path) == updated:
                atomic_json(model_path, model)
            if output.is_file() and load_json(output) == data:
                output.unlink()
            raise
        evidence.append(
            engine.journal.save(f"{job_id}/generated.json", data, "animation_json_readback")
        )
        return Result(
            job_id=job_id,
            status="verified",
            operation="generate_animation",
            backend="file",
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            artifacts=[str(output)] + ([str(model_path)] if model_path else []),
            evidence=evidence,
            data={
                "format": spec.kind,
                "core_loaded": False,
                "curves": "linear",
                "expression_range_check": "at_default_pose",
            },
            warnings=[
                "File/schema verification only; run verify-runtime for official SDK playback."
            ],
        )
