// A small catalogue on the new storage layout for the browse routes' tests: three books (two
// collected, one of corpus glyphs), their page bundles and a record pack in a fake R2, and the
// publication's answers written by the statements scripts/atlas3/answers.py runs.
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { bundleKey, cropId } from '../atlas3';
import { currentPointer } from '../atlas3Gallery';
import { catalogue } from './browse';

export const databases: Database[] = [];
// R2 as the binding answers: the stored bytes, a range when one is asked for.
export function bucket(objects: Map<string, Uint8Array>) {
  return {
    get: async (key: string, options?: { range?: { offset: number; length: number } }) => {
      const bytes = objects.get(key);
      if (!bytes) return null;
      const body = options?.range ? bytes.subarray(options.range.offset, options.range.offset + options.range.length) : bytes;
      return { body: new Response(body).body!, arrayBuffer: async () => new Uint8Array(body).buffer };
    },
  };
}
export const ctx = { waitUntil: () => {} } as unknown as ExecutionContext;
export const gz = (value: unknown) => gzipSync(JSON.stringify(value));

// The answers scripts/atlas3/answers.py writes, from the same statements.
const FACETS = `SELECT coalesce(char, -1), book, coalesce(production, -1), coalesce(category, -1), state, quiz, count(*)
  FROM crops WHERE kind = 0 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2, 3, 4, 5, 6`;
const CORPUS = `SELECT char, coalesce(production, -1), state, quiz, count(*) FROM crops WHERE kind = 1 AND char IS NOT NULL
  GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4`;
const BOOKS = 'SELECT book, sum(kind = 0), sum(kind = 1) FROM crops WHERE kind < 2 GROUP BY book ORDER BY book';
const key = (text: string) => [...text].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
export function publish(db: Database, objects: Map<string, Uint8Array>, pub: string, through: number) {
  const chars = (ids: number[]) => Object.fromEntries((db.query(`SELECT g.id, g.text, coalesce(h.text, g.text) AS head FROM graphemes g
    LEFT JOIN graphemes h ON h.id = g.head WHERE g.id IN (${ids.join(',') || 'NULL'})`).all() as { id: number; text: string; head: string }[])
    .map(r => [String(r.id), [r.text, key(r.head)]]));
  const facets = db.query(FACETS).values() as number[][], corpus = db.query(CORPUS).values() as number[][];
  objects.set(`v3/pub/${pub}/facets.json.gz`, gz({ pub, cells: facets, chars: chars(facets.map(r => r[0]).filter(c => c !== -1)) }));
  objects.set(`v3/pub/${pub}/corpus.json.gz`, gz({ pub, cells: corpus, chars: chars(corpus.map(r => r[0])) }));
  objects.set(`v3/pub/${pub}/books.json.gz`, gz({ pub, books: Object.fromEntries((db.query(BOOKS).values() as number[][]).map(([b, x, y]) => [String(b), [x, y]])) }));
  objects.set('v3/pointer.json', gz({ pub, through_gen: through }));
}

let pages = 20000;
export const PENDING = 0, CHECKED = 1, FLAGGED = 2, SEEN = 3, HARD = 4;
export const KANJI = 2, KANA = 3, HANGUL = 5;
// An isolate holds the pointer for a minute; each setup reads its own as if later than any other test.
let later = 0;
export async function setup() {
  const db = new Database(':memory:'); databases.push(db);
  const migrations = new URL('../../migrations-atlas/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  // Each setup takes pages of its own, since bundles are held per isolate by page and revision.
  const a = ++pages, b = ++pages, c = ++pages;
  db.exec(`INSERT INTO codes VALUES('state',0,'pending'),('state',1,'checked'),('state',2,'flagged'),('style',0,'cursive'),
      ('category',2,'kanji'),('category',3,'kana'),('category',5,'hangul');
    INSERT INTO productions(id,key,parent) VALUES(1,'unknown',NULL),(2,'printed',NULL),(3,'printed/type',2),(4,'handwritten',NULL);
    INSERT INTO books(id,key,title) VALUES(1,'hk:a','Book A'),(2,'hk:b','Book B'),(3,'codh:c','Corpus C');
    INSERT INTO graphemes(id,text,head) VALUES(1,'は',1),(2,'ハ',1),(3,'𛂞',1),(4,'所',NULL),(5,'한',NULL),(6,'処',NULL);
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${a},'hk:a:1',1,1,12),(${b},'hk:b:1',2,1,6),(${c},'corpus:codh:c:0',3,1,10);
    INSERT INTO generation VALUES(0,1);
    INSERT INTO actors(id,key,user,via,at) VALUES(1,'me','u1','account',0),(2,'other','u2','account',0);
    INSERT INTO submissions(id,key,actor,request,response,at,undone) VALUES
      (1,'me:1',1,'{"input":{},"target":null}','{}',0,0),(2,'other:1',2,'{"input":{},"target":null}','{}',0,0),
      (3,'other:2',2,'{"input":{},"target":"x"}','{}',0,0),(4,'other:3',2,'{"input":{},"target":"y"}','{}',0,1);
    INSERT INTO book_dates(book,axis,kind,start,end,precision,text,label,status,calendar,source) VALUES
      (1,'witness','printed',1712,1712,'year','正徳二年','1712','single','japanese','colophon'),
      (1,'composed','composed',1600,1650,'range','慶長','1600-1650','single','japanese','preface'),
      (3,'witness','copied',1850,NULL,'year','嘉永','1850','single','japanese','note');
    INSERT INTO site VALUES('collection','{"crops":18}'),('corpus_index','{"sources":2}'),('review_epoch','3');`);
  // [page, slot, char, state, quiz, production, category]
  const collected: [number, number, number | null, number, number, number, number | null][] = [
    [a, 0, 1, PENDING, 1, 2, KANA], [a, 1, 1, PENDING, 1, 2, KANA], [a, 2, 1, CHECKED, 1, 2, KANA], [a, 3, 2, PENDING, 1, 3, KANA],
    [a, 4, 2, SEEN, 1, 3, KANA], [a, 5, 4, PENDING, 1, 2, KANJI], [a, 6, 4, FLAGGED, 1, 2, KANJI], [a, 7, 4, PENDING, 0, 2, KANJI],
    [a, 8, null, PENDING, 1, 2, null], [a, 9, 3, HARD, 1, 4, KANA], [a, 10, 1, PENDING, 0, 2, KANA], [a, 11, 6, PENDING, 1, 4, KANJI],
    [b, 0, 1, PENDING, 1, 4, KANA], [b, 1, 4, CHECKED, 1, 4, KANJI], [b, 2, 4, PENDING, 1, 4, KANJI], [b, 3, 2, FLAGGED, 0, 4, KANA],
    [b, 4, 1, PENDING, 1, 3, KANA], [b, 5, 4, PENDING, 1, 3, KANJI]];
  const insert = db.prepare('INSERT INTO crops(id,kind,book,char,production,category,style,state,quiz,shuffle,cut,revision,flags) VALUES(?,?,?,?,?,?,0,?,?,?,0,0,0)');
  const shuffle = (id: number) => (id * 2654435761) % 2147483648;
  for (const [page, slot, char, state, quiz, production, category] of collected)
    insert.run(cropId(page, slot), 0, page === a ? 1 : 2, char, production, category, state, quiz, shuffle(cropId(page, slot)));
  // Corpus glyphs: は ×4 printed, 𛂞 ×3 handwritten (one checked), 한, 所 of movable type and one no one has read.
  const corpus: [number, number | null, number, number][] = [[0, 1, PENDING, 2], [1, 1, PENDING, 2], [2, 1, PENDING, 2], [3, 1, PENDING, 2],
    [4, 3, PENDING, 4], [5, 3, PENDING, 4], [6, 3, CHECKED, 4], [7, 5, PENDING, 2], [8, 4, PENDING, 3], [9, null, PENDING, 2]];
  for (const [slot, char, state, production] of corpus) insert.run(cropId(c, slot), 1, 3, char, production, null, state, 1, shuffle(cropId(c, slot)));
  const text = (char: number | null) => char === null ? '' : (db.query('SELECT text FROM graphemes WHERE id=?').get(char) as { text: string }).text;
  const records = corpus.map(([slot, char]) => new TextEncoder().encode(JSON.stringify({ id: `codh:${slot}`, label: text(char), proxyable: slot !== 2,
    image: `/atlas/media/c${slot}.webp`, text: 'line' })));
  const pack = new Uint8Array(records.reduce((n, r) => n + r.length, 0));
  let at = 0; const places = records.map(r => { pack.set(r, at); at += r.length; return [at - r.length, r.length]; });
  const objects = new Map<string, Uint8Array>([
    [bundleKey(a, 1), gz({ key: 'hk:a:1', book: 1, rev: 1, page: null, crops: collected.filter(r => r[0] === a)
      .map(([, slot, char]) => ({ slot, id: `hk:a${slot}`, label: text(char), image: `/atlas/media/a${slot}.webp`, box: { x: slot, y: 0, w: 1, h: 1 } })) })],
    [bundleKey(b, 1), gz({ key: 'hk:b:1', book: 2, rev: 1, page: null, crops: collected.filter(r => r[0] === b)
      .map(([, slot, char]) => ({ slot, id: `hk:b${slot}`, label: text(char), image: `/atlas/media/b${slot}.webp` })) })],
    [bundleKey(c, 1), gz({ key: 'corpus:codh:c:0', book: 3, rev: 1, page: null, crops: corpus.map(([slot, char]) =>
      ({ slot, id: `codh:${slot}`, label: text(char), record_place: ['packs/c.bin', ...places[slot]] })) })],
    ['packs/c.bin', pack]]);
  // Answers are held per isolate by publication, so each setup publishes its own.
  const pub = `20261020T01${String(pages % 10000).padStart(4, '0')}Z`;
  publish(db, objects, pub, 0);
  const env = { ATLAS: d1(db), MEDIA: bucket(objects), CATALOGUE_LAG: '0' } as unknown as Env;
  const crop = (page: number, slot: number) => cropId(page, slot);
  await currentPointer(env, 1e13 + ++later * 120_000);
  return { db, env, objects, a, b, c, crop, records, pub };
}
export const get = (env: Env, path: string, reviewer: string | null = null) => {
  const url = new URL(`https://glyphatlas.org${path}`);
  return catalogue(env, ctx, url, reviewer);
};
// Every page of a listing, as the browser asks for them, handing back `above`.
export async function every(env: Env, path: string, limit: number, reviewer: string | null = null) {
  const seen: string[] = [];
  let offset = 0, above: number | null = null, body: any;
  do {
    body = await get(env, `${path}&limit=${limit}&offset=${offset}${above !== null ? `&above=${above}` : ''}`, reviewer);
    seen.push(...body.items.map((item: any) => item.id));
    above = body.above ?? above;
    if (body.next_offset <= offset) break;
    offset = body.next_offset;
  } while (offset < body.total);
  return { seen, body };
}

