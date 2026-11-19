import json

import angelone


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return self._rows


def _broker():
    return angelone.AngelOneBroker.__new__(angelone.AngelOneBroker)


def test_scrip_master_cached_per_day(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIP_MASTER_CACHE_DIR", str(tmp_path / "c"))
    calls = []
    monkeypatch.setattr(angelone, "request_with_retry", lambda *a, **k: (calls.append(1), _Resp([{"token": "1"}]))[1])
    b = _broker()
    assert b._fetch_scrip_master() == [{"token": "1"}] and len(calls) == 1
    assert b._fetch_scrip_master() == [{"token": "1"}] and len(calls) == 1      # second call read the file, no download
    files = list((tmp_path / "c").glob("scrip_master_*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text()) == [{"token": "1"}]


def test_scrip_master_without_env_always_downloads(monkeypatch):
    monkeypatch.delenv("SCRIP_MASTER_CACHE_DIR", raising=False)
    calls = []
    monkeypatch.setattr(angelone, "request_with_retry", lambda *a, **k: (calls.append(1), _Resp([{"token": "1"}]))[1])
    b = _broker()
    b._fetch_scrip_master(); b._fetch_scrip_master()
    assert len(calls) == 2
