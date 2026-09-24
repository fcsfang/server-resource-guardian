#!/usr/bin/env python3
"""Deep-storm hog: 17GB filled AND continuously re-paged to hold the system
in the earlyoom kill zone (below 10% available), forcing repeated kills -
the T1 first-round condition. The marker file is written BEFORE the fill
loop so liveness probes work from second zero.

Usage: python3 deep_storm_hog.py [seconds=300] [gib=17]
"""
import sys
import time

GIB = int(sys.argv[2]) * 1024**3 if len(sys.argv) > 2 else 17 * 1024**3
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 300

with open("/tmp/deep_storm_alive", "w") as f:
    f.write("1")

# Phase 1: fill and touch every page (pushes available below 10%)
buf = bytearray(GIB)
for i in range(0, len(buf), 4096):
    buf[i] = 1

# Phase 2: hold + churn - re-touch pages in a rotating window so the kernel
# keeps seeing working-set pressure and earlyoom keeps finding kill victims
# (spawn short-lived child eaters: each allocates 600MB, dies to earlyoom)
import subprocess

end = time.time() + DURATION
i = 0
while time.time() < end:
    # re-touch a rolling 2GB window to sustain reclaim pressure
    start_page = (i * 262144) % (len(buf) - 2 * 1024**3)
    for j in range(start_page, start_page + 2 * 1024**3, 4096):
        buf[j] = (buf[j] + 1) % 128
    i += 1
    # child eater: gives earlyoom a fresh victim (avoids killing our parent)
    child = subprocess.Popen(
        ["python3", "-c", "b = bytearray(600 * 1024**2)\nfor k in range(0, len(b), 4096): b[k] = 1\nimport time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(8)

with open("/tmp/deep_storm_alive", "w") as f:
    f.write("0")
