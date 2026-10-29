"""Records raw CSI lines with PC timestamps while guiding the user with beeps.

1 long low beep  = STILL phase
2 short high beeps = WAVE phase

Usage: python record_protocol.py <port> <output file>
"""
import os
import sys
import threading
import time

import serial

from bip import beep

PHASE_SEC = 15
PHASES = ["still", "wave"] * 3
LEAD_IN_SEC = 10

if len(sys.argv) != 3:
    sys.exit("Usage: python record_protocol.py <port> <output file>")
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

    # Lead-in: three ticks so the user knows recording has started
    for _ in range(3):
        beep(600, 150)
        time.sleep(1)
    time.sleep(LEAD_IN_SEC - 3)

    for name in PHASES:
        start = time.time()
        if name == "still":
            beep(400, 700)
        else:
            beep(1200, 150)
            time.sleep(0.1)
            beep(1200, 150)
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
