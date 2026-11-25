"""找座標精靈: finds candidate coordinate addresses without knowing their values.

The user only follows timed prompts in the game ("stand still" / "walk a small loop");
memory is narrowed automatically. Results are CANDIDATES: the program never decides
which address is X, Y or Z. They can be added to the watch list and are saved as a
JSON report next to the recordings for Claude to verify.
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from ..memory.scanner import ProcessMemory, UnknownValueScanner
from ..memory.watcher import WatchEntry

log = logging.getLogger("gamerec")
TABLE_AT = 200   # live value table when this few candidates remain
ADD_ALL_AT = 50  # "add all" allowed at this many or fewer


def _beep() -> None:
    if sys.platform == "win32":
        import winsound

        winsound.MessageBeep(winsound.MB_OK)


class FinderDialog(tk.Toplevel):
    TIME_SCALE = 1.0  # tests shorten the prompts
    PREP, STILL, MOVE, GRACE, SAMPLE = 5.0, 3.0, 6.0, 1.5, 0.25

    def __init__(self, master, app, mem_factory=ProcessMemory) -> None:
        super().__init__(master)
        self.app = app
        self.target = app.locked
        self.mem_factory = mem_factory
        self.title("找座標精靈")
        self.attributes("-topmost", True)  # stays readable over the game window
        self.scanner: UnknownValueScanner | None = None
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._rows: list[int] = []
        self.finished = ""  # last outcome: done | stopped | error
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self._poll)

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        t = self.target
        ttk.Label(f, text=f"目標：{t.name} [{t.pid}]", font=("", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(f, text="型別").grid(row=1, column=0, sticky="w", pady=4)
        self.vtype = tk.StringVar(value="float")
        ttk.Combobox(f, textvariable=self.vtype, values=("float", "double", "int32"), width=8, state="readonly").grid(row=1, column=1, sticky="w")
        ttk.Label(f, text="輪數").grid(row=1, column=2, sticky="e")
        self.rounds = tk.IntVar(value=4)
        ttk.Spinbox(f, from_=1, to=8, textvariable=self.rounds, width=4).grid(row=1, column=3, sticky="w")

        self.prompt = tk.StringVar(value="按「開始」後切到遊戲，照著這裡的提示做（每換一步會響一聲）。")
        ttk.Label(f, textvariable=self.prompt, font=("", 13, "bold"), wraplength=460, foreground="#05a").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(8, 2))
        self.info = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.info, wraplength=300).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(f, textvariable=self.count_var, font=("", 10, "bold")).grid(row=3, column=2, columnspan=2, sticky="e")

        bar = ttk.Frame(f)
        bar.grid(row=4, column=0, columnspan=4, sticky="w", pady=6)
        self.b_start = ttk.Button(bar, text="開始", command=self.start)
        self.b_more = ttk.Button(bar, text="再一輪", command=lambda: self.start(more=True), state="disabled")
        self.b_stop = ttk.Button(bar, text="停止", command=self.stop, state="disabled")
        for b in (self.b_start, self.b_more, self.b_stop):
            b.pack(side="left", padx=2)
        ttk.Label(bar, text="　手動：").pack(side="left")
        self.manual = []
        for label, mode in (("變動了", "changed"), ("沒變動", "unchanged"), ("變大", "increased"), ("變小", "decreased")):
            b = ttk.Button(bar, text=label, command=lambda m=mode: self.manual_scan(m), state="disabled", width=6)
            b.pack(side="left", padx=1)
            self.manual.append(b)

        self.tree = ttk.Treeview(f, columns=("addr", "value"), show="headings", height=10)
        self.tree.heading("addr", text="位址（候選，未確認）")
        self.tree.heading("value", text="目前數值")
        self.tree.column("addr", width=220)
        self.tree.column("value", width=200)
        self.tree.grid(row=5, column=0, columnspan=4, sticky="nsew")
        add = ttk.Frame(f)
        add.grid(row=6, column=0, columnspan=4, sticky="w", pady=6)
        self.b_add_sel = ttk.Button(add, text="加入選取的到監看清單", command=self.add_selected, state="disabled")
        self.b_add_all = ttk.Button(add, text="全部加入監看清單", command=self.add_all, state="disabled")
        self.b_add_sel.pack(side="left", padx=2)
        self.b_add_all.pack(side="left", padx=2)
        ttk.Label(f, text="⚠ 這些是絕對位址：遊戲重開後就會失效。找到後請在同一次遊戲中直接錄製（例如 A 組）。\n"
                          "程式不判斷哪個是 X、Y、Z，看表格數值跟著角色移動的情形，或錄一段交給 Claude 確認。",
                  foreground="#a00", wraplength=460).grid(row=7, column=0, columnspan=4, sticky="w")
        f.columnconfigure(3, weight=1)
        f.rowconfigure(5, weight=1)

    # ------------------------------------------------------------ worker
    def start(self, more: bool = False) -> None:
        if self._worker and self._worker.is_alive():
            return
        if not more:
            try:
                if self.scanner:
                    self.scanner.close_temp()
                self.scanner = UnknownValueScanner(self.mem_factory(self.target.pid), self.vtype.get())
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("無法讀取記憶體", f"{e}\n\n請確認以系統管理員執行，且遊戲仍在執行。", parent=self)
                return
        self._stop.clear()
        self._set_busy(True)
        self.tree.delete(*self.tree.get_children())
        self._rows = []
        self._worker = threading.Thread(target=self._run, args=(more,), daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()

    def _wait(self, secs: float) -> bool:
        """Sleep, sampling nothing; False if stopped."""
        return not self._stop.wait(secs * self.TIME_SCALE)

    def _phase(self, text: str, secs: float, info: str = "") -> None:
        self._q.put(("phase", text, info, secs))

    def _window(self, mode: str, secs: float, rebase: bool = True) -> bool:
        sc = self.scanner
        sc.begin_window(rebase)
        end = time.monotonic() + secs * self.TIME_SCALE
        while time.monotonic() < end:
            if not self._wait(self.SAMPLE):
                return False
            sc.sample()
        n = sc.end_window(mode)
        self._q.put(("count", n))
        return n > 0

    def _run(self, more: bool) -> None:
        sc = self.scanner
        try:
            if not more:
                self._phase("切到遊戲，讓角色站著不動", self.PREP, "準備中…")
                if not self._wait(self.PREP):
                    return self._q.put(("stopped",))
                self._phase("站著不動（正在掃描記憶體，可能要十幾秒）", 0)
                n = sc.first_scan()
                self._q.put(("count", n))
            for r in range(1, self.rounds.get() + 1):
                self._phase(f"第 {r} 輪：繞著走一小圈（上下左右都走到），然後停下", self.MOVE)
                # first window compares against the snapshot itself (taken while standing still)
                if not self._window("changed", self.MOVE, rebase=more or r > 1):
                    return self._q.put(("done",))
                self._phase(f"第 {r} 輪：停下來，站著不動", self.GRACE + self.STILL)
                if not self._wait(self.GRACE):  # let the character come to a stop
                    return self._q.put(("stopped",))
                if not self._window("unchanged", self.STILL):
                    return self._q.put(("done",))
                if sc.count <= 3:
                    break
            self._q.put(("done",))
        except Exception as e:  # noqa: BLE001
            log.exception("finder failed")
            self._q.put(("error", str(e)))

    # ------------------------------------------------------------ UI side
    def _poll(self) -> None:
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]
                if kind == "phase":
                    _, text, info, secs = msg
                    self.prompt.set(text)
                    self.info.set(info or (f"{secs * self.TIME_SCALE:.0f} 秒" if secs else ""))
                    _beep()
                elif kind == "count":
                    self.count_var.set(f"剩下 {msg[1]:,} 個候選")
                elif kind in ("done", "stopped", "error"):
                    self._finish(kind, msg[1] if kind == "error" else "")
        except queue.Empty:
            pass
        if self._rows:
            self._refresh_values()
        try:
            self.after(200, self._poll)  # also the live table refresh rate
        except tk.TclError:
            pass

    def _finish(self, kind: str, err: str = "") -> None:
        self.finished = kind
        self._set_busy(False)
        _beep()
        n = self.scanner.count if self.scanner else 0
        self.count_var.set(f"剩下 {n:,} 個候選")
        if kind == "error":
            self.prompt.set("發生錯誤")
            self.info.set(err)
        elif n == 0:
            self.prompt.set("沒有找到候選")
            self.info.set("可能型別不對（換 double 或 int32 再試），或移動時角色沒有真的走動。")
        elif n > TABLE_AT:
            self.prompt.set(f"還剩 {n:,} 個候選")
            self.info.set("按「再一輪」繼續縮小，或用手動按鈕。")
        else:
            self.prompt.set(f"找到 {n} 個候選位址")
            self.info.set("移動角色時看哪幾個數值跟著變；選取後加入監看清單。")
            self._show_table()
        self._save_report(kind)

    def _set_busy(self, busy: bool) -> None:
        idle = "disabled" if busy else "normal"
        self.b_start["state"] = idle
        self.b_stop["state"] = "normal" if busy else "disabled"
        has = bool(self.scanner and self.scanner.history)
        self.b_more["state"] = "normal" if (not busy and has and self.scanner.count) else "disabled"
        for b in self.manual:
            b["state"] = "normal" if (not busy and has and self.scanner.count) else "disabled"
        n = self.scanner.count if self.scanner else 0
        self.b_add_sel["state"] = "normal" if (not busy and 0 < n <= TABLE_AT) else "disabled"
        self.b_add_all["state"] = "normal" if (not busy and 0 < n <= ADD_ALL_AT) else "disabled"

    def manual_scan(self, mode: str) -> None:
        if not self.scanner or (self._worker and self._worker.is_alive()):
            return
        n = self.scanner.next_scan(mode)
        self.info.set(f"手動「{mode}」：剩下 {n:,} 個候選")
        self._finish("done")

    def _show_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows = [a for a, _ in self.scanner.results(limit=TABLE_AT)]
        for a in self._rows:
            self.tree.insert("", "end", iid=hex(a), values=(hex(a), ""))
        self._refresh_values()

    def _refresh_values(self) -> None:
        if not self.winfo_exists() or (self._worker and self._worker.is_alive()):
            return
        for a, v in zip(self._rows, self.scanner.read_values(self._rows)):
            text = "（讀不到）" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))
            if self.tree.exists(hex(a)):
                self.tree.set(hex(a), "value", text)

    # ------------------------------------------------------------ results
    def add_selected(self) -> None:
        self._add([int(i, 16) for i in self.tree.selection()])

    def add_all(self) -> None:
        self._add(list(self._rows))

    def _add(self, addrs: list[int]) -> None:
        if not addrs:
            return
        names = {w.name for w in self.app.watch}
        have = {w.address for w in self.app.watch if not w.module and not w.offsets}
        i, added = 0, []
        for a in addrs:
            if a in have:
                continue
            i += 1
            while f"cand_{i:03d}" in names:
                i += 1
            name = f"cand_{i:03d}"
            names.add(name)
            self.app.watch.append(WatchEntry(name=name, type=self.scanner.vtype, address=a))
            added.append(f"{name}={hex(a)}")
        self.app._redraw()
        log.info("finder: added to watch list: %s", ", ".join(added))
        self.info.set(f"已加入 {len(added)} 個到監看清單（名稱 cand_xxx，請在同一次遊戲中錄製）")
        self._save_report("added", added)

    def _save_report(self, event: str, added: list[str] | None = None) -> None:
        """JSON next to the recordings, so the finder steps and candidates can be checked later."""
        sc = self.scanner
        if not sc:
            return
        try:
            out = Path(self.app.out_var.get())
            out.mkdir(parents=True, exist_ok=True)
            if not getattr(self, "_report_path", None):
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._report_path = out / f"finder_{stamp}_{self.target.name}.json"
            data = {
                "format": "gamerec-finder", "event": event,
                "note": "candidate addresses from changed/unchanged filtering; NOT confirmed as coordinates",
                "target": {"pid": self.target.pid, "name": self.target.name},
                "type": sc.vtype, "steps": sc.history,
                "candidates": [{"address": hex(a), "value": v} for a, v in sc.results(limit=TABLE_AT)],
                "candidates_total": sc.count,
                "added_to_watchlist": added or [],
            }
            self._report_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("finder report: %s", self._report_path)
        except OSError:
            log.exception("could not write finder report")

    def close(self) -> None:
        self._stop.set()
        if self.scanner:
            self.scanner.close_temp()
        self.destroy()
