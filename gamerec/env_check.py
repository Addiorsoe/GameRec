"""Environment checks + one-click fixes, shown automatically by the GUI at startup."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__

REPO = "Addiorsoe/gamerec"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
NPCAP_URL = "https://npcap.com/#download"
NPCAP_DIST = "https://npcap.com/dist/"
NPCAP_FALLBACK = "npcap-1.80.exe"
UA = {"User-Agent": "GameRec-updater"}


# --------------------------------------------------------------- basics
def is_windows() -> bool:
    return sys.platform == "win32"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_admin() -> bool:
    if not is_windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def windows_build() -> int:
    """Windows build number (10240+ = Windows 10, 22000+ = Windows 11); 0 if not Windows."""
    if not is_windows():
        return 0
    return sys.getwindowsversion().build


def npcap_installed() -> bool:
    """True if the Npcap (or legacy WinPcap) packet driver DLL is present."""
    if not is_windows():
        return True  # libpcap is used on other platforms; not our target
    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return any(
        p.exists()
        for p in (sysroot / "System32" / "Npcap" / "wpcap.dll", sysroot / "System32" / "wpcap.dll")
    )


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


# ---------------------------------------------------------------- fixes
def relaunch_as_admin() -> bool:
    """Restart this program elevated (UAC prompt). Returns True if the new process started."""
    import ctypes

    if is_frozen():
        exe, params = sys.executable, " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe, params = sys.executable, "-m gamerec " + " ".join(f'"{a}"' for a in sys.argv[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def _http_get(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URLs
        return r.read()


def latest_npcap_installer() -> str:
    """Filename of the newest Npcap installer listed on npcap.com/dist (fallback: pinned version)."""
    for page in (NPCAP_DIST, "https://npcap.com/"):
        try:
            html = _http_get(page).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        names = set(re.findall(r"npcap-(\d+\.\d+)\.exe", html))
        if names:
            best = max(names, key=lambda v: tuple(int(x) for x in v.split(".")))
            return f"npcap-{best}.exe"
    return NPCAP_FALLBACK


def download(url: str, dest: Path, progress: Callable[[int, int], None] | None = None) -> Path:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:  # noqa: S310
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(64 * 1024):
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return dest


def install_npcap(progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the official Npcap installer and launch it (the user clicks through it).

    The free Npcap edition has no silent mode, so the installer window will appear;
    remind the user to tick "WinPcap API-compatible Mode".
    """
    name = latest_npcap_installer()
    dest = Path(tempfile.gettempdir()) / name
    download(NPCAP_DIST + name, dest, progress)
    os.startfile(str(dest))  # noqa: S606 - we're already elevated, installer asks if not
    return dest


# -------------------------------------------------------------- updates
def _ver(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3]) or (0,)


def check_update() -> dict | None:
    """Return {"version", "url", "exe_url"} if a newer GitHub release exists, else None."""
    data = json.loads(_http_get(RELEASES_API, timeout=8))
    tag = data.get("tag_name", "")
    if _ver(tag) <= _ver(__version__):
        return None
    exe = next((a["browser_download_url"] for a in data.get("assets", []) if a["name"].lower().endswith(".exe")), None)
    return {"version": tag, "url": data.get("html_url", RELEASES_URL), "exe_url": exe}


def download_update(info: dict, progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the new exe next to the current one as GameRec-<ver>.exe."""
    folder = Path(sys.executable).parent if is_frozen() else default_output_dir().parent
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"GameRec-{info['version']}.exe"
    return download(info["exe_url"], dest, progress)


# --------------------------------------------------------------- checks
@dataclass
class Check:
    key: str
    name: str
    ok: bool
    detail: str
    required: bool = True          # False = optional feature (shown as warning)
    fix_label: str | None = None   # button text for a one-click fix


def run_checks() -> list[Check]:
    out: list[Check] = []
    build = windows_build()
    if is_windows():
        win11 = build >= 22000
        out.append(Check("os", "Windows 版本", build >= 10240,
                         f"Windows {'11' if win11 else '10' if build >= 10240 else platform.release()}（build {build}）"
                         + ("" if build >= 10240 else "，需要 Windows 10 以上")))
    else:
        out.append(Check("os", "作業系統", False, f"{platform.system()}：本工具僅支援 Windows 10/11"))
    is64 = platform.machine().endswith("64")
    out.append(Check("arch", "64 位元系統", is64, platform.machine() or "unknown"))
    admin = is_admin()
    out.append(Check("admin", "系統管理員權限", admin,
                     "已取得" if admin else "未取得：無法讀取遊戲記憶體、無法抓封包",
                     fix_label=None if admin else "以管理員重新啟動"))
    npcap = npcap_installed()
    out.append(Check("npcap", "Npcap 封包驅動", npcap,
                     "已安裝" if npcap else "未安裝：封包功能停用（記憶體與錄影照常）",
                     required=False, fix_label=None if npcap else "一鍵下載安裝"))
    return out


def all_required_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks if c.required)
