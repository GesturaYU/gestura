import sys

import gestura.acquisition.bip as bip

WARNING = (
    "sounddevice is not installed: cue tones use afplay and can start 0.3-0.9 s late. "
    "Run pip install -r requirements.txt."
)


def test_macos_falls_back_to_afplay_and_warns_once(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # import sounddevice raises ImportError
    calls = []
    monkeypatch.setattr(bip.subprocess, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(bip, "_stream", None)
    monkeypatch.setattr(bip, "_warned_afplay", False)

    bip.beep(1000, 150)
    bip.beep(500, 150)

    assert [args[0] for args in calls] == ["afplay", "afplay"]
    assert capsys.readouterr().out.count(WARNING) == 1
