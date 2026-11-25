"""tkinter GUI: pick target, edit watch list, start/stop/mark, export."""
from __future__ import annotations

import json
import logging
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..build import make_recorders
from ..env_check import default_output_dir, is_admin, is_windows, npcap_installed, open_folder, run_checks
from .env_dialog import EnvDialog
from ..export import export_session
from ..memory.watcher import WatchEntry
from ..process import get_target, list_processes
from ..session import Session

TYPES = ["int32", "uint32", "int16", "int64", "float", "double", "uint8", "bytes:16"]


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=8)
        self.master = master
        self.session: Session | None = None
        self.watch: list[WatchEntry] = []
        self._last_mark = 0.0
        self.last_zip: Path | None = None
        master.title(f"GameRec {__version__}  記憶體 / 封包 / 畫面記錄器")
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self._build()
        self.refresh_processes()
        self.after(300, self.check_environment)

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        tgt = ttk.LabelFrame(self, text="目標程式", padding=6)
        tgt.grid(row=0, column=0, sticky="ew")
        self.proc_var = tk.StringVar()
        self.proc_box = ttk.Combobox(tgt, textvariable=self.proc_var, width=50)
        self.proc_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(tgt, text="重新整理", command=self.refresh_processes).grid(row=0, column=1, padx=4)
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
        mem.columnconfigure(2, weight=1)
        mem.rowconfigure(0, weight=1)

        opt = ttk.LabelFrame(self, text="錄製設定", padding=6)
        opt.grid(row=2, column=0, sticky="ew")
        self.v_net, self.v_vid, self.v_inp = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="封包", variable=self.v_net).grid(row=0, column=0)
        ttk.Checkbutton(opt, text="錄影", variable=self.v_vid).grid(row=0, column=1)
        ttk.Checkbutton(opt, text="鍵鼠輸入", variable=self.v_inp).grid(row=0, column=2)
        self.v_rate, self.v_fps, self.v_scale = tk.DoubleVar(value=30), tk.DoubleVar(value=15), tk.DoubleVar(value=1.0)
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

        ctl = ttk.Frame(self, padding=(0, 6))
        ctl.grid(row=3, column=0, sticky="ew")
        self.b_start = ttk.Button(ctl, text="● 開始", command=self.start)
        self.b_mark = ttk.Button(ctl, text="加標記 (F9)", command=self.mark, state="disabled")
        self.b_stop = ttk.Button(ctl, text="■ 停止並匯出", command=self.stop, state="disabled")
        for b in (self.b_start, self.b_mark, self.b_stop):
            b.pack(side="left", padx=2)
        self.mark_var = tk.StringVar()
        ttk.Entry(ctl, textvariable=self.mark_var, width=24).pack(side="left", padx=4)
        self.status = tk.StringVar(value="就緒")
        ttk.Label(self, textvariable=self.status, foreground="#555").grid(row=4, column=0, sticky="w")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.master.bind("<F9>", lambda _e: self.mark())

    # ---------------------------------------------------------- environment
    def check_environment(self, auto_open: bool = True) -> None:
        """Refresh the status line; open the environment window if anything is missing."""
        checks = run_checks()
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

    def add_watch(self) -> None:
        try:
            name = self.e_name.get().strip() or f"v{len(self.watch) + 1}"
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
            with open(p, encoding="utf-8") as f:
                self.watch = [WatchEntry.from_dict(d) for d in json.load(f)]
            self._redraw()

    def save_watch(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump([w.to_dict() for w in self.watch], f, indent=2, ensure_ascii=False)

    # -------------------------------------------------------------- session
    def start(self) -> None:
        try:
            target = get_target(self._pid())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("無法開始", str(e))
            return
        recs = make_recorders(
            self.watch, network=self.v_net.get(), video=self.v_vid.get(), inputs=self.v_inp.get(),
            rate_hz=self.v_rate.get(), fps=self.v_fps.get(), scale=self.v_scale.get(),
        )
        if self.v_net.get() and not npcap_installed():
            messagebox.showwarning("Npcap", "未安裝 Npcap，這次將不會記錄封包。")
        if not recs:
            messagebox.showerror("無法開始", "沒有任何可記錄的項目：請新增監看位址或勾選封包/錄影/鍵鼠。")
            return
        self.session = Session(target, recs, root=self.out_var.get())
        for r in recs:  # F9 also works while the game window has focus
            if r.kind == "input":
                r.on_key = lambda k: k == "f9" and self.master.after(0, self.mark)
        self.session.start()
        self.b_start["state"], self.b_mark["state"], self.b_stop["state"] = "disabled", "normal", "normal"
        self._tick()

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

    def stop(self) -> None:
        if not self.session:
            return
        self.status.set("停止中，正在匯出…")
        self.update_idletasks()
        notes = _ask_text(self.master, "匯出備註", "這段錄影在做什麼？（例如：從城鎮走到 A 點，途中按 W 前進）")
        zpath = export_session(self.session, notes=notes or "")
        self.b_start["state"], self.b_mark["state"], self.b_stop["state"] = "normal", "disabled", "disabled"
        summary = "\n".join(f"{k}: {st} ({n})" for k, (st, n) in self.session.stats().items())
        self.status.set(f"已匯出 {zpath}")
        self.last_zip = zpath
        if messagebox.askyesno("匯出完成", f"{zpath}\n\n{summary}\n\n把這個 zip 交給 Claude 分析即可。\n要開啟所在資料夾嗎？"):
            open_folder(zpath.parent)


def _ask_text(master, title: str, prompt: str) -> str | None:
    from tkinter import simpledialog

    return simpledialog.askstring(title, prompt, parent=master)


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

    def report(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        logging.getLogger("gamerec").error(text)
        try:
            with open(_crash_log(), "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        messagebox.showerror("發生錯誤", f"{val}\n\n詳細記錄：{_crash_log()}")

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
                        "gamerec.inputs.logger"):
                importlib.import_module(mod)
            info["processes"] = len(app.proc_box["values"])
            info["env"] = {c.key: c.ok for c in run_checks()}
        except Exception as e:  # noqa: BLE001
            info = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
        with open(smoke_file, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        root.destroy()

    root.after(4000, finish)
