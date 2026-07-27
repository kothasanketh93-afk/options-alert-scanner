"""
tests/helpers.py -- Shared test utilities.

Every test that touches storage (pattern DB, trade history, adaptive
weights) must use `build_isolated_engine()` rather than the module-
level `QuantEngine` singleton, so tests never read/write the real
`data/` directory or interfere with each other.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from typing import Any, Dict, Optional

from ..facade import QuantEngineImpl
from ..models.signal import Signal
from ..utils.config_loader import load_config


def make_temp_dir() -> str:
    return tempfile.mkdtemp(prefix="qi_test_")


def build_isolated_engine(tmp_dir: str, overrides: Optional[Dict[str, Any]] = None) -> QuantEngineImpl:
    config = load_config()
    config.setdefault("pattern", {})["storage_path"] = os.path.join(tmp_dir, "patterns.db")
    config.setdefault("learning", {})["history_path"] = os.path.join(tmp_dir, "trade_history.jsonl")
    config["learning"]["weights_path"] = os.path.join(tmp_dir, "adaptive_weights.json")

    if overrides:
        config = _deep_merge(config, overrides)

    return QuantEngineImpl(config=config)


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def cleanup(tmp_dir: str) -> None:
    shutil.rmtree(tmp_dir, ignore_errors=True)


def sample_signal(**overrides: Any) -> Signal:
    base = dict(
        symbol="RELIANCE",
        direction="CE",
        spot_price=2500.0,
        option_price=45.5,
        strike=2500.0,
        trend="UP",
        ema_fast=2505.0,
        ema_slow=2490.0,
        vwap=2495.0,
        rsi=62.0,
        adx=28.0,
        atr=18.0,
        pcr=1.2,
        oi=150000.0,
        oi_change_pct=12.0,
        volume=50000.0,
        avg_volume=30000.0,
        iv=22.0,
        momentum_pct=0.6,
        market_breadth=0.4,
        breakout_strength=0.7,
        candle_structure="bullish_engulfing",
        min_oi_threshold=50000.0,
        min_volume_threshold=20000.0,
        spread_pct=0.5,
        gap_pct=0.1,
        is_expiry_day=False,
    )
    base.update(overrides)
    return Signal(**base)
