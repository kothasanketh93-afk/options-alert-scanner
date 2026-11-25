import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import opportunity_score


class _Stop(Exception):
    pass


def test_excluded_symbols_never_resolved(monkeypatch):
    seen = []
    monkeypatch.setattr(opportunity_score.universe, "fno_eligible_stock_symbols", lambda d: ["SILVER", "TCS"])
    monkeypatch.setattr(opportunity_score, "_resolve_instrument_key", lambda s, d: seen.append(s) or "K_" + s)

    class _B:
        def get_batch_quotes(self, keys):
            raise _Stop(keys)

    monkeypatch.setattr(opportunity_score.market_data, "get_broker", lambda: _B())
    with pytest.raises(_Stop) as e:
        opportunity_score.build_shortlist("2026-11-26")
    assert seen == ["TCS"] and e.value.args[0] == ["K_TCS"]
