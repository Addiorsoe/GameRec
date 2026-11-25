"""Drive the 找座標精靈 window end to end with fake memory (needs a display: xvfb-run on Linux)."""
import json
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
    pass


def test_wizard_finds_and_adds_candidates(tmp_path):
    root = tk.Tk()
    mem = LiveMem()
    app = SimpleNamespace(locked=TargetInfo(pid=1, name="game.exe"), watch=[],
                          out_var=tk.StringVar(value=str(tmp_path)), _redraw=lambda: None)
    FinderDialog.TIME_SCALE = 0.1
    dlg = FinderDialog(root, app, mem_factory=lambda pid: mem)
    result = {}

    def follow_prompts():                            # the "player": walks when told to
        mem.walking = dlg.expect == "walk"     # follows the worker's current instruction
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
    # Free the dialog's Tk variables here, on the Tk thread. Left to the garbage collector they can be
    # freed later on some other thread (e.g. a recorder thread in the next test), where tkinter blocks
    # waiting for a main loop that no longer runs: that stalled the video test's capture thread.
    import gc

    log_name = dlg.log_path.name
    dlg = app.out_var = None
    gc.collect()

    found = result.get("found", set())
    assert {mem.addr(mem.px), mem.addr(mem.py)} <= found, result
    assert mem.addr(mem.timer) not in found and mem.addr(mem.pz) not in found
    assert result["finished"] == "done" and "候選" in result["prompt"]
    names = [w.name for w in app.watch]
    assert names[:2] == ["cand_001", "cand_002"] and all(w.type == "float" for w in app.watch)
    report = json.loads(next(tmp_path.glob("finder_*_game.exe.json")).read_text(encoding="utf-8"))
    assert report["event"] == "added" and "NOT confirmed" in report["note"]
    assert report["steps"][0]["mode"].startswith("changed while walking")
    assert len(report["added_to_watchlist"]) == len(app.watch)
    assert log_name == "finder.log"
