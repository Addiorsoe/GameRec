"""End-to-end trial run on Windows (used by CI, also runnable by hand as admin).

Usage:
  python scripts/e2e_windows.py cli            # run recorder via `python -m gamerec record`
  python scripts/e2e_windows.py exe PATH.exe   # same, through the packaged exe
  python scripts/e2e_windows.py gui            # drive the tkinter GUI programmatically
  python scripts/e2e_windows.py smoke PATH.exe # launch the packaged GUI and check it starts
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "e2e" / "target_app.py"
SECONDS = 8


def start_target(tmp: Path) -> tuple[subprocess.Popen, dict]:
    info_file = tmp / "target.json"
    proc = subprocess.Popen([sys.executable, str(TARGET), str(info_file)])
    for _ in range(100):
        if info_file.exists() and info_file.stat().st_size:
            time.sleep(1.0)  # let the window appear
            return proc, json.loads(info_file.read_text())
        time.sleep(0.1)
    proc.kill()
    raise SystemExit("target did not start")


def watchlist(info: dict, tmp: Path) -> Path:
    wl = [
        {"name": "counter", "type": "int32", "address": hex(info["counter"])},
        {"name": "hp", "type": "int32", "address": hex(info["hp"])},
        # [ptr_holder] + x_offset  -> pointer chain from an absolute base
        {"name": "x_chain", "type": "float", "address": hex(info["ptr_holder"]), "offsets": ["0x0", hex(info["x_offset"])]},
    ]
    p = tmp / "watch.json"
    p.write_text(json.dumps(wl))
    return p


def press_keys(stop: threading.Event) -> None:
    from pynput.keyboard import Controller

    kb = Controller()
    time.sleep(2)
    while not stop.is_set():
        kb.press("w")
        time.sleep(0.1)
        kb.release("w")
        time.sleep(0.4)


def verify(zpath: Path, expect_markers: int = 0) -> None:
    print(f"verifying {zpath}")
    z = zipfile.ZipFile(zpath)
    names = set(z.namelist())
    man = json.loads(z.read("manifest.json"))
    print(json.dumps(man["streams"], indent=1, ensure_ascii=False))
    st = {s["kind"]: s for s in man["streams"]}
    problems = []

    # memory
    if st.get("memory", {}).get("status") != "ok":
        problems.append(f"memory stream: {st.get('memory')}")
    else:
        rows = list(csv.DictReader(io.StringIO(z.read("memory/samples.csv").decode())))
        by = {}
        for r in rows:
            by.setdefault(r["name"], []).append(r["value"])
        c = [int(v) for v in by.get("counter", []) if v != ""]
        x = [float(v) for v in by.get("x_chain", []) if v != ""]
        print(f"memory: counter {len(c)} samples {c[:3]}..{c[-3:]}, x_chain {len(x)} samples, hp {by.get('hp', [])[:5]}")
        if len(c) < 50 or c != sorted(c):
            problems.append("counter samples missing or not increasing")
        if len(x) < 50 or x != sorted(x):
            problems.append("pointer-chain x samples missing or not increasing")
        if not by.get("hp"):
            problems.append("hp not sampled")

    # video
    if st.get("video", {}).get("status") != "ok":
        problems.append(f"video stream: {st.get('video')}")
    else:
        import cv2

        frames = list(csv.DictReader(io.StringIO(z.read("video/frames.csv").decode())))
        tmpv = Path(tempfile.mkdtemp()) / "v.mp4"
        tmpv.write_bytes(z.read("video/screen.mp4"))
        cap = cv2.VideoCapture(str(tmpv))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, frame = cap.read()
        cap.release()
        print(f"video: {len(frames)} frame rows, mp4 frames {n}, first frame {None if not ok else frame.shape}")
        if len(frames) < SECONDS * 5 or not ok or n < len(frames) * 0.9:
            problems.append("video too few frames or unreadable mp4")
        if ok and frame.std() < 1:
            problems.append("video frames are blank")

    # input
    if st.get("input", {}).get("status") != "ok":
        problems.append(f"input stream: {st.get('input')}")
    else:
        ev = [json.loads(line) for line in z.read("input/events.jsonl").decode().splitlines()]
        keys = [e for e in ev if e["kind"] == "key_down" and e.get("key") == "w"]
        print(f"input: {len(ev)} events, {len(keys)} 'w' presses")
        if len(keys) < 3:
            problems.append("key presses not logged")

    # network: Npcap is not on CI runners -> must degrade gracefully, not crash
    # (the GUI unticks "packets" itself when Npcap is missing, so the stream may be absent)
    net = st.get("network")
    print(f"network: {net or 'disabled (no Npcap)'}")
    if net and net.get("status") not in ("ok", "unavailable"):
        problems.append(f"network stream: {net}")

    markers = z.read("markers.jsonl").decode().splitlines() if "markers.jsonl" in names else []
    if len(markers) < expect_markers:
        problems.append(f"expected {expect_markers} markers, got {len(markers)}")
    if "logs/recorder.log" not in names:
        problems.append("log missing")

    # readability: every text file must parse and show sample rows a human can read
    print("\n--- readability samples ---")
    if "README.txt" not in names:
        problems.append("README.txt missing from export")
    for name in ("memory/samples.csv", "input/events.jsonl", "network/packets.jsonl", "markers.jsonl", "video/frames.csv"):
        if name in names:
            lines = z.read(name).decode("utf-8").splitlines()
            print(f"[{name}] {len(lines)} lines")
            for line in lines[:3]:
                print("   ", line[:200])
            if name.endswith(".jsonl"):
                for line in lines:
                    rec = json.loads(line)
                    if "t_ns" not in rec:
                        problems.append(f"{name}: row without t_ns")
                        break

    if problems:
        print("\nFAILED:\n - " + "\n - ".join(problems))
        print(z.read("logs/recorder.log").decode(errors="replace")[-3000:])
        raise SystemExit(1)
    print("E2E OK")


def run_record(cmd_prefix: list[str]) -> None:
    tmp = Path(tempfile.mkdtemp())
    proc, info = start_target(tmp)
    stop = threading.Event()
    threading.Thread(target=press_keys, args=(stop,), daemon=True).start()
    result = tmp / "result.txt"
    try:
        cmd = cmd_prefix + ["record", "--pid", str(info["pid"]), "--watch", str(watchlist(info, tmp)),
                            "--seconds", str(SECONDS), "--out", str(tmp / "sessions"), "--result", str(result),
                            "--notes", "e2e"]
        print(" ".join(cmd))
        r = subprocess.run(cmd, timeout=120)
        print("exit code", r.returncode)
    finally:
        stop.set()
        proc.kill()
    if not result.exists():
        raise SystemExit("recorder produced no result file (crashed?)")
    verify(Path(result.read_text().strip()))


def run_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox

    sys.path.insert(0, str(ROOT))
    from gamerec.ui import app as appmod

    tmp = Path(tempfile.mkdtemp())
    proc, info = start_target(tmp)
    stop = threading.Event()
    threading.Thread(target=press_keys, args=(stop,), daemon=True).start()
    # auto-answer dialogs
    appmod._ask_text = lambda *a, **k: "gui e2e"
    messagebox.askyesno = lambda *a, **k: False
    messagebox.showinfo = lambda *a, **k: None
    errors: list[str] = []
    messagebox.showerror = lambda title, msg, **k: errors.append(f"{title}: {msg}")

    root = tk.Tk()
    root.geometry("860x640+700+0")
    a = appmod.App(root)
    a.out_var.set(str(tmp / "sessions"))
    a.refresh_processes()
    for name, vtype, where in (
        ("counter", "int32", hex(info["counter"])),
        ("hp", "int32", hex(info["hp"])),
        ("x_chain", "float", f"{hex(info['ptr_holder'])},{hex(info['x_offset'])}"),
    ):
        a.e_name.set(name), a.e_type.set(vtype), a.e_addr.set(where)
        a.add_watch()
    result = {}

    def step_start():
        a.start()

    def step_mark():
        a.mark_var.set("walk north")
        a.mark()

    def press_combo(*keys):
        def go():
            from pynput.keyboard import Controller, Key

            kb = Controller()
            ks = [getattr(Key, k) if len(k) > 1 else k for k in keys]
            for k in ks:
                kb.press(k)
            time.sleep(0.05)
            for k in reversed(ks):
                kb.release(k)
        threading.Thread(target=go, daemon=True).start()

    def step_hotkey_mark():  # default mark hotkey F9 via the global hook
        press_combo("f9")

    def step_force_stop():  # fixed force-stop combo must stop + export without dialogs
        press_combo("ctrl", "alt", "end")

    def step_focus_target():  # bring the fake game to the front, then press the lock hotkey (F8)
        import win32con
        import win32gui

        from gamerec.process import find_main_window

        hwnd, _ = find_main_window(info["pid"])
        press_combo("alt")  # Windows only lets a process steal focus right after a key event
        time.sleep(0.2)
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:  # noqa: BLE001
            errors.append(f"could not focus target: {e}")
        root.after(500, lambda: press_combo("f8"))

    def step_check_lock():
        if not a.locked or a.locked.pid != info["pid"]:
            errors.append(f"lock hotkey did not lock the target: {a.locked}")
        else:
            print(f"lock OK: {a.locked.name} [{a.locked.pid}] window={a.locked.window_title!r}")

    def screenshot(name):
        import mss
        import mss.tools

        out = ROOT / "e2e-artifacts" / "screens"
        out.mkdir(parents=True, exist_ok=True)
        with mss.mss() as sct:
            img = sct.grab(sct.monitors[1])
            mss.tools.to_png(img.rgb, img.size, output=str(out / name))

    def step_check():
        result["zip"] = a.last_zip
        result["stopped"] = bool(a.session and a.session.stopped)
        root.destroy()

    # hotkey validation from the GUI side
    try:
        a.hk_config.set("mark", a.hk_config.bindings["start_stop"])
        errors.append("duplicate hotkey was accepted")
    except ValueError:
        pass

    # chain the steps so each waits for the previous one (the GUI can be slow to start)
    def chain(steps):
        if steps:
            delay, fn = steps[0]
            root.after(delay, lambda: (fn(), chain(steps[1:])))

    chain([(1000, step_focus_target), (1500, step_check_lock), (300, lambda: screenshot("1_locked.png")),
           (500, step_start), (1500, step_mark), (1500, step_hotkey_mark),
           (1500, lambda: screenshot("2_recording.png")),
           (SECONDS * 1000, step_force_stop), (3000, lambda: screenshot("3_exported.png")), (200, step_check)])
    root.after(60_000, root.destroy)
    root.mainloop()
    stop.set()
    proc.kill()
    if errors:
        raise SystemExit(f"GUI showed errors: {errors}")
    if not result.get("zip"):
        raise SystemExit("GUI did not export a zip (force-stop hotkey not handled?)")
    verify(Path(result["zip"]), expect_markers=2)
    man = json.loads(zipfile.ZipFile(result["zip"]).read("manifest.json"))
    if "強制停止" not in man.get("notes", ""):
        raise SystemExit(f"export was not made by the force-stop key: notes={man.get('notes')!r}")
    if man["target"].get("window_title") != "E2E Target Game":
        raise SystemExit(f"locked target window not used: {man['target']}")
    print("hotkeys OK (F8 lock target + F9 marker + Ctrl+Alt+End force stop)")


def _diagnose() -> None:
    """On a hang: screenshot the desktop, list top-level windows, dump the crash log."""
    import mss
    import mss.tools

    out = ROOT / "e2e-artifacts"
    out.mkdir(exist_ok=True)
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        mss.tools.to_png(shot.rgb, shot.size, output=str(out / "hang.png"))
    try:
        import win32gui

        titles = []
        win32gui.EnumWindows(lambda h, _: titles.append(win32gui.GetWindowText(h)) if win32gui.IsWindowVisible(h) else None, None)
        print("visible windows:", [t for t in titles if t])
        # PyInstaller's "Unhandled exception in script" dialog: print its text (the traceback)
        def dump(h, _):
            if "exception" in win32gui.GetWindowText(h).lower():
                kids = []
                win32gui.EnumChildWindows(h, lambda c, _: kids.append(win32gui.GetWindowText(c)), None)
                print("EXCEPTION DIALOG:\n" + "\n".join(k for k in kids if k))
        win32gui.EnumWindows(dump, None)
    except Exception as e:  # noqa: BLE001
        print("window list failed:", e)
    sys.path.insert(0, str(ROOT))
    from gamerec.env_check import default_output_dir

    log = default_output_dir().parent / "gamerec_error.log"
    print("crash log:", log.read_text(encoding="utf-8")[-4000:] if log.exists() else "(none)")


def run_smoke(exe: str) -> None:
    out = Path(tempfile.mkdtemp()) / "smoke.json"
    t0 = time.time()
    p = subprocess.Popen([exe, "gui", "--smoke-test", str(out)])
    try:
        p.wait(timeout=90)
    except subprocess.TimeoutExpired:
        _diagnose()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)])
        raise SystemExit("GUI exe did not exit (hung?)")
    print(f"exe exited {p.returncode} after {time.time() - t0:.1f}s")
    if not out.exists():
        raise SystemExit("GUI exe crashed before writing smoke result")
    res = json.loads(out.read_text(encoding="utf-8"))
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if not res.get("ok"):
        raise SystemExit(1)
    hw = res.get("sysinfo") or {}
    if not ({"cpu", "ram", "gpus", "disks", "virtualization"} <= set(hw) and hw["ram"]["total_gb"] > 0):
        raise SystemExit("hardware scan missing from the packaged exe")
    print("SMOKE OK")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "cli":
        run_record([sys.executable, "-m", "gamerec"])
    elif mode == "exe":
        run_record([sys.argv[2]])
    elif mode == "gui":
        run_gui()
    elif mode == "smoke":
        run_smoke(sys.argv[2])
