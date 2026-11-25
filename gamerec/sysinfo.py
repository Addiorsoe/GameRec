"""One-time PC hardware scan, run together with the environment check.

Results are saved locally to Documents\\GameRec\\system_info.json and never sent anywhere.
AutoPath reads this file (e.g. to decide whether running the game in a virtual machine is feasible).
Only facts are recorded; no judgement is made here.

Everything is read from psutil, the registry and Win32 APIs: no PowerShell/WMI subprocess,
so the windowed exe never flashes a console and the scan takes well under a second.
"""
from __future__ import annotations

import json
import platform
import sys
import threading
import time
from pathlib import Path

GPU_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
PF_VIRT_FIRMWARE_ENABLED = 21
GB = 1024 ** 3
_LOCK = threading.Lock()  # startup scan and the environment window may ask at the same time


def info_path() -> Path:
    from .env_check import default_output_dir

    return default_output_dir().parent / "system_info.json"


# ------------------------------------------------------------- readers
def _reg_values(root, path: str) -> dict:
    import winreg

    out = {}
    with winreg.OpenKey(root, path) as k:
        i = 0
        while True:
            try:
                name, val, _ = winreg.EnumValue(k, i)
            except OSError:
                return out
            out[name] = val
            i += 1


def _cpu_name() -> str:
    if sys.platform == "win32":
        import winreg

        try:
            v = _reg_values(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(v.get("ProcessorNameString", "")).strip()
        except OSError:
            pass
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def _vram_bytes(v: dict) -> int | None:
    raw = v.get("HardwareInformation.qwMemorySize", v.get("HardwareInformation.MemorySize"))
    if isinstance(raw, bytes):
        raw = int.from_bytes(raw[:8], "little")
    return int(raw) if isinstance(raw, int) and raw > 0 else None


def _gpus() -> list[dict]:
    if sys.platform != "win32":
        return []
    import winreg

    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GPU_CLASS) as cls:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                try:
                    v = _reg_values(winreg.HKEY_LOCAL_MACHINE, f"{GPU_CLASS}\\{sub}")
                except OSError:
                    continue
                name = v.get("DriverDesc")
                if not name:
                    continue
                vram = _vram_bytes(v)
                out.append({"name": str(name), "driver": str(v.get("DriverVersion", "")),
                            "vram_gb": round(vram / GB, 1) if vram else None})
    except OSError:
        pass
    # the same adapter can appear once per driver instance
    seen, uniq = set(), []
    for g in out:
        if (g["name"], g["vram_gb"]) not in seen:
            seen.add((g["name"], g["vram_gb"]))
            uniq.append(g)
    return uniq


def _monitors() -> list[dict]:
    try:
        import mss

        with mss.mss() as s:
            return [{"width": m["width"], "height": m["height"], "left": m["left"], "top": m["top"]}
                    for m in s.monitors[1:]]
    except Exception:  # noqa: BLE001 - no display
        return []


def _disks() -> list[dict]:
    import psutil

    out = []
    for p in psutil.disk_partitions(all=False):
        if "cdrom" in p.opts or not p.fstype or p.fstype == "squashfs":
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        if u.total < GB:  # recovery / tiny system partitions
            continue
        out.append({"mount": p.mountpoint, "fs": p.fstype,
                    "total_gb": round(u.total / GB, 1), "free_gb": round(u.free / GB, 1)})
    return out


def _virtualization() -> dict:
    """Facts relevant to running a VM: firmware VT-x/AMD-V switch and whether a hypervisor is active."""
    out: dict = {"firmware_enabled": None, "hypervisor_present": None}
    if sys.platform == "win32":
        import ctypes

        try:
            out["firmware_enabled"] = bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(PF_VIRT_FIRMWARE_ENABLED))
        except Exception:  # noqa: BLE001
            pass
        # Hyper-V (or Windows' VBS) running: the firmware flag then reads False even though VT is on.
        try:
            import winreg

            v = _reg_values(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard")
            out["vbs_enabled"] = bool(v.get("EnableVirtualizationBasedSecurity", 0))
        except OSError:
            pass
        out["hypervisor_present"] = _hv_running()
    else:
        try:
            flags = Path("/proc/cpuinfo").read_text(encoding="utf-8")
            out["firmware_enabled"] = (" vmx" in flags) or (" svm" in flags)
            out["hypervisor_present"] = " hypervisor" in flags
        except OSError:
            pass
    return out


def _hv_running() -> bool:
    import psutil

    try:
        return any(p.info["name"] and p.info["name"].lower() == "vmcompute.exe"
                   for p in psutil.process_iter(["name"]))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------- scan
def scan() -> dict:
    import psutil

    vm = psutil.virtual_memory()
    freq = psutil.cpu_freq()
    return {
        "scanned_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "os": {"system": platform.system(), "release": platform.release(),
               "version": platform.version(), "machine": platform.machine()},
        "cpu": {"name": _cpu_name(), "cores": psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count(logical=True),
                "max_mhz": round(freq.max) if freq and freq.max else None},
        "ram": {"total_gb": round(vm.total / GB, 1), "available_gb": round(vm.available / GB, 1)},
        "gpus": _gpus(),
        "monitors": _monitors(),
        "disks": _disks(),
        "virtualization": _virtualization(),
    }


def load_or_scan(force: bool = False, path: Path | None = None) -> dict:
    """Return the saved scan; scan (once) only if there is none, it is unreadable, or force=True."""
    path = path or info_path()
    with _LOCK:
        return _load_or_scan(force, path)


def _load_or_scan(force: bool, path: Path) -> dict:
    if not force:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    data = scan()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # still usable in memory
    return data


def summary_lines(d: dict) -> list[tuple[str, str]]:
    """Human-readable (label, text) rows for the environment window."""
    cpu = d.get("cpu", {})
    gpus = d.get("gpus") or []
    v = d.get("virtualization", {})
    rows = [
        ("處理器", f"{cpu.get('name') or '未知'}（{cpu.get('cores')} 核 / {cpu.get('threads')} 執行緒）"),
        ("記憶體", f"{d.get('ram', {}).get('total_gb')} GB"),
        ("顯示卡", "；".join(f"{g['name']}" + (f"（{g['vram_gb']} GB）" if g.get("vram_gb") else "") for g in gpus) or "未偵測到"),
        ("螢幕", "；".join(f"{m['width']}×{m['height']}" for m in d.get("monitors") or []) or "未偵測到"),
        ("硬碟", "；".join(f"{x['mount']} 剩 {x['free_gb']} / {x['total_gb']} GB" for x in d.get("disks") or []) or "未偵測到"),
        ("虛擬化（BIOS）", _virt_text(v)),
    ]
    return rows


def _virt_text(v: dict) -> str:
    fw, hv = v.get("firmware_enabled"), v.get("hypervisor_present") or v.get("vbs_enabled")
    if fw is None:
        return "無法判斷"
    if fw:
        return "已開啟"
    # Windows reports False here while Hyper-V / VBS owns the CPU's virtualization
    return "無法判斷（Hyper-V 或核心隔離執行中，Windows 不回報）" if hv else "未開啟"
