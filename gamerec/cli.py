"""Command line: `python -m gamerec record --process game.exe --watch watch.json --seconds 60`."""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from . import __version__


def _ensure_streams() -> None:
    """A --windowed PyInstaller exe has no console: sys.stdout/stderr are None and print() crashes."""
    import os

    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    _ensure_streams()
    ap = argparse.ArgumentParser(prog="gamerec", description="記錄指定程式的記憶體、封包、畫面與輸入，並匯出成單一 zip")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")

    gp = sub.add_parser("gui", help="開啟圖形介面（預設）")
    gp.add_argument("--smoke-test", metavar="FILE", help="開啟視窗後自動關閉並寫入結果（打包驗證用）")
    sub.add_parser("ps", help="列出程式")

    r = sub.add_parser("record", help="開始記錄，Ctrl+C 或時間到即停止並匯出")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--pid", type=int)
    g.add_argument("--process", help="程式名稱，例如 game.exe")
    r.add_argument("--watch", help="監看清單 JSON（見 docs/ARCHITECTURE.md 3.1）")
    r.add_argument("--seconds", type=float, default=0, help="錄製秒數，0 = 直到 Ctrl+C")
    r.add_argument("--rate", type=float, default=30.0, help="記憶體取樣 Hz")
    r.add_argument("--fps", type=float, default=15.0)
    r.add_argument("--scale", type=float, default=0.75, help="影片縮放比例（0.75：文字仍清楚、檔案約為 1.0 的一半）")
    r.add_argument("--iface", help="網卡名稱（預設自動）")
    r.add_argument("--no-network", action="store_true")
    r.add_argument("--no-video", action="store_true")
    r.add_argument("--no-input", action="store_true")
    r.add_argument("--out", default="sessions", help="輸出資料夾")
    r.add_argument("--notes", default="", help="寫入 manifest 的備註")
    r.add_argument("--result", help="把匯出的 zip 路徑寫到這個檔案（自動化用）")

    args = ap.parse_args(argv)
    if args.cmd in (None, "gui"):
        from .ui.app import run_gui

        return run_gui(smoke_file=getattr(args, "smoke_test", None))
    if args.cmd == "ps":
        from .process import list_processes

        try:
            for pid, name in list_processes():
                print(f"{pid:>7}  {name}")
        except BrokenPipeError:  # e.g. piped into `head`
            pass
        return 0
    return _record(args)


def _record(args) -> int:
    from .build import make_recorders
    from .export import export_session
    from .memory.watcher import load_watchlist
    from .process import find_target_by_name, get_target
    from .session import Session

    target = get_target(args.pid) if args.pid else find_target_by_name(args.process)
    watch = load_watchlist(args.watch) if args.watch else []
    recs = make_recorders(
        watch, network=not args.no_network, video=not args.no_video, inputs=not args.no_input,
        rate_hz=args.rate, fps=args.fps, scale=args.scale, iface=args.iface,
    )
    s = Session(target, recs, root=args.out)
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    s.start()
    for kind, (status, _) in s.stats().items():
        print(f"[{kind}] {status}")
    print(f"錄製中：{target.name} (pid {target.pid})，按 Ctrl+C 停止")
    # Wait in short slices: a bare Event.wait() cannot be interrupted by Ctrl+C on Windows.
    deadline = time.monotonic() + args.seconds if args.seconds else None
    while not done.is_set() and (deadline is None or time.monotonic() < deadline):
        done.wait(0.25)
    z = export_session(s, notes=args.notes)
    for kind, (status, count) in s.stats().items():
        print(f"[{kind}] {status} {count}")
    print(f"已匯出：{z}")
    if args.result:
        with open(args.result, "w", encoding="utf-8") as f:
            f.write(str(z.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
