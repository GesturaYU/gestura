"""Compare CSI amplitude and phase features on existing recordings (GES-57).

Features, all computed from the same raw CSI ([imag, real] pairs per subcarrier):
  amplitude        amplitude per subcarrier, normalised by the subcarrier mean
  raw_phase        phase angle per subcarrier, as measured
  detrended_phase  phase with the linear trend across subcarriers removed per packet

The analysis methods are the ones of analyze_protocol.py and analyze_rhythm.py:
  motion  1 s windows, one threshold (best on the recording) for still versus wave
  rhythm  3 s windows, motion score + dominant frequency, leave-one-round-out
          nearest-centroid classification of still / slow / fast
For the phase features the motion score is the circular standard deviation of the phase
over the window (a plain standard deviation would be dominated by the 2*pi wrap).
"""
import argparse
import csv
import json
from io import StringIO

import numpy as np

WIN_MOTION, WIN_RHYTHM, STEP, EDGE, FS = 1.0, 3.0, 1.0, 1.5, 50.0
FEATURES = ("amplitude", "raw_phase", "detrended_phase")
CLASSES = ("still", "slow", "fast")


def load_recording(path):
    """Return packet times and complex CSI (packets x 192 subcarriers) of a recording."""
    times, rows = [], []
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
            iq = np.array(data, dtype=float).reshape(-1, 2)  # [imag, real] pairs
            times.append(float(ts))
            rows.append(iq[:, 1] + 1j * iq[:, 0])
    return np.array(times), np.array(rows)


def load_phases(path):
    rows = [line.strip().split(",") for line in open(path + ".phases", encoding="utf-8") if line.strip()]
    return [(a[0], float(a[1]), float(b[1])) for a, b in zip(rows, rows[1:])]


def used_subcarriers(z):
    """Indices of subcarriers that carry signal (null and guard subcarriers dropped)."""
    return np.where(np.abs(z).mean(axis=0) > 1.0)[0]


def circular_std(phase):
    """Circular standard deviation over the first axis, per column."""
    r = np.abs(np.exp(1j * phase).mean(axis=0))
    return np.sqrt(-2.0 * np.log(np.clip(r, 1e-6, 1.0)))


def detrend_phase(phase, idx):
    """Remove, per packet, the straight line fitted across the subcarrier index."""
    unwrapped = np.unwrap(phase, axis=1)
    design = np.column_stack([idx, np.ones(len(idx))]).astype(float)
    coef, *_ = np.linalg.lstsq(design, unwrapped.T, rcond=None)
    resid = (unwrapped.T - design @ coef).T
    return np.angle(np.exp(1j * resid))


def feature_matrix(z, feature):
    """Per-packet, per-subcarrier values of a feature (packets x used subcarriers)."""
    idx = used_subcarriers(z)
    zu = z[:, idx]
    if feature == "amplitude":
        amp = np.abs(zu)
        return amp / amp.mean(axis=0)
    phase = np.angle(zu)
    if feature == "raw_phase":
        return phase
    if feature == "detrended_phase":
        return detrend_phase(phase, idx)
    raise ValueError(feature)


def window_score(x, feature):
    """Motion score of one window: mean over subcarriers of the spread of the feature."""
    if feature == "amplitude":
        return x.std(axis=0).mean()
    return circular_std(x).mean()


def dominant_frequency(x, t, w, feature):
    """Dominant common fluctuation (first principal component) frequency in Hz."""
    sig_matrix = x if feature == "amplitude" else np.unwrap(x, axis=0)
    xc = sig_matrix - sig_matrix.mean(axis=0)
    _, _, vt = np.linalg.svd(xc, full_matrices=False)
    pc = xc @ vt[0]
    tu = np.arange(w, w + WIN_RHYTHM, 1 / FS)
    sig = np.interp(tu, t, pc)
    sig = (sig - sig.mean()) * np.hanning(len(sig))
    spec = np.abs(np.fft.rfft(sig, n=1024))
    fr = np.fft.rfftfreq(1024, 1 / FS)
    band = (fr >= 0.4) & (fr <= 6.0)
    return fr[band][np.argmax(spec[band])]


def motion_result(path, feature):
    """Still versus wave: median ratio and best single-threshold window accuracy."""
    t, z = load_recording(path)
    x = feature_matrix(z, feature)
    scores, labels = [], []
    for name, s, e in load_phases(path):
        w = s + EDGE
        while w + WIN_MOTION <= e - EDGE:
            m = (t >= w) & (t < w + WIN_MOTION)
            if m.sum() >= 10:
                scores.append(window_score(x[m], feature))
                labels.append(name)
            w += STEP
    scores, labels = np.array(scores), np.array(labels)
    still, wave = scores[labels == "still"], scores[labels == "wave"]
    acc = max(((still < thr).sum() + (wave >= thr).sum()) / len(scores) for thr in np.sort(scores))
    return {"windows": len(scores), "ratio": np.median(wave) / np.median(still), "accuracy": acc}


def rhythm_result(path, feature):
    """Still / slow / fast with score + dominant frequency, leave-one-round-out."""
    t, z = load_recording(path)
    x = feature_matrix(z, feature)
    rnds, labels, feats = [], [], []
    for i, (name, s, e) in enumerate(load_phases(path)):
        w = s + EDGE
        while w + WIN_RHYTHM <= e - EDGE + 1e-9:
            m = (t >= w) & (t < w + WIN_RHYTHM)
            if m.sum() >= 20 * WIN_RHYTHM:
                rnds.append(i // len(CLASSES))
                labels.append(name)
                feats.append([np.log(window_score(x[m], feature) + 1e-9),
                              dominant_frequency(x[m], t[m], w, feature)])
            w += STEP
    rnds, labels, f = np.array(rnds), np.array(labels), np.array(feats)
    pred = np.empty(len(labels), dtype=object)
    for r in np.unique(rnds):
        tr, te = rnds != r, rnds == r
        mu, sd = f[tr].mean(0), f[tr].std(0) + 1e-9
        zf = (f - mu) / sd
        cents = {c: zf[tr & (labels == c)].mean(0) for c in CLASSES}
        for k in np.where(te)[0]:
            pred[k] = min(CLASSES, key=lambda c: np.sum((zf[k] - cents[c]) ** 2))
    moving = labels != "still"
    return {"windows": len(labels), "accuracy": (pred == labels).mean(),
            "moving_accuracy": (pred[moving] == labels[moving]).mean()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for group in ("pilot", "repeat"):
        ap.add_argument(f"--{group}-motion", nargs="*", default=[], help="still/wave recordings")
        ap.add_argument(f"--{group}-rhythm", nargs="*", default=[], help="still/slow/fast recordings")
    args = ap.parse_args()

    print("| Group | Recording | Test | Feature | Windows | Result |")
    print("|---|---|---|---|---|---|")
    for group in ("pilot", "repeat"):
        for path in getattr(args, f"{group}_motion"):
            for feat in FEATURES:
                r = motion_result(path, feat)
                print(f"| {group} | {path.replace(chr(92), '/').split('/')[-1]} | motion | {feat} | "
                      f"{r['windows']} | {r['accuracy'] * 100:.0f}% (median ratio {r['ratio']:.2f}x) |")
        for path in getattr(args, f"{group}_rhythm"):
            for feat in FEATURES:
                r = rhythm_result(path, feat)
                print(f"| {group} | {path.replace(chr(92), '/').split('/')[-1]} | rhythm | {feat} | "
                      f"{r['windows']} | {r['accuracy'] * 100:.0f}% (moving only {r['moving_accuracy'] * 100:.0f}%) |")


if __name__ == "__main__":
    main()
