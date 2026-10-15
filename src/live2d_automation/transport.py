from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from .models import AutomationError, Settings
from .storage import Journal


class CubismWS:
    """Bounded requests, separate event routing, no automatic replay/reconnect of writes.

    Wire contract follows Live2D GARAGE and CubismExternalEditMCP. The implementation
    is independent so transport failures cannot silently re-enter a transaction.
    """

    def __init__(self, settings: Settings, journal: Journal):
        self.settings, self.journal = settings, journal
        self.socket: ClientConnection | None = None
        self.reader: asyncio.Task | None = None
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.version = "0.9.0"
        self.edit_cancelled = False
        self.registration_lock = asyncio.Lock()

    async def open(self):
        async with self.registration_lock:
            if self.socket is not None:
                return
            try:
                self.socket = await connect(
                    f"ws://127.0.0.1:{self.settings.port}",
                    proxy=None,
                    open_timeout=self.settings.timeout,
                    close_timeout=2,
                    max_size=8 * 1024 * 1024,
                )
                self.reader = asyncio.create_task(self._receive())
                token_path = self.settings.state_dir / "editor.token"
                token = (
                    token_path.read_text(encoding="utf-8").strip() if token_path.is_file() else ""
                )
                registration = await self.request(
                    "RegisterPlugin", {"Name": "live2d-automation", "Token": token}
                )
                new_token = registration.get("Token")
                if not isinstance(new_token, str) or not new_token:
                    raise AutomationError(
                        "PROTOCOL_ERROR", "Editor did not return a registration token"
                    )
                token_path.parent.mkdir(parents=True, exist_ok=True)
                token_path.write_text(new_token, encoding="utf-8")
                with contextlib.suppress(OSError):
                    token_path.chmod(0o600)
            except BaseException as exc:
                await self.close()
                if isinstance(exc, (AutomationError, asyncio.CancelledError)):
                    raise
                raise AutomationError(
                    "EDITOR_UNAVAILABLE",
                    "No usable Cubism WebSocket at the loopback port",
                    reason=type(exc).__name__,
                ) from exc

    async def negotiate(self):
        # GetAPIVersion is queried only after Allow has been observed by Engine.
        # RegisterPlugin/GetIsApproval use the basic protocol before negotiation.
        # The official nizimaLIVE bridge uses 0.9.1 for GetAPIVersion. Cubism
        # 5.3 rejects this method under 0.9.0 even though registration accepts it.
        version_data = await self.request("GetAPIVersion", {}, version="0.9.1")
        version = version_data.get("Version")
        if not isinstance(version, str):
            raise AutomationError("PROTOCOL_ERROR", "Missing API version")
        self.version = version

    async def _receive(self):
        assert self.socket is not None
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise AutomationError("PROTOCOL_ERROR", "Invalid WebSocket envelope")
                if message.get("Type") == "Event":
                    if message.get("Method") == "NotifyUndoCancel":
                        self.edit_cancelled = True
                    self.journal.trace(
                        "editor_event", method=message.get("Method"), data=message.get("Data")
                    )
                    continue
                request_id = message.get("RequestId")
                if not isinstance(request_id, str):
                    continue
                future = self.pending.get(request_id)
                if future is None or future.done():
                    continue
                if message.get("Type") == "Error":
                    data = message.get("Data", {})
                    future.set_exception(
                        AutomationError(
                            "EDITOR_API_ERROR",
                            "Editor rejected request",
                            editor_error=data.get("ErrorType"),
                            method=message.get("Method"),
                        )
                    )
                elif message.get("Type") == "Response" and isinstance(message.get("Data"), dict):
                    future.set_result(message["Data"])
                else:
                    future.set_exception(
                        AutomationError("PROTOCOL_ERROR", "Malformed Editor response")
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, AutomationError)
                else AutomationError(
                    "EDITOR_DISCONNECTED", "Editor disconnected", reason=type(exc).__name__
                )
            )
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        AutomationError("EDITOR_DISCONNECTED", "Editor connection ended")
                    )

    async def request(self, method: str, data: dict, *, version: str | None = None):
        if self.socket is None or self.reader is None or self.reader.done():
            raise AutomationError("EDITOR_DISCONNECTED", "Connect before calling Editor APIs")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.journal.trace("request", request_id=request_id, method=method, data=data)
        try:
            await self.socket.send(
                json.dumps(
                    {
                        "Version": version or self.version,
                        "RequestId": request_id,
                        "Type": "Request",
                        "Method": method,
                        "Data": data,
                    },
                    allow_nan=False,
                )
            )
            response = await asyncio.wait_for(future, self.settings.timeout)
            self.journal.trace("response", request_id=request_id, method=method, data=response)
            return response
        except asyncio.TimeoutError as exc:
            self.journal.trace("timeout", request_id=request_id, method=method)
            raise AutomationError(
                "EDITOR_UNRESPONSIVE",
                "Request timed out; it may have executed. Do not retry a write.",
                method=method,
            ) from exc
        except (ConnectionClosed, OSError) as exc:
            raise AutomationError(
                "EDITOR_DISCONNECTED",
                "Editor disconnected while sending; write outcome may be uncertain",
                method=method,
            ) from exc
        finally:
            self.pending.pop(request_id, None)

    async def close(self):
        if self.socket is not None:
            with contextlib.suppress(Exception):
                await self.socket.close()
        if self.reader is not None:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader
        self.socket = self.reader = None
        self.version = "0.9.0"
