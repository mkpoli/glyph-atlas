# Crop examples

Image source: *かてもの*, 日本古典籍データセット（国文研所蔵）CODH配信. [IIIF manifest](https://codh.rois.ac.jp/char-shape/book/200021851/manifest.json); [character annotations](https://codh.rois.ac.jp/char-shape/book/200021851/character.json). License: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

The images are selected and cropped from page `200021851_00003_2`. They illustrate possible review issues; they are not records of actual review decisions.

| File | Region | Contents |
| --- | --- | --- |
| `character-kana.jpg` | `1822,602,172,78` | か (C0001), labelled て in the example. |
| `character-kanji.jpg` | `245,1860,135,150` | 干 (C0114), labelled 千 in the example. |
| `merged-vertical.jpg` | `1810,590,200,260` | か (C0001) and て (C0002), one above the other. |
| `merged-columns.jpg` | `520,1620,350,195` | く (C0097) and 知 (C0078), side by side. |
| `crop-cut.jpg` | `1822,602,40,78` | Part of か (C0001), cut through the glyph. |
| `crop-edge.jpg` | `140,745,275,190` | 植 (C0106) with the frame line and neighbouring strokes. |
| `blank-paper.jpg` | `2100,1800,100,100` | Blank paper. |
| `blank-mark.jpg` | `1508,950,135,145` | A brown stain on the paper. |

Each region is available from the IIIF image service at `https://codh.rois.ac.jp/char-shape/iiif/200021851/200021851_00003_2.tif/{region}/full/0/default.jpg`, substituting its coordinates for `{region}`.
