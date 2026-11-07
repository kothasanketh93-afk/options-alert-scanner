from app.engines.sweep_reversal import signals as S

PRE = [(100, 100.5, 99.8, 100.2)] * 3
LEG = [(100, 101, 99.5, 100.5), (100.5, 102, 100, 101.5), (101.5, 103, 101, 102.5), (102.5, 102.8, 101.5, 101.8),
       (101.8, 102.2, 100.8, 101.0), (101.0, 102.0, 100.9, 101.8), (101.8, 102.6, 101.5, 102.4), (102.4, 102.7, 101.9, 102.0),
       (102.0, 102.2, 101.2, 101.4), (101.4, 102.3, 101.3, 102.2), (102.2, 102.9, 102.0, 102.7)]
BOS = (102.7, 104.5, 102.6, 104.3)
RETEST = (102.4, 103.6, 102.0, 103.4)


def mk(rows, flip=False):
    out = []
    for i, (o, h, l, c) in enumerate(rows):
        t = 9 * 60 + 15 + 5 * i
        ts = f"2026-11-06T{t // 60:02d}:{t % 60:02d}:00+05:30"
        out.append({"timestamp": ts, "open": 200 - o, "high": 200 - l, "low": 200 - h, "close": 200 - c} if flip
                   else {"timestamp": ts, "open": o, "high": h, "low": l, "close": c})
    return out


def test_bos_bullish_and_mirror():
    rows = PRE + LEG + [BOS]
    sig = S.detect_bos(mk(rows), 1.0)
    assert sig and sig["direction"] == "CE" and sig["level_name"] == "BOS" and sig["level_value"] == 102.7 and sig["extreme"] == 101.2
    sp = S.detect_bos(mk(rows, flip=True), 1.0)
    assert sp and sp["direction"] == "PE" and sp["level_value"] == 97.3 and sp["extreme"] == 98.8


def test_bos_needs_structure_and_displacement_and_no_lookahead():
    assert S.detect_bos(mk(PRE + LEG), 1.0) is None                         # break candle not there yet
    lower_low = list(LEG)
    lower_low[8] = (102.0, 102.2, 100.5, 101.4)                              # lower low: reversal, not continuation
    assert S.detect_bos(mk(PRE + lower_low + [BOS]), 1.0) is None
    weak = (102.7, 103.3, 102.6, 103.2)                                      # closes above but small range
    assert S.detect_bos(mk(PRE + LEG + [weak]), 1.0) is None


def test_bos_outside_window_ignored():
    assert S.detect_bos(mk(PRE + LEG + [BOS]), 1.0, window=(11 * 60, 12 * 60)) is None


def test_order_block_retest_first_touch_only():
    rows = PRE + LEG + [BOS, RETEST]
    sig = S.detect_order_block(mk(rows), 1.0)
    assert sig and sig["direction"] == "CE" and sig["level_name"] == "OB" and sig["ob"] == (101.2, 102.2) and sig["extreme"] == 101.2
    sp = S.detect_order_block(mk(rows, flip=True), 1.0)
    assert sp and sp["direction"] == "PE" and sp["ob"] == (97.8, 98.8)
    assert S.detect_order_block(mk(PRE + LEG + [BOS]), 1.0) is None          # no retest yet
    broke = (102.4, 102.5, 100.9, 101.0)                                      # closes below the OB low: setup dead
    assert S.detect_order_block(mk(PRE + LEG + [BOS, broke]), 1.0) is None
    weak_touch = (103.0, 103.1, 102.0, 102.1)                                 # touches, no reaction close above the zone
    rows2 = PRE + LEG + [BOS, weak_touch, (102.1, 103.0, 102.0, 102.9)]
    assert S.detect_order_block(mk(rows2), 1.0) is None                       # first return decided the setup; later bar ignored


def test_no_signal_on_flat_noise():
    flat = [(100, 100.6, 99.6, 100.1 + (i % 2) * 0.1) for i in range(30)]
    assert S.detect_bos(mk(flat), 1.0) is None and S.detect_order_block(mk(flat), 1.0) is None
