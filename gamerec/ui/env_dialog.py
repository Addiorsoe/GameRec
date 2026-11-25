"""Environment check window: lists requirements with one-click fixes, plus update check."""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from .. import __version__
from ..env_check import (
    NPCAP_URL,
    check_update,
    download_update,
    install_npcap,
    open_folder,
    relaunch_as_admin,
    run_checks,
)


class EnvDialog(tk.Toplevel):
    def __init__(self, master, on_change=None) -> None:
        super().__init__(master)
        self.title("環境檢查")
        self.transient(master)
        self.resizable(False, False)
        self.on_change = on_change
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.status = tk.StringVar()
        self.update_info: dict | None = None
        self.refresh()
        self.check_update_async()

    # ------------------------------------------------------------ layout
    def refresh(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        ttk.Label(self.body, text=f"GameRec {__version__} 執行環境", font=("", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.checks = run_checks()
        for i, c in enumerate(self.checks, start=1):
            icon = "✅" if c.ok else ("❌" if c.required else "⚠️")
            ttk.Label(self.body, text=f"{icon} {c.name}", width=18).grid(row=i, column=0, sticky="w")
            ttk.Label(self.body, text=c.detail, wraplength=330).grid(row=i, column=1, sticky="w", padx=6)
            if c.fix_label:
                ttk.Button(self.body, text=c.fix_label, command=lambda k=c.key: self.fix(k)).grid(row=i, column=2, sticky="e")
        r = len(self.checks) + 1
        ttk.Separator(self.body).grid(row=r, column=0, columnspan=3, sticky="ew", pady=8)
        self.upd_label = ttk.Label(self.body, text="🔄 檢查新版本中…")
        self.upd_label.grid(row=r + 1, column=0, columnspan=2, sticky="w")
        self.upd_btn = ttk.Button(self.body, text="下載新版", command=self.do_update)
        self.upd_btn.grid(row=r + 1, column=2, sticky="e")
        self.upd_btn.grid_remove()
        if self.update_info is not None:
            self._show_update(self.update_info)
        self.progress = ttk.Progressbar(self.body, length=480, mode="determinate")
        self.progress.grid(row=r + 2, column=0, columnspan=3, pady=(8, 0))
        self.progress.grid_remove()
        ttk.Label(self.body, textvariable=self.status, foreground="#555").grid(row=r + 3, column=0, columnspan=3, sticky="w")
        bar = ttk.Frame(self.body)
        bar.grid(row=r + 4, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(bar, text="重新檢查", command=self.recheck).pack(side="left", padx=4)
        ttk.Button(bar, text="關閉", command=self.destroy).pack(side="left")

    def recheck(self) -> None:
        self.refresh()
        if self.on_change:
            self.on_change()

    # -------------------------------------------------------------- fixes
    def fix(self, key: str) -> None:
        if key == "admin":
            if relaunch_as_admin():
                self.master.destroy()
            else:
                messagebox.showerror("無法提升權限", "已取消或失敗。請對 GameRec.exe 按右鍵 →「以系統管理員身分執行」。", parent=self)
        elif key == "npcap":
            if not messagebox.askokcancel(
                "安裝 Npcap",
                "將從官方網站 npcap.com 下載 Npcap 安裝程式並開啟。\n\n"
                "安裝時請勾選「Install Npcap in WinPcap API-compatible Mode」，\n"
                "裝完後按「重新檢查」。",
                parent=self,
            ):
                return
            self._run_bg("下載 Npcap 中…", install_npcap, done="安裝程式已開啟，完成後按「重新檢查」。",
                         fail_url=NPCAP_URL)

    # ------------------------------------------------------------ updates
    def check_update_async(self) -> None:
        def work():
            try:
                info = check_update()
                self.after(0, lambda: self._show_update(info or {}))
            except Exception:  # noqa: BLE001 - offline or no release yet
                self.after(0, lambda: self.upd_label.configure(text="ℹ️ 無法檢查新版本（離線或尚未發佈）"))

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, info: dict) -> None:
        self.update_info = info
        if not self.winfo_exists():
            return
        if info:
            self.upd_label.configure(text=f"🆕 有新版本 {info['version']}（目前 {__version__}）")
            self.upd_btn.grid()
        else:
            self.upd_label.configure(text=f"✅ 已是最新版本（{__version__}）")

    def do_update(self) -> None:
        info = self.update_info or {}
        if not info.get("exe_url"):
            webbrowser.open(info.get("url", ""))
            return

        def after(path):
            if messagebox.askyesno("下載完成", f"新版已存到：\n{path}\n\n關閉目前程式後執行新版即可。要開啟資料夾嗎？", parent=self):
                open_folder(path.parent)

        self._run_bg("下載新版中…", lambda p: download_update(info, p), on_done=after, fail_url=info.get("url"))

    # ------------------------------------------------------------ helpers
    def _run_bg(self, msg, fn, done=None, on_done=None, fail_url=None) -> None:
        self.status.set(msg)
        self.progress.grid()
        self.progress["value"] = 0

        def prog(n, total):
            if total:
                self.after(0, lambda: self.progress.configure(value=n * 100 / total))

        def work():
            try:
                result = fn(prog)
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda e=e: self._failed(e, fail_url))
                return

            def finish():
                self.progress.grid_remove()
                self.status.set(done or "完成")
                if on_done:
                    on_done(result)

            self.after(0, finish)

        threading.Thread(target=work, daemon=True).start()

    def _failed(self, e, url) -> None:
        self.progress.grid_remove()
        self.status.set(f"失敗：{e}")
        if url and messagebox.askyesno("下載失敗", f"{e}\n\n要改用瀏覽器開啟下載頁嗎？", parent=self):
            webbrowser.open(url)
