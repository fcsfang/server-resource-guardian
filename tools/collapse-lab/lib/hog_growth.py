import sys, time
"""Grow memory in steps until the kernel/cgroup refuses; then idle.

argv: step_mib [sleep_s]   (default 256 MiB every 3 s - slow enough to
exhaust swap before global OOM; 512+ MiB at 0 s goes straight to OOM)
"""
step = int(sys.argv[1]) if len(sys.argv) > 1 else 256
gap = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
blocks = []
try:
    while True:
        b = bytearray(step * 1024 * 1024)
        for i in range(0, len(b), 4096):
            b[i] = 1
        blocks.append(b)
        print(f"+{step}MiB total={len(blocks)*step}MiB", flush=True)
        time.sleep(gap)
except MemoryError:
    print("allocation refused", flush=True)
    time.sleep(600)
