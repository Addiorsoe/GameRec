"""Write manifest.json and zip a finished session into one package for analysis."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .session import Session

FORMAT = "gamerec-session"
FORMAT_VERSION = 1

# Already-compressed formats are stored, not deflated again.
_STORED = {".mp4", ".zip", ".png", ".jpg"}


def build_manifest(session: Session) -> dict:
    assert session.clock is not None
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "tool_version": __version__,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "duration_s": round(session.duration_ns / 1e9, 3),
        "clock": session.clock.to_dict(),
        "target": getattr(session, "target_snapshot", None) or session.target.to_dict(),
        "streams": [r.describe() for r in session.recorders],
        "markers": len(session.markers),
        "notes": session.notes,
        "files": {
            "markers.jsonl": "user markers: {t_ns, label}",
            "memory/samples.csv": "t_ns,name,value - written on change only; value empty = unreadable",
            "memory/watchlist.json": "watched addresses incl. resolved absolute address",
            "network/capture.pcap": "raw packets (open with Wireshark)",
            "network/packets.jsonl": "{t_ns, dir(out|in), proto, src, sport, dst, dport, len, payload_hex}",
            "video/screen.mp4": "window recording",
            "video/frames.csv": "frame,t_ns,left,top,width,height - map frames to the timeline",
            "input/events.jsonl": "{t_ns, kind, key|x,y,button...}",
            "logs/recorder.log": "tool log",
        },
    }


def export_session(session: Session, out_dir: str | Path | None = None, notes: str | None = None) -> Path:
    if not session.stopped:
        session.stop()
    if notes is not None:
        session.notes = notes
    manifest = build_manifest(session)
    (session.dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    out_dir = Path(out_dir) if out_dir else session.dir.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"{session.name}.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for f in sorted(session.dir.rglob("*")):
            if f.is_file():
                ctype = zipfile.ZIP_STORED if f.suffix.lower() in _STORED else zipfile.ZIP_DEFLATED
                z.write(f, f.relative_to(session.dir).as_posix(), compress_type=ctype)
    return zpath
