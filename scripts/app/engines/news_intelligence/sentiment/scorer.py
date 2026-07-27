"""
app/engines/news_intelligence/sentiment/scorer.py -- pure scoring math
over already-tokenized/lower-cased text. No I/O, no NewsItem awareness --
analyzer.py owns the NewsItem-facing API, this module is the testable
math core (same split as options_intelligence's chain_metrics.py vs
engine.py).
"""

from __future__ import annotations

from . import keywords as kw


def _find_phrase_hits(text: str, lexicon: dict[str, float]) -> list[tuple[str, float]]:
    hits = []
    for phrase, weight in lexicon.items():
        if phrase in text:
            # crude negation check: any negation word within 4 words
            # immediately before the phrase flips its sign and dampens it.
            idx = text.find(phrase)
            preceding = text[max(0, idx - 40):idx]
            negated = any(neg in preceding.split()[-4:] for neg in kw.NEGATION_WORDS)
            effective_weight = -weight * 0.6 if negated else weight
            # intensifier check: word immediately preceding the phrase
            for intensifier, multiplier in kw.INTENSIFIERS.items():
                if intensifier in preceding.split()[-3:]:
                    effective_weight *= multiplier
                    break
            hits.append((phrase, effective_weight))
    return hits


def raw_score(text: str) -> tuple[float, list[str], list[str]]:
    """Returns (raw_score in roughly [-1, 1], positive_hit_phrases,
    negative_hit_phrases). raw_score is the mean of all hit weights
    (0.0 with no hits -- neutral, not "positive because nothing bad
    found")."""
    lowered = text.lower()
    pos_hits = _find_phrase_hits(lowered, kw.POSITIVE_KEYWORDS)
    neg_hits = _find_phrase_hits(lowered, kw.NEGATIVE_KEYWORDS)
    all_hits = pos_hits + neg_hits

    if not all_hits:
        return 0.0, [], []

    total = sum(w for _, w in all_hits)
    score = max(-1.0, min(1.0, total / len(all_hits)))
    positive_phrases = [p for p, w in pos_hits if w > 0] + [p for p, w in neg_hits if w > 0]
    negative_phrases = [p for p, w in neg_hits if w < 0] + [p for p, w in pos_hits if w < 0]
    return score, positive_phrases, negative_phrases


def confidence_from_hits(hit_count: int, text_len: int) -> float:
    """More matched sentiment phrases (up to a point) and a reasonably-
    sized text both raise confidence; a single hit in a very long article
    is a weaker signal than two hits in a short headline+summary pair."""
    if hit_count == 0:
        return 0.15  # neutral-by-absence is a real signal, but a weak one
    density_component = min(1.0, hit_count / 4.0)
    length_component = 1.0 if 20 <= text_len <= 1500 else 0.7
    return round(min(1.0, 0.4 + 0.5 * density_component) * length_component, 3)
