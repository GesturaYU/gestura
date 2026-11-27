"""Checks that trial labels line up with the packets on the host monotonic clock (GES-71 dry run)."""


def check_alignment(trials, events, receiver_ids, rate_hz, min_fraction=0.8):
    """Return a list of problems; an empty list means the labels and packets line up.

    Checks: trials are in order and do not overlap, every window lies inside the captured
    packet stream, and every receiver delivered at least `min_fraction` of the packets the
    window should hold at `rate_hz`.
    """
    problems = []
    records = {rid: sorted(e.host_monotonic_ns for e in events
                           if e.receiver_id == rid and e.kind == "record") for rid in receiver_ids}
    prev_end = None
    for t in trials:
        if t.cue_end_monotonic_ns <= t.cue_start_monotonic_ns:
            problems.append(f"trial {t.trial_id}: end is not after start")
        if prev_end is not None and t.cue_start_monotonic_ns < prev_end:
            problems.append(f"trial {t.trial_id}: overlaps the previous trial")
        prev_end = t.cue_end_monotonic_ns
        window_s = (t.cue_end_monotonic_ns - t.cue_start_monotonic_ns) / 1e9
        expected = rate_hz * window_s
        for rid, times in records.items():
            if not times or t.cue_start_monotonic_ns < times[0] or t.cue_end_monotonic_ns > times[-1]:
                problems.append(f"trial {t.trial_id}: window is outside the {rid} packet stream")
                continue
            n = sum(1 for x in times if t.cue_start_monotonic_ns <= x < t.cue_end_monotonic_ns)
            if n < min_fraction * expected:
                problems.append(f"trial {t.trial_id}: {rid} has {n} packets, expected about {expected:.0f}")
    return problems
