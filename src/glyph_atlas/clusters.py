"""Written characters: a text split where one character ends and the next begins.

A combining mark joins the character before it, and a medial or final jamo joins the Hangul before
it, so a syllable spelt with conjoining jamo, ᄒᆞ (U+1112 U+119E), is one character. A mark with no
character before it, as a transcription sometimes starts (゚リ), joins the character after it.
"""

from __future__ import annotations

import unicodedata

HANGUL_TONE_MARKS = frozenset({0x302E, 0x302F})
#: The fillers that stand for a missing initial or vowel; a syllable of fillers alone is no syllable.
HANGUL_FILLERS = frozenset({0x115F, 0x1160})


def is_hangul_choseong(char: str) -> bool:
    cp = ord(char)
    return 0x1100 <= cp <= 0x115F or 0xA960 <= cp <= 0xA97F


def is_hangul_jungseong(char: str) -> bool:
    cp = ord(char)
    return 0x1160 <= cp <= 0x11A7 or 0xD7B0 <= cp <= 0xD7C6


def is_hangul_jongseong(char: str) -> bool:
    cp = ord(char)
    return 0x11A8 <= cp <= 0x11FF or 0xD7CB <= cp <= 0xD7FF


def _is_mark(char: str) -> bool:
    return unicodedata.category(char) in ("Mn", "Mc") or ord(char) in HANGUL_TONE_MARKS


def _is_hangul(char: str) -> bool:
    cp = ord(char)
    return is_hangul_choseong(char) or is_hangul_jungseong(char) or is_hangul_jongseong(char) or 0xAC00 <= cp <= 0xD7A3


def clusters(text: str) -> list[str]:
    """The written characters of `text`, in order."""
    out: list[str] = []
    leading = ""
    for char in text:
        jamo = is_hangul_jungseong(char) or is_hangul_jongseong(char)
        joins = _is_mark(char) or (jamo and bool(out) and _is_hangul(out[-1][-1]))
        if joins and out:
            out[-1] += char
        elif _is_mark(char):
            leading += char
        else:
            out.append(leading + char)
            leading = ""
    if leading:
        out.append(leading)
    return out


def is_mark_only(text: str) -> bool:
    """Whether `text` is marks with no character to carry them, which counts as no character."""
    return bool(text) and all(_is_mark(char) for char in text)


def is_one_character(text: str | None) -> bool:
    """Whether `text` is exactly one written character."""
    return bool(text) and len(clusters(text)) == 1


def is_conjoining_jamo_syllable(text: str) -> bool:
    """Whether `text` is an old-Hangul syllable Unicode has no precomposed code point for.

    The syllable is checked in NFD, so 셰 + ᇰ (U+C170 U+11F0), the NFC spelling of
    U+1109 U+1168 U+11F0, is one; text NFC would change is refused, so each syllable has one key.
    A modern syllable with no tone mark has its own code point and is refused too.
    """
    if not text or unicodedata.normalize("NFC", text) != text or len(text) == 1:
        return False
    chars = list(unicodedata.normalize("NFD", text))
    if all(ord(c) in HANGUL_FILLERS for c in chars):
        return False
    index = 0
    while index < len(chars) and is_hangul_choseong(chars[index]):
        index += 1
    if index == 0:
        return False
    first_vowel = index
    while index < len(chars) and is_hangul_jungseong(chars[index]):
        index += 1
    if index == first_vowel:
        return False
    while index < len(chars) and is_hangul_jongseong(chars[index]):
        index += 1
    if index < len(chars) and ord(chars[index]) in HANGUL_TONE_MARKS:
        index += 1
    return index == len(chars)
