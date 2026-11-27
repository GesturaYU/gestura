import random
from collections import Counter

from gestura.app.collection.trial_plan import CLASS_COUNTS, make_trial_plan


def test_counts_match_the_recording_procedure():
    plan = make_trial_plan(random.Random(1))
    assert len(plan) == 50
    assert Counter(t.class_label for t in plan) == CLASS_COUNTS


def test_trial_ids_run_from_one_to_fifty():
    plan = make_trial_plan(random.Random(1))
    assert [t.trial_id for t in plan] == list(range(1, 51))


def test_the_same_rng_seed_gives_the_same_order():
    assert make_trial_plan(random.Random(7)) == make_trial_plan(random.Random(7))


def test_the_order_is_shuffled():
    plan = make_trial_plan(random.Random(3))
    in_class_order = sorted(plan, key=lambda t: list(CLASS_COUNTS).index(t.class_label))
    assert [t.class_label for t in plan] != [t.class_label for t in in_class_order]
