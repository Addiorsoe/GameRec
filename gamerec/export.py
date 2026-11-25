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


ALL_KINDS = ("memory", "network", "video", "input")


def _streams(session: Session) -> list[dict]:
    """Every stream is listed, so a missing one is explicit instead of silently absent."""
    out = [r.describe() for r in session.recorders]
    have = {d["kind"] for d in out}
    for kind in ALL_KINDS:
        if kind in have:
            continue
        if kind == "memory":
            out.append({"kind": kind, "status": "not_configured", "files": [], "count": 0,
                        "error": "watch list was empty: no memory values were recorded"})
        else:
            out.append({"kind": kind, "status": "disabled", "files": [], "count": 0,
                        "error": "not selected for this recording"})
    return out


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
        "streams": _streams(session),
        "markers": len(session.markers),
        "group": getattr(session, "group", "") or None,
        "take": getattr(session, "take", None),
        "notes": session.notes,
        "files": {
            "markers.jsonl": "user markers: {t_ns, label}",
            "memory/samples.csv": "t_ns,name,value - written on change only; value empty = unreadable",
            "memory/watchlist.json": "watched addresses incl. resolved absolute address",
            "network/capture.pcap": "raw packets (open with Wireshark)",
            "network/packets.jsonl": "{t_ns, dir(out|in), proto, src, sport, dst, dport, len, payload_hex, payload_ascii(preview)} - arrival order, sort by t_ns",
            "video/screen.mp4": "window recording",
            "video/frames.csv": "frame,t_ns,left,top,width,height,source(window|screen|region),dup(1=repeated frame to keep real time)",
            "input/events.jsonl": "{t_ns, kind, key|x,y,button..., repeat(true=OS auto-repeat while held)}",
            "stills/*.jpg": "480 px stills from the video: one per second + one at every key press, mouse press and marker (taken by time only)",
            "stills/index.csv": "file,t_ns,reason(every_1s|key_down|mouse_down|marker),detail(key / button@x,y / marker label),video_frame",
            "logs/recorder.log": "tool log",
        },
    }


README_TXT = """GameRec 錄製資料說明 / GameRec session data
================================================
時間軸：所有檔案的 t_ns = 距離錄製開始的奈秒數（1 秒 = 1,000,000,000）。
         同一個 t_ns 代表同一個瞬間，可用來對齊記憶體、封包、畫面與按鍵。
Timeline: every file uses t_ns = nanoseconds since the session started.

manifest.json          本次錄製摘要：目標程式、視窗位置、各資料流狀態與筆數、組別（group）、該組第幾段（take）、備註（notes）
markers.jsonl          使用者標記  {"t_ns", "label"}
memory/samples.csv     記憶體數值  t_ns,name,value（只在數值改變時記錄；空白=讀不到）
memory/watchlist.json  監看位址清單（含實際解析出的位址 resolved）
network/packets.jsonl  每個封包一行 {"t_ns","dir":"out送出|in收到","proto","src","sport","dst","dport","len","payload_hex","payload_ascii"}
                       依抓到的順序寫入（新連線的前幾個封包會稍晚寫入），分析時請依 t_ns 排序
network/capture.pcap   原始封包，可用 Wireshark 開啟
video/screen.mp4       視窗錄影
video/frames.csv       frame,t_ns,left,top,width,height,source,dup（第幾幀對應哪個時間點）
                       source：window=遊戲視窗、screen=找不到視窗改錄整個螢幕；dup=1 表示重複上一幀（擷取來不及時補幀，讓影片時間等於真實時間）
input/events.jsonl     鍵盤滑鼠 {"t_ns","kind":"key_down|key_up|mouse_move|mouse_down|mouse_up|scroll",...}
                       "repeat":true 表示按住不放時系統自動重複的按鍵，不是新的一次按下
                       滑鼠 x,y 為螢幕座標；減去 manifest.target.window_rect 的左上角即為視窗內座標
stills/                影片截圖（寬 480）：每秒一張，加上每次按鍵、按滑鼠、加標記的當下；只依時間擷取，不做判斷
stills/index.csv       file,t_ns,reason,detail,video_frame（每張圖對應的時間與原始事件）
logs/recorder.log      工具執行紀錄（出錯時看這裡）
"""


def export_session(session: Session, out_dir: str | Path | None = None, notes: str | None = None) -> Path:
    if not session.stopped:
        session.stop()
    if notes is not None:
        session.notes = notes
    if not (session.dir / "stills" / "index.csv").exists():  # re-export (notes edit) keeps them
        try:
            from .video.stills import extract_stills

            extract_stills(session.dir, session.duration_ns / 1e9)
        except Exception:  # noqa: BLE001 - stills are a convenience; the recording itself is intact
            import logging

            logging.getLogger("gamerec").exception("stills extraction failed")
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
