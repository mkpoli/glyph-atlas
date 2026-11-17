import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { cropId } from '../atlas3';
import { currentPointer } from '../atlas3Answers';
import { booksRoute } from './books';

// The answer's pointer is held for a minute, and each setup publishes a pointer of its own.
(globalThis as any).caches = { default: { match: async () => undefined, put: async () => {} } };

function d1(db: Database) {
  const statement = (sql: string, args: unknown[] = []) => ({
    sql, args,
    bind: (...values: unknown[]) => statement(sql, values),
    first: async () => db.query(sql).get(...(args as any[])) ?? null,
    all: async () => ({ results: db.query(sql).all(...(args as any[])) }),
    run: async () => ({ meta: { changes: db.query(sql).run(...(args as any[])).changes } }),
  });
  return {
    prepare: (sql: string) => statement(sql),
    batch: async (list: ReturnType<typeof statement>[]) => list.map(s => /^\s*SELECT/i.test(s.sql)
      ? { results: db.query(s.sql).all(...(s.args as any[])), meta: { changes: 0 } }
      : { results: [], meta: { changes: db.query(s.sql).run(...(s.args as any[])).changes } }),
  };
}

let publishes = 0;
async function setup() {
  const db = new Database(':memory:');
  const migrations = new URL('../../migrations-atlas/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort())
    db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  db.exec(`INSERT INTO books(id,key,title) VALUES(1,'hk:a','Book A'),(2,'codh:c','Corpus C'),(3,'hl:z','別書'),(4,'hk:empty','Empty');
    INSERT INTO pages(id,key,book,rev) VALUES(100,'hk:a:0',1,1),(101,'hk:a:10',1,1),(102,'corpus:codh:c:0',2,1),(103,'hl:z:0',3,1);
    INSERT INTO book_dates(book,axis,kind,start,end,precision,text,label,status,calendar,source) VALUES
      (1,'witness','printed',1712,1712,'year','正徳二年','1712','single','japanese','colophon'),
      (1,'composed','composed',1600,1650,'range','慶長','1600-1650','single','japanese','preface'),
      (3,'witness','copied',1850,NULL,'year','嘉永','1850','single','japanese','note');`);
  const add = (page: number, slot: number, book: number) => db.query(
    'INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(?,0,?,0,1,?)')
    .run(cropId(page, slot), book, cropId(page, slot));
  for (let slot = 0; slot < 3; slot++) add(100, slot, 1);
  add(101, 0, 1); add(102, 0, 2); add(103, 0, 3);
  const pub = `test${++publishes}`;
  const objects = new Map<string, Uint8Array>([
    [`v3/pub/${pub}/books.json.gz`, gzipSync(JSON.stringify({ pub, books: { 1: [4, 0], 2: [2, 0], 3: [1, 0] } }))],
    ['v3/pointer.json', gzipSync(JSON.stringify({ pub, through_gen: 0 }))],
  ]);
  const bucket = { get: async (key: string) => {
    const bytes = objects.get(key);
    return bytes ? { body: new Response(bytes).body } : null;
  } };
  const env = { ATLAS: d1(db), MEDIA: bucket } as unknown as Env;
  await currentPointer(env, 1e13 + publishes * 120_000);
  return env;
}

const get = async (env: Env, path: string) => {
  const response = await booksRoute(env, new URL(`https://glyphatlas.org${path}`));
  return { status: response!.status, body: await response!.json() as any };
};

describe('the public books', () => {
  it('lists the books whose pages this site can open, by title', async () => {
    const { status, body } = await get(await setup(), '/atlas/books');
    expect(status).toBe(200);
    expect(body.total).toBe(2);
    expect(body.books.map((book: any) => book.id)).toEqual(['hk:a', 'hl:z']);
    expect(body.books[0]).toMatchObject({ title: 'Book A', page_count: 2, box_count: 4, cover_url: null });
    expect(body.books[0].dating.witness).toMatchObject({ kind: 'printed', start: 1712, label: '1712' });
    expect(body.books[0].dating.composed).toMatchObject({ kind: 'composed', start: 1600, end: 1650 });
  });

  it('finds a book by a Latin word start, a Han word inside it, or a date', async () => {
    const env = await setup();
    expect((await get(env, '/atlas/books?q=book')).body.books.map((book: any) => book.id)).toEqual(['hk:a']);
    expect((await get(env, '/atlas/books?q=別')).body.books.map((book: any) => book.id)).toEqual(['hl:z']);
    expect((await get(env, '/atlas/books?q=1712')).body.books.map((book: any) => book.id)).toEqual(['hk:a']);
    expect((await get(env, '/atlas/books?q=no-such-book')).body.total).toBe(0);
  });

  it('lists one book\'s pages in number order with their box counts', async () => {
    const { status, body } = await get(await setup(), '/atlas/books/hk%3Aa');
    expect(status).toBe(200);
    expect(body).toMatchObject({ id: 'hk:a', title: 'Book A', page_count: 2, box_count: 4 });
    expect(body.pages).toEqual([
      { id: 'hk:a:0', seq: 0, units: 3, width: null, height: null, thumb_url: null },
      { id: 'hk:a:10', seq: 10, units: 1, width: null, height: null, thumb_url: null },
    ]);
  });

  it('answers a book it does not hold with 404', async () => {
    await expect(booksRoute(await setup(), new URL('https://glyphatlas.org/atlas/books/hk%3Anone')))
      .rejects.toMatchObject({ status: 404 });
  });

  it('refuses a list without a positive limit', async () => {
    await expect(booksRoute(await setup(), new URL('https://glyphatlas.org/atlas/books?limit=0')))
      .rejects.toMatchObject({ status: 422 });
  });
});
