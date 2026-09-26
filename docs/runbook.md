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
python scripts/build_mj_kanji_table.py       # data/vocab/mj-kanji.tsv (58,862 rows, 58,859 with a code point)
```

The generated tables are committed and byte-identical on a rerun; the scripts are only needed when
the upstream release changes.

**Running the tests behind a proxy.** The suite serves its own pages from `127.0.0.1`, and `httpx`
reads the proxy environment. A machine that sets `all_proxy` to a SOCKS URL needs the proxy variables
cleared for the run, or `httpx` reaches for a SOCKS transport it may not have installed; one that
puts a bracketed IPv6 entry such as `[::1]` in `NO_PROXY` fails earlier, because `httpx` parses it as
a port and raises `InvalidURL` before any test starts. Neither is a defect in this repository:

```sh
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
    -u NO_PROXY -u no_proxy .venv/bin/python -m pytest -q
```

## 2. Imports

Each importer is a command, and each validates afterwards. The zips and clones they read are
fetched by `glyph_atlas.net`, which waits 1 s between requests to CODH and Hugging Face and 3 s
between requests to any other host.

```sh
atlas import codh --all                 --out work/codh-full      # 44 books, 61 min
atlas import honkoku-lines              --out work/honkoku-lines  # 1.17M lines, 4 min
atlas import kokatsuji                  --out work/kokatsuji      # 36,869 units, 7 s
atlas import ndl-minhon                 --out work/ndl-minhon     # 641,632 lines, 2 min
atlas import hilab                      --out work/hilab          # listing only, 25 s
atlas import honkoku-data --clone cache/honkoku-data --out work/honkoku-data   # 7,584 documents, 16 min
atlas import hng                        --out work/hng            # 49,786 crops, 63 documents, 19 s
atlas import hng-kiridashi              --out work/hng-kiridashi  # 10,307 boxes on 26 Gallica pages, 2 s
atlas coverage                          --out work/coverage.tsv
for d in codh-full honkoku-lines kokatsuji ndl-minhon hilab honkoku-data hng hng-kiridashi; do
  atlas tables validate work/$d
done
```

`atlas import hilab --download` extracts the 325,261 crops instead of only listing them; it is needed
before a release can materialise HI Lab crops.

The two HNG importers read clones at the commits their source files pin, and refuse a clone at any
other commit. `atlas import hng-kiridashi` also reads `cache/hng-basic-data` (or `--basic`) to link
each box to its representative crop:

```sh
git clone https://github.com/chise/hng-basic-data cache/hng-basic-data
git -C cache/hng-basic-data checkout e2174a30844b8100c34af1c0dbe1e301f186883e
git clone https://github.com/chise/hng-kiridashi-data cache/hng-kiridashi-data
git -C cache/hng-kiridashi-data checkout 346bc74071b9a8393b841b171bbdd6c8e82774a7
```

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

Transcription whitespace retains a token without a box and cannot consume a detection through
matching or splitting. The algorithm version participates in the run fingerprint, so corrected
placements have distinct ids from historical crops. Rerunning an older configuration adds the new
run's units while preserving the old run and its review evidence. Historical bad crops still need
quality filtering or an explicit repair; a rerun alone does not retire them.

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
atlas audit report work/<run> --sample <sample id> --out work/<run>/audit-<sample id>.md
```

A sampled unit carries its inclusion probability and the machine fields the interface must hide; a
unit in a sample is never drawn again and is documented as unusable for tuning.

## 9. Release

`CITATION.cff` has no `doi` yet: the concept DOI is minted at the first Zenodo deposit. The steps:

1. Run `atlas export` for the first release and check the release directory, including
   `MANIFEST.json`, `CHECKSUMS.txt` and the datasheet.
2. Deposit the release directory on Zenodo. The upload carries the dataset licence CC BY-SA 4.0 and
   the metadata JSON that `atlas export` writes; Zenodo mints a DOI for the version and a concept
   DOI for the record that covers later versions.
3. Add the concept DOI to `CITATION.cff` as `doi:`, and add the version DOI to the release notes and
   to the Zenodo metadata JSON of the following build.
4. Run `.venv/bin/cffconvert --validate` and commit `CITATION.cff`.

## What each step costs

| Step | Wall clock | Disk |
| --- | --- | --- |
| `import codh --all` | 61 min (44 downloads) | 7.5 GB of zips, then 5.1 GB of cached page images |
| `import honkoku-lines` | 4 min, 2.6 GB peak RSS | 178 MB |
| HNG basic dataset clone | minutes, network-bound | 3.1 GB checkout, 1.5 GB history |
| `build_detector_data --materialise` | 7.5 min | 7.8 GB |
| detector training, one epoch | 28–38 min | 241 MB a checkpoint |
| classifier training, one epoch | ~11 min | 116 MB a checkpoint |
| `atlas export --crops`, 36,869 units | ~20 min | 41 MB |
