"""Attestation dates: reading a date as a source writes it, converting 和暦, choosing what to show.

A source writes a date the way its cataloguers do: 寛政三年, 天明7 [1787], 文化年間, [18--], c. 827-835,
平安時代・12世紀. `read` finds what such a text states, and `interval` turns it into years, converting
Japanese era dates through a `Calendar`. The years are proleptic Gregorian; an era year is placed in the
Gregorian year its first day falls in (寛政3年 began on 1791-02-03 and is 1791), the convention of the
catalogues themselves, and the converted span is kept with the claim. A text that states no date this
module can read keeps no years: the claim shows its words and the document stays undated.

Japanese calendar dates are converted by HuTime (`HuTime`, `data/sources/hutime.yaml`): era names, era
years and lunisolar days with their leap months. Its answers are cached under `cache/hutime/`, so a
build asks each question once.

`resolve` picks, from all of a document's claims, the date of the witness (the copy itself) and the date
the text was composed; `label` writes a resolved date compactly (1791, c. 1800, 1789–1801, 18th c.).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from . import net
from .schema import DateClaim

#: The kinds that date a witness, best first, by how it was made. A printed book is dated by its
#: printing; a manuscript by its copying, then by what its colophons say.
WITNESS_ORDER = {
    "handwritten": ("copied", "colophon", "produced", "annotated", "printed", "edition"),
    "printed": ("edition", "printed", "produced", "colophon", "copied", "annotated"),
    "unknown": ("copied", "edition", "printed", "produced", "colophon", "annotated"),
}
#: Tiers in the order a disagreement is settled.
TIER_ORDER = ("attested", "editorial", "derived")
#: Sources in the order a disagreement within one tier is settled: a holder's own catalogue first.
SOURCE_ORDER = ("iiif-manifests", "kokusho", "ndl-minhon", "honkoku-data", "ainu-records")
#: The version of `resolve`, published with each resolved date.
RESOLVER = "dates-1"

_KANJI_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUMBER = r"(?:元|\d+|[〇零一二三四五六七八九十]+)"
#: What may follow an era year: 年, a date's own words (刊, 写, 序 …), a bracket, a separator or the end.
#: 文化三巻 is volume three of a work named 文化…, no date.
_YEAR_END = r"(?=年|[刊写寫序跋成版板奥奧識以頃ご後再増補新印中・、，,。(（)）\[\]［］/\-~〜～\s]|$)"
#: An era year after its era's name, with an optional month and day.
_AFTER_ERA = re.compile(rf"(?P<year>{_NUMBER}){_YEAR_END}(?:年)?(?:(?P<leap>閏)?(?P<month>{_NUMBER})月(?:(?P<day>{_NUMBER})日)?)?")
#: 同二 in a list of editions: the era named before.
_SAME_ERA = re.compile(rf"(?:(?<=^)|(?<=[、，,・\s]))同(?P<year>{_NUMBER}){_YEAR_END}")
_SAME_ERA_RANGE = re.compile(rf"\s*[-–~〜～]\s*(?P<year>{_NUMBER})年?(?![\d\u3400-\u9fff])")
_CJK = re.compile(r"[\u3400-\u9fff々]")
#: NDL's abbreviated modern eras: 明17.6, 昭11.
_ABBREVIATED = {"明": "明治", "大": "大正", "昭": "昭和", "平": "平成"}
_ABBREVIATED_YEAR = re.compile(r"(?<![㐀-鿿])(?P<era>[明大昭平])(?P<year>\d{1,2})(?:\.(?P<month>\d{1,2}))?(?!\d)")
_ERA_SPAN = re.compile(r"(?P<eras>[㐀-鿿]{2,4}(?:[・、][㐀-鿿]{2,4})*)(?:年間|中)")
_STATED_YEAR = re.compile(r"[(\[（［]\s*(?:C\.?E\.?\s*)?(?P<year>\d{3,4})\s*[)\]）］]")
_ISO_DAY = re.compile(r"(?<!\d)(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)")
_ISO_MONTH = re.compile(r"(?<!\d)(?P<y>\d{4})[-.](?P<m>\d{1,2})(?![\d-])")
_RANGE = re.compile(r"(?<!\d)(?P<a>\d{3,4})\s*[-–—~〜～]\s*(?P<b>\d{2,4})(?![\d-])")
_CENTURY_DASHES = re.compile(r"(?<!\d)(?P<c>\d{2})--(?!\d)")
_DECADE_DASH = re.compile(r"(?<!\d)(?P<d>\d{3})-(?![\d-]|\s*\d)")
#: A bare year, never a count: 200丁, 3巻 and 106コマ are leaves, volumes and frames.
_YEAR = re.compile(r"(?<![\d.])(?P<y>\d{3,4})(?!\d|\s*(?:丁|巻|冊|頁|枚|コマ|cm|mm|号|番|葉|張|部|帖|軸|点|種|ページ|p\b))")
_CENTURY_WORD = r"(?:世紀|세기|(?-i:C)\b|th century|st century|nd century|rd century|th c\.?|st c\.?|nd c\.?|rd c\.?)"
_CENTURY = re.compile(rf"(?<!\d)(?P<a>\d{{1,2}})(?:\s*(?:世紀|세기|th|st|nd|rd))?\s*[-–~〜～]\s*(?P<b>\d{{1,2}})\s*{_CENTURY_WORD}"
                      rf"|(?<!\d)(?P<c>\d{{1,2}})\s*{_CENTURY_WORD}", re.IGNORECASE)
_PERIOD = re.compile(r"[㐀-鿿]{1,4}(?:時代|前期|中期|後期|末期|初期|中頃|中葉)")

_CIRCA = re.compile(r"頃|ごろ|\bca\.|(?<![a-z])c\.\s*\d|\bcirca\b|경$|약\s", re.IGNORECASE)
_UNCERTAIN = re.compile(r"\?|？|推定|추정|カ\]|か\]")
#: 以後 and 以前 qualify a date they follow at the end of the text (嘉元元以後), not a word inside it
#: (明治検定以前の教科書).
_AFTER = re.compile(r"(?:以後|以降|以來|以来|이후)\s*[)\]）］]?\s*$|^\s*after\b", re.IGNORECASE)
_BEFORE = re.compile(r"(?:以前|이전)\s*[)\]）］]?\s*$|^\s*before\b", re.IGNORECASE)

#: Words that say what a date dates, checked in this order.
KIND_WORDS = (
    ("annotated", re.compile(r"加点|加點|訓点|訓點")),
    ("exemplar", re.compile(r"元奥書|本奥書|底本|(?:刊|版|本)の(?:再版|補刻|再校|影写|覆刻|翻刻|後印|後刷)|の影写|の写"
                            r"|(?:板|版|刊|刊本)(?:写|寫)|影写|影寫|透写|模写|臨写")),
    ("edition", re.compile(r"後印|後刷|再刷|求版|再版|補刻|覆刻|再刻|増補")),
    ("copied", re.compile(r"書写|書寫|筆写|筆寫|写|寫|필사|copied")),
    ("colophon", re.compile(r"奥書|奧書|識語|序|跋|刊記|奥付")),
    ("printed", re.compile(r"刊|版|刷|印行|発行|發行|出版|간행|간|published|printed")),
    ("composed", re.compile(r"成立|著|撰|composed|(?<=[\d〇一二三四五六七八九十元])成")),
)


def kind_of(text: str, default: str) -> str:
    """The kind of date `text` names by its own wording, or `default`."""
    for kind, words in KIND_WORDS:
        if words.search(text):
            return kind
    return default


def number(value: str) -> int:
    """A year, month or day number as catalogues write it: 元, 12, 一二 (digit by digit) or 十二."""
    if value == "元":
        return 1
    if value.isdigit():
        return int(value)
    if "十" in value:
        tens, _, units = value.partition("十")
        return (_KANJI_DIGITS[tens] if tens else 1) * 10 + (_KANJI_DIGITS[units] if units else 0)
    return int("".join(str(_KANJI_DIGITS[c]) for c in value))


def normalise(text: str) -> str:
    """`text` with full-width digits and brackets made ASCII, so one set of patterns reads them all."""
    return unicodedata.normalize("NFKC", text).replace("〔", "[").replace("〕", "]")


class Calendar(Protocol):
    """Converts Japanese calendar dates. Each answer is a pair of Gregorian days, or None."""

    def era(self, name: str) -> tuple[str, str] | None: ...
    def year(self, era: str, year: int) -> tuple[str, str] | None: ...
    def month(self, era: str, year: int, month: int, leap: bool) -> tuple[str, str] | None: ...
    def day(self, era: str, year: int, month: int, leap: bool, day: int) -> str | None: ...
    def query(self, kind: str, value: str) -> str: ...


@dataclass(frozen=True)
class EraDate:
    """A Japanese calendar date: an era year, perhaps with a month and a day."""

    era: str
    year: int
    month: int | None = None
    leap: bool = False
    day: int | None = None

    @property
    def month_text(self) -> str:
        return f"{self.era}{self.year}年{'閏' if self.leap else ''}{self.month}月"

    @property
    def text(self) -> str:
        found = f"{self.era}{self.year}年"
        if self.month:
            found += f"{'閏' if self.leap else ''}{self.month}月"
            if self.day:
                found += f"{self.day}日"
        return found


@dataclass(frozen=True)
class Reading:
    """What a date text states, before conversion."""

    eras: tuple[EraDate, ...] = ()
    spans: tuple[str, ...] = ()
    stated: tuple[int, ...] = ()
    years: tuple[int, int] | None = None
    precision: str | None = None
    day: str | None = None
    period: str | None = None
    qualifier: str | None = None
    uncertain: bool = False

    @property
    def empty(self) -> bool:
        return not (self.eras or self.spans or self.years or self.period)


@dataclass(frozen=True)
class Interval:
    """A reading converted to years."""

    start: int | None
    end: int | None
    precision: str
    qualifier: str | None = None
    uncertain: bool = False
    day: str | None = None
    calendar: str = "unstated"
    conversion: dict[str, str] | None = None
    note: str | None = None


def _era_at(text: str, i: int, calendar: Calendar) -> tuple[str, re.Match] | None:
    """The era name starting at `i` and the year after it, the longest name first."""
    for n in (4, 3, 2):
        name = text[i:i + n]
        if len(name) == n and all(_CJK.match(c) for c in name) and calendar.era(name) \
                and (found := _AFTER_ERA.match(text, i + n)):
            return name, found
    return None


def _eras_in(text: str, calendar: Calendar | None) -> tuple[list[EraDate], str]:
    """The Japanese era dates of `text`, and `text` with them blanked out.

    Every position is tried as the start of an era name, so a name after other words (書写元禄三年) or one
    that starts with 元 (元和, 元禄) is found as well as one at the start.
    """
    found: list[EraDate] = []
    blanked = text
    if calendar is None:
        return found, blanked
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        hit = _era_at(text, i, calendar) if _CJK.match(text[i]) else None
        if hit is None:
            i += 1
            continue
        era, match = hit
        month = match.group("month")
        found.append(EraDate(era, number(match.group("year")), number(month) if month else None,
                             bool(match.group("leap")), number(match.group("day")) if match.group("day") else None))
        spans.append((i, match.end()))
        i = match.end()
        # 明治6年-13年: a range within one era names the era once.
        if until := _SAME_ERA_RANGE.match(text, i):
            found.append(EraDate(era, number(until.group("year"))))
            spans.append((until.start(), until.end()))
            i = until.end()
    for match in _ABBREVIATED_YEAR.finditer(text):
        if any(a <= match.start() < b for a, b in spans):
            continue
        era = _ABBREVIATED[match.group("era")]
        if not calendar.era(era):
            continue
        found.append(EraDate(era, int(match.group("year")), int(match.group("month")) if match.group("month") else None))
        spans.append((match.start(), match.end()))
    if found:
        for match in _SAME_ERA.finditer(text):
            before = [f for f, (a, _) in sorted(zip(found, spans, strict=True), key=lambda x: x[1]) if a < match.start()]
            if before:
                found.append(EraDate(before[-1].era, number(match.group("year"))))
                spans.append((match.start(), match.end()))
    for a, b in sorted(spans, reverse=True):
        blanked = blanked[:a] + " " * (b - a) + blanked[b:]
    return found, blanked


def read(text: str, calendar: Calendar | None = None) -> Reading:
    """What `text` states as a date. `calendar` recognises Japanese era names; without one, none are read."""
    value = normalise(text)
    qualifier = "after" if _AFTER.search(value) else "before" if _BEFORE.search(value) else \
        "circa" if _CIRCA.search(value) else None
    uncertain = bool(_UNCERTAIN.search(value))
    spans = []
    if calendar is not None:
        for match in _ERA_SPAN.finditer(value):
            names = re.split(r"[・、]", match.group("eras"))
            # The run before the first name may hold other words: 巻末寛永中 names 寛永.
            names[0] = next((names[0][-n:] for n in (4, 3, 2) if len(names[0]) >= n and calendar.era(names[0][-n:])),
                            names[0])
            if all(calendar.era(name) for name in names):
                spans.extend(names)
                value = value[:match.start()] + " " * (match.end() - match.start()) + value[match.end():]
    eras, rest = _eras_in(value, calendar)
    stated = tuple(int(m.group("year")) for m in _STATED_YEAR.finditer(rest))
    if eras or spans:
        return Reading(eras=tuple(eras), spans=tuple(spans), stated=stated, qualifier=qualifier, uncertain=uncertain)
    if (m := _ISO_DAY.search(rest)) and 1 <= int(m.group("m")) <= 12:
        y = int(m.group("y"))
        if m.group("m") == "01" and m.group("d") == "01":
            # W3CDTF pads a year to its first day (1777-01-01 beside 1777(序)); it states a year.
            return Reading(years=(y, y), precision="year", qualifier=qualifier, uncertain=uncertain)
        return Reading(years=(y, y), precision="day", day=m.group(0), qualifier=qualifier, uncertain=uncertain)
    if m := _CENTURY.search(rest):
        first = int(m.group("a") or m.group("c"))
        last = int(m.group("b") or first)
        return Reading(years=((first - 1) * 100 + 1, last * 100), precision="century", qualifier=qualifier,
                       uncertain=uncertain)
    if m := _CENTURY_DASHES.search(rest):
        c = int(m.group("c"))
        return Reading(years=(c * 100, c * 100 + 99), precision="century", uncertain=uncertain, qualifier=qualifier)
    if m := _DECADE_DASH.search(rest):
        d = int(m.group("d"))
        return Reading(years=(d * 10, d * 10 + 9), precision="decade", uncertain=uncertain, qualifier=qualifier)
    if m := _RANGE.search(rest):
        a, b = m.group("a"), m.group("b")
        b_full = int(a[: len(a) - len(b)] + b) if len(b) < len(a) else int(b)
        if b_full >= int(a):
            return Reading(years=(int(a), b_full), precision="years", qualifier=qualifier, uncertain=uncertain)
    if (m := _ISO_MONTH.search(rest)) and 1 <= int(m.group("m")) <= 12:
        y = int(m.group("y"))
        return Reading(years=(y, y), precision="month", qualifier=qualifier, uncertain=uncertain)
    years = [int(m.group("y")) for m in _YEAR.finditer(rest)]
    years = [y for y in years if 100 <= y <= datetime.now(UTC).year]
    if years:
        return Reading(years=(min(years), max(years)), precision="year" if len(set(years)) == 1 else "years",
                       qualifier=qualifier, uncertain=uncertain)
    if m := _PERIOD.search(rest):
        return Reading(period=m.group(0), precision="period", qualifier=qualifier, uncertain=uncertain)
    return Reading(qualifier=qualifier, uncertain=uncertain)


def _year(day: str) -> int:
    return int(day[:4])


def interval(reading: Reading, calendar: Calendar | None = None) -> Interval | None:
    """`reading` in years, or None when it states no date. Era dates are converted by `calendar`."""
    if reading.empty:
        return None
    if reading.period:
        return Interval(None, None, "period", reading.qualifier, reading.uncertain)
    if not (reading.eras or reading.spans):
        start, end = reading.years
        return _qualified(Interval(start, end, reading.precision, reading.qualifier, reading.uncertain,
                                   day=reading.day, calendar="gregorian"))
    assert calendar is not None
    starts, ends, queries, days = [], [], [], []
    for name in reading.spans:
        found = calendar.era(name)
        if found is None:
            return None
        starts.append(_year(found[0]))
        ends.append(_year(found[1]))
        queries.append(calendar.query("era", name))
    for era in reading.eras:
        if era.month and era.day:
            found_day = calendar.day(era.era, era.year, era.month, era.leap, era.day)
            if found_day is None:
                return None
            starts.append(_year(found_day))
            ends.append(_year(found_day))
            days.append(found_day)
            queries.append(calendar.query("date", era.text))
            continue
        if era.month:
            # A month is placed by its own first day: 康応元年12月 began on 1389-12-18.
            found_month = calendar.month(era.era, era.year, era.month, era.leap)
            if found_month is None:
                return None
            starts.append(_year(found_month[0]))
            ends.append(_year(found_month[0]))
            queries.append(calendar.query("month", era.month_text))
            continue
        found = calendar.year(era.era, era.year)
        if found is None:
            return None
        starts.append(_year(found[0]))
        ends.append(_year(found[0]))
        queries.append(calendar.query("year", f"{era.era}{era.year}年"))
    start, end = min(starts), max(ends)
    if reading.spans:
        precision = "years"
    elif len(reading.eras) > 1:
        precision = "years" if start != end else "year"
    else:
        era = reading.eras[0]
        precision = "day" if era.month and era.day else "month" if era.month else "year"
    note = None
    if reading.stated and len(reading.eras) == 1 and not reading.spans and reading.stated[0] != start:
        if abs(reading.stated[0] - start) <= 1:
            # A lunisolar year runs into the next Gregorian one; the source's own reading stands.
            note = f"the source gives {reading.stated[0]}; HuTime places {reading.eras[0].text} in {start}"
            start = end = reading.stated[0]
        else:
            note = f"the source's bracketed {reading.stated[0]} is not {reading.eras[0].text}, which HuTime places in {start}"
    conversion = {"service": "hutime", "query": queries[0] if len(queries) == 1 else "\n".join(queries)}
    return _qualified(Interval(start, end, precision, reading.qualifier, reading.uncertain,
                               day=days[0] if len(days) == 1 and precision == "day" else None,
                               calendar="japanese", conversion=conversion, note=note))


def _qualified(found: Interval) -> Interval:
    """`before` and `after` leave one end open."""
    if found.qualifier == "after":
        return replace(found, end=None)
    if found.qualifier == "before":
        return replace(found, start=None)
    return found


# --- HuTime -------------------------------------------------------------------------------------

HUTIME = "https://ap.hutime.org/cal/"
#: HuTime's calendar ids: 1001.1 the Japanese calendar (和暦), 101.1 the Gregorian.
JAPANESE, GREGORIAN = "1001.1", "101.1"
#: Items per batch request; HuTime's form takes 100 lines.
BATCH = 100


def hutime_url(kind: str, value: str) -> str:
    """The GET request that converts one Japanese `value` (an `era`, a `year` or a `date`) to Gregorian days.

    The answer is plain text a reader can check against the source: the first and last day for an era or
    a year, the day for a date.
    """
    params = {"method": "conv", "ical": JAPANESE, "itype": kind, "ival": value, "ocal": GREGORIAN,
              "otype": "date", "oform": "yyyy-MM-dd"}
    if kind != "date":
        params["ep"] = "be"
    return HUTIME + "?" + urlencode(params)


@dataclass
class HuTime:
    """HuTime's calendar conversion, answered from `cache` and asked in batches through the pacing of `net`.

    `cache` is a directory; `answers.json` there maps each question (`era|寛政`, `year|寛政|3`,
    `date|寛政3年閏2月1日`) to its answer, a pair of days, one day, or null when HuTime knows no such
    date. `offline` answers only from the cache and leaves an unknown question unanswered (None).
    """

    cache: Path
    offline: bool = False
    fetcher: Callable[..., Path] = net.download
    answers: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        path = self.cache / "answers.json"
        if path.exists():
            self.answers = json.loads(path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / "answers.json"
        path.write_text(json.dumps(self.answers, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")

    def query(self, kind: str, value: str) -> str:
        return hutime_url(kind, value)

    def era(self, name: str) -> tuple[str, str] | None:
        found = self._ask("era", name)
        return tuple(found) if found else None

    def year(self, era: str, year: int) -> tuple[str, str] | None:
        found = self._ask("year", f"{era}{year}年")
        return tuple(found) if found else None

    def month(self, era: str, year: int, month: int, leap: bool) -> tuple[str, str] | None:
        found = self._ask("month", EraDate(era, year, month, leap).month_text)
        return tuple(found) if found else None

    def day(self, era: str, year: int, month: int, leap: bool, day: int) -> str | None:
        return self._ask("date", EraDate(era, year, month, leap, day).text)

    def _ask(self, kind: str, value: str) -> Any:
        key = f"{kind}|{value}"
        if key not in self.answers and not self.offline:
            self.prefetch(kind, [value])
        return self.answers.get(key)

    def prefetch(self, kind: str, values: Iterable[str]) -> int:
        """Ask HuTime every one of `values` not yet answered, `BATCH` at a time; return how many were asked."""
        wanted = sorted({v for v in values if f"{kind}|{v}" not in self.answers})
        for i in range(0, len(wanted), BATCH):
            batch = wanted[i:i + BATCH]
            params = {"method": "conv", "ical": JAPANESE, "itype": kind, "ival": "\r\n".join(batch),
                      "ocal": GREGORIAN, "otype": "date", "oform": "yyyy-MM-dd", "out": "json"}
            if kind != "date":
                params["ep"] = "be"
            digest = hashlib.sha256(json.dumps(params, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            dest = self.cache / "batches" / f"{digest}.json"
            self.fetcher(HUTIME, dest, expected="json", method="POST", data=params)
            found = json.loads(dest.read_text(encoding="utf-8"))
            step = 1 if kind == "date" else 2
            if len(found) != step * len(batch):
                raise ValueError(f"HuTime answered {len(found)} items for {len(batch)} {kind} questions")
            for j, value in enumerate(batch):
                items = found[j * step:(j + 1) * step]
                texts = [item.get("text") if isinstance(item, dict) else None for item in items]
                self.answers[f"{kind}|{value}"] = (texts[0] if step == 1 else texts) if all(texts) else None
        if wanted:
            self.save()
        return len(wanted)


def era_candidates(text: str) -> set[str]:
    """Every string in `text` that could be a Japanese era name, for asking HuTime in one batch: each run
    of two to four CJK characters followed by a year, and each named before 年間 or 中."""
    value = normalise(text)
    found: set[str] = set()
    for i in range(len(value)):
        for n in (2, 3, 4):
            name = value[i:i + n]
            if len(name) == n and all(_CJK.match(c) for c in name) and _AFTER_ERA.match(value, i + n):
                found.add(name)
    for match in _ERA_SPAN.finditer(value):
        names = re.split(r"[・、]", match.group("eras"))
        found.update(names)
        found.update(names[0][-n:] for n in (4, 3, 2) if len(names[0]) >= n)
    for match in _ABBREVIATED_YEAR.finditer(value):
        found.add(_ABBREVIATED[match.group("era")])
    return found


def year_questions(text: str, calendar: Calendar) -> dict[str, set[str]]:
    """The era years, months and days `text` asks about once its era names are known to `calendar`, by kind."""
    eras = read(text, calendar).eras
    return {"year": {f"{e.era}{e.year}年" for e in eras if not e.month},
            "month": {e.month_text for e in eras if e.month and not e.day},
            "date": {e.text for e in eras if e.month and e.day}}


# --- Claims, resolution and labels --------------------------------------------------------------


def claim_id(document: str, source: str, locator: str, kind: str, value: dict[str, Any]) -> str:
    """A claim's id, the key of its assertion in the ledger: the same statement read the same way always has
    the same one, and a statement read anew (a better reading, a conversion corrected) has another."""
    digest = hashlib.sha256(f"{document}\x1f{source}\x1f{locator}\x1f{kind}\x1f"
                            f"{json.dumps(value, ensure_ascii=False, sort_keys=True)}".encode()).hexdigest()
    return f"dt:{digest[:24]}"


def claim(document: str, text: str, *, kind: str, scope: str, tier: str, source: str, locator: str,
          calendar: Calendar | None = None, note: str | None = None, years: tuple[int | None, int | None] | None = None,
          precision: str | None = None) -> DateClaim | None:
    """A claim that `document`'s `kind` event is dated by `text`, read and converted here.

    `years` and `precision` stand for a reading made elsewhere (an importer's interval) when this module
    reads none. A text with neither years nor a named period is no claim at all: None.
    """
    text = text.strip()
    if not text:
        return None
    found = interval(read(text, calendar), calendar)
    if years is not None and (years[0] is not None or years[1] is not None):
        # The source's own reading of its words stands; ours only supplies what it leaves out.
        given = precision or (found.precision if found else "year" if years[0] == years[1] else "years")
        # The years are the importer's, not a conversion of ours; a qualifier stays only where they agree with it.
        qualifier = found.qualifier if found and (
            (found.qualifier == "after" and years[1] is None) or (found.qualifier == "before" and years[0] is None)
            or found.qualifier == "circa") else None
        found = Interval(years[0], years[1], given, qualifier, found.uncertain if found else False,
                         calendar=found.calendar if found else "unstated")
    if found is None:
        return None
    value = {"scope": scope, "text": text, "start": found.start, "end": found.end, "precision": found.precision,
             "qualifier": found.qualifier, "uncertain": found.uncertain, "day": found.day, "calendar": found.calendar,
             "conversion": found.conversion, "tier": tier,
             "note": "; ".join(n for n in (note, found.note) if n) or None}
    return DateClaim(id=claim_id(document, source, locator, kind, value), document=document, kind=kind,
                     source=source, locator=locator, **value)


@dataclass(frozen=True)
class Resolved:
    """The date one axis shows for a document, and the claims it stands on."""

    axis: str
    kind: str
    start: int | None
    end: int | None
    precision: str
    qualifier: str | None
    uncertain: bool
    text: str
    status: str
    claims: tuple[str, ...]
    calendar: str = "unstated"
    conversion: dict[str, str] | None = None
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"axis": self.axis, "kind": self.kind, "start": self.start, "end": self.end,
                "precision": self.precision, "qualifier": self.qualifier, "uncertain": self.uncertain,
                "text": self.text, "status": self.status, "claims": list(self.claims), "calendar": self.calendar,
                "conversion": self.conversion, "source": self.source, "label": label(self)}


def _rank(item: DateClaim) -> tuple[int, int, int]:
    tier = TIER_ORDER.index(item.tier)
    source = SOURCE_ORDER.index(item.source) if item.source in SOURCE_ORDER else len(SOURCE_ORDER)
    precise = ("day", "month", "year", "years", "decade", "century", "period").index(item.precision)
    return tier, source, precise


def _overlap(a: DateClaim, b: DateClaim) -> bool:
    lo = lambda c: c.start if c.start is not None else -10**6
    hi = lambda c: c.end if c.end is not None else 10**6
    return lo(a) <= hi(b) and lo(b) <= hi(a)


def _dated(c: DateClaim) -> bool:
    return c.start is not None or c.end is not None


def _pick(axis: str, candidates: Sequence[DateClaim], others: Sequence[DateClaim] = ()) -> Resolved | None:
    """The best of `candidates`, disputed when it does not overlap another of them or one of `others`."""
    dated = [c for c in candidates if _dated(c)]
    if not dated:
        return None
    ranked = sorted(dated, key=lambda c: (_rank(c), c.id))
    best = ranked[0]
    disagree = [c for c in ranked[1:] + [o for o in others if _dated(o)] if not _overlap(best, c)]
    status = "disputed" if disagree else "agreed" if len(ranked) > 1 else "single"
    return Resolved(axis=axis, kind=best.kind, start=best.start, end=best.end, precision=best.precision,
                    qualifier=best.qualifier, uncertain=best.uncertain, text=best.text, status=status,
                    claims=tuple(c.id for c in ranked), calendar=best.calendar, conversion=best.conversion,
                    source=best.source)


def resolve(claims: Iterable[DateClaim], production: str = "unknown") -> dict[str, Resolved]:
    """The witness date and the composition date of one document's claims, each where one is dated.

    The witness axis takes the first kind of `WITNESS_ORDER` for how the document was made that has a
    dated claim about this copy. Within the kind, the attested claim wins over the editorial and the
    derived one, then a holder's own catalogue over an aggregator's, then the more precise. A claim that
    does not overlap the chosen one makes the date `disputed`, and so does a date the holder gives the
    item (`produced`) that does not overlap it; every claim stays listed.
    """
    claims = list(claims)
    order = WITNESS_ORDER.get(production.split("/")[0], WITNESS_ORDER["unknown"])
    found: dict[str, Resolved] = {}
    for kind in order:
        produced = [c for c in claims if c.scope == "witness" and c.kind == "produced"] if kind != "produced" else []
        picked = _pick("witness", [c for c in claims if c.scope == "witness" and c.kind == kind], produced)
        if picked:
            found["witness"] = picked
            break
    composed = _pick("composed", [c for c in claims if c.kind == "composed"])
    if composed:
        found["composed"] = composed
    return found


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def label(found: Resolved | DateClaim) -> str:
    """A date in a few characters, honest about its range and doubt: 1791, c. 1800, 1789–1801, 18th c.

    A named period with no years, such as 江戸後期, is written as its source wrote it.
    """
    start, end, precision = found.start, found.end, found.precision
    if start is None and end is None:
        return found.text
    if found.qualifier == "after":
        text = f"after {start}"
    elif found.qualifier == "before":
        text = f"before {end}"
    elif precision == "century":
        first, last = (start - 1) // 100 + 1, (end - 1) // 100 + 1
        if start % 100 == 0 and end == start + 99:
            text = f"{start}s"
        elif start % 100 != 1 or end % 100 != 0:
            text = f"{start}–{end}"
        else:
            text = f"{_ordinal(first)} c." if first == last else f"{_ordinal(first)}–{_ordinal(last)} c."
    elif precision == "decade":
        text = f"{start}s"
    elif start == end:
        text = str(start)
    else:
        text = f"{start}–{end}"
    if found.qualifier == "circa":
        text = "c. " + text
    if found.uncertain:
        text += "?"
    return text
