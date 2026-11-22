"""Invisible characters out of outsider-written text.

Text other people wrote (other players' names, KB statements a model wrote
from web pages, the pages themselves, news) reaches the player's assistant
through the MCP's answers, and the KB refresh's model through the fetch
layer. Characters that render as nothing can carry text the player never
sees: the Unicode Tags block spells ASCII invisibly (an "ignore your
instructions" no one can read on screen), bidi controls reorder what is
shown against what is read, and zero-width characters split a word so a
pattern check misses it. The owner's ruling (2026-11-22, after an MCP
audit): strip them wherever outsider text leaves the server or reaches a
model. Applied at the MCP's one output boundary (wowstats.mcp.tools.call)
and in the KB fetch layer (tools/kb/web.text_of).

The zero-width joiner is the one kept, and only between two emoji: it is
what glues a family emoji or the rainbow flag together. Stdlib only.
"""

from __future__ import annotations

import re

# Always removed: the Tags block (U+E0000-E007F), the bidi embeddings,
# overrides and isolates and the marks (LRM, RLM, ALM), the zero-width
# space, non-joiner, word joiner and the BOM / zero-width no-break space.
_ALWAYS = re.compile("[\U000E0000-\U000E007F\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c"
                     "\u200b\u200c\u2060\ufeff]")
ZWJ = "\u200d"
_ANY = re.compile(_ALWAYS.pattern[:-1] + ZWJ + "]")
# What may sit between an emoji and the ZWJ after it: the emoji
# presentation selector (the heart on fire is U+2764 U+FE0F U+200D U+1F525).
_SELECTORS = "\ufe0e\ufe0f"
# Emoji code points, wide on purpose (a few symbols that aren't emoji in
# every font): the cost of a kept ZWJ between two symbols is nil.
_EMOJI = ((0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122),
          (0x2139, 0x2139), (0x2194, 0x21AA), (0x231A, 0x23FF), (0x24C2, 0x24C2), (0x25AA, 0x25FE),
          (0x2600, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2BFF), (0x3030, 0x3030), (0x303D, 0x303D),
          (0x3297, 0x3299), (0x1F000, 0x1FAFF))


def is_emoji(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in _EMOJI)


def _joins_emoji(t: str, i: int) -> bool:
    """Whether the ZWJ at t[i] sits between two emoji."""
    j = i - 1
    while j >= 0 and t[j] in _SELECTORS:
        j -= 1
    return j >= 0 and i + 1 < len(t) and is_emoji(t[j]) and is_emoji(t[i + 1])


def clean(text: str) -> tuple[str, int]:
    """(the text without its invisible characters, how many were removed)."""
    if not text or not _ANY.search(text):
        return text, 0
    t, n = _ALWAYS.subn("", text)
    if ZWJ not in t:
        return t, n
    out = []
    for i, ch in enumerate(t):
        if ch == ZWJ and not _joins_emoji(t, i):
            n += 1
            continue
        out.append(ch)
    return "".join(out), n


def clean_tree(v):
    """(v with every string in it cleaned, dict keys too, how many characters
    were removed in all): a tool's answer as it will be serialised."""
    if isinstance(v, str):
        return clean(v)
    if isinstance(v, dict):
        n, out = 0, {}
        for k, x in v.items():
            k2, a = clean(k) if isinstance(k, str) else (k, 0)
            x2, b = clean_tree(x)
            out[k2] = x2
            n += a + b
        return (out, n) if n else (v, 0)
    if isinstance(v, (list, tuple)):
        n, items = 0, []
        for x in v:
            x2, b = clean_tree(x)
            items.append(x2)
            n += b
        return ((items if isinstance(v, list) else tuple(items)), n) if n else (v, 0)
    return v, 0
