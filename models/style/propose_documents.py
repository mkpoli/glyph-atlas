"""Propose a style for each document from the teacher's suggestions, and lay them out for review.

The input is `work/style-suggestions/{corpus}/suggestions.jsonl` from `suggest.py`, one per corpus
named. A document with at least `--min-crops` suggested crops gets a proposal: the style most of
its crops were given, marked clear when that style holds at least `--clear` of them. The output is
`review.html`, a self-contained page (the crops are embedded) that shows every document with its
crops, the shares and a row of style choices with the proposal marked. Nothing is chosen until the
person picks a style, by click or key, so no document is recorded as reviewed that nobody looked at.
The one exception is the button that takes every clear proposal still undecided; each entry it makes
says so in its note. Its button copies the chosen entries in the
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


CREDITS = {
    "hng": "漢字字体規範史データセット (HNG), CC BY-SA 4.0",
    "codh-full": "日本古典籍くずし字データセット (国文研・CODH), CC BY-SA 4.0",
}
#: The note an entry gets when a reviewer takes every clear suggestion at once.
BULK_NOTE = "clear suggestion accepted with the bulk button"

CSS = """
:root{--bg:#f3f5f2;--surface:#fff;--ink:#1d2126;--muted:#59606a;--line:#d6dad4;--accent:#2d4f86;--on-accent:#fff;
--clear:#2e6b45;--split:#8f5a10;--paper:#fff;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#141619;--surface:#1c1f23;--ink:#e6e8ea;--muted:#9ba2ab;--line:#30353b;
--accent:#a3bbe6;--on-accent:#141619;--clear:#82c9a0;--split:#e2b56a;color-scheme:dark}}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding-inline:16px;padding-block:8px 48px}
h1{font-size:1.5rem;margin:12px 0 4px;text-wrap:balance}
.lede{color:var(--muted);max-width:68ch;margin:0 0 8px}
.keys{color:var(--muted);font-size:13px;margin:0 0 8px}
kbd{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 4px;background:var(--surface)}
.bar{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;
gap:8px 12px;align-items:center;padding-block:10px}
.bar .count{font-variant-numeric:tabular-nums;margin-right:auto}
.filters{display:flex;flex-wrap:wrap;gap:4px}
button{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:4px 12px;cursor:pointer}
button[aria-pressed=true]{border-color:var(--accent);color:var(--accent);font-weight:600}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent);font-weight:600}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
textarea{width:100%;min-height:180px;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);background:var(--surface);
border:1px solid var(--line);border-radius:6px;padding:8px;box-sizing:border-box}
.doc{display:grid;gap:8px;padding:14px 8px;margin-inline:-8px;border-bottom:1px solid var(--line);border-radius:8px}
.doc.current{box-shadow:inset 0 0 0 2px var(--accent)}
.doc header{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.doc h2{margin:0;font:600 1.1rem/1.3 "Shippori Mincho","Noto Serif JP","Hiragino Mincho ProN","Yu Mincho",serif}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
.pill{font-size:12px;letter-spacing:.03em;border:1px solid currentColor;border-radius:999px;padding:0 8px}
.pill.clear{color:var(--clear)}.pill.split{color:var(--split)}
.why{margin:0;color:var(--muted)}.why b{color:var(--ink)}
.crops{display:grid;grid-template-columns:repeat(auto-fill,minmax(56px,1fr));gap:4px}
.crops img{width:100%;aspect-ratio:1;object-fit:contain;background:var(--paper);border:1px solid var(--line);border-radius:3px}
.choices{border:0;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.choices legend{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.choices label{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:4px 12px;
background:var(--surface);cursor:pointer;user-select:none}
.choices label.suggested{border-style:dashed;border-color:var(--accent)}
.choices input{position:absolute;opacity:0;pointer-events:none}
.choices label:has(input:checked){background:var(--accent);border-color:var(--accent);border-style:solid;color:var(--on-accent);font-weight:600}
.choices label:has(input:focus-visible){outline:2px solid var(--accent);outline-offset:2px}
.choices .n{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;opacity:.7}
.note{display:flex;gap:6px;align-items:center}
.note input{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:3px 6px;width:min(44ch,70vw)}
footer{color:var(--muted);font-size:13px;margin-top:24px}
"""

SCRIPT = r"""
const root = document.querySelector('#review');
const KEY = 'document-styles-review:' + root.dataset.page;
const BULK = root.dataset.bulk;
const docs = () => [...root.querySelectorAll('.doc')];
const shown = () => docs().filter(d => !d.hidden);
const chosen = d => d.querySelector('input[type=radio]:checked')?.value || '';
let current = null;
function focusDoc(d, scroll = true) {
  if (current) current.classList.remove('current');
  current = d || null;
  if (!current) return;
  current.classList.add('current');
  if (scroll) current.scrollIntoView({block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
}
function choose(d, value) {
  const input = d.querySelector(`input[type=radio][value="${value}"]`);
  if (input) input.checked = true;
  save();
}
function next(from) {
  const list = shown(), start = list.indexOf(from);
  return list.slice(start + 1).find(d => !chosen(d)) || list.slice(start + 1)[0] || null;
}
function applyFilter() {
  const filter = root.querySelector('.filters [aria-pressed=true]').dataset.filter;
  for (const d of docs()) d.hidden = filter === 'undecided' ? !!chosen(d) : filter !== 'all' && d.dataset.state !== filter;
}
function refresh() {
  let n = 0;
  for (const d of docs()) n += !!chosen(d);
  root.querySelector('.count').textContent = `${n} of ${docs().length} chosen`;
}
function save() {
  const state = {};
  for (const d of docs()) state[d.dataset.id] = [chosen(d), d.querySelector('.note input').value];
  try { localStorage.setItem(KEY, JSON.stringify(state)) } catch {}
  refresh();
}
function restore() {
  let state = {};
  try { state = JSON.parse(localStorage.getItem(KEY) || '{}') } catch {}
  for (const d of docs()) {
    const saved = state[d.dataset.id];
    if (!saved) continue;
    if (saved[0]) choose(d, saved[0]);
    d.querySelector('.note input').value = saved[1] || '';
  }
  refresh();
}
function yaml() {
  const now = new Date(), two = n => String(n).padStart(2, '0');
  const day = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`, out = [];
  for (const d of docs()) {
    const value = chosen(d), note = d.querySelector('.note input').value.trim();
    if (!value || value === 'none') continue;
    out.push(`  ${JSON.stringify(d.dataset.id)}:`, `    style: ${value}`, `    evidence:`,
      `      - source: reviewer`, `        reviewed: ${day}`,
      `        note: ${JSON.stringify(note || 'kanji crops compared with the style teacher suggestion')}`,
      `      - source: style-teacher`, `        checkpoint_sha256: ${d.dataset.checkpoint}`,
      `        crops: ${d.dataset.crops}`, `        suggested: ${d.dataset.suggested}`);
  }
  return out.length ? out.join('\n') + '\n' : '';
}
root.addEventListener('change', event => {
  const d = event.target.closest('.doc');
  save();
  if (d && event.target.type === 'radio') { focusDoc(d, false); if (root.querySelector('.filters [aria-pressed=true]').dataset.filter === 'undecided') applyFilter() }
});
root.addEventListener('input', event => { if (event.target.matches('.note input')) save() });
root.addEventListener('focusin', event => { const d = event.target.closest('.doc'); if (d) focusDoc(d, false) });
root.addEventListener('click', async event => {
  const d = event.target.closest('.doc');
  if (d && !event.target.closest('label, input')) focusDoc(d, false);
  const filter = event.target.closest('.filters button');
  if (filter) {
    for (const b of root.querySelectorAll('.filters button')) b.setAttribute('aria-pressed', String(b === filter));
    applyFilter();
    return;
  }
  if (event.target.closest('#bulk')) {
    const open = docs().filter(x => x.dataset.state === 'clear' && !chosen(x));
    const status = root.querySelector('#status');
    if (!open.length) { status.textContent = 'Every clear document already has a choice.'; return }
    for (const x of open) {
      choose(x, x.dataset.suggested);
      const note = x.querySelector('.note input');
      if (!note.value.trim()) note.value = BULK;
    }
    save();
    status.textContent = `Took the suggestion for ${open.length} clear documents; each note says so.`;
    return;
  }
  if (!event.target.closest('#copy')) return;
  const text = yaml(), out = root.querySelector('#out'), status = root.querySelector('#status');
  if (!text) { status.textContent = 'Choose a style for at least one document first.'; out.hidden = true; return }
  out.value = text; out.hidden = false;
  try { await navigator.clipboard.writeText(text); status.textContent = 'Copied. Paste under documents: in data/vocab/document-styles.yaml.' }
  catch { out.focus(); out.select(); status.textContent = 'Selected below. Copy it with your keyboard.' }
});
document.addEventListener('keydown', event => {
  const typing = event.target instanceof Element && event.target.matches('input[type=text], textarea');
  if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
  const list = shown();
  if (!list.length) return;
  if (!current || current.hidden) focusDoc(list.find(d => !chosen(d)) || list[0]);
  const values = [...current.querySelectorAll('input[type=radio]')].map(i => i.value);
  if (/^[1-9]$/.test(event.key) && values[Number(event.key) - 1] && values[Number(event.key) - 1] !== 'none') {
    choose(current, values[Number(event.key) - 1]); focusDoc(next(current));
  } else if (event.key === 'Enter') {
    choose(current, current.dataset.suggested); focusDoc(next(current));
  } else if (event.key === '0') {
    choose(current, 'none');
  } else if (event.key === 'j' || event.key === 'ArrowDown') {
    focusDoc(list[Math.min(list.indexOf(current) + 1, list.length - 1)]);
  } else if (event.key === 'k' || event.key === 'ArrowUp') {
    focusDoc(list[Math.max(list.indexOf(current) - 1, 0)]);
  } else return;
  event.preventDefault();
});
restore();
applyFilter();
"""


def page(documents: list[dict], names: dict[str, str], per_document: int, corpora: list[str]) -> str:
    """The review page: every document with its crops, a row of style choices and a note."""
    # The common choices get the low keys.
    order = ["regular", "running", "cursive", "clerical", "seal", "ming", "gothic", "mixed"]
    options = sorted((v for v in style.vocabulary() if v != style.UNASSESSED),
                     key=lambda v: order.index(v) if v in order else len(order))
    digest = hashlib.sha256(json.dumps([[d["document_id"], d["style"], d["crops"], d["checkpoint_sha256"]]
                                        for d in documents]).encode()).hexdigest()[:16]
    attr = html.escape
    clear = sum(doc["clear"] for doc in documents)
    items = []
    for doc in documents:
        slug = hashlib.sha1(doc["document_id"].encode()).hexdigest()[:10]
        shares = ", ".join(f"{name} {doc['counts'].get(name, 0)}" for name in CLASSES if doc["counts"].get(name))
        choices = "".join(
            f'<label{" class=suggested" if value == doc["style"] else ""}>'
            f'<input type=radio name="style-{slug}" value="{attr(value)}">'
            f'{attr(value)}<span class=n>{number}</span></label>'
            for number, value in enumerate(options, 1))
        choices += f'<label><input type=radio name="style-{slug}" value=none>none<span class=n>0</span></label>'
        crops = "".join(f'<img src="{thumbnail(row["crop"])}" alt="{attr(row["character"])}" '
                        f'title="{attr(row["character"])}: suggested {attr(row["style"])}">'
                        for row in sorted(doc["rows"], key=lambda r: r["id"])[:per_document])
        state = "clear" if doc["clear"] else "split"
        items.append(
            f'<article class=doc data-state={state} data-id="{attr(doc["document_id"])}" data-crops="{doc["crops"]}" '
            f'data-suggested="{attr(doc["style"])}" data-checkpoint="{attr(doc["checkpoint_sha256"])}">'
            f'<header><h2>{attr(names.get(doc["document_id"], ""))}</h2><code>{attr(doc["document_id"])}</code>'
            f'<span class="pill {state}">{state}</span></header>'
            f'<p class=why>Suggested <b>{attr(doc["style"])}</b> (dashed) for {doc["crops"]} kanji crops: {shares}</p>'
            f'<div class=crops>{crops}</div>'
            f'<fieldset class=choices><legend>Style of {attr(doc["document_id"])}</legend>{choices}</fieldset>'
            f'<div class=note><label for="note-{slug}">Note</label><input id="note-{slug}" type=text></div></article>')
    credits = "; ".join(CREDITS.get(name, name) for name in corpora)
    return (
        "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Document Styles Review</title>"
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@600&display=swap">'
        f"<style>{CSS}</style>"
        f'<div class=wrap id=review data-page="{digest}" data-bulk="{attr(BULK_NOTE)}">'
        "<h1>Document styles to confirm</h1>"
        f'<p class=lede>{len(documents)} documents, {clear} with a clear suggestion and {len(documents) - clear} split. '
        "The suggestions come from the style teacher, trained on Calli-Tongji (CC BY-NC 4.0), and nobody has reviewed them. "
        "Pick a style where the crops show it. The dashed button is the suggestion; a document left without a choice, "
        "or set to none, records nothing. Choices are kept in this browser for this page only.</p>"
        "<p class=keys><kbd>1</kbd>–<kbd>8</kbd> choose a style for the outlined document and move on, "
        "<kbd>Enter</kbd> takes its suggestion, <kbd>0</kbd> sets none, <kbd>j</kbd>/<kbd>k</kbd> move.</p>"
        '<div class=bar><span class=count></span>'
        '<span class=filters role=group aria-label=Show>'
        '<button type=button data-filter=all aria-pressed=true>All</button>'
        '<button type=button data-filter=undecided aria-pressed=false>Undecided</button>'
        '<button type=button data-filter=clear aria-pressed=false>Clear</button>'
        '<button type=button data-filter=split aria-pressed=false>Split</button></span>'
        '<button type=button id=bulk>Take all clear suggestions</button>'
        '<button type=button id=copy class=primary>Copy YAML</button></div>'
        '<p id=status role=status class=keys></p>'
        '<textarea id=out aria-label="YAML to paste" readonly hidden></textarea>'
        f"{''.join(items)}"
        f"<footer>Crops: {attr(credits)}. Shown for review only.</footer></div>"
        f"<script>{SCRIPT}</script></html>")


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
