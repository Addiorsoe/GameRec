"""tkinter GUI: pick target, edit watch list, start/stop/mark, export."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..build import make_recorders
from ..env_check import default_output_dir, is_admin, is_windows, npcap_installed, open_folder, run_checks
from ..hotkeys import FORCE_STOP, HotkeyConfig, GlobalHotkeys, pretty
from .env_dialog import EnvDialog
from .hotkey_dialog import HotkeyDialog
from ..export import export_session
from ..memory.watcher import WatchEntry, load_watchlist
from ..process import TargetInfo, get_target, list_processes, target_from_foreground
from ..session import Session, next_take

TYPES = ["int32", "uint32", "int16", "int64", "float", "double", "uint8", "bytes:16"]


GROUPS = ("A", "B", "C", "D", "E", "F", "G")


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=8)
        self.master = master
        self.session: Session | None = None
        self.watch: list[WatchEntry] = []
        self._last_mark = 0.0
        self.last_zip: Path | None = None
        self._saved_notes = ""
        self._hotkeys: queue.Queue[str] = queue.Queue()  # filled by the global key hook thread
        self._hk_paused = False
        self._busy = False  # True while starting/stopping: hotkeys must not re-enter
        self._empty_watch_ok = False  # user confirmed recording without memory values this run
        self.locked: TargetInfo | None = None  # set by the lock-target hotkey
        self.hk_config = HotkeyConfig(path=default_output_dir().parent / "hotkeys.json")
        master.title(f"GameRec {__version__}  記憶體 / 封包 / 畫面記錄器")
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self._build()
        self._redraw()
        self.refresh_processes()
        self._start_hotkeys()
        self.after(300, self.check_environment)

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        tgt = ttk.LabelFrame(self, text="目標程式", padding=6)
        tgt.grid(row=0, column=0, sticky="ew")
        self.proc_var = tk.StringVar()
        self.proc_box = ttk.Combobox(tgt, textvariable=self.proc_var, width=50)
        self.proc_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(tgt, text="重新整理", command=self.refresh_processes).grid(row=0, column=1, padx=4)
        self.lock_var = tk.StringVar()
        ttk.Label(tgt, textvariable=self.lock_var, foreground="#0a6").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.proc_box.bind("<<ComboboxSelected>>", lambda _e: self.unlock())
        tgt.columnconfigure(0, weight=1)

        mem = ttk.LabelFrame(self, text="記憶體監看清單", padding=6)
        mem.grid(row=1, column=0, sticky="nsew", pady=6)
        cols = ("name", "type", "where")
        self.tree = ttk.Treeview(mem, columns=cols, show="headings", height=7)
        for c, w, t in zip(cols, (140, 80, 320), ("名稱", "型別", "位址 / 模組+偏移 / 指標鏈")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.grid(row=0, column=0, columnspan=6, sticky="nsew")
        self.e_name, self.e_addr = tk.StringVar(), tk.StringVar()
        self.e_type = tk.StringVar(value="float")
        ttk.Entry(mem, textvariable=self.e_name, width=14).grid(row=1, column=0)
        ttk.Combobox(mem, textvariable=self.e_type, values=TYPES, width=9).grid(row=1, column=1)
        ttk.Entry(mem, textvariable=self.e_addr, width=36).grid(row=1, column=2, sticky="ew")
        ttk.Button(mem, text="新增", command=self.add_watch).grid(row=1, column=3)
        ttk.Button(mem, text="刪除", command=self.del_watch).grid(row=1, column=4)
        bar = ttk.Frame(mem)
        bar.grid(row=2, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Button(bar, text="匯入 JSON", command=self.load_watch).pack(side="left")
        ttk.Button(bar, text="另存 JSON", command=self.save_watch).pack(side="left", padx=4)
        ttk.Label(bar, text="位址格式：0x1234ABCD ｜ game.exe+0x2233F0 ｜ game.exe+0x1F00A0,0x10,0x48").pack(side="left", padx=8)
        self.watch_hint = tk.StringVar()
        ttk.Label(mem, textvariable=self.watch_hint, foreground="#a00").grid(row=3, column=0, columnspan=6, sticky="w")
        mem.columnconfigure(2, weight=1)
        mem.rowconfigure(0, weight=1)

        opt = ttk.LabelFrame(self, text="錄製設定", padding=6)
        opt.grid(row=2, column=0, sticky="ew")
        self.v_net, self.v_vid, self.v_inp = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="封包", variable=self.v_net).grid(row=0, column=0)
        ttk.Checkbutton(opt, text="錄影", variable=self.v_vid).grid(row=0, column=1)
        ttk.Checkbutton(opt, text="鍵鼠輸入", variable=self.v_inp).grid(row=0, column=2)
        self.v_rate, self.v_fps, self.v_scale = tk.DoubleVar(value=30), tk.DoubleVar(value=15), tk.DoubleVar(value=0.75)
        for i, (lbl, var) in enumerate((("記憶體 Hz", self.v_rate), ("FPS", self.v_fps), ("縮放", self.v_scale))):
            ttk.Label(opt, text=lbl).grid(row=0, column=3 + i * 2, padx=(10, 2))
            ttk.Entry(opt, textvariable=var, width=6).grid(row=0, column=4 + i * 2)
        self.env_var = tk.StringVar()
        self.env_label = ttk.Label(opt, textvariable=self.env_var)
        self.env_label.grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))
        self.b_npcap = ttk.Button(opt, text="安裝 Npcap", command=self.open_env)
        self.b_npcap.grid(row=1, column=6, columnspan=2, sticky="w", pady=(4, 0))
        self.b_npcap.grid_remove()
        ttk.Button(opt, text="環境檢查 / 更新", command=self.open_env).grid(row=1, column=8, sticky="w", pady=(4, 0))

        out = ttk.Frame(opt)
        out.grid(row=2, column=0, columnspan=9, sticky="ew", pady=(4, 0))
        ttk.Label(out, text="輸出資料夾").pack(side="left")
        self.out_var = tk.StringVar(value=str(default_output_dir()))
        ttk.Entry(out, textvariable=self.out_var, width=50).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(out, text="選擇…", command=self.pick_out).pack(side="left")
        ttk.Button(out, text="開啟", command=lambda: self._open(self.out_var.get())).pack(side="left", padx=2)

        rec = ttk.LabelFrame(self, text="本次錄製（一段錄一個動作，每段各自匯出一個 zip）", padding=6)
        rec.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(rec, text="組別").grid(row=0, column=0, sticky="w")
        self.group_var = tk.StringVar()
        ttk.Combobox(rec, textvariable=self.group_var, values=GROUPS, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(rec, text="檔名會加上組別與流水號，例如 _A-03_", foreground="#555").grid(row=0, column=2, columnspan=2, sticky="w")
        ttk.Label(rec, text="備註").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.notes_var = tk.StringVar()
        ttk.Entry(rec, textvariable=self.notes_var).grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=(4, 0))
        self.b_notes = ttk.Button(rec, text="更新上一段的備註", command=self.save_notes, state="disabled")
        self.b_notes.grid(row=1, column=3, sticky="e", pady=(4, 0))
        self.last_var = tk.StringVar(value="錄製前可先填備註；停止時不會跳出視窗，事後也能補寫再按「更新上一段的備註」。")
        ttk.Label(rec, textvariable=self.last_var, foreground="#555", wraplength=760).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        rec.columnconfigure(2, weight=1)

        ctl = ttk.Frame(self, padding=(0, 6))
        ctl.grid(row=4, column=0, sticky="ew")
        self.b_start = ttk.Button(ctl, text="● 開始", command=self.start)
        self.b_mark = ttk.Button(ctl, text="加標記", command=self.mark, state="disabled")
        self.b_stop = ttk.Button(ctl, text="■ 停止並匯出", command=self.stop, state="disabled")
        for b in (self.b_start, self.b_mark, self.b_stop):
            b.pack(side="left", padx=2)
        ttk.Button(ctl, text="熱鍵設定…", command=self.open_hotkeys).pack(side="right", padx=2)
        self.mark_var = tk.StringVar()
        ttk.Entry(ctl, textvariable=self.mark_var, width=24).pack(side="left", padx=4)
        self.hk_hint = tk.StringVar()
        ttk.Label(ctl, textvariable=self.hk_hint, foreground="#a00").pack(side="right", padx=6)
        self.status = tk.StringVar(value="就緒")
        ttk.Label(self, textvariable=self.status, foreground="#555").grid(row=5, column=0, sticky="w")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._label_hotkeys()

    # ---------------------------------------------------------- environment
    def check_environment(self, auto_open: bool = True) -> None:
        """Refresh the status line; open the environment window if anything is missing."""
        checks = run_checks()
        if not getattr(self, "_hw_started", False):  # PC specs: scanned once, alongside the first check
            self._hw_started = True
            threading.Thread(target=_scan_hardware_once, daemon=True).start()
        msgs = []
        if not is_admin():
            msgs.append("⚠ 未以系統管理員執行（讀記憶體可能失敗）")
        if npcap_installed():
            msgs.append("✓ Npcap 已安裝，可以抓封包")
            self.b_npcap.grid_remove()
        else:
            msgs.append("✗ 未安裝 Npcap：封包功能停用（記憶體與錄影照常）")
            self.v_net.set(False)
            self.b_npcap.grid()
        self.env_var.set("　".join(msgs))
        if auto_open and not all(c.ok for c in checks) and not getattr(self, "_env_shown", False):
            self._env_shown = True
            self.open_env()

    def open_env(self) -> None:
        dlg = getattr(self, "_env_dlg", None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            return
        self._env_dlg = EnvDialog(self.master, on_change=lambda: self.check_environment(auto_open=False))

    def pick_out(self) -> None:
        p = filedialog.askdirectory(initialdir=self.out_var.get())
        if p:
            self.out_var.set(p)

    def _open(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        open_folder(path)

    # --------------------------------------------------------------- target
    def refresh_processes(self) -> None:
        try:
            self.proc_box["values"] = [f"{name}  [{pid}]" for pid, name in list_processes()]
        except Exception as e:  # noqa: BLE001
            self.status.set(f"無法列出程式：{e}")

    def lock_foreground(self) -> None:
        """Lock the target to the window that currently has focus (hotkey)."""
        if self.session and self.session.started and not self.session.stopped:
            self.status.set("錄製中不能更換目標")
            return
        t = target_from_foreground()
        if t is None:
            self.status.set("無法取得目前視窗（只支援 Windows）")
            return
        import os

        if t.pid == os.getpid():
            self.status.set(f"請先點一下遊戲視窗，再按 {pretty(self.hk_config.bindings['lock_target'])}")
            return
        self.locked = t
        self.proc_var.set(f"{t.name}  [{t.pid}]")
        self.lock_var.set(f"🔒 已鎖定：{t.name} [{t.pid}]｜視窗「{t.window_title}」（錄影只錄這個視窗）")
        self.status.set(f"已鎖定 {t.name}")
        try:
            self.master.bell()
        except tk.TclError:
            pass

    def unlock(self) -> None:
        self.locked = None
        self._lock_hint()

    def _lock_hint(self) -> None:
        if not self.locked:
            self.lock_var.set(f"建議：切到遊戲視窗按 {pretty(self.hk_config.bindings['lock_target'])} 鎖定目標，"
                              "避免抓到錯誤的程式（下拉選單會自動猜視窗，可能不準）")

    def _pid(self) -> int:
        s = self.proc_var.get()
        if "[" not in s:
            raise ValueError("請先選擇目標程式")
        return int(s.rsplit("[", 1)[1].rstrip("]"))

    # ------------------------------------------------------------ watchlist
    @staticmethod
    def parse_where(name: str, vtype: str, where: str) -> WatchEntry:
        where = where.strip()
        if "+" in where:
            module, rest = where.split("+", 1)
            offs = [int(x.strip(), 0) for x in rest.split(",")]
            if len(offs) == 1:
                return WatchEntry(name=name, type=vtype, module=module.strip(), offset=offs[0])
            return WatchEntry(name=name, type=vtype, module=module.strip(), offsets=offs)
        if "," in where:  # absolute pointer base + chain: 0x1234,0x10,0x8
            first, *rest = [int(x.strip(), 0) for x in where.split(",")]
            return WatchEntry(name=name, type=vtype, address=first, offsets=[0, *rest])
        return WatchEntry(name=name, type=vtype, address=int(where, 0))

    @staticmethod
    def fmt_where(e: WatchEntry) -> str:
        if e.module and e.offsets:
            return f"{e.module}+" + ",".join(hex(o) for o in e.offsets)
        if e.module:
            return f"{e.module}+{hex(e.offset or 0)}"
        if e.offsets:
            return ",".join([hex(e.address or 0), *(hex(o) for o in e.offsets[1:])])
        return hex(e.address or 0)

    def _redraw(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for e in self.watch:
            self.tree.insert("", "end", values=(e.name, e.type, self.fmt_where(e)))
        self.watch_hint.set("" if self.watch else "⚠ 清單是空的：錄製時不會記錄任何記憶體數值（例如座標）。請新增位址或「匯入 JSON」。")

    def add_watch(self) -> None:
        try:
            name = self.e_name.get().strip() or f"v{len(self.watch) + 1}"
            if any(w.name == name for w in self.watch):
                raise ValueError(f"名稱「{name}」已存在，請換一個（同名會讓資料混在一起）")
            self.watch.append(self.parse_where(name, self.e_type.get(), self.e_addr.get()))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("位址格式錯誤", str(e))
            return
        self._redraw()

    def del_watch(self) -> None:
        idx = [self.tree.index(i) for i in self.tree.selection()]
        self.watch = [w for i, w in enumerate(self.watch) if i not in idx]
        self._redraw()

    def load_watch(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            try:
                self.watch = load_watchlist(p)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("無法匯入", str(e))
                return
            self._redraw()

    def save_watch(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump([w.to_dict() for w in self.watch], f, indent=2, ensure_ascii=False)

    # -------------------------------------------------------------- session
    def start(self) -> None:
        if self._busy or (self.session and self.session.started and not self.session.stopped):
            return
        if self.locked:
            import psutil

            try:
                alive = psutil.Process(self.locked.pid).name() == self.locked.name
            except psutil.Error:
                alive = False
            if not alive:  # the pid may now belong to a different program
                self.unlock()
                messagebox.showerror("無法開始", "鎖定的程式已經關閉，請重新切到遊戲視窗按鎖定熱鍵。")
                return
        try:
            target = self.locked or get_target(self._pid())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("無法開始", str(e))
            return
        try:
            rate, fps, scale = self.v_rate.get(), self.v_fps.get(), self.v_scale.get()
            if rate <= 0 or fps <= 0 or not 0 < scale <= 4:
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror("設定錯誤", "記憶體 Hz、FPS 需大於 0，縮放需介於 0～4")
            return
        recs = make_recorders(
            self.watch, network=self.v_net.get(), video=self.v_vid.get(), inputs=self.v_inp.get(),
            rate_hz=rate, fps=fps, scale=scale,
        )
        if self.v_net.get() and not npcap_installed():
            messagebox.showwarning("Npcap", "未安裝 Npcap，這次將不會記錄封包。")
        if not recs:
            messagebox.showerror("無法開始", "沒有任何可記錄的項目：請新增監看位址或勾選封包/錄影/鍵鼠。")
            return
        if not self.watch and not self._empty_watch_ok:
            # asked before recording starts (not during), once per run
            if not messagebox.askyesno(
                "記憶體監看清單是空的",
                "這次錄製不會記錄任何記憶體數值（例如角色座標）。\n\n仍要開始嗎？\n"
                "（選「是」之後，這次開啟程式期間不再提醒）",
            ):
                self.status.set("已取消：請先新增監看位址或匯入 JSON")
                return
            self._empty_watch_ok = True
        group = self.group_var.get().strip()
        take = next_take(self.out_var.get(), group) if group else None
        if self.last_zip is not None and self.notes_var.get() == self._saved_notes:
            self.notes_var.set("")  # the previous segment's note is already saved with it
        self.session = Session(target, recs, root=self.out_var.get(), group=group, take=take)
        self.session.start()
        self.b_notes["state"] = "disabled"
        self.b_start["state"], self.b_mark["state"], self.b_stop["state"] = "disabled", "normal", "normal"
        self._tick()

    # -------------------------------------------------------------- hotkeys
    def _start_hotkeys(self) -> None:
        def fire(action: str) -> None:  # pynput thread: never touch tkinter here
            if not self._hk_paused or action == "force_stop":
                self._hotkeys.put(action)

        self._global_hk = GlobalHotkeys(self.hk_config, fire)
        try:
            self._global_hk.start()
        except Exception as e:  # noqa: BLE001 - app still works with buttons
            logging.getLogger("gamerec").warning("global hotkeys unavailable: %s", e)
        self._poll_hotkeys()

    def _poll_hotkeys(self) -> None:
        try:
            while not self._hotkeys.empty():
                self.handle_hotkey(self._hotkeys.get_nowait())
        finally:  # an error in one action must not silently kill hotkeys for the rest of the run
            self.after(50, self._poll_hotkeys)

    def handle_hotkey(self, action: str) -> None:
        if self._busy:  # a stop/export dialog is open: ignore (prevents nested stop/start)
            return
        recording = bool(self.session and self.session.started and not self.session.stopped)
        if action == "force_stop":
            if recording:
                self.stop(forced=True)
        elif action == "mark" and recording:
            self.mark()
        elif action == "lock_target" and not recording:
            self.lock_foreground()
        elif action == "start_stop":
            self.stop() if recording else self.start()

    def _label_hotkeys(self) -> None:
        b = self.hk_config.bindings
        self.b_start.configure(text=f"● 開始 ({pretty(b['start_stop'])})")
        self.b_stop.configure(text=f"■ 停止並匯出 ({pretty(b['start_stop'])})")
        self.b_mark.configure(text=f"加標記 ({pretty(b['mark'])})")
        self.hk_hint.set(f"鎖定目標：{pretty(b['lock_target'])}　強制停止：{pretty(FORCE_STOP)}（固定）")
        self._lock_hint()

    def open_hotkeys(self) -> None:
        dlg = getattr(self, "_hk_dlg", None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            return
        self._hk_dlg = HotkeyDialog(self.master, self.hk_config, on_change=self._label_hotkeys,
                                    on_capture=lambda on: setattr(self, "_hk_paused", on))

    def _tick(self) -> None:
        s = self.session
        if not s or s.stopped:
            return
        secs = s.clock.now_ns() / 1e9
        parts = [f"{k}:{st} {n}" for k, (st, n) in s.stats().items()]
        self.status.set(f"錄製中 {secs:6.1f}s ｜ " + " ｜ ".join(parts) + f" ｜ 標記 {len(s.markers)}")
        self.after(500, self._tick)

    def mark(self) -> None:
        now = time.monotonic()
        if now - self._last_mark < 0.3:  # F9 can arrive from both tk and the global key hook
            return
        self._last_mark = now
        if self.session and not self.session.stopped:
            m = self.session.mark(self.mark_var.get())
            self.mark_var.set("")
            self.status.set(f"已加標記 @ {m['t_ns'] / 1e9:.2f}s：{m['label']}")

    def stop(self, forced: bool = False) -> None:
        """Stop and export right away. No dialog: it would steal focus from the game."""
        if self._busy or not self.session or self.session.stopped:
            return
        self._busy = True
        try:
            self.session.stop()
            self.status.set("已停止，正在匯出…")
            self.update_idletasks()
            notes = self.notes_var.get().strip()
            if forced:
                notes = (notes + " " if notes else "") + "（使用強制停止鍵結束）"
            zpath = export_session(self.session, notes=notes)
        finally:
            self._busy = False
        self.b_start["state"], self.b_mark["state"], self.b_stop["state"] = "normal", "disabled", "disabled"
        self.last_zip, self._saved_notes = zpath, self.notes_var.get()
        self.b_notes["state"] = "normal"
        summary = "，".join(f"{k} {st} {n}" for k, (st, n) in self.session.stats().items())
        self.status.set(f"已匯出 {zpath.name}")
        self.last_var.set(f"上一段：{zpath.name}（{summary}）")

    def save_notes(self) -> None:
        """Rewrite the last segment's zip with the note typed afterwards."""
        if self._busy or not self.session or not self.session.stopped or self.last_zip is None:
            return
        notes = self.notes_var.get().strip()
        if self.session.notes.endswith("（使用強制停止鍵結束）"):
            notes = (notes + " " if notes else "") + "（使用強制停止鍵結束）"
        self.last_zip = export_session(self.session, out_dir=self.last_zip.parent, notes=notes)
        self._saved_notes = self.notes_var.get()
        self.status.set(f"已更新備註：{self.last_zip.name}")


def _scan_hardware_once() -> None:
    try:
        from ..sysinfo import load_or_scan

        load_or_scan()
    except Exception:  # noqa: BLE001 - specs are informational, never block the app
        logging.getLogger("gamerec").exception("hardware scan failed")


def _crash_log() -> Path:
    p = default_output_dir().parent / "gamerec_error.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def run_gui(smoke_file: str | None = None) -> int:
    if is_windows():
        try:  # crisp text on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    root.geometry("860x620")

    last_dialog = [0.0]

    def report(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        if smoke_file:  # never block a packaging check on a modal dialog
            with open(smoke_file, "w", encoding="utf-8") as f:
                json.dump({"ok": False, "error": str(val), "trace": text}, f, ensure_ascii=False)
            root.destroy()
            return
        logging.getLogger("gamerec").error(text)
        try:
            with open(_crash_log(), "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        # one dialog at a time, at most every 5 s: a repeating error must not flood the screen
        now = time.monotonic()
        if now - last_dialog[0] < 5:
            return
        last_dialog[0] = float("inf")
        try:
            messagebox.showerror("發生錯誤", f"{val}\n\n詳細記錄：{_crash_log()}")
        finally:
            last_dialog[0] = time.monotonic()

    root.report_callback_exception = report
    app = App(root)

    def on_close():
        if app.session and app.session.started and not app.session.stopped:
            if not messagebox.askyesno("離開", "仍在錄製中，要停止並匯出後再離開嗎？\n（選「否」直接離開，資料不會打包）"):
                app.session.stop()
                root.destroy()
                return
            app.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    if smoke_file:
        _arm_smoke(root, app, smoke_file)
    root.mainloop()
    return 0


def _arm_smoke(root, app, smoke_file: str) -> None:
    """Packaging check: prove the window, recorder modules and env checks load, then exit."""

    def finish():
        info = {"ok": True, "version": __version__}
        try:
            import importlib

            for mod in ("cv2", "mss", "numpy", "psutil", "pymem", "pynput.keyboard", "scapy.all",
                        "gamerec.memory.scanner", "gamerec.net.capture", "gamerec.video.recorder",
                        "gamerec.inputs.logger", "gamerec.env_check", "gamerec.ui.env_dialog",
                        "gamerec.hotkeys", "gamerec.ui.hotkey_dialog"):
                importlib.import_module(mod)
            info["processes"] = len(app.proc_box["values"])
            info["env"] = {c.key: c.ok for c in run_checks()}
            from ..sysinfo import load_or_scan
            info["sysinfo"] = load_or_scan()
            info["env_fixes"] = [c.fix_label for c in run_checks() if c.fix_label]  # one-click installer bundled
            info["hotkeys"] = app.hk_config.bindings
        except Exception as e:  # noqa: BLE001
            info = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
        with open(smoke_file, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        root.destroy()

    root.after(4000, finish)
