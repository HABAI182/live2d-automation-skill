"""Optional official SDK/WebGL verification; SDK/Core remain user-supplied."""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Literal

import psutil
from pydantic import Field, model_validator

from .engine import Engine
from .files import load_json, validate_bundle
from .models import AutomationError, Result, StrictModel
from .storage import atomic_json, safe_path


class RuntimeCase(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["parameters", "motion", "expression"] = "parameters"
    parameters: dict[str, float] = Field(default_factory=dict, max_length=64)
    expected_parameters: dict[str, float] = Field(default_factory=dict, max_length=64)
    resource_name: str | None = Field(default=None, max_length=128)
    time: float = Field(default=0.25, ge=0, le=3600)
    require_pixel_change: bool = True
    require_geometry_change: bool = False

    @model_validator(mode="after")
    def valid_case(self):
        if self.kind == "parameters" and self.resource_name is not None:
            raise ValueError("Parameter cases cannot name an animation resource")
        if self.kind != "parameters" and (not self.resource_name or self.parameters):
            raise ValueError(
                "Animation cases require a resource name and no direct parameter overrides"
            )
        return self


class RuntimeOptions(StrictModel):
    model_relative_path: str = Field(max_length=1024)
    framework_dir: str = Field(max_length=1024)
    core_dir: str = Field(max_length=1024)
    cases: list[RuntimeCase] = Field(default_factory=list, max_length=16)
    browser_channel: Literal["msedge", "chrome", "chromium"] = "msedge"


async def verify_runtime(engine: Engine, options: RuntimeOptions) -> Result:
    source = safe_path(engine.settings.output_dir, options.model_relative_path)
    checked = validate_bundle(str(source))
    if not checked.data.get("resource_checks_passed"):
        raise AutomationError(
            "INVALID_BUNDLE", "Bundle resource checks failed", errors=checked.data.get("errors")
        )
    framework, core = Path(options.framework_dir).resolve(), Path(options.core_dir).resolve()
    if (
        not (framework / "src/live2dcubismframework.ts").is_file()
        or not (core / "live2dcubismcore.min.js").is_file()
    ):
        raise AutomationError("SDK_MISSING", "Supply official Framework and Core directories")
    modules = Path(
        os.environ.get(
            "LIVE2D_NODE_MODULES", str(Path(__file__).resolve().parents[2] / "runtime/node_modules")
        )
    ).resolve()
    node = shutil.which("node")
    if (
        not node
        or not (modules / "vite/package.json").is_file()
        or not (modules / "playwright/package.json").is_file()
    ):
        raise AutomationError(
            "RUNTIME_DEPENDENCY_MISSING",
            "Install runtime/package.json dependencies and set LIVE2D_NODE_MODULES",
        )
    model = load_json(source)
    refs = model["FileReferences"]
    for case in options.cases:
        if case.kind == "parameters" and case.resource_name is not None:
            raise AutomationError("INVALID_INPUT", "Parameter case cannot name a motion/expression")
        if case.kind == "motion" and case.resource_name not in refs.get("Motions", {}):
            raise AutomationError("RESOURCE_MISSING", "Motion group is not in model3")
        if case.kind == "expression" and not any(
            e["Name"] == case.resource_name for e in refs.get("Expressions", [])
        ):
            raise AutomationError("RESOURCE_MISSING", "Expression is not in model3")
    job_id = "runtime-" + uuid.uuid4().hex
    root = safe_path(engine.settings.output_dir, "runtime-checks/" + job_id)
    project = root / "project"
    project.mkdir(parents=True)
    assets = Path(__file__).parent / "runtime"
    for name in ("index.html", "main.ts"):
        shutil.copy2(assets / name, project / name)
    shutil.copytree(framework / "src", project / "vendor/framework/src")
    # No project binaries/art are included in the distributed tool package.
    destination_core = project / "vendor/core"
    destination_core.mkdir(parents=True)
    shutil.copy2(core / "live2dcubismcore.min.js", destination_core)
    for extra in core.glob("*.d.ts"):
        shutil.copy2(extra, destination_core)
    for extra in core.glob("LICENSE*"):
        if extra.is_file():
            shutil.copy2(extra, destination_core)
    if (core / "shaders").is_dir():
        shutil.copytree(core / "shaders", destination_core / "shaders")
    for item in checked.data["checked"]:
        target = safe_path(project / "model", item["file"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_path(source.parent, item["file"]), target)
    shutil.copy2(source, project / "model/model.model3.json")
    atomic_json(root / "options.json", options.model_dump())
    process = await asyncio.create_subprocess_exec(
        node,
        str(assets / "verify.mjs"),
        str(root),
        str(modules),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 90)
    except (TimeoutError, asyncio.CancelledError) as exc:
        try:
            for child in psutil.Process(process.pid).children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
        if process.returncode is None:
            process.kill()
        await process.wait()
        report = {
            "passed": False,
            "errors": ["SDK verifier timed out or was interrupted"],
            "progress": load_json(root / "progress.json")
            if (root / "progress.json").exists()
            else None,
        }
        atomic_json(root / "report.json", report)
        if isinstance(exc, asyncio.CancelledError):
            raise
        return Result(
            job_id=job_id,
            status="blocked",
            operation="verify_runtime",
            backend="official_cubism_sdk_webgl",
            artifacts=[str(root / "report.json")],
            data=report,
            error={"code": "RUNTIME_TIMEOUT", "message": "SDK verification exceeded 90 seconds"},
        )
    (root / "stdout.log").write_bytes(stdout)
    (root / "stderr.log").write_bytes(stderr)
    report_path = root / "report.json"
    report = (
        load_json(report_path)
        if report_path.exists()
        else {
            "passed": False,
            "errors": ["Verifier exited without a report"],
            "exit_code": process.returncode,
        }
    )
    passed = bool(report.get("passed")) and process.returncode == 0
    evidence = engine.journal.save(f"{job_id}/report.json", report, "official_sdk_webgl")
    return Result(
        job_id=job_id,
        status="verified" if passed else "failed",
        operation="verify_runtime",
        backend="official_cubism_sdk_webgl",
        tool_call_succeeded=passed,
        postconditions_passed=passed,
        evidence=[evidence],
        artifacts=[str(report_path), *[str(p) for p in root.glob("*.png")]],
        data=report,
        warnings=[
            "Deterministic SDK/visibility checks do not judge artistic quality or desktop-pet interaction."
        ],
    )
