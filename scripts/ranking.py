"""
ranking.py -- Cross-symbol opportunity ranking, split out of scanner.py.
---------------------------------------------------------------------------
Shared sort key and the flat best-to-worst ranking used both within a
symbol's own best-strike selection and across all symbols together.
"""


def _grade_rank_key(setup: dict) -> tuple:
    """
    Shared sort key (grade tier, then internal quality score) used by
    both rank_all() and the within-bucket best-strike selection in
    detect_setups() -- one definition of "what makes one candidate
    better than another" instead of two copies that could drift apart.
    """
    grade_order = {"Strong Entry": 2, "Entry": 1}
    return (grade_order.get(setup.get("grade"), 0), setup.get("_quality_score", 0))


def rank_all(candidates: list) -> list:
    """
    Returns ALL candidates sorted best-to-worst (grade, then internal
    quality score). main.py needs the full ranked list so it can fall
    through to the next-best candidate if the top one is suppressed
    (already has an open trade on that symbol+direction) rather than
    silently alerting nothing.
    """
    return sorted(candidates, key=_grade_rank_key, reverse=True)
