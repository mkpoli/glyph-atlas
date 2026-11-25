"""Build data/vocab/tongyong-guifan-forms.tsv: what the 通用规范汉字表 prints beside each of its characters.

附件1 of the table, 规范字与繁体字、异体字对照表, gives the 繁体字 and the 异体字 of a 规范字. A row is one
异体字 of one full form (`full`) of a character (`char`): the full form itself where the table prints no other,
and the character itself as its own full form where the table marks it with ～ (后 stands for 后 and for 後) or
has 异体字 alone for it. `note` is what the table says of one of them (瞭：读liào时不简化作“了”).

    uv run scripts/build_tongyong_forms.py

The rows are those of mandel59's transcription, itself made from Wikisource's, at a pinned revision. Network
access happens here and nowhere else; the download is cached under `cache/tongyong/`.
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import yaml

from glyph_atlas.net import download

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "vocab" / "tongyong-guifan-forms.tsv"
SOURCE = ROOT / "data" / "sources" / "tongyong-guifan.yaml"
SHA256 = "7e7ed4d51f71fa0afd4849d72b79cc5d13828c15a709b75f28914d3a7863ada0"
COLUMNS = ["position", "char", "full", "variant", "note"]


def rows(text: str) -> list[list[str]]:
    return [[str(int(row["序号"])), row["规范字"], row["繁体字"], row["异体字"], row["註解"]]
            for row in csv.DictReader(io.StringIO(text), delimiter="\t")]


def main() -> None:
    source = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    path = download(source["access"]["forms"], ROOT / "cache" / "tongyong" / "variants.txt")
    if (found := hashlib.sha256(path.read_bytes()).hexdigest()) != SHA256:
        raise SystemExit(f"{path}: sha256 {found}, not {SHA256}")
    table = rows(path.read_text(encoding="utf-8"))
    header = [
        "# What the 通用规范汉字表 (2013) prints beside its characters, 附件1; see scripts/build_tongyong_forms.py.",
        f"# source {source['id']}: {source['name']}; {source['licence']} ({source['licence_evidence']}); {source['attribution']}",
        "\t".join(COLUMNS),
    ]
    TARGET.write_text("\n".join([*header, *("\t".join(row) for row in table)]) + "\n", encoding="utf-8")
    print(f"{len(table)} rows -> {TARGET}")


if __name__ == "__main__":
    main()
