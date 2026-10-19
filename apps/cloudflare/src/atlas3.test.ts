import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from './forms.test';
import { PAGE, bundle, bundleKey, cropId, cropItems, cropOf, pageOf, publicId, slotOf } from './atlas3';

const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

// R2 as the binding answers: the stored bytes, a range when one is asked for.
function bucket(objects: Map<string, Uint8Array>, reads: string[] = []) {
  return {
    get: async (key: string, options?: { range?: { offset: number; length: number } }) => {
      reads.push(key);
      const bytes = objects.get(key);
      if (!bytes) return null;
      const body = options?.range ? bytes.subarray(options.range.offset, options.range.offset + options.range.length) : bytes;
      return { body: new Response(body).body!, text: async () => new TextDecoder().decode(body), arrayBuffer: async () => body.buffer };
    },
  };
}

let pages = 1000;
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  db.exec(readFileSync(new URL('../migrations-atlas/0001_init.sql', import.meta.url), 'utf8'));
  // Each test takes pages of its own, since bundles are held per isolate by page and revision.
  const page = ++pages, corpusPage = ++pages;
  const a = cropId(page, 0), b = cropId(page, 1), c = cropId(page, 2), g = cropId(corpusPage, 0);
  db.exec(`INSERT INTO books VALUES(1,'hk:book'),(2,'corpus-book');
    INSERT INTO graphemes(id,text) VALUES(1,'所'),(2,'処');
    INSERT INTO productions(id,key,parent) VALUES(1,'handwritten',NULL);
    INSERT INTO codes VALUES('state',0,'pending'),('state',1,'checked'),('style',0,'cursive'),('category',2,'kanji');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${page},'hk:book:1',1,3,3),(${corpusPage},'corpus:x:corpus-book:0',2,1,1);
    INSERT INTO crops(id,kind,book,char,production,category,style,state,quiz,year,shuffle,cut,revision,flags) VALUES
      (${a},0,1,2,1,2,0,1,1,1790,5,0,4,1),(${b},0,1,1,1,2,0,0,0,NULL,6,0,0,0),(${c},2,1,1,1,2,0,0,1,NULL,7,0,0,0),
      (${g},1,2,1,1,NULL,NULL,0,1,NULL,8,0,0,0);`);
  const record = new TextEncoder().encode(JSON.stringify({ id: 'kmnist:1', label: '所', image: '/atlas/media/k.webp', text: 'line' }));
  const pack = new Uint8Array(100 + record.length); pack.set(record, 100);
  const objects = new Map<string, Uint8Array>([
    [bundleKey(page, 3), gzipSync(JSON.stringify({ key: 'hk:book:1', book: 1, rev: 3, page: { width: 10 }, crops: [
      { slot: 0, id: 'hk:a', label: '所', image: '/atlas/media/a.webp', context_box: { x: 0 }, production_evidence: ['colophon'] },
      { slot: 1, id: 'hk:b', label: '所', image: '/atlas/media/b.webp' },
      { slot: 2, id: 'hk:c', label: '所', image: '/atlas/media/c.webp' }] }))],
    [bundleKey(corpusPage, 1), gzipSync(JSON.stringify({ key: 'corpus:x:corpus-book:0', book: 2, rev: 1, page: null, crops: [
      { slot: 0, id: 'kmnist:1', label: '所', record_place: ['packs/p.bin', 100, record.length] }] }))],
    ['packs/p.bin', pack]]);
  const reads: string[] = [];
  const env = { ATLAS: d1(db), MEDIA: bucket(objects, reads) } as unknown as Env;
  return { db, env, reads, page, a, b, c, g };
}

describe('new-layout crop ids', () => {
  it('split into page and slot past 32 bits', () => {
    const id = cropId(11932, 8388609);
    expect(id).toBe(11932 * PAGE + 8388609);
    expect([pageOf(id), slotOf(id)]).toEqual([11932, 8388609]);
    expect(cropId(2 ** 29 - 1, PAGE - 1)).toBe(Number.MAX_SAFE_INTEGER);
  });

  it('round-trip through Crockford base 32, reading i, l and o as digits', () => {
    for (const id of [PAGE, cropId(1, 73), cropId(11932, 255), cropId(2 ** 29 - 1, PAGE - 1)]) expect(cropOf(publicId(id))).toBe(id);
    const text = publicId(cropId(1, 1));
    expect(cropOf(text.toUpperCase())).toBe(cropId(1, 1));
    expect(cropOf('10')).toBe(cropOf('lO'));
    expect(cropOf('ex:a1')).toBeNull();
    expect(cropOf('u')).toBeNull();
    expect(cropOf('0')).toBeNull();
  });
});

describe('new-layout listing items', () => {
  it('show the bundle record under the row’s current values, in the order asked', async () => {
    const { env, a, b } = setup();
    const items = await cropItems(env, [b, a]);
    expect(items.map(item => item.source_id)).toEqual(['hk:b', 'hk:a']);
    expect(items[1]).toMatchObject({ id: publicId(a), label: '処', state: 'checked', style: 'cursive', category: 'kanji',
      production: 'handwritten', revision: 4, cut: 0, document: 'hk:book', year: 1790, withheld: false, flags: 1,
      image: '/atlas/media/a.webp', production_evidence: ['colophon'], origin: 'local' });
    expect(items[1].context_box).toBeUndefined();
    expect(items[1].page_id).toBe('hk:book:1');
    expect(items[0]).toMatchObject({ state: 'pending', withheld: true, year: null });
  });

  it('leave out candidates and ids with no crop', async () => {
    const { env, a, c } = setup();
    expect((await cropItems(env, [c, cropId(999999, 0), a])).map(item => item.source_id)).toEqual(['hk:a']);
  });

  it('read a corpus glyph’s record from its pack', async () => {
    const { env, g } = setup();
    const [item] = await cropItems(env, [g]);
    expect(item).toMatchObject({ id: publicId(g), source_id: 'kmnist:1', origin: 'corpus', image: '/atlas/media/k.webp', label: '所', state: 'pending' });
    expect(item.text).toBeUndefined();
    expect(item.record_place).toBeUndefined();
  });

  it('read each bundle once per isolate', async () => {
    const { env, reads, page, a, b } = setup();
    await cropItems(env, [a, b]);
    await cropItems(env, [a]);
    expect(reads.filter(key => key === bundleKey(page, 3))).toHaveLength(1);
    expect((await bundle(env, page, 3))?.bySlot.get(1)?.id).toBe('hk:b');
  });
});
