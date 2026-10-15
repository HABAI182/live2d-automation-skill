import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from live2d_automation.models import AutomationError, Settings
from live2d_automation.storage import Journal
from live2d_automation.transport import CubismWS


@pytest.fixture
def ws(tmp_path):
    return CubismWS(
        Settings(state_dir=tmp_path / "state", output_dir=tmp_path / "output", timeout=0.15),
        Journal(tmp_path / "trace"),
    )


async def reply(socket, msg, data, type="Response"):
    await socket.send(
        json.dumps(
            {
                "Version": "1.1.0",
                "Type": type,
                "RequestId": msg["RequestId"],
                "Method": msg["Method"],
                "Data": data,
            }
        )
    )


async def handshake(socket, msg):
    if msg["Method"] == "RegisterPlugin":
        await reply(socket, msg, {"Token": "fixture-secret-never-log"})
        return True
    if msg["Method"] == "GetAPIVersion":
        await reply(socket, msg, {"Version": "1.1.0"})
        return True
    return False


async def test_real_loopback_wire_correlates_out_of_order_and_events(ws):
    messages = []

    async def server(socket):
        async for raw in socket:
            msg = json.loads(raw)
            if await handshake(socket, msg):
                continue
            messages.append(msg)
            if len(messages) == 2:
                await socket.send(
                    json.dumps(
                        {"Type": "Event", "Method": "NotifyUndoCancel", "Data": {"Result": True}}
                    )
                )
                await reply(socket, messages[1], {"answer": "second"})
                await reply(socket, messages[0], {"answer": "first"})

    async with serve(server, "127.0.0.1", 0) as fixture_server:
        ws.settings.port = fixture_server.sockets[0].getsockname()[1]
        await ws.open()
        one, two = await asyncio.gather(ws.request("First", {}), ws.request("Second", {}))
        assert one["answer"] == "first" and two["answer"] == "second"
        assert ws.edit_cancelled and not ws.pending
        await ws.close()
    assert "fixture-secret-never-log" not in (ws.journal.root / "trace.jsonl").read_text()


async def test_timeout_is_bounded_and_late_reply_cannot_satisfy_next_request(ws):
    async def server(socket):
        async for raw in socket:
            msg = json.loads(raw)
            if await handshake(socket, msg):
                continue
            if msg["Method"] == "Slow":
                await asyncio.sleep(0.2)
            await reply(socket, msg, {"method": msg["Method"]})

    async with serve(server, "127.0.0.1", 0) as fixture_server:
        ws.settings.port = fixture_server.sockets[0].getsockname()[1]
        await ws.open()
        with pytest.raises(AutomationError, match="timed out"):
            await ws.request("Slow", {})
        assert not ws.pending
        result = await ws.request("Next", {})
        assert result["method"] == "Next"
        await ws.close()


async def test_disconnect_wakes_pending_requests(ws):
    async def server(socket):
        async for raw in socket:
            msg = json.loads(raw)
            if not await handshake(socket, msg):
                await socket.close()

    async with serve(server, "127.0.0.1", 0) as fixture_server:
        ws.settings.port = fixture_server.sockets[0].getsockname()[1]
        await ws.open()
        with pytest.raises(AutomationError, match="connection ended"):
            await ws.request("EditBegin", {})
        assert not ws.pending
        await ws.close()


async def test_api_error_envelope_is_failure(ws):
    async def server(socket):
        async for raw in socket:
            msg = json.loads(raw)
            if not await handshake(socket, msg):
                await reply(socket, msg, {"ErrorType": "InvalidEditOperation"}, type="Error")

    async with serve(server, "127.0.0.1", 0) as fixture_server:
        ws.settings.port = fixture_server.sockets[0].getsockname()[1]
        await ws.open()
        with pytest.raises(AutomationError) as error:
            await ws.request("AddParameter", {"ModelUID": "x"})
        assert error.value.details["editor_error"] == "InvalidEditOperation"
        await ws.close()


async def test_closed_port_is_not_a_registered_editor(ws):
    async with serve(lambda socket: None, "127.0.0.1", 0) as fixture_server:
        ws.settings.port = fixture_server.sockets[0].getsockname()[1]
    with pytest.raises(AutomationError):
        await ws.open()
    assert ws.socket is None
