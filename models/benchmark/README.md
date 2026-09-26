# Single-crop recognizer benchmark

`bench.py` measures how well a recognizer names the character in one crop, the way the review
panel uses it: up to five answers, best first. A crop counts at k when its character is among the
first k answers; a character the recognizer cannot name counts as a miss. `family` also accepts
the other form of a 新字・旧字 pair, because CODH merged those forms.

## Sets

| set | crops | what it is |
| --- | ---: | --- |
| `codh-test` | 45,981 | CODH's four test books (`data/splits/codh.tsv`): every kanji crop and 12,000 of the others |
| `hilab-test` | 37,606 | HI Lab crops in the held-out tenth of `hilab_split`, drawn by blocks of 200 ids |
| `atlas-reviewed` | 168 | crops whose character a person confirmed (119) or corrected (49) on the review site (`reviewed.py`) |

The crops the atlas extracted from transcriptions (`ex:` ids) are not a test set. The extraction
kept a crop only when the recognizers agreed with the transcription
(`glyph_atlas.extraction_queue`), so those labels favour the models that chose them.

`atlas-reviewed` is small, and most of it is `ex:` crops, so it serves as a check on the site's
own images rather than a ranking. Its `corrected` group holds the crops a person relabelled, where
a suggestion matters most.

## Models

- `atlas`: the served Atlas classifier (`models/classifier/artifacts/classifier.onnx`).
- `ndl`: NDLkotenOCR-Lite's PARSeq line model reading one crop; `ndl-pad` pads the crop onto a
  line-shaped canvas instead of stretching it.
- `metom`: SakanaAI's Metom (https://huggingface.co/SakanaAI/Metom, Apache-2.0), a ViT over 2,703
  CODH characters. It was trained on a random split of all of CODH, so it has seen crops of CODH's
  test books; its `codh-test` score is not a held-out measure.
- `soramaru`: Soramaru's classifier (https://huggingface.co/yuta1984/soramaru_kuzushiji_ai,
  CC BY-SA 4.0), a ConvNeXt-tiny at 384×384 over 3,673 characters by Yuta Hashimoto (yuta1984),
  trained on the Kaggle Kuzushiji Recognition pages and the HI Lab crops. Its README states no split
  of the HI Lab crops, so `hilab-test` may overlap its training data. The Kaggle data differs from
  CODH's dataset (https://codh.rois.ac.jp/competition/kaggle/), so whether `codh-test` overlaps it
  is uncertain. `soramaru` crops the centre square, as its demo does; `soramaru-pad` pads to a white
  square instead.
- `onnx:PATH`: any exported classifier with its `classes.json` beside it.
- Merged lists: `served` is NDL's answers followed by the classifier's (the panel's order before
  `suggestions.rank`); `interleave` alternates the classifier's and NDL's, classifier first (the
  order `rank` gives); `atlas+metom` and `atlas+soramaru` alternate the classifier's answers
  with Metom's or Soramaru's.

```
uv run python models/benchmark/bench.py --set codh-test --limit 8000 --model atlas ndl served interleave
METOM=path/to/snapshot uv run --with einops python models/benchmark/bench.py --set hilab-test --model metom
```

## Results (2026-09-26)

Top-1 / top-5, percent. `results/` holds every breakdown by script.

| model | codh-test (8,000) | hilab-test | atlas-reviewed | corrected (49) |
| --- | --- | --- | --- | --- |
| `atlas` | 88.4 / 92.9 | 46.7 / 59.8 | 83.3 / 86.3 | 55.1 / 63.3 |
| `ndl` | 52.6 / 61.4 | 16.3 / 21.3 (8,000) | 70.8 / 75.0 | 49.0 / 61.2 |
| `ndl-pad` | 44.0 / 50.4 | | | |
| `served` | 52.6 / 94.7 | 16.3 / 59.2 (8,000) | 70.8 / 91.1 | 49.0 / 79.6 |
| `interleave` | 88.4 / 94.9 | 46.6 / 59.9 (8,000) | 83.3 / 91.1 | 55.1 / 79.6 |
| `metom` | 95.5 / 98.4 (seen in training, all 45,981) | 41.4 / 61.3 | 81.5 / 94.6 | 67.3 / 85.7 |
| `atlas+metom` | | 46.7 / 64.4 | 83.3 / 93.5 | 55.1 / 81.6 |

## Backbones (2026-09-26)

Each backbone trained on the same CODH + HI Lab manifests (`models/classifier/build_combined.py`,
3,428 classes) for two epochs of half the training crops each, with the same optimiser and
brightness-contrast jitter, then measured here. Top-1 / top-5, percent; `atlas-reviewed` had 168
crops, 49 of them corrected.

| backbone | licence | input | codh-test | hilab-test | atlas-reviewed | corrected |
| --- | --- | ---: | --- | --- | --- | --- |
| ConvNeXt-tiny, `fb_in22k_ft_in1k` | Apache-2.0 | 96 | 88.2 / 95.9 | 71.4 / 86.9 | 84.5 / 93.5 | 67.3 / 83.7 |
| ConvNeXt-tiny, `fb_in22k_ft_in1k` | Apache-2.0 | 128 | 88.8 / 96.2 | 72.4 / 87.6 | 87.5 / 94.0 | 71.4 / 85.7 |
| ConvNeXt V2-tiny, `fcmae_ft_in22k_in1k` | CC BY-NC 4.0 | 96 | 88.8 / 96.0 | 72.6 / 87.9 | 86.9 / 94.0 | 67.4 / 85.7 |
| ConvNeXt-tiny, `dinov3_lvd1689m` | DINOv3 Licence | 96 | 90.0 / 96.6 | 73.9 / 88.7 | 87.5 / 93.5 | 71.4 / 83.7 |
| DINOv2 ViT-S/14 reg4, `lvd142m` | Apache-2.0 | 112 | 89.2 / 96.3 | 72.0 / 87.8 | 86.9 / 94.6 | 69.4 / 85.7 |
| EVA-02 Small, `mim_in22k` | MIT | 112 | 88.6 / 96.1 | 70.8 / 86.8 | 86.9 / 93.5 | 71.4 / 83.7 |
| DeiT III Small, `fb_in22k_ft_in1k` | Apache-2.0 | 128 | 88.4 / 96.0 | 69.9 / 86.5 | 83.3 / 93.5 | 69.4 / 85.7 |
| CAFormer-S18, `sail_in22k_ft_in1k` | Apache-2.0 | 96 | 89.5 / 96.5 | 73.4 / 88.7 | 88.1 / 94.0 | 73.5 / 85.7 |
| CAFormer-S36, `sail_in22k_ft_in1k` | Apache-2.0 | 96 | 89.7 / 96.5 | 74.6 / 89.1 | 86.9 / 92.3 | 69.4 / 79.6 |
| CAFormer-S18, `sail_in22k_ft_in1k` | Apache-2.0 | 128 | 90.1 / 96.7 | 74.2 / 88.9 | 87.5 / 94.6 | 67.3 / 83.7 |

ConvNeXt V2's weights forbid commercial use and the DINOv3 licence requires a "Built with DINOv3"
notice, so neither is served. The CAFormer-S18 at 128, trained for ten full epochs, is the served
classifier (`models/classifier/README.md`); its results are in `results/final-caformer128-*.json`:
92.3 / 97.1 on `codh-test`, 80.9 / 91.7 on `hilab-test`, 89.9 / 95.2 on `atlas-reviewed` and
69.4 / 85.7 on its corrected crops.

## Soramaru (2026-09-27)

The served CAFormer (`atlas`) against Soramaru, top-1 / top-5, percent:

| model | codh-test | hilab-test | atlas-reviewed | corrected (49) |
| --- | --- | --- | --- | --- |
| `atlas` | 92.3 / 97.1 | 80.9 / 91.7 | 89.9 / 95.2 | 69.4 / 85.7 |
| `soramaru` | 86.2 / 95.0 | 94.4 / 98.0 (may overlap its training) | 84.5 / 94.6 | 59.2 / 87.8 |
| `soramaru-pad` | | | 75.0 / 86.9 | 49.0 / 73.5 |
| `atlas+soramaru` | 92.3 / 98.3 | 80.9 / 98.1 | 89.9 / 95.8 | 69.4 / 87.8 |

On `codh-test` hiragana, `atlas` reads 93.2 / 99.5 and `soramaru` 77.6 / 94.6. The full breakdowns
are in `results/soramaru-*.json`.
