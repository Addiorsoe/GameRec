"""Print e2e screenshots into the CI log as chunked base64 JPEG (for viewers without artifact access)."""
import base64
import glob
import os

import cv2

files = sorted(glob.glob("e2e-artifacts/screens/*.png"))
print(f"{len(files)} screenshots")
for f in files:
    img = cv2.imread(f)
    ok, buf = cv2.imencode(".jpg", cv2.resize(img, None, fx=0.75, fy=0.75), [cv2.IMWRITE_JPEG_QUALITY, 80])
    b64 = base64.b64encode(buf.tobytes()).decode()
    name = os.path.basename(f)
    for i in range(0, len(b64), 2000):
        print(f"SHOT {name} {i // 2000:04d} {b64[i:i + 2000]}")
