import sys, time
"""Hold N MiB of touched memory for H seconds (holder pressure).

Verified form: 24 containers x 280MiB + restart=on-failure pins a
3.8GiB+2GiB-swap host into continuous global-OOM churn (dead hogs are
replaced by docker until the demand is met again).
"""
mib = int(sys.argv[1]); hold = int(sys.argv[2])
buf = bytearray(mib * 1024 * 1024)
for i in range(0, len(buf), 4096):
    buf[i] = 1
print(f"holding {mib}MiB for {hold}s", flush=True)
time.sleep(hold)
