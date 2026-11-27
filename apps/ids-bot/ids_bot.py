"""The IDS bot: every ideographic description in the community chat gets its IDS Workshop page.

The bot is the Matrix account @idsbot:glyphatlas.org. It joins every public room of its homeserver
and follows the homeserver's sync stream; a message holding a description (⿱⿻韦丷吋) is
answered with one https://ids.mkpo.li/<description> line per description. Edits, its own messages and
messages that already link the Workshop are left alone.

The file uses the standard library only, so a copy of it runs on the chat server without the
project's environment:

    python3 ids_bot.py --token-file /etc/ids-bot/token --state /var/lib/ids-bot/state.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from pathlib import Path
from typing import Any

WORKSHOP = "https://ids.mkpo.li/"
# Cloudflare in front of the homeserver refuses the default urllib agent, so the bot names itself.
USER_AGENT = "glyph-atlas-ids-bot/1 (+https://glyphatlas.org)"
HOMESERVER = "https://matrix.glyphatlas.org"

# The operators the Workshop reads, each with how many descriptions it takes (its `OPERATORS`).
OPERATORS = {
    "⿰": 2, "⿱": 2, "⿲": 3, "⿳": 3, "⿴": 2, "⿵": 2, "⿶": 2, "⿷": 2, "⿸": 2, "⿹": 2,
    "⿺": 2, "⿻": 2, "⿼": 2, "⿽": 2, "⿾": 1, "⿿": 1, "㇯": 2, "〾": 1,
}
EMPTY = frozenset("⬚＋")
# Characters that would end or cut the destination path, so a link carrying them could not open. The
# Workshop reads them as components in its editor; the bot leaves descriptions holding them alone.
UNSAFE = frozenset("./?#%")
# The operators a zi.tools modifier may follow (its `MODIFIABLE`), and the characters one holds.
MODIFIABLE = frozenset("⿻⿴⿵⿶⿷⿼㇯")
MODIFIER = re.compile(r"[0-9:,._xabcdlr|-]+")
# The longest description the Workshop serves by path (worker/page.js caps paths at 200);
# a longer one is read only to find where it ends, so the bot stays silent instead of linking its fragments.
LONGEST = 200
LONGEST_READ = 8192
CUSTOM_LONGEST = 48
TONES = frozenset("\u302e\u302f")
CUSTOM_FORMS = (
    ("{", "}", re.compile(r"[0-9]{1,4}")),
    ("&", ";", re.compile(r"[A-Za-z][A-Za-z0-9+_-]*")),
    ("#(", ")", re.compile(r"[^\s()\[\]{}#&;]+")),
)
# Default ignorables han-compose reads neither as a component nor as a mark: the combining grapheme
# joiner, the Khmer inherent vowels, the Mongolian selectors and the Hangul fillers.
IGNORABLE = frozenset("\u034f\u17b4\u17b5\u180b\u180c\u180d\u180e\u180f\u115f\u1160\u3164\uffa0")
# What stays in a grapheme cluster although its category is no mark: ZWNJ, the halfwidth kana voicing
# marks (Extend) and the Thai and Lao AM vowels (SpacingMark).
CLUSTER_EXTRA = frozenset("\u200c\uff9e\uff9f\u0e33\u0eb3")
# The loose marks of han-compose's data/unicode/properties.json (Unicode 18.0.0) that Python 3.12's
# Unicode 15.0 database does not know.
EXTRA_MARK_RANGES = (
    (0x558, 0x558), (0x58B, 0x58C), (0x5C8, 0x5C9), (0x897, 0x897), (0xB53, 0xB54), (0x1ACF, 0x1AF0),
    (0x208F, 0x208F), (0x209D, 0x209F), (0xA7F1, 0xA7F1), (0x107BB, 0x107BF), (0x10D4E, 0x10D4E),
    (0x10D69, 0x10D6D), (0x10D6F, 0x10D6F), (0x10EC5, 0x10EC5), (0x10ECB, 0x10ECF), (0x10EF0, 0x10EFC),
    (0x113B8, 0x113C0), (0x113C2, 0x113C2), (0x113C5, 0x113C5), (0x113C7, 0x113CA), (0x113CC, 0x113D0),
    (0x113D2, 0x113D2), (0x113E1, 0x113E2), (0x11B60, 0x11B67), (0x11DD9, 0x11DD9), (0x11DF0, 0x11DF0),
    (0x11F5A, 0x11F5A), (0x1611E, 0x1612F), (0x16D40, 0x16D42), (0x16D6B, 0x16D6C), (0x16FF2, 0x16FF3),
    (0x1D127, 0x1D128), (0x1D250, 0x1D252), (0x1D25B, 0x1D25C), (0x1D25F, 0x1D25F), (0x1D280, 0x1D281),
    (0x1DFCD, 0x1DFFF), (0x1E5EE, 0x1E5EF), (0x1E6E3, 0x1E6E3), (0x1E6E6, 0x1E6E6), (0x1E6EE, 0x1E6EF),
    (0x1E6F5, 0x1E6F5), (0x1E6FF, 0x1E6FF),
)
EXTRA_MARK_ENDS = tuple(first for first, _ in EXTRA_MARK_RANGES)
# Extended_Pictographic (emoji-data.txt, the same in Unicode 15.1 and 17.0), so a symbol such as ⬚ or ㇀
# is not read as an Emoji.
EXTENDED_PICTOGRAPHIC_RANGES = (
    (0xA9, 0xA9), (0xAE, 0xAE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122), (0x2139, 0x2139),
    (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x2328, 0x2328), (0x23CF, 0x23CF),
    (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25AB), (0x25B6, 0x25B6),
    (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2600, 0x2604), (0x260E, 0x260E), (0x2611, 0x2611),
    (0x2614, 0x2615), (0x2618, 0x2618), (0x261D, 0x261D), (0x2620, 0x2620), (0x2622, 0x2623),
    (0x2626, 0x2626), (0x262A, 0x262A), (0x262E, 0x262F), (0x2638, 0x263A), (0x2640, 0x2640),
    (0x2642, 0x2642), (0x2648, 0x2653), (0x265F, 0x2660), (0x2663, 0x2663), (0x2665, 0x2666),
    (0x2668, 0x2668), (0x267B, 0x267B), (0x267E, 0x267F), (0x2692, 0x2697), (0x2699, 0x2699),
    (0x269B, 0x269C), (0x26A0, 0x26A1), (0x26A7, 0x26A7), (0x26AA, 0x26AB), (0x26B0, 0x26B1),
    (0x26BD, 0x26BE), (0x26C4, 0x26C5), (0x26C8, 0x26C8), (0x26CE, 0x26CF), (0x26D1, 0x26D1),
    (0x26D3, 0x26D4), (0x26E9, 0x26EA), (0x26F0, 0x26F5), (0x26F7, 0x26FA), (0x26FD, 0x26FD),
    (0x2702, 0x2702), (0x2705, 0x2705), (0x2708, 0x270D), (0x270F, 0x270F), (0x2712, 0x2712),
    (0x2714, 0x2714), (0x2716, 0x2716), (0x271D, 0x271D), (0x2721, 0x2721), (0x2728, 0x2728),
    (0x2733, 0x2734), (0x2744, 0x2744), (0x2747, 0x2747), (0x274C, 0x274C), (0x274E, 0x274E),
    (0x2753, 0x2755), (0x2757, 0x2757), (0x2763, 0x2764), (0x2795, 0x2797), (0x27A1, 0x27A1),
    (0x27B0, 0x27B0), (0x27BF, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50), (0x2B55, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297),
    (0x3299, 0x3299), (0x1F004, 0x1F004), (0x1F02C, 0x1F02F), (0x1F094, 0x1F09F), (0x1F0AF, 0x1F0B0),
    (0x1F0C0, 0x1F0C0), (0x1F0CF, 0x1F0D0), (0x1F0F6, 0x1F0FF), (0x1F170, 0x1F171), (0x1F17E, 0x1F17F),
    (0x1F18E, 0x1F18E), (0x1F191, 0x1F19A), (0x1F1AE, 0x1F1E5), (0x1F201, 0x1F20F), (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A), (0x1F23C, 0x1F23F), (0x1F249, 0x1F25F), (0x1F266, 0x1F321),
    (0x1F324, 0x1F393), (0x1F396, 0x1F397), (0x1F399, 0x1F39B), (0x1F39E, 0x1F3F0), (0x1F3F3, 0x1F3F5),
    (0x1F3F7, 0x1F3FA), (0x1F400, 0x1F4FD), (0x1F4FF, 0x1F53D), (0x1F549, 0x1F54E), (0x1F550, 0x1F567),
    (0x1F56F, 0x1F570), (0x1F573, 0x1F57A), (0x1F587, 0x1F587), (0x1F58A, 0x1F58D), (0x1F590, 0x1F590),
    (0x1F595, 0x1F596), (0x1F5A4, 0x1F5A5), (0x1F5A8, 0x1F5A8), (0x1F5B1, 0x1F5B2), (0x1F5BC, 0x1F5BC),
    (0x1F5C2, 0x1F5C4), (0x1F5D1, 0x1F5D3), (0x1F5DC, 0x1F5DE), (0x1F5E1, 0x1F5E1), (0x1F5E3, 0x1F5E3),
    (0x1F5E8, 0x1F5E8), (0x1F5EF, 0x1F5EF), (0x1F5F3, 0x1F5F3), (0x1F5FA, 0x1F64F), (0x1F680, 0x1F6C5),
    (0x1F6CB, 0x1F6D2), (0x1F6D5, 0x1F6E5), (0x1F6E9, 0x1F6E9), (0x1F6EB, 0x1F6F0), (0x1F6F3, 0x1F6FF),
    (0x1F7DA, 0x1F7FF), (0x1F80C, 0x1F80F), (0x1F848, 0x1F84F), (0x1F85A, 0x1F85F), (0x1F888, 0x1F88F),
    (0x1F8AE, 0x1F8AF), (0x1F8BC, 0x1F8BF), (0x1F8C2, 0x1F8CF), (0x1F8D9, 0x1F8FF), (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945), (0x1F947, 0x1F9FF), (0x1FA58, 0x1FA5F), (0x1FA6E, 0x1FAFF), (0x1FC00, 0x1FFFD),
)
EXTENDED_PICTOGRAPHIC_STARTS = tuple(first for first, _ in EXTENDED_PICTOGRAPHIC_RANGES)
JAMO_RANGES = (
    ("L", 0x1100, 0x115E), ("L", 0xA960, 0xA97C),
    ("V", 0x1161, 0x11A7), ("V", 0xD7B0, 0xD7C6),
    ("T", 0x11A8, 0x11FF), ("T", 0xD7CB, 0xD7FB),
)
KEYCAP_BASES = frozenset("0123456789#*")
KEYCAP = re.compile("[0-9#*]\ufe0f?\u20e3")
# The most long probes one message gets, so a run of operators with no description cannot stall the
# sync loop.
PROBES = 16
# A message longer than this is read only this far: chat messages need no more, and the scan stays bounded.
SCAN = 8192
# The most links one message gets; a wall of descriptions should not become a wall of replies.
MOST = 10
# How long /sync is asked to hold a request open, and how much longer the socket waits for the answer.
SYNC_TIMEOUT_MS = 30_000
SYNC_READ_TIMEOUT = SYNC_TIMEOUT_MS / 1000 + 15
# The timeline the bot reads; room state and ephemeral events are of no use to it.
FILTER = json.dumps({"room": {"timeline": {"limit": 50}, "state": {"limit": 0}, "ephemeral": {"limit": 0}}})
REFRESH_SECONDS = 3600

log = logging.getLogger("ids-bot")


def _selector(char: str) -> bool:
    point = ord(char)
    return 0xFE00 <= point <= 0xFE0F or 0xE0100 <= point <= 0xE01EF


def _in_ranges(point: int, ranges: tuple[tuple[int, int], ...], starts: tuple[int, ...]) -> bool:
    idx = bisect_right(starts, point) - 1
    return idx >= 0 and point <= ranges[idx][1]


def _is_mark(char: str, *, include_tones: bool = False) -> bool:
    """Whether `char` may trail a description as a loose mark (han-compose `is_mark` / `unicodeProperty`)."""
    point = ord(char)
    if (not include_tones and char in TONES) or char in IGNORABLE or _selector(char):
        return False
    if _in_ranges(point, EXTRA_MARK_RANGES, EXTRA_MARK_ENDS):
        return True
    category = unicodedata.category(char)
    if category.startswith("M") or category == "Lm":
        return True
    if category == "No":
        return point in (0xB2, 0xB3, 0xB9) or 0x2070 <= point <= 0x209F
    if category == "Sk":
        return (
            point in (0xA8, 0xAF, 0xB4, 0xB8)
            or 0x2D2 <= point <= 0x2DF
            or 0x2E5 <= point <= 0x2ED
            or 0x309B <= point <= 0x309C
            or 0xA700 <= point <= 0xA71F
        )
    return False


def _jamo_class(char: str) -> str | None:
    point = ord(char)
    return next((role for role, first, last in JAMO_RANGES if first <= point <= last), None)


def _jamo_run_end(text: str, at: int) -> tuple[int, int]:
    """`(cluster_end, initial_end)` for a conjoining jamo syllable (`L+ V+ T*`).

    `cluster_end` is 0 when no valid syllable starts at `text[at]`; `initial_end` is where the
    initial `L+` run ended so consecutive `L`s without a following `V` need not be re-scanned.
    """
    if at >= len(text) or _jamo_class(text[at]) != "L":
        return 0, 0
    end = at
    while end < len(text) and _jamo_class(text[end]) == "L":
        end += 1
    v_start = end
    while end < len(text) and _jamo_class(text[end]) == "V":
        end += 1
    if end == v_start:
        return 0, v_start
    while end < len(text) and _jamo_class(text[end]) == "T":
        end += 1
    return end, v_start


def _pictographic(char: str) -> bool:
    return _in_ranges(ord(char), EXTENDED_PICTOGRAPHIC_RANGES, EXTENDED_PICTOGRAPHIC_STARTS)


def _extends(char: str) -> bool:
    """Whether `char` stays in the grapheme cluster before it (Grapheme_Cluster_Break Extend or
    SpacingMark), approximated from the general category this Python knows."""
    point = ord(char)
    return (
        unicodedata.category(char) in ("Mn", "Me", "Mc")
        or char in CLUSTER_EXTRA
        or _selector(char)
        or 0x1F3FB <= point <= 0x1F3FF
        or 0xE0020 <= point <= 0xE007F
        or _in_ranges(point, EXTRA_MARK_RANGES, EXTRA_MARK_ENDS)
    )


def _emoji_end(text: str, at: int) -> int:
    """Where the Emoji sequence starting at `text[at]` ends, or 0 where none starts there.

    han-compose reads the first grapheme cluster (Intl.Segmenter) as one component when it starts with
    an Extended_Pictographic, a regional indicator or a keycap base, and holds an Emoji.
    """
    char = text[at]
    regional = 0x1F1E6 <= ord(char) <= 0x1F1FF
    if not (regional or char in KEYCAP_BASES or _pictographic(char)):
        return 0
    end = at + 1
    if regional and end < len(text) and 0x1F1E6 <= ord(text[end]) <= 0x1F1FF:
        end += 1
    # A ZWJ joins the next pictograph only after a pictograph and its Extend run (UAX #29 GB11).
    joinable = _pictographic(char)
    while end < len(text):
        nxt = text[end]
        if nxt == "\u200d":
            end += 1
            if joinable and end < len(text) and _pictographic(text[end]):
                end += 1
            else:
                joinable = False
            continue
        if not _extends(nxt):
            break
        joinable = joinable and unicodedata.category(nxt) != "Mc"
        end += 1
    cluster = text[at:end]
    if regional or _pictographic(char) or KEYCAP.match(cluster):
        return end
    return 0


def _custom_end(text: str, at: int) -> tuple[int, bool] | None:
    """`(end, is_chain)` for a `{n}`, `&entity;` or `#(chain)` component at `text[at]`, or `None`."""
    for opener, closer, pattern in CUSTOM_FORMS:
        if not text.startswith(opener, at):
            continue
        start = at + len(opener)
        close = text.find(closer, start, min(len(text), at + CUSTOM_LONGEST + 1))
        if close < 0:
            continue
        if pattern.fullmatch(text[start:close]):
            return close + len(closer), opener == "#("
    return None


def _tones_end(text: str, at: int) -> int:
    while at < len(text) and text[at] in TONES:
        at += 1
    return at


def _component(char: str) -> bool:
    """Whether the Workshop reads `char` as a component: one graphic character, no operator or slot.

    The Workshop draws mixed-script descriptions, so a component is any character a message can carry
    against the Unicode version this Python knows: an unassigned code point is read as a component the
    Workshop may know by a later version. A character that would break the answer's URL is refused.
    """
    if char in OPERATORS or char in EMPTY or char in UNSAFE or char in IGNORABLE or _selector(char):
        return False
    category = unicodedata.category(char)
    return not category.startswith("Z") and category not in ("Cc", "Cf", "Cs")


def _modifier_length(text: str, at: int) -> int:
    """How many characters the zi.tools modifier at `text[at]` takes, 0 where none starts there."""
    if at >= len(text) or text[at] != "[":
        return 0
    end = text.find("]", at + 1)
    if end < 0 or end - at > 65 or not MODIFIER.fullmatch(text[at + 1:end]):
        return 0
    return end - at + 1


def _end_of(text: str, at: int, longest: int, *, customs: bool) -> tuple[int, bool]:
    """Read one description under `customs`; answers `(end, overflowed_longest)`."""
    if at >= len(text) or text[at] not in OPERATORS:
        return -1, False
    start = at
    cap = start + longest
    limit = min(len(text), cap)
    skip_jamo_before = 0
    required = 1
    while required:
        if at >= limit:
            return -1, at >= cap
        char = text[at]
        arity = OPERATORS.get(char)
        if arity is not None:
            at += 1
            if char in MODIFIABLE:
                skip = _modifier_length(text, at)
                if at + skip > limit:
                    return -1, at + skip > cap
                at += skip
            required += arity - 1
            continue
        if customs:
            custom = _custom_end(text, at)
            if custom is not None:
                at, is_chain = custom
                if is_chain:
                    if at < len(text) and _selector(text[at]):
                        at += 1
                    at = _tones_end(text, at)
                if at > limit:
                    return -1, True
                required -= 1
                continue
        emoji = _emoji_end(text, at)
        if emoji:
            at = _tones_end(text, emoji)
            if at > limit:
                return -1, True
            required -= 1
            continue
        if at >= skip_jamo_before:
            jamo, l_end = _jamo_run_end(text, at)
            if jamo:
                at = _tones_end(text, jamo)
                if at > limit:
                    return -1, True
                required -= 1
                continue
            skip_jamo_before = max(skip_jamo_before, l_end)
        if not _component(char):
            return -1, False
        at += 1
        if at < len(text) and _selector(text[at]):
            at += 1
        at = _tones_end(text, at)
        if at > limit:
            return -1, True
        required -= 1
    while at < len(text) and _is_mark(text[at], include_tones=True):
        at += 1
        if at > limit:
            return -1, True
    return (at, False) if at > start else (-1, False)


def description_end(text: str, at: int = 0, longest: int = LONGEST) -> int:
    """Where the description starting at `text[at]` ends, or -1 when none well formed starts there.

    Matches han-compose's grammar: operators with modifiers, custom components (with literal fallback),
    multi-codepoint Emoji runs, conjoining Hangul jamo clusters, Middle Korean tone marks, and
    trailing loose marks.
    """
    # Nothing past the longest description and the character after it can change the answer, so a long
    # run of marks or jamo is read once per message rather than once per operator before it.
    window = text[: at + longest + 1]
    end, overflow = _end_of(window, at, longest, customs=True)
    if end >= 0 or overflow:
        return end
    return _end_of(window, at, longest, customs=False)[0]


def _edge(char: str) -> bool:
    """Whether `char` separates a description from prose: a description never ends against a letter,
    a digit, a mark or an unassigned script code point (用⿰表示)."""
    category = unicodedata.category(char)
    return (
        category[0] not in ("L", "N", "M")
        and (category != "Cn" or _pictographic(char))
        and not _is_mark(char, include_tones=True)
    )


def _delimited(text: str, at: int, end: int, after: int = -1) -> bool:
    """Whether a description between `at` and `end` stands apart: at the start, beside a break, or
    directly after `after`, the end of the description before it (⿰木木⿰日曰)."""
    return (at == 0 or at == after or _edge(text[at - 1])) and (end >= len(text) or _edge(text[end]))


def sequences(text: str) -> list[str]:
    """The descriptions in `text`, in reading order and once each.

    A description is read from its operator to the exact end its operators ask for, so the prose after
    it is not taken for components. It must stand as a word of its own, between the start or a space or
    punctuation and the end or another such break: 用⿰表示 names no description, while ⿰木木、是林
    names ⿰木木. A text may carry several (⿱⿻韦丷吋 ⿳⿻韦丷一吋). A description too long for the
    Workshop's ordinary parser is skipped whole, so none of its parts is linked on its own.
    """
    found: list[str] = []
    seen: set[str] = set()
    at = 0
    after = -1
    probes = 0
    limit = min(len(text), SCAN)
    while at < limit:
        if text[at] in OPERATORS:
            end = description_end(text, at)
            if end > 0 and _delimited(text, at, end, after):
                sequence = text[at:end]
                if UNSAFE.isdisjoint(sequence) and sequence not in seen:
                    seen.add(sequence)
                    found.append(sequence)
                after = end
                at = end
                continue
            if probes < PROBES:
                probes += 1
                long_end = description_end(text, at, LONGEST_READ)
                if long_end > at + LONGEST:
                    at = long_end
                    continue
            # An operator run belongs to one description, even when it is incomplete.
            while at + 1 < limit and text[at + 1] in OPERATORS:
                at += 1
        at += 1
    return found


def _unquoted(body: str) -> str:
    """The part of a message below Matrix's quoted reply fallback, the `> …` lines it starts with."""
    lines = body.splitlines()
    while lines and lines[0].lstrip().startswith(">"):
        lines.pop(0)
    return "\n".join(lines).strip()


def wanted(event: dict[str, Any], user_id: str) -> list[str]:
    """The descriptions in a message that deserve a reply, or none for a message the bot leaves alone."""
    if event.get("type") != "m.room.message" or event.get("sender") == user_id:
        return []
    content = event.get("content")
    if not isinstance(content, dict) or content.get("msgtype") not in ("m.text", "m.emote"):
        return []
    relates = content.get("m.relates_to")
    if content.get("m.new_content") or (isinstance(relates, dict) and relates.get("rel_type") == "m.replace"):
        return []
    body = content.get("body")
    if not isinstance(body, str):
        return []
    body = _unquoted(body)
    if not body or "ids.mkpo.li" in body:
        return []
    return sequences(body)[:MOST]


def link(sequence: str) -> str:
    """The Workshop page a description opens, as written in a message."""
    return WORKSHOP + sequence


def reply_content(descriptions: list[str], event_id: str) -> dict[str, Any]:
    """The message that answers `event_id` with one page per description."""
    body = "\n".join(link(sequence) for sequence in descriptions)
    formatted = "<br />".join(
        f'<a href="{WORKSHOP}{urllib.parse.quote(sequence, safe="")}">{html.escape(link(sequence))}</a>'
        for sequence in descriptions
    )
    return {
        "msgtype": "m.text",
        "body": body,
        "format": "org.matrix.custom.html",
        "formatted_body": formatted,
        "m.relates_to": {"rel_type": "m.in_reply_to", "event_id": event_id},
    }


def transaction_id(event_id: str) -> str:
    """The send transaction that answers `event_id`: the same answer is sent once, even across restarts."""
    return "ids-" + hashlib.sha256(event_id.encode()).hexdigest()[:48]


class MatrixError(Exception):
    """An answer from the homeserver that is not the one asked for."""

    def __init__(self, status: int | None, message: str = "", retry_after_ms: int | None = None,
                 code: str | None = None):
        super().__init__(f"{status}: {message}" if status is not None else message)
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.code = code


class Matrix:
    """The handful of client API calls the bot makes, over the standard library."""

    def __init__(self, homeserver: str = HOMESERVER, token: str = "", timeout: float = 45.0):
        self.homeserver = homeserver.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method: str, path: str, *, params: dict[str, str] | None = None,
             body: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        url = self.homeserver + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(error.read() or b"{}")
            except ValueError:
                pass
            retry = payload.get("retry_after_ms")
            code = payload.get("errcode")
            raise MatrixError(error.code, str(payload.get("error") or error.reason),
                              retry if isinstance(retry, int) else None,
                              code if isinstance(code, str) else None) from None
        except ValueError as error:
            raise MatrixError(None, f"the homeserver answered with JSON that could not be read: {error}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise MatrixError(None, str(getattr(error, "reason", error))) from None


class State:
    """The sync token to resume from, kept in a file so a restart does not replay the rooms."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None

    def load(self) -> str | None:
        if self.path is None:
            return None
        try:
            token = json.loads(self.path.read_text(encoding="utf-8")).get("since")
        except (OSError, ValueError, AttributeError):
            return None
        return token if isinstance(token, str) else None

    def save(self, since: str) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"since": since}), encoding="utf-8")
        os.replace(temporary, self.path)

    def forget(self) -> None:
        """Drop the saved token, after the homeserver no longer knows it."""
        if self.path is not None:
            self.path.unlink(missing_ok=True)


def _room(room_id: str) -> str:
    return urllib.parse.quote(room_id, safe="")


class Bot:
    """The account, the rooms it answers in, and the sync loop that carries their messages to it."""

    def __init__(self, matrix: Matrix, state: State, *, refresh_seconds: float = REFRESH_SECONDS):
        self.matrix = matrix
        self.state = state
        self.refresh_seconds = refresh_seconds
        self.user_id = ""
        self._joined_at = 0.0

    def run(self) -> None:
        self.user_id = self.matrix.call("GET", "/_matrix/client/v3/account/whoami")["user_id"]
        log.info("signed in as %s", self.user_id)
        self.join_public_rooms()
        since = self.state.load()
        if since is None:
            since = self.initial_sync()
        wait = 1
        while True:
            try:
                since = self.cycle(since)
                wait = 1
            except MatrixError as error:
                if error.status in (401, 403):
                    raise
                if error.status == 400 and error.code in ("M_UNKNOWN_POS", "M_UNKNOWN"):
                    log.warning("sync position refused: %s; reading the rooms again", error)
                    self.state.forget()
                    since = self.initial_sync()
                    wait = 1
                    continue
                log.warning("sync: %s", error)
                time.sleep(wait)
                wait = min(wait * 2, 60)
            if time.monotonic() - self._joined_at >= self.refresh_seconds:
                self.join_public_rooms()

    def initial_sync(self) -> str:
        """Read the rooms once without answering them, and keep the token the next sync resumes from."""
        response = self.matrix.call("GET", "/_matrix/client/v3/sync", params={"timeout": "0", "filter": FILTER},
                                    timeout=60)
        since = self._token(response)
        self.state.save(since)
        return since

    def cycle(self, since: str) -> str:
        """Wait for the next events, answer them, and keep the token the next cycle resumes from."""
        response = self.matrix.call(
            "GET", "/_matrix/client/v3/sync",
            params={"since": since, "timeout": str(SYNC_TIMEOUT_MS), "filter": FILTER},
            timeout=SYNC_READ_TIMEOUT,
        )
        self.process(response)
        token = self._token(response)
        self.state.save(token)
        return token

    @staticmethod
    def _token(response: dict[str, Any]) -> str:
        token = response.get("next_batch") if isinstance(response, dict) else None
        if not isinstance(token, str):
            raise MatrixError(None, "the sync answer carries no next_batch")
        return token

    def process(self, response: dict[str, Any]) -> None:
        rooms = response.get("rooms") if isinstance(response, dict) else None
        joined = rooms.get("join") if isinstance(rooms, dict) else None
        if not isinstance(joined, dict):
            return
        for room_id, room in joined.items():
            timeline = room.get("timeline") if isinstance(room, dict) else None
            if not isinstance(timeline, dict):
                continue
            if timeline.get("limited"):
                log.warning("the timeline of %s was cut; some messages went unread", room_id)
            for event in timeline.get("events") or []:
                if not isinstance(event, dict):
                    continue
                try:
                    self.answer(room_id, event)
                except Exception:
                    # A room's events are not trusted to have this code's shape; one bad event must not
                    # stop the stream or replay it after a restart.
                    log.exception("answering in %s", room_id)

    def answer(self, room_id: str, event: dict[str, Any]) -> None:
        descriptions = wanted(event, self.user_id)
        event_id = event.get("event_id")
        if not descriptions or not isinstance(event_id, str):
            return
        path = f"/_matrix/client/v3/rooms/{_room(room_id)}/send/m.room.message/{transaction_id(event_id)}"
        try:
            self.send(path, reply_content(descriptions, event_id))
        except MatrixError as error:
            log.warning("reply in %s: %s", room_id, error)

    def send(self, path: str, content: dict[str, Any]) -> None:
        """Send one message, waiting out a rate limit instead of losing the answer to it."""
        for attempt in range(3):
            try:
                self.matrix.call("PUT", path, body=content)
                return
            except MatrixError as error:
                if error.status == 429 and attempt < 2:
                    wait = error.retry_after_ms if error.retry_after_ms is not None else 1000
                    time.sleep(wait / 1000)
                    continue
                raise

    def join_public_rooms(self) -> None:
        """Join every public room on the bot's homeserver, so a room added later is covered too.

        The homeserver's room directory is the list of public rooms; a room it names that refuses the
        join is left alone, and the listing is read a page at a time.
        """
        self._joined_at = time.monotonic()
        try:
            answer = self.matrix.call("GET", "/_matrix/client/v3/joined_rooms")
            joined = set(answer.get("joined_rooms") or []) if isinstance(answer, dict) else set()
            since = None
            for _ in range(20):
                params = {"limit": "100"}
                if since:
                    params["since"] = since
                answer = self.matrix.call("GET", "/_matrix/client/v3/publicRooms", params=params)
                if not isinstance(answer, dict):
                    break
                for room in answer.get("chunk") or []:
                    room_id = room.get("room_id") if isinstance(room, dict) else None
                    if isinstance(room_id, str) and room_id not in joined:
                        self._join(room_id)
                since = answer.get("next_batch")
                if not since:
                    break
        except (MatrixError, KeyError, TypeError, AttributeError) as error:
            log.warning("joining the public rooms: %s", error)

    def _join(self, room_id: str) -> None:
        try:
            self.matrix.call("POST", f"/_matrix/client/v3/join/{_room(room_id)}", body={})
        except MatrixError as error:
            # One room refusing (or failing) the join must not leave the rooms after it unread.
            if error.status in (403, 404):
                log.info("not joining %s: %s", room_id, error)
            else:
                log.warning("joining %s: %s", room_id, error)


def read_token(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Answer a description in the community chat with its IDS Workshop page.")
    parser.add_argument("--homeserver", default=os.environ.get("MATRIX_HOMESERVER", HOMESERVER))
    parser.add_argument("--token", default=os.environ.get("MATRIX_ACCESS_TOKEN"),
                        help="the bot's access token; or give --token-file")
    parser.add_argument("--token-file", type=Path, help="file holding the bot's access token")
    parser.add_argument("--state", type=Path, help="file keeping the sync position between starts")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    token = args.token or (read_token(args.token_file) if args.token_file else "")
    if not token:
        parser.error("a token is needed: --token-file, --token or MATRIX_ACCESS_TOKEN")
    Bot(Matrix(args.homeserver, token), State(args.state)).run()


if __name__ == "__main__":
    main()
