"""Build the recorder list from simple options (shared by CLI and GUI)."""
from __future__ import annotations

from .inputs.logger import InputLogger
from .memory.watcher import MemoryWatcher, WatchEntry
from .net.capture import PacketCapture
from .recorder import Recorder
from .video.recorder import ScreenRecorder


def make_recorders(
    watch: list[WatchEntry] | None = None,
    memory: bool = True,
    network: bool = True,
    video: bool = True,
    inputs: bool = True,
    rate_hz: float = 30.0,
    fps: float = 15.0,
    scale: float = 0.75,
    iface: str | None = None,
) -> list[Recorder]:
    recs: list[Recorder] = []
    if memory and watch:
        recs.append(MemoryWatcher(watch, rate_hz=rate_hz))
    if network:
        recs.append(PacketCapture(iface=iface))
    if video:
        recs.append(ScreenRecorder(fps=fps, scale=scale))
    if inputs:
        recs.append(InputLogger())
    return recs
