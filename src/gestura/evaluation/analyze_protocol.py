"""Compares CSI amplitude fluctuation between STILL and WAVE phases of a guided recording."""
import csv
import json
import sys
from io import StringIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

WINDOW_SEC = 1.0
EDGE_SEC = 1.5  # ignore phase transitions (reaction time)

path = sys.argv[1]
times, amps = [], []
with open(path, encoding="utf-8") as f:
    for raw in f:
        ts, _, line = raw.partition("|")
        try:
            row = next(csv.reader(StringIO(line)))
            data = json.loads(row[-1])
        except (ValueError, StopIteration, json.JSONDecodeError):
            continue
        if len(data) != int(row[-3]):
            continue
        iq = np.array(data, dtype=float).reshape(-1, 2)  # [imag, real] pairs
        times.append(float(ts))
        amps.append(np.hypot(iq[:, 0], iq[:, 1]))

lengths = {len(a) for a in amps}
n = max(lengths, key=lambda L: sum(len(a) == L for a in amps))
keep = [i for i, a in enumerate(amps) if len(a) == n]
t = np.array([times[i] for i in keep])
A = np.array([amps[i] for i in keep])
A = A[:, A.mean(axis=0) > 1.0]  # drop null/guard subcarriers

phases = []
with open(path + ".phases", encoding="utf-8") as f:
    rows = [line.strip().split(",") for line in f if line.strip()]
for (name, start), (_, nxt) in zip(rows, rows[1:]):
    phases.append((name, float(start), float(nxt)))
t0 = phases[0][1]

# Motion score per window: mean over subcarriers of amplitude std within the window,
# normalised by each subcarrier's mean amplitude so strong subcarriers don't dominate
norm = A / A.mean(axis=0)
win_t, win_score, win_label = [], [], []
for name, s, e in phases:
    w = s + EDGE_SEC
    while w + WINDOW_SEC <= e - EDGE_SEC:
        m = (t >= w) & (t < w + WINDOW_SEC)
        if m.sum() >= 10:
            win_t.append(w + WINDOW_SEC / 2 - t0)
            win_score.append(norm[m].std(axis=0).mean())
            win_label.append(name)
        w += WINDOW_SEC
win_t, win_score, win_label = np.array(win_t), np.array(win_score), np.array(win_label)

still = win_score[win_label == "still"]
wave = win_score[win_label == "wave"]
duration = t[-1] - t[0]
print(f"packets: {len(t)} over {duration:.1f}s -> {len(t) / duration:.0f} pkt/s, subcarriers used: {A.shape[1]}")
print(f"still windows: n={len(still)} median={np.median(still):.4f} max={still.max():.4f}")
print(f"wave  windows: n={len(wave)} median={np.median(wave):.4f} min={wave.min():.4f}")
print(f"median ratio wave/still: {np.median(wave) / np.median(still):.1f}x")

# Best single threshold: how many windows does it label correctly?
best = max(
    ((thr, ((still < thr).sum() + (wave >= thr).sum()) / len(win_score)) for thr in np.sort(win_score)),
    key=lambda x: x[1],
)
print(f"best threshold {best[0]:.4f} -> window accuracy {best[1] * 100:.0f}%")
for name, s, e in phases:
    sel = (win_t >= s - t0) & (win_t < e - t0)
    print(f"  {name:5s} {s - t0:5.1f}-{e - t0:5.1f}s median={np.median(win_score[sel]):.4f}")

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
for name, s, e in phases:
    for ax in (ax1, ax2):
        ax.axvspan(s - t0, e - t0, color="#f2b5a0" if name == "wave" else "#dde3ea", alpha=0.6, lw=0)
ax1.plot(t - t0, norm[:, :: max(1, A.shape[1] // 12)], lw=0.6)
ax1.set_ylabel("Normalised amplitude\n(12 subcarriers)")
ax1.set_title("CSI amplitude - grey: STILL, orange: WAVE")
ax2.scatter(win_t, win_score, c=["#c0392b" if l == "wave" else "#34495e" for l in win_label], s=22)
ax2.axhline(best[0], color="#7f8c8d", ls="--", lw=1, label=f"threshold (window accuracy {best[1] * 100:.0f}%)")
ax2.set_ylabel("Motion score\n(1 s window)")
ax2.set_xlabel("Time (s)")
ax2.legend(loc="upper left")
fig.tight_layout()
out_png = path.rsplit(".", 1)[0] + ".png"
fig.savefig(out_png, dpi=130)
print("plot:", out_png)
