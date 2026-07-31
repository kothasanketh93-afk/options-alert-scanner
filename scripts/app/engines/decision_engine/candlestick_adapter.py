"""
app/engines/decision_engine/candlestick_adapter.py -- Candlestick Pattern
Recognition integration glue.
------------------------------------------------------------------------------
Wires candlestick_patterns.py into the existing, unmodified Decision
Engine as an additional final step -- called from engine.py's evaluate(),
after Quant Intelligence and News Intelligence have already run, same
"invoked last, never disturbs what came before" posture as those two.

DISCOVERY, NOT DUPLICATION: this module does not recompute anything.
scanner.score_strike() already ran candlestick_patterns.analyze() this
cycle (on candles_15m it already had in memory) and attached the
result at trade["candlestick_patterns"] -- attach() below only READS
that field. No broker call, no candle fetch, no pattern-matching logic
here at all.

FAIL-SOFT BY DESIGN: attach() never raises. Any failure (config flag
off, or trade["candlestick_patterns"] missing/malformed) degrades to
decision_object["candlestick_patterns"] = {"enabled": False, ...} and
leaves every other field of the already-computed Decision Object --
including whatever quant_adapter/news_adapter already added --
completely untouched.

INFLUENCE IS OPT-IN, AND DELIBERATELY GENTLER THAN QUANT/NEWS: by
default (config.CANDLESTICK_PATTERNS_INFLUENCE_ENABLED = False)
candlestick reads are computed and attached for visibility/logging
only. Unlike quant_adapter/news_adapter's _apply_influence() -- which
CAN flip an approved trade to rejected -- this module never touches
decision_object["approved"] or ["decision"] at all, in either
direction. A single secondary pattern read on top of a 10-factor Trade
Score and two dedicated intelligence engines is too noisy a signal to
hand veto power to (see candlestick_patterns.py's own module docstring
and option_chain.directional_bias()'s documented 2026-07-24 incident
about hard-gating on a secondary signal). Flipping the influence flag
on instead lets a confidently-OPPOSING pattern read nudge
decision_object["confidence_score"] down by at most
config.CANDLESTICK_MAX_CONFIDENCE_DELTA points, and a confidently-
CONFIRMING read nudge it up by the same cap -- bounded, symmetric,
and never enough on its own to cross DECISION_ENGINE_MIN_CONFIDENCE
unless the trade was already sitting right at that line from other
factors.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from logging_setup import get_logger
from config import (
    CANDLESTICK_PATTERNS_ENABLED,
    CANDLESTICK_PATTERNS_INFLUENCE_ENABLED,
    CANDLESTICK_MAX_CONFIDENCE_DELTA,
)

_logger = get_logger("decision_engine.candlestick_patterns")


def _disabled_result(reason: str) -> Dict[str, Any]:
    return {"enabled": False, "reason": reason}


def _apply_influence(decision_object: dict, candlestick_read: dict) -> None:
    """Opt-in only (config.CANDLESTICK_PATTERNS_INFLUENCE_ENABLED).
    Nudges confidence_score by up to CANDLESTICK_MAX_CONFIDENCE_DELTA
    points, scaled by how far the alignment score sits from neutral
    (0.5) -- never flips approved/decision either way, unlike
    quant_adapter/news_adapter's influence gates."""
    score = candlestick_read.get("alignment_score")
    if score is None:
        return  # no directional pattern this cycle -- nothing to apply

    current_confidence = decision_object.get("confidence_score")
    if current_confidence is None:
        return

    # score is 0-1, 0.5 = neutral. Scale the ±0.5 max deviation onto
    # the ±CANDLESTICK_MAX_CONFIDENCE_DELTA point cap.
    deviation = score - 0.5  # -0.5..+0.5
    delta = deviation * 2.0 * CANDLESTICK_MAX_CONFIDENCE_DELTA
    new_confidence = max(0.0, min(100.0, current_confidence + delta))
    if new_confidence == current_confidence:
        return

    decision_object["confidence_score"] = round(new_confidence, 1)
    decision_object.setdefault("warnings", []).append(
        f"Confidence adjusted by candlestick pattern read "
        f"({'+' if delta >= 0 else ''}{round(delta, 1)}, "
        f"config.CANDLESTICK_PATTERNS_INFLUENCE_ENABLED)"
    )


def attach(trade: dict, decision_object: dict) -> None:
    """Mutates decision_object in place, adding
    decision_object['candlestick_patterns']. Never raises -- see module
    docstring."""
    if not CANDLESTICK_PATTERNS_ENABLED:
        decision_object["candlestick_patterns"] = _disabled_result(
            "Candlestick Patterns disabled (config.CANDLESTICK_PATTERNS_ENABLED = False)"
        )
        return

    try:
        candlestick_read = trade.get("candlestick_patterns")
        if not candlestick_read or not candlestick_read.get("enabled"):
            decision_object["candlestick_patterns"] = _disabled_result(
                candlestick_read.get("reason", "no candlestick read attached to this trade")
                if candlestick_read else "no candlestick read attached to this trade"
            )
            return

        decision_object["candlestick_patterns"] = dict(candlestick_read)

        if CANDLESTICK_PATTERNS_INFLUENCE_ENABLED:
            _apply_influence(decision_object, candlestick_read)

        if candlestick_read.get("patterns_detected"):
            _logger.info(
                f"CANDLESTICK_PATTERNS {trade.get('symbol')} {trade.get('direction')}: "
                f"patterns={candlestick_read['patterns_detected']} "
                f"dominant={candlestick_read.get('dominant_pattern')} "
                f"alignment={candlestick_read.get('alignment_score_pct')}"
            )

    except Exception as exc:  # noqa: BLE001 - never let this optional add-on break the Decision Object
        _logger.error(
            f"CANDLESTICK_PATTERNS_ATTACH_FAILED {trade.get('symbol')} {trade.get('direction')}: "
            f"{type(exc).__name__}: {exc}", exc_info=True,
        )
        decision_object["candlestick_patterns"] = {
            "enabled": False, "error": True, "reason": f"internal error, ignored: {exc}",
        }
