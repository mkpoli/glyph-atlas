// The browse routes' counts against the old database's: the fixture catalogue is written into the old
// schema (apps/cloudflare/migrations) as the old publication wrote it, and the old routes' statements,
// copied here as they stood before the cutover, answer beside the new routes.
import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { tallyDates } from '../dating';
import { catalogue, corpusCharacters, dateStats } from './browse';
import { ctx, databases, setup } from './browse.fixture.test';

const edge = new Map<string, Response>();
beforeEach(() => {
  edge.clear();
  (globalThis as any).caches = { default: {
    match: async (request: Request) => edge.get(request.url)?.clone(),
    put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
  } };
});
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
type Facet = { label: string | null; family: string | null; document: string | null; title: string | null; state: string; n: number };

// ---- The old routes' statements (apps/cloudflare/src/index.ts and dating.ts before the cutover).
function material(scope: string, column: string): [string, string[]] {
  if (scope === 'all') return ['1=1', []];
  const negated = scope.startsWith('not:'), node = negated ? scope.slice(4) : scope;
  const test = `${column}>=? AND ${column}<?`;
  return [negated ? `NOT (${test})` : `(${test})`, [node, node + '0']];
}
function listingFilter(review: boolean, production: string, withheld = false) {
  const [materials, materialValues] = material(production, 'production');
  return { where: ["origin='local'", ...(review ? ['quiz=1', "character<>''"] : withheld ? [] : ['+quiz=1']), materials], values: materialValues };
}
function facetsQueries(review: boolean, where: string[]) {
  const filter = where.join(' AND '), book = review ? 'NULL' : 'document';
  const stored = review
    ? `SELECT nullif(character,'') AS label,nullif(max(family),'') AS family,NULL AS document,NULL AS title,state,sum(n) AS n
    FROM unit_counts WHERE ${filter} GROUP BY character,state`
    : `SELECT nullif(character,'') AS label,nullif(max(family),'') AS family,nullif(document,'') AS document,nullif(max(title),'') AS title,state,sum(n) AS n
    FROM unit_counts WHERE ${filter} GROUP BY character,document,state`;
  const marked = `SELECT character AS label,${book} AS document,m.mark AS state,count(*) AS n
    FROM unit_marks m CROSS JOIN units ON units.id=m.id WHERE ${filter} AND state='pending' GROUP BY 1,2,3`;
  return { stored, marked };
}
function moved(stored: Facet[], moves: Facet[]): Facet[] {
  const key = (row: { label: string | null; document: string | null }, state: string) => JSON.stringify([row.label, row.document, state]);
  const rows = new Map(stored.map(row => [key(row, row.state), { ...row }]));
  for (const move of moves) {
    const pending = rows.get(key(move, 'pending'));
    const n = Math.min(move.n, pending?.n ?? 0);
    if (!pending || !n) continue;
    pending.n -= n;
    const target = rows.get(key(move, move.state)) ?? { ...pending, state: move.state, n: 0 };
    target.n += n;
    rows.set(key(move, move.state), target);
  }
  return [...rows.values()].filter(row => row.n > 0);
}
function oldCatalogue(old: Database, review: boolean, production: string, withheld: boolean) {
  const { where, values } = listingFilter(review, production, withheld);
  const { stored, marked } = facetsQueries(review, where);
  const groups = moved(old.query(stored).all(...values) as Facet[], old.query(marked).all(...values) as Facet[]);
  const categories = new Map<string, any>(), documents = new Map<string, any>();
  const counts: any = { pending: 0, seen: 0, flagged: 0, checked: 0, hard: 0, skipped: 0 };
  const empty = { total: 0, pending: 0, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 };
  const add = (label: string | null, state: string, n: number, document: string | null = null, title: string | null = null, family: string | null = null) => {
    counts[state] += n;
    if (label) {
      const category = categories.get(label) || { label, grapheme: family || cp(label), ...empty };
      category.total += n; category[state] += n; categories.set(label, category);
    }
    if (!document) return;
    const book = documents.get(document) || { id: document, title, ...empty };
    book.total += n; book[state] += n; documents.set(document, book);
  };
  for (const row of groups) add(row.label, row.state, row.n, row.document, row.title, row.family);
  if (review) {
    const [materials, mv] = material(production, 'production');
    const families = new Map((old.query(browseCorpusQuery('all').sql).all() as { label: string; family: string | null }[]).map(r => [r.label, r.family || cp(r.label)]));
    for (const row of old.query(`SELECT character AS label,sum(n-named) AS n FROM corpus_characters WHERE ${materials} GROUP BY character`).all(...mv) as { label: string; n: number }[])
      if (row.n > 0) add(row.label, 'pending', row.n, null, null, families.get(row.label) ?? null);
  }
  return { counts,
    categories: [...categories.values()].sort((a, b) => b.total - a.total || a.label.localeCompare(b.label)),
    documents: [...documents.values()].sort((a, b) => b.total - a.total || (a.title ?? '').localeCompare(b.title ?? '') || a.id.localeCompare(b.id)) };
}
function browseCorpusQuery(production: string) {
  const [materials, values] = material(production, 'cc.production');
  return { sql: `SELECT cc.character AS label,sum(cc.n) AS n,json_extract(ch.data,'$.grapheme.code_point') AS family
    FROM corpus_characters cc LEFT JOIN characters ch ON ch.character=cc.character WHERE ${materials} GROUP BY cc.character`, values };
}
const dateStatsQuery = () => `SELECT x.document,x.n,d.kind,d.start,d.end,coalesce(c.start,c.end) AS composed
  FROM (SELECT document,sum(n) AS n FROM unit_counts WHERE origin='local' AND document<>'' GROUP BY document
        UNION ALL SELECT document,n FROM corpus_documents) x
  LEFT JOIN document_dating d ON d.document=x.document AND d.axis='witness'
  LEFT JOIN document_dating c ON c.document=x.document AND c.axis='composed'`;
function withheldCount(old: Database, production: string, named: { character: string | null; document: string | null; family: string | null }) {
  const [materials, values] = material(production, 'production');
  const keys = (['character', 'document', 'family'] as const).filter(key => named[key] !== null);
  return (old.query(`SELECT coalesce(sum(n),0) AS n FROM unit_counts WHERE origin='local' AND quiz=0 AND ${materials}${keys.map(key => ` AND ${key}=?`).join('')}`)
    .get(...values, ...keys.map(key => named[key]!)) as { n: number }).n;
}

// ---- The fixture written as the old publication wrote it: a crop seen or hard to read is a pending
// unit with its mark, a corpus glyph is a `corpus_units` row, and a book's title is its crops' source.
async function both() {
  const fixture = await setup();
  const { db } = fixture;
  const old = new Database(':memory:'); databases.push(old);
  const migrations = new URL('../../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) old.exec(readFileSync(new URL(file, migrations), 'utf8'));
  const rows = db.query(`SELECT c.id,c.kind,g.text AS label,coalesce(h.text,g.text) AS head,h.id IS NOT NULL AS family,p.key AS production,
    k.key AS category,s.key AS state,c.quiz,c.shuffle,b.key AS document,b.title FROM crops c JOIN books b ON b.id=c.book
    LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN graphemes h ON h.id=g.head LEFT JOIN productions p ON p.id=c.production
    LEFT JOIN codes k ON k.field='category' AND k.code=c.category LEFT JOIN codes s ON s.field='state' AND s.code=c.state`).all() as any[];
  const unit = old.prepare(`INSERT INTO units(id,origin,character,family,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document)
    VALUES(?,'local',?,?,?,?,?,0,?,1,?,?,'{}','{}','{}',?)`);
  const glyph = old.prepare(`INSERT INTO corpus_units(id,character,family,shuffle,object,offset,size,production,document,named) VALUES(?,?,?,?,'p',0,1,?,?,?)`);
  for (const r of rows) {
    const family = r.label && r.family ? cp(r.head) : null;
    if (r.kind === 0) {
      const marked = r.state === 'seen' || r.state === 'hard';
      unit.run(`u${r.id}`, r.label, family, r.production, r.category ?? 'other', marked ? 'pending' : r.state, r.quiz, r.shuffle % 268435456,
        JSON.stringify({ label: r.label, source: r.title }), r.document);
      if (marked) old.query('INSERT INTO unit_marks(id,mark) VALUES(?,?)').run(`u${r.id}`, r.state);
    } else {
      // A corpus glyph a review decided has its unit, and is named.
      glyph.run(`g${r.id}`, r.label, family, r.shuffle % 268435456, r.production, r.document, r.state === 'pending' ? 0 : 1);
      if (r.state !== 'pending') old.query(`INSERT INTO units(id,origin,character,family,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
        VALUES(?,'corpus',?,?,?,'other',?,1,1,1,?,'{}','{}','{}','{}')`).run(`g${r.id}`, r.label, family, r.production, r.state, r.shuffle % 268435456);
    }
  }
  old.exec(`INSERT INTO corpus_characters SELECT character,production,count(*),sum(named) FROM corpus_units WHERE character IS NOT NULL GROUP BY 1,2;
    INSERT INTO corpus_documents(document,n,title) SELECT document,count(*),NULL FROM corpus_units GROUP BY 1`);
  for (const r of db.query(`SELECT g.text, coalesce(h.text, g.text) AS head FROM graphemes g LEFT JOIN graphemes h ON h.id=g.head WHERE g.head IS NOT NULL`).all() as any[])
    old.query(`INSERT INTO characters(code_point,character,name,data,detail) VALUES(?,?,'',?,'{}')`).run(cp(r.text), r.text, JSON.stringify({ grapheme: { code_point: cp(r.head) } }));
  for (const r of db.query(`SELECT b.key,d.* FROM book_dates d JOIN books b ON b.id=d.book`).all() as any[])
    old.query(`INSERT INTO document_dating(document,axis,kind,start,end,precision,text,label,status,claims,calendar,source,resolver,export)
      VALUES(?,?,?,?,?,?,?,?,?,'[]',?,?,'r','e')`).run(r.key, r.axis, r.kind, r.start, r.end, r.precision, r.text, r.label, r.status, r.calendar, r.source);
  return { ...fixture, old };
}

describe('the browse routes answer as the old database did', () => {
  it('count the browse page by character, book and state, in every material, withheld or not', async () => {
    const { env, old } = await both();
    for (const production of ['all', 'printed', 'printed/type', 'not:printed/type', 'handwritten'])
      for (const withheld of [false, true]) {
        const body = await catalogue(env, ctx, new URL(`https://glyphatlas.org/atlas?limit=0&production=${encodeURIComponent(production)}${withheld ? '&withheld=show' : ''}`), null);
        const was = oldCatalogue(old, false, production, withheld);
        expect({ counts: body.counts, categories: body.categories, documents: body.documents }).toEqual(was);
        if (!withheld) expect(body.withheld_count).toBe(withheldCount(old, production, { character: null, document: null, family: null }));
      }
    for (const [query, named] of [['character=は', { character: 'は', document: null, family: null }], ['document=hk:b', { character: null, document: 'hk:b', family: null }],
      ['grapheme=U%2B306F', { character: null, document: null, family: 'U+306F' }]] as const)
      expect((await catalogue(env, ctx, new URL(`https://glyphatlas.org/atlas?limit=0&${query}`), null)).withheld_count).toBe(withheldCount(old, 'all', named));
  });

  it('count a round’s characters with their corpus glyphs', async () => {
    const { env, old } = await both();
    for (const production of ['all', 'not:printed/type', 'handwritten']) {
      const body = await catalogue(env, ctx, new URL(`https://glyphatlas.org/atlas?purpose=review&limit=0&state=pending&production=${encodeURIComponent(production)}`), null);
      expect({ counts: body.counts, categories: body.categories, documents: body.documents }).toEqual(oldCatalogue(old, true, production, false));
    }
  });

  it('count the corpus glyphs by character', async () => {
    const { env, old } = await both();
    for (const production of ['all', 'printed', 'not:printed/type', 'handwritten']) {
      const { sql, values } = browseCorpusQuery(production);
      const rows = old.query(sql).all(...values) as { label: string; n: number; family: string | null }[];
      const was = rows.filter(row => row.label && row.n > 0).map(row => [row.label, row.family || cp(row.label), row.n]);
      expect((await corpusCharacters(env, ctx, new URL(`https://glyphatlas.org/atlas/corpus/characters?production=${encodeURIComponent(production)}`))).items).toEqual(was);
    }
  });

  it('count the collection’s dates', async () => {
    const { env, old } = await both();
    expect(await dateStats(env, ctx, new URL('https://glyphatlas.org/atlas/dates/stats'))).toEqual(tallyDates(old.query(dateStatsQuery()).all() as any[]));
  });
});
