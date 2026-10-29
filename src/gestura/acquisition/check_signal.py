"""Quick signal check before a recording: does a hand pass through the line leave a trace?

5 s of quiet, then 5 beeps 4 s apart; pass the hand once through the middle of the line on
each beep. Prints, per beep, the peak of the motion envelope relative to the quiet part. The
17 Oct test showed ~3x or more when the hand crossed the line and ~1x when it did not.

Usage: python check_signal.py <port>
"""
import csv
import json
import sys
import threading
import time
from io import StringIO

import numpy as np
import serial

from bip import beep

QUIET_SEC, BEEPS, GAP_SEC = 5, 5, 4.0

if len(sys.argv) != 2:
    sys.exit("Usage: python check_signal.py <port>")
ser = serial.Serial(sys.argv[1], 921600, timeout=1)
rows, stop = [], threading.Event()


def reader():
    while not stop.is_set():
        line = ser.readline().decode(errors="ignore").strip()
        if line.startswith("CSI_DATA"):
            rows.append((time.time(), line))


th = threading.Thread(target=reader, daemon=True)
th.start()
t_start = time.time()
time.sleep(QUIET_SEC)
cues = []
for _ in range(BEEPS):
    cues.append(time.time())
    beep(1000, 150)
    time.sleep(GAP_SEC - 0.15)
stop.set()
th.join(timeout=2)

T, A = [], []
for ts, line in rows:
    try:
        r = next(csv.reader(StringIO(line)))
        data = json.loads(r[-1])
    except (ValueError, StopIteration, json.JSONDecodeError):
        continue
    if len(data) != 384:
        continue
    iq = np.array(data, dtype=float).reshape(-1, 2)
    T.append(ts)
    A.append(np.hypot(iq[:, 0], iq[:, 1]))
if len(T) < 100:
    sys.exit(f"Only {len(T)} usable packets. Check that both boards are powered and the receiver port is right.")
T, A = np.array(T), np.array(A)
A = A[:, A.mean(axis=0) > 1.0]
A = A / A.mean(axis=0)

FS, n = 50.0, 10  # 0.2 s rolling window
tu = np.arange(T[0], T[-1], 1 / FS)
U = np.stack([np.interp(tu, T, A[:, k]) for k in range(A.shape[1])], axis=1)
c1 = np.cumsum(np.vstack([np.zeros(U.shape[1]), U]), axis=0)
c2 = np.cumsum(np.vstack([np.zeros(U.shape[1]), U ** 2]), axis=0)
mean = (c1[n:] - c1[:-n]) / n
env = np.sqrt(np.maximum((c2[n:] - c2[:-n]) / n - mean ** 2, 0)).mean(axis=1)
tenv = tu[n // 2: n // 2 + len(env)]

quiet = env[(tenv > t_start + 1.0) & (tenv < cues[0])]
base = np.median(quiet)
print(f"packets: {len(T)} in {T[-1] - T[0]:.1f}s -> {len(T) / (T[-1] - T[0]):.0f} pkt/s")
print(f"quiet: median {base:.3f}, max {quiet.max() / base:.1f}x median")
ratios = []
for i, c in enumerate(cues):
    m = (tenv >= c + 0.3) & (tenv < c + 2.5)
    r = env[m].max() / base
    ratios.append(r)
    print(f"  pass {i + 1}: peak {r:.1f}x quiet")
print("VERDICT:", "trace visible" if np.median(ratios) >= 2.5 else "no clear trace - hand probably not crossing the line")
