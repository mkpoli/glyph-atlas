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
#: Compatibility jamo whose classifier shape mapping is pinned explicitly instead of inferred.
COMPATIBILITY_SHAPE_KEY_OVERRIDES = {
    "\u3164": "\u1160",  # HANGUL FILLER
    "\u318d": "\u119e",  # HANGUL LETTER ARAEA
    "\u318e": "\u11a1",  # HANGUL LETTER ARAEAE
}


def _jongseong_to_choseong() -> dict[str, str]:
    choseong: dict[str, str] = {}
    for cp in (*range(0x1100, 0x1160), *range(0xA960, 0xA980)):
        char = chr(cp)
        name = unicodedata.name(char, "")
        if name.startswith("HANGUL CHOSEONG "):
            choseong[name.removeprefix("HANGUL CHOSEONG ")] = char

    mapping: dict[str, str] = {}
    for cp in (*range(0x11A8, 0x1200), *range(0xD7CB, 0xD800)):
        char = chr(cp)
        name = unicodedata.name(char, "")
        if not name.startswith("HANGUL JONGSEONG "):
            continue
        same_shape = choseong.get(name.removeprefix("HANGUL JONGSEONG "))
        if same_shape is not None:
            mapping[char] = same_shape
    return mapping


JONGSEONG_TO_CHOSEONG = _jongseong_to_choseong()


def is_hangul_choseong(char: str) -> bool:
    cp = ord(char)
    return 0x1100 <= cp <= 0x115F or 0xA960 <= cp <= 0xA97F


def is_hangul_jungseong(char: str) -> bool:
    cp = ord(char)
    return 0x1160 <= cp <= 0x11A7 or 0xD7B0 <= cp <= 0xD7C6


def is_hangul_jongseong(char: str) -> bool:
    cp = ord(char)
    return 0x11A8 <= cp <= 0x11FF or 0xD7CB <= cp <= 0xD7FF


def _compatibility_jamo_shape(char: str) -> str:
    if char in COMPATIBILITY_SHAPE_KEY_OVERRIDES:
        return COMPATIBILITY_SHAPE_KEY_OVERRIDES[char]
    cp = ord(char)
    if not 0x3131 <= cp <= 0x318E:
        return char
    parts = [part for part in unicodedata.decomposition(char).split() if not part.startswith("<")]
    if not parts:
        return char
    mapped = "".join(chr(int(part, 16)) for part in parts)
    if len(mapped) == 1 and is_hangul_jongseong(mapped):
        return JONGSEONG_TO_CHOSEONG.get(mapped, mapped)
    return mapped


def shape_key(cluster: str) -> str:
    """One key per printed Hangul shape, for the classifier: never a rewrite of a label.

    Transcribers spell one printed shape several ways: the particle ᅵ as ㅣ (U+3163), ᅵ (U+1175) or
    a filler and ᅵ (U+115F U+1175). Compatibility jamo become the conjoining jamo of the same shape
    (an initial for a consonant, a vowel for a vowel), and a lone final becomes its initial when
    Unicode has one. A filler before a vowel, or a vowel filler after an initial, is dropped; the bare
    filler pair stays as it is.
    """
    mapped = "".join(_compatibility_jamo_shape(char) for char in cluster)
    if (
        len(mapped) >= 2
        and mapped[0] == "\u115f"
        and 0x1160 <= ord(mapped[1]) <= 0x11A7
        and not (len(mapped) == 2 and mapped[1] == "\u1160")
    ):
        mapped = mapped[1:]
    if len(mapped) >= 2 and mapped[0] != "\u115f" and is_hangul_choseong(mapped[0]) and mapped[1] == "\u1160":
        mapped = mapped[0] + mapped[2:]
    if len(mapped) == 1 and is_hangul_jongseong(mapped):
        mapped = JONGSEONG_TO_CHOSEONG.get(mapped, mapped)
    return unicodedata.normalize("NFC", mapped)


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
