"""Date fields in the holder catalogues linked by otherwise sparse IIIF manifests.

Only known catalogue layouts are read. Digitization dates, descriptions of historical events,
and modern catalogue-maintenance timestamps never supply a witness date.
"""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from . import net


class DateFields(HTMLParser):
    """Text in explicitly named Drupal date fields, excluding the field label."""

    def __init__(self, fields: set[str]):
        super().__init__()
        self.fields = fields
        self.depth = 0
        self.active = None
        self.item = None
        self.parts: list[str] = []
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            self.depth += 1
            classes = set(dict(attrs).get("class", "").split())
            if self.active is None and classes & self.fields:
                self.active = self.depth
            if self.active and "field--item" in classes and self.item is None:
                self.item = self.depth
                self.parts = []
        elif tag == "br" and self.item:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.item:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self.item == self.depth:
            self.values.append("".join(self.parts).strip())
            self.item = None
        if self.active == self.depth:
            self.active = None
        self.depth -= 1


def catalogue_url(manifest_url: str) -> tuple[str, str] | None:
    url = urlsplit(manifest_url)
    if url.hostname in {"iiif.dl.itc.u-tokyo.ac.jp", "da.dl.itc.u-tokyo.ac.jp"} and (
            m := re.search(r"/iiif/([0-9a-f-]{36})/manifest$", url.path)):
        return "tokyo", f"https://da.dl.itc.u-tokyo.ac.jp/portal/assets/{m[1]}?_format=json"
    if url.hostname == "shimuchi.lib.u-ryukyu.ac.jp" and url.path.endswith("/manifest.json"):
        return "ryukyu", manifest_url.removesuffix("/manifest.json")
    if url.hostname == "rmda.kulib.kyoto-u.ac.jp" and (
            m := re.search(r"/metadata_manifest/(RB[0-9]+)/manifest\.json$", url.path)):
        return "kyoto", f"https://rmda.kulib.kyoto-u.ac.jp/item/{m[1]}"
    return None


def catalogue_entries(manifest_url: str, cache: Path, *, fetch: bool = False):
    from .date_claims import _text

    target = catalogue_url(manifest_url)
    if target is None:
        return
    provider, url = target
    path = cache / "date-catalogues" / (hashlib.sha256(url.encode()).hexdigest() + ".txt")
    if fetch and not path.exists():
        try:
            net.download(url, path, expected="json" if provider == "tokyo" else None, retries=2, timeout=20)
        except net.DownloadError:
            return
    if not path.exists():
        return
    body = path.read_text(encoding="utf-8-sig")
    if provider == "tokyo":
        try:
            record = json.loads(body)
        except json.JSONDecodeError:
            return
        if not isinstance(record, dict):
            return
        entries = [(key, _text(record[key])) for key in ("dcterms:date", "dcterms:issued") if record.get(key)]
        # Some collections put a labelled publication-year field inside the description array.
        for description in record.get("dcterms:description") or []:
            if isinstance(description, str) and (match := re.match(r"^(刊年|出版年)\s*[:：]\s*(.+)$", description)):
                entries.append((match[1], match[2]))
        yield url, entries
    else:
        fields = {"field--name-field-time-series"} if provider == "ryukyu" else {
            "field--name-field-year", "field--name-field-japanese-year"}
        parser = DateFields(fields)
        parser.feed(body)
        yield url, [("Date", value) for value in parser.values]
