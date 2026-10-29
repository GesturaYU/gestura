"""STILL / SLOW / FAST separability: motion score + dominant motion frequency,
evaluated with leave-one-round-out nearest-centroid classification."""
import csv
import json
import sys
from io import StringIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

WIN_SEC, STEP_SEC, EDGE_SEC, FS = 3.0, 1.0, 1.5, 50.0
CLASSES = ["still", "slow", "fast"]
COLORS = {"still": "#34495e", "slow": "#2980b9", "fast": "#c0392b"}

path = sys.argv[1]
T, A = [], []
with open(path, encoding="utf-8") as f:
    for raw in f:
        ts, _, line = raw.partition("|")
        try:
            row = next(csv.reader(StringIO(line)))
            data = json.loads(row[-1])
        except (ValueError, StopIteration, json.JSONDecodeError):
            continue
        if len(data) != int(row[-3]) or len(data) != 384:
            continue
        iq = np.array(data, dtype=float).reshape(-1, 2)
        T.append(float(ts))
        A.append(np.hypot(iq[:, 0], iq[:, 1]))
T, A = np.array(T), np.array(A)
A = A[:, A.mean(axis=0) > 1.0]
A = A / A.mean(axis=0)

rows = [line.strip().split(",") for line in open(path + ".phases", encoding="utf-8") if line.strip()]
phases = [(a[0], float(a[1]), float(b[1])) for a, b in zip(rows, rows[1:])]
t0 = phases[0][1]

feats = []  # (round, label, t_mid, score, dom_freq)
round_of = {}
for i, (name, s, e) in enumerate(phases):
    rnd = i // len(CLASSES)
    w = s + EDGE_SEC
    while w + WIN_SEC <= e - EDGE_SEC + 1e-9:
        m = (T >= w) & (T < w + WIN_SEC)
        if m.sum() < 20 * WIN_SEC:
            w += STEP_SEC
            continue
        X = A[m]
        score = X.std(axis=0).mean()
        # First principal component = dominant common fluctuation across subcarriers
        Xc = X - X.mean(axis=0)
        _, _, vt = np.linalg.svd(Xc, full_matrices=False)
        pc = Xc @ vt[0]
        tu = np.arange(w, w + WIN_SEC, 1 / FS)
        sig = np.interp(tu, T[m], pc)
        sig = (sig - sig.mean()) * np.hanning(len(sig))
        spec = np.abs(np.fft.rfft(sig, n=1024))
        fr = np.fft.rfftfreq(1024, 1 / FS)
        band = (fr >= 0.4) & (fr <= 6.0)
        dom = fr[band][np.argmax(spec[band])]
        feats.append((rnd, name, w + WIN_SEC / 2 - t0, score, dom))
        w += STEP_SEC

rnds = np.array([f[0] for f in feats])
labels = np.array([f[1] for f in feats])
tmid = np.array([f[2] for f in feats])
F = np.array([[np.log(f[3]), f[4]] for f in feats])

for c in CLASSES:
    sel = labels == c
    print(f"{c:5s} n={sel.sum():2d}  score median={np.exp(np.median(F[sel, 0])):.3f}  "
          f"dom freq median={np.median(F[sel, 1]):.2f} Hz  IQR=[{np.percentile(F[sel, 1], 25):.2f}, {np.percentile(F[sel, 1], 75):.2f}]")


def loro(feature_idx):
    pred = np.empty(len(labels), dtype=object)
    for r in np.unique(rnds):
        tr, te = rnds != r, rnds == r
        mu, sd = F[tr][:, feature_idx].mean(0), F[tr][:, feature_idx].std(0) + 1e-9
        Z = (F[:, feature_idx] - mu) / sd
        cents = {c: Z[tr & (labels == c)].mean(0) for c in CLASSES}
        for i in np.where(te)[0]:
            pred[i] = min(CLASSES, key=lambda c: np.sum((Z[i] - cents[c]) ** 2))
    return pred


for name, idx in [("score only", [0]), ("freq only", [1]), ("score + freq", [0, 1])]:
    pred = loro(idx)
    acc = (pred == labels).mean()
    print(f"\n[{name}] leave-one-round-out accuracy: {acc * 100:.0f}%")
    print("        pred-> " + " ".join(f"{c:>6s}" for c in CLASSES))
    for c in CLASSES:
        print(f"  true {c:5s}  " + " ".join(f"{np.sum((labels == c) & (pred == p)):6d}" for p in CLASSES))
    moving = labels != "still"
    acc_m = (pred[moving] == labels[moving]).mean()
    print(f"  slow vs fast only (moving windows): {acc_m * 100:.0f}%")

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
for name, s, e in phases:
    for ax in (ax1, ax2):
        ax.axvspan(s - t0, e - t0, color=COLORS[name], alpha=0.12, lw=0)
for c in CLASSES:
    sel = labels == c
    ax1.scatter(tmid[sel], np.exp(F[sel, 0]), c=COLORS[c], s=22, label=c)
    ax2.scatter(tmid[sel], F[sel, 1], c=COLORS[c], s=22, label=c)
ax1.set_ylabel("Motion score\n(3 s window)")
ax1.set_title("STILL / SLOW / FAST - background shows the instructed phase")
ax1.legend(loc="upper left", ncol=3)
ax2.set_ylabel("Dominant motion\nfrequency (Hz)")
ax2.set_xlabel("Time (s)")
fig.tight_layout()
out_png = path.rsplit(".", 1)[0] + ".png"
fig.savefig(out_png, dpi=130)
print("\nplot:", out_png)
