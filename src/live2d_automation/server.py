from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .animation import AnimationSpec
from .bindings import BlinkRecipe
from .capabilities import registry
from .cli import settings_from_env
from .engine import Engine
from .files import validate_bundle
from .models import Operation, Result, Settings
from .runtime_check import RuntimeOptions


def make_server(settings: Settings | None = None):
    engine = Engine(settings or settings_from_env())

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {"engine": engine}
        finally:
            await engine.close()

    mcp = FastMCP("live2d-automation", lifespan=lifespan)
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    )

    async def call(method, **kwargs):
        return await engine.run(method, **kwargs)

    @mcp.tool(annotations=read)
    async def live2d_doctor() -> Result:
        """Probe environment, real API port, permissions, mode and recovery markers."""
        return await call("doctor")

    @mcp.tool(annotations=read)
    async def live2d_capabilities() -> dict[str, Any]:
        """Read capability levels; protocol tests never certify real native editing."""
        return registry()

    @mcp.tool(annotations=read)
    async def live2d_connect() -> Result:
        """Register with local Editor; Allow/Edit must be granted in Cubism."""
        return await call("connect")

    @mcp.tool(annotations=read)
    async def live2d_list_documents() -> Result:
        """List open Editor documents; never infer the target from a window title."""
        return await call("list_documents")

    @mcp.tool(annotations=read)
    async def live2d_snapshot_model(expected_uid: str | None = None) -> Result:
        """Read and hash parameter, part and deformer trees; not a native binary backup."""
        return await call("snapshot", expected_uid=expected_uid)

    @mcp.tool(annotations=read)
    async def live2d_inspect_model(expected_uid: str | None = None) -> Result:
        """Inspect ranges, keys and duplicate IDs; partial base-API inspection is labeled."""
        return await call("inspect", expected_uid=expected_uid)

    @mcp.tool(annotations=read)
    async def live2d_plan_changes(expected_uid: str, operations: list[Operation]) -> Result:
        """Dry-run typed changes and produce a snapshot hash for optimistic concurrency."""
        return await call("plan", expected_uid=expected_uid, operations=operations)

    @mcp.tool(annotations=write)
    async def live2d_apply_parameter_ops(
        expected_uid: str,
        operations: list[Operation],
        idempotency_key: str,
        dry_run: bool = True,
        confirm: bool = False,
        expected_snapshot_sha256: str | None = None,
    ) -> Result:
        """Apply parameter/group operations in one transaction; default is dry-run."""
        if any(op.kind in {"create_part", "create_warp", "create_rotation"} for op in operations):
            return Result(
                status="blocked",
                operation="apply",
                error={
                    "code": "INVALID_INPUT",
                    "message": "Use structure tool for object creation",
                },
            )
        return await call(
            "apply",
            expected_uid=expected_uid,
            operations=operations,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
            confirm=confirm,
            expected_snapshot_sha256=expected_snapshot_sha256,
        )

    @mcp.tool(annotations=write)
    async def live2d_apply_structure_ops(
        expected_uid: str,
        operations: list[Operation],
        idempotency_key: str,
        dry_run: bool = True,
        confirm: bool = False,
        expected_snapshot_sha256: str | None = None,
    ) -> Result:
        """Create empty Part/Warp/Rotation containers. Does not author or bind geometry."""
        if any(
            op.kind not in {"create_part", "create_warp", "create_rotation"} for op in operations
        ):
            return Result(
                status="blocked",
                operation="apply",
                error={
                    "code": "INVALID_INPUT",
                    "message": "Use parameter tool for parameter/group changes",
                },
            )
        return await call(
            "apply",
            expected_uid=expected_uid,
            operations=operations,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
            confirm=confirm,
            expected_snapshot_sha256=expected_snapshot_sha256,
        )

    @mcp.tool(annotations=read)
    async def live2d_verify_changes(job_id: str) -> Result:
        """Independently reread current model state against a recorded plan."""
        return await call("verify", job_id=job_id)

    @mcp.tool(annotations=write)
    async def live2d_rollback_job(job_id: str, expected_uid: str | None = None) -> Result:
        """Confirm restored structure, clear matching recovery marker; never invent inverse edits."""
        return await call("rollback", job_id=job_id, expected_uid=expected_uid)

    @mcp.tool(annotations=read)
    async def live2d_validate_bundle(model_path: str) -> Result:
        """Offline resource/partial JSON checks, explicitly separate from Core load or visual QA."""
        return validate_bundle(model_path)

    @mcp.tool(annotations=read)
    async def live2d_snapshot_bindings(expected_uid: str) -> Result:
        """Read object properties at every bounded key combination; excludes vertex coordinates."""
        return await call("snapshot_bindings", expected_uid=expected_uid)

    @mcp.tool(annotations=write)
    async def live2d_apply_blink_binding(
        expected_uid: str,
        recipe: BlinkRecipe,
        dry_run: bool = True,
        confirm: bool = False,
        cancel_trial: bool = False,
        expected_snapshot_sha256: str | None = None,
    ) -> Result:
        """Bind existing eye/lid ArtMeshes by opacity, read back keys/properties; no sculpting."""
        return await call(
            "blink",
            uid=expected_uid,
            recipe=recipe,
            dry_run=dry_run,
            confirm=confirm,
            cancel_trial=cancel_trial,
            expected_hash=expected_snapshot_sha256,
        )

    @mcp.tool(annotations=write)
    async def live2d_editor_file(
        action: Literal["save", "reopen", "export"],
        expected_uid: str,
        relative_path: str | None = None,
        save_job_id: str | None = None,
        dry_run: bool = True,
        confirm: bool = False,
        expected_snapshot_sha256: str | None = None,
    ) -> Result:
        """Save copy, reopen a verified save, or export via scoped Windows JAB/shortcuts. Uses focus."""
        return await call(
            "editor_file",
            action=action,
            uid=expected_uid,
            relative_path=relative_path,
            save_job_id=save_job_id,
            dry_run=dry_run,
            confirm=confirm,
            expected_hash=expected_snapshot_sha256,
        )

    @mcp.tool(annotations=write)
    async def live2d_generate_animation(
        expected_uid: str,
        spec: AnimationSpec,
        relative_path: str,
        model_relative_path: str | None = None,
        dry_run: bool = True,
        confirm: bool = False,
        expected_snapshot_sha256: str | None = None,
    ) -> Result:
        """Generate range-checked linear motion3/exp3; optionally back up and link exported model3."""
        return await call(
            "generate_animation",
            expected_uid=expected_uid,
            spec=spec,
            relative_path=relative_path,
            model_relative_path=model_relative_path,
            dry_run=dry_run,
            confirm=confirm,
            expected_hash=expected_snapshot_sha256,
        )

    @mcp.tool(annotations=read)
    async def live2d_verify_runtime(options: RuntimeOptions) -> Result:
        """Load user-supplied official Core/Framework, render WebGL, check parameter/motion/expression effects."""
        return await call("verify_runtime", options=options)

    return mcp


def main():
    make_server().run(transport="stdio")


if __name__ == "__main__":
    main()
