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


def test_a_protected_or_relabelled_crop_is_left_alone():
    blocks = {("k", 48, "ocr9"): [crop(6, "緋", identity="kept"), crop(4, "雨")]}
    assert propose(blocks, {"p": PAGE}, {"kept"})[0] == []


def test_ocr_punctuation_is_not_aligned_with_a_ruby_base():
    page = letters("《振り仮名：アツテカニ｜捧》。ハリキテケワ")
    assert corrections("アツテカニ。ハリキテケワ", page) == {}


def test_an_equal_match_close_by_counts_as_another_place():
    assert corrections("千両百匁程", "千弐百匁程千七百匁程") == {}


def test_a_page_without_text_is_counted():
    blocks = {("k", 48, "ocr9"): [crop(6, "緋")]}
    assert propose(blocks, {"p": ""}, set())[1]["no transcription"] == 1


def test_the_method_is_named():
    assert ocr_repair.METHOD == "ocr-transcription-v1"


def test_a_normalised_form_is_told_apart_from_a_misreading():
    assert ocr_repair.form_pair("嶋", "島") == "variant"
    assert ocr_repair.form_pair("ハ", "は") == "kana"
    assert ocr_repair.form_pair("緋", "鯡") is None


def test_kana_around_decide_a_kana_the_ink_cannot():
    assert ocr_repair.prior("ハ", "は", "これかのものに") > 0
    assert ocr_repair.prior("ハ", "は", "コレカノモノニ") < 0
    assert ocr_repair.prior("嶋", "島", "コレカノモノニ") == 0


def test_the_written_form_stays_unless_the_ink_or_its_neighbours_say_otherwise():
    assert ocr_repair.keeps_written(None, None, 0.0)
    assert ocr_repair.keeps_written(0.6, 0.1, 0.0)
    assert not ocr_repair.keeps_written(0.1, 0.6, 0.0)
    assert ocr_repair.keeps_written(0.3, 0.4, -2.0)


def test_a_kept_written_form_keeps_its_label_and_text():
    items = [{"unit_id": "u", "before": "嶋", "character": "島", "text_before": "嶋", "text": "島", "offset": 0,
              "form": "variant", "prior": 0.0, "text_form": True}]
    ocr_repair.settle(None, items, None)
    assert (items[0]["character"], items[0]["text"], items[0]["written"]) == ("嶋", "嶋", True)


def test_a_transcription_that_keeps_the_older_form_is_followed():
    assert ocr_repair.normalises("國", "国") and ocr_repair.normalises("邊", "辺")
    assert not ocr_repair.normalises("辺", "邊") and not ocr_repair.normalises("嶋", "島")
    items = [{"unit_id": "u", "before": "国", "character": "國", "text_before": "国", "text": "國", "offset": 0,
              "form": "variant", "prior": 0.0, "text_form": True}]
    ocr_repair.settle(None, items, None)
    assert (items[0]["character"], items[0]["text"], items[0].get("written")) == ("國", "國", None)


def test_a_simplified_chinese_pair_keeps_the_ocr_form():
    items = [{"unit_id": "u", "before": "烟", "character": "煙", "text_before": "烟", "text": "煙", "offset": 0,
              "form": "variant", "prior": 0.0, "text_form": True}]
    ocr_repair.settle(None, items, None)
    assert (items[0]["character"], items[0]["text"], items[0]["written"]) == ("烟", "烟", True)


def test_a_crop_is_cut_at_the_services_full_resolution(monkeypatch, tmp_path):
    from PIL import Image

    from glyph_atlas import images, net
    from glyph_atlas.schema import Box, Page

    asked = []
    monkeypatch.setattr(images, "info", lambda service, client=None: {"width": 2686, "version": 2})

    def download(url, dest, **_):
        asked.append(url)
        Image.new("L", (46, 90)).save(dest, "JPEG")
        return dest

    monkeypatch.setattr(net, "download", download)
    page = Page(id="hk:d:1", document_id="hk:d", seq=1, width=1343, height=1000,
                image="https://example.org/iiif/?IIIF=/a.tif/full/1343,/0/default.jpg")
    picture = ocr_repair.ink(page, Box(x=162, y=406, w=23, h=45), {})
    assert asked == ["https://example.org/iiif/?IIIF=/a.tif/324,812,46,90/full/0/default.jpg"]
    assert picture.size == (46, 90)
