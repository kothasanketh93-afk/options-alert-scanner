"""ONE-OFF diagnostic (2026-11-06): why do GOLDM 5m candles differ from GOLD 5m candles while live quotes agree?"""
import os, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from angelone import AngelOneBroker

b = AngelOneBroker(); b.login()
master = b._scrip_master()
for name in ("GOLD", "GOLDM", "GOLDPETAL"):
    futs = sorted([x for x in master if x.get("exch_seg") == "MCX" and x.get("name") == name and x.get("instrumenttype") == "FUTCOM"],
                  key=lambda x: datetime.strptime(x["expiry"], "%d%b%Y"))
    print(name, [(x["symbol"], x["token"], x["expiry"], x.get("lotsize")) for x in futs[:4]])
    for x in futs[:2]:
        q = b._batch_quotes("MCX", [x["token"]]).get(x["token"], {})
        print("  quote", x["symbol"], "ltp", q.get("ltp"), "close", q.get("close"), "avgPrice", q.get("avgPrice"))
        since = (datetime.now() - timedelta(days=1)).replace(hour=21, minute=0, second=0).isoformat()
        cs = b.get_candles("MCX|" + x["token"], "5minute", since_iso=since)
        print("  5m", [(c["timestamp"][11:16], c["open"], c["high"], c["low"], c["close"]) for c in cs[:14]])
