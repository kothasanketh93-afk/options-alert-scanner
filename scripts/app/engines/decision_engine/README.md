# Decision Engine (Part 6)

Centralized final-authority engine. Sits between the existing scoring
engines and the alert layer:

```
Market Snapshot -> Early Breakout -> Options Intelligence -> Decision Engine -> Alert
             (or)  Trade Score Engine ->
```

## Why this is additive, not a rewrite

Every sub-engine here reads fields the upstream engines **already
computed and attached** to the trade dict this cycle -- it never
re-fetches candles/option chains or re-runs scoring math. See
`base.py`'s module docstring and `adapters.py`.

## Entry point

```python
from app.engines.decision_engine import engine as decision_engine

decision = decision_engine.evaluate(trade, snapshot=snapshot)
trade["decision"] = decision
if config.DECISION_ENGINE_FILTER_ENABLED and not decision["approved"]:
    ...  # skip the alert, same rollout pattern as OPTIONS_INTELLIGENCE_FILTER_ENABLED
```

`main.py` already does exactly this (`apply_decision_engine()`), called
right before both `NEW_TRADE_ALERT` sites (plain scanner and Early
Breakout paths).

## Rollout flags (config.py, "Part 6" section)

- `DECISION_ENGINE_ENABLED` (default `True`) -- master switch. `False`
  fully restores pre-Part-6 behavior (passthrough object, always
  approved).
- `DECISION_ENGINE_FILTER_ENABLED` (default `False`) -- the Decision
  Object is *always* computed and attached for visibility/logging;
  this flag is the only thing that lets a rejection actually block an
  alert. Flip it on once you've watched the logs for a while.

## Files

| File | Engine(s) |
|---|---|
| `base.py` | Plugin contract + registry (Engine Discovery / Plugin Architecture) |
| `adapters.py` | Adapters over Market Snapshot, Early Breakout, Trade Score Engine, Options Intelligence |
| `confidence.py` | Confidence Engine |
| `probability.py` | Probability Engine |
| `risk.py` | Risk Engine, Risk/Reward Engine, Position Size Engine |
| `entry_validation.py` | Entry Validation Engine |
| `stop_loss.py` | Stop Loss Engine |
| `target.py` | Target Engine (Target 3, advisory) |
| `trade_quality.py` | Trade Quality Engine + Alert Priority Engine |
| `explanation.py` | Trade Explanation Engine + Trade Rejection Engine |
| `engine.py` | Orchestrator -- `evaluate()`, `build_registry()` |

## Adding a future engine (Institutional Intelligence, Trading
Intelligence, Replay, AI Prediction, ...)

1. Write one adapter class in `adapters.py` subclassing
   `IntelligencePlugin`, with an `is_available()` that checks for
   whatever field/module that future engine would attach.
2. Register it in `engine.py`'s `build_registry()`.

Nothing else changes -- `PluginRegistry.discover()` already handles
"ignore unavailable optional engines" and confidence/quality scoring
already renormalize automatically over whichever plugins are present.

## What the Decision Object contains

`decision`, `approved`, `trade_quality_score`, `confidence_score`,
`probability` (win/loss/success/historical-similarity), `risk`,
`risk_reward`, `entry`/`stop_loss`/`target_1`/`target_2`/`target_3`,
`position_size`, `entry_validation`, `priority`
(Very High/High/Medium/Low/Rejected), `explanation`,
`rejection_reasons`, `warnings`, `engines_used`/`engines_available`,
`execution_time_ms`, `timestamp`.

## Tests

```
python scripts/app/engines/decision_engine/tests/test_decision_engine.py
```

Covers plugin discovery, both trade shapes (Early Breakout / plain
scanner), approval and rejection paths, missing-engine edge cases, the
master on/off flag, and every position-size mode.

## Not built in this pass (and why)

Historical Similarity (Replay-based) stays `None` until a Replay
engine exists in this codebase -- there is nothing to read yet, and
inventing a fake similarity score would violate "never reference
modules that do not exist." The field and its plumbing are already in
place (`probability.py`) so a future Replay engine only needs to
attach `trade["replay_similarity_pct"]` for it to start flowing
through automatically.
