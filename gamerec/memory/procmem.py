"""Linux /proc/<pid>/mem backend (used for testing GameRec off Windows; needs root/ptrace)."""
from __future__ import annotations

import os


class ProcMem:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)

    def read_bytes(self, addr: int, size: int) -> bytes:
        data = os.pread(self._fd, size, addr)
        if len(data) != size:
            raise OSError(f"short read at {hex(addr)}")
        return data

    def regions(self, writable_only: bool = True):
        with open(f"/proc/{self.pid}/maps") as f:
            for line in f:
                parts = line.split()
                lo, hi = (int(x, 16) for x in parts[0].split("-"))
                perms = parts[1]
                if perms[0] != "r" or (writable_only and perms[1] != "w"):
                    continue
                if len(parts) > 5 and parts[5] in ("[vvar]", "[vsyscall]"):
                    continue
                yield lo, hi - lo

    def module_base(self, name: str) -> int:
        with open(f"/proc/{self.pid}/maps") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 5 and os.path.basename(parts[5]) == name:
                    return int(parts[0].split("-")[0], 16)
        raise LookupError(f"module not found: {name}")

    def close(self) -> None:
        os.close(self._fd)
