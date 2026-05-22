"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re
from typing import Tuple


# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Matches "Rating: X" / "rating - X" / "Rating: **X**" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
# Also matches Chinese labels: "评级：X" / "评级: X"
_RATING_LABEL_RE = re.compile(
    r"(?:rating|评级).*?[:\-：][\s*]*(\w+)", re.IGNORECASE
)

# Matches title/header patterns like "决策：**Underweight**" or "## X: **Rating**"
_TITLE_RATING_RE = re.compile(
    r"(?:决策|decision|结论).*?[:\-：][\s*]*(\w+)", re.IGNORECASE
)


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Multi-pass strategy:
    1. Look for an explicit "Rating: X" or "评级：X" label.
    2. Look for title/decision patterns like "决策：X".
    3. Fall back to the first 5-tier rating word in the first 5 lines only
       (avoids false positives from body text discussing other ratings).

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    # Pass 1: explicit rating label
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    # Pass 2: title/decision pattern
    for line in text.splitlines():
        m = _TITLE_RATING_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    # Pass 3: first rating word in the first 5 lines (title area only)
    for line in text.splitlines()[:5]:
        for word in line.lower().split():
            clean = word.strip("*:.,;!?\"'`#>")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default
