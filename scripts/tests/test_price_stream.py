import json
import price_stream as ps


def test_parse_tick_paise_and_change():
    by = {(1, "99926000"): "NIFTY", (5, "123"): "CRUDEOIL"}
    r = ps.parse_tick({"exchange_type": 1, "token": '"99926000"', "last_traded_price": 2500050, "closed_price": 2490000}, by)
    assert r == ("NIFTY", {"ltp": 25000.5, "prev": 24900.0, "chg": 0.4})
    assert ps.parse_tick({"exchange_type": 5, "token": "123", "last_traded_price": 532100}, by)[1]["chg"] is None
    assert ps.parse_tick({"exchange_type": 1, "token": "999", "last_traded_price": 1}, by) is None
    assert ps.parse_tick("junk", by) is None


def test_write_snapshot(tmp_path):
    seen = []
    p = str(tmp_path / "p.json")
    ps.write_snapshot({"NIFTY": {"ltp": 1}}, p, seen.append)
    assert json.load(open(p))["p"]["NIFTY"]["ltp"] == 1 and seen == [p]
