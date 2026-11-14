# Box refiner

A ResNet-18 that looks at a character box with its surroundings and answers how far each edge is
from the character, and the IoU it expects the box to have with it. `glyph_atlas.refine.Refiner`
serves the ONNX export; the cutter runs it on every candidate box before the classifier reads the
crop, and its expected IoU is a feature of both cutter models.

## Data

CODH's training books (`data/splits/codh.tsv`), 4,713 pages. A sample is one ground-truth box moved
off its character: every edge jittered, and about seven in ten moved further the ways a detector's
box is off — pushed into the next character, pulled back to cut the character short, widened,
narrowed, taken around two characters, or thrown somewhere nearby. The target is the box of the
character the moved box overlaps most.

```sh
python models/benchmark/cuts.py build            # the held-out books, for the measurement
python models/benchmark/cuts.py build --train    # the training books
python models/refiner/train.py --steps 4000      # artifacts/refiner.pt, refiner.onnx, metrics.json
```

| Setting | Value |
| --- | --- |
| Model | `timm` ResNet-18, one grey channel, from scratch, 11.2M parameters |
| Input | 112×112: the box with 0.75 of its width and height around it, so the box is always the centre |
| Output | four edge moves in shares of the box, clipped to ±0.75; expected IoU |
| Loss | smooth L1 on the edges of boxes overlapping a character by 0.3, ×4; cross-entropy on the IoU |
| Training | 4,000 steps of 288 boxes, AdamW, one-cycle to 2e-3, bfloat16, 7 minutes on an RTX 5070 Ti |
| Augmentation | brightness and contrast, Gaussian blur on three pages in ten |

## Measured

5,760 moved boxes on 120 pages of CODH's two `val` books (`artifacts/metrics.json`):

| | mean IoU | IoU ≥ 0.7 | IoU ≥ 0.85 |
| --- | ---: | ---: | ---: |
| as given | 0.626 | 37.2% | 11.0% |
| one round | 0.843 | 89.1% | 70.9% |
| two rounds | 0.861 | 91.0% | 76.1% |

The expected IoU is off by 0.069 on average. Adding the refiner to the cutter raised the characters
published at 0.97 precision (IoU 0.7) on the `test` books from 56.9% to 65.8% (`models/cutter`).

## Licence

Code MIT. The training data is CODH's CC BY-SA 4.0 ground truth, and the weights are intended for
release under CC BY-SA 4.0, as the detector's are (`models/detector/README.md`). Weights go to a
GitHub release, not to git.

## Sources

The design follows two ideas known from memory of the papers, which were not read again for this
work: regressing a box a second time from its own crop (Cai and Vasconcelos, "Cascade R-CNN", CVPR
2018, https://arxiv.org/abs/1712.00726) and predicting a box's IoU as its confidence (Jiang et al.,
"Acquisition of Localization Confidence for Accurate Object Detection", ECCV 2018,
https://arxiv.org/abs/1807.11590).
