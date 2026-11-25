"""Unknown-initial-value scanner (Cheat Engine style "unknown initial value" + changed/unchanged).

Used by the "找座標精靈": snapshot every plausible value in the target's writable memory,
then narrow with changed / unchanged / increased / decreased steps. It needs no known value.

Scales to multi-GB processes: candidates are kept as numpy arrays per memory chunk
(offset uint32 + value), and the first snapshot spills to temp files when it is large.
The scanner only reports what memory did; it never decides what a value means.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("gamerec")

DTYPES = {"float": np.dtype("<f4"), "double": np.dtype("<f8"), "int32": np.dtype("<i4")}
MODES = ("changed", "unchanged", "increased", "decreased")
CHUNK = 32 * 1024 * 1024
MAX_ABS = 1e7          # coordinates beyond this are implausible
MIN_ABS_FLOAT = 1e-30  # drops denormals (mostly raw bytes that aren't floats)
SPILL_AT = 16_000_000  # candidates kept in RAM before spilling to temp files (~128 MB)


class ProcessMemory:
    """Readable+writable committed regions of a process (private, mapped and image data)."""

    def __init__(self, pid: int) -> None:
        import pymem  # noqa: PLC0415

        self.pm = pymem.Pymem()
        self.pm.open_process_from_id(pid)

    def regions(self):
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

    def read(self, addr: int, size: int) -> bytes | None:
        try:
            return self.pm.read_bytes(addr, size)
        except Exception:  # noqa: BLE001 - page freed or protection changed meanwhile
            return None

    def close(self) -> None:
        try:
            self.pm.close_process()
        except Exception:  # noqa: BLE001
            pass


@dataclass
class _Chunk:
    base: int
    idx: np.ndarray   # element offsets inside the chunk (uint32)
    vals: np.ndarray  # last seen values


class UnknownValueScanner:
    def __init__(self, mem, vtype: str = "float", align: int = 4, spill_at: int = SPILL_AT) -> None:
        if vtype not in DTYPES:
            raise ValueError(f"unsupported type {vtype}")
        self.mem = mem
        self.vtype = vtype
        self.dtype = DTYPES[vtype]
        self.align = align
        self.spill_at = spill_at
        self.chunks: list[_Chunk] = []
        self.history: list[dict] = []  # [{step, mode, count}] for the log / report
        self._tmp: Path | None = None
        self._n_spill = 0

    # ------------------------------------------------------------ helpers
    @property
    def count(self) -> int:
        return int(sum(len(c.idx) for c in self.chunks))

    def _plausible(self, v: np.ndarray) -> np.ndarray:
        if self.vtype == "int32":
            return (v != 0) & (np.abs(v.astype(np.int64)) <= MAX_ABS)
        with np.errstate(invalid="ignore", over="ignore"):
            a = np.abs(v)
            return np.isfinite(v) & (a <= MAX_ABS) & (a >= MIN_ABS_FLOAT)

    def _view(self, buf: bytes, start: int = 0) -> np.ndarray:
        """Values at every `align` bytes of buf (start = byte offset where buf begins)."""
        isz = self.dtype.itemsize
        if self.align == isz:
            return np.frombuffer(buf, dtype=self.dtype, count=len(buf) // isz)
        raw = np.frombuffer(buf, dtype=np.uint8)
        n = (len(raw) - isz) // self.align + 1
        if n <= 0:
            return np.empty(0, self.dtype)
        return np.lib.stride_tricks.as_strided(raw, shape=(n, isz), strides=(self.align, 1)).copy().view(self.dtype).ravel()

    def _spill(self, arr: np.ndarray) -> np.ndarray:
        if self._tmp is None:
            self._tmp = Path(tempfile.mkdtemp(prefix="gamerec_scan_"))
        self._n_spill += 1
        p = self._tmp / f"{self._n_spill}.npy"
        np.save(p, arr)
        return np.load(p, mmap_mode="r")

    # -------------------------------------------------------------- scans
    def first_scan(self) -> int:
        """Snapshot every plausible value (unknown initial value)."""
        self.close_temp()
        self.chunks, self.history = [], []
        total, spilling = 0, False
        for base, size in self.mem.regions():
            for off in range(0, size, CHUNK):
                buf = self.mem.read(base + off, min(CHUNK, size - off))
                if not buf:
                    continue
                v = self._view(buf)
                keep = np.nonzero(self._plausible(v))[0].astype(np.uint32)
                if not len(keep):
                    continue
                vals = v[keep]
                total += len(keep)
                if total > self.spill_at:
                    spilling = True
                if spilling:
                    keep, vals = self._spill(keep), self._spill(vals)
                self.chunks.append(_Chunk(base + off, keep, vals))
        self._record("first", total)
        return total

    def next_scan(self, mode: str) -> int:
        if mode not in MODES:
            raise ValueError(mode)
        out: list[_Chunk] = []
        for c in self.chunks:
            new = self._read_at(c)
            if new is None:
                continue
            old = np.asarray(c.vals)
            with np.errstate(invalid="ignore"):
                if mode == "changed":
                    m = new != old
                elif mode == "unchanged":
                    m = new == old
                elif mode == "increased":
                    m = new > old
                else:
                    m = new < old
            m &= self._plausible(new)
            if m.any():
                out.append(_Chunk(c.base, np.asarray(c.idx)[m].copy(), new[m].copy()))
        c = old = None  # noqa: F841 - release references to spilled arrays
        self.chunks = out  # results are small after a filter: back in RAM, temp files freed
        self.close_temp()
        n = self.count
        self._record(mode, n)
        return n

    # ---- windowed scans: sample repeatedly while the player acts, then filter once
    def begin_window(self, rebase: bool = True) -> None:
        """Start a window: values now are the reference; sample() records any change after this.

        rebase=False keeps the stored values as the reference: use it right after first_scan so
        the (possibly huge, spilled) snapshot is never copied into RAM."""
        if rebase:
            self.rebase()
        self._any = [np.zeros(len(c.idx), bool) for c in self.chunks]

    def sample(self) -> None:
        for c, any_ in zip(self.chunks, self._any):
            new = self._read_at(c)
            if new is None:
                any_[:] = True  # unreadable chunk: treat as changed (dropped by "unchanged")
                continue
            with np.errstate(invalid="ignore"):
                any_ |= new != np.asarray(c.vals)

    def end_window(self, mode: str) -> int:
        """mode "changed": changed at ANY sample (walking a loop changes every axis at some point,
        even if the player ends near the start). "unchanged": never changed at any sample."""
        self.sample()
        out = []
        for c, any_ in zip(self.chunks, self._any):
            new = self._read_at(c)
            if new is None:
                continue
            m = (any_ if mode == "changed" else ~any_) & self._plausible(new)
            if m.any():
                out.append(_Chunk(c.base, np.asarray(c.idx)[m].copy(), new[m].copy()))
        self.chunks, self._any = out, []
        n = self.count
        self._record(f"{mode} (window)", n)
        return n

    def rebase(self) -> int:
        """Refresh stored values without filtering (start of a new comparison window)."""
        out = []
        for c in self.chunks:
            new = self._read_at(c)
            if new is not None:
                out.append(_Chunk(c.base, np.asarray(c.idx).copy(), new))
        c = None  # noqa: F841
        self.chunks = out
        self.close_temp()
        return self.count

    def _read_at(self, c: _Chunk) -> np.ndarray | None:
        idx = np.asarray(c.idx)
        if not len(idx):
            return None
        lo, hi = int(idx[0]) * self.align, int(idx[-1]) * self.align + self.dtype.itemsize
        buf = self.mem.read(c.base + lo, hi - lo)  # only the span that still holds candidates
        if not buf or len(buf) < hi - lo:
            return None
        return self._view(buf)[idx - idx[0]]

    # ------------------------------------------------------------ results
    def results(self, limit: int = 1000) -> list[tuple[int, float | int]]:
        out = []
        for c in self.chunks:
            for i, v in zip(np.asarray(c.idx), np.asarray(c.vals)):
                out.append((c.base + int(i) * self.align, v.item()))
                if len(out) >= limit:
                    return out
        return out

    def read_values(self, addrs: list[int]) -> list[float | int | None]:
        out = []
        for a in addrs:
            b = self.mem.read(a, self.dtype.itemsize)
            out.append(np.frombuffer(b, self.dtype)[0].item() if b and len(b) == self.dtype.itemsize else None)
        return out

    def _record(self, mode: str, n: int) -> None:
        self.history.append({"step": len(self.history) + 1, "mode": mode, "count": n})
        log.info("finder %s %s -> %d candidates", self.vtype, mode, n)

    def close_temp(self) -> None:
        if self._tmp is not None:
            import gc

            gc.collect()  # drop memory-mapped arrays first: Windows can't delete mapped files
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
