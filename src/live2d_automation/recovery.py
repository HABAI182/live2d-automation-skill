"""Confirm recovery of binding/UI jobs without guessing inverse native edits."""

import hashlib
import re
from pathlib import Path

from filelock import FileLock

from .bindings import capture
from .engine import Engine
from .files import load_json
from .models import AutomationError, Result
from .storage import safe_path


async def recover(engine: Engine, job_id: str, expected_uid: str | None = None) -> Result:
    if not re.fullmatch(r"(?:binding|ui)-[a-f0-9]{32}", job_id):
        raise AutomationError("INVALID_JOB", "Expected a binding/UI job ID")
    with FileLock(engine.lock_root / f"editor-{engine.settings.port}.lock", timeout=0):
        name = "binding.json" if job_id.startswith("binding-") else "recovery.json"
        record = load_json(safe_path(engine.settings.state_dir, f"jobs/{job_id}/{name}"))
        before = record["before"]
        original_uid = before["structure"]["model_uid"]
        uid, _ = await engine.ready(expected_uid or original_uid)
        if uid != original_uid:
            docs = await engine.ws.request("GetDocuments", {})
            sources = [
                Path(d.get("DocumentFilePath", ""))
                for d in docs.get("ModelingDocuments", [])
                if any(v.get("ModelUID") == uid for v in d.get("Views", []))
            ]
            backup = safe_path(engine.settings.state_dir, f"jobs/{job_id}/original.cmo3")
            if (
                len(sources) != 1
                or not sources[0].is_file()
                or not backup.is_file()
                or (
                    hashlib.sha256(sources[0].read_bytes()).digest()
                    != hashlib.sha256(backup.read_bytes()).digest()
                )
            ):
                raise AutomationError(
                    "RESTORE_IDENTITY_UNCONFIRMED", "Reopen this job's original.cmo3 backup"
                )
        after = await capture(engine, uid)

        def logical(value):
            return {"normalized": value["structure"]["normalized"], "objects": value["objects"]}

        if logical(after) != logical(before):
            raise AutomationError(
                "MANUAL_RESTORE_REQUIRED",
                "Model properties/keys still differ; reopen the native backup. Unsaved artwork is not recoverable from a structural snapshot.",
            )
        if job_id.startswith("ui-"):
            from .editor_ui import EditorUI

            assert engine.editor is not None
            ui = EditorUI(engine.editor["pid"])
            try:
                titles = [w["title"] for w in ui.bridge.windows()]
                if not titles or any(not t.startswith("Live2D Cubism Editor") for t in titles):
                    raise AutomationError(
                        "UI_DIALOG_PENDING",
                        "Close Editor dialogs before confirming recovery",
                        windows=titles,
                    )
            finally:
                ui.close()
        if engine.marker.exists():
            marker = load_json(engine.marker)
            if marker.get("job_id") != job_id:
                raise AutomationError(
                    "RECOVERY_JOB_MISMATCH", "Another job owns the recovery marker"
                )
            engine.marker.unlink()
        evidence = engine.journal.save(
            f"{job_id}/recovery-confirmed.json", after, "binding_property_recovery"
        )
        return Result(
            job_id=job_id,
            status="rolled_back",
            operation="recover_workflow",
            model_uid=uid,
            tool_call_succeeded=True,
            postconditions_passed=True,
            evidence=[evidence],
            warnings=[
                "Property/key recovery confirmed; vertex geometry is outside the snapshot scope."
            ],
        )
