"""Small JPEG stills taken from the finished recording, for reading the session without the video.

Taken purely by time: one per STEP seconds, plus one at every key press, mouse press and marker.
Nothing is detected or interpreted here; stills/index.csv says which time and which raw event
each image belongs to. The full video stays next to them for closer looks.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

log = logging.getLogger("gamerec")

STEP = 1.0        # seconds between periodic stills
WIDTH = 480       # px
QUALITY = 60      # JPEG
MIN_GAP = 0.15    # event stills closer than this to the previous event still are skipped


def _wanted(session_dir: Path, duration_s: float) -> list[tuple[int, str, str]]:
    """[(t_ns, reason, detail)] sorted by time."""
    out = [(int(i * STEP * 1e9), "every_1s", "") for i in range(int(duration_s / STEP) + 1)]
    ev = session_dir / "input" / "events.jsonl"
    last = -1e18
    if ev.exists():
        for line in ev.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") not in ("key_down", "mouse_down") or e.get("repeat"):
                continue
            t = int(e["t_ns"])
            if t - last < MIN_GAP * 1e9:
                continue
            last = t
            detail = e.get("key") or f"{e.get('button', '')}@{e.get('x')},{e.get('y')}"
            out.append((t, e["kind"], str(detail)))
    mk = session_dir / "markers.jsonl"
    if mk.exists():
        for line in mk.read_text(encoding="utf-8").splitlines():
            try:
                m = json.loads(line)
                out.append((int(m["t_ns"]), "marker", str(m.get("label", ""))))
            except (ValueError, KeyError):
                continue
    return sorted(out)


def extract_stills(session_dir: str | Path, duration_s: float) -> int:
    """Write stills/*.jpg and stills/index.csv; returns how many were written."""
    import cv2
    import numpy as np

    session_dir = Path(session_dir)
    video, frames = session_dir / "video" / "screen.mp4", session_dir / "video" / "frames.csv"
    if not video.exists() or not frames.exists():
        return 0
    with frames.open(encoding="utf-8") as f:
        times = np.array([int(r["t_ns"]) for r in csv.DictReader(f)], dtype=np.int64)
    if not len(times):
        return 0
    wanted = _wanted(session_dir, duration_s)
    # frame shown at time t = last frame whose timestamp is <= t
    idx = np.clip(np.searchsorted(times, [t for t, _, _ in wanted], side="right") - 1, 0, len(times) - 1)
    by_frame: dict[int, list[int]] = {}
    for k, fi in enumerate(idx):
        by_frame.setdefault(int(fi), []).append(k)
    out_dir = session_dir / "stills"
    out_dir.mkdir(exist_ok=True)
    rows, cap, fi = [], cv2.VideoCapture(str(video)), -1
    try:
        last_needed = max(by_frame)
        while fi < last_needed:
            ok, img = cap.read()
            if not ok:
                break
            fi += 1
            if fi not in by_frame:
                continue
            h, w = img.shape[:2]
            small = cv2.resize(img, (WIDTH, max(2, round(h * WIDTH / w))), interpolation=cv2.INTER_AREA)
            for k in by_frame[fi]:
                t, reason, detail = wanted[k]
                name = f"t{t / 1e9:08.2f}_{reason}.jpg"
                if not (out_dir / name).exists():
                    cv2.imwrite(str(out_dir / name), small, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
                rows.append((name, t, reason, detail, fi))
    finally:
        cap.release()
    with (out_dir / "index.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["file", "t_ns", "reason", "detail", "video_frame"])
        wr.writerows(rows)
    log.info("stills: %d images", len(rows))
    return len(rows)
