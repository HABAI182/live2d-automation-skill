"""Version-scoped Java Accessibility/Win32 adapter; no computer-use service required."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import time
import uuid
from pathlib import Path

from filelock import FileLock

from .bindings import capture
from .engine import Engine
from .jab import Bridge, Todo
from .models import AutomationError, Result
from .storage import atomic_json, digest, safe_path


def logical(snapshot):
    return {"normalized": snapshot["structure"]["normalized"], "objects": snapshot["objects"]}


class EditorUI:
    def __init__(self, pid: int):
        import win32api
        import win32con
        import win32gui
        import win32process

        self.api, self.con, self.gui, self.proc = win32api, win32con, win32gui, win32process
        self.pid, self.bridge = pid, Bridge(pid)

    def close(self):
        self.bridge.close()

    def nodes(self, title: str):
        self.bridge.close()
        windows = self.bridge.windows()
        matches = [w for w in windows if w["title"].strip() == title]
        if title == "main":
            matches = [w for w in windows if w["title"].startswith("Live2D Cubism Editor")]
        if not matches:
            return [], None
        if len(matches) != 1:
            raise AutomationError("UI_AMBIGUOUS", "Expected one Editor window", title=title)
        window = matches[0]
        return list(self.bridge.walk(window["vm"], window["ac"], max_depth=26)), window

    def find(self, title: str, name: str, role: str):
        nodes, window = self.nodes(title)
        selected = [n for n in nodes if n["name"] == name and n["role"] == role]
        if len(selected) != 1:
            raise AutomationError(
                "UI_CONTROL_MISSING",
                "Expected one named control",
                window=title,
                name=name,
                role=role,
            )
        if "enabled" not in selected[0]["states"].split(","):
            raise AutomationError("UI_CONTROL_DISABLED", "Editor control is disabled", name=name)
        return selected[0], window

    def focus(self, hwnd):
        self.gui.ShowWindow(hwnd, self.con.SW_RESTORE)
        try:
            self.gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if self.proc.GetWindowThreadProcessId(self.gui.GetForegroundWindow())[1] != self.pid:
            current = ctypes.windll.kernel32.GetCurrentThreadId()
            foreground = self.proc.GetWindowThreadProcessId(self.gui.GetForegroundWindow())[0]
            ctypes.windll.user32.AttachThreadInput(current, foreground, True)
            try:
                self.gui.BringWindowToTop(hwnd)
                self.gui.SetForegroundWindow(hwnd)
            finally:
                ctypes.windll.user32.AttachThreadInput(current, foreground, False)
        self.bridge.pump(0.08)
        self.check_focus()

    def check_focus(self):
        if self.proc.GetWindowThreadProcessId(self.gui.GetForegroundWindow())[1] != self.pid:
            raise AutomationError("UI_FOCUS_CHANGED", "Input stopped because Cubism lost focus")

    def key(self, *keys):
        self.check_focus()
        try:
            for key in keys:
                self.api.keybd_event(key, 0, 0, 0)
            time.sleep(0.03)
        finally:
            for key in reversed(keys):
                self.api.keybd_event(key, 0, self.con.KEYEVENTF_KEYUP, 0)
        self.bridge.pump(0.1)

    def shortcut(self, name):
        _, window = self.nodes("main")
        if window is None:
            raise AutomationError(
                "ACCESSIBILITY_UNAVAILABLE",
                "Launch the test Editor with the Java Access Bridge enabled",
            )
        pending = [
            w["title"] or "unnamed menu/window"
            for w in self.bridge.windows()
            if w["hwnd"] != window["hwnd"]
        ]
        if pending:
            raise AutomationError(
                "UI_DIALOG_PENDING",
                "Close open menus/dialogs before a file shortcut",
                windows=pending,
            )
        self.focus(window["hwnd"])
        self.key(
            *{
                "save_as": [17, 16, 83],
                "save": [17, 83],
                "export": [17, 18, 83],
                "open": [17, 79],
                "close": [17, 87],
                "atlas": [17, 84],
            }[name]
        )

    def wait(self, title, name=None, role=None, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            nodes, window = self.nodes(title)
            if window and (
                name is None or any(n["name"] == name and n["role"] == role for n in nodes)
            ):
                return
            self.bridge.pump(0.15)
        raise AutomationError("UI_TIMEOUT", "Expected Editor dialog did not appear", title=title)

    def text(self, title, name, value):
        node, _ = self.find(title, name, "text")
        if not self.bridge.dll.setTextContents(node["vm"], node["ac"], value):
            raise AutomationError("UI_TEXT_FAILED", "Editor text field rejected the value")
        self.bridge.pump(0.1)

    def button(self, title, name):
        node, window = self.find(title, name, "push button")
        self.focus(window["hwnd"])
        # A key event opens modal dialogs outside JAB's synchronous IPC thread.
        if not self.bridge.dll.requestFocus(node["vm"], node["ac"]):
            raise AutomationError("UI_FOCUS_FAILED", "Could not focus the named button")
        self.bridge.pump(0.08)
        self.key(13)

    def menu(self, parent, child):
        node, window = self.find("main", parent, "menu")
        self.focus(window["hwnd"])
        todo = Todo()
        todo.count = 1
        todo.items[0].name = "click"
        failure = ctypes.c_int(-1)
        if not self.bridge.dll.doAccessibleActions(
            node["vm"], node["ac"], ctypes.byref(todo), ctypes.byref(failure)
        ):
            raise AutomationError("UI_MENU_FAILED", "Could not open Editor menu")
        self.bridge.pump(0.1)
        for _ in range(60):
            selected = [
                n
                for n in self.bridge.walk(node["vm"], node["ac"], max_depth=2)
                if "selected" in n["states"].split(",")
            ]
            if any(n["name"] == child for n in selected):
                self.key(13)
                return
            self.key(40)
        raise AutomationError("UI_MENU_FAILED", "Target menu item was not selected")


async def run_ui(
    engine: Engine,
    action: str,
    *,
    uid: str | None = None,
    relative_path: str | None = None,
    save_job_id: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
    expected_hash: str | None = None,
) -> Result:
    job_id = "ui-" + uuid.uuid4().hex
    lock = FileLock(engine.lock_root / f"editor-{engine.settings.port}.lock", timeout=0)
    ui = None
    evidence = []
    try:
        lock.acquire()
        if engine.marker.exists():
            raise AutomationError(
                "RECOVERY_REQUIRED", "Resolve the previous write before UI actions"
            )
        uid, info = await engine.ready(uid)
        engine.edit_gate(info)
        before = await capture(engine, uid)
        if action not in {"save", "export", "reopen"}:
            raise AutomationError("INVALID_INPUT", "Unknown UI workflow")
        if action != "reopen":
            target = safe_path(engine.settings.output_dir, relative_path or "")
            suffix = ".cmo3" if action == "save" else ".moc3"
            if target.suffix.lower() != suffix or target.exists():
                raise AutomationError(
                    "INVALID_OUTPUT", "Use a new output file with extension " + suffix
                )
            if action == "export" and target.parent.exists() and any(target.parent.iterdir()):
                raise AutomationError("INVALID_OUTPUT", "Export requires a new empty subdirectory")
        if dry_run:
            return Result(
                status="review_required",
                operation="plan_editor_ui",
                model_uid=uid,
                tool_call_succeeded=True,
                data={
                    "action": action,
                    "snapshot_sha256": digest(before),
                    "relative_path": relative_path,
                    "save_job_id": save_job_id,
                    "requires_focus": True,
                },
            )
        if not confirm or expected_hash != digest(before):
            raise AutomationError(
                "STALE_PLAN", "UI workflow needs confirmation and the current snapshot hash"
            )
        assert engine.editor is not None
        ui = EditorUI(engine.editor["pid"])
        evidence = [engine.journal.save(f"{job_id}/before.json", before, "ui_preflight")]
        evidence.append(await engine.backup(uid, job_id))
        recovery_path = safe_path(engine.settings.state_dir, f"jobs/{job_id}/recovery.json")
        atomic_json(recovery_path, {"before": before, "action": action})
        if action == "reopen":
            if (
                not save_job_id
                or not save_job_id.startswith("ui-")
                or any(c in save_job_id for c in "/\\:")
            ):
                raise AutomationError("INVALID_JOB", "Reopen requires a successful save job")
            saved = json.loads(
                safe_path(engine.settings.state_dir, f"jobs/{save_job_id}/ui.json").read_text(
                    encoding="utf-8"
                )
            )
            target = Path(saved["file"])
            if not target.is_relative_to(engine.settings.output_dir.resolve()):
                raise AutomationError("UNSAFE_PATH", "Saved model is outside the output directory")
            if hashlib.sha256(target.read_bytes()).hexdigest() != saved["sha256"]:
                raise AutomationError(
                    "SAVED_FILE_CHANGED", "The saved model changed since verification"
                )
            if saved.get("action") != "save":
                raise AutomationError("INVALID_JOB", "Reopen requires a native save job")
            if logical(before) != saved["snapshot"]:
                raise AutomationError(
                    "UNSAVED_MODEL_CHANGED", "Save the current model before reopening"
                )
            atomic_json(engine.marker, {"job_id": job_id, "phase": "ui_reopen", "model_uid": uid})
            ui.shortcut("close")
            ui.shortcut("open")
            ui.wait("Open", "File Name:", "text")
            ui.text("Open", "File Name:", str(target))
            ui.button("Open", "Open")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    current, _ = await engine.ready()
                    docs = await engine.ws.request("GetDocuments", {})
                    if any(
                        Path(d.get("DocumentFilePath", "")).resolve() == target.resolve()
                        and any(v.get("ModelUID") == current for v in d.get("Views", []))
                        for d in docs.get("ModelingDocuments", [])
                    ):
                        after = await capture(engine, current)
                        if logical(after) != saved["snapshot"]:
                            raise AutomationError(
                                "POSTCONDITION_FAILED", "Reopened model properties/bindings differ"
                            )
                        evidence.append(
                            engine.journal.save(
                                f"{job_id}/reopened.json", after, "native_reopen_readback"
                            )
                        )
                        engine.marker.unlink()
                        return Result(
                            job_id=job_id,
                            status="verified",
                            operation="reopen_project",
                            backend="java_access_bridge_and_win32_shortcuts",
                            model_uid=current,
                            tool_call_succeeded=True,
                            postconditions_passed=True,
                            artifacts=[str(target)],
                            evidence=evidence,
                            data={
                                "old_uid": uid,
                                "new_uid": current,
                                "saved_project_reopened": True,
                                "computer_use_calls": 0,
                            },
                        )
                except AutomationError as exc:
                    if exc.code not in {"NO_MODEL", "EDITOR_API_ERROR"}:
                        raise
                await asyncio.sleep(0.2)
            raise AutomationError("UI_TIMEOUT", "Saved model did not reopen")
        if relative_path is None:
            raise AutomationError("INVALID_INPUT", "A relative output path is required")
        target = safe_path(engine.settings.output_dir, relative_path)
        if target.exists():
            raise AutomationError(
                "OUTPUT_EXISTS",
                "Choose a new output path; native files are never silently overwritten",
            )
        if action == "save":
            if target.suffix.lower() != ".cmo3":
                raise AutomationError("INVALID_INPUT", "Native project must end in .cmo3")
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(engine.marker, {"job_id": job_id, "phase": "ui_save", "model_uid": uid})
            ui.shortcut("save_as")
        elif action == "export":
            if (
                target.suffix.lower() != ".moc3"
                or target.parent.exists()
                and any(target.parent.iterdir())
            ):
                raise AutomationError(
                    "INVALID_INPUT", "Export requires .moc3 in a new empty subdirectory"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(engine.marker, {"job_id": job_id, "phase": "ui_export", "model_uid": uid})
            ui.shortcut("export")
            ui.wait("Export settings", "OK", "push button")
            ui.button("Export settings", "OK")
        else:
            raise AutomationError("INVALID_INPUT", "Unknown UI workflow")
        ui.wait("Save", "File Name:", "text")
        ui.text("Save", "File Name:", str(target))
        ui.button("Save", "Save")
        end, previous, stable = time.monotonic() + 45, None, 0
        while time.monotonic() < end:
            if target.is_file() and target.stat().st_size > 0:
                size = target.stat().st_size
                stable = stable + 1 if size == previous else 0
                previous = size
                if stable >= 5:
                    break
            await asyncio.sleep(0.2)
        else:
            raise AutomationError("UI_TIMEOUT", "Native output was not written")
        after = await capture(engine, uid)
        if logical(after) != logical(before):
            raise AutomationError(
                "POSTCONDITION_FAILED", "Save/export changed model properties or bindings"
            )
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        record = {"file": str(target), "sha256": sha, "snapshot": logical(after), "action": action}
        record_path = safe_path(engine.settings.state_dir, f"jobs/{job_id}/ui.json")
        atomic_json(record_path, record)
        evidence.append(engine.journal.save(f"{job_id}/after.json", record, "native_file_write"))
        engine.marker.unlink()
        return Result(
            job_id=job_id,
            status="verified",
            operation="save_project" if action == "save" else "export_model",
            backend="java_access_bridge_and_win32_shortcuts",
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            artifacts=[str(target)],
            evidence=evidence,
            data={
                "sha256": sha,
                "computer_use_calls": 0,
                "backend": "java_access_bridge_and_win32_shortcuts",
                "core_load_verified": False,
            },
        )
    except Exception as exc:
        return Result(
            job_id=job_id,
            status="blocked",
            operation="editor_ui",
            backend="java_access_bridge_and_win32_shortcuts",
            evidence=evidence,
            error=exc.as_dict()
            if isinstance(exc, AutomationError)
            else {"code": "UI_FAILED", "message": str(exc)},
        )
    finally:
        if ui:
            ui.close()
        if lock.is_locked:
            lock.release()
