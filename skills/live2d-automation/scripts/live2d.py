"""Forward CLI arguments to the installed package runtime without shell interpolation."""

import json
import os
import subprocess
import sys
from pathlib import Path

runtime = Path(__file__).resolve().parents[1] / "runtime.json"
config = json.loads(runtime.read_text(encoding="utf-8")) if runtime.is_file() else {}
python = config.get("python", sys.executable)
env = dict(os.environ)
if config.get("node_modules"):
    env.setdefault("LIVE2D_NODE_MODULES", config["node_modules"])
raise SystemExit(
    subprocess.call([python, "-X", "utf8", "-m", "live2d_automation.cli", *sys.argv[1:]], env=env)
)
