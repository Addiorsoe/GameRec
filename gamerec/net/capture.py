"""Capture packets belonging to the target process (scapy + Npcap on Windows).

Windows cannot sniff by PID, so we sniff the interface and keep only packets whose
local port is currently owned by the target (refreshed every second via psutil).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

from ..process import local_ports
from ..recorder import Recorder


class PacketCapture(Recorder):
    kind = "network"

    def __init__(self, iface: str | None = None, max_payload: int = 4096, ports_fn=None) -> None:
        super().__init__()
        self.iface = iface
        self.max_payload = max_payload
        self._ports_fn = ports_fn or local_ports
        self._ports: set[tuple[str, int]] = set()
        self._lock = threading.Lock()

    def setup(self) -> None:
        if sys.platform == "win32" and not _npcap_installed():
            raise OSError("Npcap not found - install Npcap (WinPcap compatible mode)")
        from scapy.all import PcapWriter  # noqa: PLC0415

        self._refresh_ports()
        self._pcap = PcapWriter(str(self.path("network/capture.pcap")), linktype=1, sync=False)
        self._jsonl = open(self.path("network/packets.jsonl"), "w", encoding="utf-8")
        # The sniffer runs in its own thread and would otherwise fail silently (status "ok",
        # 0 packets). Start it, check it is alive, and retry without a BPF filter if the
        # filter cannot be compiled (we filter by port in Python anyway).
        err = None
        for bpf in ("tcp or udp", None):
            err = self._start_sniffer(bpf)
            if err is None:
                break
        if err is not None:
            raise OSError(f"packet sniffer failed to start: {err}")
        self.extra = {"iface": self.iface or "default", "max_payload": self.max_payload, "bpf": self._bpf}

    def _start_sniffer(self, bpf: str | None) -> str | None:
        from scapy.all import AsyncSniffer  # noqa: PLC0415

        self._bpf = bpf
        self._sniffer = AsyncSniffer(iface=self.iface, filter=bpf, prn=self._on_packet, store=False)
        self._sniffer.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            exc = getattr(self._sniffer, "exception", None)
            thread = getattr(self._sniffer, "thread", None)
            if exc is not None or (thread is not None and not thread.is_alive()):
                self._sniffer = None
                return f"{type(exc).__name__}: {exc}" if exc else "sniffer thread exited"
            time.sleep(0.05)
        return None

    def _refresh_ports(self) -> None:
        assert self.session is not None
        try:
            ports = self._ports_fn(self.session.target.pid)
        except Exception:  # noqa: BLE001 - process may have exited
            return
        with self._lock:
            self._ports = ports

    def run(self) -> None:
        while not self.stopping:
            self._refresh_ports()
            if self._sniffer is not None and getattr(self._sniffer, "exception", None) is not None:
                raise OSError(f"packet sniffer stopped: {self._sniffer.exception}")
            self._stop.wait(1.0)

    def classify(self, proto: str, sport: int, dport: int) -> str | None:
        with self._lock:
            if (proto, sport) in self._ports:
                return "out"
            if (proto, dport) in self._ports:
                return "in"
        return None

    def _on_packet(self, pkt) -> None:
        from scapy.layers.inet import IP, TCP, UDP
        from scapy.layers.inet6 import IPv6

        ip = pkt.getlayer(IP) or pkt.getlayer(IPv6)
        l4 = pkt.getlayer(TCP) or pkt.getlayer(UDP)
        if ip is None or l4 is None:
            return
        proto = "tcp" if isinstance(l4, TCP) else "udp"
        direction = self.classify(proto, l4.sport, l4.dport)
        if direction is None:
            return
        if self._jsonl.closed:
            return
        payload = bytes(l4.payload)
        rec = {
            "t_ns": self.session.clock.epoch_to_t_ns(float(pkt.time)),
            "dir": direction,
            "proto": proto,
            "src": ip.src, "sport": l4.sport,
            "dst": ip.dst, "dport": l4.dport,
            "len": len(payload),
            "payload_hex": payload[: self.max_payload].hex(),
            # quick human preview: printable ASCII kept, everything else shown as '.'
            "payload_ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in payload[:96]),
        }
        if proto == "tcp":
            rec["flags"] = str(l4.flags)
            rec["seq"] = l4.seq
        self._pcap.write(pkt)
        self._jsonl.write(json.dumps(rec) + "\n")
        self.count += 1

    def teardown(self) -> None:
        if getattr(self, "_sniffer", None):
            try:
                self._sniffer.stop()
            except Exception:  # noqa: BLE001
                pass
        for fh in ("_pcap", "_jsonl"):
            if getattr(self, fh, None):
                getattr(self, fh).close()


def _npcap_installed() -> bool:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    return any(
        os.path.exists(os.path.join(windir, d, "wpcap.dll"))
        for d in (r"System32\Npcap", "System32")
    )
