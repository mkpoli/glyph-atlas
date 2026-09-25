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
| `atlas-reviewed` | 158 | crops whose character a person confirmed on the review site (`reviewed.py`) |

The crops the atlas extracted from transcriptions (`ex:` ids) are not a test set. The extraction
kept a crop only when the recognizers agreed with the transcription
(`glyph_atlas.extraction_queue`), so those labels favour the models that chose them.

`atlas-reviewed` is small, and most of it is `ex:` crops, so it serves as a check on the site's
own images rather than a ranking.

## Models

- `atlas`: the served Atlas classifier (`models/classifier/artifacts/classifier.onnx`).
- `ndl`: NDLkotenOCR-Lite's PARSeq line model reading one crop; `ndl-pad` pads the crop onto a
  line-shaped canvas instead of stretching it.
- `metom`: SakanaAI's Metom (https://huggingface.co/SakanaAI/Metom, Apache-2.0), a ViT over 2,703
  CODH characters. It was trained on a random split of all of CODH, so it has seen crops of CODH's
  test books; its `codh-test` score is not a held-out measure.
- `onnx:PATH`: any exported classifier with its `classes.json` beside it.
- Merged lists: `served` is NDL's answers followed by the classifier's (the panel's order before
  `suggestions.rank`); `interleave` alternates the classifier's and NDL's, classifier first (the
  order `rank` gives); `atlas+metom` alternates the classifier's and Metom's.

```
uv run python models/benchmark/bench.py --set codh-test --limit 8000 --model atlas ndl served interleave
METOM=path/to/snapshot uv run --with einops python models/benchmark/bench.py --set hilab-test --model metom
```

## Results (2026-09-25)

Top-1 / top-5, percent. `results/` holds every breakdown by script.

| model | codh-test (8,000) | hilab-test | atlas-reviewed |
| --- | --- | --- | --- |
| `atlas` | 88.4 / 92.9 | 46.7 / 59.8 | 84.8 / 88.0 |
| `ndl` | 52.6 / 61.4 | 16.3 / 21.3 (8,000) | 73.4 / 77.2 |
| `ndl-pad` | 44.0 / 50.4 | | |
| `served` | 52.6 / 94.7 | 16.3 / 59.2 (8,000) | 73.4 / 93.0 |
| `interleave` | 88.4 / 94.9 | 46.6 / 59.9 (8,000) | 84.8 / 93.0 |
| `metom` | 95.5 / 98.4 (seen in training, all 45,981) | 41.4 / 61.3 | 82.3 / 94.3 |
| `atlas+metom` | | 46.7 / 64.4 | 84.8 / 93.7 |
