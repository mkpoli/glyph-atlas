# Runbook

The commands that build the dataset from the upstreams, in the order they run, with what each one
costs and what it leaves behind. Everything below was run on 2026-09-11; the counts are the ones it
printed. Downloads and intermediate tables stay out of git (`cache/`, `work/`, `out/`).

## 1. Reference tables

```sh
uv sync --group dev --extra data     # pytest, ruff, odfpy
python scripts/build_hentaigana_table.py     # data/vocab/hentaigana.tsv   (287 rows)
python scripts/build_mj_table.py             # data/vocab/mj-hentaigana.tsv (299 rows, 286 with a code point)
python scripts/build_kanji_equivalents.py    # data/vocab/kanji-equivalents.tsv (2,026 rows)
```

Both generated tables are committed and byte-identical on a rerun; the scripts are only needed when
the upstream release changes.

## 2. Imports

Each importer is a command, and each validates afterwards. The zips and clones they read are
fetched by `kuzushiji_atlas.net`, which waits 1 s between requests to CODH and Hugging Face and 3 s
between requests to any other host.

```sh
atlas import codh --all                 --out work/codh-full      # 44 books, 61 min
atlas import honkoku-lines              --out work/honkoku-lines  # 1.17M lines, 4 min
atlas import kokatsuji                  --out work/kokatsuji      # 36,869 units, 7 s
atlas import ndl-minhon                 --out work/ndl-minhon     # 641,632 lines, 2 min
atlas import hilab                      --out work/hilab          # listing only, 25 s
atlas import honkoku-data --clone cache/honkoku-data --out work/honkoku-data   # 7,584 documents, 16 min
atlas coverage                          --out work/coverage.tsv
for d in codh-full honkoku-lines kokatsuji ndl-minhon hilab honkoku-data; do
  atlas tables validate work/$d
done
```

`atlas import hilab --download` extracts the 325,261 crops instead of only listing them; it is needed
before a release can materialise HI Lab crops.

## 3. Images

```sh
atlas images info  work/codh-full/pages.parquet          # 6,151 pages sized from info.json
atlas images fetch work/codh-full/pages.parquet          # ~5 GB, bounded by --limit/--document/--pages
atlas images crop  work/kokatsuji/units.parquet --out out/crops
```

The cache is `cache/images/<sha256[:2]>/<sha256>.<ext>` with an index beside it; a fetch never
rewrites a page it already holds.

## 4. Detector data and the models

```sh
python scripts/build_codh_split.py                       # data/splits/codh.tsv: 4 test, 2 val books
python scripts/build_detector_data.py --materialise      # 53,238 tiles, 7.8 GB, 7.5 min
uv run python models/detector/train.py                   # RT-DETR, config models/detector/config.yaml
uv run python models/detector/train.py --test --weights models/detector/artifacts/best.pt
uv run python models/detector/export_onnx.py             # ONNX for detect.py
python models/classifier/build_manifests.py              # 917,309 / 44,782 / 124,196 crops
uv run python models/classifier/train.py                 # ConvNeXt-tiny, 96x96 grey
uv run python models/classifier/export_onnx.py           # ONNX for classify.py
```

`models/detector/SPIKE.md` records why the detector is RT-DETR: mmdetection has no mmcv wheel for
this interpreter and its source build fails on a C++ standard older than the installed torch asks
for. The GPU is 16 GB; the detector peaks at 7.9 GiB and the classifier at 1.2 GiB, and they are run
one at a time.

## 5. Alignment

```sh
atlas align work/honkoku-lines --run pilot-v1 --pages hl:...:0,hl:...:1
```

The run configuration names the two ONNX exports, the equivalence policy and the transition weights;
its fingerprint is part of every unit id it writes, so a rerun with a changed configuration adds
units rather than overwriting them, and units a reviewer wrote are never touched.

## 6. Pilot

```sh
atlas pilot images work/honkoku-lines --group calibration          # the 20 calibration pages
atlas pilot images work/honkoku-lines --group heldout --per-item 10
atlas pilot export /tmp/pilot-calibration --group calibration      # one package per page
atlas review serve /tmp/pilot-calibration/hl_..._0 --port 8770     # the interface and the API
atlas review apply /tmp/pilot-calibration/hl_..._0                 # tables and reviews.jsonl
atlas review replay /tmp/pilot-calibration/hl_..._0                # rebuild the state and check it
atlas eval alignment --truth data/pilot/truth/heldout --pred work/honkoku-lines
```

A package holds the page image, `page.json`, `lines.parquet` and `units.parquet`; the review service
loads one directly, and its page record carries the checksum of the image the package ships, so the
service answers with the local file.

## 7. Rights and release

```sh
atlas rights resolve    work/honkoku-lines --limit 40      # manifest evidence, bounded fetches
atlas rights report     work/honkoku-lines --out out/rights.md
atlas rights attribution work/honkoku-lines --out ATTRIBUTION.md
atlas export work/kokatsuji work/hilab --out out/0.1 --crops --review transcriber --include-machine
```

`atlas export` merges the inputs, applies the licence, review and crop filters, appends the
normalisation policy's columns and writes `ATTRIBUTION.md`, `COUNTS.md`, `datasheet.md`, `README.md`,
`zenodo.json`, `CHECKSUMS.txt` and `MANIFEST.json`. A release with no admitted unit fails loudly
rather than writing an empty directory. The default review filter admits only reviewed units, of
which there are none until the pilot is annotated.

## 8. Audit

```sh
atlas audit sample work/<run> --n 2000 --strata document,script --seed 0 --run <name>
atlas audit report work/<run> --sample <sample id> --out docs/reports/audit-<sample id>.md
```

A sampled unit carries its inclusion probability and the machine fields the interface must hide; a
unit in a sample is never drawn again and is documented as unusable for tuning.

## What each step costs

| Step | Wall clock | Disk |
| --- | --- | --- |
| `import codh --all` | 61 min (44 downloads) | 7.5 GB of zips, then 5.1 GB of cached page images |
| `import honkoku-lines` | 4 min, 2.6 GB peak RSS | 178 MB |
| `build_detector_data --materialise` | 7.5 min | 7.8 GB |
| detector training, one epoch | 28–38 min | 241 MB a checkpoint |
| classifier training, one epoch | ~11 min | 116 MB a checkpoint |
| `atlas export --crops`, 36,869 units | ~20 min | 41 MB |
