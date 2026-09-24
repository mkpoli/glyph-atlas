"""Tests of the joined-crop splitter: where it cuts, and where it refuses to.

The crops here are synthetic ink: each character is a small distinct bitmap pattern painted into a
cell, and a joined crop is two to four of those cells stacked with blank rows between them. The
character model the splitter is given is a template matcher — it correlates a crop's ink mask with the
rendered patterns and answers one mass each — which is a model of the *ink*, not a restatement of the
splitter: it never sees a profile, a valley or a cut, and it answers the same for a crop however that
crop was produced. A cut in the wrong place therefore scores badly for the same reason a real model
would mark it badly: half a character is not a character.

What the tests pin down is the set of refusals, because those are the behaviour a caller depends on:
no blank run means no cut, a cut that leaves a child the model cannot read is not applied, a stroke
running out of the crop is not a join, an ambiguous pair of boundaries is reported rather than
guessed, and a ligature — one encoded character made of two — is never cut into its parts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
import pytest
from PIL import Image, ImageDraw

from glyph_atlas import split_proposals as sp
from glyph_atlas.schema import Box

#: One 4x6 ink pattern a character: the shape the template matcher knows it by.
PATTERNS: dict[str, tuple[str, ...]] = {
    "ア": ("1000", "1111", "0010", "0010", "0010", "0110"),
    "イ": ("0100", "0100", "0111", "0100", "0100", "0100"),
    "ウ": ("0110", "1001", "0001", "0010", "0100", "1111"),
    "シ": ("0010", "0100", "1010", "0010", "0100", "1000"),
    "ヤ": ("1111", "0010", "0111", "0010", "0010", "0010"),
}
CODE = {"ア": "U+30A2", "イ": "U+30A4", "ウ": "U+30A6", "シ": "U+30B7", "ヤ": "U+30E4"}


def cell(character: str, *, width: int = 32, height: int = 48, inset: int = 3) -> Image.Image:
    """One character's ink on white, its pattern painted inside a blank margin of the cell.

    The margin is what a detector box around a character usually holds: the box is drawn a little
    wider than the ink, so the crop's first and last rows are blank and a stroke that does touch them
    is a stroke the box cut through.
    """
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    pattern = PATTERNS[character]
    rows, columns = len(pattern), len(pattern[0])
    grown = max(1, height - 2 * inset)
    top_edge = inset
    for row, line in enumerate(pattern):
        top = min(height - 1, top_edge + round(row * grown / rows))
        bottom = min(height, max(top + 1, top_edge + round((row + 1) * grown / rows)))
        for column, mark in enumerate(line):
            if mark != "1":
                continue
            left = round(column * width / columns)
            right = max(left + 1, round((column + 1) * width / columns))
            draw.rectangle([left, top, right - 1, bottom - 1], fill=0)
    return image


def joined(parts: Sequence[tuple[str, int]], *, width: int = 32, gap: int = 6,
           tail: int = 0) -> Image.Image:
    """Cells stacked top to bottom with `gap` blank rows between them, as joined ink."""
    cells = [cell(character, width=width, height=height) for character, height in parts]
    height = sum(image.height for image in cells) + gap * (len(cells) - 1) + tail
    canvas = Image.new("L", (width, height), 255)
    top = 0
    for image in cells:
        canvas.paste(image, (0, top))
        top += image.height + gap
    return canvas


def bands(parts: Sequence[tuple[str, int]], *, gap: int = 6, tail: int = 0) -> list[tuple[int, int]]:
    """The (top, bottom) rows each character truly occupies in `joined`'s canvas."""
    found: list[tuple[int, int]] = []
    top = 0
    for _, height in parts:
        found.append((top, top + height))
        top += height + gap
    assert top - gap + tail >= top - gap
    return found


def mask(image: Image.Image) -> np.ndarray:
    """A crop's ink as a boolean grid; a grey pixel counts as ink where it is over half dark."""
    return np.asarray(image.convert("L"), dtype=np.float32) < 128


def template(character: str, width: int, height: int) -> np.ndarray:
    """The pattern of a character drawn on a canvas of a crop's own size."""
    return mask(cell(character, width=width, height=height))


def template_scorer(crops: Sequence[Image.Image]) -> list[Mapping[str, float]]:
    """A nearest-template character model: mass is the cube of the ink-mask overlap.

    It sees only the crop: its size and its ink. Half a character overlaps no whole template, and two
    characters in one crop overlap none either, so the masses it answers are low exactly where a
    character model has nothing to say. The cube keeps a half-overlap from scoring like a match.
    """
    answers: list[Mapping[str, float]] = []
    for crop in crops:
        ink = mask(crop)
        height, width = ink.shape
        if not ink.any():
            answers.append({code: 0.0 for code in CODE.values()})
            continue
        found: dict[str, float] = {}
        for character, code in CODE.items():
            drawn = template(character, width, height)
            union = np.logical_or(ink, drawn).sum()
            overlap = np.logical_and(ink, drawn).sum() / union if union else 0.0
            found[code] = float(overlap) ** 3
        answers.append(found)
    return answers


def agreeing_scorer(*expected: str):
    """A model that always reads the expected character, whatever the crop: no geometry in it at all.

    It is here to isolate the rules that are not about recognition — a crop of blank paper is still
    refused, and two boundaries the reading cannot separate are still a tie — and not to stand in for
    a model. The template matcher above is the model; this one has no opinion about ink.
    """
    codes = [CODE[character] for character in expected]

    def scorer(crops: Sequence[Image.Image]) -> list[Mapping[str, float]]:
        answers = []
        for index, _ in enumerate(crops):
            wanted = codes[min(index, len(codes) - 1)]
            answers.append({code: (0.9 if code == wanted else 0.01) for code in CODE.values()})
        return answers

    return scorer


def on_paper(image: Image.Image, *, paper: int = 190, ink: int = 70) -> Image.Image:
    """The same ink on paper of another colour: white becomes `paper`, black becomes `ink`."""
    return image.point(lambda value: round(ink + (paper - ink) * value / 255))


def guard_answering(ligatures: Mapping[str, str]):
    """A character layer that knows exactly the ligatures a test tells it about."""

    def guard(text: str) -> sp.LigatureCheck:
        return sp.LigatureCheck(code_point=ligatures.get(text), available=True)

    return guard


def recording(scorer):
    """A scorer that remembers the batches it was called with."""
    calls: list[list[Image.Image]] = []

    def wrapped(crops: Sequence[Image.Image]):
        calls.append(list(crops))
        return scorer(crops)

    return wrapped, calls


# --- the cut itself ------------------------------------------------------------------------------


def test_two_stacked_characters_are_split_on_the_blank_run_between_them() -> None:
    """The common case: two characters, one gap, and the cut lands in the gap."""
    parts = [("ア", 48), ("イ", 48)]
    crop = joined(parts)
    proposal = sp.propose_split(crop, "アイ", scorer=template_scorer,
                                ligature=guard_answering({}))
    assert proposal.accepted, proposal.reason
    assert len(proposal.boxes) == 2 and len(proposal.cuts) == 1
    (first, second) = proposal.boxes
    assert (first.x, first.w) == (0, crop.width) and (second.x, second.w) == (0, crop.width)
    assert first.y == 0 and second.y + second.h == crop.height
    assert 44 <= proposal.cuts[0] <= 56, "the cut belongs in the blank run, not inside a stroke"
    for box, (top, bottom) in zip(proposal.boxes, bands(parts), strict=True):
        assert box.y <= top + 2 and box.y + box.h >= bottom - 2, "a child holds its whole character"
    assert proposal.children[0].canonical and proposal.children[1].canonical
    assert proposal.cut_scores[0].ink_share == 0.0
    children = proposal.crops(crop)
    assert [image.size for image in children] == [(box.w, box.h) for box in proposal.boxes]


def test_three_characters_are_cut_twice() -> None:
    """A crop of three characters needs two boundaries, and gets them both."""
    parts = [("ア", 40), ("イ", 40), ("ウ", 40)]
    crop = joined(parts)
    proposal = sp.propose_split(crop, "アイウ", scorer=template_scorer,
                                ligature=guard_answering({}))
    assert proposal.accepted, proposal.reason
    assert len(proposal.boxes) == 3 and len(proposal.cuts) == 2
    assert proposal.cuts == sorted(proposal.cuts)
    heights = [box.h for box in proposal.boxes]
    assert sum(heights) == crop.height
    for box, (top, bottom) in zip(proposal.boxes, bands(parts), strict=True):
        assert box.y <= top + 2 and box.y + box.h >= bottom - 2


def test_children_need_not_be_the_same_height() -> None:
    """Two characters of very different heights are one shape a cut may take, not a reason to cut."""
    parts = [("ア", 20), ("イ", 80)]
    crop = joined(parts)
    proposal = sp.propose_split(crop, "アイ", scorer=template_scorer,
                                ligature=guard_answering({}))
    assert proposal.accepted, proposal.reason
    first, second = proposal.boxes
    assert second.h > 2 * first.h, "an equal division would have cut this pair in half"
    assert proposal.cuts[0] < crop.height // 2


def test_boxes_are_local_to_the_crop_and_map_to_the_page() -> None:
    """A child box is crop-local; `to_page` is the only step between it and the page."""
    parts = [("ア", 40), ("イ", 40)]
    crop = joined(parts)
    proposal = sp.propose_split(crop, "アイ", scorer=template_scorer, ligature=guard_answering({}))
    assert proposal.accepted
    parent = Box(x=100, y=200, w=crop.width, h=crop.height)
    placed = sp.to_page(proposal.boxes, parent)
    assert placed[0] == Box(x=100, y=200, w=crop.width, h=proposal.boxes[0].h)
    assert placed[1].y == 200 + proposal.boxes[1].y


# --- the refusals --------------------------------------------------------------------------------


def test_a_crop_with_no_blank_run_is_not_split_by_its_length() -> None:
    """Two characters long is not evidence of where the boundary is: solid ink has no boundary."""
    crop = Image.new("L", (32, 90), 255)
    ImageDraw.Draw(crop).rectangle([4, 6, 27, 83], fill=0)
    proposal = sp.propose_split(crop, "アイ", scorer=template_scorer, ligature=guard_answering({}))
    assert not proposal.accepted
    assert proposal.boxes == []
    assert "equal division is not proposed" in proposal.reason


def test_two_characters_that_touch_are_not_split() -> None:
    """Ink that meets in the middle has no row the strokes do not cross, so nothing is proposed."""
    crop = Image.new("L", (32, 96), 255)
    draw = ImageDraw.Draw(crop)
    draw.rectangle([6, 4, 25, 47], fill=0)
    draw.rectangle([6, 48, 25, 91], fill=0)
    proposal = sp.propose_split(crop, "アイ", scorer=template_scorer, ligature=guard_answering({}))
    assert not proposal.accepted
    assert "no row of the crop holds less ink" in proposal.reason


def test_a_cut_whose_child_the_model_cannot_read_is_withheld() -> None:
    """The blank run is real — but the characters either side are not the ones that were read."""
    parts = [("ア", 48), ("イ", 48)]
    crop = joined(parts)
    proposal = sp.propose_split(crop, "アン", scorer=template_scorer, ligature=guard_answering({}))
    assert not proposal.accepted
    assert proposal.children[1].mass < sp.Limits().min_character_mass
    assert "was expected" in proposal.reason and "a different character" in proposal.reason
    assert proposal.boxes, "the geometry is still reported, so a reviewer can see what was tried"


def test_a_stroke_running_out_of_the_crop_is_not_a_join() -> None:
    """A character the box cut through cannot be split into characters, however many are expected."""
    parts = [("ア", 40), ("イ", 40)]
    crop = joined(parts, tail=0)
    pixels = crop.load()
    for x in range(6, 26):  # the last character's ink runs off the bottom row
        pixels[x, crop.height - 1] = 0
    proposal = sp.propose_split(crop, "アイ", scorer=template_scorer, ligature=guard_answering({}))
    assert not proposal.accepted
    assert "cut off by the box" in proposal.reason


def test_a_child_with_no_ink_is_refused_by_the_rule() -> None:
    """A blank rectangle is not a character, however well the model scores it.

    The generator cannot offer such a child — every boundary it proposes has ink on both sides of it
    — so the rule is stated and tested on its own, where a caller can see what a split may produce.
    """
    blank = sp.ChildScore(text="ア", box=Box(x=0, y=0, w=32, h=40), mass=0.9, ink=0, top="U+30A2",
                          canonical=True)
    real = sp.ChildScore(text="イ", box=Box(x=0, y=40, w=32, h=40), mass=0.9, ink=180, top="U+30A4",
                         canonical=True)
    assert "no ink at all" in (sp.child_refusal([blank, real], min_mass=0.05) or "")
    unread = sp.ChildScore(text="イ", box=Box(x=0, y=40, w=32, h=40), mass=0.001, ink=180,
                           top="U+30A4", canonical=True)
    assert "a split needs" in (sp.child_refusal([real, unread], min_mass=0.05) or "")
    assert sp.child_refusal([real, real], min_mass=0.05) is None


def test_ink_in_one_band_of_a_tall_crop_has_no_boundary() -> None:
    """A boundary needs a character on each side of it; blank paper either side is not a join."""
    crop = Image.new("L", (32, 120), 255)
    ImageDraw.Draw(crop).rectangle([6, 40, 25, 80], fill=0)
    proposal = sp.propose_split(crop, "アイ", scorer=agreeing_scorer("ア", "イ"),
                                ligature=guard_answering({}))
    assert not proposal.accepted
    assert "shows no boundary" in proposal.reason


def test_two_boundaries_the_model_cannot_tell_apart_are_reported_not_guessed() -> None:
    """With three characters of blank run either side, only the model can choose; a flat one cannot."""
    parts = [("ア", 40), ("イ", 40), ("ウ", 40)]
    crop = joined(parts)
    proposal = sp.propose_split(crop, "アイ", scorer=agreeing_scorer("ア", "イ"),
                                ligature=guard_answering({}))
    assert not proposal.accepted
    assert "equally well" in proposal.reason
    assert proposal.alternatives and proposal.margin is not None
    assert proposal.margin < sp.Limits().min_margin


def test_an_ambiguous_pair_is_kept_as_evidence() -> None:
    """A withheld proposal still says which boundaries were considered and how close they were."""
    parts = [("ア", 40), ("イ", 40), ("ウ", 40)]
    proposal = sp.propose_split(joined(parts), "アイ", scorer=agreeing_scorer("ア", "イ"),
                                ligature=guard_answering({}))
    assert len(proposal.alternatives) >= 1
    assert all(cut not in (0,) for alternative in proposal.alternatives for cut in alternative.cuts)
    assert all(alternative.cost >= 0 for alternative in proposal.alternatives)


# --- the reading, the graphemes and the ligature guard -------------------------------------------


def test_a_single_character_reading_is_not_a_join() -> None:
    """One character has no boundary to find, and a ligature is one character."""
    crop = joined([("ア", 40), ("イ", 40)])
    for expected in ("ア", "𪜈"):
        proposal = sp.propose_split(crop, expected, scorer=template_scorer,
                                    ligature=guard_answering({}))
        assert not proposal.accepted
        assert "a joined crop is 2 to 4" in proposal.reason


def test_the_components_of_a_ligature_are_not_two_characters() -> None:
    """トモ read off a crop may be 𪜈, one encoded character; the layer that knows says so."""
    crop = joined([("ア", 40), ("イ", 40)])
    guard = guard_answering({"トモ": "U+2A708"})
    proposal = sp.propose_split(crop, "トモ", scorer=template_scorer, ligature=guard)
    assert not proposal.accepted
    assert "U+2A708" in proposal.reason and "one encoded ligature" in proposal.reason
    assert proposal.boxes == []


def test_an_unavailable_ligature_layer_withholds_the_split() -> None:
    """A tree without `data/vocab/ligatures.yaml` cannot rule out a ligature, so it does not cut."""
    crop = joined([("ア", 40), ("イ", 40)])
    def unavailable(text):
        return sp.LigatureCheck(available=False)

    proposal = sp.propose_split(crop, "アイ", scorer=template_scorer, ligature=unavailable)
    assert not proposal.accepted
    assert "ligature overlay is not available" in proposal.reason
    assert proposal.evidence["ligature_guard"] == "unavailable"


def test_the_default_guard_answers_from_the_character_layer() -> None:
    """The default asks `refs.ligature`; a tree without the overlay says so instead of guessing."""
    from glyph_atlas import refs

    check = sp.default_ligature_guard("ア")
    assert check.available == (getattr(refs, "ligature", None) is not None)
    if not check.available:
        assert check.code_point is None


def test_a_combining_sequence_is_one_child_in_either_spelling() -> None:
    """か + U+3099 is が: one character, one child, and the same split whichever spelling came in."""
    parts = [("ア", 48), ("イ", 48)]
    crop = joined(parts)
    decomposed = sp.propose_split(crop, "か\u3099し", scorer=template_scorer,
                                  ligature=guard_answering({}))
    composed = sp.propose_split(crop, "がし", scorer=template_scorer,
                                ligature=guard_answering({}))
    assert [child.text for child in decomposed.children] == ["が", "し"]
    assert len(decomposed.boxes) == 2, "a base and its mark are one child, not two"
    assert decomposed.boxes == composed.boxes and decomposed.cuts == composed.cuts
    assert sp.graphemes("か\u3099し") == ["が", "し"]
    spellings = sp.default_candidates("が")
    assert spellings[0] == "U+304C" and "U+304B" in spellings and "U+3099" in spellings
    assert sp.default_candidates("か\u3099") == spellings


def test_the_scorer_is_called_once_a_boundary_with_every_child() -> None:
    """A batch a candidate boundary, so a caller can size the batch a device takes."""
    parts = [("ア", 40), ("イ", 40), ("ウ", 40)]
    scorer, calls = recording(template_scorer)
    proposal = sp.propose_split(joined(parts), "アイウ", scorer=scorer, ligature=guard_answering({}))
    assert proposal.accepted, proposal.reason
    assert calls, "the model has to be asked"
    assert all(len(batch) == 3 for batch in calls), "one batch a boundary, every child in it"
    assert len(calls) == proposal.evidence["candidates"]


def test_the_evidence_carries_the_thresholds_and_the_ink() -> None:
    """A proposal states what it was decided on, so a caller can argue with it."""
    parts = [("ア", 40), ("イ", 40)]
    proposal = sp.propose_split(joined(parts), "アイ", scorer=template_scorer,
                                ligature=guard_answering({}))
    evidence = proposal.evidence
    assert evidence["limits"]["min_margin"] == sp.Limits().min_margin
    assert evidence["crop"][1] == parts[0][1] * 2 + 6
    assert evidence["median_row_ink"] > 0 and evidence["total_ink"] > 0
    assert evidence["boundaries"] and "y" in evidence["boundaries"][0]
    assert evidence["ligature_guard"] == "answered"


def test_the_profile_is_read_from_ink_not_from_the_expected_length() -> None:
    """A profile with a blank run in the middle has one boundary; the ink decides, not the text."""
    from glyph_atlas.split_proposals import ink_profile

    profile = ink_profile(joined([("ア", 40), ("イ", 40)]), smooth=1)
    assert min(profile) == 0, "the blank run between the cells is in the profile"
    assert profile[0] == 0 and profile[-1] == 0, "the cells were drawn with a margin"
    assert len(profile) == 40 * 2 + 6
    assert profile[20] > 0, "the first character's ink is in the profile"


def test_a_proposal_without_a_model_run_yet_is_still_a_refusal() -> None:
    """The scorer is required: a caller cannot forget it and get a geometric split by accident."""
    with pytest.raises(TypeError):
        sp.propose_split(joined([("ア", 40), ("イ", 40)]), "アイ")  # type: ignore[call-arg]


def test_ink_on_brown_paper_gives_the_same_geometry() -> None:
    """A century-old scan is brown: paper must not count as ink, or there is no blank run to cut in."""
    parts = [("ア", 48), ("イ", 48)]
    white = joined(parts)
    brown = on_paper(white)
    white_profile = sp.ink_profile(white, smooth=1)
    brown_profile = sp.ink_profile(brown, smooth=1)
    assert min(white_profile) == 0 and min(brown_profile) == 0
    assert max(brown_profile) == pytest.approx(max(white_profile), rel=0.05)
    first = sp.propose_split(white, "アイ", scorer=template_scorer, ligature=guard_answering({}))
    second = sp.propose_split(brown, "アイ", scorer=template_scorer, ligature=guard_answering({}))
    assert first.accepted and second.accepted, second.reason
    assert first.boxes == second.boxes and first.cuts == second.cuts
    assert second.cut_scores[0].ink_share == 0.0


def test_blank_paper_of_any_colour_holds_no_ink() -> None:
    """Blank paper is blank however dark it is, and scanner noise on it is not ink either."""
    for level in (255, 210, 190, 160):
        crop = Image.new("L", (32, 120), level)
        assert sp.ink_profile(crop, smooth=1) == [0.0] * 120
        proposal = sp.propose_split(crop, "アイ", scorer=template_scorer,
                                    ligature=guard_answering({}))
        assert not proposal.accepted
        assert "holds no ink" in proposal.reason, f"paper at {level}: {proposal.reason}"
    noise = np.random.default_rng(7).normal(190, 3.0, size=(120, 32))
    noisy = Image.fromarray(np.clip(noise, 0, 255).astype("uint8"), mode="L")
    assert sp.ink_profile(noisy, smooth=1) == [0.0] * 120
    assert "holds no ink" in sp.propose_split(
        noisy, "アイ", scorer=template_scorer, ligature=guard_answering({})).reason


def test_the_model_top_choice_has_to_be_the_expected_character() -> None:
    """A mass is not enough: if the model's answer for the crop is another character, no split.

    This is the case a caller with a calibrated model meets at `min_character_mass=0.85`: the
    expected character holds 0.9, so the mass rule passes, and the model's top-1 is something else, so
    the crop is not that character and the cut is not applied.
    """
    parts = [("ア", 48), ("イ", 48)]
    crop = joined(parts)
    limits = sp.Limits(min_character_mass=0.85)

    def elsewhere(crops: Sequence[Image.Image]) -> list[Mapping[str, float]]:
        assert len(crops) == 2
        return [{"U+30A2": 0.90, "U+30A4": 0.95}, {"U+30A2": 0.0, "U+30A4": 0.97}]

    proposal = sp.propose_split(crop, "アイ", scorer=elsewhere, ligature=guard_answering({}),
                                candidates_of=lambda character: [CODE[character]], limits=limits)
    assert not proposal.accepted
    assert proposal.children[0].mass == 0.90 and proposal.children[0].top == "U+30A4"
    assert proposal.children[0].canonical is False
    assert "where 'ア' was expected" in proposal.reason

    def agreeing(crops: Sequence[Image.Image]) -> list[Mapping[str, float]]:
        return [{"U+30A2": 0.95, "U+30A4": 0.0}, {"U+30A2": 0.0, "U+30A4": 0.97}]

    accepted = sp.propose_split(crop, "アイ", scorer=agreeing, ligature=guard_answering({}),
                                candidates_of=lambda character: [CODE[character]], limits=limits)
    assert accepted.accepted, accepted.reason


def test_the_default_candidate_set_is_the_written_character_not_its_reading() -> None:
    """し is U+3057, not every hentaigana that reads し; the phonetic set is opt-in."""
    assert sp.default_candidates("し") == ["U+3057"]
    assert len(sp.variant_candidates("し")) > 1, "the layer knows other forms of the reading"
    assert "U+3057" == sp.variant_candidates("し")[0]
    assert sp.default_candidates("ア") == ["U+30A2"]
    assert sp.default_candidates("あい") == ["U+3042", "U+3044"]


def test_the_text_cost_is_a_log_mass_and_not_a_shape() -> None:
    """Two children read at 0.5 and 0.5 cost less than one at 0.9 and one at 0.01."""
    cheap = [sp.ChildScore(text="ア", box=Box(x=0, y=0, w=1, h=1), mass=0.5),
             sp.ChildScore(text="イ", box=Box(x=0, y=1, w=1, h=1), mass=0.5)]
    dear = [sp.ChildScore(text="ア", box=Box(x=0, y=0, w=1, h=1), mass=0.9),
            sp.ChildScore(text="イ", box=Box(x=0, y=1, w=1, h=1), mass=0.01)]
    assert sp._text_cost(cheap, 1e-4) < sp._text_cost(dear, 1e-4)
    assert sp._text_cost(cheap, 1e-4) == pytest.approx(-2 * math.log(0.5))
