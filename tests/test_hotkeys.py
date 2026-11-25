import json

import pytest

from gamerec.hotkeys import FORCE_STOP, ComboTracker, HotkeyConfig, normalize


def test_normalize_orders_modifiers():
    assert normalize("Shift+Ctrl+F9") == "ctrl+shift+f9"
    assert normalize("control_l+a") == "ctrl+a"
    assert normalize("F10") == "f10"
    with pytest.raises(ValueError):
        normalize("ctrl+shift")          # modifiers only
    with pytest.raises(ValueError):
        normalize("a+b")                 # two main keys


def test_reject_duplicates_and_force_stop(tmp_path):
    cfg = HotkeyConfig(path=tmp_path / "hk.json")
    with pytest.raises(ValueError, match="重複"):
        cfg.set("mark", cfg.bindings["start_stop"])
    with pytest.raises(ValueError, match="強制停止"):
        cfg.set("mark", "Ctrl+Alt+End")
    assert cfg.set("mark", "ctrl+m") == "ctrl+m"
    # same action re-binding to its own combo is fine
    assert cfg.set("mark", "ctrl+m") == "ctrl+m"


def test_persist_and_restore(tmp_path):
    p = tmp_path / "hk.json"
    cfg = HotkeyConfig(path=p)
    cfg.set("mark", "shift+f2")
    assert HotkeyConfig(path=p).bindings["mark"] == "shift+f2"
    cfg.restore_defaults()
    assert HotkeyConfig(path=p).bindings == {"lock_target": "f8", "start_stop": "f9", "mark": "f10"}


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "hk.json"
    p.write_text(json.dumps({"mark": "f10", "start_stop": "f10"}))   # duplicate
    assert HotkeyConfig(path=p).bindings == {"lock_target": "f8", "start_stop": "f9", "mark": "f10"}
    p.write_text(json.dumps({"mark": FORCE_STOP}))
    assert HotkeyConfig(path=p).bindings["mark"] == "f10"
    p.write_text("not json")
    assert HotkeyConfig(path=p).bindings["mark"] == "f10"


def test_combo_tracker():
    t = ComboTracker()
    assert t.down("ctrl_l") is None
    assert t.down("alt_l") is None
    assert t.down("end") == FORCE_STOP
    t.up("alt_l")
    assert t.down("f9") == "ctrl+f9"
    t.up("f9")
    t.up("ctrl_l")
    assert t.down("f9") == "f9"
