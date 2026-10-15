from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from platformdirs import user_data_path

from .capabilities import registry
from .engine import Engine
from .files import load_json, validate_bundle
from .models import Settings


def settings_from_env() -> Settings:
    return Settings(
        port=int(os.environ.get("LIVE2D_PORT", "22033")),
        timeout=float(os.environ.get("LIVE2D_TIMEOUT", "8")),
        state_dir=Path(
            os.environ.get("LIVE2D_STATE_DIR", str(user_data_path("live2d-automation")))
        ),
        output_dir=Path(os.environ.get("LIVE2D_OUTPUT_DIR", str(Path.cwd() / "output"))),
    )


def parser():
    p = argparse.ArgumentParser(
        description="Evidence-driven Cubism automation (development preview)"
    )
    p.add_argument("--port", type=int)
    p.add_argument("--state-dir", type=Path)
    p.add_argument("--output-dir", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("doctor", "connect", "list-documents", "capabilities", "mcp"):
        sub.add_parser(name)
    for name in ("snapshot", "inspect", "snapshot-bindings"):
        command = sub.add_parser(name)
        command.add_argument("--uid")
    command = sub.add_parser("blink")
    command.add_argument("--uid", required=True)
    command.add_argument("--recipe", type=Path, required=True)
    command.add_argument("--cancel-trial", action="store_true")
    command = sub.add_parser("editor-file")
    command.add_argument("--uid", required=True)
    command.add_argument("--action", choices=["save", "reopen", "export"], required=True)
    command.add_argument("--path")
    command.add_argument("--save-job")
    command = sub.add_parser("generate-animation")
    command.add_argument("--uid", required=True)
    command.add_argument("--spec", type=Path, required=True)
    command.add_argument("--path", required=True)
    command.add_argument("--model", help="Relative model3 path inside configured output directory")
    command = sub.add_parser("verify-runtime")
    command.add_argument("--options", type=Path, required=True)
    for name in ("blink", "editor-file", "generate-animation"):
        command = sub.choices[name]
        command.add_argument("--execute", action="store_true")
        command.add_argument("--confirm", action="store_true")
        command.add_argument("--snapshot-hash")
    for name in ("plan", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--uid", required=True)
        command.add_argument(
            "--ops", type=Path, required=True, help="JSON array of typed operations"
        )
        if name == "apply":
            command.add_argument("--key", required=True)
            command.add_argument("--execute", action="store_true", help="Apply instead of dry-run")
            command.add_argument("--confirm", action="store_true")
            command.add_argument("--snapshot-hash")
    for name in ("verify", "rollback"):
        command = sub.add_parser(name)
        command.add_argument("--job", required=True)
        if name == "rollback":
            command.add_argument("--uid", help="Explicit new UID after reopening a native backup")
    command = sub.add_parser("validate-bundle")
    command.add_argument("model", type=Path)
    return p


async def execute(args, settings):
    engine = Engine(settings)
    try:
        params = {}
        if args.command in {"snapshot", "inspect", "snapshot-bindings"}:
            params["expected_uid"] = args.uid
        if args.command in {"plan", "apply"}:
            params.update(expected_uid=args.uid, operations=load_json(args.ops))
        if args.command == "apply":
            params.update(
                idempotency_key=args.key,
                dry_run=not args.execute,
                confirm=args.confirm,
                expected_snapshot_sha256=args.snapshot_hash,
            )
        if args.command in {"verify", "rollback"}:
            params["job_id"] = args.job
        if args.command == "rollback":
            params["expected_uid"] = args.uid
        if args.command in {"blink", "editor-file", "generate-animation"}:
            params.update(
                dry_run=not args.execute, confirm=args.confirm, expected_hash=args.snapshot_hash
            )
        if args.command == "blink":
            params.update(
                uid=args.uid, recipe=load_json(args.recipe), cancel_trial=args.cancel_trial
            )
        if args.command == "editor-file":
            params.update(
                uid=args.uid, action=args.action, relative_path=args.path, save_job_id=args.save_job
            )
        if args.command == "generate-animation":
            params.update(
                expected_uid=args.uid,
                spec=load_json(args.spec),
                relative_path=args.path,
                model_relative_path=args.model,
            )
        if args.command == "verify-runtime":
            params["options"] = load_json(args.options)
        return await engine.run(args.command.replace("-", "_"), **params)
    finally:
        await engine.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    settings = settings_from_env()
    for field in ("port", "state_dir", "output_dir"):
        if getattr(args, field, None) is not None:
            setattr(settings, field, getattr(args, field))
    if args.command == "mcp":
        from .server import make_server

        make_server(settings).run(transport="stdio")
        return
    if args.command == "capabilities":
        print(json.dumps(registry(), ensure_ascii=False, indent=2))
        return
    try:
        result = (
            validate_bundle(str(args.model))
            if args.command == "validate-bundle"
            else asyncio.run(execute(args, settings))
        )
        print(result.model_dump_json(indent=2))
        if result.status in {"blocked", "failed", "cancelled"} or (
            result.status == "rolled_back" and not result.postconditions_passed
        ):
            raise SystemExit(2)
    except (ValueError, OSError) as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": {"code": "INVALID_INPUT", "message": str(exc)}}
            )
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
