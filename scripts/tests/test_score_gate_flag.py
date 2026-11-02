"""SCORE_GATE_ENABLED=False: a low-score ('Reject' grade) strike that clears every hard gate still reaches the
shadow-candidate step; with the flag True the old score floor rejects it first."""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
sys.path.insert(0, _here)

import test_scanner_to_alert_pipeline as pipeline
import test_probability_calibration_pipeline as calib
import scanner


def _run(flag):
    scanner.reset_shadow_candidates()
    orig_cfg, orig_grade, orig_shadow = scanner.config.SCORE_GATE_ENABLED, scanner.trade_score.compute_grade, scanner.PROBABILITY_SHADOW_MODE_ENABLED
    scanner.config.SCORE_GATE_ENABLED = flag
    scanner.trade_score.compute_grade = lambda s: "Reject"
    scanner.PROBABILITY_SHADOW_MODE_ENABLED = True
    try:
        pipeline._run_mocked_score_strike(**calib._valid_kwargs(calibrated_probability_override=None))
        return scanner.get_shadow_candidates()
    finally:
        scanner.config.SCORE_GATE_ENABLED, scanner.trade_score.compute_grade = orig_cfg, orig_grade
        scanner.PROBABILITY_SHADOW_MODE_ENABLED = orig_shadow


def test_flag_off_low_score_reaches_shadow():
    assert len(_run(False)) == 1


def test_flag_on_low_score_rejected():
    assert _run(True) == []


if __name__ == "__main__":
    test_flag_off_low_score_reaches_shadow()
    test_flag_on_low_score_rejected()
    print("ok")
