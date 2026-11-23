import csv
import json
import struct
import time
import zipfile

from gamerec.export import export_session
from gamerec.memory.watcher import MemoryWatcher, WatchEntry, resolve
from gamerec.net.capture import PacketCapture
from gamerec.process import TargetInfo
from gamerec.recorder import Recorder
from gamerec.session import Session

TARGET = TargetInfo(pid=4242, name="game.exe")


class FakeReader:
    """Fake process memory: module game.exe at 0x400000, pointer chain to a moving float."""

    def __init__(self, pid):
        self.mem = {}
        self.x = 0.0
        self.put_ptr(0x400000 + 0x100, 0x9000)   # [base+0x100] -> 0x9000
        self.put_ptr(0x9000 + 0x10, 0xA000)      # [0x9000+0x10] -> 0xA000

    def put_ptr(self, addr, val):
        self.mem[addr] = val.to_bytes(8, "little")

    def module_base(self, name):
        assert name == "game.exe"
        return 0x400000

    def read_ptr(self, addr):
        return int.from_bytes(self.mem[addr], "little")

    def read(self, addr, size):
        if addr == 0xA000 + 0x40:
            self.x += 1.5
            return struct.pack("<f", self.x)
        if addr == 0x400000 + 0x200:
            return struct.pack("<i", 100)
        raise OSError("unreadable")


def test_resolve_variants():
    r = FakeReader(0)
    assert resolve(WatchEntry("a", address=0x1234), r) == 0x1234
    assert resolve(WatchEntry("b", module="game.exe", offset=0x200), r) == 0x400200
    assert resolve(WatchEntry("c", module="game.exe", offsets=[0x100, 0x10, 0x40]), r) == 0xA040


def test_watchentry_roundtrip():
    d = {"name": "x", "type": "float", "module": "game.exe", "offsets": ["0x100", "0x10", "0x40"]}
    e = WatchEntry.from_dict(d)
    assert e.offsets == [0x100, 0x10, 0x40]
    assert WatchEntry.from_dict(e.to_dict()).offsets == e.offsets


class DummyRecorder(Recorder):
    kind = "dummy"

    def setup(self):
        self.fh = open(self.path("dummy/data.txt"), "w")

    def run(self):
        while not self.stopping:
            self.fh.write(f"{self.now_ns()}\n")
            self.count += 1
            self._stop.wait(0.01)

    def teardown(self):
        self.fh.close()


class BrokenRecorder(Recorder):
    kind = "broken"

    def setup(self):
        raise ImportError("no such driver")


def test_session_export(tmp_path):
    watch = [
        WatchEntry("x", "float", module="game.exe", offsets=[0x100, 0x10, 0x40]),
        WatchEntry("hp", "int32", module="game.exe", offset=0x200),
        WatchEntry("bad", "int32", address=0xDEAD),
    ]
    mem = MemoryWatcher(watch, rate_hz=100, reader_factory=FakeReader)
    s = Session(TARGET, [mem, DummyRecorder(), BrokenRecorder()], root=tmp_path)
    s.start()
    time.sleep(0.2)
    s.mark("walk north")
    time.sleep(0.1)
    z = export_session(s, notes="test run")

    assert z.exists()
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        assert {"manifest.json", "markers.jsonl", "memory/samples.csv", "memory/watchlist.json",
                "dummy/data.txt", "logs/recorder.log"} <= names
        man = json.loads(zf.read("manifest.json"))
        rows = list(csv.DictReader(zf.read("memory/samples.csv").decode().splitlines()))
        markers = [json.loads(line) for line in zf.read("markers.jsonl").decode().splitlines()]

    assert man["format"] == "gamerec-session"
    assert man["notes"] == "test run"
    st = {x["kind"]: x for x in man["streams"]}
    assert st["memory"]["status"] == "ok" and st["memory"]["count"] > 5
    assert st["dummy"]["status"] == "ok"
    assert st["broken"]["status"] == "unavailable"
    xs = [r for r in rows if r["name"] == "x"]
    hp = [r for r in rows if r["name"] == "hp"]
    assert len(xs) > 5 and len(hp) == 1          # hp unchanged -> written once
    ts = [int(r["t_ns"]) for r in xs]
    assert ts == sorted(ts)
    assert markers[0]["label"] == "walk north" and 0 < markers[0]["t_ns"] < man["duration_s"] * 1e9


def test_packet_direction():
    pc = PacketCapture(ports_fn=lambda pid: {("tcp", 50000), ("udp", 6000)})
    pc._ports = {("tcp", 50000), ("udp", 6000)}
    assert pc.classify("tcp", 50000, 443) == "out"
    assert pc.classify("tcp", 443, 50000) == "in"
    assert pc.classify("udp", 6000, 9999) == "out"
    assert pc.classify("tcp", 6000, 9999) is None
