"""Customisable global hotkeys with combo support.

Rules (shared by GameRec and the future AutoPath bot):
- A binding is a combo like "ctrl+shift+f9": zero or more modifiers + exactly one main key.
- Two actions may never share a binding, and nothing may reuse the force-stop combo.
- The force-stop combo is fixed and cannot be changed or removed (safety net against a
  bad binding that makes the app uncontrollable).
- "Restore defaults" is always reachable with the mouse from the settings window.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MODIFIERS = ("ctrl", "alt", "shift", "win")
FORCE_STOP = "ctrl+alt+end"          # fixed, never rebindable


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    default: str


GAMEREC_ACTIONS = (
    Action("lock_target", "鎖定目前視窗為目標", "f8"),
    Action("start_stop", "開始 / 停止錄製", "f10"),
    Action("mark", "加標記", "f9"),
)

# pynput / tk names -> canonical names
_ALIASES = {
    "control": "ctrl", "control_l": "ctrl", "control_r": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt", "option": "alt", "meta_l": "alt", "meta_r": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win", "super_l": "win", "super_r": "win", "win_l": "win", "win_r": "win",
    "return": "enter", "escape": "esc", "prior": "page_up", "next": "page_down",
    "kp_enter": "enter", "back_space": "backspace", "caps_lock": "caps_lock",
}


def canon_key(name: str) -> str:
    n = name.strip().lower().replace(" ", "_")
    return _ALIASES.get(n, n)


def normalize(combo: str) -> str:
    """'Shift+Ctrl+F9' -> 'ctrl+shift+f9'. Raises ValueError if not exactly one main key."""
    parts = [canon_key(p) for p in combo.split("+") if p.strip()]
    mods = sorted({p for p in parts if p in MODIFIERS}, key=MODIFIERS.index)
    keys = [p for p in parts if p not in MODIFIERS]
    if len(keys) != 1:
        raise ValueError(f"熱鍵必須是「修飾鍵(可無) + 一個主鍵」：{combo!r}")
    return "+".join([*mods, keys[0]])


def pretty(combo: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in combo.split("+"))


class HotkeyConfig:
    def __init__(self, actions=GAMEREC_ACTIONS, path: str | Path | None = None) -> None:
        self.actions = {a.key: a for a in actions}
        self.path = Path(path) if path else None
        self.bindings: dict[str, str] = {a.key: normalize(a.default) for a in actions}
        self.load()

    def validate(self, action: str, combo: str) -> str:
        """Return the normalized combo or raise ValueError explaining why it's not allowed."""
        c = normalize(combo)
        if c == FORCE_STOP:
            raise ValueError(f"{pretty(FORCE_STOP)} 是固定的強制停止鍵，不能綁定給其他功能")
        for other, oc in self.bindings.items():
            if other != action and oc == c:
                raise ValueError(f"{pretty(c)} 已經用於「{self.actions[other].label}」，不能重複綁定")
        return c

    def set(self, action: str, combo: str) -> str:
        c = self.validate(action, combo)
        self.bindings[action] = c
        self.save()
        return c

    def restore_defaults(self) -> None:
        self.bindings = {k: normalize(a.default) for k, a in self.actions.items()}
        self.save()

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            trial = dict(self.bindings)
            for k, v in data.items():
                if k in self.actions:
                    trial[k] = normalize(v)
            vals = list(trial.values())
            if len(set(vals)) != len(vals) or FORCE_STOP in vals:
                raise ValueError("duplicate or reserved binding in file")
            self.bindings = trial
        except (ValueError, OSError, json.JSONDecodeError):
            pass  # broken file -> keep defaults (never lock the user out)

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.bindings, indent=2), encoding="utf-8")


def pynput_key_name(key) -> str:
    """Canonical name for a pynput key, robust to Ctrl turning letters into control chars."""
    vk = getattr(key, "vk", None)
    if vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        return chr(vk).lower()
    char = getattr(key, "char", None)
    if char and char.isprintable():
        return char.lower()
    name = getattr(key, "name", None) or str(key).replace("Key.", "")
    return canon_key(name)


class ComboTracker:
    """Feed key down/up names; returns the completed combo string on a main-key press.

    `modifier_down(mod)` asks the OS whether a modifier is really held, so a missed key-up
    (e.g. a UAC prompt swallowed it) can't leave Ctrl "stuck" and turn Alt+End into the
    force-stop combo.
    """

    def __init__(self, modifier_down: Callable[[str], bool] | None = None) -> None:
        self.held: set[str] = set()
        self.modifier_down = modifier_down

    def down(self, name: str) -> str | None:
        name = canon_key(name)
        if name in MODIFIERS:
            self.held.add(name)
            return None
        if self.modifier_down:
            self.held = {m for m in self.held if self.modifier_down(m)}
        mods = sorted(self.held, key=MODIFIERS.index)
        return "+".join([*mods, name])

    def up(self, name: str) -> None:
        self.held.discard(canon_key(name))


def _os_modifier_down():
    """Windows: GetAsyncKeyState-based check of the real modifier state; None elsewhere."""
    import sys

    if sys.platform != "win32":
        return None
    import ctypes

    vks = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}
    gaks = ctypes.windll.user32.GetAsyncKeyState
    return lambda m: any(gaks(vk) & 0x8000 for vk in vks[m])


class GlobalHotkeys:
    """System-wide hotkey listener (works while the game has focus).

    `on_fire(action)` is called on the listener thread: hand off to the UI via a queue.
    """

    def __init__(self, config: HotkeyConfig, on_fire: Callable[[str], None]) -> None:
        self.config = config
        self.on_fire = on_fire
        self.tracker = ComboTracker(_os_modifier_down())
        self._listener = None
        self._lock = threading.Lock()

    def start(self) -> None:
        from pynput import keyboard  # noqa: PLC0415

        self._listener = keyboard.Listener(on_press=self._down, on_release=self._up)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    def _down(self, key) -> None:
        combo = self.tracker.down(pynput_key_name(key))
        if combo is None:
            return
        if combo == FORCE_STOP:
            self.on_fire("force_stop")
            return
        for action, c in self.config.bindings.items():
            if c == combo:
                self.on_fire(action)

    def _up(self, key) -> None:
        self.tracker.up(pynput_key_name(key))
