"""The game locked last time is locked again automatically once its window exists."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("needs a display", allow_module_level=True)

from gamerec.process import TargetInfo  # noqa: E402
from gamerec.ui import app as app_mod  # noqa: E402


def test_auto_lock_last_game(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "default_output_dir", lambda: tmp_path / "recordings")
    monkeypatch.setattr(app_mod.App, "check_environment", lambda self: None)
    monkeypatch.setattr(app_mod.App, "_start_hotkeys", lambda self: None)
    root = tk.Tk()
    a = app_mod.App(root)
    running = {}
    monkeypatch.setattr(app_mod.sys, "platform", "win32")

    def find(name):
        if name not in running:
            raise LookupError(name)
        return running[name]

    monkeypatch.setattr(app_mod, "find_target_by_name", find)
    a._auto_lock()
    assert a.locked is None                                  # nothing remembered yet
    game = TargetInfo(pid=42, name="game.exe", window_title="Game", hwnd=7)
    a._set_locked(game)                                      # user locks once
    saved = json.loads((tmp_path / "last_target.json").read_text(encoding="utf-8"))
    assert saved == {"name": "game.exe"}
    a.locked = None                                          # next start of GameRec
    a._auto_lock()
    assert a.locked is None                                  # game not running yet
    running["game.exe"] = TargetInfo(pid=43, name="game.exe", window_title="Game", hwnd=8)
    a._auto_lock()
    assert a.locked.pid == 43 and "自動鎖定" in a.lock_var.get()
    root.destroy()
