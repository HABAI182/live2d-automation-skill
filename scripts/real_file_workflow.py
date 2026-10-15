"""Opt-in real stdio MCP save/reopen/export/animation/SDK test on a disposable model.

No fixture, asset download, UI permission grant or arbitrary shell is used.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from live2d_automation.cli import settings_from_env
from live2d_automation.files import load_json
from live2d_automation.storage import atomic_json, safe_path


async def run(args):
    settings = settings_from_env()
    prefix = "acceptance-" + uuid.uuid4().hex[:12]
    report_path = safe_path(settings.output_dir, prefix + "/workflow.json")
    records = []
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "live2d_automation.server"], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            if len(tools) != 17 or any(t.outputSchema is None for t in tools):
                raise RuntimeError("Expected 17 typed tools from this installation")

            async def call(name, payload):
                start = time.perf_counter()
                response = await client.call_tool(name, payload)
                result = response.structuredContent
                if response.isError or not result:
                    raise RuntimeError(str(response))
                records.append(
                    {
                        "tool": name,
                        "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                        **result,
                    }
                )
                atomic_json(
                    report_path,
                    {
                        "tools": len(tools),
                        "calls": records,
                        "passed": False,
                        "computer_use_calls": 0,
                    },
                )
                print(name, result["status"], flush=True)
                if result.get("error") or result["status"] in {"blocked", "failed"}:
                    raise RuntimeError(json.dumps(result.get("error") or result.get("data")))
                return result

            uid = args.uid
            await call("live2d_snapshot_bindings", {"expected_uid": uid})
            if args.recipe:
                recipe = load_json(args.recipe)
                payload = {"expected_uid": uid, "recipe": recipe}
                plan = await call("live2d_apply_blink_binding", payload)
                trial = await call(
                    "live2d_apply_blink_binding",
                    {
                        **payload,
                        "dry_run": False,
                        "confirm": True,
                        "cancel_trial": True,
                        "expected_snapshot_sha256": plan["data"]["snapshot_sha256"],
                    },
                )
                if not trial["postconditions_passed"]:
                    raise RuntimeError("Binding cancellation readback failed")
                await call("live2d_verify_changes", {"job_id": trial["job_id"]})
            saved_job = None
            for action, target in [
                ("save", prefix + "/Model.cmo3"),
                ("reopen", None),
                ("export", prefix + "/export/Model.moc3"),
            ]:
                payload = {
                    "action": action,
                    "expected_uid": uid,
                    "relative_path": target,
                    "save_job_id": saved_job if action == "reopen" else None,
                }
                plan = await call("live2d_editor_file", payload)
                result = await call(
                    "live2d_editor_file",
                    {
                        **payload,
                        "dry_run": False,
                        "confirm": True,
                        "expected_snapshot_sha256": plan["data"]["snapshot_sha256"],
                    },
                )
                if not result["postconditions_passed"]:
                    raise RuntimeError("Native file readback failed")
                if action == "save":
                    saved_job = result["job_id"]
                uid = result["model_uid"]
            for index, spec_file in enumerate(args.animation):
                spec = load_json(spec_file)
                suffix = ".motion3.json" if spec["kind"] == "motion" else ".exp3.json"
                payload = {
                    "expected_uid": uid,
                    "spec": spec,
                    "relative_path": prefix + f"/export/animations/animation-{index}" + suffix,
                    "model_relative_path": prefix + "/export/Model.model3.json",
                }
                plan = await call("live2d_generate_animation", payload)
                await call(
                    "live2d_generate_animation",
                    {
                        **payload,
                        "dry_run": False,
                        "confirm": True,
                        "expected_snapshot_sha256": plan["data"]["snapshot_sha256"],
                    },
                )
            options = load_json(args.runtime_options)
            options["model_relative_path"] = prefix + "/export/Model.model3.json"
            verified = await call("live2d_verify_runtime", {"options": options})
            passed = verified["postconditions_passed"]
            atomic_json(
                report_path,
                {"tools": len(tools), "calls": records, "passed": passed, "computer_use_calls": 0},
            )
            print(json.dumps({"passed": passed, "report": str(report_path), "new_uid": uid}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True)
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--animation", type=Path, action="append", default=[])
    parser.add_argument("--runtime-options", type=Path, required=True)
    parser.add_argument("--execute-on-test-copy", action="store_true", required=True)
    asyncio.run(run(parser.parse_args()))
