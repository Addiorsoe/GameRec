"""Drive the 找座標精靈 window end to end with fake memory (needs a display: xvfb-run on Linux)."""
import json
import math
import os
import sys
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("needs a display", allow_module_level=True)

from gamerec.process import TargetInfo  # noqa: E402
from gamerec.ui.finder_dialog import FinderDialog  # noqa: E402
from test_finder import FakeMem  # noqa: E402


class LiveMem(FakeMem):
    """Memory of a 'game': while the player is told to walk, x/y move along a circle."""

    def __init__(self):
        super().__init__()
        self.walking = False
        self.t = 0.0

    def read(self, addr, size):
        f = self.f32(0x20000000)
        f[self.timer] += 0.016                      # animation timer: always changes
        if self.walking:
            self.t += 0.3
            f[self.px] = 120.5 + 10 * math.cos(self.t)
            f[self.py] = -33.25 + 10 * math.sin(self.t)
        return super().read(addr, size)


def test_wizard_finds_and_adds_candidates(tmp_path):
    root = tk.Tk()
    mem = LiveMem()
    app = SimpleNamespace(locked=TargetInfo(pid=1, name="game.exe"), watch=[],
                          out_var=tk.StringVar(value=str(tmp_path)), _redraw=lambda: None)
    FinderDialog.TIME_SCALE = 0.1
    dlg = FinderDialog(root, app, mem_factory=lambda pid: mem)
    result = {}

    def follow_prompts():                            # the "player": walks when told to
        mem.walking = "繞著走" in dlg.prompt.get()
        if dlg.finished and "found" not in result:
            result["found"] = {a for a, _ in dlg.scanner.results()}
            result["prompt"] = dlg.prompt.get()
            result["finished"] = dlg.finished
            dlg.add_all()
            root.after(300, root.destroy)
            return
        root.after(20, follow_prompts)

    dlg.start()
    root.after(20, follow_prompts)
    root.after(30_000, root.destroy)
    root.mainloop()

    found = result.get("found", set())
    assert {mem.addr(mem.px), mem.addr(mem.py)} <= found, result
    assert mem.addr(mem.timer) not in found and mem.addr(mem.pz) not in found
    assert result["finished"] == "done" and "候選" in result["prompt"]
    names = [w.name for w in app.watch]
    assert names[:2] == ["cand_001", "cand_002"] and all(w.type == "float" for w in app.watch)
    report = json.loads(next(tmp_path.glob("finder_*_game.exe.json")).read_text(encoding="utf-8"))
    assert report["event"] == "added" and "NOT confirmed" in report["note"]
    assert report["steps"][0]["mode"] == "first" and len(report["added_to_watchlist"]) == len(app.watch)
