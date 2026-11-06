# Crop examples

Image source: *かてもの*, 日本古典籍データセット（国文研所蔵）CODH配信. [IIIF manifest](https://codh.rois.ac.jp/char-shape/book/200021851/manifest.json); [character annotations](https://codh.rois.ac.jp/char-shape/book/200021851/character.json). License: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

The images are selected and cropped from page `200021851_00003_2`. They illustrate possible review issues; they are not records of actual review decisions.

- `character.jpg`: region `1822,602,172,78`, annotated か (C0001).
- `merged.jpg`: region `1810,590,200,260`, containing annotated か (C0001) and て (C0002).
- `crop.jpg`: region `1822,602,40,78`, part of annotated か (C0001).
- `blank.jpg`: region `2100,1800,100,100`, blank paper.

Each region is available from the IIIF image service at `https://codh.rois.ac.jp/char-shape/iiif/200021851/200021851_00003_2.tif/{region}/full/0/default.jpg`, substituting its coordinates for `{region}`.
