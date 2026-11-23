"""Poll a watch list of addresses at a fixed rate and write memory/samples.csv."""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field

from ..recorder import Recorder
from .types import decode, parse_int, size_of


@dataclass
class WatchEntry:
    name: str
    type: str = "int32"
    address: int | None = None          # absolute
    module: str | None = None           # module base +
    offset: int | None = None           #   static offset
    offsets: list[int] = field(default_factory=list)  # or pointer chain
    resolved: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> WatchEntry:
        return cls(
            name=d["name"],
            type=d.get("type", "int32"),
            address=parse_int(d["address"]) if d.get("address") is not None else None,
            module=d.get("module"),
            offset=parse_int(d["offset"]) if d.get("offset") is not None else None,
            offsets=[parse_int(o) for o in d.get("offsets", [])],
        )

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "type": self.type}
        if self.address is not None:
            d["address"] = hex(self.address)
        if self.module:
            d["module"] = self.module
        if self.offset is not None:
            d["offset"] = hex(self.offset)
        if self.offsets:
            d["offsets"] = [hex(o) for o in self.offsets]
        if self.resolved is not None:
            d["resolved"] = hex(self.resolved)
        return d


def load_watchlist(path) -> list[WatchEntry]:
    with open(path, encoding="utf-8") as f:
        return [WatchEntry.from_dict(d) for d in json.load(f)]


class MemoryReader:
    """Thin wrapper over pymem so it can be faked in tests."""

    def __init__(self, pid: int) -> None:
        import pymem  # noqa: PLC0415 - Windows only

        self.pm = pymem.Pymem()
        self.pm.open_process_from_id(pid)
        import pymem.process

        self.is_64 = bool(pymem.process.is_64_bit(self.pm.process_handle))

    def module_base(self, name: str) -> int:
        import pymem.process

        mod = pymem.process.module_from_name(self.pm.process_handle, name)
        if mod is None:
            raise LookupError(f"module not found: {name}")
        return mod.lpBaseOfDll

    def read(self, addr: int, size: int) -> bytes:
        return self.pm.read_bytes(addr, size)

    def read_ptr(self, addr: int) -> int:
        size = 8 if self.is_64 else 4
        return int.from_bytes(self.read(addr, size), "little")

    def close(self) -> None:
        self.pm.close_process()


def resolve(entry: WatchEntry, reader) -> int:
    if entry.address is not None and not entry.module:
        return entry.address
    base = reader.module_base(entry.module) if entry.module else 0
    if len(entry.offsets) == 1:
        return base + entry.offsets[0]
    if entry.offsets:
        # Cheat Engine style: [[base+o0]+o1]+...+o_last
        addr = reader.read_ptr(base + entry.offsets[0])
        for off in entry.offsets[1:-1]:
            addr = reader.read_ptr(addr + off)
        return addr + entry.offsets[-1]
    return base + (entry.offset or 0)


class MemoryWatcher(Recorder):
    kind = "memory"

    def __init__(self, entries: list[WatchEntry], rate_hz: float = 30.0, reader_factory=None) -> None:
        super().__init__()
        self.entries = entries
        self.rate_hz = rate_hz
        self.extra = {"rate_hz": rate_hz}
        self._reader_factory = reader_factory or MemoryReader
        self._has_chain = any(e.offsets for e in entries)

    def setup(self) -> None:
        assert self.session is not None
        self.reader = self._reader_factory(self.session.target.pid)
        for e in self.entries:
            try:
                e.resolved = resolve(e, self.reader)
            except Exception:  # noqa: BLE001 - keep going; logged as empty values
                e.resolved = None
        with open(self.path("memory/watchlist.json"), "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in self.entries], f, indent=2, ensure_ascii=False)
        self._fh = open(self.path("memory/samples.csv"), "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._fh)
        self._csv.writerow(["t_ns", "name", "value"])

    def run(self) -> None:
        period = 1.0 / self.rate_hz
        last: dict[str, object] = {}
        next_t = time.perf_counter()
        while not self.stopping:
            t = self.now_ns()
            for e in self.entries:
                if self._has_chain and e.offsets:
                    try:  # pointer chains can move (e.g. map change); re-resolve each tick
                        e.resolved = resolve(e, self.reader)
                    except Exception:  # noqa: BLE001
                        e.resolved = None
                if e.resolved is None:
                    continue
                try:
                    v = decode(e.type, self.reader.read(e.resolved, size_of(e.type)))
                except Exception:  # noqa: BLE001 - unreadable this tick
                    v = None
                # Write only on change to keep files small (first sample always written).
                if e.name not in last or last[e.name] != v:
                    self._csv.writerow([t, e.name, "" if v is None else v])
                    last[e.name] = v
                    self.count += 1
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_t = time.perf_counter()

    def teardown(self) -> None:
        if getattr(self, "_fh", None):
            self._fh.close()
        if getattr(self, "reader", None) and hasattr(self.reader, "close"):
            self.reader.close()
