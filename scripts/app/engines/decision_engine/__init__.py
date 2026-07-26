"""
app/engines/decision_engine -- Part 6: Enterprise Decision Engine.
------------------------------------------------------------------------------
Centralized final-authority engine that sits between the existing
scoring engines (Market Snapshot, Early Breakout, Trade Score Engine,
Options Intelligence) and the alert layer. See engine.py's module
docstring for the integration snippet and README.md for the full
architecture writeup.
"""

from .engine import evaluate, build_registry

__all__ = ["evaluate", "build_registry"]
