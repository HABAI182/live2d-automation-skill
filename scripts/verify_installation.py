"""Verify a wheel install against source and exercise an actual stdio MCP client."""

import asyncio
import hashlib
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify():
    repo = Path(__file__).resolve().parents[1]
    source = repo / "src" / "live2d_automation"
    installed = Path(str(files("live2d_automation")))
    if installed.resolve() == source.resolve():
        raise RuntimeError("Expected an installed wheel, not an editable source import")
    checks = []
    for path in sorted(source.rglob("*")):
        if path.suffix not in {".py", ".yaml", ".ts", ".html", ".mjs"}:
            continue
        relative = path.relative_to(source)
        target = installed / relative
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise RuntimeError(f"Wheel/source mismatch: {path.name}")
        checks.append({"file": relative.as_posix(), "sha256": sha})
    env = {**os.environ, "LIVE2D_TIMEOUT": "2"}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "live2d_automation.server"], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            initialized = await client.initialize()
            tools = (await client.list_tools()).tools
            if len(tools) != 17 or initialized.serverInfo.name != "live2d-automation":
                raise RuntimeError("Unexpected MCP tool inventory")
            if any(t.outputSchema is None for t in tools):
                raise RuntimeError("Missing typed MCP output schema")
            capabilities = await client.call_tool("live2d_capabilities", {})
            if capabilities.isError or capabilities.structuredContent is None:
                raise RuntimeError("MCP capability call failed")
            doctor = await client.call_tool("live2d_doctor", {})
            if doctor.isError or doctor.structuredContent is None:
                raise RuntimeError("MCP doctor failed to return a structured result")
            snapshot = None
            if os.environ.get("LIVE2D_TEST_MODEL_UID"):
                response = await client.call_tool(
                    "live2d_snapshot_model",
                    {"expected_uid": os.environ["LIVE2D_TEST_MODEL_UID"]},
                )
                snapshot = response.structuredContent
                if response.isError or not snapshot or not snapshot.get("postconditions_passed"):
                    raise RuntimeError("Installed MCP real-model snapshot failed")
            return {
                "wheel_source_files_match": True,
                "installed_package": str(installed),
                "files": checks,
                "mcp_initialized": True,
                "tools": [t.name for t in tools],
                "all_output_schemas_present": True,
                "native_doctor": doctor.structuredContent,
                "native_snapshot": snapshot,
            }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(asyncio.run(verify()), ensure_ascii=False, indent=2))
