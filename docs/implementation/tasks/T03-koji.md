# T03 Koji markup parser

Goal: turn a みんなで翻刻 transcription line into characters with structure and raw-text offsets.

Read first: https://wiki.honkoku.org/doku.php?id=howto_markup (current notation),
https://github.com/yuta1984/koji (grammar, MIT) and `koji_notation.md` in the Honkoku-Lines Zenodo
record, https://github.com/mkpoli/honkoku-client/tree/main/packages/markup (a renderer for the
same notation), `docs/schema.md` (lines, units).

Outputs
- `src/kuzushiji_atlas/koji.py`: `parse(text) -> Parsed`. `Parsed.plain` is the text with markup
  removed by the same rule as Honkoku-Lines' `plain_text`. `Parsed.nodes` is a tree: `Element(kind,
  start, end, children, attrs)` and `Text(start, end)`, offsets half-open in code points of the raw
  line. `Parsed.chars` flattens the tree to `Char(text, start, end, path, role)` where `path` is
  the list of ancestor element ids and `role` is `main`, `ruby`, `ruby-left`, `warigaki` (with
  `attrs.column` 1 to 3), `note`, `okurigana`, `kaeriten`, `gap`, `unreadable`, `cancelled`,
  `inserted`.
- Notation, both current and legacy forms: 振り仮名 `漢字（かな）` and `漢字（右｜左）` and
  `《振り仮名：…｜…[｜…]》`; 送り仮名 `￣…`; 返り点 `＿レ`, `＿一` … (also `｛＿レ｝`); 割書 `《割書：a｜b[｜c]》`;
  題 `《題：…》`; 箱 `《箱：…》`; 見せ消ち `《見せ消ち：a｜b》`; 圏点 `《圏点：…｜◯》`; 注記 `【…】`; 判読不能 runs
  of `□` (one `gap` Char each) and `■` (`unreadable`); 場所 `《場所：…》` and legacy `〔…〕`; 人物
  `｛…｝`; 日時 `＜…＞`; 脚注 `＃１０`; block marks `％字下げ一`, `％表紙`; the `<TATE>` token as ー.
  Unknown `《name：…》` keeps its content as `main` with `attrs.unknown = name`.

Edge cases: nesting (振り仮名 inside 割書); unbalanced brackets (literal text, `attrs.malformed`);
ASCII and full-width `|` and `:`; a 割書 that starts mid-line and returns to full width after.

Tests: every example on the notation page; `tests/fixtures/koji-pairs.tsv`, 50 `text` and
`plain_text` pairs copied from `lines.jsonl.gz` with a header naming the source and CC BY-SA 4.0;
offsets map back into the raw string for every Char.

Acceptance: `scripts/check_koji.py` over 10,000 random Honkoku-Lines rows reports `plain` equal to
`plain_text` for at least 99.5%, with the mismatches listed in the pull request.

Size: medium. Depends on: nothing.
