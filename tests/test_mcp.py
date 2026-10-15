import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_actual_stdio_server_handshake_tools_schema_and_offline_errors(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "live2d_automation.server"],
        env={
            **os.environ,
            "LIVE2D_STATE_DIR": str(tmp_path / "state"),
            "LIVE2D_OUTPUT_DIR": str(tmp_path / "output"),
            "LIVE2D_PORT": "22998",
            "LIVE2D_TIMEOUT": "0.2",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "live2d-automation"
            tools = (await session.list_tools()).tools
            assert len(tools) == 17
            for name in (
                "live2d_apply_blink_binding",
                "live2d_editor_file",
                "live2d_generate_animation",
            ):
                schema = next(t for t in tools if t.name == name).inputSchema
                assert schema["properties"]["dry_run"]["default"] is True
                assert schema["properties"]["confirm"]["default"] is False
            assert all(t.outputSchema for t in tools)
            tool = next(t for t in tools if t.name == "live2d_apply_parameter_ops")
            assert tool.inputSchema["properties"]["dry_run"]["default"] is True
            assert tool.inputSchema["properties"]["confirm"]["default"] is False
            caps = await session.call_tool("live2d_capabilities", {})
            assert caps.structuredContent["capabilities"]["parameter.edit"]["level"] == "pilot"
            invalid = await session.call_tool(
                "live2d_apply_parameter_ops",
                {
                    "expected_uid": "x",
                    "operations": [{"kind": "Shell", "id": "X"}],
                    "idempotency_key": "invalid",
                },
            )
            assert invalid.isError
            read_result = await session.call_tool(
                "live2d_validate_bundle", {"model_path": str(tmp_path / "missing.model3.json")}
            )
            assert read_result.structuredContent["status"] == "failed"
            probe = await session.call_tool("live2d_doctor", {})
            assert "data" in probe.structuredContent
