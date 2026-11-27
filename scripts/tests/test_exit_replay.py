import exit_replay as e

TR = {"tracked_entry_price": 100.0, "initial_stop_loss": 60.0, "stop_loss": 60.0, "target_1": 120.0, "target_2": 150.0,
      "actual_entry_time": "2026-11-26T10:00:00+05:30"}


def C(*rows):
    return {f"10:{i + 1:02d}": list(r) for i, r in enumerate(rows)}


def test_baseline_t2_and_sl():
    assert e.replay(TR, C((100, 125, 99, 120), (120, 151, 119, 150)), {})[1] == "T2"
    assert e.replay(TR, C((100, 105, 55, 58)), {})[0] == -40.0


def test_be_after_t1_exits_at_entry_but_keeps_straight_t2():
    path = C((100, 121, 100, 120), (120, 121, 99, 100))
    assert e.replay(TR, path, {"be_after_t1": True}) == (0.0, "T1_SL")
    assert e.replay(TR, C((100, 121, 100, 120), (120, 151, 119, 150)), {"be_after_t1": True})[1] == "T2"


def test_early_be_kills_deep_dip_winner():
    path = C((100, 107, 100, 106), (106, 107, 80, 90), (90, 151, 90, 150))
    assert e.replay(TR, path, {})[1] == "T2"
    assert e.replay(TR, path, {"be_at_pct": 6.0}) == (0.0, "SL")


def test_same_candle_stop_and_target_counts_as_stop():
    assert e.replay(TR, C((100, 151, 55, 100)), {})[1] == "SL"
