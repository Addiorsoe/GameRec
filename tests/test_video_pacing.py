"""Video playback time must match real time even when screen capture is slower than the FPS.

Needs a display (run under xvfb-run on Linux; Windows CI has a desktop)."""
import os
import sys
import time

import pytest

from gamerec.process import TargetInfo
from gamerec.session import Session

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="needs a display")


def test_slow_capture_keeps_real_time(tmp_path, monkeypatch):
    import cv2
    import mss

    from gamerec.video.recorder import ScreenRecorder

    real_mss = mss.mss

    class SlowMSS:
        def __init__(self):
            self._s = real_mss()
            self.monitors = self._s.monitors

        def grab(self, region):
            time.sleep(0.12)  # capture can only do ~8 fps
            return self._s.grab(region)

        def close(self):
            self._s.close()

    monkeypatch.setattr(mss, "mss", SlowMSS)
    rec = ScreenRecorder(fps=15, region=(0, 0, 320, 240))
    s = Session(TargetInfo(pid=os.getpid(), name="t"), [rec], root=tmp_path)
    s.start()
    time.sleep(3.0)
    s.stop()
    cap = cv2.VideoCapture(str(s.dir / "video/screen.mp4"))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    playback = frames / 15
    real = s.duration_ns / 1e9
    assert abs(playback - real) < 0.5, f"video plays {playback:.2f}s for {real:.2f}s of real time"
