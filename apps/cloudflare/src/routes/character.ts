// Character pages on the new storage layout: the card and its 異体字 from the character's reference
// object (`atlas3Reference.ts`), the galleries of its crops and corpus glyphs from index ranges
// (`atlas3Gallery.ts`), their counts from the publication's answers, and its decades and time axis.
//
// A crop joins the family of its character (`graphemes.head`); a crop with no character joins the
// family its source names (`crops.family`, migration 0012), which every crop keeps. A family's gallery reads one range
// per member character and one per member's filed crops, merged in the order they show.
import { cropOf, publicId } from '../atlas3';
import { STYLE_GROUPS, STYLE_NAMES, characterCounts, composedBooks, cursorOf, filedCounts, galleryPage, groupOf, phasesOf, rangeCountQuery,
  type Cell, type GalleryQuery, type Phase, type Source, type StyleGroup } from '../atlas3Gallery';
import { listingItems } from '../atlas3Listing';
import { cardList, characterObject, type CharacterObject } from '../atlas3Reference';
import { catalogueStamps, catalogueVersion, publicationKey } from '../atlas3Stamps';
import { Problem } from '../busy';
import { idsCharacter } from '../category';
import { CITE_TTL, CITE_TYPES, FORMATS, bibtex, characterEntry, csl, hayagriva, type Day, type Format } from '../citation';
import { yearOptions, type YearOptions } from '../dating';

type Json = any;
export type CharacterTools = { literal: (value: string) => string; jamoSyllable: (value: string) => boolean };

const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
const fail = (status: number, message: string): never => { throw new Problem(status, message) };
// Answers that change only with the catalogue are kept at the edge an hour.
const KEPT = 3600;
async function edge<T>(ctx: ExecutionContext, key: Request, build: () => Promise<T>): Promise<T> {
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as T;
  const body = await build();
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${KEPT}` } })));
  return body;
}
// A value a publication states about the whole site (`site`, migration 0005).
async function siteValue(env: Env, key: string): Promise<Json> {
  const row = await env.ATLAS.prepare('SELECT value FROM site WHERE key=?').bind(key).first<{ value: string }>();
  return row ? JSON.parse(row.value) : null;
}

// Cards.

export function jamoCard(value: string) {
  const code_point = cp(value), url = '/layers/characters/' + code_point;
  const self = { code_point, char: value, name: null, script: 'hangul' };
  const grapheme = { ...self, label: value, members: [{ code_point, char: value }], character_count: 1, relation: 'self',
    evidence: [], url: '/layers/graphemes/' + code_point, is_self: true, occurrence_count: 0 };
  const data = { ...self, age: null, block: 'Hangul Jamo', readings: [], reading: null, jibo: [], ligature: null, occurrence_count: 0,
    url, grapheme, default_scope: 'character', kind: 'hangul',
    candidates: { status: 'ok', known: false, glyphs: 0, lines: 0, pages: 0, total: 0, sources: [],
      counts_kind: 'source_transcription_classes', requires_family_scope: false, source_glyphs: 0, family_glyphs: 0, imported: 0 } };
  const detail = { ...data, alias: null, category: 'Lo', confusables: [], characters: [data], derived: [], expansions: [],
    visual_analysis: { status: 'not_analyzed', family: code_point, model_revision: null, sample_count: 0, assigned_count: 0,
      unassigned_count: 0, groups: [] } };
  return { data, detail };
}

/** The card of a character Unicode lacks, written as an ideographic description sequence: it has no
 * entry in the character table and is its own grapheme, in the shape the table's cards have. */
export function describedCard(value: string) {
  const code_point = cp(value), url = '/layers/characters/' + code_point;
  const self = { code_point, char: value, name: null, script: 'han' };
  const grapheme = { ...self, label: value, members: [{ code_point, char: value }], character_count: 1, relation: 'self',
    evidence: [], url: '/layers/graphemes/' + code_point, is_self: true, occurrence_count: 0 };
  const data = { ...self, age: null, block: null, readings: [], reading: null, jibo: [], ligature: null, occurrence_count: 0,
    url, grapheme, default_scope: 'character', kind: 'han',
    candidates: { status: 'ok', known: false, glyphs: 0, lines: 0, pages: 0, total: 0, sources: [],
      counts_kind: 'source_transcription_classes', requires_family_scope: false, source_glyphs: 0, family_glyphs: 0, imported: 0 } };
  const detail = { ...data, alias: null, category: null, confusables: [], characters: [data], derived: [], expansions: [],
    visual_analysis: { status: 'not_analyzed', family: code_point, model_revision: null, sample_count: 0, assigned_count: 0,
      unassigned_count: 0, groups: [] } };
  return { data, detail };
}

export type Card = { data: Json; detail: Json; object: CharacterObject | null };
// A visual group's code and each sample's crop, as the reference object holds them, by the names a page
// reads: the group's key, and each sample's public id with the address of its image.
export const groupKeysQuery = () => `SELECT code,key FROM codes WHERE field='visual_group' AND code IN (SELECT value FROM json_each(?))`;
async function namedGroups(env: Env, detail: Json): Promise<Json> {
  const groups: Json[] = detail?.visual_analysis?.groups ?? [];
  if (!groups.length) return detail;
  const codes = groups.map(g => g.code).filter(code => code != null);
  const keys = new Map(codes.length ? (await env.ATLAS.prepare(groupKeysQuery()).bind(JSON.stringify(codes)).all<{ code: number; key: string }>())
    .results.map(r => [r.code, r.key]) : []);
  const sample = (imaged: boolean) => ({ crop, ...rest }: Json) => {
    const id = publicId(crop);
    return { id, ...(imaged ? { image: `/layers/visual-groups/samples/${id}/image` } : {}), ...rest };
  };
  const named = groups.map(({ code, ...group }) => ({ ...(code == null ? {} : { id: keys.get(code) ?? String(code) }), ...group,
    ...(group.representatives ? { representatives: group.representatives.map(sample(true)) } : {}),
    ...(group.anchors ? { anchors: group.anchors.map(sample(false)) } : {}) }));
  return { ...detail, visual_analysis: { ...detail.visual_analysis, groups: named } };
}
/** A character's card, by code point or the character itself; 404 when no card has it and it is
 *  neither a description sequence nor an old Hangul syllable. One reference object. */
export async function characterCard(env: Env, value: string, tools: CharacterTools, ctx?: ExecutionContext): Promise<Card> {
  const text = tools.literal(value);
  const object = text ? await characterObject(env, cp(text), ctx) : null;
  if (object) return { data: object.data, detail: await namedGroups(env, object.detail), object };
  if (idsCharacter(text)) return { ...describedCard(text), object: null };
  if (tools.jamoSyllable(text)) return { ...jamoCard(text), object: null };
  throw new Problem(404, 'Character not found.');
}
const gid = (card: Card, char: string): number | null => card.object && Object.hasOwn(card.object.ids, char) ? card.object.ids[char] : null;

// A family's characters by its head, by `grapheme_head`, the head among them; at most FAMILY_MAX.
const FAMILY_MAX = 128;
export const membersQuery = () => `SELECT id,text FROM graphemes WHERE head=? LIMIT ${FAMILY_MAX}`;
async function familyOf(env: Env, card: Card): Promise<{ head: number | null; members: number[] }> {
  const own = gid(card, card.data.char), head = gid(card, card.data.grapheme?.char ?? card.data.char) ?? own;
  if (head === null) return { head: null, members: [] };
  const rows = (await env.ATLAS.prepare(membersQuery()).bind(head).all<{ id: number }>()).results;
  return { head, members: [...new Set([head, ...(own === null ? [] : [own]), ...rows.map(r => r.id)])] };
}

// 異体字.

// Each row of a card's variants lists at most this many, the most attested first; a gallery widens to
// exactly the first row. The Python layer's VARIANTS_SHOWN.
const VARIANTS_SHOWN = 32;
// The tier of predictions, as its own source id: the citation comes from the export's metadata.
const DERIVED_IDS = 'derived-ids';
// A pair any source calls simplified is kept apart even where another lists it as a plain variant
// (refs.KEPT_APART): cjkvi pairs 干 with 乾 and 幹, which four sources give as simplifications.
const KEPT_APART = new Set(['simplified']);
// Strings by code point, as Python compares them; `<` compares UTF-16 units and puts 𠀀 before 﨑.
export const byCodePoint = (a: string, b: string) => {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i].codePointAt(0)! - y[i].codePointAt(0)!;
  return x.length - y.length;
};
type Edge = { other: string; relation: string; source: string; detail: string; written: number; widens: number };
const edgesOf = (object: CharacterObject | null): Edge[] => (object?.variants ?? [])
  .map(([other, relation, source, detail, written, widens]) => ({ other, relation, source, detail, written, widens }));
/** The characters a gallery widened to its variants deals with `char`: the card's first row, in the
 *  card's order (most sources, then code point), at most VARIANTS_SHOWN. */
export function writtenVariants(object: CharacterObject | null, char: string): string[] {
  const by = new Map<string, Edge[]>();
  for (const edge of edgesOf(object)) if (edge.other !== char) by.set(edge.other, [...(by.get(edge.other) ?? []), edge]);
  const widening = [...by].filter(([, edges]) => edges.some(e => e.widens) && !edges.some(e => KEPT_APART.has(e.relation)))
    .map(([other, edges]) => ({ other, sources: new Set(edges.map(e => e.source)).size }))
    .sort((a, b) => b.sources - a.sources || byCodePoint(a.other, b.other)).slice(0, VARIANTS_SHOWN);
  return [char, ...widening.map(v => v.other)];
}
type Substitution = { was: string; became: string; count: number; pairs: { a: string; b: string; sources: string[] }[] };
// A substitution's own spelling, as refs._substitution writes it: the side of fewer code points
// first, then the smaller by code point.
export function substitutionKey(left: string, right: string): [string, string] {
  const l = [...left].map(c => c.codePointAt(0)!), r = [...right].map(c => c.codePointAt(0)!);
  if (l.length !== r.length) return l.length < r.length ? [left, right] : [right, left];
  for (let i = 0; i < l.length; i++) if (l[i] !== r[i]) return l[i] < r[i] ? [left, right] : [right, left];
  return [left, right];
}
// The derived group of one character as the export ranked it, each route's substitutions backed by the
// pairs the object holds for them. A form of one character is encoded, a sequence is not. A route whose
// substitution has no pairs is dropped, and a form left with none is dropped.
function derivedOf(object: CharacterObject) {
  const evidence = new Map(object.substitutions.map(([a, b, count, pairs]) => [`${a}\u0000${b}`, { count, pairs }]));
  const made = (was: string, became: string): Substitution | null => {
    const found = evidence.get(substitutionKey(was, became).join('\u0000'));
    return found ? { was, became, count: found.count, pairs: found.pairs } : null;
  };
  return object.derived.flatMap(([other, routes]) => {
    const kept = routes.map(route => route.map(([was, became]) => made(was, became))).filter((route): route is Substitution[] => route.every(Boolean));
    if (!kept.length) return [];
    const encoded = [...other].length === 1;
    const sources = new Set(kept.flatMap(route => route.flatMap(sub => sub.pairs.flatMap(pair => pair.sources))));
    return [{ char: other, code_point: encoded ? cp(other) : null, encoded, routes: kept, sources: [...sources].sort() }];
  });
}
const sum = (cells: Cell[] | undefined, kind: number) => (cells ?? []).filter(c => c.kind === kind).reduce((n, c) => n + c.n, 0);
/** The characters `char` shares an edge with, as the card lists them: `items` a gallery widens to (a
 *  pair with a widening edge and none that keeps it apart), `related` the rest, each most attested first
 *  (sources, then code point), every edge kept; `written` marks a pair one of whose edges says one may
 *  be written for the other. `writes` are the graphemes besides its own a reduced form of several
 *  characters writes (后: 後), `writers` the forms of that kind that write its family head's grapheme,
 *  each `shared` when it writes something else too; `own` is false for a form with no character of its
 *  own. Each row counts the character's crops here and in the corpus. */
async function variantsOf(env: Env, ctx: ExecutionContext, origin: string, card: Card, version: string) {
  const char = card.detail.char, object = card.object;
  return edge(ctx, new Request(`${origin}/layers/variants?c=${encodeURIComponent(char)}&v=${encodeURIComponent(version)}`), async () => {
    const byChar = new Map<string, { char: string; code_point: string; widens: boolean; written: boolean; relations: Json[]; edges: Edge[] }>();
    for (const e of edgesOf(object)) {
      if (e.other === char) continue;
      const entry = byChar.get(e.other) ?? { char: e.other, code_point: cp(e.other), widens: false, written: false, relations: [], edges: [] };
      entry.edges.push(e);
      entry.relations.push({ relation: e.relation, source: e.source, detail: e.detail });
      byChar.set(e.other, entry);
    }
    const attested = (entry: { relations: Json[] }) => new Set(entry.relations.map(r => r.source)).size;
    const ordered = [...byChar.values()].map(({ edges: pair, ...entry }) => ({ ...entry,
      widens: pair.some(e => e.widens) && !pair.some(e => KEPT_APART.has(e.relation)), written: pair.some(e => e.written) }))
      .sort((a, b) => attested(b) - attested(a) || (a.char.codePointAt(0)! - b.char.codePointAt(0)!));
    const shown = [...ordered.filter(e => e.widens).slice(0, VARIANTS_SHOWN), ...ordered.filter(e => !e.widens).slice(0, VARIANTS_SHOWN)];
    const derived = object ? derivedOf(object) : [];
    const chars = [...shown.map(entry => entry.char), ...derived.filter(entry => entry.encoded).map(entry => entry.char)];
    const ids = new Map(chars.flatMap(c => { const id = card.object && Object.hasOwn(card.object.ids, c) ? card.object.ids[c] : null; return id === null ? [] : [[c, id] as const] }));
    const counts = await characterCounts(env, [...ids.values()]);
    const count = (c: string, kind: number) => ids.has(c) ? sum(counts.get(ids.get(c)!), kind) : 0;
    const cited: Record<string, string> = (await siteValue(env, 'variant_sources')) || {};
    const rows = shown.map(entry => ({ ...entry, sources: [...new Set(entry.relations.map(r => r.source))].sort(),
      count: count(entry.char, 0), corpus_count: count(entry.char, 1) }));
    const predicted = derived.map(entry => ({ ...entry, count: count(entry.char, 0), corpus_count: count(entry.char, 1) }));
    const used = new Set(rows.flatMap(row => row.sources));
    for (const entry of predicted) for (const source of entry.sources) used.add(source);
    if (predicted.length) used.add(DERIVED_IDS);
    const writes = (object?.writes ?? []).map(([c]) => ({ char: c, code_point: cp(c) }));
    const writers = (object?.writers ?? []).map(([c, shared]) => ({ char: c, code_point: cp(c), shared: !!shared }));
    const own = !(object?.writes ?? []).some(([, mine]) => mine === 0);
    return { items: rows.filter(row => row.widens), related: rows.filter(row => !row.widens), derived: predicted,
      total: byChar.size, writes, writers, own, sources: Object.fromEntries([...used].sort().map(source => [source, cited[source] ?? source])) };
  });
}

// Words.

// The spellings of every word a character is cited as writing (decision record 0004): the words by the
// `spelling` index, then each word's rows by the key. The limit bounds a character that writes many words.
export const wordSpellingsQuery = () => `SELECT s.word,w.reading,w.class,s.spelling,s.source,s.locator,s.tier,s.documents,s.occurrences
  FROM word_spellings s JOIN words w ON w.id=s.word
  WHERE s.word IN (SELECT word FROM word_spellings WHERE spelling=?) LIMIT 500`;
type WordRow = { word: string; reading: string; class: string; spelling: string; source: string; locator: string; tier: string; documents: number | null; occurrences: number | null };
type WordSpelling = { spelling: string; code_point: string | null; current: boolean; sources: { source: string; tier: string; ruby: string | null; documents: number | null; occurrences: number | null }[] };
// The 振り仮名 a row counts, from its locator (`ruby-spellings.tsv なと 抔`).
const rubyOf = (locator: string) => locator.startsWith('ruby-spellings.tsv ') ? locator.split(' ')[1] : null;
const WORDS_SHAPE = 2;
/** The words `char` is cited as writing, by id, each with every spelling cited for it: most sources
 *  first, then most documents in all, then by code point; `sources` cites each source used. */
async function wordsOf(env: Env, ctx: ExecutionContext, origin: string, char: string, version: string) {
  return edge(ctx, new Request(`${origin}/layers/words?c=${encodeURIComponent(char)}&v=${encodeURIComponent(`${WORDS_SHAPE}:${version}`)}`), async () => {
    const rows = (await env.ATLAS.prepare(wordSpellingsQuery()).bind(char).all<WordRow>()).results;
    const words = new Map<string, { id: string; reading: string; class: string; spellings: Map<string, WordSpelling> }>();
    for (const row of rows) {
      const word = words.get(row.word) ?? { id: row.word, reading: row.reading, class: row.class, spellings: new Map() };
      const entry = word.spellings.get(row.spelling) ?? { spelling: row.spelling, code_point: [...row.spelling].length === 1 ? cp(row.spelling) : null,
        current: row.spelling === char, sources: [] };
      entry.sources.push({ source: row.source, tier: row.tier, ruby: rubyOf(row.locator), documents: row.documents, occurrences: row.occurrences });
      word.spellings.set(row.spelling, entry);
      words.set(row.word, word);
    }
    const cited = (entry: WordSpelling) => new Set(entry.sources.map(s => s.source)).size;
    const documents = (entry: WordSpelling) => entry.sources.reduce((n, s) => n + (s.documents ?? 0), 0);
    const items = [...words.values()].sort((a, b) => byCodePoint(a.id, b.id)).map(word => ({ id: word.id, reading: word.reading, class: word.class,
      spellings: [...word.spellings.values()].sort((a, b) => cited(b) - cited(a) || documents(b) - documents(a) || byCodePoint(a.spelling, b.spelling)) }));
    const all: Record<string, string> = (await siteValue(env, 'word_sources')) || {};
    const used = [...new Set(rows.map(row => row.source))].sort();
    return { items, sources: Object.fromEntries(used.map(source => [source, all[source] ?? source])) };
  });
}

// Galleries.

function styleGroup(q: URLSearchParams): StyleGroup | null {
  const value = q.get('style');
  if (!value || value === 'all') return null;
  if (!Object.hasOwn(STYLE_GROUPS, value)) throw new Problem(422, 'Invalid style.');
  return value as StyleGroup;
}
// A widened gallery counts no further than this: the variants of a common character can hold a hundred
// thousand corpus glyphs.
const WIDENED_CAP = 2000;
// A gallery pages by cursor (`after`, the `next` of the page before). A page addressed by offset comes
// from a tab opened before the change and is answered with a prompt to reload it.
export const STALE_PAGE = 'This page is from before an update. Reload it.';
function paging(q: URLSearchParams) {
  if ((q.get('offset') ?? '0') !== '0' && q.get('offset') !== '') throw new Problem(409, STALE_PAGE, { code: 'reload' });
  return q.get('after') || null;
}
// A review state by name, through `codes`; a name no code has matches no crop.
async function stateCode(env: Env, name: string | null): Promise<number | null> {
  if (!name || name === 'all') return null;
  const row = await env.ATLAS.prepare(`SELECT code FROM codes WHERE field='state' AND key=?`).bind(name).first<{ code: number }>();
  return row?.code ?? -1;
}
// The books dated on the composed axis, by `book_date_axis`, with the year a time axis places them at
// (a date with only an end at that end). Every gallery on the axis reads this list.
export const composedQuery = () => `SELECT book,coalesce(start,end) AS year FROM book_dates WHERE axis='composed' AND coalesce(start,end) IS NOT NULL`;
async function composedYears(env: Env): Promise<Map<number, number>> {
  return new Map((await env.ATLAS.prepare(composedQuery()).all<{ book: number; year: number }>()).results.map(r => [r.book, r.year]));
}
// Counts by style group from cells, the withheld apart.
type Groups = Record<StyleGroup, number>;
const none = (): Groups => ({ cursive: 0, unassessed: 0, formal: 0 });
function counted(cells: Cell[], keep: (cell: Cell) => boolean): { styles: Groups; withheld: Groups } {
  const styles = none(), withheld = none();
  for (const cell of cells) if (keep(cell)) (cell.quiz === 0 ? withheld : styles)[groupOf(cell.style)] += cell.n;
  return { styles, withheld };
}
const total = (by: Groups, group: StyleGroup | null) => group ? by[group] : by.cursive + by.unassessed + by.formal;
const plus = (a: Groups, b: Groups, sign = 1): Groups =>
  ({ cursive: a.cursive + sign * b.cursive, unassessed: a.unassessed + sign * b.unassessed, formal: a.formal + sign * b.formal });
// A gallery's ranges answer RangeError for a cursor they did not write.
async function paged<T>(read: () => Promise<T>): Promise<T> {
  try { return await read() } catch (error) { if (error instanceof RangeError) throw new Problem(422, error.message); throw error }
}

type Scope = 'character' | 'grapheme' | 'variants';
type Gallery = { sources: Source[][]; chars: number[]; filed: number[] };
// Where a gallery's crops are: the character's range; a family's member ranges and its members' filed
// crops; or the character and its variants, one after another in code point order. `filedOnly` keeps
// only the crops with no character.
async function galleryOf(env: Env, card: Card, scope: Scope, filedOnly = false): Promise<Gallery> {
  if (scope === 'variants') {
    if (filedOnly) return { sources: [], chars: [], filed: [] };
    const chars = writtenVariants(card.object, card.data.char).sort(byCodePoint).flatMap(c => { const id = gid(card, c); return id === null ? [] : [id] });
    return { sources: chars.map(key => [{ by: 'char', key }]), chars, filed: [] };
  }
  if (scope === 'grapheme') {
    const { members } = await familyOf(env, card), chars = filedOnly ? [] : members;
    const sources: Source[] = [...chars.map(key => ({ by: 'char' as const, key })), ...members.map(key => ({ by: 'family' as const, key }))];
    return { sources: sources.length ? [sources] : [], chars, filed: members };
  }
  const own = gid(card, card.data.char);
  return own === null || filedOnly ? { sources: [], chars: [], filed: [] } : { sources: [[{ by: 'char', key: own }]], chars: [own], filed: [] };
}
async function cellsOf(env: Env, g: Gallery, kind: 0 | 1): Promise<Cell[]> {
  const [chars, filed] = await Promise.all([characterCounts(env, g.chars), g.filed.length ? filedCounts(env, g.filed) : new Map<number, Cell[]>()]);
  return [...[...chars.values()].flat(), ...[...filed.values()].flat()].filter(cell => cell.kind === kind);
}
/** Counts by style group of the crops a dated gallery lists, the withheld among them when it shows
 *  them: from the cells where they can answer (no year range, or the crops of no year on the witness
 *  axis), else counted over the range's own index. */
async function datedCounts(env: Env, g: Gallery, q: GalleryQuery, cells: Cell[]): Promise<Groups> {
  const fromCells = (more: (cell: Cell) => boolean = () => true) => {
    const c = counted(cells, cell => (q.state == null || cell.state === q.state) && more(cell));
    return q.withheld ? plus(c.styles, c.withheld) : c.styles;
  };
  if (!q.years) return fromCells();
  if (q.years === 'undated' && q.axis !== 'composed') return fromCells(cell => cell.decade === null);
  // The crops of no composed date are those outside every dated book.
  const ranged = q.years === 'undated' ? { ...q, years: null } : q, phase: Phase = q.axis === 'composed' ? 'K' : 'R';
  const statements = g.sources.flat().flatMap(source => {
    const range = rangeCountQuery(ranged, phase, source);
    return range ? [env.ATLAS.prepare(range.sql).bind(...range.values)] : [];
  });
  const found = none();
  if (statements.length) for (const result of await env.ATLAS.batch(statements) as D1Result<{ style: number; n: number }>[])
    for (const row of result.results) found[groupOf(row.style)] += row.n;
  return q.years === 'undated' ? plus(fromCells(), found, -1) : found;
}
const datedFields = (dated: YearOptions | null) => dated ? { order: dated.order, axis: dated.axis, years: dated.years } : {};
function galleryQuery(kind: 0 | 1, g: Gallery, dated: YearOptions | null, composed: Map<number, number> | undefined,
  rest: { group: StyleGroup | null; state?: number | null; withheld: boolean; after: string | null; limit: number }): GalleryQuery {
  return { kind, segments: g.sources, order: dated?.order ?? 'style', years: dated?.years ?? null, axis: dated?.axis ?? 'witness', composed, ...rest };
}

/** The collection's crops of a character, its family or its variants (`/layers/occurrences`). */
export async function occurrences(env: Env, card: Card, q: URLSearchParams) {
  const { data } = card, limit = integer(q, 'limit', 24, 200), after = paging(q), group = styleGroup(q);
  if (q.get('visual_group')) throw new Problem(422, 'Filter the corpus gallery by visual group.');
  const state = await stateCode(env, q.get('state')), withheld = q.get('withheld') === 'show';
  const widened = q.get('scope') === 'variants' || q.get('expand') === 'variants';
  const scope: Scope = widened ? 'variants' : q.get('scope') === 'grapheme' || q.get('expand') === 'grapheme' ? 'grapheme' : 'character';
  const dated = widened ? null : yearOptions(q, fail);
  const g = await galleryOf(env, card, scope);
  const query = galleryQuery(0, g, dated, dated?.axis === 'composed' ? await composedYears(env) : undefined, { group, state, withheld, after, limit });
  const [cells, page] = await Promise.all([cellsOf(env, g, 0), paged(() => galleryPage(env, query))]);
  const items = await listingItems(env, page.ids);
  const head = { ...data.candidates, query: data.code_point, code_point: data.code_point };
  const c = counted(cells, cell => state === null || cell.state === state);
  if (widened) {
    const all = total(withheld ? plus(c.styles, c.withheld) : c.styles, group), n = Math.min(all, WIDENED_CAP);
    return { ...head, total: n, capped: all > WIDENED_CAP, available: items.length, items,
      counts: { total: n, exact: n, exact_total: n }, style_groups: STYLE_NAMES, scope: 'variants', withheld: null, status: 'ok', next: page.next };
  }
  const styles = dated ? await datedCounts(env, g, query, cells) : withheld ? plus(c.styles, c.withheld) : c.styles, n = total(styles, group);
  return { ...head, total: n, available: items.length, items, counts: { total: n, exact: n, exact_total: n }, styles,
    style_groups: STYLE_NAMES, scope: q.get('scope') || 'character', status: 'ok',
    withheld: withheld || dated ? null : total(c.withheld, group), ...datedFields(dated), next: page.next };
}

// A visual group's crops, read whole from `crop_visual`: a few hundred at most.
const VISUAL_MAX = 5000;
export const visualQuery = () => `SELECT id,char,family,style,shuffle,year,book FROM crops
  WHERE visual_group=? AND kind=1 AND visual_group IS NOT NULL LIMIT ${VISUAL_MAX}`;
type VisualRow = { id: number; char: number | null; family: number | null; style: number; shuffle: number; year: number | null; book: number };
// One page of a visual group's crops within a gallery, in the gallery's order and under its cursors,
// with the counts by style group of those it lists.
async function visualPage(env: Env, g: Gallery, q: GalleryQuery, code: number) {
  const rows = (await env.ATLAS.prepare(visualQuery()).bind(code).all<VisualRow>()).results;
  const chars = new Set(g.chars), filed = new Set(g.filed), phases = phasesOf(q);
  const years = q.years && q.years !== 'undated' ? q.years : null, books = new Map(composedBooks(q));
  const placed = rows.filter(r => r.char !== null ? chars.has(r.char) : r.family !== null && filed.has(r.family)).flatMap(r => {
    const y = q.axis === 'composed' ? (q.composed?.get(r.book) ?? null) : r.year;
    const inRange = q.axis === 'composed' ? books.has(r.book) : y !== null && (!years || (y >= years.from && y <= years.to));
    const phase = phases.find(p => p === 'S' || (p === 'U' || p === 'N' ? y === null : inRange));
    return phase ? [{ ...r, phase, rank: phases.indexOf(phase), lead: phase === 'Y' || phase === 'C' ? y! : r.style }] : [];
  });
  const styles = none();
  for (const r of placed) styles[groupOf(r.style)]++;
  const shown = placed.filter(r => !q.group || groupOf(r.style) === q.group)
    .sort((a, b) => a.rank - b.rank || a.lead - b.lead || a.shuffle - b.shuffle || a.id - b.id);
  const name = (r: typeof shown[number]) => `0${r.phase}${r.lead}.${r.shuffle}.${publicId(r.id)}`;
  // A page starts after the cursor's place in the order, whether or not that crop is still listed.
  let start = 0;
  if (q.after) {
    const at = paged(async () => cursorOf(q.after!));
    const { segment, phase, after: [lead, shuffle, id] } = await at;
    if (segment !== 0 || !phases.includes(phase)) throw new Problem(422, 'Invalid cursor.');
    const key = [phases.indexOf(phase), lead, shuffle, id];
    start = shown.findIndex(r => { const k = [r.rank, r.lead, r.shuffle, r.id]; for (let i = 0; i < 4; i++) if (k[i] !== key[i]) return k[i] > key[i]; return false });
    if (start < 0) start = shown.length;
  }
  const page = shown.slice(start, start + q.limit);
  return { ids: page.map(r => r.id), next: start + q.limit < shown.length ? name(page[page.length - 1]) : null, styles };
}

/** The corpus glyphs of a character, its family or its variants (`/layers/candidates`). */
export async function corpusOccurrences(env: Env, card: Card, q: URLSearchParams) {
  const { data, detail } = card, limit = integer(q, 'limit', 24, 200), after = paging(q), group = styleGroup(q);
  const scope: Scope = q.get('scope') === 'grapheme' ? 'grapheme' : q.get('scope') === 'variants' ? 'variants' : 'character';
  const widened = scope === 'variants', dated = widened ? null : yearOptions(q, fail), visual = q.get('visual_group');
  const [g, family] = await Promise.all([galleryOf(env, card, scope, visual === 'unassigned'), galleryOf(env, card, 'grapheme')]);
  const query = galleryQuery(1, g, dated, dated?.axis === 'composed' ? await composedYears(env) : undefined, { group, withheld: true, after, limit });
  let page: { ids: number[]; next: string | null }, styles: Groups;
  if (visual && visual !== 'unassigned') {
    const code = await env.ATLAS.prepare(`SELECT code FROM codes WHERE field='visual_group' AND key=?`).bind(visual).first<{ code: number }>();
    ({ styles, ...page } = code ? await visualPage(env, g, query, code.code) : { ids: [], next: null, styles: none() });
  } else {
    const [cells, found] = await Promise.all([cellsOf(env, g, 1), paged(() => galleryPage(env, query))]);
    page = found;
    styles = dated ? await datedCounts(env, g, query, cells) : (c => plus(c.styles, c.withheld))(counted(cells, () => true));
  }
  const [items, members, filed] = await Promise.all([listingItems(env, page.ids), characterCounts(env, family.chars), filedCounts(env, family.filed)]);
  const glyphs = (cells: Map<number, Cell[]>) => [...cells.values()].flat().filter(c => c.kind === 1).reduce((n, c) => n + c.n, 0);
  const n = total(styles, group), unassigned = glyphs(filed);
  return { ...data.candidates, code_point: data.code_point, total: widened ? Math.min(n, WIDENED_CAP) : n, capped: widened && n > WIDENED_CAP,
    available: items.length, items, ...(widened ? {} : { styles }), scope, status: 'ok', visual_analysis: detail.visual_analysis,
    family_total: glyphs(members) + unassigned, unassigned_count: unassigned, ...datedFields(dated), next: page.next };
}

// Decades and the time axis.

// The decades and the time axis are kept at the edge by the publication and the hour, as the old site
// kept them: a review moves a crop to another decade or character there within the hour.
async function axisVersion(env: Env, now = Date.now()) {
  return `${publicationKey(await catalogueStamps(env, now))}:${Math.floor(now / 3_600_000)}`;
}
const decadeOf = (year: number) => year - (((year % 10) + 10) % 10);
const byDecade = (a: number | null, b: number | null) => a === b ? 0 : a === null ? -1 : b === null ? 1 : a - b;
// One source's crops in the dated books of the composed axis, counted per book along `crop_char_book`
// or `crop_family_book`.
export const composedCountQuery = (source: Source, kind: 0 | 1, books: number[], withheld: boolean) =>
  `SELECT book,count(*) AS n FROM crops INDEXED BY ${source.by === 'char' ? 'crop_char_book' : 'crop_family_book'} WHERE ${source.by === 'char'
    ? 'char=? AND kind<2' : 'family=? AND family IS NOT NULL AND char IS NULL'} AND kind=${kind} AND book IN (${books.join(',')})${withheld ? '' : ' AND quiz=1'} GROUP BY book`;
/** A character's crops (or its family's) counted per decade of its book's date, the collection's and the
 *  corpus's apart, with the undated as decade null, sorted as SQLite sorts them (null first). Withheld
 *  crops are left out unless `withheld` asks for them, as in the gallery the decades sit beside. On the
 *  witness axis the decades are the publication's counts and the reviews since; on the composed axis
 *  the crops in the dated books are counted per book and the rest are undated. */
export async function decades(env: Env, ctx: ExecutionContext, url: URL, tools: CharacterTools) {
  const q = url.searchParams, card = await characterCard(env, q.get('code_point') || '', tools, ctx), { data } = card;
  const axis = q.get('axis') === 'composed' ? 'composed' : 'witness', grapheme = q.get('scope') === 'grapheme', withheld = q.get('withheld') === 'show';
  const version = await axisVersion(env);
  const key = new Request(`${url.origin}/layers/decades?v=${encodeURIComponent(version)}&c=${encodeURIComponent(data.code_point)}&s=${grapheme}&a=${axis}&w=${withheld}`);
  return edge(ctx, key, async () => {
    const g = await galleryOf(env, card, grapheme ? 'grapheme' : 'character');
    const years = axis === 'composed' ? await composedYears(env) : new Map<number, number>(), books = [...years.keys()].sort((a, b) => a - b);
    const lists = await Promise.all(([0, 1] as const).map(async kind => {
      const cells = await cellsOf(env, g, kind), shown = (cell: Cell) => kind === 1 || withheld || cell.quiz !== 0;
      const by = new Map<number | null, number>(), add = (decade: number | null, n: number) => { if (n) by.set(decade, (by.get(decade) ?? 0) + n) };
      if (axis === 'witness') { for (const cell of cells) if (shown(cell)) add(cell.decade, cell.n) }
      else {
        const statements = books.length ? g.sources.flat().map(source => env.ATLAS.prepare(composedCountQuery(source, kind, books, kind === 1 || withheld)).bind(source.key)) : [];
        let dated = 0;
        for (const result of statements.length ? await env.ATLAS.batch(statements) as D1Result<{ book: number; n: number }>[] : [])
          for (const row of result.results) { add(decadeOf(years.get(row.book)!), row.n); dated += row.n }
        add(null, cells.filter(shown).reduce((n, c) => n + c.n, 0) - dated);
      }
      return [...by].sort((a, b) => byDecade(a[0], b[0])).map(([decade, n]) => [decade, n]);
    }));
    return { code_point: data.code_point, axis, scope: grapheme ? 'grapheme' : 'character', local: lists[0], corpus: lists[1] };
  });
}

// A character's or grapheme's crops along its time axis (編年): each decade's crops counted, and a few of
// them drawn by their shuffle so each decade shows a fair sample; the undated as decade null. A crop is
// placed by its book's date on `axis`, and the crops may be narrowed to a style group, a kind of
// production (`handwritten`, `printed`, `inscribed` and what is under it) or some of the grapheme's
// characters (a script's). Each source's crops are read once along their index (`crop_year` and
// `crop_undated`, or `crop_char_book`, and the family's) and ranked in the database, so a request reads
// the crops it counts and answers the few it shows; a production on the witness axis, or a crop outside
// the composed axis's books, reads its crop's row as well. The edge keeps the answer an hour.
const CHRONOLOGY_MAX = 240;
export type ChronologyFilter = { style: StyleGroup | null; production: string | null; chars: string[] };
function chronologyFilter(q: URLSearchParams): ChronologyFilter {
  const production = q.get('production');
  if (production && !/^[a-z_]+(\/[a-z_]+)*$/.test(production)) throw new Problem(422, 'Unknown production.');
  const chars = (q.get('chars') ?? '').split(',').filter(Boolean);
  if (chars.length > 20 || chars.some(c => [...c].length > 4)) throw new Problem(422, 'Name at most 20 characters.');
  return { style: styleGroup(q), production, chars };
}
// A production and every node under it: `printed` takes `printed/woodblock`, compared as text. The
// table holds a few dozen rows.
export const productionsQuery = () => `SELECT id FROM productions WHERE key=? OR substr(key,1,?)=?`;
// Characters by text, through `graphemes`' unique key.
export const graphemeIdsQuery = () => `SELECT id FROM graphemes WHERE text IN (SELECT value FROM json_each(?))`;
type Ranked = { id: number; decade: number | null; shuffle: number; bucket: number };
const DECADE = (year: string) => `CASE WHEN ${year} IS NULL THEN NULL ELSE (${year} - (((${year} % 10) + 10) % 10)) END`;
/** The statements that read one source's crops for the time axis, the dated and the undated, on either
 *  axis: each decade's first `per` crops by shuffle and id, each with its decade and how many crops the
 *  decade holds. Each ranks the source's crops it reads along its index, so the database sorts them. */
export function chronologyQueries(source: Source, kind: 0 | 1, axis: 'witness' | 'composed',
  filter: { style: StyleGroup | null; productions: number[] | null; withheld: boolean }, composed: [number, number][], per: number) {
  const char = source.by === 'char', by = `${char ? 'char=? AND kind<2' : 'family=? AND family IS NOT NULL AND char IS NULL'} AND kind=${kind}`;
  const extra: string[] = [], values: number[] = [source.key];
  if (filter.style) {
    const [low, high] = STYLE_GROUPS[filter.style];
    extra.push('style>=?'); values.push(low);
    if (high !== null) { extra.push('style<=?'); values.push(high) }
  }
  if (filter.productions) extra.push(`production IN (${filter.productions.join(',') || 'NULL'})`);
  if (!filter.withheld) extra.push('quiz=1');
  const tail = extra.map(c => ' AND ' + c).join('');
  const read = (from: string, decade: string) => ({ sql: `SELECT id,decade,shuffle,bucket FROM (SELECT id,${decade} AS decade,shuffle,
    row_number() OVER (PARTITION BY ${decade} ORDER BY shuffle,id) AS r,count(*) OVER (PARTITION BY ${decade}) AS bucket FROM crops ${from}${tail}) WHERE r<=?`,
    values: [...values, per] });
  if (axis === 'witness') return [
    read(`INDEXED BY ${char ? 'crop_year' : 'crop_family_year'} WHERE ${by} AND year IS NOT NULL`, DECADE('year')),
    read(char ? `INDEXED BY ${kind === 0 ? 'crop_undated' : 'crop_undated_corpus'} WHERE ${by} AND kind=${kind} AND year IS NULL` : `WHERE ${by} AND year IS NULL`, 'NULL'),
  ];
  const books = composed.map(([b]) => b).join(',');
  if (!composed.length) return [read(`WHERE ${by}`, 'NULL')];
  return [
    read(`INDEXED BY ${char ? 'crop_char_book' : 'crop_family_book'} WHERE ${by} AND book IN (${books})`,
      `CASE book ${composed.map(([b, y]) => `WHEN ${b} THEN ${decadeOf(y)}`).join(' ')} END`),
    read(`WHERE ${by} AND +book NOT IN (${books})`, 'NULL'),
  ];
}
const productionKeys = new Map<string, Promise<number[]>>();
function productionIds(env: Env, key: string): Promise<number[]> {
  let read = productionKeys.get(key);
  if (!read) {
    read = env.ATLAS.prepare(productionsQuery()).bind(key, key.length + 1, key + '/').all<{ id: number }>().then(r => r.results.map(row => row.id));
    read.catch(() => productionKeys.delete(key));
    productionKeys.set(key, read);
  }
  return read;
}
export async function chronology(env: Env, ctx: ExecutionContext, url: URL, tools: CharacterTools) {
  const q = url.searchParams, card = await characterCard(env, q.get('code_point') || '', tools, ctx), { data } = card;
  const axis = q.get('axis') === 'composed' ? 'composed' : 'witness', grapheme = q.get('scope') === 'grapheme';
  const per = Math.max(1, integer(q, 'per', 6, 12)), filter = chronologyFilter(q), withheld = q.get('withheld') === 'show';
  const version = await axisVersion(env);
  const key = new Request(`${url.origin}/layers/chronology?v=${encodeURIComponent(version)}&c=${encodeURIComponent(data.code_point)}&s=${grapheme}&a=${axis}&p=${per}`
    + `&f=${encodeURIComponent(JSON.stringify(filter))}&w=${withheld}`);
  return edge(ctx, key, async () => {
    const [productions, composed, g, wanted] = await Promise.all([
      filter.production ? productionIds(env, filter.production) : null,
      axis === 'composed' ? composedYears(env).then(years => [...years].sort((a, b) => a[0] - b[0])) : [],
      galleryOf(env, card, grapheme ? 'grapheme' : 'character'),
      // Some of the family's characters: their ranges only, and no crop of no character.
      filter.chars.length ? env.ATLAS.prepare(graphemeIdsQuery()).bind(JSON.stringify(filter.chars)).all<{ id: number }>()
        .then(r => new Set(r.results.map(row => row.id))) : null,
    ]);
    const sources = g.sources.flat().filter(s => !wanted || (s.by === 'char' && wanted.has(s.key)));
    const lists = await Promise.all(([0, 1] as const).map(async kind => {
      const statements = sources.flatMap(source => chronologyQueries(source, kind, axis, { style: filter.style, productions, withheld: kind === 1 || withheld }, composed, per))
        .map(({ sql, values }) => env.ATLAS.prepare(sql).bind(...values));
      const results = statements.length ? (await env.ATLAS.batch(statements) as D1Result<Ranked>[]).map(r => r.results) : [];
      // Each decade's crops ranked by shuffle and id across the sources, counted over every source, the
      // decades as SQLite sorts them (null first).
      const by = new Map<number | null, { ranked: Ranked[]; bucket: number }>();
      for (const rows of results) {
        const counted = new Set<number | null>();
        for (const row of rows) {
          const d = by.get(row.decade) ?? by.set(row.decade, { ranked: [], bucket: 0 }).get(row.decade)!;
          d.ranked.push(row);
          if (!counted.has(row.decade)) { counted.add(row.decade); d.bucket += row.bucket }
        }
      }
      return [...by].sort((a, b) => byDecade(a[0], b[0])).map(([decade, d]) => ({ decade, ranked: d.ranked.sort((x, y) => x.shuffle - y.shuffle || x.id - y.id), bucket: d.bucket }));
    }));
    // At most CHRONOLOGY_MAX crops of each list are drawn: across many decades each shows fewer, and
    // past the cap the first decades keep theirs.
    const fewer = (found: { decade: number | null; ranked: Ranked[] }[]) => {
      const take = (k: number) => found.flatMap(d => d.ranked.slice(0, k).map((row, i) => ({ ...row, r: i + 1 })));
      let k = per;
      while (k > 1 && take(k).length > CHRONOLOGY_MAX) k--;
      return take(k).slice(0, CHRONOLOGY_MAX);
    };
    type Bucket = { decade: number | null; local: number; corpus: number; items: Json[] };
    const buckets = new Map<number | null, Bucket>();
    const bucketOf = (decade: number | null) => buckets.get(decade) ?? buckets.set(decade, { decade, local: 0, corpus: 0, items: [] }).get(decade)!;
    for (const d of lists[0]) bucketOf(d.decade).local = d.bucket;
    for (const d of lists[1]) bucketOf(d.decade).corpus = d.bucket;
    // A decade shows the collection's crops first; the corpus fills what is left of its `per`.
    const own = fewer(lists[0]), shown = new Map<number | null, number>();
    for (const row of own) shown.set(row.decade, (shown.get(row.decade) ?? 0) + 1);
    const glyphs = fewer(lists[1]).filter(row => row.r <= per - (shown.get(row.decade) ?? 0));
    const [local, corpus] = await Promise.all([listingItems(env, own.map(r => r.id)), listingItems(env, glyphs.map(r => r.id))]);
    const decadeOfCrop = new Map([...own, ...glyphs].map(r => [r.id, r.decade]));
    for (const item of local) bucketOf(decadeOfCrop.get(cropOf(item.id)!) ?? null).items.push({ ...item, origin: 'collection' });
    for (const item of corpus) bucketOf(decadeOfCrop.get(cropOf(item.id)!) ?? null).items.push({ ...item, origin: 'corpus' });
    const all = [...buckets.values()];
    for (const bucket of all) bucket.items = bucket.items.slice(0, per);
    return { code_point: data.code_point, char: data.char, axis, scope: grapheme ? 'grapheme' : 'character', per,
      buckets: all.filter(b => b.decade !== null).sort((a, b) => a.decade! - b.decade!), undated: buckets.get(null) ?? { decade: null, local: 0, corpus: 0, items: [] } };
  });
}

// Lists, the character page, citations.

/** A page of the card lists (`/layers/graphemes`, `/layers/ligatures`): the families of several
 *  characters, or the ligatures, by code point. `total` is the page's length, as it always was. */
export async function cards(env: Env, list: 'graphemes' | 'ligatures', q: URLSearchParams, ctx: ExecutionContext) {
  const { items } = await cardList(env, list, integer(q, 'offset', 0), integer(q, 'limit', 200, 500), ctx);
  return { items, total: items.length };
}

/** The character page (`/layers/characters/:cp`): the card's detail, its 異体字 and words, and the first
 *  page of its gallery with its counts. */
export async function characterPage(env: Env, ctx: ExecutionContext, url: URL, value: string, tools: CharacterTools) {
  const card = await characterCard(env, value, tools, ctx), { detail } = card;
  const found = await occurrences(env, card, url.searchParams);
  const version = await catalogueVersion(env);
  const [variants, words] = await Promise.all([variantsOf(env, ctx, url.origin, card, version), wordsOf(env, ctx, url.origin, detail.char, version)]);
  return { ...detail, variants, words, query: detail.code_point, samples: found.items, occurrences: { ...found.counts, filtered: found.total } };
}

const CITABLE_CHARACTER = /^U\+[0-9A-Fa-f]{4,6}(-U\+[0-9A-Fa-f]{4,6})*$/;
/** A citation of a grapheme or a form (`/atlas/cite/grapheme|form/:cp`), as CSL-JSON, BibTeX or
 *  Hayagriva (`?format=`). It is read again once the catalogue version moves, and on each new day (in
 *  UTC), the date it was accessed. */
export async function citeCharacter(env: Env, ctx: ExecutionContext, url: URL, kind: 'grapheme' | 'form', raw: string, tools: CharacterTools,
  open: Record<string, string>): Promise<Response> {
  let id: string;
  try { id = decodeURIComponent(raw) } catch { throw new Problem(404, 'Nothing is published at this address.') }
  const token = url.searchParams.get('v'), format = (url.searchParams.get('format') ?? 'csl') as Format;
  if (id.length > 512 || (token !== null && !/^[0-9a-z]{6}$/.test(token)) || !FORMATS.includes(format)) throw new Problem(422, 'Invalid citation address.');
  if (!CITABLE_CHARACTER.test(id)) throw new Problem(404, 'Character not found.');
  const now = new Date(), day: Day = [now.getUTCFullYear(), now.getUTCMonth() + 1, now.getUTCDate()];
  const key = new Request(`${url.origin}/atlas/cite/${kind}/${encodeURIComponent(id)}?v=${token ?? ''}&f=${format}&d=${day.join('-')}&c=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return new Response(cached.body, { headers: { ...Object.fromEntries(cached.headers), 'cache-control': 'no-cache' } });
  const entry = characterEntry(kind, (await characterCard(env, id.split('-').join(' '), tools, ctx)).detail);
  const body = format === 'bibtex' ? bibtex(entry, url.origin, day) + '\n' : format === 'hayagriva' ? hayagriva(entry, url.origin, day)
    : JSON.stringify([csl(entry, url.origin, day)], null, 2);
  // The edge keeps a copy for the day it is dated; a browser asks again, so a citation is never dated a day late.
  const headers = { 'content-type': CITE_TYPES[format], 'x-content-type-options': 'nosniff', ...open };
  ctx.waitUntil(caches.default.put(key, new Response(body, { headers: { ...headers, 'cache-control': `public, max-age=${CITE_TTL}` } })));
  return new Response(body, { headers: { ...headers, 'cache-control': 'no-cache' } });
}
