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
            "network/packets.jsonl": "{t_ns, dir(out|in), proto, src, sport, dst, dport, len, payload_hex, payload_ascii(preview)}",
            "video/screen.mp4": "window recording",
            "video/frames.csv": "frame,t_ns,left,top,width,height - map frames to the timeline",
            "input/events.jsonl": "{t_ns, kind, key|x,y,button...}",
            "logs/recorder.log": "tool log",
        },
    }


README_TXT = """GameRec 錄製資料說明 / GameRec session data
================================================
時間軸：所有檔案的 t_ns = 距離錄製開始的奈秒數（1 秒 = 1,000,000,000）。
         同一個 t_ns 代表同一個瞬間，可用來對齊記憶體、封包、畫面與按鍵。
Timeline: every file uses t_ns = nanoseconds since the session started.

manifest.json          本次錄製摘要：目標程式、視窗位置、各資料流狀態與筆數、備註
markers.jsonl          使用者標記  {"t_ns", "label"}
memory/samples.csv     記憶體數值  t_ns,name,value（只在數值改變時記錄；空白=讀不到）
memory/watchlist.json  監看位址清單（含實際解析出的位址 resolved）
network/packets.jsonl  每個封包一行 {"t_ns","dir":"out送出|in收到","proto","src","sport","dst","dport","len","payload_hex","payload_ascii"}
network/capture.pcap   原始封包，可用 Wireshark 開啟
video/screen.mp4       視窗錄影
video/frames.csv       frame,t_ns,left,top,width,height（第幾幀對應哪個時間點）
input/events.jsonl     鍵盤滑鼠 {"t_ns","kind":"key_down|key_up|mouse_move|mouse_down|mouse_up|scroll",...}
                       滑鼠 x,y 為螢幕座標；減去 manifest.target.window_rect 的左上角即為視窗內座標
logs/recorder.log      工具執行紀錄（出錯時看這裡）
"""


def export_session(session: Session, out_dir: str | Path | None = None, notes: str | None = None) -> Path:
    if not session.stopped:
        session.stop()
    if notes is not None:
        session.notes = notes
    manifest = build_manifest(session)
    (session.dir / "README.txt").write_text(README_TXT, encoding="utf-8")
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
