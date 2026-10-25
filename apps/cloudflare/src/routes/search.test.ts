import { Database } from 'bun:sqlite';
import { afterAll, describe, expect, it } from 'bun:test';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { IDS_DIGITS, SUMMARY_DIGITS, componentMatches, referenceShard } from '../atlas3Reference';
import { choiceSpellingsQuery, headsQuery, namesQuery, structureOf, substitutesQuery, suggest, wordChoices, wordChoicesQuery } from './search';

// The edge cache.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
  delete: async (request: Request) => edge.delete(request.url),
} };

const databases: Database[] = [];
afterAll(() => { for (const db of databases.splice(0)) db.close(); });
function migrated(folder: string) {
  const db = new Database(':memory:'); databases.push(db);
  const migrations = new URL(`../../${folder}/`, import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
// The part of D1 the routes use, over SQLite, counting statements.
function d1(db: Database, statements: string[] = []) {
  return { prepare: (sql: string) => {
    const bound = (values: unknown[]) => ({
      all: async () => { statements.push(sql); return { results: db.query(sql).all(...(values as never[])) }; },
      first: async () => { statements.push(sql); return db.query(sql).get(...(values as never[])) ?? null; },
    });
    return { ...bound([]), bind: (...values: unknown[]) => bound(values) };
  } } as unknown as D1Database;
}
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const points = (code: string) => code.split(' ').map(p => parseInt(p.slice(2), 16));
const byPoints = (a: string, b: string) => {
  const x = points(a), y = points(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i] - y[i];
  return x.length - y.length;
};

// ---- One universe of reference data, as reference.py writes the layout. The isolate holds every reference object a test file reads, so this file reads one set,
// apart from the objects atlas3Reference.test.ts reads (checked below).
type Card = { char: string; occurrences: number; name?: string; family?: string[]; ligature?: string };
const cards: Card[] = [
  { char: 'ね', occurrences: 50, name: 'HIRAGANA LETTER NE', family: ['ね', 'ネ', '𛂒', '𛄧'] },
  { char: 'ネ', occurrences: 40, name: 'KATAKANA LETTER NE', family: ['ね', 'ネ', '𛂒', '𛄧'] },
  { char: '𛂒', occurrences: 30, name: 'HENTAIGANA LETTER NE-1', family: ['ね', 'ネ', '𛂒', '𛄧'] },
  { char: '𛄧', occurrences: 20, name: 'HENTAIGANA LETTER NE-5', family: ['ね', 'ネ', '𛂒', '𛄧'] },
  { char: 'ぬ', occurrences: 5, name: 'HIRAGANA LETTER NU' },
  { char: 'と', occurrences: 70, name: 'HIRAGANA LETTER TO' },
  { char: 'き', occurrences: 60, name: 'HIRAGANA LETTER KI' },
  { char: '𛄤', occurrences: 3, name: 'HENTAIGANA LETTER TO-KI' },
  { char: '時', occurrences: 900, family: ['時', '时', '旹'] },
  { char: '时', occurrences: 4, family: ['時', '时', '旹'] },
  { char: '旹', occurrences: 2, family: ['時', '时', '旹'] },
  { char: '刻', occurrences: 80 },
  { char: '鴇', occurrences: 1 },
  { char: '鯡', occurrences: 7 }, { char: '𩹉', occurrences: 2 },
  { char: '鮓', occurrences: 9 }, { char: '鮺', occurrences: 3 }, { char: '鮨', occurrences: 3 }, { char: '蚱', occurrences: 1 },
  { char: 'か゚', occurrences: 6, name: '' }, { char: 'か', occurrences: 300, name: 'HIRAGANA LETTER KA' },
  { char: 'ア', occurrences: 10, name: 'KATAKANA LETTER A' }, { char: 'ア゙', occurrences: 0, name: '' },
  { char: '候', occurrences: 50 }, { char: '滑', occurrences: 12 }, { char: '骨', occurrences: 30 }, { char: '月', occurrences: 400 },
  { char: '﨑', occurrences: 2, name: 'CJK COMPATIBILITY IDEOGRAPH-FA11' },
  { char: '𠀀', occurrences: 0 },
  { char: 'ﾈ', occurrences: 0, name: 'HALFWIDTH KATAKANA LETTER NE', ligature: 'ﾈ' },
];
// Every seventh character of the component spread has a card, most frequent first by its place.
for (let point = 0x4E00; point < 0x4E00 + 5000; point += 7)
  if (!cards.some(card => card.char === String.fromCodePoint(point))) cards.push({ char: String.fromCodePoint(point), occurrences: (point * 31) % 97 });
for (const card of cards) if (card.name === undefined) {
  const point = card.char.codePointAt(0)!;
  card.name = `CJK UNIFIED IDEOGRAPH-${point.toString(16).toUpperCase()}`;
}
cards.sort((a, b) => byPoints(cp(a.char), cp(b.char)));
const dataOf = (card: Card) => {
  const members = card.family ?? [card.char], head = members[0];
  return { code_point: cp(card.char), char: card.char, name: card.name, occurrence_count: card.occurrences, ligature: card.ligature ?? null,
    grapheme: { code_point: cp(head), char: head, label: members.join(' = '), character_count: members.length,
      members: members.map(c => ({ code_point: cp(c), char: c })) } };
};
const aliases: [string, string, number][] = [];
for (const card of cards) aliases.push([card.char, cp(card.char), 0], [cp(card.char).toLowerCase(), cp(card.char), 0]);
aliases.push(['ね', cp('𛂒'), 4], ['ね', cp('ぬ'), 4], ['ね', cp('ネ'), 1], ['ね', cp('𛄧'), 4], ['とき', cp('𛄤'), 4],
  ['時', cp('时'), 2], ['じ', cp('时'), 5], ['ne', cp('ね'), 6], ['ne', cp('ネ'), 6]);
// [a, b, relation, source, widens]
const edges: [string, string, string, string, number][] = [
  ['鯡', '𩹉', 'variant', 'cjkvi-variants', 1], ['鮓', '鮺', 'variant', 'cjkvi-variants', 1], ['鮺', '鮓', 'variant', 'wikidata', 1],
  ['蚱', '鮓', 'borrowed', 'cjkvi-variants', 0], ['鮓', '𩼮', 'variant', 'wikidata', 1], ['鮓', '鮨', 'variant', 'cjkvi-variants', 1],
  ['鮓', '鮨', 'simplified', 'unihan', 0], ['時', '旹', 'variant', 'unihan', 1], ['時', '刻', 'variant', 'other', 1], ['時', '鴇', 'variant', 'other', 1],
  ['時', '鴇', 'variant', 'third', 1],
];
const readings = { 'とき': ['時', '刻', '鴇', '朱鷺'], 'ね': ['子', '音', '値'], 'じ': ['時', '事'], 'こく': ['刻', '黒'], 'そうろう': ['候'] };
const structures: [string, string[], { text: string; regions: string }[] | null][] = [
  ['滑', ['⿰氵骨'], [{ text: '⿰氵骨', regions: 'GTJ' }, { text: '⿰氵⿵冖月', regions: 'K' }]],
  ['骨', ['⿱⿵冂⿰丨𠃌月'], null],
];
const substitutions: [string, string, number][] = [['氵', '水', 40], ['月', '⺼', 12], ['骨', '𩨗', 3], ['冂', '月', 2]];

// The component spread of atlas3Reference.test.ts's component tests: five components over the
// spread of characters, 一 past the component scan limit, and 滑 with its parts.
type Row = [string, number, number, number, number, number];
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
const W = 'ja/ばかり/副助詞', OTHER = 'ja/のみ/副助詞';


// The new layout: graphemes numbered in code point order with their family heads, the single code
// point names, 0004's tables, and the R2 objects.
const atlasDb = migrated('migrations-atlas');
const ids = new Map(cards.map((card, i) => [card.char, i + 1]));
for (const card of cards) {
  const head = (card.family ?? [card.char])[0];
  atlasDb.query('INSERT INTO graphemes(id,text,head) VALUES(?,?,?)').run(ids.get(card.char)!, card.char, head === card.char ? null : ids.get(head)!);
}
for (const [query, code, rank] of aliases) if (!code.includes(' '))
  atlasDb.query('INSERT OR IGNORE INTO names(text,code_point,rank) VALUES(?,?,?)').run(query, parseInt(code.slice(2), 16), rank);
for (const [a, b, count] of substitutions) atlasDb.query('INSERT INTO component_variants(a,b,count) VALUES(?,?,?)').run(a, b, count);

const objects: Record<string, unknown> = {};
const summaries: Record<string, Record<string, Record<string, unknown>>> = {};
for (const card of cards) {
  const head = cp((card.family ?? [card.char])[0]), shard = await referenceShard(head, SUMMARY_DIGITS);
  ((summaries[shard] ??= {})[head] ??= {})[cp(card.char)] = dataOf(card);
}
for (const [shard, families] of Object.entries(summaries)) objects[`v3/reference/summary/${shard}.json.gz`] = families;
// names.json.gz as reference.names_list writes it.
const named: [string, string, number][] = [], plain = new Map<string, [string, number[], number[], number]>();
for (const card of cards) {
  const code = cp(card.char), found = /^(.*IDEOGRAPH-)([0-9A-F]{4,6})$/.exec(card.name ?? '');
  const point = card.char.codePointAt(0)!;
  if (!code.includes(' ') && found && parseInt(found[2], 16) === point && found[2].length === point.toString(16).toUpperCase().padStart(4, '0').length) {
    const group = plain.get(found[1]) ?? [found[1], [], [], 0];
    group[1].push(point - group[3]); group[2].push(card.occurrences); group[3] = point;
    plain.set(found[1], group);
  } else named.push([code, card.name ?? '', card.occurrences]);
}
objects['v3/reference/names.json.gz'] = { names: named, plain: [...plain.values()].map(g => g.slice(0, 3)) };
// Each card's object with its edges from both ends, in the order reference.py writes them.
const sortedEdges = [...edges].sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0) || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0)
  || (x[2] < y[2] ? -1 : x[2] > y[2] ? 1 : 0) || (x[3] < y[3] ? -1 : x[3] > y[3] ? 1 : 0));
// The families of the cards of `chars`, each family whole, as reference.families_text writes them.
const familiesOf = (chars: string[]) => {
  const families: Record<string, Record<string, unknown>> = {};
  for (const char of chars) {
    const card = cards.find(c => c.char === char);
    if (!card) continue;
    const head = (card.family ?? [card.char])[0];
    families[cp(head)] ??= Object.fromEntries(cards.filter(c => (c.family ?? [c.char])[0] === head).map(c => [cp(c.char), dataOf(c)]));
  }
  return families;
};
for (const card of cards) {
  const variants = sortedEdges.flatMap(([a, b, relation, source, widens]) => [
    ...(a === card.char ? [[b, relation, source, '', 1, widens]] : []), ...(b === card.char ? [[a, relation, source, '', 1, widens]] : [])]);
  objects[`v3/reference/char/${cp(card.char).split(' ').join('-')}.json.gz`] = { grapheme: ids.get(card.char), data: dataOf(card), detail: {},
    variants, variant_families: familiesOf(variants.map(v => v[0] as string)), derived: [], substitutions: [], writes: [], writers: [], ids: {} };
}
for (const [reading, kanji] of Object.entries(readings)) {
  const name = `v3/reference/readings/${await referenceShard(reading, SUMMARY_DIGITS)}.json.gz`;
  objects[name] = { ...(objects[name] as object ?? {}), [reading]: { kanji, families: familiesOf(kanji.slice(0, 49)) } };
}
for (const [char, sequences, shapes] of structures) {
  const name = `v3/reference/ids/${await referenceShard(char, IDS_DIGITS)}.json.gz`;
  objects[name] = { ...(objects[name] as object ?? {}), [char]: [sequences, shapes] };
}
// Component lists as reference.component_list writes them.
const byComponent = new Map<string, Row[]>();
for (const row of rows) byComponent.set(row[0], [...(byComponent.get(row[0]) ?? []), row]);
objects['v3/reference/components/names.json.gz'] = { same: [...byComponent.keys()].join(''), other: { '⺡': '氵' } };
for (const [c, list] of byComponent) {
  const groups: [number, number, number[], number[]][] = [];
  let last = 0;
  for (const [, tier, size, point, n, direct] of [...list].sort((a, b) => a[1] - b[1] || a[2] - b[2] || a[3] - b[3])) {
    const group = groups.at(-1);
    if (group && group[0] === tier && group[1] === size) { group[2].push(point - last); group[3].push(n * 2 + direct); }
    else groups.push([tier, size, [point], [n * 2 + direct]]);
    last = point;
  }
  objects[`v3/reference/components/${cp(c)}.json.gz`] = { total: list.length, groups };
}

const reads: string[] = [];
const MEDIA = { get: async (name: string) => {
  reads.push(name);
  const value = objects[name];
  return value === undefined ? null : { arrayBuffer: async () => new Uint8Array(gzipSync(JSON.stringify(value))).buffer };
} };
const statements: string[] = [];
const newEnv = { ATLAS: d1(atlasDb, statements), MEDIA, CATALOGUE_LAG: '0' } as unknown as Env;
const ctx = { waitUntil: () => {}, passThroughOnException: () => {} } as unknown as ExecutionContext;
const ask = (q: string, limit?: number) => new URLSearchParams(limit === undefined ? { q } : { q, limit: String(limit) });

describe('the search fixture', () => {
  it('reads no object atlas3Reference.test.ts reads with other contents', async () => {
    const theirs = new Set([await referenceShard('U+3044', SUMMARY_DIGITS), await referenceShard('U+3046', SUMMARY_DIGITS)]);
    for (const shard of Object.keys(summaries)) expect(theirs.has(shard)).toBe(false);
    for (const [char] of structures) expect(await referenceShard(char, IDS_DIGITS)).not.toBe(await referenceShard('姥', IDS_DIGITS));
    expect(objects['v3/reference/char/U+3042-U+3099.json.gz']).toBeUndefined();
  });
});

describe('a character search', () => {
  // First in the file, so the isolate holds none of the objects yet.
  it('reads the name list, the summary shards of the families the query names, and one object for its kanji or 異体字', async () => {
    const summary = async (chars: string[]) => new Set(await Promise.all(chars.map(async c => `v3/reference/summary/${await referenceShard(cp(c), SUMMARY_DIGITS)}.json.gz`)));
    reads.length = 0;
    await suggest(newEnv, ask('とき', 8), ctx);
    expect(reads.filter(r => !r.includes('/summary/'))).toEqual(['v3/reference/names.json.gz',
      `v3/reference/readings/${await referenceShard('とき', SUMMARY_DIGITS)}.json.gz`]);
    expect(new Set(reads.filter(r => r.includes('/summary/')))).toEqual(await summary(['𛄤']));
    reads.length = 0;
    await suggest(newEnv, ask('鮓', 48), ctx);
    expect(reads.filter(r => !r.includes('/summary/'))).toEqual([`v3/reference/char/${cp('鮓')}.json.gz`]);
    expect(new Set(reads.filter(r => r.includes('/summary/')))).toEqual(await summary(['鮓']));
  });

  it('refuses a limit past 48', async () => {
    await expect(suggest(newEnv, ask('ね', 49), ctx)).rejects.toThrow('Invalid limit.');
  });

  it('lists each grapheme with all its forms, the forms the query found first', async () => {
    const chars = async (q: string) => (await suggest(newEnv, ask(q, 8), ctx)).items.map((item: { char: string }) => item.char);
    expect(await chars('𛄧')).toEqual(['𛄧', 'ね', 'ネ', '𛂒']);
    expect(await chars('ね')).toEqual(['ね', 'ネ', '𛂒', '𛄧', 'ぬ']);
    expect(await chars('とき')).toEqual(['𛄤', '時', '时', '旹', '刻', '鴇']);
    expect(await chars('鯡')).toEqual(['鯡', '𩹉']);
  });

  it('finds the kanji of a reading typed in historical kana by its modern spelling', async () => {
    const chars = async (q: string) => (await suggest(newEnv, ask(q, 8), ctx)).items.map((item: { char: string }) => item.char);
    expect(await chars('そうろう')).toEqual(['候']);
    expect(await chars('さふらふ')).toEqual(['候']);
    expect(await chars('サウラフ')).toEqual(['候']);
  });

  it('asks D1 for the names and the family heads only, each by key', async () => {
    statements.length = 0;
    await suggest(newEnv, ask('とき', 8), ctx);
    expect(statements).toEqual([namesQuery(), headsQuery()]);
    statements.length = 0;
    // A list of kanji alone has their cards from the reading's shard: no family head is asked for.
    expect((await suggest(newEnv, ask('こく', 8), ctx)).items.map((item: { char: string }) => item.char)).toEqual(['刻']);
    expect(statements).toEqual([namesQuery()]);
    const plan = (sql: string, ...args: unknown[]) => (atlasDb.query('EXPLAIN QUERY PLAN ' + sql).all(...(args as never[])) as { detail: string }[]).map(r => r.detail);
    expect(plan(namesQuery(), 'a', 'b', 'c')).toEqual(['SEARCH names USING PRIMARY KEY (text=?)', 'USE TEMP B-TREE FOR GROUP BY']);
    const heads = plan(headsQuery(), JSON.stringify(['時']));
    expect(heads.filter(d => d.startsWith('SCAN') && !d.includes('json_each'))).toEqual([]);
    expect(heads).toContain('SEARCH g USING INDEX sqlite_autoindex_graphemes_1 (text=?)');
    expect(heads).toContain('SEARCH h USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN');
  });
});

describe('a component search', () => {
  it('reads a typed form as its component and finds nothing for a component no name gives', async () => {
    expect(await componentMatches(newEnv, new Map([['⺡', 1], ['骨', 1]]), 8)).toEqual({ codes: ['U+6ED1'], more: false });
    expect(await componentMatches(newEnv, new Map([['⺡', 1], ['月', 1]]), 8)).toEqual({ codes: [], more: false });
  });
});

describe("the IDS editor's structure", () => {
  it('answers with its sequences, shapes and substitutes, kept at the edge by the catalogue version', async () => {
    expect(await structureOf(newEnv, ctx, 'https://new.test', '滑')).toEqual({ char: '滑', sequences: ['⿰氵骨'],
      shapes: [{ text: '⿰氵骨', regions: 'GTJ' }, { text: '⿰氵⿵冖月', regions: 'K' }],
      substitutes: { '氵': [{ char: '水', count: 40 }], '骨': [{ char: '𩨗', count: 3 }] } });
    expect([...edge.keys()].some(key => key.startsWith('https://new.test/layers/structure?c=%E6%BB%91&v='))).toBe(true);
    await expect(structureOf(newEnv, ctx, 'https://new.test', '')).rejects.toThrow('Name one character.');
    await expect(structureOf(newEnv, ctx, 'https://new.test', '滑骨')).rejects.toThrow('Name one character.');
  });

  it('reads the substitutes by either end of the pair', () => {
    const plan = (atlasDb.query('EXPLAIN QUERY PLAN ' + substitutesQuery(2)).all('氵', '骨', '氵', '骨') as { detail: string }[]).map(r => r.detail);
    expect(plan.filter(d => d.startsWith('SCAN'))).toEqual([]);
    expect(plan.some(d => /SEARCH component_variants USING (COVERING )?INDEX component_variant_b/.test(d))).toBe(true);
    expect(plan.some(d => /SEARCH component_variants USING PRIMARY KEY \(a=\?\)/.test(d))).toBe(true);
  });
});

describe('word choices', () => {
  const db = migrated('migrations-atlas');
  db.exec(`INSERT INTO words VALUES('${W}','ja','ばかり','副助詞'),('${OTHER}','ja','のみ','副助詞');`);
  const spelling = (word: string, char: string, source = 'dictionary') => db.query(`INSERT INTO word_spellings
    VALUES(?,?,?,'entry','attested','source','','','',NULL,NULL)`).run(word, char, source);
  const env = { ATLAS: d1(db) } as unknown as Env;

  it('offers the crop spellings first, narrows them to a typed reading, folds katakana, and lists distinct spellings', async () => {
    spelling(W, '計'); spelling(W, '計', 'other'); spelling(W, '許'); spelling(OTHER, '而已');
    expect(await wordChoices(env, '', ['計'])).toEqual([{ id: W, reading: 'ばかり', class: '副助詞', spellings: ['計', '許'] }]);
    expect(await wordChoices(env, 'ノ', ['計'])).toEqual([{ id: OTHER, reading: 'のみ', class: '副助詞', spellings: ['而已'] }]);
    expect((await wordChoices(env, 'バ', ['許', '許'])).map(r => r.id)).toEqual([W]);
    expect(await wordChoices(env, '', [])).toEqual([]);
    expect(await wordChoices(env, '%', [])).toEqual([]);
    expect(await wordChoices(env, '\u{10ffff}', [])).toEqual([]);
    expect(await wordChoices(env, '', ['a', 'b', 'c', 'd', '計'])).toEqual([]);
  });

  it('limits choices to twenty, keeping words spelled by the crop first', async () => {
    for (let i = 0; i < 25; i++) { db.query("INSERT INTO words VALUES(?,'ja',?,'名詞')").run('ja/あ' + i + '/名詞', 'あ' + i); spelling('ja/あ' + i + '/名詞', i % 2 ? '計' : '許') }
    const found = await wordChoices(env, 'ア', ['計']);
    expect(found).toHaveLength(20);
    expect(found.slice(0, 12).every(r => r.spellings.includes('計'))).toBe(true);
    // A crop spelling twenty words or more still finds the one a reading names.
    expect((await wordChoices(env, 'あ24', ['計', '許'])).map(r => r.id)).toEqual(['ja/あ24/名詞']);
  });

  it('uses keys and the spelling index without scanning', () => {
    const plans = [
      [wordChoicesQuery('spelling'), [JSON.stringify(['計'])]], [wordChoicesQuery('both'), [JSON.stringify(['計']), 'ja/ば', 'ja/ぱ']],
      [wordChoicesQuery('reading'), ['ja/ば', 'ja/ぱ']], [choiceSpellingsQuery(), [JSON.stringify([W])]],
    ] as [string, string[]][];
    const details = plans.map(([sql, args]) => (db.query('EXPLAIN QUERY PLAN ' + sql).all(...args) as { detail: string }[]).map(r => r.detail));
    for (const plan of details) expect(plan.filter(d => /^SCAN (words|word_spellings)\b/.test(d))).toEqual([]);
    expect(details[0].some(d => /SEARCH word_spellings USING COVERING INDEX word_spelling_spelling/.test(d))).toBe(true);
    expect(details[1].some(d => /SEARCH word_spellings USING COVERING INDEX word_spelling_spelling/.test(d))).toBe(true);
    expect(details[2]).toContain('SEARCH words USING PRIMARY KEY (id>? AND id<?)');
  });
});
