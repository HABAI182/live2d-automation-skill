"""Shared typed workflow dispatch under Engine's session serialization."""

from pydantic import TypeAdapter

from .animation import AnimationSpec, generate
from .bindings import BlinkRecipe, apply_blink, capture
from .capabilities import route
from .editor_ui import run_ui
from .models import Result
from .runtime_check import RuntimeOptions, verify_runtime
from .storage import digest


async def dispatch(engine, method, **kwargs):
    route(
        {
            "blink": "binding.blink",
            "editor_file": "gui.automation",
            "generate_animation": "animation.generate",
            "verify_runtime": "runtime.verify",
            "snapshot_bindings": "model.bindings",
        }[method]
    )
    if method == "blink":
        kwargs["recipe"] = BlinkRecipe.model_validate(kwargs["recipe"])
        return await apply_blink(engine, **kwargs)
    if method == "editor_file":
        return await run_ui(engine, **kwargs)
    if method == "generate_animation":
        kwargs["spec"] = TypeAdapter(AnimationSpec).validate_python(kwargs["spec"])
        return await generate(engine, **kwargs)
    if method == "verify_runtime":
        return await verify_runtime(engine, RuntimeOptions.model_validate(kwargs["options"]))
    uid, _ = await engine.ready(kwargs["expected_uid"])
    state = await capture(engine, uid)
    return Result(
        status="verified",
        operation="snapshot_bindings",
        model_uid=uid,
        tool_call_succeeded=True,
        postconditions_passed=True,
        data={
            "snapshot": state,
            "snapshot_sha256": digest(state),
            "vertex_positions_included": False,
        },
    )
