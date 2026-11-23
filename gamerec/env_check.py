"""Environment checks shown in the GUI (admin rights, Npcap driver)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NPCAP_URL = "https://npcap.com/#download"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_admin() -> bool:
    if not is_windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def npcap_installed() -> bool:
    """True if the Npcap (or legacy WinPcap) packet driver DLL is present."""
    if not is_windows():
        return True  # libpcap is used on other platforms; not our target
    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [
        sysroot / "System32" / "Npcap" / "wpcap.dll",
        sysroot / "System32" / "wpcap.dll",  # WinPcap-compatible mode
    ]
    return any(p.exists() for p in candidates)


def default_output_dir() -> Path:
    """Documents\\GameRec\\sessions (falls back to home)."""
    home = Path.home()
    docs = home / "Documents"
    return (docs if docs.exists() else home) / "GameRec" / "sessions"


def open_folder(path: str | Path) -> None:
    path = str(path)
    if is_windows():
        os.startfile(path)  # noqa: S606
    else:
        import subprocess

        subprocess.Popen(["xdg-open", path])  # noqa: S603,S607
