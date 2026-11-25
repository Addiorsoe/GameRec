"""Capture packets belonging to the target process (scapy + Npcap on Windows).

Windows cannot sniff by PID, so we sniff the interface and keep only packets whose local
endpoint (protocol + local IP + local port) is currently owned by the target, refreshed
every 0.5 s via psutil. Packets of a connection opened between two refreshes are held
briefly and written once the refresh shows the connection belongs to the target, so the
first packets of a new connection are not lost. Files are in arrival order: sort by t_ns.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import sys
import threading
import time

from ..process import _norm_ip, local_endpoints, machine_ips
from ..recorder import Recorder


class PacketCapture(Recorder):
    kind = "network"

    WILDCARDS = ("0.0.0.0", "::")
    PENDING_MAX = 5000       # bounded: never grows without limit
    PENDING_SECONDS = 1.5    # > 2 refresh periods
    REFRESH_SECONDS = 0.5

    def __init__(self, iface: str | None = None, max_payload: int = 4096, ports_fn=None) -> None:
        super().__init__()
        self.iface = iface
        self.max_payload = max_payload
        self._ports_fn = ports_fn or local_endpoints
        self._ports: set[tuple[str, str, int]] = set()
        self._local_ips: set[str] = set()
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._pending: collections.deque = collections.deque(maxlen=self.PENDING_MAX)
        self.dropped_pending = 0

    def setup(self) -> None:
        if sys.platform == "win32" and not _npcap_installed():
            raise OSError("Npcap not found - install Npcap (WinPcap compatible mode)")
        from scapy.all import PcapWriter  # noqa: PLC0415

        self._local_ips = machine_ips()
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
        except Exception as e:  # noqa: BLE001
            import psutil

            if isinstance(e, psutil.NoSuchProcess):
                # target exited: its ports may be reused by other programs, so match nothing
                if self._ports:
                    logging.getLogger("gamerec").info("target process exited; packet matching stopped")
                ports = set()
            else:
                return  # transient (e.g. access denied): keep the last known endpoints
        with self._lock:
            self._ports = ports

    def run(self) -> None:
        while not self.stopping:
            self._refresh_ports()
            self._flush_pending()
            if self._sniffer is not None and getattr(self._sniffer, "exception", None) is not None:
                raise OSError(f"packet sniffer stopped: {self._sniffer.exception}")
            self._stop.wait(self.REFRESH_SECONDS)
        self._refresh_ports()
        self._flush_pending(final=True)

    def _owns(self, proto: str, ip: str, port: int) -> bool:
        if (proto, ip, port) in self._ports:
            return True
        return ip in self._local_ips and any((proto, w, port) in self._ports for w in self.WILDCARDS)

    def classify(self, proto: str, src: str, sport: int, dst: str, dport: int) -> str | None:
        src, dst = _norm_ip(src), _norm_ip(dst)
        with self._lock:
            if self._owns(proto, src, sport):
                return "out"
            if self._owns(proto, dst, dport):
                return "in"
        return None

    def _flush_pending(self, final: bool = False) -> None:
        """Re-check held packets against the refreshed endpoints; drop ones that never matched."""
        now = time.monotonic()
        keep = collections.deque(maxlen=self.PENDING_MAX)
        for _ in range(len(self._pending)):  # only what was there now: no chasing new arrivals
            t, pkt, key = self._pending.popleft()
            d = self.classify(*key)
            if d:
                self._write(pkt, key, d)
            elif not final and now - t < self.PENDING_SECONDS:
                keep.append((t, pkt, key))
        self._pending.extendleft(reversed(keep))

    def _on_packet(self, pkt) -> None:  # hot path: called for every packet on the interface
        IP, IPv6, TCP, UDP = _layers()

        ip = pkt.getlayer(IP) or pkt.getlayer(IPv6)
        l4 = pkt.getlayer(TCP) or pkt.getlayer(UDP)
        if ip is None or l4 is None:
            return
        proto = "tcp" if isinstance(l4, TCP) else "udp"
        key = (proto, str(ip.src), int(l4.sport), str(ip.dst), int(l4.dport))
        direction = self.classify(*key)
        if direction is None:
            # maybe a connection the target opened since the last refresh: hold it briefly
            if _norm_ip(key[1]) in self._local_ips or _norm_ip(key[3]) in self._local_ips:
                if len(self._pending) == self.PENDING_MAX:
                    self.dropped_pending += 1
                self._pending.append((time.monotonic(), pkt, key))
            return
        self._write(pkt, key, direction)

    def _write(self, pkt, key, direction: str) -> None:
        _, _, TCP, UDP = _layers()

        l4 = pkt.getlayer(TCP) or pkt.getlayer(UDP)
        proto = key[0]
        payload = bytes(l4.payload)
        rec = {
            "t_ns": self.session.clock.epoch_to_t_ns(float(pkt.time)),
            "dir": direction,
            "proto": proto,
            "src": key[1], "sport": key[2],
            "dst": key[3], "dport": key[4],
            "len": len(payload),
            "payload_hex": payload[: self.max_payload].hex(),
            # quick human preview: printable ASCII kept, everything else shown as '.'
            "payload_ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in payload[:96]),
        }
        if proto == "tcp":
            rec["flags"] = str(l4.flags)
            rec["seq"] = l4.seq
        with self._wlock:
            if self._jsonl.closed:
                return
            self._pcap.write(pkt)
            self._jsonl.write(json.dumps(rec) + "\n")
            self.count += 1

    def teardown(self) -> None:
        if getattr(self, "_sniffer", None):
            try:
                self._sniffer.stop()
            except Exception:  # noqa: BLE001
                pass
        self.extra["unmatched_buffer_overflow"] = self.dropped_pending
        with self._wlock:
            for fh in ("_pcap", "_jsonl"):
                if getattr(self, fh, None):
                    getattr(self, fh).close()


_LAYERS = None


def _layers():
    global _LAYERS
    if _LAYERS is None:
        from scapy.layers.inet import IP, TCP, UDP
        from scapy.layers.inet6 import IPv6

        _LAYERS = (IP, IPv6, TCP, UDP)
    return _LAYERS


def _npcap_installed() -> bool:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    return any(
        os.path.exists(os.path.join(windir, d, "wpcap.dll"))
        for d in (r"System32\Npcap", "System32")
    )
