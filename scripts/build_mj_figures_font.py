"""Build the MJ figure font: the registered ideographic variation sequences the form picker offers.

The Moji_Joho collection of the IVD registers one sequence per MJ文字図形 (所 has 所󠄂 and 所󠄃), and
data/vocab/mj-kanji.tsv lists them. A browser draws such a sequence only with a font that maps it, so
this script cuts IPAmj明朝 Ver.006.01, the collection's reference font, down to the code points with
more than one registered figure, in files of `CHUNK` code points by code point order. The IPA Font
License v1.0 lets a derived font be redistributed under its terms and under a name that is not the
original's, so the subset is named `MJ Figures` (family and files alike) and ships the licence and
the way back to the original font (apps/review/static/fonts/README.md).

  uv run scripts/build_mj_figures_font.py

Writes apps/review/static/fonts/mj/: the fonts, beside each font a JSON of its code points'
sequences with their MJ figure names (the default figure marked), `figures.json` with each file's
first and last code point, and the licence.
"""
import hashlib
import io
import json
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / 'data/vocab/mj-kanji.tsv'
OUT = ROOT / 'apps/review/static/fonts/mj'
CACHE = ROOT / 'cache/fonts'
CHUNK = 40   # code points per file: one picker reads one file
FAMILY = 'MJ Figures'
# IPAmj明朝 Ver.006.01, distributed by 文字情報技術促進協議会 through 窓の杜
# (https://moji.or.jp/mojikiban/font/ links https://forest.watch.impress.co.jp/library/software/ipamjfont/).
SOURCE = {'url': 'https://dforest.watch.impress.co.jp/library/i/ipamjfont/10750/ipamjm00601.zip',
          'sha256': '35494e0f2896f38b3f7369a8421a895cea6440a42c0a66ac95eab47d6ed25b68',
          'font': 'ipamjm.ttf', 'licence': 'IPA_Font_License_Agreement_v1.0.txt'}


def fetch():
    path = CACHE / SOURCE['url'].rsplit('/', 1)[1]
    if not (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == SOURCE['sha256']):
        data = urllib.request.urlopen(SOURCE['url'], timeout=300).read()
        assert hashlib.sha256(data).hexdigest() == SOURCE['sha256'], 'the IPAmj明朝 archive does not match its pinned hash'
        CACHE.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    return zipfile.ZipFile(path)


def figures():
    """Code point -> its registered sequences as (sequence, MJ figure name, default), for the code
    points with more than one, from mj-kanji.tsv (columns mj, code_point, implemented_code_point, ivs)."""
    found = defaultdict(list)
    for line in TABLE.read_text(encoding='utf-8').splitlines():
        if line.startswith('#') or line.startswith('mj\t'):
            continue
        mj, point, implemented, ivs = line.split('\t')
        # A figure registered on several bases (MJ059399 on U+2B9E4 and U+535A) is offered on its own;
        # it is the default when the font draws it for its base alone (MJ030260 is drawn for U+FA40).
        for sequence in ivs.split(';') if point and ivs else ():
            base, selector = sequence.split('_')
            if f'U+{base}' == point:
                found[int(base, 16)].append((chr(int(base, 16)) + chr(int(selector, 16)), mj, implemented == point))
    return {code: sorted(rows) for code, rows in sorted(found.items()) if len(rows) > 1}


def rename(font):
    """Name every record `MJ Figures`: the licence bars the original's name on a derived font."""
    names = {1: FAMILY, 2: 'Regular', 3: f'{FAMILY} Regular (subset of the MJ figures)', 4: f'{FAMILY} Regular',
             6: FAMILY.replace(' ', '') + '-Regular', 16: FAMILY, 17: 'Regular'}
    table = font['name']
    for record in list(table.names):
        if record.nameID in names:
            table.removeNames(nameID=record.nameID)
    for nameID, text in names.items():
        table.setName(text, nameID, 3, 1, 0x409)


def main():
    archive, wanted = fetch(), figures()
    source = io.BytesIO(archive.read(SOURCE['font']))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in [*OUT.glob('*.woff2'), *OUT.glob('*.json')]:
        old.unlink()
    (OUT / 'IPA_Font_License_Agreement_v1.0.txt').write_bytes(archive.read(SOURCE['licence']))
    codes, files = list(wanted), []
    for i in range(0, len(codes), CHUNK):
        part = codes[i:i + CHUNK]
        font = TTFont(source, recalcTimestamp=False)
        options = subset.Options(); options.flavor = 'woff2'; options.hinting = False
        options.layout_features = []; options.name_IDs = ['*']
        # The selectors are asked for too, or the subset drops the sequences' cmap (format 14).
        selectors = {ord(text[-1]) for code in part for text, _, _ in wanted[code]}
        sub = subset.Subsetter(options=options); sub.populate(unicodes=[*part, *selectors]); sub.subset(font)
        rename(font)
        font.flavor = 'woff2'   # the option only says what to load; the saved font takes its own
        name = f'mj-figures-{i // CHUNK:02d}.woff2'
        font.save(OUT / name)
        (OUT / name.replace('.woff2', '.json')).write_text(json.dumps(
            {chr(code): [[text, mj, default] for text, mj, default in wanted[code]] for code in part},
            ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        files.append([name.removesuffix('.woff2'), part[0], part[-1]])
    (OUT / 'figures.json').write_text(json.dumps({
        'source': f'IPAmj明朝 Ver.006.01 ({SOURCE["sha256"][:12]}), IPA Font License v1.0; sequences from mj-kanji.tsv',
        'family': FAMILY, 'files': files,
    }, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    size = sum(path.stat().st_size for path in OUT.glob('*.woff2'))
    print(f'{len(codes)} code points, {sum(map(len, wanted.values()))} sequences, {len(files)} files, {size} bytes')


if __name__ == '__main__':
    main()
