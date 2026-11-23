"""Cheat-Engine style value scanner: first scan, then narrow with next scans."""
from __future__ import annotations

from collections.abc import Callable

from .types import decode, encode, size_of

Predicate = Callable[[object, object], bool]  # (old, new) -> keep?

PREDICATES: dict[str, Predicate] = {
    "changed": lambda o, n: o != n,
    "unchanged": lambda o, n: o == n,
    "increased": lambda o, n: n > o,
    "decreased": lambda o, n: n < o,
}


class ValueScanner:
    def __init__(self, pid: int, vtype: str = "int32") -> None:
        import pymem  # noqa: PLC0415

        self.pm = pymem.Pymem()
        self.pm.open_process_from_id(pid)
        self.vtype = vtype
        self.size = size_of(vtype)
        self.results: dict[int, object] = {}

    def _regions(self):
        """Yield (base, size) of committed, readable+writable private/image memory."""
        import pymem.ressources.kernel32 as k32
        import pymem.ressources.structure as st

        addr, end = 0, 0x7FFFFFFFFFFF
        mbi = st.MEMORY_BASIC_INFORMATION()
        import ctypes

        while addr < end:
            if k32.VirtualQueryEx(self.pm.process_handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            writable = mbi.Protect & 0x04 or mbi.Protect & 0x40  # PAGE_READWRITE | PAGE_EXECUTE_READWRITE
            if mbi.State == 0x1000 and writable and not mbi.Protect & 0x100:  # MEM_COMMIT, not GUARD
                yield base, size
            addr = base + size

    def first_scan(self, value, align: int = 4, limit: int = 2_000_000) -> int:
        needle = encode(self.vtype, value)
        self.results.clear()
        for base, size in self._regions():
            try:
                buf = self.pm.read_bytes(base, size)
            except Exception:  # noqa: BLE001
                continue
            i = buf.find(needle)
            while i != -1:
                if i % align == 0:
                    self.results[base + i] = value
                    if len(self.results) >= limit:
                        return len(self.results)
                i = buf.find(needle, i + 1)
        return len(self.results)

    def next_scan(self, mode: str = "exact", value=None) -> int:
        keep: dict[int, object] = {}
        for addr, old in self.results.items():
            try:
                new = decode(self.vtype, self.pm.read_bytes(addr, self.size))
            except Exception:  # noqa: BLE001
                continue
            ok = (new == value) if mode == "exact" else PREDICATES[mode](old, new)
            if ok:
                keep[addr] = new
        self.results = keep
        return len(keep)
