from __future__ import annotations

import re
import sys
from pathlib import Path

import psutil


def installation_info(root: Path) -> dict:
    """Read launcher metadata without executing any launcher or changing Editor files."""
    launcher = root / "CubismEditor5.bat"
    version = None
    if launcher.is_file():
        text = launcher.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"Live2D Cubism ver\s+([^\r\n]+)", text)
        version = match.group(1).strip() if match else None
    return {
        "install_dir": str(root),
        "version": version,
        "version_source": str(launcher) if version else None,
    }


def listener_info(port: int) -> dict:
    """Bind version evidence to the process that owns the actual loopback API port."""
    try:
        listeners = [
            c
            for c in psutil.net_connections(kind="tcp")
            if c.status == psutil.CONN_LISTEN
            and c.laddr.port == port
            and c.laddr.ip in {"127.0.0.1", "0.0.0.0", "::1", "::"}
        ]
        if not listeners:
            return {"found": False}
        conn = listeners[0]
        proc = psutil.Process(conn.pid)
        exe = Path(proc.exe()).resolve()
        info = {
            "found": True,
            "pid": conn.pid,
            "process_created": proc.create_time(),
            "executable": str(exe),
            "version": None,
            "cubism_process": False,
        }
        # Cubism's Java executable lives under INSTALL/app/jre/bin.
        roots = [exe.parent, *exe.parents][:6]
        for root in roots:
            if (root / "CubismEditor5.exe").is_file() and (
                root / "app" / "lib" / "Live2D_Cubism.jar"
            ).is_file():
                cmdline = proc.cmdline()
                if exe.name.lower() == "cubismeditor5.exe" or any(
                    "com.live2d.cubism.CECubismEditorApp" in a for a in cmdline
                ):
                    info.update(installation_info(root))
                    info["cubism_process"] = True
                    break
        return info
    except (psutil.Error, OSError) as exc:
        return {"found": None, "diagnostic": type(exc).__name__, "version": None}


def environment_report(port: int) -> dict:
    installs = []
    if sys.platform == "win32":
        # Read immediate children only; never scan all user content.
        for root in (Path("D:/"), Path("C:/Program Files"), Path("C:/Program Files (x86)")):
            if root.is_dir():
                for child in root.iterdir():
                    if (
                        child.is_dir()
                        and "cubism" in child.name.lower()
                        and (child / "CubismEditor5.exe").is_file()
                    ):
                        installs.append(installation_info(child))
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "listener": listener_info(port),
        "installed_editors": installs,
    }
