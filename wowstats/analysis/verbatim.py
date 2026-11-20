"""How much of a source a KB text repeats word for word.

The knowledge base takes facts from guides, news and forum posts, never their
wording: a statement says what the source says in our own words, or as an
aggregate, with the link (docs/kb.md, "Sources and licensing"). The model
passes (tools/kb/refresh.py, tools/kb/meta.py) check every proposal against
the text they just read; tests/test_kb_verbatim.py checks the shipped files.

Two measures, on lowercased words:

- the longest run of consecutive words the text shares with the source:
  RUN or more is a copied sentence, whatever its length;
- the share of the text's 8-word shingles found in the source: above SHARE,
  the text is a patchwork of the source's phrases even when no single run is
  long.

Stdlib only: the KB tools run without the project's dependencies.
"""

from __future__ import annotations

import re

RUN = 12  # consecutive words shared with the source: a copy
SHINGLE = 8  # words per shingle
SHARE = 0.20  # share of the text's shingles found in the source: a copy
QUOTE_MAX = 15  # words a KB text may quote between quotation marks

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
_QUOTED = re.compile(r'"([^"]+)"|“([^“”]+)”')


def words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower().replace("’", "'"))


def _grams(w: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def longest_run(text: str, source: str) -> tuple[int, str]:
    """The longest run of consecutive words of `text` that `source` also has,
    as (length, the run)."""
    a, b = words(text), words(source)
    if not a or not b:
        return 0, ""
    # Positions of each word in the source, then the classic longest common
    # substring over words, but only along the source positions a word has.
    at: dict[str, list[int]] = {}
    for j, w in enumerate(b):
        at.setdefault(w, []).append(j)
    best, end = 0, 0
    prev: dict[int, int] = {}
    for i, w in enumerate(a):
        cur = {}
        for j in at.get(w, ()):
            n = prev.get(j - 1, 0) + 1
            cur[j] = n
            if n > best:
                best, end = n, i + 1
        prev = cur
    return best, " ".join(a[end - best:end])


def shingle_share(text: str, source: str) -> float:
    """The share of the text's 8-word shingles the source holds (0 for a text
    shorter than one shingle)."""
    mine = _grams(words(text), SHINGLE)
    if not mine:
        return 0.0
    return len(mine & _grams(words(source), SHINGLE)) / len(mine)


def copied(text: str, source: str, run: int = RUN, share: float = SHARE) -> str:
    """Why the text copies the source, or "" when it doesn't. The reason
    names the measure, never the copied words (it lands in a KB file)."""
    n, _ = longest_run(text, source)
    if n >= run:
        return f"repeats {n} consecutive words of its source"
    s = shingle_share(text, source)
    if s > share:
        return f"shares {s:.0%} of its 8-word phrases with its source"
    return ""


def quoted_runs(text: str) -> list[str]:
    """Every passage of the text between quotation marks ("..." or curly
    quotes)."""
    return [next(g for g in m.groups() if g) for m in _QUOTED.finditer(str(text or ""))]
