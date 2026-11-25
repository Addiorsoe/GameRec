"""Motion detector: panning view = walk, static view with animated effects = still."""
import numpy as np

from gamerec.video.motion import MotionDetector


def world(seed=0):
    rng = np.random.default_rng(seed)
    import cv2

    base = rng.integers(0, 255, (900, 1600), dtype=np.uint8)
    return cv2.GaussianBlur(base, (0, 0), 3)   # ground-like texture


def frame(w, x, y, rng=None, hud=True):
    img = w[y: y + 360, x: x + 640].copy()
    if hud:
        img[300:, :] = 40                       # static HUD bar (doesn't move with the camera)
    if rng is not None:                         # effects: random flicker in a few spots
        for _ in range(6):
            cx, cy = rng.integers(100, 540), rng.integers(60, 280)
            img[cy: cy + 20, cx: cx + 20] = rng.integers(0, 255, (20, 20))
    return img


def test_walk_still_and_effects():
    w, rng = world(), np.random.default_rng(1)
    d = MotionDetector(grab=lambda: None)
    t, x, y = 0.0, 400, 200
    states = []
    for i in range(40):                          # standing, effects playing
        states.append(d.feed(frame(w, x, y, rng), now=t))
        t += 0.125
    assert states[-1] == "still"
    still_since = d.since
    for i in range(16):                          # walking: camera pans ~6 px/frame diagonally
        x, y = x + 6, y + 3
        states.append(d.feed(frame(w, x, y, rng), now=t))
        t += 0.125
    assert states[-1] == "walk" and d.since > still_since
    assert d.walking_since(t) and not d.walking_since(d.since - 1)
    walk_start = d.since
    for i in range(10):
        states.append(d.feed(frame(w, x, y, rng), now=t))
        t += 0.125
    assert states[-1] == "still" and d.since > walk_start
    # quick: walk detected within 3 frames of starting, still within ~6 frames of stopping
    assert walk_start - still_since < 40 * 0.125


def test_scene_change_is_unknown():
    d = MotionDetector(grab=lambda: None)
    for i in range(6):
        d.feed(frame(world(0), 0, 0), now=-1 + 0.1 * i)
    assert d.state == "still"
    d.feed(frame(world(0), 0, 0), now=0)
    d.feed(np.zeros((360, 640), np.uint8) + 128, now=0.1)   # loading screen
    for i in range(2, 5):
        d.feed(np.zeros((360, 640), np.uint8) + 128, now=0.1 * i)
    assert d.state == "unknown" and d.since == 0.1   # dated from the first bad frame
