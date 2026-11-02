import os, sys
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_root, "research"))
import validate_strategies as v


def _rows(vals):
    return [{"date": "2026-11-%02d" % (1 + i % 20), "alert_time": "%05d" % i, "net_result_pct": str(x)} for i, x in enumerate(vals)]


def test_losing_strategy_fails():
    o = v.evaluate(_rows([-1.0, 0.5] * 60))
    assert o["gate1"] and not o["gate2"]


def test_winning_strategy_passes():
    o = v.evaluate(_rows([2.0, 1.0, 1.5] * 40))
    assert o["gate2"]


def test_too_few_trades_fails_gate1():
    assert not v.evaluate(_rows([2.0] * 20))["gate1"]


if __name__ == "__main__":
    test_losing_strategy_fails(); test_winning_strategy_passes(); test_too_few_trades_fails_gate1(); print("ok")
