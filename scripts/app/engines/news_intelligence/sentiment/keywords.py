"""
app/engines/news_intelligence/sentiment/keywords.py -- the local sentiment
lexicon.
------------------------------------------------------------------------------
Pure Python dict, no NLTK/VADER/TextBlob dependency -- keeps this package's
"only free Python libraries, no cloud AI" requirement trivially true (it
doesn't even need a free THIRD-PARTY library, just stdlib + this lexicon).
Weights are hand-tuned for Indian financial/market-news vocabulary
specifically (NSE/BSE announcement language, RBI/SEBI release language),
not general-purpose sentiment -- "beats estimates" and "circuit breaker"
mean something more specific here than in general text.

Each entry: phrase -> weight in [-1.0, 1.0]. Multi-word phrases are
matched before single words so "misses estimates" (negative) isn't
partially matched by a hypothetical positive "estimates" entry.
"""

POSITIVE_KEYWORDS: dict[str, float] = {
    "beats estimates": 0.7, "beat estimates": 0.7, "record profit": 0.8,
    "record revenue": 0.75, "strong growth": 0.65, "robust growth": 0.65,
    "outperform": 0.6, "upgrade": 0.55, "upgraded": 0.55, "raises guidance": 0.7,
    "wins order": 0.6, "bags order": 0.6, "secures contract": 0.55,
    "expansion plan": 0.4, "capacity expansion": 0.4, "dividend announced": 0.45,
    "bonus issue": 0.5, "stock split": 0.3, "buyback": 0.4,
    "net profit rises": 0.6, "profit jumps": 0.7, "profit surges": 0.75,
    "revenue growth": 0.5, "margin expansion": 0.5, "positive outlook": 0.5,
    "rate cut": 0.5, "repo rate cut": 0.55, "gdp growth": 0.4, "fii inflow": 0.45,
    "foreign investment": 0.35, "new partnership": 0.35, "strategic tie-up": 0.35,
    "approval granted": 0.4, "clears approval": 0.4, "successful launch": 0.4,
    "all-time high": 0.6, "52-week high": 0.5, "surge": 0.5, "rally": 0.45,
    "acquisition completed": 0.35, "merger approved": 0.35, "credit rating upgrade": 0.6,
}

NEGATIVE_KEYWORDS: dict[str, float] = {
    "misses estimates": -0.7, "below estimates": -0.65, "profit falls": -0.65,
    "profit declines": -0.65, "profit plunges": -0.8, "net loss": -0.75,
    "widening loss": -0.6, "downgrade": -0.55, "downgraded": -0.55,
    "cuts guidance": -0.7, "lowers guidance": -0.65, "job cuts": -0.55,
    "layoffs": -0.6, "resignation": -0.4, "resigns": -0.4, "steps down": -0.35,
    "investigation": -0.6, "probe": -0.55, "penalty": -0.6, "fined": -0.6,
    "show cause notice": -0.55, "litigation": -0.45, "lawsuit": -0.5,
    "fraud": -0.85, "scam": -0.85, "default": -0.7, "debt restructuring": -0.5,
    "credit rating downgrade": -0.7, "rating cut": -0.6, "bankruptcy": -0.9,
    "insolvency": -0.85, "delisting": -0.6, "suspension of trading": -0.65,
    "circuit breaker": -0.4, "lower circuit": -0.55, "stock crashes": -0.75,
    "sell-off": -0.5, "sharp decline": -0.55, "plunges": -0.7, "tumbles": -0.6,
    "rate hike": -0.35, "repo rate hike": -0.4, "inflation surge": -0.45,
    "recall": -0.45, "product recall": -0.5, "regulatory action": -0.45,
    "ban": -0.6, "banned": -0.6, "strike": -0.4, "labour unrest": -0.45,
}

# Words that flip the polarity of whatever follows within a short window
# (e.g. "not profitable", "no growth") -- a lightweight negation handler,
# not a full dependency parse.
NEGATION_WORDS = {"not", "no", "never", "without", "fails to", "unable to"}

INTENSIFIERS: dict[str, float] = {
    "significantly": 1.3, "sharply": 1.3, "massively": 1.4, "substantially": 1.25,
    "slightly": 0.6, "marginally": 0.55, "modestly": 0.7,
}
