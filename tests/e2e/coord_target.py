"""Fake 'game' for the coordinate finder e2e: ~200 MB of plausible float noise, a player whose
x/y walk a circle while the control file exists, an animation timer that always changes.

argv[1]: JSON info file to write; argv[2]: control file path ("walk" while it exists).
"""
import ctypes
import json
import math
import os
import sys
import time

import numpy as np

noise = np.random.default_rng(0).uniform(-5000, 5000, 50_000_000).astype(np.float32)  # 200 MB


class Player(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("z", ctypes.c_float), ("timer", ctypes.c_float)]


p = Player(812.5, -1290.25, 37.0, 0.0)
info = {"pid": os.getpid(), "x": ctypes.addressof(p), "y": ctypes.addressof(p) + 4,
        "z": ctypes.addressof(p) + 8, "timer": ctypes.addressof(p) + 12, "noise_mb": noise.nbytes // 2**20}
with open(sys.argv[1], "w") as f:
    json.dump(info, f)

t = 0.0
while True:
    p.timer += 0.016
    if os.path.exists(sys.argv[2]):
        t += 0.05
        p.x = 812.5 + 40 * math.cos(t)
        p.y = -1290.25 + 40 * math.sin(t)
    time.sleep(0.016)
