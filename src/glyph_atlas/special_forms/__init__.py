"""Special forms of one character: its crops grouped by stroke shape, so abbreviated forms can be named.

Some characters have abbreviations that no code point covers, such as the ん-like and zigzag forms of 候
in Japanese manuscripts. The site's form clusters (`form_clusters`) cannot separate them: they embed a crop
with the character classifier, which was trained to give every way of writing a character the same answer,
so what is left between its clusters is partly form and partly paper, crop box and ink weight. This package
regroups one family's crops crop by crop:

1. `crops`: the family's crops, at most 160 px on the long side, with their site cluster and classifier
   embedding (`crops.npz`).
2. `ink`: each crop framed at 96 px with its ink mask and a quality score (`framed.npz`).
3. `restore`: a U-Net trained on the crisp crops, made worse on purpose, returns every crop as clean ink
   (`restored.npy`), which is trimmed and refitted (`ink.npy`).
4. `encoder`: two contrastive encoders trained on the restored ink alone, so that position, size, slant,
   ink weight and texture stop counting (`features.npy`).
5. `tree`: UMAP to ten dimensions and a Ward tree over every crop, cut into shape clusters (`tree.npz`).
6. `decide`: the forms a person named in `data/forms/special/<code>.yaml`. The classifier, which reads the
   character well, decides written character or abbreviation; the shape clusters sort the abbreviations
   (`forms.tsv`).
7. `report`: one HTML page with the forms, the tree, a gallery, the site clusters against the forms crop by
   crop, a 3-D map and the method.

Every step writes below `work/special-forms/<code>/` and is skipped when its output is there, so a report
can be rebuilt without training again. Training is random on the GPU: shape cluster numbers hold for one
run, and the decisions file names the run it was written for.
"""
from __future__ import annotations

from pathlib import Path

#: Where each family's run is written.
WORK = Path("work/special-forms")
#: The decisions a person made, one file per family.
DECISIONS = Path("data/forms/special")
#: Shape clusters the tree is cut into.
LEAVES = 24


def run_dir(code: str, base: Path = WORK) -> Path:
    return base / code
