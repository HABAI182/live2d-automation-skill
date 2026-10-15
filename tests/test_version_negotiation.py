import json

from websockets.asyncio.server import serve

from live2d_automation.models import Settings
from live2d_automation.storage import Journal
from live2d_automation.transport import CubismWS


async def test_cubism_53_requires_091_for_version_query(tmp_path):
    """Reproduce the real 5.3.04 UnsupportedVersion observed under 0.9.0."""

    async def server(socket):
        async for raw in socket:
            request = json.loads(raw)
            bad = request["Method"] == "GetAPIVersion" and request["Version"] == "0.9.0"
            data = (
                {"ErrorType": "UnsupportedVersion"}
                if bad
                else {"Token": "fixture-token"}
                if request["Method"] == "RegisterPlugin"
                else {"Version": "1.0.1"}
            )
            await socket.send(
                json.dumps(
                    {
                        "RequestId": request["RequestId"],
                        "Method": request["Method"],
                        "Type": "Error" if bad else "Response",
                        "Data": data,
                    }
                )
            )

    async with serve(server, "127.0.0.1", 0) as fixture:
        port = fixture.sockets[0].getsockname()[1]
        settings = Settings(
            state_dir=tmp_path / "state", output_dir=tmp_path / "output", port=port, timeout=1
        )
        client = CubismWS(settings, Journal(tmp_path / "trace"))
        try:
            await client.open()
            await client.negotiate()
            assert client.version == "1.0.1"
        finally:
            await client.close()
