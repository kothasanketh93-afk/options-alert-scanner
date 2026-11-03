"""ATM strike failing only the ATR chase ceiling -> shadow candidate 'chase_bypass', never a live setup."""
import os, sys
_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here)); sys.path.insert(0, _here)
import test_scanner_to_alert_pipeline as pipeline
import test_extension_filter as ext
import scanner
import config

_OPEN = ext._SESSION_OPEN


def _run(spot_offset, flag=True, conditions=True):
    scanner.reset_shadow_candidates()
    o = (config.CHASE_BYPASS_SHADOW_ENABLED, config.CONDITIONS_ALERT_MODE, scanner.PROBABILITY_SHADOW_MODE_ENABLED)
    config.CHASE_BYPASS_SHADOW_ENABLED, config.CONDITIONS_ALERT_MODE, scanner.PROBABILITY_SHADOW_MODE_ENABLED = flag, conditions, True
    try:
        kw = ext._fully_confirmed_kwargs(_OPEN + spot_offset, 50.0)
        kw["calibrated_probability_override"] = None
        res = pipeline._run_mocked_score_strike(**kw)
        return res, scanner.get_shadow_candidates()
    finally:
        config.CHASE_BYPASS_SHADOW_ENABLED, config.CONDITIONS_ALERT_MODE, scanner.PROBABILITY_SHADOW_MODE_ENABLED = o


def test_extended_atm_becomes_shadow_only():
    res, sh = _run(150.0)
    assert res is None, "never a live setup"
    assert len(sh) == 1 and sh[0]["strategy"] == "chase_bypass" and sh[0]["candidate_id"].endswith(":CHASE")
    assert sh[0]["execution_mode"] == "SHADOW"


def test_flag_off_no_shadow():
    res, sh = _run(150.0, flag=False)
    assert res is None and sh == []


def test_not_extended_unchanged():
    res, sh = _run(40.0)
    assert res is not None and all(c.get("strategy") != "chase_bypass" for c in sh)


if __name__ == "__main__":
    test_extended_atm_becomes_shadow_only(); test_flag_off_no_shadow(); test_not_extended_unchanged(); print("ok")
