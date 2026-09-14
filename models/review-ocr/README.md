# Review OCR suggestions

Character review uses two local models:

- [NDLkotenOCR-Lite PARSeq](https://github.com/ndl-lab/ndlkotenocr-lite) recognizes text within a crop, including several joined characters. The National Diet Library publishes it under [CC BY 4.0](https://github.com/ndl-lab/ndlkotenocr-lite/blob/ede4283845cdc0ba2bda8b7ebfc3dc80b33c92c8/LICENCE).
- The [Atlas character classifier](../classifier/README.md) supplies alternatives for individual characters.

Download the 42.4 MB sequence model and its alphabet:

```sh
python scripts/fetch_review_ocr.py
```

The fetcher pins revision `ede4283845cdc0ba2bda8b7ebfc3dc80b33c92c8` and verifies SHA-256 checksums.
Files stay in `cache/models/ndlkotenocr-lite/`, outside Git. The licence is downloaded alongside them.
Install the project's `ocr` optional dependencies for ONNX Runtime support.
`ATLAS_OCR_MODEL_DIR` and `ATLAS_CLASSIFIER_MODEL` can select other local model paths.
Restart the review server after changing model files.

`review/suggestions.py` follows the upstream recognizer's rotation, resize, channel order and
normalization. It adds EOS-aware decoding with a few alternate tokens. Suggestions are guesses;
the displayed choices are not independent OCR runs, and their scores are not presented as accuracy.
Only the source pixels enter inference. Existing assigned readings and transcriptions are excluded.

CUDA is preferred when available. Inference is serialized, each session's CUDA arena is capped at
768 MiB, CPU threads are limited to two, and at most 512 crop results are cached. GPU allocations
outside the arenas can add overhead. The cache key includes the source image identity, its file
stamp and the exact crop coordinates. No remote image upload or background collection-wide job runs.

Reporting an error never waits for OCR. Choosing a suggested single character can resolve a wrong
reading. A suggested sequence for joined characters is retained as review evidence; splitting the
crop requires a separate correction. Suggestions alone never change the review journal.
