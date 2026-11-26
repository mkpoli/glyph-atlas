// Addresses and records made under a grapheme's key before a load files it under another's: 国 heads a family, and
// the load files it under 國. Each is read once before the load, so what the edge and the isolate keep of it is
// there to be wrong.
import { Database } from 'bun:sqlite';
import { afterEach, beforeEach, describe, expect, it, setSystemTime } from 'bun:test';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { filedClaimsKept } from '../atlas3Gallery';
import { catalogueVersion } from '../atlas3Stamps';
import { d1 } from '../forms.test';
import { keyHeads } from '../ledger';
import worker from '../index';
import { graphemeMembers } from './browse';
import { runGraphemesQuery } from './runs';

const OLD = 'U+56FD', NEW = 'U+570B', START = new Date('2026-11-25T00:00:00Z');
let db: Database, env: Env, objects: Map<string, Uint8Array>, stored: Map<string, Response>, waits: Promise<unknown>[], now: number, media = 0;
const card = (char: string, head: string, members: string[]) => {
  const at = (c: string) => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase(), grapheme = { code_point: at(head), char: head };
  objects.set(`v3/reference/char/${at(char)}.json.gz`, gzipSync(JSON.stringify({ grapheme: 1, ids: { 國: 1, 国: 2 }, variants: [], derived: [], substitutions: [],
    writes: [], writers: [], data: { code_point: at(char), char, grapheme }, detail: { code_point: at(char), char,
      grapheme: { ...grapheme, members: members.map(c => ({ char: c, code_point: at(c) })) } } })));
};
const get = async (path: string) => {
  const response = await worker.fetch(new Request('https://glyphatlas.org' + path), env, { waitUntil: (p: Promise<unknown>) => waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext);
  await Promise.all(waits.splice(0));
  return await response.json() as any;
};
const after = (seconds: number) => setSystemTime(new Date(now += seconds * 1000));
// What the load does, in its order: the heads and their stamp, then the cards.
const heads = () => db.exec(`UPDATE graphemes SET head=1 WHERE id=2; INSERT INTO imports(stamp, at) VALUES('reference-5-x.rows', 1)`);
const cards = () => { for (const char of '國国') card(char, '國', ['國', '国']) };

beforeEach(() => {
  db = new Database(':memory:'); objects = new Map(); stored = new Map(); waits = [];
  const layout = new URL('../../migrations-atlas/', import.meta.url);
  for (const file of readdirSync(layout).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, layout), 'utf8'));
  db.exec(`INSERT INTO graphemes(id,text,head) VALUES(1,'國',1),(2,'国',2),(3,'家',3);
    INSERT INTO books(id,key,title) VALUES(1,'b','b');
    INSERT INTO crops(id,kind,book,char,family,state,quiz,shuffle) VALUES(4096,0,1,1,1,0,1,0),(4097,0,1,2,2,0,1,1),(4098,0,1,3,3,0,1,2);
    INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
      VALUES('x','has_grapheme','','',4098,0,'asserted','${OLD}','[]','[]','[]','r','t')`);
  card('國', '國', ['國']); card('国', '国', ['国']);
  for (let shard = 0; shard < 256; shard++) objects.set(`v3/pub/T/counts/${shard}.json.gz`, gzipSync(JSON.stringify({ pub: 'T', chars: {}, families: {} })));
  objects.set('v3/pub/T/corpus.json.gz', gzipSync(JSON.stringify({ pub: 'T', cells: [], filed: [], chars: {} })));
  objects.set('v3/pointer.json', gzipSync(JSON.stringify({ pub: 'T', through_gen: 0 })));
  const MEDIA = { held: ++media, get: async (name: string) => { const bytes = objects.get(name); return bytes ? { arrayBuffer: async () => new Uint8Array(bytes).buffer,
    json: async () => JSON.parse(new TextDecoder().decode(Bun.gunzipSync(bytes))), text: async () => new TextDecoder().decode(Bun.gunzipSync(bytes)),
    body: new Response(bytes).body!, etag: 'e' } : null } };
  // An edge that forgets an answer when its max-age is over.
  const ends = new Map<string, number>();
  (globalThis as any).caches = { default: { match: async (key: Request) => Date.now() < (ends.get(key.url) ?? 0) ? stored.get(key.url)!.clone() : undefined,
    put: async (key: Request, value: Response) => { stored.set(key.url, value); ends.set(key.url, Date.now() + 1000 * Number(/max-age=(\d+)/.exec(value.headers.get('cache-control') ?? '')?.[1] ?? 0)) } } };
  // Each test starts past what the isolate kept for the one before it.
  now = START.getTime() + media * 1e8; setSystemTime(new Date(now));
  env = { ATLAS: d1(db), MEDIA, CATALOGUE_LAG: '0' } as unknown as Env;
});
afterEach(() => { setSystemTime(); db.close() });

describe('a grapheme\'s old key, once a load files it under another', () => {
  it('opens a page that names the new head, where its address leads', async () => {
    expect((await get(`/layers/characters/${OLD}`)).grapheme.code_point).toBe(OLD);
    heads(); cards(); after(301);
    const page = await get(`/layers/characters/${OLD}`);
    expect(page.grapheme).toMatchObject({ code_point: NEW, members: [{ char: '國' }, { char: '国' }] });
  });

  it('keeps the old card for the five minutes an isolate or the edge holds it, and no longer', async () => {
    await get(`/layers/characters/${OLD}`);
    heads(); cards(); after(60);
    expect((await get(`/layers/characters/${OLD}`)).grapheme.code_point).toBe(OLD);
    after(250);
    expect((await get(`/layers/characters/${OLD}`)).grapheme.code_point).toBe(NEW);
  });

  it('is cited by the new head, five minutes and an hour after the load at the latest', async () => {
    const cited = async () => (await get(`/atlas/cite/grapheme/${OLD}`))[0].URL;
    expect(await cited()).toBe(`https://glyphatlas.org/grapheme/${OLD}`);
    heads(); cards(); after(60);
    // Made in the five minutes of the old card, the citation is kept as long as any other.
    expect(await cited()).toBe(`https://glyphatlas.org/grapheme/${OLD}`);
    after(301);
    expect(await cited()).toBe(`https://glyphatlas.org/grapheme/${OLD}`);
    after(3600);
    expect(await cited()).toBe(`https://glyphatlas.org/grapheme/${NEW}`);
  });

  it('is the new head\'s in a claim filed under it, from the load on', async () => {
    const filed = async () => (await filedClaimsKept(env, await catalogueVersion(env))).map(r => [r.grapheme, r.head]);
    expect(await filed()).toEqual([[OLD, OLD]]);
    heads();
    expect(await filed()).toEqual([[OLD, NEW]]);
    expect(await keyHeads(env, [OLD])).toEqual(new Map([[OLD, NEW]]));
  });

  it('is the new head\'s in a run\'s address, from the load on', () => {
    const folded = () => db.query(runGraphemesQuery()).all(JSON.stringify(['国', '家']) as any);
    heads();
    expect(folded()).toEqual([{ character: '国', head: '國' }, { character: '家', head: '家' }]);
  });

  it('names one character in a round\'s or a listing\'s address, as a member\'s key does', async () => {
    expect((await graphemeMembers(env, OLD)).texts).toEqual(['国']);
    heads();
    expect((await graphemeMembers(env, OLD)).texts).toEqual(['国']);
    expect((await graphemeMembers(env, NEW)).texts).toEqual(['國', '国']);
  });
});
