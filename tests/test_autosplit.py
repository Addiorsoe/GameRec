"""Continuous recording is cut into segments of a set length, each exported as its own zip."""
import json
import os
import sys
import time
import zipfile

import psutil
import pytest

tk = pytest.importorskip("tkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("needs a display", allow_module_level=True)

from gamerec.process import TargetInfo  # noqa: E402
from gamerec.recorder import Recorder  # noqa: E402
from gamerec.ui import app as app_mod  # noqa: E402


class Ticker(Recorder):
    kind = "input"

    def setup(self):
        self._fh = open(self.path("input/events.jsonl"), "w", encoding="utf-8")

    def run(self):
        while not self._stop.wait(0.05):
            self._fh.write(json.dumps({"t_ns": self.session.clock.now_ns(), "kind": "mouse_move"}) + "\n")
            self.count += 1

    def teardown(self):
        self._fh.close()


def test_segments_split_and_exported(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "default_output_dir", lambda: tmp_path / "rec")
    monkeypatch.setattr(app_mod.App, "check_environment", lambda self: None)
    monkeypatch.setattr(app_mod.App, "_start_hotkeys", lambda self: None)
    monkeypatch.setattr(app_mod.App, "_auto_lock", lambda self: None)
    monkeypatch.setattr(app_mod, "make_recorders", lambda *a, **k: [Ticker()])
    root = tk.Tk()
    a = app_mod.App(root)
    a._empty_watch_ok = True
    a.v_net.set(False)
    me = psutil.Process()
    a.locked = TargetInfo(pid=me.pid, name=me.name())
    a.v_split.set(0.01)                       # 0.6 s per segment
    a.group_var.set("B")
    a.notes_var.set("連續測試")
    a.start()

    def poll():                               # stand-in for the hotkey poll loop
        while not a._hotkeys.empty():
            a.handle_hotkey(a._hotkeys.get_nowait())
        root.after(20, poll)

    poll()
    root.after(2200, a.stop)                  # user presses F9 after ~3.5 segments
    root.after(4000, root.destroy)
    root.mainloop()
    deadline = time.time() + 5
    while time.time() < deadline and a._exports:
        time.sleep(0.05)
    zips = sorted((tmp_path / "rec").glob("*.zip"))
    assert len(zips) >= 3, zips
    notes = [json.loads(zipfile.ZipFile(z).read("manifest.json"))["notes"] for z in zips]
    takes = [json.loads(zipfile.ZipFile(z).read("manifest.json"))["take"] for z in zips]
    assert takes == list(range(1, len(zips) + 1))
    assert notes[0] == "連續測試 （連續錄製第 1 段，時間到自動切檔）"
    assert notes[-1].endswith(f"（連續錄製第 {len(zips)} 段，最後一段）")
    durs = [json.loads(zipfile.ZipFile(z).read("manifest.json"))["duration_s"] for z in zips]
    assert all(0.5 <= d <= 1.3 for d in durs[:-1]), durs
