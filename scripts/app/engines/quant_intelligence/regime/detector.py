"""
regime/detector.py -- Market Regime Engine.

Classifies the market into one of: Strong Trend, Trending, Range,
Sideways, High Volatility, Low Volatility, Gap Up, Gap Down, Expiry,
News Driven, High Volume, Low Volume.

Priority order matters: structural/event regimes (Expiry, Gap Up/Down)
are checked before volatility/trend regimes, since "it's expiry day AND
trending" is common -- the caller gets both facts in `reasons` even
though only one label is returned as the primary `regime`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..models.result import RegimeResult
from ..models.signal import Signal
from .analyzer import confidence_label


class RegimeDetector:
    def __init__(self, config: Dict[str, Any]) -> None:
        self._cfg = config.get("regime", {})

    def detect(self, signal: Signal) -> RegimeResult:
        reasons: List[str] = []
        cfg = self._cfg

        # --- Event-driven regimes (highest priority) -------------------------
        if signal.is_news_driven:
            reasons.append("Signal window flagged as news/event driven by the caller.")
            score = 65.0
            return RegimeResult(
                regime="News Driven", confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        if signal.is_expiry_day:
            reasons.append("Today is flagged as an expiry day; premium decay dominates.")
            score = 70.0
            return RegimeResult(
                regime="Expiry", confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        gap_threshold = cfg.get("gap_threshold_pct", 0.5)
        if signal.gap_pct is not None and abs(signal.gap_pct) >= gap_threshold:
            direction = "Gap Up" if signal.gap_pct > 0 else "Gap Down"
            reasons.append(f"Opening gap of {signal.gap_pct:.2f}% exceeds {gap_threshold}% threshold.")
            score = min(100.0, 50 + abs(signal.gap_pct) * 10)
            return RegimeResult(
                regime=direction, confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        # --- Volatility regimes ------------------------------------------------
        atr_pct = None
        if signal.atr is not None and signal.spot_price:
            atr_pct = (signal.atr / signal.spot_price) * 100
        high_vol = cfg.get("atr_high_vol_pct", 1.2)
        low_vol = cfg.get("atr_low_vol_pct", 0.35)

        if atr_pct is not None and atr_pct >= high_vol:
            reasons.append(f"ATR is {atr_pct:.2f}% of spot, at/above the high-volatility threshold {high_vol}%.")
            score = min(100.0, 55 + (atr_pct - high_vol) * 15)
            regime = "High Volatility"
            return RegimeResult(
                regime=regime, confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        # --- Volume regimes (checked alongside trend, can co-occur) -----------
        volume_regime = None
        if signal.volume is not None and signal.avg_volume:
            ratio = signal.volume / signal.avg_volume
            if ratio >= cfg.get("high_volume_ratio", 1.5):
                volume_regime = "High Volume"
                reasons.append(f"Volume is {ratio:.2f}x average -- unusually active.")
            elif ratio <= cfg.get("low_volume_ratio", 0.6):
                volume_regime = "Low Volume"
                reasons.append(f"Volume is {ratio:.2f}x average -- thin participation.")

        # --- Trend regimes -------------------------------------------------------
        adx = signal.adx
        strong_trend_th = cfg.get("adx_strong_trend", 30)
        trending_th = cfg.get("adx_trending", 20)

        if adx is not None and adx >= strong_trend_th:
            reasons.append(f"ADX {adx:.1f} >= {strong_trend_th} indicates a strong directional trend.")
            score = min(100.0, 60 + (adx - strong_trend_th))
            return RegimeResult(
                regime="Strong Trend", confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        if adx is not None and adx >= trending_th:
            reasons.append(f"ADX {adx:.1f} >= {trending_th} indicates a developing trend.")
            score = min(100.0, 45 + (adx - trending_th) * 1.5)
            regime = volume_regime or "Trending"
            if volume_regime:
                reasons.append(f"Trend co-occurring with {volume_regime.lower()} conditions.")
            return RegimeResult(
                regime="Trending", confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        if atr_pct is not None and atr_pct <= low_vol:
            reasons.append(f"ATR is {atr_pct:.2f}% of spot, at/below the low-volatility threshold {low_vol}%.")
            score = min(100.0, 55 + (low_vol - atr_pct) * 15)
            return RegimeResult(
                regime="Low Volatility", confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        if volume_regime:
            score = 55.0
            return RegimeResult(
                regime=volume_regime, confidence=confidence_label(score), regime_score=score, reasons=reasons
            )

        # --- Default: range-bound / sideways --------------------------------------
        if adx is not None and adx < trending_th:
            reasons.append(f"ADX {adx:.1f} below trending threshold {trending_th} -- no clear direction.")
            return RegimeResult(
                regime="Range", confidence=confidence_label(50.0), regime_score=50.0, reasons=reasons
            )

        reasons.append("Insufficient directional signal to classify a trend; defaulting to sideways read.")
        return RegimeResult(
            regime="Sideways", confidence=confidence_label(30.0), regime_score=30.0, reasons=reasons
        )
