import json
import price_stream as ps


def test_parse_feeds_ltpc_and_full():
    by = {"NSE_INDEX|Nifty 50": "NIFTY", "MCX_FO|1": "CRUDEOIL", "BSE_INDEX|SENSEX": "SENSEX"}
    msg = {"type": "live_feed", "feeds": {
        "NSE_INDEX|Nifty 50": {"ltpc": {"ltp": 25000.5, "cp": 24900}},
        "MCX_FO|1": {"fullFeed": {"marketFF": {"ltpc": {"ltp": 5321}}}},
        "BSE_INDEX|SENSEX": {"fullFeed": {"indexFF": {"ltpc": {"ltp": 82000, "cp": 82100}}}},
        "OTHER|9": {"ltpc": {"ltp": 1}}}}
    r = ps.parse_feeds(msg, by)
    assert r["NIFTY"] == {"ltp": 25000.5, "prev": 24900.0, "chg": 0.4}
    assert r["CRUDEOIL"]["chg"] is None and r["SENSEX"]["chg"] == -0.12 and "OTHER" not in r
    assert ps.parse_feeds({}, by) == {} and ps.parse_feeds(None, by) == {}


def test_resolve_mcx_nearest_future():
    rows = [{"segment": "MCX_FO", "instrument_type": "FUT", "underlying_symbol": "CRUDEOIL", "expiry": 200, "instrument_key": "MCX_FO|b"},
            {"segment": "MCX_FO", "instrument_type": "FUT", "underlying_symbol": "CRUDEOIL", "expiry": 150, "instrument_key": "MCX_FO|a"},
            {"segment": "MCX_FO", "instrument_type": "FUT", "underlying_symbol": "CRUDEOIL", "expiry": 50, "instrument_key": "MCX_FO|old"},
            {"segment": "MCX_FO", "instrument_type": "OPTFUT", "underlying_symbol": "GOLD", "expiry": 150, "instrument_key": "MCX_FO|opt"}]
    assert ps.resolve_mcx(rows, 100) == {"CRUDEOIL": "MCX_FO|a"}


def test_write_snapshot(tmp_path):
    seen = []
    p = str(tmp_path / "p.json")
    ps.write_snapshot({"NIFTY": {"ltp": 1}}, p, seen.append)
    assert json.load(open(p))["p"]["NIFTY"]["ltp"] == 1 and seen == [p]
