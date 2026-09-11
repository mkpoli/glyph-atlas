# T04 Reference tables: hentaigana candidates and character equivalence

Goal: the lookups the alignment and the 字母 steps need, from openly licensed tables.

Read first: `data/vocab/hentaigana.tsv`, `scripts/build_hentaigana_table.py`,
`data/sources/mj-hentaigana.yaml`, `data/sources/unicode-ucd.yaml`, `docs/plan.md` section 5.

Outputs
- `scripts/build_mj_table.py`: downloads `MJIH00201-ods.zip`, parses the ODS with `odfpy` or
  `pandas[odf]`, writes `data/vocab/mj-hentaigana.tsv` with columns `mj`, `code_point`, `name`,
  `jibo`, `jibo_code_point`, `readings` (音価１–３ joined by /), `koseki`, `gakujutsu`, `ninjal_url`,
  `note`, and a header comment naming the source, version 002.01, and CC BY-SA 2.1 JP with IPA credit.
- `data/vocab/kanji-equivalents.tsv`: pairs of characters treated as the same for alignment (新字
  and 旧字, common 異体字, compatibility ideographs and their canonical forms), one pair per row with
  `kind` (`shinji-kyuji`, `itaiji`, `compatibility`) and `source`. Use an openly licensed source and
  name it in the header; candidates are the MJ文字情報一覧表 main table (CC BY-SA 2.1 JP) and the
  Unicode decomposition data for compatibility ideographs. Do not copy from a source without a
  stated licence.
- `src/kuzushiji_atlas/refs.py`: `hentaigana()` (rows of both tables joined on code point),
  `candidates(reading: str) -> list[str]` (code points whose readings include the kana, ordinary
  hiragana code point first, then hentaigana in code point order, KA-KE style shared readings
  included), `jibo(code_point) -> str | None`, `modern(code_point) -> str` (the hiragana),
  `equivalents(char) -> set[str]` (the character, its kana forms, its 新旧 and 異体 partners, voiced
  and unvoiced kana, small and full-size kana), `same(a, b) -> bool`.

Edge cases: 𛀁 (U+1B001) and 𛄟 (U+1B11F) have no HENTAIGANA name; readings with several kana
(`ね/こ`); katakana readings map through their hiragana.

Tests: `candidates("か")` returns 13 code points (U+304B and the 12 hentaigana); `jibo("U+1B098")`
is 子; `same("国", "國")`, `same("か", "𛀙")`, `same("は", "ば")` are true; `same("か", "き")` is false;
the MJ table has 299 rows, 286 with a code point.

Acceptance: both TSV files committed with headers; tests pass offline (the ODS download happens
only in the script).

Size: small to medium. Depends on: nothing.
