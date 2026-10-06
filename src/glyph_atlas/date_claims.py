"""Every dated statement the project's sources make about its documents, as claims.

`build` reads each corpus's `documents.parquet` and, for each document, the dates these sources state:

- the document's own `dating`, which its importer read from that corpus's source;
- the 国書データベース record of a document that names one (`kokusho.yaml`): the copy's 刊年 (`bpublish`),
  the dates in its notes on colophons, prefaces and printing (`chuki`, read from free text, so `derived`),
  and the works' dates (`work[].year`: composed, or the work's first printing or preface);
- the metadata of the holder's IIIF manifest (`iiif-manifests.yaml`), for corpora that carry no dating
  of their own, and みんなで翻刻's copy of it where the manifest is not cached (`honkoku-data.yaml`);
- the curated dates of the Ainu records (`ainu-records.yaml`): the work's date and a witness's copying.
- linked holder catalogues, the publication-year field in Honkoku's v1 index, and Wikisource scan-index years.

A Commons file's `date` field is left out: on the scanned books it is mostly the upload or scan date
(2017-05-01 on every 四部叢刊 volume), and nothing in the field tells the two apart.

Japanese dates are converted by HuTime (`dates.HuTime`); the era names, years and days every text
asks about are collected first and asked in batches. Nothing is fetched unless `fetch` is set: then
the missing 国書 records and manifests are read through `net`, and HuTime answers what its cache lacks.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import dates, net, withdrawn
from .schema import DateClaim

#: The corpora read, under the corpus root, with the source id of their own `dating`.
CORPORA = {
    "honkoku-lines": "honkoku-lines", "honkoku-atlas": "honkoku-lines", "honkoku-user": "honkoku-data",
    "honkoku-collection/current": "honkoku-data", "honkoku-data": "honkoku-data", "ainu-records": "ainu-records",
    "ainu-characters": "ainu-records", "codh-full": "codh-char-shape", "kokatsuji": "codh-kokatsuji",
    "hilab": "hi-lab-kuzushiji", "hng": "hng-basic-data", "hng-kiridashi": "hng-kiridashi-data",
    "hdic-krm": "hdic-krm", "hdic-ktb": "hdic-ktb", "hdic-tsj": "hdic-tsj", "khs": "khs",
    "hangeul-museum": "hangeul-museum", "nlk-sayeogwon": "nlk", "glossary-headwords": "nlk",
    "wikisource-zh-scans": "wikisource", "wikisource-ko-scans": "wikisource", "hunminjeongeum": "wikisource",
    "hunminjeongeum-hanja": "wikisource", "hunminjeongeum-cells": "wikisource", "ndl-1014828": "ndl-1014828",
    "ndl-minhon": "ndl-minhon-ocr",
}
#: Corpora whose importer dates each document from a source that studies it (the HDIC editions, HNG,
#: 国家遺産庁, 国立한글박물관, 国立中央図書館). The holder's manifest describes the reproduction the images come
#: from, a facsimile among them, so it is not read for these.
OWN_DATING = frozenset({"hng", "hng-kiridashi", "hdic-krm", "hdic-ktb", "hdic-tsj", "khs", "hangeul-museum",
                        "nlk-sayeogwon", "glossary-headwords"})
#: Origins whose dates may be written in the Japanese calendar.
JAPANESE_ORIGINS = frozenset({"japan", "unknown", None})
#: An importer's `dating.kind` as a claim's kind and scope.
IMPORTED_KIND = {"composition": ("composed", "work"), "copying": ("copied", "witness"),
                 "publication": ("printed", "witness"), "impression": ("edition", "witness"),
                 "unknown": ("produced", "witness")}

KOKUSHO_RECORD = "https://kokusho.nijl.ac.jp/biblio/{bid}"
KOKUSHO_DETAIL = "https://kokusho.nijl.ac.jp/api/biblioDetail/{bid}"
_BID = re.compile(r"(?:kokusho|kotenseki)\.nijl\.ac\.jp/(?:biblio|api/iiif)/(\d{9})\b")
NDL_MANIFEST = "https://dl.ndl.go.jp/api/iiif/{pid}/manifest.json"
AINU_CURATION = "https://github.com/mkpoli/ainu-records/blob/main/data/sources.yaml"
#: 国書's note tags that carry dates of this copy, and the kind each names.
CHUKI_TAGS = {"奥": "colophon", "識": "colophon", "序": "colophon", "跋": "colophon", "刊": "printed",
              "版": "printed", "写": "copied", "書": "copied", "加": "annotated", "成": "composed"}
#: A note's phrase that may date something: an era year, an era span (寛永中, 寛永年間) or a period.
_NOTE_DATE = re.compile(r"[\u3400-\u9fff]{2}(?:元|\d+|[０-９]+|[〇一二三四五六七八九十]+)(?:年|[^\d０-９〇一二三四五六七八九十])"
                        r"|[\u3400-\u9fff]{2}(?:中|年間)|時代|前期|中期|後期|初期|末期")

#: Manifest metadata labels that date the item, each with the kind it names when its value says no more.
#: A label paired with a fuller one (W3CDTF beside the written date) is read only when that one is absent.
LABELS: tuple[tuple[str, str, str | None], ...] = (
    ("年代", "produced", None),
    ("年代記述", "produced", None),
    ("製作年", "produced", None),
    ("作成年", "produced", None),
    ("刊年", "printed", None),
    ("出版年", "printed", None),
    ("出版・刊行年", "printed", None),
    ("刊行年", "printed", None),
    ("日付", "produced", None),
    ("年月日(Date)", "produced", None),
    ("dcterms:issued", "printed", "dcterms:date"),
    ("Publication Date", "produced", None),
    ("Publication Date (W3CDTF fortmat)", "produced", "Publication Date"),
    ("作成年代始（和暦)", "produced", None),
    ("作成年代始（西暦)", "produced", "作成年代始（和暦)"),
    ("内容年代始（和暦）", "other", None),
    ("書誌情報（発行年：リテラル）", "produced", None),
    ("書誌情報（発行年）", "produced", "書誌情報（発行年：リテラル）"),
    ("出版年月等／Published Date", "produced", None),
    ("出版年（西暦）／Publication Year (Christian era)", "produced", "出版年月等／Published Date"),
    ("出版／作成年（西暦）", "produced", None),
    ("出版年月日 / Date of Issue", "produced", None),
    ("作成年月日 / Date of Creation", "produced", None),
    ("刊行年(西暦)", "printed", None),
    ("日付：出版年Ｆ", "produced", None),
    ("dcterms:date", "produced", None),
    ("年・ Year", "produced", None),
    ("Date (Text)", "produced", None),
    ("Date / Zeit / 時間", "produced", None),
    ("日付 / Date", "produced", None),
    ("年月日", "produced", None),
    ("年代域", "produced", None),
    ("Date", "produced", None),
    ("date", "produced", None),
    ("時代・世紀 / Period,Century / 年代・世纪 / 시대・세기", "produced", None),
    ("Time Period", "produced", None),
    ("時代", "produced", None),
)
_LABEL = {label.strip(): (kind, fuller) for label, kind, fuller in LABELS}
_DATE_LIKE = re.compile(r"[0-9０-９]|年|世紀|時代|期|century|period", re.IGNORECASE)


@dataclass(frozen=True)
class Statement:
    """A date text and what the source says it dates, before it is read."""

    text: str
    kind: str
    scope: str
    tier: str
    source: str
    locator: str
    note: str | None = None
    years: tuple[int | None, int | None] | None = None
    #: The kind is the field's own; words in the text do not override it.
    fixed: bool = False
    uncertain: bool | None = None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _text(value: Any) -> str:
    """A IIIF label or value (a string, a language map, a v2 `@value` list) as one string."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " / ".join(t for t in (_text(v) for v in value) if t)
    if isinstance(value, dict):
        if "@value" in value:
            return _text(value["@value"])
        return " / ".join(t for t in (_text(v) for v in value.values()) if t)
    return "" if value is None else str(value)


def bid_of(row: dict[str, Any]) -> str | None:
    """The 国書データベース id a document names, as `kokusho.bid_of` finds it."""
    refs = _json(row.get("source_refs")) or {}
    if (bid := refs.get("nijl-bid")) and re.fullmatch(r"\d{9}", bid):
        return bid
    meta = _json(row.get("meta")) or {}
    if isinstance(meta, dict) and isinstance(meta.get("kokusho"), dict) and meta["kokusho"].get("bid"):
        return str(meta["kokusho"]["bid"])
    for value in (*refs.values(), json.dumps(meta, ensure_ascii=False)):
        if match := _BID.search(str(value)):
            return match.group(1)
    return None


def manifest_of(row: dict[str, Any]) -> str | None:
    """The holder's IIIF manifest of a document; for an NDL item named only by its pid, NDL's."""
    refs = _json(row.get("source_refs")) or {}
    if url := refs.get("iiif-manifest"):
        return url
    meta = _json(row.get("meta")) or {}
    pid = refs.get("ndl-pid") or (re.search(r"dl\.ndl\.go\.jp/(?:info:ndljp/)?pid/(\d+)", str(meta.get("ndl_url") or ""))
                                   or [None, None])[1]
    return NDL_MANIFEST.format(pid=pid) if pid else None


def _manifest_path(cache: Path, url: str) -> Path:
    return cache / "manifests" / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def importer_statements(row: dict[str, Any], source: str) -> Iterator[Statement]:
    """The document's own `dating`, as its importer read it from `source`."""
    refs = _json(row.get("source_refs")) or {}
    record = next((v for k, v in refs.items() if k in ("record", "catalogue") and str(v).startswith("http")), None)
    for item in row.get("dating") or []:
        evidence = item.get("evidence") or ""
        if "commons.wikimedia.org" in evidence:
            continue
        if not evidence:
            # An importer's date that names no evidence is left for a source that states it.
            continue
        locator = evidence if evidence.startswith("http") else f"{record or source}#{evidence}"
        kind, scope = IMPORTED_KIND[item.get("kind") or "unknown"]
        yield Statement(text=item.get("literal") or "", kind=kind, scope=scope, tier="attested", source=source,
                        locator=locator, years=(item.get("start"), item.get("end")),
                        fixed=item.get("kind") not in (None, "unknown"))


def kokusho_statements(record: dict[str, Any]) -> Iterator[Statement]:
    """The dates of a 国書データベース record: this copy's, its notes', and its works'."""
    bid = str(record.get("bid"))
    url = KOKUSHO_RECORD.format(bid=bid)
    made = {"刊": "printed", "写": "copied"}.get((record.get("kansha") or "").strip(), "produced")
    for i, item in enumerate(record.get("bpublish") or []):
        item = str(item).strip()
        if item and _DATE_LIKE.search(item) and not item.startswith("〈"):
            yield Statement(text=item, kind=made, scope="witness", tier="attested", source="kokusho",
                            locator=f"{url}#bpublish.{i}")
            break
    for i, note in enumerate(record.get("chuki") or []):
        tag = re.match(r"〈(.)〉", note or "")
        if not tag or tag.group(1) not in CHUKI_TAGS:
            continue
        kind = "exemplar" if re.search(r"元奥書|本奥書", note) else CHUKI_TAGS[tag.group(1)]
        scope = "work" if kind == "composed" else "witness"
        for part in re.split(r"[，,。；;]", note[tag.end():]):
            if _NOTE_DATE.search(part):
                yield Statement(text=part.strip("「」『』（）() "), kind=kind, scope=scope, tier="derived",
                                source="kokusho", locator=f"{url}#chuki.{i}", note=note.strip())
    for i, work in enumerate(record.get("work") or []):
        year = (work.get("year") or "").strip()
        for event in events(year):
            yield Statement(text=event, kind=dates.kind_of(event, "composed"), scope="work", tier="attested",
                            source="kokusho", locator=f"{url}#work.{i}.year", note=work.get("name"), fixed=True)


_ERA_NAME = re.compile(r"[\u3400-\u9fff]{2,4}?(?=元|\d|[０-９]|[〇一二三四五六七八九十])")


def events(text: str) -> list[str]:
    """The events of one 国書 work date, each with its own kind: 寛政四成、同五序、同八刊 is a composition,
    a preface and a printing, read as 寛政四成, 寛政五序 and 寛政八刊. A date of one event stays whole."""
    parts = [p.strip() for p in re.split(r"[、，,]", text) if p.strip()]
    if len(parts) < 2 or not all(dates.kind_of(p, "") for p in parts):
        return [text] if text else []
    out, era = [], None
    for part in parts:
        if part.startswith("同") and era:
            part = era + part[1:]
        elif found := _ERA_NAME.match(part):
            era = found.group(0)
        out.append(part)
    return out


def metadata_statements(entries: Iterable[tuple[str, str]], *, source: str, locator: str) -> Iterator[Statement]:
    """Dates in IIIF metadata (label, value) pairs, by `LABELS`."""
    pairs = list(dict.fromkeys((label.strip(), value) for label, value in entries))
    present = dict(pairs)
    for label, value in pairs:
        if label not in _LABEL:
            continue
        kind, fuller = _LABEL[label]
        if fuller and present.get(fuller):
            continue
        value = re.sub(r"<[^>]+>", " ", value).strip()
        # A value given in several languages or forms is joined by " / " (平安時代・12世紀 / Heian period/12th
        # century; 1777(序) / 1777-01-01): the first states it as the holder wrote it.
        value = value.split(" / ")[0].strip()
        if not value or value.strip("0") == "" or re.fullmatch(r"\d", value) or (
                not _DATE_LIKE.search(value) and not dates.era_candidates(value)):
            continue
        if urlsplit(locator).hostname == "shimuchi.lib.u-ryukyu.ac.jp":
            if match := re.fullmatch(r"\[(\d{4}-\d{4})\](\[\d{4}\]\[写\])", value):
                # 冠船 records list historical content first, then the supplied copying year.
                yield Statement(text=match[1], kind="other", scope="witness", tier="derived", source=source,
                                locator=f"{locator}#metadata={label}", note=f"Content dates; catalogue field: {value}",
                                fixed=True)
                yield Statement(text=match[2], kind="copied", scope="witness", tier="derived", source=source,
                                locator=f"{locator}#metadata={label}", note=f"Supplied copying date; catalogue field: {value}",
                                fixed=True, uncertain=True)
                continue
            if "[版刷][写]" in value:
                # This catalogue's descriptions identify these as printed editions despite the extra 写 tag.
                yield Statement(text=value, kind="printed", scope="witness", tier="attested", source=source,
                                locator=f"{locator}#metadata={label}", fixed=True)
                continue
        yield Statement(text=value, kind=kind, scope="witness", tier="attested", source=source,
                        locator=f"{locator}#metadata={label}", note=None if label.startswith(("Publication", "Date")) else label)
    # AMANE's Ina catalogues flatten columns into ラベル. The token before the Western sort year
    # contains the written date. Sort years include placeholders (近世 → 1867) and errors
    # (元治二年 → 1862), so only that written token is read; later 原資料年代 dates the exemplar.
    if urlsplit(locator).hostname == "ourarchives.amane-project.jp":
        for label, value in pairs:
            if label == "ラベル" and (m := re.search(r"\s([^\s]+)\s+[0-9０-９]{3,4}\s", value)):
                text = m[1]
                if dates.era_candidates(text):
                    yield Statement(text=text, kind="produced", scope="witness", tier="derived", source=source,
                                    locator=f"{locator}#metadata={label}", note="Written date in catalogue label",
                                    fixed=True)
            # Kadomi's structured fields are sometimes filed under the misleading label 'manifest URI'.
            fields = dict(re.findall(r"\[([^\]]+)\]:\s*([^\[]*)", value))
            era, western = fields.get("年（年号）", "").strip(), fields.get("年（西暦）", "").strip()
            if era or western:
                text = era if dates.era_candidates(era) else western
                if text:
                    yield Statement(text=text, kind="produced", scope="witness", tier="derived", source=source,
                                    locator=f"{locator}#metadata={label}/年", fixed=True)


def manifest_entries(manifest: Any) -> list[tuple[str, str]]:
    if not isinstance(manifest, dict):
        return []
    found = []
    for entry in manifest.get("metadata") or []:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        labels = [_text(label)]
        if isinstance(label, dict):
            labels += [_text(v) for v in label.values()]
        chosen = next((v for v in labels if v.strip() in _LABEL), labels[0])
        found.append((chosen, _text(entry.get("value"))))
    if not any(_LABEL.get(label.strip()) for label, _ in found) and manifest.get("navDate"):
        found.append(("date", str(manifest["navDate"])[:10]))
    return found


def curated_statements(row: dict[str, Any]) -> Iterator[Statement]:
    """The Ainu records' curated dates: the work's (`date`) and this witness's copying (`copied`)."""
    meta = _json(row.get("meta")) or {}
    refs = _json(row.get("source_refs")) or {}
    where = f"{AINU_CURATION}#{refs.get('ainu-work') or ''}"
    if meta.get("curated_date"):
        yield Statement(text=str(meta["curated_date"]), kind="composed", scope="work", tier="attested",
                        source="ainu-records", locator=where, fixed=True)
    if meta.get("curated_copied"):
        yield Statement(text=str(meta["curated_copied"]), kind="copied", scope="witness", tier="attested",
                        source="ainu-records", locator=f"{where}/{refs.get('ainu-witness') or ''}", fixed=True)


def statements(row: dict[str, Any], corpus: str, cache: Path, *, fetch: bool = False) -> list[Statement]:
    """Every date statement about one document."""
    found = list(importer_statements(row, CORPORA[corpus]))
    found += curated_statements(row)
    if bid := bid_of(row):
        path = cache / "kokusho" / f"{bid}.json"
        if fetch and not path.exists():
            try:
                net.download(KOKUSHO_DETAIL.format(bid=bid), path, expected="json")
            except net.DownloadError:
                pass
        record = _load(path)
        if isinstance(record, dict) and str(record.get("bid")) == bid:
            found += kokusho_statements(record)
    if corpus not in OWN_DATING and (url := manifest_of(row)) and not _BID.search(url):
        path = _manifest_path(cache, url)
        if fetch and not path.exists():
            try:
                net.download(url, path, expected="json")
            except net.DownloadError:
                pass
        manifest = _load(path)
        if manifest is not None:
            found += metadata_statements(manifest_entries(manifest), source="iiif-manifests", locator=url)
        from .date_catalogues import catalogue_entries
        for record_url, entries in catalogue_entries(url, cache, fetch=fetch):
            found += metadata_statements(entries, source="holder-catalogues", locator=record_url)
    # A holder may have removed metadata since Honkoku copied it. Keep both attributed statements.
    meta = _json(row.get("meta")) or {}
    copied = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else None
    refs = _json(row.get("source_refs")) or {}
    if copied and (entry := refs.get("honkoku-entry")):
        entries = []
        for k, v in copied.items():
            # Older importers stringified IIIF v3 language-map labels as Python dicts.
            if k.startswith("{"):
                import ast
                try:
                    label = ast.literal_eval(k)
                    k = next((s for s in (_text(v) for v in label.values()) if s in _LABEL), _text(label))
                except (ValueError, SyntaxError, AttributeError):
                    pass
            entries.append((k, _text(v)))
        found += metadata_statements(entries, source="honkoku-data", locator=entry)
        if (url := manifest_of(row)) and urlsplit(url).hostname == "ourarchives.amane-project.jp":
            # Apply the holder-specific field layout, retaining the actual copied source as evidence.
            found += [Statement(**{**s.__dict__, "locator": s.locator.replace(url, entry, 1)})
                      for s in metadata_statements(entries, source="honkoku-data", locator=url)
                      if s.tier == "derived"]
    # The Wikisource index dates the scanned edition. Commons' file/scan date remains excluded.
    index = meta.get("index") or {}
    if isinstance(index, dict) and index.get("year") and (url := refs.get("wikisource-index")):
        found.append(Statement(text=str(index["year"]), kind="produced", scope="witness", tier="attested",
                               source="wikisource", locator=f"{url}#year", fixed=True))
    return found


def documents(root: Path, corpora: Iterable[str] = CORPORA) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every (corpus, document row) under `root`, withdrawn documents left out. A document several corpora
    hold (a みんなで翻刻 entry in the platform's corpora and in the Ainu records) comes once from each."""
    import pyarrow.parquet as pq

    gone = withdrawn.documents()
    for corpus in corpora:
        path = root / corpus / "documents.parquet"
        if not path.exists():
            continue
        for row in pq.read_table(path).to_pylist():
            if row["id"] not in gone:
                yield corpus, row


def build(root: Path, cache: Path, *, fetch: bool = False, convert: bool = False, corpora: Iterable[str] = CORPORA,
          site: set[str] | None = None) -> tuple[list[DateClaim], dict[str, dict[str, Any]], Counter]:
    """Every document's claims, its resolved dates, and counts of what was found.

    `fetch` reads the 国書 records and manifests the cache lacks, only for the documents of `site` when it
    is given (the corpora hold many more documents than the site publishes); `convert` asks HuTime what
    its cache lacks.
    """
    gathered: dict[str, tuple[dict[str, Any], str, list[Statement]]] = {}
    shared: dict[tuple[str, str], list[Statement]] = {}
    identities: dict[str, set[tuple[str, str]]] = {}
    # The NDL training corpus names the original Honkoku v1 records, but its importer did not carry
    # their publication dates. Read that exact identifier from the cached upstream catalogue.
    v1_path = cache / "honkoku-data" / "v1" / "entries.csv"
    v1_attempted = v1_path.exists()
    v1 = {}
    if v1_path.exists():
        with v1_path.open(encoding="utf-8-sig", newline="") as handle:
            v1 = {r["暫定ID"]: r for r in csv.DictReader(handle)}
    for corpus, row in documents(root, corpora):
        mine = fetch and (site is None or row["id"] in site)
        found = statements(row, corpus, cache, fetch=mine)
        refs = _json(row.get("source_refs")) or {}
        if str(refs.get("ndl-minhon-ocr", "")).startswith("v1/"):
            if mine and not v1_attempted:
                v1_attempted = True
                try:
                    net.download("https://raw.githubusercontent.com/yuta1984/honkoku-data/master/v1/entries.csv",
                                 v1_path, expected="text")
                except net.DownloadError:
                    pass
                if v1_path.exists():
                    with v1_path.open(encoding="utf-8-sig", newline="") as handle:
                        v1 = {r["暫定ID"]: r for r in csv.DictReader(handle)}
            key = refs["ndl-minhon-ocr"].removeprefix("v1/")
            if value := v1.get(key, {}).get("出版年（西暦年月日）", "").strip():
                locator = ("https://github.com/yuta1984/honkoku-data/blob/master/v1/entries.csv"
                           f"#{key}/出版年（西暦年月日）")
                if m := re.fullmatch(r"(\d{4})[（(](\d{4}写)[）)]", value):
                    found += [Statement(text=text, kind=kind, scope="witness", tier="derived", source="honkoku-data",
                                        locator=locator, note=f"Publication field: {value}", fixed=True)
                              for text, kind in ((m[1], "exemplar"), (m[2], "copied"))]
                else:
                    # A leading dash has an unspecified endpoint; do not turn it into an exact year.
                    found.append(Statement(text=value, kind="other" if value.startswith("-") else "produced",
                                           scope="witness", tier="attested", source="honkoku-data", locator=locator,
                                           fixed=value.startswith("-")))
        keys = {("honkoku", str(refs["honkoku-data"]).lower())} if refs.get("honkoku-data") else set()
        if corpus not in OWN_DATING and (url := manifest_of(row)):
            keys.add(("manifest", url))
        identities.setdefault(row["id"], set()).update(keys)
        for key in keys:
            shared.setdefault(key, []).extend(found)
        if row["id"] not in gathered:
            gathered[row["id"]] = (row, corpus, found)
            continue
        first, named, held = gathered[row["id"]]
        # The first corpus names the document; a later one adds what its source states, and a known
        # production or origin where the first had none.
        known = {k: row.get(k) for k in ("production", "origin")
                 if first.get(k) in (None, "unknown") and row.get(k) not in (None, "unknown")}
        gathered[row["id"]] = ({**first, **known}, named, held + [s for s in found if s not in held])
    for document, (row, corpus, found) in gathered.items():
        for key in identities[document]:
            found.extend(s for s in shared[key] if s not in found)
    calendar = dates.HuTime(cache / "hutime", offline=not convert)
    japanese = [s.text for row, _, found in gathered.values() if row.get("origin") in JAPANESE_ORIGINS for s in found]
    candidates = set().union(*(dates.era_candidates(t) for t in japanese)) if japanese else set()
    if convert:
        calendar.prefetch("era", candidates)
        asked: dict[str, set[str]] = {"year": set(), "month": set(), "date": set()}
        for text in japanese:
            for kind, values in dates.year_questions(text, calendar).items():
                asked[kind] |= values
        for kind, values in asked.items():
            calendar.prefetch(kind, values)
    claims: list[DateClaim] = []
    resolved: dict[str, dict[str, Any]] = {}
    counts: Counter = Counter()
    for row, corpus, found in gathered.values():
        counts["documents"] += 1
        uses = calendar if row.get("origin") in JAPANESE_ORIGINS else None
        made: dict[str, DateClaim] = {}
        for s in found:
            kind = s.kind if s.fixed else dates.kind_of(s.text, s.kind)
            scope = "work" if kind == "composed" else s.scope
            c = dates.claim(row["id"], s.text, kind=kind, scope=scope, tier=s.tier, source=s.source,
                            locator=s.locator, calendar=uses, note=s.note, years=s.years, uncertain=s.uncertain)
            if c is None:
                counts["statements without a date"] += 1
                continue
            made.setdefault(c.id, c)
        mine = list(made.values())
        claims += mine
        counts["claims"] += len(mine)
        production = row.get("production") or "unknown"
        found_dates = dates.resolve(mine, production)
        if mine:
            counts["documents with a claim"] += 1
        if "witness" in found_dates:
            counts["documents with a witness date"] += 1
            counts[f"witness date: {found_dates['witness'].kind}"] += 1
        if "composed" in found_dates:
            counts["documents with a composition date"] += 1
        if any(r.status == "disputed" for r in found_dates.values()):
            counts["disputed"] += 1
        if found_dates:
            resolved[row["id"]] = {"document": row["id"], "corpus": corpus, "production": production,
                                   **{axis: r.as_dict() for axis, r in found_dates.items()}}
    return claims, resolved, counts


def write(out: Path, claims: list[DateClaim], resolved: dict[str, dict[str, Any]], counts: Counter) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "claims.jsonl").open("w", encoding="utf-8") as handle:
        for c in sorted(claims, key=lambda c: c.id):
            handle.write(c.model_dump_json() + "\n")
    with (out / "resolved.jsonl").open("w", encoding="utf-8") as handle:
        for key in sorted(resolved):
            handle.write(json.dumps(resolved[key], ensure_ascii=False) + "\n")
    (out / "counts.json").write_text(json.dumps(dict(sorted(counts.items())), ensure_ascii=False, indent=1),
                                     encoding="utf-8")


#: The fields of a claim that make its ledger value.
VALUE_FIELDS = ("text", "start", "end", "precision", "qualifier", "uncertain", "day", "calendar", "conversion", "note")


def assertion(claim: DateClaim) -> dict[str, Any]:
    """`claim` as the ledger holds it: subject, predicate (`date_<kind>`), value, tier, asserter and evidence."""
    return {"id": claim.id, "subject": claim.document, "predicate": f"date_{claim.kind}", "tier": claim.tier,
            "value": {"of": claim.scope, **{k: getattr(claim, k) for k in VALUE_FIELDS}},
            "asserted_by": f"source:{claim.source}", "evidence": {"kind": "source", "ref": claim.source,
                                                                   "locator": claim.locator}}
