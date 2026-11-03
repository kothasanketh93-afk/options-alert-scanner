import os, sys, tempfile
_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here))
import eod_image


def _t(sym, status):
    return {"symbol": sym, "direction": "CE", "strike": 100.0, "alert_time": "2026-11-03T09:31:00+05:30",
            "tracked_entry_price": 10.0, "stop_loss": 8.0, "target_1": 12.0, "target_2": 15.0, "status": status, "final_status": status}


def test_renders_png():
    st = {"trades": {"a": _t("A", "SL_HIT")}, "shadow_trades": {"b": _t("B", "T2_HIT"), "c": _t("C", "ACTIVE")}}
    p = os.path.join(tempfile.mkdtemp(), "x.png")
    assert eod_image.render(st, "2026-11-03", p)
    from PIL import Image
    im = Image.open(p); assert im.size[0] == 1500 and im.size[1] > 400


def test_empty_returns_false():
    assert eod_image.render({}, "d", os.path.join(tempfile.mkdtemp(), "x.png")) is False
