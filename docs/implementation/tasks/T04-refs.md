# T04 Reference tables: hentaigana candidates and character equivalence

Goal: the lookups the alignment and 字母 steps use, from openly licensed tables, with every matching
policy named and versioned.

Read first: `data/vocab/hentaigana.tsv`, `scripts/build_hentaigana_table.py`,
`data/sources/mj-hentaigana.yaml`, `data/sources/unicode-ucd.yaml`, `docs/plan.md` section 5.

Representation: characters are passed as literal strings; code points as `U+XXXX` strings.

Outputs
- `scripts/build_mj_table.py`: downloads `MJIH00201-ods.zip`, reads the ODS (`odfpy`), writes
  `data/vocab/mj-hentaigana.tsv` with `mj`, `code_point`, `name`, `jibo`, `jibo_code_point`,
  `readings` (音価１–３ joined by `/`), `koseki`, `gakujutsu`, `ninjal_url`, `note`, and a header
  comment naming the source, version 002.01, CC BY-SA 2.1 JP, IPA credit.
- `data/vocab/kanji-equivalents.tsv`: rows `a`, `b`, `kind`, `source`. Kinds: `compatibility`
  (CJK compatibility ideograph and its canonical decomposition, from `UnicodeData.txt`, Unicode
  License v3), `shinji-kyuji` and `itaiji` from the MJ文字情報一覧表 main table (CC BY-SA 2.1 JP),
  using the columns that relate an MJ glyph to its JIS X 0213 and 常用漢字 counterparts; the exact
  column names are recorded in the file header after download. No row from a source without a
  stated licence.
- `src/kuzushiji_atlas/refs.py`: `hentaigana()` (both tables joined on code point);
  `candidates(reading) -> list[str]` (the ordinary hiragana code point first, then every hentaigana
  whose readings include the kana, shared-reading letters such as KA-KE included, in code point
  order); `jibo(code_point) -> str | None`; `readings(code_point) -> list[str]`;
  `equivalents(char, policy) -> set[str]` and `same(a, b, policy) -> bool`, where `policy` is the
  name of a row in `data/vocab/equivalence-policies.yaml` (`align-v1`: kana forms of one reading,
  compatibility, shinji-kyuji, itaiji, voiced and unvoiced kana, small and full-size kana;
  `strict`: compatibility only). Policies are versioned and never edited in place.

Edge cases: 𛀁 and 𛄟 have no HENTAIGANA name; readings with several kana (`ね/こ`); katakana map
through hiragana; a reading absent from every table returns `[]`.

Tests: `candidates("か")` has 13 entries and starts with `U+304B`; `jibo("U+1B098") == "子"`;
`readings("U+1B098") == ["ね", "こ"]`; under `align-v1` `same("国", "國")`, `same("か", "𛀙")`,
`same("は", "ば")` hold and `same("か", "き")` does not; under `strict` `same("は", "ば")` fails;
the MJ table has 299 rows, 286 with a code point.

Acceptance: the three data files committed with headers; tests pass offline.

Size: small to medium. Depends on: nothing.
