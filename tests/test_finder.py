"""Coordinate finder core, against fake process memory (4 MB of noise + a moving player)."""
import numpy as np
import pytest

from gamerec.memory.scanner import UnknownValueScanner


class FakeMem:
    def __init__(self, seed=1):
        rng = np.random.default_rng(seed)
        self.regions_ = {}
        for base in (0x10000000, 0x20000000):
            # junk bytes, plus many plausible-looking constant floats
            buf = rng.integers(0, 256, 2 * 1024 * 1024, dtype=np.uint8)
            floats = buf[: 1024 * 1024].view("<f4")
            floats[::3] = rng.uniform(-5000, 5000, len(floats[::3]))
            self.regions_[base] = buf
        f = self.f32(0x20000000)
        self.px, self.py, self.pz = 1000, 1001, 1002          # player x, y, z (z constant: flat ground)
        self.cam_x = 5000                                      # camera follows the player
        self.timer = 7000                                      # always changing (animation timer)
        self.once = 9000                                       # changes once, then stays
        f[self.px], f[self.py], f[self.pz], f[self.cam_x] = 120.5, -33.25, 4.0, 120.5
        f[self.timer], f[self.once] = 1.0, 2.0
        self.moves = 0

    def f32(self, base):
        return self.regions_[base][: 1024 * 1024].view("<f4")

    def regions(self):
        return [(b, len(v)) for b, v in self.regions_.items()]

    def read(self, addr, size):
        for b, v in self.regions_.items():
            if b <= addr and addr + size <= b + len(v):
                return v[addr - b: addr - b + size].tobytes()
        return None

    def addr(self, i):
        return 0x20000000 + 4 * i

    def tick(self, moving):
        f = self.f32(0x20000000)
        f[self.timer] += 0.016
        if moving:
            self.moves += 1
            dx, dy = 3.0, (-2.5 if self.moves % 2 else 0.0)   # y doesn't move on every step
            f[self.px] += dx
            f[self.py] += dy
            f[self.cam_x] = f[self.px]
            if self.moves == 1:
                f[self.once] = 5.0


def run(mem, sc, rounds=4):
    sc.first_scan()
    mem.tick(False)
    sc.next_scan("unchanged")
    for _ in range(rounds):
        mem.tick(True)
        sc.next_scan("changed")
        mem.tick(False)
        sc.next_scan("unchanged")
    return {a for a, _ in sc.results()}


def test_finds_moving_coordinates_only():
    mem = FakeMem()
    sc = UnknownValueScanner(mem, "float")
    found = run(mem, sc)
    assert mem.addr(mem.px) in found and mem.addr(mem.cam_x) in found   # x and its copy
    assert mem.addr(mem.timer) not in found and mem.addr(mem.once) not in found
    assert mem.addr(mem.pz) not in found  # never moved -> can't be told apart, correctly dropped
    # y only moved on some rounds: "changed" on a round where only x moved removes it
    assert mem.addr(mem.py) not in found
    assert len(found) <= 5
    assert [h["mode"] for h in sc.history][:3] == ["first", "unchanged", "changed"]


def test_axis_that_moves_every_round_survives():
    mem = FakeMem()
    sc = UnknownValueScanner(mem, "float")
    sc.first_scan()
    for _ in range(3):
        mem.tick(True)
        mem.tick(True)          # two moves per round -> both axes change every round
        sc.next_scan("changed")
        mem.tick(False)
        sc.next_scan("unchanged")
    found = {a for a, _ in sc.results()}
    assert {mem.addr(mem.px), mem.addr(mem.py)} <= found
    vals = sc.read_values([mem.addr(mem.px)])
    assert vals[0] == pytest.approx(float(mem.f32(0x20000000)[mem.px]))


def test_spill_to_disk_gives_same_result():
    a, b = FakeMem(), FakeMem()
    in_ram = run(a, UnknownValueScanner(a, "float"))
    sc = UnknownValueScanner(b, "float", spill_at=1000)
    sc.first_scan()
    assert sc._tmp is not None and any(sc._tmp.iterdir())    # really spilled
    b.tick(False)
    sc.next_scan("unchanged")
    assert sc._tmp is None                                    # temp files removed after narrowing
    for _ in range(4):
        b.tick(True)
        sc.next_scan("changed")
        b.tick(False)
        sc.next_scan("unchanged")
    assert {x for x, _ in sc.results()} == in_ram


def test_plausibility_filter():
    mem = FakeMem()
    f = mem.f32(0x20000000)
    f[2000], f[2001], f[2002] = np.nan, 3e9, 1e-40
    sc = UnknownValueScanner(mem, "float")
    sc.first_scan()
    found = {a for a, _ in sc.results(limit=10_000_000)}
    assert mem.addr(mem.px) in found
    assert not {mem.addr(2000), mem.addr(2001), mem.addr(2002)} & found


def test_double_and_unaligned_read():
    mem = FakeMem()
    raw = mem.regions_[0x10000000]
    raw[1_500_004:1_500_012] = np.frombuffer(np.float64(777.125).tobytes(), np.uint8)  # 4-aligned, not 8
    sc = UnknownValueScanner(mem, "double")
    sc.first_scan()
    assert (0x10000000 + 1_500_004) in {a for a, _ in sc.results(limit=10_000_000)}


def test_window_catches_axis_that_returns_to_start():
    # Walking a loop: x goes out and comes back. End-vs-start would call it "unchanged";
    # the window scan sees the change in between.
    mem = FakeMem()
    sc = UnknownValueScanner(mem, "float")
    sc.first_scan()
    f = mem.f32(0x20000000)
    for _ in range(3):
        sc.begin_window()
        for dx, dy in ((5, 0), (0, 4), (-5, 0)):   # x returns to where it started
            f[mem.px] += dx
            f[mem.py] += dy
            f[mem.timer] += 0.016
            sc.sample()
        sc.end_window("changed")
        sc.begin_window()
        for _ in range(3):
            f[mem.timer] += 0.016
            sc.sample()
        sc.end_window("unchanged")
    found = {a for a, _ in sc.results()}
    assert {mem.addr(mem.px), mem.addr(mem.py)} <= found
    assert mem.addr(mem.timer) not in found and len(found) <= 4
