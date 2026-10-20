import { Database } from 'bun:sqlite';
import { beforeEach, describe, expect, it } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { LIST_PAGE, cardList, characterObject, characterStructure, componentMatches, familySummaries, nameMatches, referenceShard } from './atlas3Reference';
import { COMPONENT_SCAN, componentMatchQuery, rankMatches } from './components';

const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());

function bucket(objects: Record<string, unknown>) {
  const reads: string[] = [];
  const MEDIA = { get: async (name: string) => {
    reads.push(name);
    const value = objects[name];
    return value === undefined ? null : { arrayBuffer: async () => new Uint8Array(gzipSync(JSON.stringify(value))).buffer };
  } };
  return { env: { MEDIA } as unknown as Env, reads };
}

describe('reference objects', () => {
  it('name a shard by the first two hex digits of the SHA-256, as reference.py does', async () => {
    // Python: hashlib.sha256(key.encode()).hexdigest()[:2]
    expect(await referenceShard('U+3042')).toBe('08');
    expect(await referenceShard('好')).toBe('f8');
  });

  it('read a card by its code points and answer null for one no card has', async () => {
    const { env } = bucket({ 'v3/reference/char/U+3042-U+3099.json.gz': { grapheme: 9 } });
    expect((await characterObject(env, 'U+3042 U+3099'))?.grapheme).toBe(9);
    expect(await characterObject(env, 'U+0000')).toBeNull();
  });

  it('read each family shard once for members of several heads', async () => {
    const a = await referenceShard('U+3044'), b = await referenceShard('U+3046');
    const { env, reads } = bucket({ [`v3/reference/summary/${a}.json.gz`]: { 'U+3044': { char: 'い' }, 'U+1B006': { char: '𛀆' } },
      [`v3/reference/summary/${b}.json.gz`]: { 'U+3046': { char: 'う' } } });
    const found = await familySummaries(env, ['U+3044', 'U+3044', 'U+3046']);
    expect([...found.keys()].sort()).toEqual(['U+1B006', 'U+3044', 'U+3046']);
    expect(reads.length).toBe(a === b ? 1 : 2);
  });

  it('find a structure and answer null for a character the shard lacks', async () => {
    const { env } = bucket({ [`v3/reference/ids/${await referenceShard('姥')}.json.gz`]: { '姥': [['⿰女老'], null] } });
    expect(await characterStructure(env, '姥')).toEqual({ sequences: ['⿰女老'], shapes: null });
    expect(await characterStructure(env, 'constructor')).toBeNull();
  });

  it('page a list across two objects at most', async () => {
    const items = Array.from({ length: LIST_PAGE + 10 }, (_, i) => ({ i }));
    const { env, reads } = bucket({
      'v3/reference/list/graphemes/0.json.gz': { total: items.length, offset: 0, items: items.slice(0, LIST_PAGE) },
      'v3/reference/list/graphemes/1.json.gz': { total: items.length, offset: LIST_PAGE, items: items.slice(LIST_PAGE) } });
    const page = await cardList(env, 'graphemes', LIST_PAGE - 2, 5);
    expect(page).toEqual({ items: items.slice(LIST_PAGE - 2, LIST_PAGE + 3), total: LIST_PAGE + 10 });
    expect(reads.length).toBe(2);
  });

  it('find a card by its name or code point, those named by code point included', async () => {
    const { env } = bucket({ 'v3/reference/names.json.gz': {
      names: [['U+3042', 'HIRAGANA LETTER A', 50], ['U+304B U+309A', '', 0], ['U+2F800', 'CJK COMPATIBILITY IDEOGRAPH-2F800', 0]],
      plain: [['CJK UNIFIED IDEOGRAPH-', [0x4E00, 1, 0x24E00 - 0x4E01], [3, 9, 0]]] } });
    expect(await nameMatches(env, 'hiragana', 8)).toEqual({ codes: ['U+3042'], total: 1 });
    expect(await nameMatches(env, '4e00', 8)).toEqual({ codes: ['U+4E00', 'U+24E00'], total: 2 });
    expect(await nameMatches(env, 'U+4E00', 8)).toEqual({ codes: ['U+4E00'], total: 1 });
    expect(await nameMatches(env, 'u+309a', 8)).toEqual({ codes: ['U+304B U+309A'], total: 1 });
    // The most frequent first: 丁 (9) before 一 (3).
    expect(await nameMatches(env, 'unified ideograph', 1)).toEqual({ codes: ['U+4E01'], total: 3 });
  });

  // Rows as han_components holds them: [component, tier, size, code point, n, direct].
  type Row = [string, number, number, number, number, number];
  // A component's list as reference.py writes it.
  function componentList(rows: Row[]) {
    const groups: [number, number, number[], number[]][] = [];
    let last = 0;
    for (const [, tier, size, point, n, direct] of [...rows].sort((a, b) => a[1] - b[1] || a[2] - b[2] || a[3] - b[3])) {
      const group = groups.at(-1);
      if (group && group[0] === tier && group[1] === size) { group[2].push(point - last); group[3].push(n * 2 + direct); }
      else groups.push([tier, size, [point], [n * 2 + direct]]);
      last = point;
    }
    return { total: rows.length, groups };
  }
  const key = (c: string) => `U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}`;

  function components(rows: Row[], other: Record<string, string> = {}) {
    const byComponent = new Map<string, Row[]>();
    for (const row of rows) byComponent.set(row[0], [...(byComponent.get(row[0]) ?? []), row]);
    const objects: Record<string, unknown> = { 'v3/reference/components/names.json.gz': { same: [...byComponent.keys()].join(''), other } };
    for (const [c, list] of byComponent) objects[`v3/reference/components/${key(c)}.json.gz`] = componentList(list);
    // The D1 search over the same rows, as the old database answered it.
    const db = new Database(':memory:');
    db.run('CREATE TABLE han_components(component, tier, size, code_point, n, direct, PRIMARY KEY(component, tier, size, code_point))');
    const insert = db.prepare('INSERT INTO han_components VALUES(?,?,?,?,?,?)');
    for (const [c, tier, size, point, n, direct] of rows) insert.run(c, tier, size, `U+${point.toString(16).toUpperCase()}`, n, direct);
    const totals = new Map([...byComponent].map(([c, list]) => [c, list.length]));
    const sql = (wanted: Map<string, number>, limit: number) => {
      const [driver, ...rest] = [...wanted].sort((a, b) => totals.get(a[0])! - totals.get(b[0])!);
      const found = db.query(componentMatchQuery(rest.length)).all(driver[0], ...rest.flatMap(([c, n]) => [c, n]), driver[1]) as any[];
      const ranked = rankMatches(found);
      return { codes: ranked.slice(0, limit).map(r => r.code_point), more: ranked.length > limit || totals.get(driver[0])! > COMPONENT_SCAN };
    };
    return { ...bucket(objects), sql };
  }

  // A seeded spread of characters over five components, tiers and sizes, 一 past COMPONENT_SCAN, and 滑
  // with its parts. The isolate holds what a test read, so every test reads this one set.
  let seed = 7;
  const random = () => (seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
  const rows: Row[] = [['氵', 0, 3, 0x6ED1, 1, 1], ['骨', 0, 3, 0x6ED1, 1, 1], ['骨', 0, 3, 0x9AB0, 1, 1]];
  for (let point = 0x4E00; point < 0x4E00 + 5000; point++) {
    const tier = Math.floor(random() * 3), size = 2 + Math.floor(random() * 12);
    for (const c of ['一', '口', '木', '水', '𠂉']) {
      if (c === '一' ? random() < 0.8 : random() < 0.15)
        rows.push([c, tier, size, point, 1 + Math.floor(random() * random() * 3), random() < 0.4 ? 1 : 0]);
    }
  }
  const search = components(rows, { '⺡': '氵' });

  it('find characters by their components as the D1 search found them', async () => {
    const { env, sql } = search;
    for (const asked of [['口', '木'], ['一', '口'], ['木', '木'], ['一', '水', '𠂉'], ['口', '木', '水', '𠂉']]) {
      const wanted = new Map<string, number>();
      for (const c of asked) wanted.set(c, (wanted.get(c) ?? 0) + 1);
      for (const limit of [8, 48]) expect(await componentMatches(env, wanted, limit)).toEqual(sql(wanted, limit));
    }
    expect(sql(new Map([['一', 1], ['口', 1]]), 8).more).toBe(true);
    expect(sql(new Map([['口', 1], ['木', 1], ['水', 1]]), 48).codes.length).toBeGreaterThan(8);
  });

  it('read a typed form as its component and find nothing for a component no name gives', async () => {
    const { env } = search;
    expect(await componentMatches(env, new Map([['⺡', 1], ['骨', 1]]), 8)).toEqual({ codes: ['U+6ED1'], more: false });
    expect(await componentMatches(env, new Map([['⺡', 1], ['月', 1]]), 8)).toEqual({ codes: [], more: false });
  });
});
