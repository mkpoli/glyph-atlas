# Kuzushiji Atlas

A character-shape dataset (字形データセット) of pre-modern Japanese writing. Each record is one graphic
occurrence, in most cases one written character on a page image: its rectangle, the transcriber's text, a diplomatic reading, the Unicode
code points including hentaigana, the 字母 of kana forms, a variant key for kanji, and the document's
production type, genre, register and date. The data licence is CC BY-SA 4.0, with the rights of every
image and text recorded per record so that a user can take the subset whose terms fit.

The material comes from open transcriptions aligned to open page images (みんなで翻刻, Japanese
Wikisource) and from existing character-level datasets (CODH, 東京大学史料編纂所, NDL). What the
existing sets lack, and this one records, is the 字母 and code point of each kana form, kanji variants
as written, and bibliographic metadata usable for filtering by period, genre and script style.

Status: planning. `docs/plan.md` has the survey of existing datasets, the goals and the pipeline;
`docs/schema.md` the data model; `docs/licensing.md` how upstream licences compose;
`data/sources/` one file per upstream with its licence evidence and format;
`docs/implementation/` the task cards, one per unit of work, with their dependencies.

## Layout

- `data/sources/` upstream registry (YAML), `data/vocab/` controlled vocabularies.
- `docs/` plan, schema, licensing, decisions.
- `src/kuzushiji_atlas/` the `atlas` command: registry, schema models, importers.
- `tests/` unit tests.
- `cache/`, `work/`, `out/` fetched images, intermediate files and releases, kept out of git.

## Development

```sh
uv sync --all-extras --group dev
uv run pytest
uv run atlas sources
```

## Licences

Code: MIT (`LICENSE`). Dataset records and annotations produced here: CC BY-SA 4.0 (`LICENSE-DATA`).
Upstream images and texts keep their own terms, listed in `data/sources/` and carried on each record.
