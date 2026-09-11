# T03 Koji markup parser

Goal: turn a みんなで翻刻 transcription line into characters with roles and raw-text offsets.

Read first: https://wiki.honkoku.org/doku.php?id=howto_markup (notation reference),
https://github.com/yuta1984/koji (grammar, MIT), `~/projects/Philology/honkoku-client/packages/markup`
(a renderer for the same markup), `docs/schema.md` (lines, units).

Outputs
- `src/kuzushiji_atlas/koji.py`: `parse(text) -> Parsed` with `Parsed.plain` (markup removed, same
  rule as Honkoku-Lines' `plain_text`) and `Parsed.chars`, a list of `Char(text, start, end, role,
  flags)` where `start`/`end` are code point offsets into the raw line, `role` is one of `main`,
  `ruby`, `warigaki-1`, `warigaki-2`, `warigaki-3`, `note`, `okurigana`, `kaeriten`, and `flags` may
  contain `gap` (from □, one Char per box), `unreadable` (■), `cancelled` (the first segment of
  見せ消ち), `inserted` (its second segment), `title`, `person`, `place`, `date`, `emphasis`.
- Elements handled: `｛…｝` 人物, `〔…〕` 場所, `＜…＞` 日時, `《題：…》`, `《割書：a｜b[｜c]》`,
  `未（いまだ｜ズ）` and `《振り仮名：…｜…[｜…]》`, `《圏点：…｜﹅》`, `｛＿レ｝` and `＿一` 返り点, `￣…`
  送り仮名, `《見せ消ち：a｜b》`, `【…】` 注釈, `＃１０` 脚注, `□` and `■` runs, `％字下げ一`, `％表紙`,
  `《箱：…》`. Unknown `《name：…》` elements keep their content as `main` and add flag `unknown:name`.
- `plain` must equal Honkoku-Lines' `plain_text` for the same `text`; where the rule is unclear, the
  Honkoku-Lines pair is the reference.

Edge cases: nested elements (振り仮名 inside 割書); unbalanced brackets (parse as literal text, flag
`malformed`); full-width and ASCII variants of `|` and `:`; the `<TATE>` token rendered as ー.

Tests: every example from the notation page; a fixture of 50 `text`/`plain_text` pairs copied from
`lines.jsonl.gz` (a small file under `tests/fixtures/`, CC BY-SA 4.0 noted in its header) must
round-trip; offsets map back into the raw string.

Acceptance: on 10,000 random Honkoku-Lines rows (command, not test), `plain` matches `plain_text`
for at least 99.5%; the mismatches are listed in the pull request.

Size: medium. Depends on: nothing.
