# IDS composer: goals and open cases

`glyph_atlas.compose` draws a character Unicode lacks from its Ideographic Description Sequence in
GenZui Sans. The goal is a drawing a reader takes for a character the type designer drew. Each
target below is measured by `scripts/compose_ids.py bench` on the font's own encoded kanji, redrawn
from their BabelStone sequences with nothing taken from the character itself; the plain way to
compose, each operand's own glyph squeezed into its share, scores 0.337 on the same set. By
structure: ⿰ 0.767 (1,093 kanji), ⿱ 0.637 (478), enclosures 0.37–0.61, nested parts 0.54–0.64.
With every part placed in the real glyph's own boxes, the parts the composer chooses score 0.76
and the best parts any host offers 0.81: layout is the larger remaining loss, stacked layouts most.

## Targets

| Measure | Set | Now | Target |
|---|---|---|---|
| Ink overlap with the real glyph (intersection over union, 96 px), mean | 1,981 compound jōyō kanji | 0.697 | 0.75 |
| Ink overlap, tenth percentile | same | 0.463 | 0.55 |
| Outline length within 15% of the real glyph's (no stroke missing or added) | same | 93.5% | 99.5% |
| Drawn at all | same | 1,967 of 1,981 | all |
| Stroke width against the real glyph, median, across x and across y | same | 0.997, 1.000 | within 0.97–1.03 |
| Facing gap between neighbouring parts within the 5–95% range of drawn characters | same | not yet measured | 99% |
| Drawn at all | distinct HDIC headword sequences: KRM 3,029, TSJ 356, KTB 9 | 2,997, 355, 9 (all failures ⿻) | all |
| Preferred over zi.tools by a reviewer, side by side | 96 KRM headwords | not yet judged | 80% |

## Method

- A layout comes from the three drawn characters of the same structure whose operands are most
  like the target's, averaged; at the top level in the em, where the designer placed them.
- A part comes from a character that draws it in the same place, in the positional form Japanese
  characters write there (𤣩 for 王 on the left), with the stroke count that operand usually has
  there; a nested sequence a character draws whole is taken whole.
- An enclosed part fills the room the enclosing part leaves, found in its own outline.
- Parts facing each other closer than 95% of drawn characters keep them are moved apart.
- Every stroke is brought to the width a real character of the result's density has.
- Three operands with no teacher sharing one of them share the axis as the font's own ⿳ and ⿲
  characters do: by ink, a stacked middle part compressed.
- Components Noto Sans CJK lacks are drawn from Plangothic (SIL OFL 1.1), the Source Han Sans
  design extended to the CJK extensions.

## Ceilings

Measured on two-part jōyō kanji with every part placed in the real glyph's own boxes, so that only
the parts differ:

| Parts | Ink overlap |
|---|---|
| the composer's choice | 0.76–0.79 |
| the best in the composer's shortlist of hosts (about 12 per operand) | 0.80 |
| the best among about 55 hosts per operand | 0.81 |
| the composer's choice, each warped (three bands per axis) to fit the real part as well as it can | 0.82 |

The shortlist already holds a better part about as often as not, but nothing measurable before
drawing tells which: a learned ranker over box fit, proportions, outline count and ink-grid
distance picked as well as the current order (0.835 against 0.834 per part, held-out), and a warp
predicted from the same knowledge scored below none (0.720 against 0.724 on 299 two-part kanji),
as did a glyph-image network used to steer part choice and placement (+0.004) or to steer
that warp (0.730 against 0.724). The learned
layout's boxes lose a further 0.04–0.10 to these. Reusing drawn parts therefore tops out near
0.72 overall; the 0.75 target needs parts reshaped to their new company, which the font does not
show how to do.

## Next mechanisms

In order of expected gain:

1. Extraction scored jointly: cut candidates for all operands of a host at once, against signatures
   learned from clean cuts (contour groups, counters, endpoints, junctions), recursively for nested
   operands, with containment tested on outlines, not boxes.
2. Layout by component family: teachers sharing a distinctive component first (the ⿱𡗜 family for
   ⿱𡗜集), averaged only within a compatible shape family; the three heights of ⿳ fitted
   together against learned priors.
3. Enclosed parts fitted to the opening's shape (a mask or distance field), not the largest empty
   rectangle, with the contacts drawn characters make.
4. Designed sub-shapes found by stroke structure and contacts (丘 in 岳, 失 in 鉄, 炎 in 談, ⿱⺀㐅)
   for sequences only strokes describe.
5. One bounded spacing solve on the final weighted outlines, for gaps too wide as well as too tight.
6. Compression that keeps dots, hooks and counters, preferring a donor already drawn compressed.
7. ⿻ laid out from the font's own crossing arrangements.

## Open cases

Each case is a sequence and what is wrong with its drawing.

### Proportion of stacked and side-by-side parts

- ⿳安米心, ⿳髟人巾: the middle part keeps its full height; drawn characters compress it.
- ⿳一丷目, ⿱鮮向: proportions and balance.
- ⿱⿱宀尸⿱⺫心: parts crowded, proportions off.
- ⿴囗吕: proportions.
- ⿱𡗜集: should follow how other ⿱𡗜 characters are drawn.
- ⿰扌頑, ⿰扌开: 扌 stands too high. (Fixed by top-level em positions; to confirm.)
- ⿱⿰生力力: lower part misaligned.
- ⿱⿰生生⿱𠆢虫: ⿱𠆢虫 below should be a flat compressed form. (Teacher 螒; to confirm.)

### Spacing

- ⿲亻言吴, ⿱比乃, ⿱⿰目力皿, ⿲氵方乚, ⿰土⿳厶龰肉, ⿱⿱一丷⿱一八: gaps too tight or too loose.
- ⿰亻⿸广⿳工日小: parts overlap.

### Enclosures (半包圍)

Enclosed parts sit flush against the enclosing part with no gap, in the proportions the font gives
its common enclosures.

- 鬼 ⿷⿱甶儿厶: 厶 belongs in the open lower right of ⿱甶儿, rescaled to fit.
- 斉 ⿵齐二, ⿷匚⿰亻皮, ⿰⿸厂貝頁, ⿰魚⿱⿹勹久缶: room and proportions.
- 看 ⿸龵目, 戒 ⿹戈廾: no gap, good proportions.

### Strokes added, lost or reshaped

- 麺 ⿺麦面 (刂 added), 抹 ⿰扌末 (丿 added), 題 ⿺是頁 (part added), 駅 ⿰馬尺 and 期 ⿰其月
  (丿 lost), 菜 ⿱艹采 (⿱㇒𭕄 lost), ⿰⿱𠆢品攴 (stroke added). (Stroke-count check added; to
  confirm.)
- ⿰訁者, 被 ⿰衤皮: parts drawn as dots.
- 定 ⿱宀𤴓: 宀's bar shortens over what it covers.
- 靴 ⿰革化: 革 on the left does not pass under 化.
- 姿 ⿱次女, 預 ⿰予頁, 醜 ⿰酉鬼: compressed parts keep their dots and hooks well shaped.
- 冬 ⿱夂⺀: the dots sit wrongly.
- 監 ⿱⿰臣⿱𠂉一皿, 貫 ⿱毌貝: strokes too close together.

### Sequences only strokes describe

These need a shape a designer drew, found by its strokes: 丘 ⿱㇒⿺丄丅, 失 ⿰㇒夫, 友 ⿸𠂇又,
炎 ⿱火火, 揺 ⿰扌⿱⿱㇒𭕄𠙻, 図 ⿴囗⿱⺀㐅 (⿱⺀㐅), ⿱⿱一丷⿱一八.

### Components no font here draws

- 𦒱 in ⿰金𦒱: drawn from Plangothic since b7cf9433; its parts still need checking.
- 𠂊 sits with no gap above what follows.
- Sequences with ⿻ and other "not drawn" cases: every sequence should draw.
- ⿰土⿰彳⿺𠃊⿱日安, ⿰扌𡉵: wrong drawing.
