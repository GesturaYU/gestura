"""Trial plan for one cued recording session (GES-71).

The counts come from the recording procedure (GES-73, Section 5): 10 repetitions of each
gesture and 20 "no gesture" samples, in randomized order.
"""
import random
from dataclasses import dataclass

from gestura.app.collection.session_files import CLASS_LABELS

CLASS_COUNTS = {
    "push": 10,
    "slow_wave": 10,
    "fast_wave": 10,
    "no_gesture": 20,
}


@dataclass(frozen=True)
class PlannedTrial:
    trial_id: int
    class_label: str


def make_trial_plan(rng=None, counts=None):
    """Return the trials of one session in random order, numbered from 1.

    Labels are repeated by their counts and then shuffled, so every session has exactly the
    procedure's counts. The realized order is stored in trials.csv, so no seed is kept; `rng`
    exists so that tests can get a fixed order. `counts` exists for short tests only.
    """
    rng = rng or random.Random()
    counts = counts or CLASS_COUNTS
    labels = [label for label in CLASS_LABELS for _ in range(counts.get(label, 0))]
    rng.shuffle(labels)
    return [PlannedTrial(trial_id=i, class_label=label) for i, label in enumerate(labels, start=1)]
