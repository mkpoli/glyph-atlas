"""Propose a style for each document from the teacher's suggestions, and lay them out for review.

The input is `work/style-suggestions/{corpus}/suggestions.jsonl` from `suggest.py`, one per corpus
named. A document with at least `--min-crops` suggested crops gets a proposal: the style most of
its crops were given, marked clear when that style holds at least `--clear` of them. The output is
`review.html`, a self-contained page (the crops are embedded) that shows every document with its
crops, the shares and a style menu. Every menu starts empty, and a proposal enters it only when the
person presses its button, so no document is recorded as reviewed that nobody looked at. Its button copies the chosen entries in the
form `data/vocab/document-styles.yaml` takes, to paste under its `documents:`. The page is written
to be published as a private Claude artifact: a fragment with its own title, themed for light and
dark, with the crops embedded. The page records the
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


CREDITS = {
    "hng": "漢字字体規範史データセット (HNG), CC BY-SA 4.0",
    "codh-full": "日本古典籍くずし字データセット (国文研・CODH), CC BY-SA 4.0",
}

CSS = """
:root{--bg:#f3f5f2;--surface:#ffffff;--ink:#1d2126;--muted:#59606a;--line:#d6dad4;--accent:#2d4f86;
--clear:#2e6b45;--split:#8f5a10;--paper:#ffffff;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141619;--surface:#1c1f23;--ink:#e6e8ea;
--muted:#9ba2ab;--line:#30353b;--accent:#a3bbe6;--clear:#82c9a0;--split:#e2b56a;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#141619;--surface:#1c1f23;--ink:#e6e8ea;--muted:#9ba2ab;--line:#30353b;
--accent:#a3bbe6;--clear:#82c9a0;--split:#e2b56a;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding-inline:16px;padding-block:8px 48px}
h1{font-size:1.5rem;margin:12px 0 4px;text-wrap:balance}
.lede{color:var(--muted);max-width:68ch;margin:0 0 12px}
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:2;background:var(--bg);border-bottom:1px solid var(--line);
display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding-block:10px}
.bar .count{font-variant-numeric:tabular-nums}
button{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:4px 12px;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--bg);font-weight:600}
button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
textarea{width:100%;min-height:180px;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);
background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:8px;box-sizing:border-box}
.doc{display:grid;gap:8px;padding-block:14px;border-bottom:1px solid var(--line)}
.doc header{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.doc h2{margin:0;font:600 1.1rem/1.3 "Shippori Mincho","Noto Serif JP","Hiragino Mincho ProN","Yu Mincho",serif}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
.pill{font-size:12px;letter-spacing:.03em;border:1px solid currentColor;border-radius:999px;padding:0 8px}
.pill.clear{color:var(--clear)}.pill.split{color:var(--split)}
.why{margin:0;color:var(--muted)}
.why b{color:var(--ink)}
.crops{display:grid;grid-template-columns:repeat(auto-fill,minmax(56px,1fr));gap:4px;max-width:100%}
.crops img{width:100%;aspect-ratio:1;object-fit:contain;background:var(--paper);border:1px solid var(--line);border-radius:3px}
.controls{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
.controls label{display:flex;gap:6px;align-items:center}
select,input{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:3px 6px;max-width:100%}
.controls input{width:min(40ch,70vw)}
.doc.chosen{background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 10%,transparent),transparent 60%)}
footer{color:var(--muted);font-size:13px;margin-top:24px}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""

SCRIPT = """
const root = document.querySelector('#review');
const KEY = 'document-styles-review:' + root.dataset.page;
const docs = () => [...root.querySelectorAll('.doc')];
function refresh() {
  let chosen = 0;
  for (const d of docs()) { const on = !!d.querySelector('select').value; d.classList.toggle('chosen', on); chosen += on }
  root.querySelector('.count').textContent = `${chosen} of ${docs().length} chosen`;
}
function save() {
  const state = {};
  for (const d of docs()) state[d.dataset.id] = [d.querySelector('select').value, d.querySelector('input').value];
  try { localStorage.setItem(KEY, JSON.stringify(state)) } catch {}
  refresh();
}
function restore() {
  let state = {};
  try { state = JSON.parse(localStorage.getItem(KEY) || '{}') } catch {}
  for (const d of docs()) {
    const saved = state[d.dataset.id];
    if (saved) { d.querySelector('select').value = saved[0]; d.querySelector('input').value = saved[1] }
  }
  refresh();
}
function yaml() {
  const now = new Date(), two = n => String(n).padStart(2, '0');
  const day = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`, out = [];
  for (const d of docs()) {
    const value = d.querySelector('select').value, note = d.querySelector('input').value.trim();
    if (!value) continue;
    out.push(`  ${JSON.stringify(d.dataset.id)}:`, `    style: ${value}`, `    evidence:`,
      `      - source: reviewer`, `        reviewed: ${day}`,
      `        note: ${JSON.stringify(note || 'kanji crops compared with the style teacher suggestion')}`,
      `      - source: style-teacher`, `        checkpoint_sha256: ${d.dataset.checkpoint}`,
      `        crops: ${d.dataset.crops}`, `        suggested: ${d.dataset.suggested}`);
  }
  return out.length ? out.join('\\n') + '\\n' : '';
}
root.addEventListener('click', async event => {
  const use = event.target.closest('.use');
  if (use) { const d = use.closest('.doc'); d.querySelector('select').value = d.dataset.suggested; save(); return }
  if (!event.target.closest('#copy')) return;
  const text = yaml(), out = root.querySelector('#out'), status = root.querySelector('#status');
  if (!text) { status.textContent = 'Choose a style for at least one document first.'; out.hidden = true; return }
  out.value = text; out.hidden = false;
  try { await navigator.clipboard.writeText(text); status.textContent = 'Copied. Paste under documents: in data/vocab/document-styles.yaml.' }
  catch { out.focus(); out.select(); status.textContent = 'Selected below. Copy it with your keyboard.' }
});
root.addEventListener('change', save);
root.addEventListener('input', save);
restore();
"""


def page(documents: list[dict], names: dict[str, str], per_document: int, corpora: list[str]) -> str:
    """The review page as an artifact fragment: a title, styles, the page and its script."""
    options = [value for value in style.vocabulary() if value != style.UNASSESSED]
    digest = hashlib.sha256(json.dumps([[d["document_id"], d["style"], d["crops"], d["checkpoint_sha256"]]
                                        for d in documents]).encode()).hexdigest()[:16]
    attr = html.escape
    clear = sum(doc["clear"] for doc in documents)
    items = []
    for doc in documents:
        slug = hashlib.sha1(doc["document_id"].encode()).hexdigest()[:10]
        shares = ", ".join(f"{name} {doc['counts'].get(name, 0)}" for name in CLASSES if doc["counts"].get(name))
        menu = "".join(f'<option value="{value}">{value or "—"}</option>' for value in ["", *options])
        crops = "".join(f'<img src="{thumbnail(row["crop"])}" alt="{attr(row["character"])}" '
                        f'title="{attr(row["character"])}: suggested {attr(row["style"])}">'
                        for row in sorted(doc["rows"], key=lambda r: r["id"])[:per_document])
        state = "clear" if doc["clear"] else "split"
        items.append(
            f'<article class=doc data-id="{attr(doc["document_id"])}" data-crops="{doc["crops"]}" '
            f'data-suggested="{attr(doc["style"])}" data-checkpoint="{attr(doc["checkpoint_sha256"])}">'
            f'<header><h2>{attr(names.get(doc["document_id"], ""))}</h2><code>{attr(doc["document_id"])}</code>'
            f'<span class="pill {state}">{state}</span></header>'
            f'<p class=why>Suggested <b>{attr(doc["style"])}</b> for {doc["crops"]} kanji crops: {shares}</p>'
            f'<div class=crops>{crops}</div>'
            f'<div class=controls><label for="style-{slug}">Style</label><select id="style-{slug}">{menu}</select>'
            f'<button type=button class=use>Use {attr(doc["style"])}</button>'
            f'<label for="note-{slug}">Note</label><input id="note-{slug}" type=text></div></article>')
    credits = "; ".join(CREDITS.get(name, name) for name in corpora)
    return (
        "<title>Document Styles Review</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@600&display=swap">'
        f"<style>{CSS}</style>"
        f'<div class=wrap id=review data-page="{digest}">'
        "<h1>Document styles to confirm</h1>"
        f'<p class=lede>{len(documents)} documents, {clear} with a clear suggestion and {len(documents) - clear} split. '
        "The suggestions come from the style teacher, trained on Calli-Tongji (CC BY-NC 4.0), and nobody has reviewed them. "
        "Choose a style only where the crops show it, or press the button to take the suggestion; a document left empty "
        "records nothing. Choices are kept in this browser for this page only.</p>"
        '<div class=bar><span class=count></span><button type=button id=copy class=primary>Copy YAML</button>'
        '<span id=status role=status></span></div>'
        '<textarea id=out aria-label="YAML to paste" readonly hidden></textarea>'
        f"{''.join(items)}"
        f"<footer>Crops: {attr(credits)}. Shown for review only.</footer></div>"
        f"<script>{SCRIPT}</script>")


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
    args.out.write_text(page(documents, titles(args.corpora), args.per_document, args.corpora), encoding="utf-8")
    clear = sum(doc["clear"] for doc in documents)
    print(json.dumps({"documents": len(documents), "already_confirmed": len(done), "clear": clear,
                      "split": len(documents) - clear,
                      "styles": dict(Counter(doc["style"] for doc in documents if doc["clear"])),
                      "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
