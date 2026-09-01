from __future__ import annotations

import re
from typing import Iterable, TypeVar, Callable

T = TypeVar("T")


def fuzzy_score(query: str, text: str) -> float | None:
    """Return a small-is-good subsequence score, or None if it does not match.

    This intentionally follows the useful part of fzf's behaviour: characters may
    have gaps, so ``dcker`` matches ``docker``. It is used for non-interactive
    search; the interactive UI delegates ranking to fzf itself.
    """
    query = query.casefold().strip()
    text_folded = text.casefold()
    if not query:
        return 0.0

    # Treat whitespace-separated query parts independently, while preserving
    # subsequence matching inside each part.
    parts = [part for part in re.split(r"\s+", query) if part]
    total = 0.0
    search_start = 0

    for part in parts:
        positions: list[int] = []
        cursor = search_start
        for char in part:
            found = text_folded.find(char, cursor)
            if found < 0:
                # A later query word may occur before the previous one; retry
                # independently before declaring failure.
                positions = []
                cursor = 0
                for retry_char in part:
                    retry_found = text_folded.find(retry_char, cursor)
                    if retry_found < 0:
                        return None
                    positions.append(retry_found)
                    cursor = retry_found + 1
                break
            positions.append(found)
            cursor = found + 1

        if not positions:
            return None

        gaps = sum(max(0, b - a - 1) for a, b in zip(positions, positions[1:]))
        span = positions[-1] - positions[0] + 1
        start_penalty = positions[0] * 0.08
        boundary_bonus = 0.0
        first = positions[0]
        if first == 0 or text_folded[first - 1] in " /_-.:":
            boundary_bonus = -1.5
        contiguous_bonus = -2.0 if gaps == 0 else 0.0
        total += gaps * 1.5 + span * 0.08 + start_penalty + boundary_bonus + contiguous_bonus
        search_start = positions[-1] + 1

    total += len(text_folded) * 0.002
    return total


def fuzzy_filter(
    query: str,
    items: Iterable[T],
    text: Callable[[T], str],
    limit: int = 50,
) -> list[T]:
    if limit < 0:
        raise ValueError("limit must not be negative")
    scored: list[tuple[float, int, T]] = []
    for index, item in enumerate(items):
        score = fuzzy_score(query, text(item))
        if score is not None:
            scored.append((score, index, item))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item for _, _, item in scored[:limit]]
