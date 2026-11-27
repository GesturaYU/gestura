import pytest

from gestura.app.collection.flow import ConsoleUI
from gestura.app.collection.trial_plan import PlannedTrial

TRIAL = PlannedTrial(trial_id=1, class_label="push")


def _ask(monkeypatch, answers):
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    return ConsoleUI().ask_failed(TRIAL)


def test_empty_answer_is_ok(monkeypatch):
    assert _ask(monkeypatch, [""]) == (False, "")


def test_f_asks_for_the_reason(monkeypatch):
    assert _ask(monkeypatch, ["f", "2"]) == (True, "hand_not_through_line")


@pytest.mark.parametrize("answer, reason", [("f1", "cue_missed"), ("f3", "wrong_gesture"),
                                            ("F4", "other")])
def test_f_with_a_number_is_failed_with_that_reason(monkeypatch, answer, reason):
    assert _ask(monkeypatch, [answer]) == (True, reason)


@pytest.mark.parametrize("unclear", ["x", "f5", "f0", "ok", "ff", "f 2"])
def test_unclear_answers_are_asked_again_not_taken_as_ok(monkeypatch, unclear):
    # The unclear answer is rejected; the next answer decides.
    assert _ask(monkeypatch, [unclear, "f2"]) == (True, "hand_not_through_line")


def test_an_invalid_reason_number_is_asked_again(monkeypatch):
    assert _ask(monkeypatch, ["f", "9", "abc", "4"]) == (True, "other")
