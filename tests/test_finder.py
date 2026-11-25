"""Coordinate finder core, against fake process memory (4 MB of noise + a walking player)."""
import math

import numpy as np
import pytest

from gamerec.memory import scanner as scanner_mod
from gamerec.memory.scanner import Cancelled, UnknownValueScanner


class FakeMem:
    """Every read() is a moment in game time: the timer ticks; x/y walk a circle while walking."""

    def __init__(self, seed=1):
        rng = np.random.default_rng(seed)
        self.regions_ = {}
        for base in (0x10000000, 0x20000000):
            buf = rng.integers(0, 256, 2 * 1024 * 1024, dtype=np.uint8)
            floats = buf[: 1024 * 1024].view("<f4")
            floats[::3] = rng.uniform(-5000, 5000, len(floats[::3]))   # plausible constants
            self.regions_[base] = buf
        f = self.f32()
        self.px, self.py, self.pz, self.cam_x, self.timer = 1000, 1001, 1002, 5000, 7000
        f[self.px], f[self.py], f[self.pz], f[self.cam_x], f[self.timer] = 120.5, -33.25, 4.0, 120.5, 1.0
        self.walking = False
        self.t = 0.0
        self.reads = 0

    def f32(self, base=0x20000000):
        return self.regions_[base][: 1024 * 1024].view("<f4")

    def regions(self):
        return [(b, len(v)) for b, v in self.regions_.items()]

    def read(self, addr, size):
        self.reads += 1
        f = self.f32()
        f[self.timer] += 0.016
        if self.walking:
            self.t += 0.3
            f[self.px] = 120.5 + 10 * math.cos(self.t)
            f[self.py] = -33.25 + 10 * math.sin(self.t)
            f[self.cam_x] = f[self.px]
        for b, v in self.regions_.items():
            if b <= addr and addr + size <= b + len(v):
                return v[addr - b: addr - b + size].tobytes()
        return None

    def addr(self, i):
        return 0x20000000 + 4 * i


def window(sc, mem, mode, walking, samples=4):
    mem.walking = walking
    sc.begin_window()
    for _ in range(samples):
        sc.sample()
    return sc.end_window(mode)


def test_walk_scan_then_windows_find_xy():
    mem = FakeMem()
    sc = UnknownValueScanner(mem, "float")
    mem.walking = True
    first = sc.walk_scan(gap=0)
    assert 0 < first < 10          # only things that changed while walking (x, y, cam, timer)
    window(sc, mem, "unchanged", walking=False)
    for _ in range(2):
        window(sc, mem, "changed", walking=True)
        window(sc, mem, "unchanged", walking=False)
    found = {a for a, _ in sc.results()}
    assert {mem.addr(mem.px), mem.addr(mem.py), mem.addr(mem.cam_x)} <= found
    assert mem.addr(mem.timer) not in found and mem.addr(mem.pz) not in found
    assert sc.history[0]["mode"].startswith("changed while walking")
    assert sc.scan_stats["bytes_readable"] == sc.scan_stats["bytes_total"] == 4 * 1024 * 1024


def test_standing_still_during_first_pass_finds_nothing_but_timer():
    mem = FakeMem()
    sc = UnknownValueScanner(mem, "float")
    sc.walk_scan(gap=0)              # player forgot to walk
    assert {a for a, _ in sc.results()} == {mem.addr(mem.timer)}


def test_progress_and_cancel(monkeypatch):
    monkeypatch.setattr(scanner_mod, "BATCH", 1024 * 1024)   # several batches
    monkeypatch.setattr(scanner_mod, "CHUNK", 512 * 1024)
    mem = FakeMem()
    seen = []
    UnknownValueScanner(mem, "float").walk_scan(progress=lambda d, t: seen.append((d, t)), gap=0)
    assert len(seen) == 4 and seen[-1][0] == seen[-1][1] == 4 * 1024 * 1024
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    calls = []
    with pytest.raises(Cancelled):
        UnknownValueScanner(mem, "float").walk_scan(cancelled=lambda: calls.append(1) or len(calls) > 3, gap=0)


def test_unreadable_process_is_a_clear_error():
    class Denied(FakeMem):
        def read(self, addr, size):
            return None
    with pytest.raises(PermissionError, match="系統管理員"):
        UnknownValueScanner(Denied(), "float").walk_scan(gap=0)


def test_window_reads_only_candidate_spans():
    mem = FakeMem()
    sc = UnknownValueScanner(mem, "float")
    mem.walking = True
    sc.walk_scan(gap=0)
    mem.reads = 0
    sc.begin_window()
    sc.sample()
    # x/y/z sit together, cam and timer apart: a handful of small reads, not whole chunks
    assert mem.reads <= 2 * len(sc.chunks) * 3


def test_plausibility_filter_and_double():
    mem = FakeMem()
    f = mem.f32()
    f[2000], f[2001] = np.nan, 3e9
    mem.walking = True
    sc = UnknownValueScanner(mem, "float")
    sc.walk_scan(gap=0)
    found = {a for a, _ in sc.results()}
    assert not {mem.addr(2000), mem.addr(2001)} & found

    class D(FakeMem):           # a double coordinate at a 4-aligned (not 8-aligned) address
        def read(self, addr, size):
            raw = self.regions_[0x10000000]
            self.t += 1
            raw[1_500_004:1_500_012] = np.frombuffer(np.float64(777.125 + self.t).tobytes(), np.uint8)
            return super().read(addr, size)
    d = D()
    sc = UnknownValueScanner(d, "double")
    sc.walk_scan(gap=0)
    assert (0x10000000 + 1_500_004) in {a for a, _ in sc.results(limit=100000)}
