# Development

Conventions for contributing to the repository.

## Conventions

- Python 3.12, `uv`, `ruff`, `pytest`. Run `uv sync --all-extras --group dev`, then
  `uv run ruff check src tests scripts` and `uv run pytest -q` before opening a pull request.
- One logical change per branch and per pull request, branch `feat/<slug>` from `main`, title
  `feat(honkoku-lines): import Honkoku-Lines`. CI runs lint and tests on every pull request.
- Every record goes through the models in `src/glyph_atlas/schema.py` and every table through
  `tables.write`. Update `docs/schema.md` in the same pull request as a schema change.
- Ids of imported records are deterministic from the upstream identity (see `docs/schema.md`), so a
  second import produces byte-identical tables.
- Network access happens only in commands, never in tests. Tests use small synthetic fixtures built
  in `tmp_path`; a local HTTP server fixture stands in for remote hosts.
- Downloads go under `cache/`, intermediate tables under `work/<source>/`, releases under `out/`.
  None of these directories is committed. Data files in `data/` are small tables with a header line
  naming their source and licence.
- Requests to institutional servers carry the User-Agent
  `glyph-atlas (+https://github.com/mkpoli/glyph-atlas)` and wait at least 3 seconds
  between requests to the same host; CODH and Hugging Face get 1 second. Retries back off
  exponentially and give up after five attempts.
- Upstream inputs are pinned: a zip by size and checksum, a Hugging Face file by revision, a git
  clone by commit; the pin is written into the source file under `data/sources/` on first import.
- Counts that the upstream publishes are acceptance criteria: an importer that returns a different
  count is a bug until the difference is explained in the pull request.
- The pull request description carries the command that was run and the counts it printed.
- Prose in docs and comments states facts about the subject. No hype adjectives, no "not A but B"
  constructions, no notes about the process that produced the text.

## Hardware

One workstation: 16 CPU cores, 47 GB RAM, one GPU with 16 GB VRAM (Blackwell generation, needs a
PyTorch build with CUDA 12.8 or later), 148 GB free disk. Training runs state their memory budget;
image caches are large (CODH pages about 7 GB; the Honkoku-Lines bundled line crops are 103 GB and
are never fetched whole: pilot pages come from the holders' IIIF servers as full pages), so a command
that fetches images takes `--limit`, `--document` or `--pages` options and never assumes the whole
corpus is local. Tiles and crops are generated on demand; checkpoints and caches stay outside git.
