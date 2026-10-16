from glyph_atlas.review import ocr_repair
from glyph_atlas.review.ocr_repair import corrections, letters, propose

PAGE = letters("【右丁】\n　古金千弐百匁程　シリヘツ山材木運上\n　同　千七百匁程　　鯡運上\n"
               "【左丁】\n　同　千弐百匁程　　商船運上\n")


def test_a_misread_character_between_matches_takes_the_transcriptions():
    assert corrections("同千七百両程緋運上", PAGE) == {4: "匁", 6: "鯡"}


def test_a_phrase_the_page_repeats_is_left_alone():
    assert corrections("百匁程", letters("千弐百匁程運上\n千七百匁程運上")) == {}


def test_a_block_the_page_does_not_hold_is_left_alone():
    assert corrections("熊の皮鯛昆布", PAGE) == {}


def test_warigaki_text_is_read_in_order():
    page = letters("熊の皮　鯡　昆布《割書：うかにて取所の石付と赤とを上品｜とししのりを下品とす》　椎茸")
    assert corrections("熊の皮鯛昆布うかりて石等の石付と酒にを上品椎茸", page) == {
        3: "鯡", 8: "に", 10: "取", 11: "所", 16: "赤", 17: "と"}


def crop(position, label, offset=0, identity=None):
    return {"id": identity or f"u{position}", "page_id": "p", "context": "同千七百両程緋運上", "position": position,
            "offset": offset, "label": label, "text": "同千七百両程緋運上"[position]}


def test_a_shifted_crop_takes_the_correction_of_the_place_its_label_came_from():
    blocks = {("k", 48, "ocr9"): [crop(5, "緋", offset=1), crop(6, "運", offset=1), crop(3, "百")]}
    got = {p["unit_id"]: (p["character"], p["text"]) for p in propose(blocks, {"p": PAGE}, set())[0]}
    assert got == {"u5": ("鯡", "程"), "u6": ("運", "鯡")}


def test_a_protected_or_relabelled_crop_keeps_its_label():
    blocks = {("k", 48, "ocr9"): [crop(6, "緋", identity="kept"), crop(4, "雨")]}
    got = {p["unit_id"]: p["character"] for p in propose(blocks, {"p": PAGE}, {"kept"})[0]}
    assert got == {"u4": "雨"}


def test_the_method_is_named():
    assert ocr_repair.METHOD == "ocr-transcription-v1"
