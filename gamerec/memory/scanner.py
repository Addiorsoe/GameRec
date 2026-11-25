"""Unknown-initial-value scanner for the "找座標精靈" (coordinate finder).

First pass (walk_scan) streams through the target's writable memory while the player keeps
walking: every batch (~256 MB) is read twice, a fraction of a second apart, and only values
that changed in between are kept. Nothing like a full-memory snapshot is ever stored, so it
stays fast and light on multi-GB games (the earlier snapshot design spilled ~11 GB to disk on
a simulated 8 GB game and then re-read all of it on every sample).

Later passes (windows) sample the remaining candidates while the player stands still or walks.
The scanner only reports what memory did; it never decides what a value means.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("gamerec")

DTYPES = {"float": np.dtype("<f4"), "double": np.dtype("<f8"), "int32": np.dtype("<i4")}
MODES = ("changed", "unchanged", "increased", "decreased")
CHUNK = 32 * 1024 * 1024
BATCH = 256 * 1024 * 1024  # bytes held in RAM at once during walk_scan
GAP = 0.3                  # seconds between the two reads of a batch
SPLIT = 64 * 1024          # candidates further apart than this are read separately
MAX_ABS = 1e7              # coordinates beyond this are implausible
MIN_ABS_FLOAT = 1e-30      # drops denormals (mostly raw bytes that aren't floats)


class Cancelled(Exception):
    pass


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
    idx: np.ndarray   # element offsets inside the chunk (uint32), sorted
    vals: np.ndarray  # last seen values


class UnknownValueScanner:
    def __init__(self, mem, vtype: str = "float", align: int = 4) -> None:
        if vtype not in DTYPES:
            raise ValueError(f"unsupported type {vtype}")
        self.mem = mem
        self.vtype = vtype
        self.dtype = DTYPES[vtype]
        self.align = align
        self.chunks: list[_Chunk] = []
        self.history: list[dict] = []  # [{step, mode, count, seconds}] for the log / report
        self.scan_stats: dict = {}
        self._any: list[np.ndarray] = []

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

    def _view(self, buf: bytes) -> np.ndarray:
        """Values at every `align` bytes of buf."""
        isz = self.dtype.itemsize
        if self.align == isz:
            return np.frombuffer(buf, dtype=self.dtype, count=len(buf) // isz)
        raw = np.frombuffer(buf, dtype=np.uint8)
        n = (len(raw) - isz) // self.align + 1
        if n <= 0:
            return np.empty(0, self.dtype)
        return np.lib.stride_tricks.as_strided(raw, shape=(n, isz), strides=(self.align, 1)).copy().view(self.dtype).ravel()

    def _record(self, mode: str, n: int, secs: float) -> None:
        self.history.append({"step": len(self.history) + 1, "mode": mode, "count": n, "seconds": round(secs, 2)})
        log.info("finder %s %s -> %d candidates (%.1fs)", self.vtype, mode, n, secs)

    # ---------------------------------------------------------- first pass
    def walk_scan(self, progress: Callable[[int, int], None] | None = None,
                  cancelled: Callable[[], bool] | None = None, gap: float = GAP,
                  walking_since: Callable[[float], bool] | None = None,
                  paused: Callable[[bool], None] | None = None) -> int:
        """Keep values that change while the player keeps walking (two reads `gap` s apart).

        walking_since(t): True if the player has been walking continuously since monotonic time t.
        When given (auto mode), a batch is only kept if the player walked through both of its
        reads; otherwise it waits for walking to resume and reads that batch again."""
        t0 = time.monotonic()
        regions = [(b, s) for b, s in self.mem.regions()]
        pieces = [(b + off, min(CHUNK, s - off)) for b, s in regions for off in range(0, s, CHUNK)]
        total = sum(n for _, n in pieces)
        done = read_ok = 0
        self.chunks, self.history, self._any = [], [], []
        i = 0
        while i < len(pieces):
            batch, size = [], 0
            while i < len(pieces) and (not batch or size + pieces[i][1] <= BATCH):
                batch.append(pieces[i])
                size += pieces[i][1]
                i += 1
            while True:
                if walking_since:
                    while not walking_since(time.monotonic()):
                        if paused:
                            paused(True)
                        if cancelled and cancelled():
                            raise Cancelled
                        time.sleep(0.1)
                    if paused:
                        paused(False)
                first = []
                t_first = time.monotonic()
                for addr, n in batch:
                    if cancelled and cancelled():
                        raise Cancelled
                    buf = self.mem.read(addr, n)
                    first.append(self._view(buf) if buf else None)
                wait = gap - (time.monotonic() - t_first)
                if wait > 0:
                    time.sleep(wait)
                if not walking_since or walking_since(t_first):
                    break  # the player walked through the first reads: compare now
            for (addr, n), old in zip(batch, first):
                done += n
                if old is None:
                    continue
                buf = self.mem.read(addr, n)
                if not buf or len(buf) != n:
                    continue
                read_ok += n
                new = self._view(buf)
                with np.errstate(invalid="ignore"):
                    m = (new != old) & self._plausible(new) & self._plausible(old)
                idx = np.nonzero(m)[0].astype(np.uint32)
                if len(idx):
                    self.chunks.append(_Chunk(addr, idx, new[idx].copy()))
            del first
            if progress:
                progress(done, total)
        self.scan_stats = {"regions": len(regions), "bytes_total": total, "bytes_readable": read_ok,
                           "seconds": round(time.monotonic() - t0, 1)}
        log.info("finder walk_scan: %s", self.scan_stats)
        if total and not read_ok:
            raise PermissionError("讀不到這個程式的任何記憶體：可能沒有以系統管理員執行，或被遊戲的防護機制擋住。")
        n = self.count
        self._record("changed while walking (first pass)", n, time.monotonic() - t0)
        return n

    # ------------------------------------------------------------ windows
    def begin_window(self) -> None:
        """Values now are the reference; sample() records any change after this."""
        self.rebase()
        self._any = [np.zeros(len(c.idx), bool) for c in self.chunks]
        self._t_window = time.monotonic()

    def sample(self, commit: bool = True) -> list[np.ndarray]:
        """Compare now with the window's reference. Returns the per-chunk "changed" masks;
        commit=False lets the caller decide later (auto mode drops samples taken around the
        moment the player started moving, which the screen detector only notices ~0.4 s late)."""
        masks = []
        for c, any_ in zip(self.chunks, self._any):
            new = self._read_at(c)
            if new is None:
                m = np.ones(len(c.idx), bool)  # unreadable now: treat as changed (dropped by "unchanged")
            else:
                with np.errstate(invalid="ignore"):
                    m = new != c.vals
            masks.append(m)
            if commit:
                any_ |= m
        return masks

    def commit(self, masks: list[np.ndarray]) -> None:
        for any_, m in zip(self._any, masks):
            any_ |= m

    def end_window(self, mode: str, sample_now: bool = True) -> int:
        """mode "changed": changed at ANY sample (walking a loop changes every axis at some point,
        even if the player ends near the start). "unchanged": never changed at any sample."""
        if sample_now:
            self.sample()
        out = []
        for c, any_ in zip(self.chunks, self._any):
            new = self._read_at(c)
            if new is None:
                continue
            m = (any_ if mode == "changed" else ~any_) & self._plausible(new)
            if m.any():
                out.append(_Chunk(c.base, c.idx[m], new[m]))
        self.chunks, self._any = out, []
        n = self.count
        self._record(f"{mode} (window)", n, time.monotonic() - getattr(self, "_t_window", time.monotonic()))
        return n

    def next_scan(self, mode: str) -> int:
        """Manual filter: compare now with the stored values."""
        if mode not in MODES:
            raise ValueError(mode)
        t0 = time.monotonic()
        out: list[_Chunk] = []
        for c in self.chunks:
            new = self._read_at(c)
            if new is None:
                continue
            with np.errstate(invalid="ignore"):
                m = {"changed": new != c.vals, "unchanged": new == c.vals,
                     "increased": new > c.vals, "decreased": new < c.vals}[mode]
            m &= self._plausible(new)
            if m.any():
                out.append(_Chunk(c.base, c.idx[m], new[m]))
        self.chunks = out
        n = self.count
        self._record(mode, n, time.monotonic() - t0)
        return n

    def rebase(self) -> int:
        out = []
        for c in self.chunks:
            new = self._read_at(c)
            if new is not None:
                out.append(_Chunk(c.base, c.idx, new))
        self.chunks = out
        return self.count

    def _read_at(self, c: _Chunk) -> np.ndarray | None:
        """Current values of a chunk's candidates. Reads only the spans that hold candidates
        (split where they are > SPLIT bytes apart), not the whole chunk."""
        idx = c.idx
        if not len(idx):
            return None
        isz = self.dtype.itemsize
        offs = idx.astype(np.int64) * self.align
        cuts = np.nonzero(np.diff(offs) > SPLIT)[0] + 1
        parts = []
        for seg in np.split(np.arange(len(idx)), cuts):
            lo, hi = int(offs[seg[0]]), int(offs[seg[-1]]) + isz
            buf = self.mem.read(c.base + lo, hi - lo)
            if not buf or len(buf) < hi - lo:
                return None
            raw = np.frombuffer(buf, np.uint8)
            rel = offs[seg] - lo
            parts.append(raw[(rel[:, None] + np.arange(isz)).ravel()].view(self.dtype))
        return np.concatenate(parts)

    # ------------------------------------------------------------ results
    def results(self, limit: int = 1000) -> list[tuple[int, float | int]]:
        out = []
        for c in self.chunks:
            for i, v in zip(c.idx, c.vals):
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
