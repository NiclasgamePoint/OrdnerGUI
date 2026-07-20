from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def normalize_search_text(value: str) -> str:
    """Return comparable words without case, accents, or punctuation."""
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_marks = "".join(char for char in folded if not unicodedata.combining(char))
    return " ".join(_WORD_RE.findall(without_marks))


def search_tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize_search_text(value).split()))


@dataclass(frozen=True)
class SearchField:
    text: str
    weight: float = 1.0


def _token_similarity(query: str, candidate: str) -> float:
    if query == candidate:
        return 1.0
    shorter, longer = sorted((query, candidate), key=len)
    if len(shorter) >= 2 and longer.startswith(shorter):
        return 0.94
    if len(shorter) >= 3 and shorter in longer:
        # A contained name fragment such as "horst" in "wahnhorst" is strong.
        return 0.84 + 0.12 * (len(shorter) / len(longer))
    if min(len(query), len(candidate)) < 3:
        return 0.0
    ratio = SequenceMatcher(None, query, candidate, autojunk=False).ratio()
    # Reject weak look-alikes before field weights can promote them to matches.
    return ratio if ratio >= 0.74 else 0.0


def fuzzy_record_score(query: str, fields: list[SearchField]) -> float | None:
    """Score a record by mapping every query word to its best searchable field."""
    queries = search_tokens(query)
    if not queries:
        return None

    prepared = [
        (search_tokens(field.text), max(0.1, field.weight), normalize_search_text(field.text))
        for field in fields
        if str(field.text or "").strip()
    ]
    if not prepared:
        return None

    token_scores: list[float] = []
    for query_token in queries:
        best = 0.0
        for candidates, weight, _normalized in prepared:
            for candidate in candidates:
                best = max(best, min(1.12, _token_similarity(query_token, candidate) * weight))
        token_scores.append(best)

    strongest = max(token_scores)
    if strongest < 0.72:
        return None
    coverage = sum(score >= 0.72 for score in token_scores) / len(token_scores)
    average = sum(token_scores) / len(token_scores)
    score = average * 0.65 + coverage * 0.25 + strongest * 0.10

    normalized_query = normalize_search_text(query)
    if any(normalized_query == normalized for _, _, normalized in prepared):
        score += 0.18
    elif any(normalized_query in normalized for _, _, normalized in prepared):
        score += 0.08
    return score
