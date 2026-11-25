"""Capture packets belonging to the target process (scapy + Npcap on Windows).

Windows cannot sniff by PID, so we sniff the interface and keep only packets whose
local port is currently owned by the target (refreshed every second via psutil).
"""
from __future__ import annotations

import json
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
        from scapy.all import AsyncSniffer, PcapWriter, conf  # noqa: PLC0415

        if not conf.use_pcap and __import__("sys").platform == "win32":
            raise OSError("Npcap not found - install Npcap (WinPcap compatible mode)")
        self._refresh_ports()
        self._pcap = PcapWriter(str(self.path("network/capture.pcap")), sync=False)
        self._jsonl = open(self.path("network/packets.jsonl"), "w", encoding="utf-8")
        # Prefer a kernel BPF filter; fall back to Python-side filtering if it can't compile.
        self._sniffer = None
        last_err: Exception | None = None
        for bpf in ("tcp or udp", None):
            sn = AsyncSniffer(iface=self.iface, filter=bpf, prn=self._on_packet, store=False)
            sn.start()
            time.sleep(0.3)
            if sn.thread is not None and sn.thread.is_alive():
                self._sniffer, self._bpf = sn, bpf
                break
            last_err = sn.exception or RuntimeError("sniffer thread exited")
        if self._sniffer is None:
            raise OSError(f"cannot start packet capture: {last_err}")
        self.extra = {"iface": self.iface or "default", "max_payload": self.max_payload, "bpf": self._bpf}

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
            if not self._sniffer.thread.is_alive():
                raise RuntimeError(f"packet sniffer stopped: {self._sniffer.exception}")
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
        payload = bytes(l4.payload)
        rec = {
            "t_ns": self.session.clock.epoch_to_t_ns(float(pkt.time)),
            "dir": direction,
            "proto": proto,
            "src": ip.src, "sport": l4.sport,
            "dst": ip.dst, "dport": l4.dport,
            "len": len(payload),
            "payload_hex": payload[: self.max_payload].hex(),
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
                self._sniffer.stop(join=True)
            except Exception:  # noqa: BLE001 - already reported by run()
                pass
        for fh in ("_pcap", "_jsonl"):
            if getattr(self, fh, None):
                getattr(self, fh).close()
