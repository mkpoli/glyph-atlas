import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { bundleKey, cropId, publicId } from '../atlas3';
import { currentPointer } from '../atlas3Gallery';
import { listingItems } from '../atlas3Listing';
import { d1 } from '../forms.test';
import { redrawLimits } from '../cropRecords';
import { bookCharactersQuery, bookFormsQuery, booksQuery, corpusFormsQuery, runCountQuery,
  reviewedQuery, runGraphemesQuery, runHasQuery, runOccurrencesQuery, runProbeQuery, runRangeQuery, runWorksQuery, runsRoute, writtenStatement } from './runs';

type Json = any;
const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });
const schema = (folder: string) => {
  const db = new Database(':memory:'); databases.push(db);
  const dir = new URL(`../../${folder}/`, import.meta.url);
  for (const file of readdirSync(dir).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, dir), 'utf8'));
  return db;
};

// R2 as the binding answers: the stored bytes, a range when one is asked for.
function bucket(objects: Map<string, Uint8Array>) {
  return {
    get: async (key: string, options?: { range?: { offset: number; length: number } }) => {
      const bytes = objects.get(key);
      if (!bytes) return null;
      const body = options?.range ? bytes.subarray(options.range.offset, options.range.offset + options.range.length) : bytes;
      return { body: new Response(body).body!, json: async () => JSON.parse(new TextDecoder().decode(body)),
        text: async () => new TextDecoder().decode(body), arrayBuffer: async () => new Uint8Array(body).buffer };
    },
  };
}

// One collected book (a みんなで翻刻 entry) whose line reads ん𛁅んし, 𛁅 being a hentaigana し, a second (hk:other)
// with んし written across the page, and two corpus glyphs んし (ndl:book), book ids in key order as the first build gave them, whose record lives in a pack.
let pages = 5000;
const DOC_PAGE = 'hk:83b29132c6d01a596a847de5ba3b0b0d:3';
function fixture() {
  const doc = ++pages, other = ++pages, corpus = ++pages;
  const id = (page: number, slot: number) => cropId(page, slot);
  const crops: { text: string; id: number; label: string; book: number; page: number; kind: number; char: number; x: number }[] = [
    { text: 'ar:doc:1-l1-0', id: id(doc, 0), label: 'ん', book: 1, page: doc, kind: 0, char: 1, x: 0 },
    { text: 'ar:doc:1-l1-1', id: id(doc, 1), label: '𛁅', book: 1, page: doc, kind: 0, char: 3, x: 0 },
    { text: 'ar:doc:1-l1-2', id: id(doc, 2), label: 'ん', book: 1, page: doc, kind: 0, char: 1, x: 0 },
    { text: 'ar:doc:1-l1-3', id: id(doc, 3), label: 'し', book: 1, page: doc, kind: 0, char: 2, x: 0 },
    { text: 'ex:o1', id: id(other, 0), label: 'ん', book: 2, page: other, kind: 0, char: 1, x: 0 },
    { text: 'ex:o2', id: id(other, 1), label: 'し', book: 2, page: other, kind: 0, char: 2, x: 20 },
    { text: 'codh:g1', id: id(corpus, 0), label: 'ん', book: 3, page: corpus, kind: 1, char: 1, x: 0 },
    { text: 'codh:g2', id: id(corpus, 1), label: 'し', book: 3, page: corpus, kind: 1, char: 2, x: 0 },
  ];
  const at = crops.map((c, i) => ({ ...c, y: (i % 4) * 20 }));
  const pageKey = (c: typeof at[number]) => c.page === doc ? DOC_PAGE : c.page === other ? 'ex:other:1' : 'ndl:book:7';
  const source = (c: typeof at[number]) => c.book === 1 ? '藻汐草' : c.book === 2 ? '別書' : undefined;
  // A crop's record as the publication wrote it, and the corpus glyphs' published records.
  const record = (c: typeof at[number]): Json => withEditable(c, { id: c.text, label: c.label, production: c.kind === 1 ? 'printed' : 'handwritten', state: 'pending', revision: 0,
    page_id: pageKey(c), box: { x: c.x, y: c.y, w: 10, h: 10 }, crop_box: { x: c.x, y: c.y, w: 10, h: 10 }, image: `/atlas/media/${c.id}.webp`,
    context_image: `/atlas/media/ctx${c.page}.webp`, context_box: { x: 0, y: 0, w: 100, h: 200 }, page_number: 4,
    ...(c.kind === 1 ? { source: { document_id: 'ndl:book', corpus: 'codh', page_id: pageKey(c) } }
      : { source: source(c), context: true, source_scale: [1, 1] }) });
  // The export writes whether a reviewer may redraw a collected crop's box, by the Worker's rule.
  const withEditable = (c: typeof at[number], r: Json) => c.kind === 1 ? r : { ...r, crop_editable: Boolean(redrawLimits(r)) };
  const packed = at.filter(c => c.kind === 1).map(c => new TextEncoder().encode(JSON.stringify(record(c))));
  const pack = new Uint8Array(packed.reduce((n, b) => n + b.length, 0));
  const places: [number, number][] = [];
  packed.reduce((offset, b) => { pack.set(b, offset); places.push([offset, b.length]); return offset + b.length; }, 0);
  const place = (c: typeof at[number]) => places[at.filter(x => x.kind === 1).indexOf(c)];
  // Runs as the export writes them: first crop, size, the crops, text, book, direction and graphemes.
  const runs: [number, number, number, number | null, string, string, number][] = [
    [0, 2, 1, null, 'ん𛁅', 'hk:83b29132c6d01a596a847de5ba3b0b0d', 1], [1, 2, 2, null, '𛁅ん', 'hk:83b29132c6d01a596a847de5ba3b0b0d', 1], [2, 2, 3, null, 'んし', 'hk:83b29132c6d01a596a847de5ba3b0b0d', 1],
    [0, 3, 1, 2, 'ん𛁅ん', 'hk:83b29132c6d01a596a847de5ba3b0b0d', 1], [1, 3, 2, 3, '𛁅んし', 'hk:83b29132c6d01a596a847de5ba3b0b0d', 1],
    [4, 2, 5, null, 'んし', 'hk:other', 1], [6, 2, 7, null, 'んし', 'ndl:book', 1]];
  const fold = (t: string) => t.replaceAll('𛁅', 'し');
  const shuffle = (i: number) => 1000 - i * 7;

  const occurrence = (page: number, position: number, label: string) => JSON.stringify({ sample: `${page}-l1-${position}`, page, line: 1,
    block: 'l1', position, context: 'ん𛁅', source: 'transcription', label, box: [1, 2, 3, 4] });
  const characters = [['hk:83b29132c6d01a596a847de5ba3b0b0d', 0, 0, occurrence(1, 0, 'ん')], ['hk:83b29132c6d01a596a847de5ba3b0b0d', 1, 1, occurrence(1, 1, '𛁅')], ['ndl:book', 0, 6, occurrence(7, 0, 'ん')]] as const;

  // The new layout.
  const atlas = schema('migrations-atlas'), runsDb = schema('migrations-atlas-runs');
  atlas.exec(`INSERT INTO books(id,key,title) VALUES(1,'hk:83b29132c6d01a596a847de5ba3b0b0d','藻汐草'),(2,'hk:other','別書'),(3,'ndl:book','源氏物語');
    INSERT INTO graphemes(id,text,head) VALUES(1,'ん',NULL),(2,'し',NULL),(3,'𛁅',2);
    INSERT INTO productions(id,key,parent) VALUES(1,'handwritten',NULL),(2,'printed',NULL);
    INSERT INTO codes VALUES('state',0,'pending'),('state',1,'checked'),('state',2,'flagged'),('category',1,'kana'),('style',2,'unassessed');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${doc},'${DOC_PAGE}',1,1,4),(${other},'ex:other:1',2,1,2),(${corpus},'corpus:ndl:book:0',3,1,2);
    INSERT INTO book_dates(book,axis,kind,start,end,precision,uncertain,text,label,status,calendar,source)
      VALUES(1,'witness','copied',1790,1790,'year',0,'寛政二年','1790','single','japanese','source:test');`);
  const crop = atlas.prepare(`INSERT INTO crops(id,kind,book,char,production,category,style,state,quiz,shuffle) VALUES(?,?,?,?,?,1,2,0,1,?)`);
  for (const c of at) crop.run(c.id, c.kind, c.book, c.char, c.kind === 1 ? 2 : 1, shuffle(at.indexOf(c)));
  for (const [book, ord, c, data] of characters.map(([d, o, c, data]) => [d === 'hk:83b29132c6d01a596a847de5ba3b0b0d' ? 1 : 3, o, c, data] as const))
    atlas.prepare('INSERT INTO book_characters(book,ord,crop,data) VALUES(?,?,?,?)').run(book, ord, at[c].id, data);
  const entry = (c: typeof at[number], slot: number) => {
    if (c.kind === 1) return { slot, id: c.text, label: c.label, record_place: ['packs/c.bin', ...place(c)] };
    const { id: _id, state: _s, revision: _r, production: _p, page_id: _pg, crop_editable: _e, ...rest } = record(c);
    return { slot, id: c.text, ...rest };
  };
  const objects = new Map<string, Uint8Array>([['packs/c.bin', pack],
    ['v3/pointer.json', gzipSync(JSON.stringify({ pub: '20261019T130411Z', through_gen: 0 }))]]);
  for (const page of [doc, other, corpus]) {
    const on = at.filter(c => c.page === page);
    objects.set(bundleKey(page, 1), gzipSync(JSON.stringify({ key: page, book: on[0].book, rev: 1, page: null, crops: on.map(c => entry(c, c.id % 2 ** 24)) })));
  }
  const runRow = runsDb.prepare(`INSERT INTO runs(size,graphemes,hand,shuffle,first,second,third,book) VALUES(?,?,0,?,?,?,?,?)`);
  for (const [a, size, b, c, text, document] of runs)
    runRow.run(size, fold(text), shuffle(a), at[a].id, at[b].id, c === null ? null : at[c].id, document === 'hk:83b29132c6d01a596a847de5ba3b0b0d' ? 1 : document === 'hk:other' ? 2 : 3);
  runsDb.exec(`INSERT INTO run_stats SELECT size,graphemes,count(*),count(*) FROM runs GROUP BY size,graphemes`);
  // The crops written apart from their family head, and the publication's answers (run_answers.py).
  for (const c of at.filter(c => c.label !== fold(c.label))) runsDb.prepare('INSERT INTO run_written(crop,char) VALUES(?,?)').run(c.id, c.label);
  writeAnswers(runsDb, objects);
  const media = bucket(objects);
  const newEnv = { ATLAS: d1(atlas), ATLAS_RUNS: d1(runsDb), MEDIA: media, CATALOGUE_LAG: '0' } as unknown as Env;
  return { atlas, runsDb, newEnv, at };
}

// The runs' answers as run_answers.py writes them, from a runs database: each run written as its crops'
// rows, else the head its graphemes name there.
function writeAnswers(runsDb: Database, objects: Map<string, Uint8Array>) {
  const put = (name: string, value: Json) => objects.set(`v3/runs/${name}.json.gz`, gzipSync(JSON.stringify(value)));
  const written = new Map(runsDb.query('SELECT crop,char FROM run_written').all().map((r: Json) => [r.crop, r.char]));
  const rows = runsDb.query('SELECT size,graphemes,first,second,third,book FROM runs').all() as Json[];
  const order = (a: [string, number], b: [string, number]) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
  const forms = new Map<string, Map<string, number>>(), books = new Map<string, Map<string, number>>(), sites = new Map<string, number>();
  for (const r of rows) {
    const heads = [...r.graphemes];
    const text = [r.first, r.second, r.third].slice(0, r.size).map((crop, i) => written.get(crop) ?? heads[i]).join('');
    const key = `${r.size}|${r.graphemes}`;
    forms.set(key, (forms.get(key) ?? new Map()).set(text, (forms.get(key)?.get(text) ?? 0) + 1));
    sites.set(key, (sites.get(key) ?? 0) + 1);
    const book = `${r.book}-${r.size}`;
    books.set(book, (books.get(book) ?? new Map()).set(r.graphemes, (books.get(book)?.get(r.graphemes) ?? 0) + 1));
  }
  const shards = new Map<string, Json>();
  for (const [key, counts] of forms) {
    const [size, graphemes] = key.split('|');
    const name = `forms/${size}-${graphemes.codePointAt(0)!.toString(16)}`;
    shards.set(name, { ...shards.get(name), [graphemes]: [counts.size, [...counts].sort(order).slice(0, 24)] });
  }
  for (const [name, value] of shards) put(name, value);
  for (const [book, counts] of books) put(`books/${book}`, [...counts].sort(order).slice(0, 480).map(([g, n]) => [g, n, n]));
  for (const size of [2, 3]) put(`grid-${size}`, [...sites].filter(([k]) => k.startsWith(`${size}|`)).map(([k, n]) => [k.slice(2), n] as [string, number])
    .sort(order).slice(0, 480).map(([g, n]) => [g, n, n, forms.get(`${size}|${g}`)!.size]));
}

const ctx = () => {
  const waits: Promise<unknown>[] = [];
  return { waits, ctx: { waitUntil: (p: Promise<unknown>) => waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext };
};
function freshCache() {
  const stored = new Map<string, Response>();
  (globalThis as any).caches = { default: { match: async (key: Request) => stored.get(key.url)?.clone(), put: async (key: Request, value: Response) => { stored.set(key.url, value) } } };
  return stored;
}
async function ask(env: Env, path: string): Promise<Response> {
  freshCache();
  const { waits, ctx: c } = ctx();
  const response = (await runsRoute(env, c, new URL('https://glyphatlas.org' + path)))!;
  await Promise.all(waits);
  return response;
}
// Each answer as the old database's routes gave it at the cutover, pinned in __snapshots__.
describe('runs on the new layout keep their answers', () => {
  it('count pairs and trigrams on the site and in a book', async () => {
    const f = fixture(), found: Json[] = [];
    for (const path of ['/atlas/ngrams/2', '/atlas/ngrams/3', '/atlas/ngrams/2?document=hk%3Aother', '/atlas/ngrams/2?document=hk%3A83b29132c6d01a596a847de5ba3b0b0d', '/atlas/ngrams/2?document=none'])
      found.push([path, await (await ask(f.newEnv, path)).json()]);
    expect(found).toMatchSnapshot();
    expect((await (await ask(f.newEnv, '/atlas/ngrams/2')).json()).items[0]).toEqual({ text: 'んし', n: 4, vertical: true, forms: 2 });
  });

  it('list a run’s occurrences, forms, hands and books', async () => {
    const f = fixture(), found: Json[] = [];
    for (const query of ['text=んし', 'text=ん𛁅', 'text=んし&form=ん𛁅', 'text=んし&form=んし', 'text=んし&document=hk%3A83b29132c6d01a596a847de5ba3b0b0d', 'text=んし&sort=source',
      'text=んし&hand=hand', 'text=んし&offset=1&limit=1', 'text=んしん', 'text=んしんし', 'text=しんし&document=hk%3A83b29132c6d01a596a847de5ba3b0b0d', 'text=んし&document=none']) {
      const path = '/atlas/runs?' + new URLSearchParams(Object.fromEntries(new URLSearchParams(query)));
      const response = await ask(f.newEnv, path);
      expect(response.headers.get('server-timing')).toContain('total;dur=');
      found.push([query, await response.json()]);
    }
    expect(found).toMatchSnapshot();
  });

  it('name the runs near one', async () => {
    const f = fixture(), found: Json[] = [];
    for (const text of ['んし', 'しん', 'んしん', 'んしんし'])
      found.push([text, await (await ask(f.newEnv, '/atlas/runs/related?text=' + encodeURIComponent(text))).json()]);
    expect(found).toMatchSnapshot();
  });

  it('list a book’s characters for other sites', async () => {
    const f = fixture(), found: Json[] = [];
    for (const document of ['hk%3A83b29132c6d01a596a847de5ba3b0b0d', 'ndl%3Abook']) {
      const response = await ask(f.newEnv, `/atlas/documents/${document}/characters`);
      expect([response.headers.get('access-control-allow-origin'), response.headers.get('cache-control')]).toEqual(['*', 'no-cache']);
      // The publication is named by the pointer, which an isolate holds for a while: it may be another test's.
      const { published_at: _published, ...body } = await response.json() as Json;
      found.push([document, body]);
    }
    expect(found).toMatchSnapshot();
  });
});

describe('runs on the new layout', () => {
  it('list an occurrence of a unit the site never had by its text id', async () => {
    const f = fixture();
    f.atlas.exec(`INSERT INTO book_characters(book,ord,crop,unit,data) VALUES(2,9,NULL,'ar:gone','{"label":"い"}')`);
    const book = await (await ask(f.newEnv, '/atlas/documents/' + encodeURIComponent('hk:other') + '/characters')).json();
    expect(book.characters).toEqual([{ label: 'い', unit: 'ar:gone', atlas: false }]);
  });

  it('refuse lengths and pages they do not serve', async () => {
    const f = fixture();
    await expect(ask(f.newEnv, '/atlas/ngrams/4')).rejects.toMatchObject({ status: 404 });
    await expect(ask(f.newEnv, '/atlas/runs?text=ん')).rejects.toMatchObject({ status: 422 });
    await expect(ask(f.newEnv, '/atlas/runs?text=んし&offset=2000')).rejects.toMatchObject({ status: 404 });
    await expect(ask(f.newEnv, '/atlas/runs?text=んし&form=ん')).rejects.toMatchObject({ status: 422 });
    await expect(ask(f.newEnv, '/atlas/runs?text=んし&hand=pen')).rejects.toMatchObject({ status: 422 });
    await expect(ask(f.newEnv, '/atlas/documents/none/characters')).rejects.toMatchObject({ status: 404 });
    expect(await runsRoute(f.newEnv, ctx().ctx, new URL('https://glyphatlas.org/atlas/elsewhere'))).toBeNull();
  });

  it('leave out an occurrence whose crop is gone', async () => {
    const f = fixture();
    f.atlas.exec(`DELETE FROM crops WHERE id=${f.at[3].id}`);
    const body = await (await ask(f.newEnv, '/atlas/runs?text=' + encodeURIComponent('んし'))).json();
    expect(body.items.map((i: Json) => i.crops[0].source_id).sort()).toEqual(['ar:doc:1-l1-0', 'codh:g1', 'ex:o1']);
    expect([body.total, body.next_offset]).toEqual([4, 4]);
  });

  it('show a corpus glyph once a round showed it, with its review', async () => {
    const f = fixture();
    const glyph = f.at[6].id;
    const shown = async () => (await (await ask(f.newEnv, '/atlas/documents/ndl%3Abook/characters')).json()).characters[0];
    expect((await shown()).atlas).toBe(false);
    f.atlas.exec(`INSERT INTO seen(submission,crop,cut,at) VALUES(1,${glyph},0,0)`);
    expect(await shown()).toMatchObject({ atlas: true, label: 'ん', state: 'pending', revision: 0, issue: null, source: 'transcription' });
    // A round's mark moves the revision; the listing shows the revision before it.
    f.atlas.exec(`INSERT INTO codes VALUES('state',3,'seen'); UPDATE crops SET state=3,revision=1 WHERE id=${glyph};
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${glyph},1,0,0,1,1,0,3)`);
    expect(await shown()).toMatchObject({ state: 'pending', revision: 0 });
    // A review that flags it as another character.
    f.atlas.exec(`INSERT INTO codes VALUES('issue',6,'reading'); UPDATE crops SET state=2,revision=2,char=2 WHERE id=${glyph};
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,issue) VALUES(${glyph},2,0,0,1,2,0,1,6)`);
    expect(await shown()).toMatchObject({ state: 'flagged', revision: 2, issue: 'character', label: 'し', source: 'review' });
  });

  it('show a marked crop pending at its revision before the marks, and a flagged one with its issue', async () => {
    const f = fixture();
    const [marked, flagged] = [f.at[2].id, f.at[3].id];
    f.atlas.exec(`INSERT INTO codes VALUES('state',3,'seen'),('issue',6,'reading');
      UPDATE crops SET state=3,revision=1 WHERE id=${marked}; UPDATE crops SET state=2,revision=1 WHERE id=${flagged};
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${marked},1,0,0,1,1,0,3);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,issue) VALUES(${flagged},1,0,0,1,1,0,1,6);`);
    const body = await (await ask(f.newEnv, '/atlas/runs?text=' + encodeURIComponent('んし') + '&document=' + encodeURIComponent('hk:83b29132c6d01a596a847de5ba3b0b0d'))).json();
    const crops = body.items.flatMap((i: Json) => i.crops);
    expect(crops.find((c: Json) => c.id === publicId(marked))).toMatchObject({ state: 'pending', revision: 0 });
    expect(crops.find((c: Json) => c.id === publicId(flagged))).toMatchObject({ state: 'flagged', revision: 1, issue: 'character' });
  });

  it('keep a flagged crop’s issue after a later event preserves it', async () => {
    const f = fixture(), crop = f.at[0].id;
    f.atlas.exec(`INSERT INTO codes VALUES('issue',6,'reading'),('issue',7,'other');
      UPDATE crops SET state=2,revision=2 WHERE id=${crop};
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,issue,kept_issue)
        VALUES(${crop},1,0,0,1,1,0,1,6,NULL),(${crop},2,0,0,1,2,0,2,7,6)`);
    const body = await (await ask(f.newEnv, '/atlas/runs?text=' + encodeURIComponent('んし'))).json();
    expect(body.items.flatMap((item: Json) => item.crops).find((item: Json) => item.id === publicId(crop)))
      .toMatchObject({ state: 'flagged', revision: 2, issue: 'character' });
    const book = await (await ask(f.newEnv, '/atlas/documents/' + encodeURIComponent('hk:83b29132c6d01a596a847de5ba3b0b0d') + '/characters')).json();
    expect(book.characters[0]).toMatchObject({ state: 'flagged', revision: 2, issue: 'character' });
  });

  it('shows a pending redraw without moving the published run geometry', async () => {
    const f = fixture(), crop = f.at[0].id;
    const path = '/atlas/runs?text=' + encodeURIComponent('んし');
    const before = (await (await ask(f.newEnv, path)).json()).items.find((item: Json) => item.crops[0].id === publicId(crop));
    f.atlas.exec(`INSERT INTO reviews(crop,gen,at,state,x,y,w,h) VALUES(${crop},1,0,0,1,2,7,8)`);
    const after = (await (await ask(f.newEnv, path)).json()).items.find((item: Json) => item.crops[0].id === publicId(crop));
    expect(after.crops[0]).toMatchObject({ box: { x: 1, y: 2, w: 7, h: 8 }, box_pending: true });
    expect(after.crops[0].crop_box).toEqual(before.crops[0].crop_box);
    expect(after.page).toEqual(before.page);
  });

  it('keeps a human reading over an older form decision in runs and listings until undone', async () => {
    const f = fixture(), crop = f.at[6].id;
    f.atlas.exec(`INSERT INTO form_glyphs(crop,family,cluster,form,issue,character,written_family,decision)
        VALUES(${crop},'U+3093',NULL,'ん',NULL,NULL,'U+3093','old');
      UPDATE crops SET char=3,state=1,revision=1 WHERE id=${crop};
      INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,named) VALUES(1,${crop},1,0,0,1,1,0,1,3)`);
    const shown = async () => {
      const body = await (await ask(f.newEnv, '/atlas/runs?text=' + encodeURIComponent('んし'))).json();
      return [body.items.flatMap((item: Json) => item.crops).find((item: Json) => item.id === publicId(crop)),
        ...(await listingItems(f.newEnv, [crop]))];
    };
    for (const item of await shown()) expect(item).toMatchObject({ label: '𛁅', char: '𛁅', written_character: '𛁅',
      code_point: 'U+1B045', grapheme: 'U+3057', identity_basis: 'human_review', identity_status: 'assigned', visual_group: null });
    f.atlas.exec(`UPDATE crops SET char=1,state=0,revision=2 WHERE id=${crop};
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,reverses) VALUES(${crop},2,0,0,1,2,1,5,1)`);
    for (const item of await shown()) expect(item).toMatchObject({ label: 'ん', identity_basis: 'form_glyph' });
    // The same form carried from a cluster decision of an earlier clustering is the glyph's cluster form.
    f.atlas.exec(`UPDATE form_glyphs SET carried='old' WHERE crop=${crop}`);
    for (const item of await shown()) expect(item).toMatchObject({ label: 'ん', identity_basis: 'form_cluster' });
  });

  it('does not add review revisions when a round mark is undone', async () => {
    const f = fixture(), crop = f.at[0].id;
    f.atlas.exec(`UPDATE crops SET revision=2 WHERE id=${crop};
      INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,reverses)
        VALUES(1,${crop},1,0,0,1,1,0,3,NULL),(2,${crop},2,0,0,1,2,1,5,1)`);
    const runs = await (await ask(f.newEnv, '/atlas/runs?text=' + encodeURIComponent('んし'))).json();
    expect(runs.items.flatMap((item: Json) => item.crops).find((item: Json) => item.id === publicId(crop)))
      .toMatchObject({ state: 'pending', revision: 0 });
    const book = await (await ask(f.newEnv, '/atlas/documents/' + encodeURIComponent('hk:83b29132c6d01a596a847de5ba3b0b0d') + '/characters')).json();
    expect(book.characters[0]).toMatchObject({ state: 'pending', revision: 0 });
  });

  it('show a crop as a review writes it, in the form filter and a book’s forms', async () => {
    const f = fixture();
    // A review names ex:o2 (し) as 𛁅: its row says so, and its run moves to that written form at once.
    await writtenStatement(f.newEnv, f.at[5].id, '𛁅').run();
    await writtenStatement(f.newEnv, f.at[5].id, '𛁅').run();
    expect(f.runsDb.query('SELECT char FROM run_written WHERE crop=?').all(f.at[5].id)).toEqual([{ char: '𛁅' }]);
    const other = await (await ask(f.newEnv, '/atlas/runs?text=' + encodeURIComponent('んし') + '&document=hk%3Aother')).json();
    expect(other.forms).toEqual([{ text: 'ん𛁅', n: 1 }]);
    const filtered = await (await ask(f.newEnv, '/atlas/runs?text=' + encodeURIComponent('んし') + '&form=' + encodeURIComponent('ん𛁅'))).json();
    expect(filtered.items.map((i: Json) => i.crops[0].source_id).sort()).toEqual(['ar:doc:1-l1-0', 'ex:o1']);
  });

  it('counts only trailing marks for each book character', () => {
    const f = fixture();
    f.atlas.exec(`INSERT INTO codes VALUES('state',3,'seen'),('state',4,'hard');
      INSERT INTO book_characters(book,ord,crop,data) VALUES(1,2,${f.at[2].id},'{}'),(1,3,${f.at[3].id},'{}');
      UPDATE crops SET state=3,revision=2 WHERE id=${f.at[0].id}; UPDATE crops SET state=2,revision=2 WHERE id=${f.at[1].id};
      UPDATE crops SET state=4,revision=1 WHERE id=${f.at[2].id};
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${f.at[0].id},1,0,0,1,1,0,1),(${f.at[0].id},2,0,0,1,2,0,3),
        (${f.at[1].id},1,0,0,1,1,0,3),(${f.at[1].id},2,0,0,1,2,0,1),(${f.at[2].id},1,0,0,1,1,0,4)`);
    const read = (sql: string) => f.atlas.query(sql).all(1, 0, 1000).map((r: Json) => [r.crop, r.state, r.revision]);
    expect(read(bookCharactersQuery()).map(r => r[2])).toEqual([1, 2, 0, 0]);
  });

  it('read every table by its key or an index', () => {
    const f = fixture();
    const plan = (db: Database, sql: string, values: unknown[]) => db.query('EXPLAIN QUERY PLAN ' + sql).all(...(values as any[])).map((r: Json) => r.detail as string);
    // A capped subquery's own rows (`s`, `(subquery-n)`) and a bound list (`j`) are read whole.
    const scans = (details: string[]) => details.filter(d => /^SCAN /.test(d) && !/^SCAN (j|s|json_each|\(subquery-\d+\))( |$)/.test(d));
    const shapes: [Database, string, unknown[]][] = [
      [f.runsDb, runRangeQuery(), [3, 'んし', 'んし\u{10FFFF}', '']], [f.runsDb, runHasQuery(), [2, 'んし']],
      [f.runsDb, bookFormsQuery(2), ['ん', 'し', 2, 'んし', 1]], [f.runsDb, bookFormsQuery(3), ['ん', 'し', 'ん', 3, 'んしん', 1]],
      [f.runsDb, runProbeQuery(false), ['んしん']], [f.runsDb, runProbeQuery(true), [1, 'んしん']],
      [f.atlas, runGraphemesQuery(), ['["ん"]']], [f.atlas, booksQuery(), ['[1]']],
      [f.atlas, corpusFormsQuery(), ['[1]']], [f.atlas, bookCharactersQuery(), [1, 0, 1000]], [f.atlas, reviewedQuery(), ['[1]']],
    ];
    for (const size of [2, 3, 5]) for (let anchor = 0; anchor + Math.min(size, 3) <= size; anchor++) for (const book of [false, true]) {
      const links = Math.max(0, size - 3);
      const bound = [...Array(links).fill('んし'), ...(book ? [1] : []), Math.min(size, 3), 'んしん'];
      shapes.push([f.runsDb, runOccurrencesQuery(size, anchor, book, true, 'hand', size <= 3), [...bound, ...(size <= 3 ? Array(size * 2).fill('ん') : []), 0, 48, 0]]);
      shapes.push([f.runsDb, runCountQuery(size, anchor, book), [...bound, 0]]);
      if (!book) {
        shapes.push([f.runsDb, runOccurrencesQuery(size, anchor, false, false, 'source'), [...bound, 48, 0]]);
        shapes.push([f.runsDb, runWorksQuery(size, anchor), bound]);
      }
    }
    for (const [db, sql, values] of shapes) {
      const details = plan(db, sql, values);
      if (process.env.SHOW_PLANS) console.log(sql.slice(0, 60).replace(/\s+/g, ' '), '|', details.join('; '));
      expect(scans(details)).toEqual([]);
    }
    // The long run reaches its other crops by their indexes.
    const long = plan(f.runsDb, runOccurrencesQuery(5, 1, false), ['んし', 'んし', 3, 'しんし', 48, 0]).join('; ');
    expect(long).toMatch(/INDEX run_first \(first=\?\)/);
    expect(long).toMatch(/INDEX run_second \(second=\?/);
    // A book's rows are its range of `run_book`.
    for (const sql of [runOccurrencesQuery(2, 0, true, true), runCountQuery(2, 0, true), bookFormsQuery(2)])
      expect(plan(f.runsDb, sql, sql === bookFormsQuery(2) ? ['ん', 'し', 2, 'んし', 1] : [1, 2, 'んし', 0, ...(sql.includes('OFFSET') ? [48, 0] : [])]).join('; '))
        .toContain('run_book (size=? AND graphemes=? AND book=?)');
  });
});
