"""Hotkey settings window: rebind with combos, reject duplicates, restore defaults by mouse."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from ..hotkeys import FORCE_STOP, MODIFIERS, HotkeyConfig, canon_key, pretty

_ALT_MASK = 0x20000 if sys.platform == "win32" else 0x8


def tk_event_combo(event) -> str | None:
    """Combo string from a tk <KeyPress>, or None while only modifiers are held."""
    key = canon_key(event.keysym)
    if key in MODIFIERS or key in ("??", ""):
        return None
    mods = []
    if event.state & 0x4:
        mods.append("ctrl")
    if event.state & _ALT_MASK:
        mods.append("alt")
    if event.state & 0x1:
        mods.append("shift")
    return "+".join([*mods, key])


class HotkeyDialog(tk.Toplevel):
    def __init__(self, master, config: HotkeyConfig, on_change=None, on_capture=None) -> None:
        super().__init__(master)
        self.title("熱鍵設定")
        self.transient(master)
        self.resizable(False, False)
        self.config_ = config
        self.on_change = on_change
        self.on_capture = on_capture  # callback(bool): pause global hotkeys while capturing
        self.capturing: str | None = None
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.bind("<KeyPress>", self._key)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def refresh(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        ttk.Label(self.body, text="點「變更」後按下新的按鍵或組合鍵（例如 Ctrl+Shift+F9）",
                  foreground="#555").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        r = 1
        for key, action in self.config_.actions.items():
            ttk.Label(self.body, text=action.label, width=16).grid(row=r, column=0, sticky="w")
            text = "請按下按鍵…" if self.capturing == key else pretty(self.config_.bindings[key])
            ttk.Label(self.body, text=text, width=22, relief="groove", anchor="center").grid(row=r, column=1, padx=6, pady=2)
            if self.capturing == key:
                ttk.Button(self.body, text="取消", command=self.cancel).grid(row=r, column=2)
            else:
                ttk.Button(self.body, text="變更", command=lambda k=key: self.capture(k)).grid(row=r, column=2)
            r += 1
        ttk.Label(self.body, text="強制停止（固定）", width=16).grid(row=r, column=0, sticky="w")
        ttk.Label(self.body, text=pretty(FORCE_STOP), width=22, relief="groove", anchor="center",
                  foreground="#a00").grid(row=r, column=1, padx=6, pady=2)
        ttk.Label(self.body, text="不可修改", foreground="#888").grid(row=r, column=2)
        bar = ttk.Frame(self.body)
        bar.grid(row=r + 1, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="恢復預設熱鍵", command=self.restore).pack(side="left", padx=4)
        ttk.Button(bar, text="關閉", command=self.close).pack(side="left")

    def capture(self, key: str) -> None:
        self.capturing = key
        if self.on_capture:
            self.on_capture(True)
        self.refresh()
        self.focus_force()

    def cancel(self) -> None:
        self.capturing = None
        if self.on_capture:
            self.on_capture(False)
        self.refresh()

    def _key(self, event) -> None:
        if not self.capturing:
            return
        combo = tk_event_combo(event)
        if combo is None:
            return
        try:
            self.config_.set(self.capturing, combo)
        except ValueError as e:
            messagebox.showerror("無法綁定", str(e), parent=self)
            return
        self.cancel()
        if self.on_change:
            self.on_change()

    def restore(self) -> None:
        self.config_.restore_defaults()
        self.cancel()
        if self.on_change:
            self.on_change()

    def close(self) -> None:
        if self.capturing:
            self.cancel()
        self.destroy()
