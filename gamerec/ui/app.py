"""tkinter GUI: pick target, edit watch list, start/stop/mark, export."""
from __future__ import annotations

import json
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..build import make_recorders
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
        master.title("GameRec 記憶體 / 封包 / 畫面記錄器")
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self._build()
        self.refresh_processes()

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
        return WatchEntry(name=name, type=vtype, address=int(where, 0))

    @staticmethod
    def fmt_where(e: WatchEntry) -> str:
        if e.module and e.offsets:
            return f"{e.module}+" + ",".join(hex(o) for o in e.offsets)
        if e.module:
            return f"{e.module}+{hex(e.offset or 0)}"
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
        self.session = Session(target, recs)
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
        messagebox.showinfo("匯出完成", f"{zpath}\n\n{summary}\n\n把這個 zip 交給 Claude 分析即可。")


def _ask_text(master, title: str, prompt: str) -> str | None:
    from tkinter import simpledialog

    return simpledialog.askstring(title, prompt, parent=master)


def run_gui() -> int:
    root = tk.Tk()
    root.geometry("760x560")
    App(root)
    root.mainloop()
    return 0
