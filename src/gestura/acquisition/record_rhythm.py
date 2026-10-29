"""Records raw CSI with PC timestamps for a STILL / SLOW / FAST rhythm protocol.

1 long low beep      = STILL
2 medium beeps       = SLOW  (hand up-down through the link, ~1 per second)
3 short high beeps   = FAST  (same motion, ~3 per second)

Usage: python record_rhythm.py <port> <output file>
"""
import os
import sys
import threading
import time

import serial

from bip import beep

PHASE_SEC = 10
PHASES = ["still", "slow", "fast"] * 4
LEAD_IN_SEC = 10
CUES = {
    "still": [(400, 700)],
    "slow": [(800, 200), (800, 200)],
    "fast": [(1400, 90), (1400, 90), (1400, 90)],
}

if len(sys.argv) != 3:
    sys.exit("Usage: python record_rhythm.py <port> <output file>")
port, out_path = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
ser = serial.Serial(port, 921600, timeout=1)
phase_log = []
stop = threading.Event()


def reader(f):
    while not stop.is_set():
        line = ser.readline().decode(errors="ignore").strip()
        if line.startswith("CSI_DATA"):
            f.write(f"{time.time():.4f}|{line}\n")


with open(out_path, "w", encoding="utf-8") as f:
    t = threading.Thread(target=reader, args=(f,), daemon=True)
    t.start()

    for _ in range(3):
        beep(600, 150)
        time.sleep(1)
    time.sleep(LEAD_IN_SEC - 3)

    for name in PHASES:
        start = time.time()
        for freq, dur in CUES[name]:
            beep(freq, dur)
            time.sleep(0.08)
        phase_log.append((name, start))
        time.sleep(PHASE_SEC - (time.time() - start))
    end = time.time()
    for _ in range(3):
        beep(900, 300)

    stop.set()
    t.join(timeout=2)

with open(out_path + ".phases", "w", encoding="utf-8") as f:
    for name, start in phase_log:
        f.write(f"{name},{start:.4f}\n")
    f.write(f"end,{end:.4f}\n")
print("done", out_path)
