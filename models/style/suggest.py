"""Suggest a script style for a sample of one corpus's kanji crops, and lay them out for a person.

The teacher is the checkpoint `models/style/train.py` writes. A crop is the display crop the site
serves (`MediaCache.corpus_image`, 480 pixels), prepared by `glyph_atlas.style_teacher`. The sample
is every Han unit whose `sha1(id)` falls under a threshold, so the same crops are drawn on every run,
and a later run over more of the corpus keeps the earlier ones.

The output goes to `work/style-suggestions/{corpus}/`, outside the dataset tables:
`suggestions.jsonl` holds one line per crop with the style, the probability of each style and the
checkpoint's SHA-256, and `sheet.html` shows the crops grouped by suggested style, most confident
first, beside what the teacher saw. The suggestions derive from a CC BY-NC 4.0 dataset
(`models/style/README.md`) and are not published.

    python models/style/suggest.py hng --share 0.02
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from glyph_atlas.style_teacher import CLASSES, prepare, tensor

ROOT = Path(__file__).resolve().parents[2]


def sampled(row_id: str, share: float) -> bool:
    return int(hashlib.sha1(row_id.encode()).hexdigest()[:8], 16) < share * 0x100000000


def crops(corpus_name: str, share: float, limit: int):
    """(unit id, display-crop path, source document) for the sampled Han units that have an image."""
    import pyarrow.dataset as ds

    from glyph_atlas.corpus import sources
    from glyph_atlas.corpus.api import CorpusAPI
    from glyph_atlas.corpus.index import _char_of_codepoint, _character_unit_row, _MetaCache, _unit_row
    from glyph_atlas.review.media import MediaCache

    corpus = next((c for c in sources.discover("work") if c.name == corpus_name), None)
    if corpus is None:
        raise SystemExit(f"no corpus named {corpus_name}")
    api = CorpusAPI("work", "work/corpus-index", autobuild=False)
    media, context = MediaCache(corpus_root=Path("work")), _MetaCache(corpus)
    dataset = ds.dataset([str(p) for p in corpus.parquet_files("units")], format="parquet")
    found = 0
    for batch in dataset.scanner(batch_size=4096).to_batches():
        for row in batch.to_pylist():
            if row.get("script") != "han" or not sampled(row["id"], share) or not _character_unit_row(row):
                continue
            char = _char_of_codepoint(row.get("unicode")) or row.get("text_source")
            if not char:
                continue
            joined = _unit_row(corpus, context, row, char, row.get("unicode") or "")
            api._decorate_unit(joined, width=480)
            image = media.corpus_image(joined, api.crops, edge=480) if joined.get("render_available") else None
            if not image:
                continue
            try:
                path = media.materialize(image.rsplit("/", 1)[-1].removesuffix(".webp"))
            except (OSError, ValueError):
                continue
            yield row["id"], char, path, joined.get("document_id")
            found += 1
            if found >= limit:
                return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpus")
    parser.add_argument("--share", type=float, default=0.02, help="share of Han units drawn (default 0.02)")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "models/style/artifacts/best.pt")
    parser.add_argument("--out", type=Path, default=Path("work/style-suggestions"))
    parser.add_argument("--per-style", type=int, default=60, help="crops shown per style on the sheet")
    args = parser.parse_args()

    import timm
    import torch

    state = torch.load(args.checkpoint, map_location="cuda")
    model = timm.create_model(state["checkpoint"], pretrained=False, num_classes=len(CLASSES)).cuda().eval()
    model.load_state_dict(state["model"])
    checkpoint_sha = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    out = args.out / args.corpus
    (out / "seen").mkdir(parents=True, exist_ok=True)
    rows = []
    batch = []

    def flush():
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            logits = model(torch.as_tensor(np.stack([tensor(p) for *_, p in batch]), device="cuda")).float()
        for (unit, char, path, document, prepared), p in zip(batch, torch.softmax(logits, 1).cpu().numpy(), strict=True):
            seen = out / "seen" / (hashlib.sha1(unit.encode()).hexdigest() + ".png")
            prepared.save(seen)
            rows.append({"id": unit, "character": char, "document_id": document, "crop": str(path),
                         "seen": seen.name, "style": CLASSES[int(p.argmax())],
                         "p": {name: round(float(v), 4) for name, v in zip(CLASSES, p, strict=True)},
                         "basis": "calli-tongji-teacher", "checkpoint_sha256": checkpoint_sha})
        batch.clear()

    for unit, char, path, document in crops(args.corpus, args.share, args.limit):
        with Image.open(path) as image:
            batch.append((unit, char, path, document, prepare(image)))
        if len(batch) == 64:
            flush()
    if batch:
        flush()
    with (out / "suggestions.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    by = defaultdict(list)
    for row in rows:
        by[row["style"]].append(row)
    parts = [f"<h1>{html.escape(args.corpus)}: {len(rows)} crops</h1>",
             "<p>Suggested by the Calli-Tongji teacher (CC BY-NC 4.0), not reviewed. Each pair is the crop and what the teacher saw.</p>"]
    for style in CLASSES:
        group = sorted(by[style], key=lambda r: -r["p"][style])
        parts.append(f"<h2>{style}: {len(group)}</h2><div class=grid>")
        for row in group[:args.per_style]:
            crop = Path(row["crop"]).resolve().as_uri()
            parts.append(f'<figure><img src="{crop}"><img src="seen/{row["seen"]}"><figcaption>{html.escape(row["character"])} '
                         f'{row["p"][style]:.2f}<br>{html.escape(row["document_id"] or "")}</figcaption></figure>')
        parts.append("</div>")
    style = ("body{font:13px system-ui;margin:16px}.grid{display:flex;flex-wrap:wrap;gap:8px}"
             "figure{margin:0;width:150px}img{width:72px;height:72px;object-fit:contain;background:#fff;border:1px solid #ccc}"
             "figcaption{font-size:11px;word-break:break-all}")
    (out / "sheet.html").write_text(f"<!doctype html><meta charset=utf-8><title>{args.corpus} styles</title><style>{style}</style>"
                                    + "".join(parts), encoding="utf-8")
    counts = {name: len(by[name]) for name in CLASSES}
    print(json.dumps({"corpus": args.corpus, "crops": len(rows), "styles": counts}, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
