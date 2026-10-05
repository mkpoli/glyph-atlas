"""User-perceived character clusters used by the atlas."""

from __future__ import annotations

import unicodedata

HANGUL_TONE_MARKS = frozenset({0x302E, 0x302F})


def is_hangul_choseong(char: str) -> bool:
    cp = ord(char)
    return 0x1100 <= cp <= 0x115F or 0xA960 <= cp <= 0xA97F


def is_hangul_jungseong(char: str) -> bool:
    cp = ord(char)
    return 0x1160 <= cp <= 0x11A7 or 0xD7B0 <= cp <= 0xD7C6


def is_hangul_jongseong(char: str) -> bool:
    cp = ord(char)
    return 0x11A8 <= cp <= 0x11FF or 0xD7CB <= cp <= 0xD7FF


def joins_previous(char: str) -> bool:
    """Whether this code point belongs to the previous written character."""
    cp = ord(char)
    return (
        unicodedata.category(char) in ("Mn", "Mc")
        or 0x1160 <= cp <= 0x11FF
        or 0xD7B0 <= cp <= 0xD7FF
        or cp in HANGUL_TONE_MARKS
    )


def clusters(text: str) -> list[str]:
    """Split text into the atlas's written characters."""
    out: list[str] = []
    for char in text:
        if joins_previous(char) and out:
            out[-1] += char
        else:
            out.append(char)
    return out


def is_one_character(text: str | None) -> bool:
    """Whether `text` is exactly one atlas character."""
    return bool(text) and len(clusters(text)) == 1


def is_conjoining_jamo_syllable(text: str) -> bool:
    """A well-formed old-Hangul syllable spelt with conjoining jamo."""
    if not text or not is_one_character(text):
        return False
    index = 0
    chars = list(text)
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
