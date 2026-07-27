"""
scripts/production
-------------------
Part 9 production-readiness additions, kept in their own package so
that nothing in the existing scanner/decision-engine/replay/analytics/
dashboard code has to be touched to get them. Everything here is
additive and opt-in:

- logging_manager.py   -> centralized, rotating, per-channel logging
- retry_manager.py     -> retry + circuit breaker on top of retry_utils.py
- shutdown_manager.py  -> coordinated graceful shutdown

Existing modules (logging_setup.py, retry_utils.py, exceptions.py,
config_validation.py, cache.py) are NOT replaced. Callers that already
work continue to work unmodified. New code (or existing code, if you
choose to migrate it later) can opt into these by importing from here.
"""
