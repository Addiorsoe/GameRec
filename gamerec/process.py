"""Process / window / connection helpers (Windows-specific bits are imported lazily)."""
from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class TargetInfo:
    pid: int
    name: str
    exe: str = ""
    window_title: str = ""
    hwnd: int = 0

    def to_dict(self) -> dict:
        d = {"pid": self.pid, "name": self.name, "exe": self.exe, "window_title": self.window_title}
        rect = window_rect(self.hwnd) if self.hwnd else None
        if rect:
            d["window_rect"] = list(rect)
        return d


def list_processes() -> list[tuple[int, str]]:
    import psutil

    out = []
    for p in psutil.process_iter(["pid", "name"]):
        if p.info["name"]:
            out.append((p.info["pid"], p.info["name"]))
    return sorted(out, key=lambda x: x[1].lower())


def get_target(pid: int) -> TargetInfo:
    import psutil

    p = psutil.Process(pid)
    try:
        exe = p.exe()
    except (psutil.AccessDenied, psutil.ZombieProcess):
        exe = ""
    hwnd, title = find_main_window(pid)
    return TargetInfo(pid=pid, name=p.name(), exe=exe, window_title=title, hwnd=hwnd)


def find_target_by_name(name: str) -> TargetInfo:
    for pid, pname in list_processes():
        if pname.lower() == name.lower():
            return get_target(pid)
    raise LookupError(f"process not found: {name}")


def target_from_foreground() -> TargetInfo | None:
    """The process that owns the window the user is looking at right now (Windows only)."""
    if sys.platform != "win32":
        return None
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    return target_from_hwnd(hwnd) if hwnd else None


def target_from_hwnd(hwnd: int) -> TargetInfo:
    import psutil
    import win32gui
    import win32process

    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    p = psutil.Process(pid)
    try:
        exe = p.exe()
    except (psutil.AccessDenied, psutil.ZombieProcess):
        exe = ""
    return TargetInfo(pid=pid, name=p.name(), exe=exe, window_title=win32gui.GetWindowText(hwnd), hwnd=hwnd)


def find_main_window(pid: int) -> tuple[int, str]:
    """Return (hwnd, title) of the largest visible window owned by pid."""
    if sys.platform != "win32":
        return 0, ""
    import win32gui
    import win32process

    best: list = [0, "", 0]

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        if wpid != pid:
            return True
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        area = max(0, r - l) * max(0, b - t)
        if area > best[2]:
            best[:] = [hwnd, win32gui.GetWindowText(hwnd), area]
        return True

    win32gui.EnumWindows(cb, None)
    return best[0], best[1]


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Client-area rect in screen coords: (left, top, width, height)."""
    if sys.platform != "win32" or not hwnd:
        return None
    import win32gui

    try:
        l, t, r, b = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (l, t))
    except Exception:  # noqa: BLE001 - window closed
        return None
    return x, y, r - l, b - t


def local_ports(pid: int) -> set[tuple[str, int]]:
    """Set of (proto, local_port) currently used by pid. proto is 'tcp' or 'udp'."""
    import socket

    import psutil

    ports: set[tuple[str, int]] = set()
    try:
        conns = psutil.Process(pid).net_connections(kind="inet")
    except AttributeError:  # psutil < 6
        conns = psutil.Process(pid).connections(kind="inet")
    for c in conns:
        if c.laddr:
            proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
            ports.add((proto, c.laddr.port))
    return ports
