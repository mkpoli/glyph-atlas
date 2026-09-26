"""Propose a style for each document from the teacher's suggestions, and lay them out for review.

The input is `work/style-suggestions/{corpus}/suggestions.jsonl` from `suggest.py`, one per corpus
named. A document with at least `--min-crops` suggested crops gets a proposal: the style most of
its crops were given, marked clear when that style holds at least `--clear` of them. The output is
`review.html`, a self-contained page (the crops are embedded) that shows every document with its
crops, the shares and a style menu. Every menu starts empty, and a proposal enters it only when the
person presses its button, so no document is recorded as reviewed that nobody looked at. Its button copies the chosen entries in the
form `data/vocab/document-styles.yaml` takes, to paste under its `documents:`. The page records the
teacher's checkpoint and the number of crops, not the shares, which derive from the teacher's
CC BY-NC training data. A document the file already confirms is left off the page, and choices are
kept in the browser only for this exact page, so a page made from other suggestions starts empty.
All suggestions must come from one checkpoint.

Run it from the repository root:

    python models/style/propose_documents.py hng codh-full --out work/style-suggestions/review.html
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

from glyph_atlas import style, tables
from glyph_atlas.corpus import sources
from glyph_atlas.schema import Document
from glyph_atlas.style_teacher import CLASSES

THUMB = 72


def proposals(rows: list[dict], *, min_crops: int, clear: float) -> list[dict]:
    """One proposal per document with enough crops, in document id order."""
    by = defaultdict(list)
    for row in rows:
        by[row["document_id"]].append(row)
    found = []
    for document, group in sorted(by.items()):
        if len(group) < min_crops:
            continue
        counts = Counter(row["style"] for row in group)
        top, n = counts.most_common(1)[0]
        found.append({"document_id": document, "crops": len(group), "counts": dict(counts), "style": top,
                      "share": round(n / len(group), 3), "clear": n / len(group) >= clear,
                      "checkpoint_sha256": group[0]["checkpoint_sha256"], "rows": group})
    return found


def thumbnail(path: str) -> str:
    with Image.open(path) as image:
        image = image.convert("L")
        image.thumbnail((THUMB, THUMB))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def titles(corpora: list[str]) -> dict[str, str]:
    found = {}
    for corpus in sources.discover("work"):
        path = corpus.table("documents") if corpus.name in corpora else None
        if path:
            found.update({document.id: document.title for document in tables.read(path, Document)})
    return found


def page(documents: list[dict], names: dict[str, str], per_document: int) -> str:
    options = [value for value in style.vocabulary() if value != style.UNASSESSED]
    digest = hashlib.sha256(json.dumps([[d["document_id"], d["style"], d["crops"], d["checkpoint_sha256"]]
                                        for d in documents]).encode()).hexdigest()[:16]
    attr = html.escape
    items = []
    for doc in documents:
        shares = ", ".join(f"{name} {doc['counts'].get(name, 0)}" for name in CLASSES if doc["counts"].get(name))
        menu = "".join(f'<option value="{value}">{value}</option>' for value in ["", *options])
        crops = "".join(f'<img src="{thumbnail(row["crop"])}" title="{attr(row["character"])} {attr(row["style"])}">'
                        for row in sorted(doc["rows"], key=lambda r: r["id"])[:per_document])
        items.append(
            f'<section data-id="{attr(doc["document_id"])}" data-crops="{doc["crops"]}" '
            f'data-suggested="{attr(doc["style"])}" data-checkpoint="{attr(doc["checkpoint_sha256"])}">'
            f'<h2>{html.escape(names.get(doc["document_id"], ""))} <code>{html.escape(doc["document_id"])}</code></h2>'
            f'<p>Suggested <b>{doc["style"]}</b> for {doc["crops"]} kanji crops ({shares})'
            f'{" (clear)" if doc["clear"] else " (split)"}</p>'
            f'<div class=crops>{crops}</div>'
            f'<label>Style <select>{menu}</select></label> <button type=button class=use>Use {doc["style"]}</button> '
            f'<label>Note <input size=50></label></section>')
    script = """
const KEY = 'document-styles-review:' + document.body.dataset.page;
function save() {
  const state = {};
  for (const s of document.querySelectorAll('section'))
    state[s.dataset.id] = [s.querySelector('select').value, s.querySelector('input').value];
  try { localStorage.setItem(KEY, JSON.stringify(state)) } catch {}
}
function restore() {
  let state = {};
  try { state = JSON.parse(localStorage.getItem(KEY) || '{}') } catch {}
  for (const s of document.querySelectorAll('section')) {
    const saved = state[s.dataset.id];
    if (saved) { s.querySelector('select').value = saved[0]; s.querySelector('input').value = saved[1] }
  }
}
function yaml() {
  const now = new Date(), two = n => String(n).padStart(2, '0');
  const day = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`, out = [];
  for (const s of document.querySelectorAll('section')) {
    const value = s.querySelector('select').value, note = s.querySelector('input').value.trim();
    if (!value) continue;
    out.push(`  ${JSON.stringify(s.dataset.id)}:`, `    style: ${value}`, `    evidence:`,
      `      - source: reviewer`, `        reviewed: ${day}`,
      `        note: ${JSON.stringify(note || 'kanji crops compared with the style teacher suggestion')}`,
      `      - source: style-teacher`, `        checkpoint_sha256: ${s.dataset.checkpoint}`,
      `        crops: ${s.dataset.crops}`, `        suggested: ${s.dataset.suggested}`);
  }
  return out.join('\\n') + '\\n';
}
document.addEventListener('click', event => {
  if (!event.target.matches('.use')) return;
  const section = event.target.closest('section');
  section.querySelector('select').value = section.dataset.suggested;
  save();
});
document.addEventListener('change', save);
document.addEventListener('input', save);
restore();
document.querySelector('#copy').addEventListener('click', async () => {
  const text = yaml();
  const out = document.querySelector('#out');
  out.value = text; out.textContent = text;
  try { await navigator.clipboard.writeText(text); document.querySelector('#status').textContent = 'Copied.' }
  catch { document.querySelector('#status').textContent = 'Copy from the box below.' }
});
"""
    css = (":root{color-scheme:light dark}body{font:14px system-ui,sans-serif;margin:16px auto;max-width:1100px;padding:0 16px}"
           "section{border-top:1px solid #ccc;padding:8px 0}h2{font-size:15px;margin:4px 0}"
           ".crops{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}"
           f".crops img{{width:{THUMB}px;height:{THUMB}px;object-fit:contain;background:#fff;border:1px solid #ddd}}"
           "textarea{width:100%;height:200px;font:12px monospace}textarea:empty{display:none}"
           "@media(prefers-color-scheme:dark){body{background:#1b1b1b;color:#eee}section{border-color:#444}}")
    header = ("<h1>Document styles to confirm</h1><p>The suggestions come from the style teacher "
              "(trained on Calli-Tongji, CC BY-NC 4.0) and nobody has reviewed them. Choose a style only "
              "where the crops show it; leave the menu empty to record nothing. Paste the copied entries under documents: in "
              "data/vocab/document-styles.yaml, in place of {} while it is empty. The button beside a menu fills in the suggestion. Choices are kept in this "
              "browser. <button id=copy>Copy YAML</button> <span id=status></span></p>"
              "<textarea id=out readonly></textarea>")
    return (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>Document styles</title><style>{css}</style><body data-page={digest}>{header}{''.join(items)}"
            f"<script>{script}</script>")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpora", nargs="+")
    parser.add_argument("--min-crops", type=int, default=12)
    parser.add_argument("--clear", type=float, default=0.8)
    parser.add_argument("--per-document", type=int, default=16)
    parser.add_argument("--out", type=Path, default=Path("work/style-suggestions/review.html"))
    args = parser.parse_args()
    if not Path("work").is_dir():
        raise SystemExit("run this from the repository root, which holds work/")
    rows = []
    for corpus in args.corpora:
        path = Path("work/style-suggestions") / corpus / "suggestions.jsonl"
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    checkpoints = {row["checkpoint_sha256"] for row in rows}
    if len(checkpoints) != 1:
        raise SystemExit(f"the suggestions come from {len(checkpoints)} checkpoints; rerun suggest.py for one")
    done = style.confirmed()
    documents = [doc for doc in proposals(rows, min_crops=args.min_crops, clear=args.clear)
                 if doc["document_id"] not in done]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page(documents, titles(args.corpora), args.per_document), encoding="utf-8")
    clear = sum(doc["clear"] for doc in documents)
    print(json.dumps({"documents": len(documents), "already_confirmed": len(done), "clear": clear,
                      "split": len(documents) - clear,
                      "styles": dict(Counter(doc["style"] for doc in documents if doc["clear"])),
                      "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
