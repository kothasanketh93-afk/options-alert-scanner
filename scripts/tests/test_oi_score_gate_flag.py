import os, sys
_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here))
import inspect
from app.engines.options_intelligence import scoring
import config


def _fn():
    return [f for n, f in inspect.getmembers(scoring, inspect.isfunction) if "below floor" in inspect.getsource(f)][0]


def _call(score):
    f = _fn(); sig = inspect.signature(f)
    subs = {k: 0.9 for k in scoring._REJECTION_LABELS}
    kw = {}
    for name in sig.parameters:
        kw[name] = {"sub_scores": subs, "score": score, "iv_abnormal": False}[name]
    return f(**kw)


def test_low_composite_passes_when_gate_off():
    assert _call(40)[0] is True


def test_low_composite_rejected_when_gate_on():
    config.OPTIONS_INTELLIGENCE_SCORE_GATE_ENABLED = True
    try:
        assert _call(40)[0] is False
    finally:
        config.OPTIONS_INTELLIGENCE_SCORE_GATE_ENABLED = False


def test_hygiene_still_rejects():
    f = _fn(); subs = {k: 0.9 for k in scoring._REJECTION_LABELS}; k0 = [k for k in subs if k != "iv"][0]; subs[k0] = 0.1
    kw = {n: {"sub_scores": subs, "score": 90, "iv_abnormal": False}[n] for n in inspect.signature(f).parameters}
    assert f(**kw)[0] is False


if __name__ == "__main__":
    test_low_composite_passes_when_gate_off(); test_low_composite_rejected_when_gate_on(); test_hygiene_still_rejects(); print("ok")
