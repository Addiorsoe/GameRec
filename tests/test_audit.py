"""Regression tests from the 2026-11-25 audit. Each one reproduced a real misreading first."""
import csv
import struct
import time
from types import SimpleNamespace

import pytest

from gamerec.hotkeys import FORCE_STOP, ComboTracker
from gamerec.memory.types import decode
from gamerec.memory.watcher import MemoryWatcher, WatchEntry
from gamerec.net.capture import PacketCapture
from gamerec.process import TargetInfo
from gamerec.session import Session, next_take
import json
import zipfile

from gamerec.export import export_session

TARGET = TargetInfo(pid=4242, name="game.exe")


class ChainReader:
    """[0x1000] -> 0x2000; value float at 0x2000+0x10. `broken` makes the chain unreadable."""

    def __init__(self, pid):
        self.broken = False
        self.x = 1.0

    def module_base(self, name):
        return 0

    def read_ptr(self, addr):
        if self.broken:
            raise OSError("unreadable")
        return 0x2000

    def read(self, addr, size):
        if addr == 0x2010:
            return struct.pack("<f", self.x)
        if addr == 0x3000:
            return struct.pack("<i", 7)
        raise OSError("unreadable")


def _samples(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_float_small_changes_not_lost():
    assert decode("float", struct.pack("<f", 1e-7)) != 0.0
    assert decode("double", struct.pack("<d", 1.23456789e-9)) == 1.23456789e-9
    assert decode("double", struct.pack("<d", 0.1 + 0.2)) == 0.1 + 0.2


def test_duplicate_watch_names_rejected():
    with pytest.raises(ValueError):
        MemoryWatcher([WatchEntry("hp", address=1), WatchEntry("hp", address=2)])


def test_broken_pointer_chain_is_recorded_not_stale(tmp_path):
    readers = []

    def factory(pid):
        readers.append(ChainReader(pid))
        return readers[-1]

    w = MemoryWatcher([WatchEntry("x", "float", address=0x1000, offsets=[0, 0x10])], rate_hz=200, reader_factory=factory)
    s = Session(TARGET, [w], root=tmp_path)
    s.start()
    time.sleep(0.1)
    readers[0].broken = True          # e.g. map change: chain no longer valid
    time.sleep(0.1)
    s.stop()
    vals = [r["value"] for r in _samples(s.dir / "memory/samples.csv")]
    assert vals[0] == "1.0" and "" in vals, vals   # must show "unreadable", not keep 1.0


def test_session_folders_never_collide(tmp_path):
    a = Session(TARGET, [], root=tmp_path)
    b = Session(TARGET, [], root=tmp_path)
    a.start(); a.stop(); b.start(); b.stop()
    assert a.dir != b.dir


def _pkt_capture(endpoints):
    pc = PacketCapture(ports_fn=lambda pid: endpoints)
    pc.session = SimpleNamespace(target=TARGET)
    pc._local_ips = {"192.168.1.10", "127.0.0.1"}
    pc._refresh_ports()
    return pc


def test_packets_of_other_programs_not_attributed_to_target():
    # target: TCP 192.168.1.10:50000 -> game server 1.2.3.4:7000
    pc = _pkt_capture({("tcp", "192.168.1.10", 50000)})
    assert pc.classify("tcp", "192.168.1.10", 50000, "1.2.3.4", 7000) == "out"
    assert pc.classify("tcp", "1.2.3.4", 7000, "192.168.1.10", 50000) == "in"
    # another program talks to some server whose port happens to be 50000
    assert pc.classify("tcp", "192.168.1.10", 61000, "5.6.7.8", 50000) is None
    assert pc.classify("tcp", "5.6.7.8", 50000, "192.168.1.10", 61000) is None


def test_wildcard_udp_bind_matches_local_ips_only():
    pc = _pkt_capture({("udp", "0.0.0.0", 6000)})
    assert pc.classify("udp", "192.168.1.10", 6000, "1.2.3.4", 9999) == "out"
    assert pc.classify("udp", "5.6.7.8", 6000, "1.2.3.4", 9999) is None   # not our machine


def test_ports_cleared_when_target_exits():
    import psutil

    calls = {"n": 0}

    def ports(pid):
        calls["n"] += 1
        if calls["n"] > 1:
            raise psutil.NoSuchProcess(pid)
        return {("tcp", "192.168.1.10", 50000)}

    pc = PacketCapture(ports_fn=ports)
    pc.session = SimpleNamespace(target=TARGET)
    pc._local_ips = {"192.168.1.10"}
    pc._refresh_ports()
    pc._refresh_ports()   # game closed; port may be reused by another program
    assert pc.classify("tcp", "192.168.1.10", 50000, "1.2.3.4", 7000) is None


def test_stuck_modifier_cannot_fake_force_stop():
    t = ComboTracker(modifier_down=lambda m: m == "alt")   # OS says only Alt is really held
    t.down("ctrl_l")          # ctrl pressed, its key-up was never seen (e.g. UAC prompt)
    t.down("alt_l")
    assert t.down("end") != FORCE_STOP


def test_ctrl_letter_logged_as_letter():
    from gamerec.inputs.logger import InputLogger

    key = SimpleNamespace(char="\x01", vk=0x41)   # what pynput reports for Ctrl+A
    assert InputLogger._name(key) == "a"


def test_first_packets_of_new_connection_kept(tmp_path):
    from scapy.all import PcapWriter
    from scapy.layers.inet import IP, TCP
    from scapy.layers.l2 import Ether

    state = {"ports": set()}
    pc = PacketCapture(ports_fn=lambda pid: state["ports"])
    pc.session = SimpleNamespace(target=TARGET, clock=SimpleNamespace(epoch_to_t_ns=lambda t: 1))
    pc._local_ips = {"192.168.1.10"}
    pc._pcap = PcapWriter(str(tmp_path / "c.pcap"), linktype=1)
    pc._jsonl = open(tmp_path / "p.jsonl", "w")
    syn = Ether() / IP(src="192.168.1.10", dst="1.2.3.4") / TCP(sport=50001, dport=7000, flags="S")
    other = Ether() / IP(src="192.168.1.10", dst="9.9.9.9") / TCP(sport=40000, dport=443, flags="S")
    pc._on_packet(syn)       # game opened a connection before the next refresh
    pc._on_packet(other)     # some other program
    assert pc.count == 0
    state["ports"] = {("tcp", "192.168.1.10", 50001)}
    pc._refresh_ports()
    pc._flush_pending(final=True)
    pc._jsonl.close()
    lines = (tmp_path / "p.jsonl").read_text().splitlines()
    assert pc.count == 1 and '"sport": 50001' in lines[0]


def test_group_take_in_name_and_manifest(tmp_path):
    assert next_take(tmp_path, "A") == 1
    (tmp_path / "session_20261125_120000_A-01_game.exe.zip").touch()
    (tmp_path / "session_20261125_120100_A-07_game.exe").mkdir()
    (tmp_path / "session_20261125_120200_B-03_game.exe.zip").touch()
    (tmp_path / "session_20261125_120300_AB-09_game.exe.zip").touch()   # different group
    assert next_take(tmp_path, "A") == 8 and next_take(tmp_path, "B") == 4 and next_take(tmp_path, "C") == 1

    s = Session(TARGET, [], root=tmp_path, group="A", take=8)
    s.start()
    s.stop()
    assert "_A-08_" in s.name
    z = export_session(s, notes="first")
    z2 = export_session(s, out_dir=z.parent, notes="edited later")   # notes added afterwards
    assert z2 == z
    man = json.loads(zipfile.ZipFile(z).read("manifest.json"))
    assert man["group"] == "A" and man["take"] == 8 and man["notes"] == "edited later"


def test_held_hotkey_fires_once():
    # Before: holding F9 sent repeated key-downs, so start/stop toggled over and over.
    t = ComboTracker()
    assert t.down("f9") == "f9"
    assert t.down("f9") is None and t.down("f9") is None   # auto-repeat
    t.up("f9")
    assert t.down("f9") == "f9"


def test_lost_key_up_does_not_kill_hotkey(monkeypatch):
    import gamerec.hotkeys as hk

    now = [100.0]
    monkeypatch.setattr(hk.time, "monotonic", lambda: now[0])
    t = ComboTracker()
    assert t.down("f9") == "f9"        # key-up never arrives
    now[0] += 2.0
    assert t.down("f9") == "f9"        # next real press still works
