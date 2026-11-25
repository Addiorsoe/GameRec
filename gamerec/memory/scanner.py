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
        import sys

        if sys.platform != "win32":  # test backend
            from .procmem import ProcMem

            self.pm = ProcMem(pid)
        else:
            import pymem  # noqa: PLC0415

            self.pm = pymem.Pymem()
            self.pm.open_process_from_id(pid)
        self.vtype = vtype
        self.size = size_of(vtype)
        self.results: dict[int, object] = {}

    def _regions(self):
        """Yield (base, size) of committed, writable, non-guard memory regions."""
        if hasattr(self.pm, "regions"):
            yield from self.pm.regions()
            return
        import pymem.exception
        import pymem.memory

        addr, end = 0, 0x7FFFFFFF0000
        while addr < end:
            try:
                mbi = pymem.memory.virtual_query(self.pm.process_handle, addr)
            except pymem.exception.WinAPIError:
                break
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if size <= 0:
                break
            writable = mbi.Protect & (0x04 | 0x08 | 0x40 | 0x80)  # RW, WRITECOPY, EXEC_RW, EXEC_WRITECOPY
            if mbi.State == 0x1000 and writable and not mbi.Protect & 0x100:  # MEM_COMMIT, not PAGE_GUARD
                yield base, size
            addr = base + size

    def first_scan(self, value, align: int = 4, limit: int = 2_000_000) -> int:
        needle = encode(self.vtype, value)
        self.results.clear()
        chunk = 32 * 1024 * 1024
        for base, size in self._regions():
            for off in range(0, size, chunk):
                try:
                    buf = self.pm.read_bytes(base + off, min(chunk, size - off))
                except Exception:  # noqa: BLE001 - page vanished meanwhile
                    continue
                i = buf.find(needle)
                while i != -1:
                    if i % align == 0:
                        self.results[base + off + i] = value
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
