import strategy_scorecard as S


def row(date, at, real, sym="NIFTY", d="CE", res=None):
    return {"strat": "x", "date": date, "at": at, "real": real, "net": real, "res": real + 2 if res is None else res,
            "t1": False, "key": at, "sym": sym, "dir": d}


def test_sample_labels():
    assert [S.sample_label(n) for n in (1, 9, 10, 29, 30, 99, 100, 199, 200)] == [
        "Very thin", "Very thin", "Building", "Building", "Preliminary", "Preliminary", "Developing", "Developing", "Larger sample"]


def test_wilson_interval_brackets_rate_and_widens_when_thin():
    lo, hi = S.wilson_ci(6, 10)
    assert lo < 60 < hi
    lo2, hi2 = S.wilson_ci(60, 100)
    assert (hi2 - lo2) < (hi - lo)
    assert S.wilson_ci(0, 0) == (0.0, 100.0)


def test_same_move_variants_count_once():
    g = [row("2026-11-09", "2026-11-09T10:00:00+05:30", 5), row("2026-11-09", "2026-11-09T10:10:00+05:30", 5),
         row("2026-11-09", "2026-11-09T12:00:00+05:30", -3), row("2026-11-09", "2026-11-09T10:05:00+05:30", 5, d="PE")]
    assert len(S._clusters(g)) == 3          # ITM/ATM/OTM-style repeats within 30 min of one move = one opportunity


def test_drawdown_and_losing_streak():
    g = [row("2026-11-09", f"2026-11-09T10:{i:02d}:00+05:30", v) for i, v in enumerate([5, -2, -3, -1, 4])]
    dd, streak = S.max_drawdown_and_streak(g)
    assert dd == -6 and streak == 3


def test_bootstrap_is_deterministic_and_small_samples_have_no_interval():
    v = [1.0, -2.0, 3.0, 0.5, -1.0, 2.0]
    assert S.bootstrap_ci(v) == S.bootstrap_ci(v)
    assert S.bootstrap_ci([1.0]) == (None, None)


def test_status_never_claims_more_than_shadow_validated_and_thin_cannot_validate():
    g = [row("2026-11-09", "2026-11-09T10:00:00+05:30", 50)] * 3
    assert S.evidence(g)["status"] in ("Not enough data", "Provisional leader", "Not validated")
    losing = [row(f"2026-11-{9 + i % 5:02d}", f"2026-11-{9 + i % 5:02d}T{9 + i % 6:02d}:{i % 60:02d}:00+05:30", -2.0) for i in range(40)]
    assert S.evidence(losing)["status"] == "Not validated"


def test_zero_loss_profit_factor_is_capped():
    rows = [{**row("2026-11-09", "2026-11-09T10:00:00+05:30", 4), "res": 6.0}, {**row("2026-11-10", "2026-11-10T10:00:00+05:30", 4), "res": 6.0}]
    assert S.score_rows(rows)[0]["pf"] == 9.99
