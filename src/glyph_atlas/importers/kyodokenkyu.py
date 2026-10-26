"""Collect the kanbun editors and scan references linked by Kyoto's research index.

A text page is an upstream file, explicitly marked as a logical text segment. It
has no image, dimensions or inferred relationship to the separately catalogued
scans. Sentence records retain their complete CoNLL-U blocks in metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urljoin, urlsplit
from uuid import uuid4

import yaml

from .. import net, tables
from ..schema import Document, Licence, Line, Page, PageText, Register, Rights
from .honkoku_data import canvas_page, canvases
from .honkoku_queue import publish_directory
from .iiif_collection import text as iiif_text

SOURCE = "kyodokenkyu"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.images: list[str] = []
        self.options: dict[str, str] = {}
        self.option: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "img" and attrs.get("src"):
            self.images.append(attrs["src"])
        elif tag == "option":
            self.option = attrs.get("value")
            if self.option:
                self.options[self.option] = ""

    def handle_endtag(self, tag):
        if tag == "option":
            self.option = None

    def handle_data(self, data):
        if self.option:
            self.options[self.option] += data


def editor_sections(html: str) -> dict[str, str]:
    parser = Links()
    parser.feed(html)
    sections = {path: label.strip() for path, label in parser.options.items()}
    if not sections:
        sections = {path: "" for path in re.findall(r'\.volpath\s*=\s*"([^"]+)"', html)}
    if not sections:
        raise ValueError("Editor has no corpus sections")
    return sections


def sentences(raw: str) -> list[tuple[str, str]]:
    """Return the declared text and original block of each CoNLL-U sentence."""
    result = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip()):
        if not block:
            continue
        declared = []
        tokens = 0
        for row in block.splitlines():
            if row.startswith("# text = "):
                declared.append(row[len("# text = "):])
            elif row and not row.startswith("#"):
                if len(row.split("\t")) != 10:
                    raise ValueError("Invalid CoNLL-U row: expected ten columns")
                tokens += 1
        if len(declared) != 1 or not tokens:
            raise ValueError("CoNLL-U sentence requires one text comment and token rows")
        result.append((declared[0], block))
    return result


def fetch(resource: dict, cache: Path) -> Path:
    """Fetch and verify a pinned input, including inputs already cached."""
    path = cache / resource["sha256"]
    net.download(resource["url"], path)
    if path.stat().st_size != resource["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != resource["sha256"]:
        raise ValueError(f"Snapshot mismatch: {resource['url']}")
    return path


def text_records(book: dict, archive: Path, editor: str, repo: dict):
    sections = editor_sections(editor)
    doc_id = f"{SOURCE}:text:{book['id']}"
    revision = repo["revision"]
    document = Document(
        id=doc_id, title=book["title"], text_register=Register.KANBUN,
        source_refs={SOURCE: book["editor"]["url"], "repository": repo["url"], "revision": revision},
        text_rights=Rights(licence=Licence.UNKNOWN, attribution="古典中国語コーパス共同研究班",
                           evidence=repo["url"]),
        meta={"collection": SOURCE, "record_kind": "annotated-text", "sections": sections,
              "page_alignment": "unavailable", "text_licence_status": "not stated in repository"},
    )
    pages, texts, lines = [], [], []
    with zipfile.ZipFile(archive) as zipped:
        members = {}
        for name in zipped.namelist():
            if "/" not in name or name.endswith("/"):
                continue
            path = name.split("/", 1)[1]
            if PurePosixPath(path).parent.as_posix() in sections and path.endswith(".txt"):
                if path in members:
                    raise ValueError(f"Duplicate archive path: {path}")
                members[path] = name
        present = {str(PurePosixPath(path).parent) for path in members}
        if missing := set(sections) - present:
            raise ValueError(f"Missing corpus sections for {book['id']}: {sorted(missing)}")
        for seq, (path, member) in enumerate(sorted(members.items())):
            raw = zipped.read(member).decode("utf-8")
            parsed = sentences(raw)
            if not parsed:
                raise ValueError(f"Empty text file: {path}")
            page_id = f"{doc_id}:{path}"
            locator = repo["url"] + "/-/blob/" + revision + "/" + quote(path, safe="/")
            pages.append(Page(id=page_id, document_id=doc_id, seq=seq, image="", width=0, height=0,
                              transcription={"source": SOURCE, "entry": path, "revision": revision},
                              meta={"record_kind": "text-segment", "source_url": locator,
                                    "source_path": path, "sha256": hashlib.sha256(raw.encode()).hexdigest()}))
            texts.append(PageText(page_id=page_id, source=SOURCE, revision=revision,
                                  text_raw="\n".join(text for text, _ in parsed)))
            for sentence_seq, (text, block) in enumerate(parsed):
                lines.append(Line(id=f"{page_id}:s{sentence_seq}", page_id=page_id, seq=sentence_seq,
                                  text_raw=text, text=text,
                                  meta={"record_kind": "sentence", "conllu": block, "source_url": locator}))
    return document, pages, texts, lines


def scan_records(book: dict, payload: bytes):
    doc_id = f"{SOURCE}:scan:{book['id']}"
    refs = {SOURCE: book["source"]["url"], "catalogue": book["catalogue"]}
    pages = []
    meta = {"collection": SOURCE, "record_kind": "scan", "page_alignment": "unavailable"}
    if book["format"] == "iiif":
        manifest = json.loads(payload)
        refs["iiif-manifest"] = book["source"]["url"]
        meta["manifest_metadata"] = manifest.get("metadata", [])
        meta["holder_statement"] = manifest.get("requiredStatement") or manifest.get("attribution")
        meta["holder_rights_url"] = manifest.get("rights") or manifest.get("license")
        title = iiif_text(manifest.get("label")) or book["title"]
        for seq, canvas in enumerate(canvases(manifest)):
            canvas_id, image, width, height = canvas_page(canvas)
            identity = canvas_id or image
            if not identity or not image:
                raise ValueError(f"Canvas lacks an image or identity: {book['id']} {seq}")
            suffix = hashlib.sha256(identity.encode()).hexdigest()[:20]
            pages.append(Page(id=f"{doc_id}:{suffix}", document_id=doc_id, seq=seq,
                              canvas=canvas_id, image=image, width=width, height=height))
    else:
        title = book["title"]
        parser = Links()
        parser.feed(payload.decode("euc_jp"))
        images = dict.fromkeys(urljoin(book["source"]["url"], src) for src in parser.images)
        for seq, url in enumerate(images):
            if urlsplit(url).scheme not in {"http", "https"}:
                raise ValueError(f"Unsupported image URL: {url}")
            suffix = hashlib.sha256(url.encode()).hexdigest()[:20]
            pages.append(Page(id=f"{doc_id}:{suffix}", document_id=doc_id, seq=seq, image=url,
                              width=0, height=0, meta={"dimensions_status": "not fetched"}))
    if not pages:
        raise ValueError(f"No scans found: {book['id']}")
    document = Document(id=doc_id, title=title, holder=book["holder"], source_refs=refs,
                        image_rights=Rights(licence=Licence(book["image_licence"]), holder=book["holder"],
                                            attribution=book["holder"], evidence=book["source"]["url"]),
                        meta=meta)
    return document, pages


def collect(source: Path, out: Path, cache: Path) -> dict:
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    if out.exists() and not out.is_symlink():
        raise ValueError("Output must be absent or a snapshot symlink; choose a new output directory")
    cache.mkdir(parents=True, exist_ok=True)
    index = fetch(config["index"], cache)
    parsed_index = Links()
    parsed_index.feed(index.read_bytes().decode("euc_jp"))
    links = list(dict.fromkeys(urljoin(config["url"], url) for url in parsed_index.links))
    archives = {name: fetch(repo["archive"], cache) for name, repo in config["repositories"].items()}
    documents, pages, texts, lines = [], [], [], []
    counts = {}
    for book in config["corpora"]:
        editor = fetch(book["editor"], cache).read_text(encoding="utf-8")
        doc, pp, tt, ll = text_records(book, archives[book["repository"]], editor,
                                     config["repositories"][book["repository"]])
        documents.append(doc)
        pages.extend(pp)
        texts.extend(tt)
        lines.extend(ll)
        counts[book["id"]] = {"text_segments": len(pp), "sentences": len(ll)}
    for book in config["scans"]:
        doc, pp = scan_records(book, fetch(book["source"], cache).read_bytes())
        documents.append(doc)
        pages.extend(pp)
        counts[book["id"]] = {"scan_pages": len(pp)}
    summary = {"corpora": counts, "documents": len(documents), "text_segments": len(texts),
               "sentences": len(lines), "scan_pages": len(pages) - len(texts), "character_crops": 0}
    # Stage and validate the complete result before replacing an existing import.
    versions = out.parent / f".{out.name}-versions"
    versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=versions) as tmp:
        staging = Path(tmp)
        for name, records, model in [("documents", documents, Document), ("pages", pages, Page),
                                     ("page_texts", texts, PageText), ("lines", lines, Line)]:
            tables.write(staging / f"{name}.parquet", records, model)
        errors = tables.Dataset(staging).validate()
        if errors:
            raise ValueError("Invalid import: " + "; ".join(errors[:10]))
        tables.Dataset(staging).merge([], staging, command="collect kyodokenkyu")
        manifest = json.loads((staging / "MANIFEST.json").read_text(encoding="utf-8"))
        manifest["collection"] = summary
        manifest["source_config_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        (staging / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
        (staging / "source-links.json").write_text(json.dumps({"source": config["url"], "links": links},
                                                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        generation = versions / uuid4().hex
        publish_directory(staging, generation)
        # Retain prior snapshots for readers that pinned them before this switch.
        with tables.locked(out):
            link = out.with_name(f".{out.name}-{uuid4().hex}.next")
            try:
                link.symlink_to(generation.relative_to(out.parent), target_is_directory=True)
                os.replace(link, out)
                descriptor = os.open(out.parent, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                link.unlink(missing_ok=True)
    return summary
