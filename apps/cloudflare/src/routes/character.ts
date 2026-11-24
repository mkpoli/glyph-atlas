// Character pages on the new storage layout: the card and its 異体字 from the character's reference
// object (`atlas3Reference.ts`), the galleries of its crops and corpus glyphs from index ranges
// (`atlas3Gallery.ts`), their counts from the publication's answers, and its decades and time axis.
//
// A crop joins the family of its character (`graphemes.head`); a crop with no character joins the
// family its source names (`crops.family`, migration 0012), which every crop keeps. A family's gallery reads one range
// per member character and one per member's filed crops, merged in the order they show.
import { cropOf, publicId } from '../atlas3';
import { STYLE_GROUPS, STYLE_NAMES, characterCounts, claimsAt, composedBooks, cursorOf, familyClaims, filedCell, filedClaimsKept, filedCounts, galleryPage, groupOf, phasesOf,
  rangeCountQuery, sourceWhere, type Cell, type Filed, type FiledAt, type GalleryQuery, type Phase, type Source, type StyleGroup } from '../atlas3Gallery';
import { listingItems } from '../atlas3Listing';
import { cardList, characterIds, characterObject, type CharacterObject } from '../atlas3Reference';
import { currentPointer } from '../atlas3Answers';
import { catalogueStamps, catalogueVersion, publicationKey } from '../atlas3Stamps';
import { Problem } from '../busy';
import { idsCharacter, isSeal } from '../category';
import { CITE_TTL, CITE_TYPES, FORMATS, bibtex, characterEntry, csl, hayagriva, type Day, type Format } from '../citation';
import { yearOptions, type YearOptions } from '../dating';
import { WIDENED_CAP, addCrops, fileClaims, soleForms, type Rows } from '../graphemeTotals';
import { figureForms, partsOf } from '../partFigures';
import { drawnForm, drawnFormsOf } from '../representation';
import { WRITERS, WRITER_SOURCES } from '../writtenWithData';

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

/** A grapheme with its drawn forms (docs/decisions/0014-drawn-forms.md) after its characters and before
 *  its small seal characters: each is a member the reference object does not hold, since only the site's
 *  table names it. */
function withDrawn(grapheme: Json): Json {
  if (!grapheme?.members) return grapheme;
  const listed = new Set(grapheme.members.map((m: Json) => m.char));
  const drawn = drawnFormsOf(grapheme.char).filter(char => !listed.has(char));
  if (!drawn.length) return grapheme;
  const members = [...grapheme.members.filter((m: Json) => !isSeal(m.char)),
    ...drawn.map(char => ({ code_point: cp(char), char, script: drawnForm(char)!.script, drawn: true })), ...grapheme.members.filter((m: Json) => isSeal(m.char))];
  return { ...grapheme, members, character_count: members.length };
}
const drawnIn = (card: Json) => card?.grapheme ? { ...card, grapheme: withDrawn(card.grapheme) } : card;
/** The card of a drawn form, from the card of the grapheme it writes: it has no entry in the character
 *  table, and is filed under that grapheme. `id` is its row of `graphemes`, null before a crop is filed
 *  under it. */
export function drawnCard(value: string, head: CharacterObject, id: number | null): Card {
  const code_point = cp(value), url = '/layers/characters/' + code_point, script = drawnForm(value)!.script;
  const self = { code_point, char: value, name: null, script };
  const grapheme = { ...withDrawn(head.detail.grapheme ?? head.data.grapheme), is_self: false };
  const data = { ...self, age: null, block: null, readings: [], reading: null, jibo: [], ligature: null, occurrence_count: 0,
    url, grapheme, default_scope: 'character', kind: head.data.kind, drawn: true,
    candidates: { status: 'ok', known: false, glyphs: 0, lines: 0, pages: 0, total: 0, sources: [],
      counts_kind: 'source_transcription_classes', requires_family_scope: false, source_glyphs: 0, family_glyphs: 0, imported: 0 } };
  const detail = { ...data, alias: null, category: 'Co', confusables: [], characters: [data], derived: [], expansions: [],
    visual_analysis: { status: 'not_analyzed', family: grapheme.code_point, model_revision: null, sample_count: 0, assigned_count: 0,
      unassigned_count: 0, groups: [] } };
  // Its own edges are none; the ids are its family's, with its own.
  const object: CharacterObject = { ...head, data, detail, variants: [], variant_families: {}, derived: [], substitutions: [], writes: [], writers: [],
    ids: { ...head.ids, ...(id === null ? {} : { [value]: id }) } };
  return { data, detail, object };
}

export type Card = { data: Json; detail: Json; object: CharacterObject | null };
/** The card of a character the table does not hold (a description sequence, an old Hangul syllable), with
 *  its row of `graphemes`, which its gallery reads; none before a crop is written with it. */
function ownCard(text: string, card: { data: Json; detail: Json }, id: number | null): Card {
  return { ...card, object: id === null ? null : { grapheme: id, ...card, variants: [], variant_families: {}, derived: [], substitutions: [], writes: [], writers: [], ids: { [text]: id } } };
}
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
  if (object) return { data: drawnIn(object.data), detail: drawnIn(await namedGroups(env, object.detail)), object };
  const row = async () => (await env.ATLAS.prepare('SELECT id FROM graphemes WHERE text=?').bind(text).first<{ id: number }>())?.id ?? null;
  if (idsCharacter(text)) return ownCard(text, describedCard(text), await row());
  const drawn = drawnForm(text), head = drawn ? await characterObject(env, cp(drawn.for), ctx) : null;
  if (head) return drawnCard(text, head, await row());
  if (tools.jamoSyllable(text)) return ownCard(text, jamoCard(text), await row());
  throw new Problem(404, 'Character not found.');
}
const gid = (card: Card, char: string): number | null => card.object && Object.hasOwn(card.object.ids, char) ? card.object.ids[char] : null;

// A family's characters by its head, by `grapheme_head`, the head among them; at most FAMILY_MAX.
const FAMILY_MAX = 128;
export const membersQuery = () => `SELECT id,text FROM graphemes WHERE head=? LIMIT ${FAMILY_MAX}`;
async function familyOf(env: Env, card: Card): Promise<{ head: number | null; members: number[]; texts: Map<number, string> }> {
  const char = card.data.char, top = card.data.grapheme?.char ?? char, own = gid(card, char), head = gid(card, top) ?? own;
  if (head === null) return { head: null, members: [], texts: new Map() };
  const rows = (await env.ATLAS.prepare(membersQuery()).bind(head).all<{ id: number; text: string }>()).results;
  const texts = new Map([...(own === null ? [] : [[own, char] as const]), [head, gid(card, top) === null ? char : top] as const, ...rows.map(r => [r.id, r.text] as const)]);
  return { head, members: [...new Set([head, ...texts.keys()])], texts };
}

// 異体字.

// Each row of a card's variants lists at most this many, the most attested first; a gallery widens to
// exactly the first row. The Python layer's VARIANTS_SHOWN.
const VARIANTS_SHOWN = 32;
// The tier of predictions, as its own source id: the citation comes from the export's metadata.
const DERIVED_IDS = 'derived-ids';
// The forms made by writing a part as a registered figure, as a source id of their own.
const PART_FIGURES = 'part-figures';
const PART_FIGURES_CITATION = 'Registered figures of a part (derived, not attested): variation sequences of the Unicode Ideographic Variation Database that Noto Sans CJK JP draws, put in a character\'s BabelStone IDS; Unicode License v3, BabelStone IDS PD';
// The rule the answers of `variantsOf` are kept under: raised when they change for the same catalogue.
const VARIANTS_SHAPE = 2;
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
// A substitution of a component, with its count of contexts; a part written as a registered figure has none.
type Substitution = { was: string; became: string; count?: number; pairs: { a: string; b: string; sources: string[] }[] };
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
// The derived forms that write a part of the character as a registered figure, the largest parts first.
// A character described in several ways takes the forms of each. The card stands without them when its
// descriptions cannot be read.
async function figuresOf(env: Env, ctx: ExecutionContext, char: string) {
  if ([...char].length !== 1) return [];
  try { return await readFigures(env, ctx, char) }
  catch (error) { console.log(JSON.stringify({ event: 'part_figures_unread', message: String(error) })); return [] }
}
async function readFigures(env: Env, ctx: ExecutionContext, char: string) {
  const sequences = (await characterIds(env, char, ctx))?.sequences ?? [];
  const parts = [...new Set(sequences.flatMap(partsOf))];
  const described = new Map(await Promise.all(parts.map(async part => [part, (await characterIds(env, part, ctx))?.sequences[0]] as const)));
  return figureForms(sequences, part => described.get(part))
    .map(form => ({ char: form.char, code_point: null, encoded: false, routes: [form.route.map(made => ({ ...made, pairs: [] }))], sources: [PART_FIGURES] }));
}
const sum = (cells: Cell[] | undefined, kind: number) => (cells ?? []).filter(c => c.kind === kind).reduce((n, c) => n + c.n, 0);
/** The forms a family carries through a reviewer's `written_for` claim: shapes Unicode lacks, written as
 *  an IDS, and registered figures, written as a variation sequence (突󠄃), which no character card holds. Each counts the family's crops a person marked with it, here
 *  and in the corpus; a form already on the card as a member or a static variant is not listed twice. */
export const writtenFormsQuery = () => `SELECT c.subject AS form,r.scheme,r.value FROM current_claims c
  JOIN forms f ON f.id=c.subject JOIN representations r ON r.id=f.anchor
  WHERE c.predicate='written_for' AND c.object=? AND c.scope='' AND r.scheme IN ('ids','ivs')
    AND c.status IN ('asserted','accepted','adjudicated') ORDER BY r.value`;
export const writtenFormUseQuery = () => `SELECT c.object AS form,
  sum(CASE WHEN k.kind=0 THEN 1 ELSE 0 END) AS count,sum(CASE WHEN k.kind=1 THEN 1 ELSE 0 END) AS corpus_count
  FROM current_claims c JOIN crops k ON k.id=c.crop LEFT JOIN reviews r ON r.crop=k.id
  WHERE c.predicate='has_form' AND c.scope='' AND c.slot='' AND c.status IN ('asserted','accepted','adjudicated')
    AND c.cut=k.cut+(r.x IS NOT NULL) AND c.object IN (SELECT value FROM json_each(?1))
    AND (k.char IN (SELECT value FROM json_each(?2)) OR k.family IN (SELECT value FROM json_each(?2)))
  GROUP BY c.object`;
async function writtenForms(env: Env, card: Card, rows: Json[]) {
  const head = card.detail.grapheme?.code_point ?? card.detail.code_point;
  const family = await familyOf(env, card);
  if (!head || !family.members.length) return [];
  const linked = (await env.ATLAS.prepare(writtenFormsQuery()).bind(head).all<{ form: string; scheme: string; value: string }>()).results;
  const known = new Set([...(card.detail.grapheme?.members ?? []).map((m: Json) => m.char), ...rows.map(r => r.char)]);
  const fresh = linked.filter(row => !known.has(row.value));
  if (!fresh.length) return [];
  const used = (await env.ATLAS.prepare(writtenFormUseQuery()).bind(JSON.stringify(fresh.map(row => row.form)), JSON.stringify(family.members))
    .all<{ form: string; count: number; corpus_count: number }>()).results;
  const counts = new Map(used.map(row => [row.form, row]));
  return fresh.map(row => ({ char: row.value, code_point: null, form: row.form, scheme: row.scheme,
    count: counts.get(row.form)?.count ?? 0, corpus_count: counts.get(row.form)?.corpus_count ?? 0 }))
    .sort((a, b) => (b.count + b.corpus_count) - (a.count + a.corpus_count) || (a.char < b.char ? -1 : a.char > b.char ? 1 : 0));
}
/** The characters `char` shares an edge with, as the card lists them: `items` a gallery widens to (a
 *  pair with a widening edge and none that keeps it apart), `related` the rest, each most attested first
 *  (sources, then code point), every edge kept; `written` marks a pair one of whose edges says one may
 *  be written for the other. `writes` are the graphemes besides its own a reduced form of several
 *  characters writes (后: 後), `writers` the forms of that kind that write its family head's grapheme,
 *  each `shared` when it writes something else too; `own` is false for a form with no character of its
 *  own. Each row counts the character's crops here and in the corpus. */
async function variantsOf(env: Env, ctx: ExecutionContext, origin: string, card: Card, version: string, claimed: Claimed) {
  const char = card.detail.char, object = card.object;
  return edge(ctx, new Request(`${origin}/layers/variants?c=${encodeURIComponent(char)}&v=${encodeURIComponent(`${VARIANTS_SHAPE}:${TOTALS_SHAPE}:${version}`)}`), async () => {
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
    const derived = object ? [...await figuresOf(env, ctx, char), ...derivedOf(object)] : [];
    const chars = [...shown.map(entry => entry.char), ...derived.filter(entry => entry.encoded).map(entry => entry.char)];
    const ids = new Map(chars.flatMap(c => { const id = card.object && Object.hasOwn(card.object.ids, c) ? card.object.ids[c] : null; return id === null ? [] : [[c, id] as const] }));
    // Characters of another script the site's table gives as writing this grapheme (事: ヿ), and for
    // such a character the grapheme it also writes (data/vocab/written-with.yaml). The reference object
    // names neither, so their rows of `graphemes` are looked up.
    const head = card.detail.grapheme?.char ?? char;
    const lent = WRITERS.filter(w => w.grapheme === head && w.char !== char && !byChar.has(w.char));
    const lends = WRITERS.filter(w => w.char === char && !(object?.writes ?? []).some(([c]) => c === w.grapheme));
    const named = new Map<string, number>(lent.length || lends.length ? (await env.ATLAS.prepare(graphemeIdsQuery())
      .bind(JSON.stringify([...lent.map(w => w.char), ...lends.map(w => w.grapheme)])).all<{ id: number; text: string }>()).results.map(r => [r.text, r.id]) : []);
    for (const w of lent) if (named.has(w.char)) ids.set(w.char, named.get(w.char)!);
    const idOf = (c: string) => gid(card, c) ?? named.get(c) ?? null;
    // The 字種 row's numbers: a grapheme a form writes is a family of its own, counted as one.
    const written = [...(object?.writes ?? []).map(([c]) => c), ...lends.map(w => w.grapheme)];
    const writeIds = new Map(written.flatMap(c => { const id = idOf(c); return id === null ? [] : [[id, c] as const] }));
    // A head may name itself as its family (`familyOf` takes the same care): count each row once.
    const writeFamilies = new Map<number, Map<number, string>>([...writeIds].map(([id, c]) => [id, new Map([[id, c]])]));
    if (writeIds.size) for (const row of (await env.ATLAS.prepare(writeFamiliesQuery()).bind(JSON.stringify([...writeIds.keys()])).all<{ id: number; text: string; head: number }>()).results)
      writeFamilies.get(row.head)?.set(row.id, row.text);
    const members = [...writeFamilies.values()].flatMap(family => [...family.keys()]);
    const [counts, filed, claims] = await Promise.all([characterCounts(env, [...ids.values(), ...members]), filedCounts(env, members),
      members.length ? claimed() : []]);
    const count = (c: string, kind: number) => ids.has(c) ? sum(counts.get(ids.get(c)!), kind) : 0;
    const cited: Record<string, string> = (await siteValue(env, 'variant_sources')) || {};
    const rows = [...shown, ...lent.map(w => ({ char: w.char, code_point: cp(w.char), widens: false, written: true, relations: w.relations }))]
      .map(entry => ({ ...entry, sources: [...new Set(entry.relations.map(r => r.source))].sort(),
        count: count(entry.char, 0), corpus_count: count(entry.char, 1) }));
    const forms = await writtenForms(env, card, rows);
    // A derived form a reviewer said is written for the family is one of its 字体, and is predicted no more.
    const said = new Set(forms.map(form => form.char));
    const predicted = derived.filter(entry => !said.has(entry.char))
      .map(entry => ({ ...entry, count: count(entry.char, 0), corpus_count: count(entry.char, 1) }));
    const used = new Set(rows.flatMap(row => row.sources));
    // The grapheme a character of another script also writes is cited on its own card too.
    for (const w of lends) for (const relation of w.relations) used.add(relation.source);
    for (const entry of predicted) for (const source of entry.sources) used.add(source);
    if (predicted.some(entry => !entry.sources.includes(PART_FIGURES))) used.add(DERIVED_IDS);
    const writes = written.map(c => { const id = idOf(c);
      return { char: c, code_point: cp(c), ...(id === null ? { count: 0, corpus_count: 0 } : familyTotal(familyRows(cp(c), writeFamilies.get(id)!, counts, filed, claims), cp(c))) } });
    // A character of another script has its own grapheme too, so it is a shared form.
    const writers = [...(object?.writers ?? []).map(([c, shared]) => ({ char: c, code_point: cp(c), shared: !!shared })),
      ...lent.map(w => ({ char: w.char, code_point: cp(w.char), shared: true }))];
    const own = !(object?.writes ?? []).some(([, mine]) => mine === 0);
    return { items: rows.filter(row => row.widens), related: rows.filter(row => !row.widens), derived: predicted, forms,
      total: byChar.size + lent.length, writes, writers, own,
      sources: Object.fromEntries([...used].sort().map(source => [source, cited[source] ?? (source === PART_FIGURES ? PART_FIGURES_CITATION : WRITER_SOURCES[source]) ?? source])) };
  });
}

/** A family's own forms (the card's `grapheme.members`), by character, each with the crops filed under
 *  it here and the corpus glyphs recorded for it, and the family's totals: the 字体 row states each
 *  form's images, the 字種 row the grapheme's (`graphemeTotals.ts`), of the members its gallery reads.
 *  `sole` is the form whose page shows the gallery the grapheme's does, if one holds every crop of it.
 *  A member the card's object names has an id already; the rest are looked up in `graphemes`. */
async function familyUsage(env: Env, ctx: ExecutionContext, origin: string, card: Card, version: string, claimed: Claimed) {
  const members: { char: string; code_point: string }[] = card.detail.grapheme?.members ?? [];
  if (!members.length) return null;
  return edge(ctx, new Request(`${origin}/layers/family?c=${encodeURIComponent(card.detail.char)}&v=${encodeURIComponent(`${TOTALS_SHAPE}:${version}`)}`), async () => {
    const known = card.object?.ids ?? {};
    const ids = new Map<string, number>(members.flatMap(({ char }) => Object.hasOwn(known, char) ? [[char, known[char]] as const] : []));
    const missing = members.map(({ char }) => char).filter(char => !ids.has(char));
    const [rest, family, claims] = await Promise.all([
      missing.length ? env.ATLAS.prepare(graphemeIdsQuery()).bind(JSON.stringify(missing)).all<{ id: number; text: string }>().then(r => r.results) : [],
      familyOf(env, card), claimed()]);
    for (const row of rest) ids.set(row.text, row.id);
    const [counts, filed] = await Promise.all([characterCounts(env, [...ids.values(), ...family.members]), filedCounts(env, family.members)]);
    const count = (char: string, kind: number) => ids.has(char) ? sum(counts.get(ids.get(char)!), kind) : 0;
    const forms = Object.fromEntries(members.map(({ char }) => [char, { count: count(char, 0), corpus_count: count(char, 1) }]));
    const key = cp(card.data.grapheme?.char ?? card.data.char);
    const rows = familyRows(key, family.texts, counts, filed, claims), total = familyTotal(rows, key);
    const held = soleForms(rows.values()).get(key), sole = held ? cp(held) : !total.count && !total.corpus_count && members.length === 1 ? members[0].code_point : null;
    return { forms, total, sole };
  });
}
// The shape of the totals an edge copy holds; a change of what they count raises it.
const TOTALS_SHAPE = 2;
// The crops grapheme claims file, read once a request.
type Claimed = () => Promise<FiledAt[]>;
function claimsKept(env: Env, version: string): Claimed {
  let read: Promise<FiledAt[]> | null = null;
  return () => read ??= filedClaimsKept(env, version);
}
/** The rows of a grapheme's family: its characters' crops, those filed under it with no character, and
 *  the claims that file crops into it and away, as its gallery lists them. */
function familyRows(key: string, texts: Map<number, string>, counts: Map<number, Cell[]>, filed: Map<number, Cell[]>, claims: FiledAt[]): Rows {
  const rows: Rows = new Map(), { away, here } = claimsAt(claims, key, [...texts.keys()]);
  for (const [id, label] of texts) for (const kind of [0, 1]) {
    addCrops(rows, { label, grapheme: key }, kind, sum(counts.get(id), kind));
    addCrops(rows, { label: '', grapheme: key }, kind, sum(filed.get(id), kind));
  }
  fileClaims(rows, away, r => ({ label: r.char === null ? '' : texts.get(r.char)!, grapheme: key }));
  fileClaims(rows, here, () => null);
  return rows;
}
function familyTotal(rows: Rows, key: string) {
  const total = { count: 0, corpus_count: 0 };
  for (const row of rows.values()) if (row.grapheme === key) { total.count += row.collected; total.corpus_count += row.corpus }
  return total;
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
type Gallery = { sources: Source[][]; chars: number[]; filed: number[]; away: Filed[]; here: Filed[] };
const NONE: Gallery = { sources: [], chars: [], filed: [], away: [], here: [] };
// Where a gallery's crops are: the character's range; a family's member ranges and its members' filed
// crops, less the crops a grapheme claim files under another family and with those it files under this
// one; or the character and its variants, one after another in code point order. `filedOnly` keeps
// only the crops with no character.
// A request reads its family's claims once, however many of its galleries ask.
const familyClaimsRead = new WeakMap<Card, ReturnType<typeof familyClaims>>();
function claimsOf(env: Env, card: Card, members: number[]) {
  if (!familyClaimsRead.has(card)) familyClaimsRead.set(card, familyClaims(env, cp(card.data.grapheme?.char ?? card.data.char), members));
  return familyClaimsRead.get(card)!;
}
async function galleryOf(env: Env, card: Card, scope: Scope, filedOnly = false): Promise<Gallery> {
  if (scope === 'variants') {
    if (filedOnly) return NONE;
    const chars = writtenVariants(card.object, card.data.char).sort(byCodePoint).flatMap(c => { const id = gid(card, c); return id === null ? [] : [id] });
    return { ...NONE, sources: chars.map(key => [{ by: 'char', key }]), chars };
  }
  if (scope === 'grapheme') {
    const { head, members } = await familyOf(env, card), chars = filedOnly ? [] : members;
    if (head === null) return NONE;
    const claims = await claimsOf(env, card, members);
    const here = claims.here.filter(r => !filedOnly || r.char === null), except = claims.away.map(r => r.id);
    const sources: Source[] = [...chars.map(key => ({ by: 'char' as const, key, except })), ...members.map(key => ({ by: 'family' as const, key, except })),
      ...(here.length ? [{ by: 'claimed' as const, key: head, ids: here.map(r => r.id) }] : [])];
    return { sources: sources.length ? [sources] : [], chars, filed: members, away: claims.away.filter(r => !filedOnly || r.char === null), here };
  }
  const own = gid(card, card.data.char);
  return own === null || filedOnly ? NONE : { ...NONE, sources: [[{ by: 'char', key: own }]], chars: [own] };
}
async function cellsOf(env: Env, g: Gallery, kind: 0 | 1): Promise<Cell[]> {
  return (await allCells(env, g)).filter(cell => cell.kind === kind);
}
async function allCells(env: Env, g: Gallery): Promise<Cell[]> {
  const [chars, filed] = await Promise.all([characterCounts(env, g.chars), g.filed.length ? filedCounts(env, g.filed) : new Map<number, Cell[]>()]);
  return [...[...chars.values()].flat(), ...[...filed.values()].flat(), ...g.away.map(r => filedCell(r, -1)), ...g.here.map(r => filedCell(r, 1))];
}
/** Counts by style group of the crops of a dated gallery, the withheld apart: from the cells where they can answer (no year range, or the crops of no year on the witness
 *  axis), else counted over the range's own index. */
async function datedCounts(env: Env, g: Gallery, q: GalleryQuery, cells: Cell[]): Promise<{ styles: Groups; withheld: Groups }> {
  const fromCells = (more: (cell: Cell) => boolean = () => true) => counted(cells, cell => (q.state == null || cell.state === q.state) && more(cell));
  if (!q.years) return fromCells();
  if (q.years === 'undated' && q.axis !== 'composed') return fromCells(cell => cell.decade === null);
  // The crops of no composed date are those outside every dated book.
  const ranged = q.years === 'undated' ? { ...q, years: null } : q, phase: Phase = q.axis === 'composed' ? 'K' : 'R';
  const statements = g.sources.flat().flatMap(source => {
    const range = rangeCountQuery(ranged, phase, source);
    return range ? [env.ATLAS.prepare(range.sql).bind(...range.values)] : [];
  });
  const found = { styles: none(), withheld: none() };
  if (statements.length) for (const result of await env.ATLAS.batch(statements) as D1Result<{ style: number; quiz: number; n: number }>[])
    for (const row of result.results) (row.quiz === 0 ? found.withheld : found.styles)[groupOf(row.style)] += row.n;
  if (q.years !== 'undated') return found;
  const all = fromCells();
  return { styles: plus(all.styles, found.styles, -1), withheld: plus(all.withheld, found.withheld, -1) };
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
  // The counts by style group are of every crop the gallery can list, withheld ones among them; its total
  // is of those it lists, and `withheld` of those it leaves out.
  const held = dated ? await datedCounts(env, g, query, cells) : c, styles = plus(held.styles, held.withheld), n = total(withheld ? styles : held.styles, group);
  return { ...head, total: n, available: items.length, items, counts: { total: n, exact: n, exact_total: n }, styles,
    style_groups: STYLE_NAMES, scope: q.get('scope') || 'character', status: 'ok',
    withheld: withheld ? null : total(held.withheld, group), ...datedFields(dated), next: page.next };
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
  const chars = new Set(g.chars), filed = new Set(g.filed), away = new Set(g.away.map(r => r.id)), here = new Set(g.here.map(r => r.id)), phases = phasesOf(q);
  const years = q.years && q.years !== 'undated' ? q.years : null, books = new Map(composedBooks(q));
  const placed = rows.filter(r => here.has(r.id) || !away.has(r.id) && (r.char !== null ? chars.has(r.char) : r.family !== null && filed.has(r.family))).flatMap(r => {
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
    const c = dated ? await datedCounts(env, g, query, cells) : counted(cells, () => true);
    styles = plus(c.styles, c.withheld);
  }
  const [items, members, filed] = await Promise.all([listingItems(env, page.ids), characterCounts(env, family.chars), filedCounts(env, family.filed)]);
  const glyphs = (cells: Cell[]) => cells.filter(c => c.kind === 1).reduce((n, c) => n + c.n, 0);
  // The family's glyphs with no character, and with them those a grapheme claim files in or out.
  const claimed = (char: boolean) => [...family.away.filter(r => (r.char !== null) === char).map(r => filedCell(r, -1)),
    ...family.here.filter(r => (r.char !== null) === char).map(r => filedCell(r, 1))];
  const n = total(styles, group), unassigned = glyphs([...[...filed.values()].flat(), ...claimed(false)]);
  return { ...data.candidates, code_point: data.code_point, total: widened ? Math.min(n, WIDENED_CAP) : n, capped: widened && n > WIDENED_CAP,
    available: items.length, items, ...(widened ? {} : { styles }), scope, status: 'ok', visual_analysis: detail.visual_analysis,
    family_total: glyphs([...[...members.values()].flat(), ...claimed(true)]) + unassigned, unassigned_count: unassigned, ...datedFields(dated), next: page.next };
}

// Decades and the time axis.

// The time axis and the books of a gallery are kept at the edge by the publication and the hour: a review
// moves a crop to another decade, book or character there within the hour. The decades of the copies'
// dates stand beside the card, so they are kept by what the card is, and always add up to it.
async function axisVersion(env: Env, now = Date.now()) {
  return `${publicationKey(await catalogueStamps(env, now))}:${Math.floor(now / 3_600_000)}`;
}
const decadeOf = (year: number) => year - (((year % 10) + 10) % 10);
const byDecade = (a: number | null, b: number | null) => a === b ? 0 : a === null ? -1 : b === null ? 1 : a - b;
// One source's crops in the dated books of the composed axis, counted per book along `crop_char_book`
// or `crop_family_book`.
export function composedCountQuery(source: Source, kind: 0 | 1, books: number[]) {
  const { where, values } = sourceWhere(source, kind);
  const index = source.by === 'claimed' ? '' : ` INDEXED BY ${source.by === 'char' ? 'crop_char_book' : 'crop_family_book'}`;
  return { sql: `SELECT book,count(*) AS n FROM crops${index} WHERE ${[...where, ...(source.by === 'char' ? ['kind<2'] : [])].join(' AND ')}`
    + ` AND book IN (${books.join(',')}) GROUP BY book`, values };
}
/** A character's crops (or its family's) counted per decade of its book's date, the collection's and the
 *  corpus's apart, withheld ones among them, with the undated as decade null, sorted as SQLite sorts them (null first). On the
 *  witness axis the decades are the publication's counts and the reviews since; on the composed axis
 *  the crops in the dated books are counted per book and the rest are undated. */
export async function decades(env: Env, ctx: ExecutionContext, url: URL, tools: CharacterTools) {
  const q = url.searchParams, card = await characterCard(env, q.get('code_point') || '', tools, ctx), { data } = card;
  const axis = q.get('axis') === 'composed' ? 'composed' : 'witness', grapheme = q.get('scope') === 'grapheme';
  const version = axis === 'composed' ? await axisVersion(env) : `${(await currentPointer(env)).pub}:${await catalogueVersion(env)}`;
  const key = new Request(`${url.origin}/layers/decades?v=${encodeURIComponent(version)}&c=${encodeURIComponent(data.code_point)}&s=${grapheme}&a=${axis}`);
  return edge(ctx, key, async () => {
    const g = await galleryOf(env, card, grapheme ? 'grapheme' : 'character');
    const years = axis === 'composed' ? await composedYears(env) : new Map<number, number>(), books = [...years.keys()].sort((a, b) => a - b);
    const lists = await Promise.all(([0, 1] as const).map(async kind => {
      const cells = await cellsOf(env, g, kind);
      const by = new Map<number | null, number>(), add = (decade: number | null, n: number) => { if (n) by.set(decade, (by.get(decade) ?? 0) + n) };
      if (axis === 'witness') { for (const cell of cells) add(cell.decade, cell.n) }
      else {
        const statements = books.length ? g.sources.flat().map(source => {
          const { sql, values } = composedCountQuery(source, kind, books);
          return env.ATLAS.prepare(sql).bind(...values);
        }) : [];
        let dated = 0;
        for (const result of statements.length ? await env.ATLAS.batch(statements) as D1Result<{ book: number; n: number }>[] : [])
          for (const row of result.results) { add(decadeOf(years.get(row.book)!), row.n); dated += row.n }
        add(null, cells.reduce((n, c) => n + c.n, 0) - dated);
      }
      return [...by].sort((a, b) => byDecade(a[0], b[0])).map(([decade, n]) => [decade, n]);
    }));
    return { code_point: data.code_point, axis, scope: grapheme ? 'grapheme' : 'character', local: lists[0], corpus: lists[1] };
  });
}

// A gallery grouped by book: the books a character's crops (or its family's) come from, the most crops
// first, each with its first crops, the collection's and then the corpus's. Two records of one copy, a
// corpus's and the collection's, are one book, and the crops whose book no record names come last. The
// records and their counts are kept at the edge as the decades are; the crops are read from the database.
type BookCell = [kind: number, group: StyleGroup, quiz: number, n: number];
type BookRecord = { id: number; key: string; title: string | null; year: number | null; cells: BookCell[] };
const GALLERY_BOOKS = 60, BOOK_CROPS = 60;
/** The key of the crops whose book no record names. */
export const UNRECORDED = 'unrecorded';
// The くずし字データセット of the 東京大学史料編纂所 is published as one record and names no crop's document.
const DATASETS = new Set(['hi:kuzushiji-2023']);
// Records of one copy under unrelated keys, by the record they are kept with: the CODH corpus's of the
// same 国文学研究資料館 書誌ID, and the two みんなで翻刻 entries of NDL 2539136.
const COPIES: Record<string, string> = {
  'hl:48ADDF6AFC3EC1002E472AA1137F87AE': 'codh:100241706', 'hl:B7B8D4E48A5270A5A9660A5790907EFA': 'codh:100249476', 'hl:0C3E70E736947328B7062BED6947A189': 'codh:100249376',
  'hl:0E1D76C249C4E0E52A3D34F9E68389BB': 'codh:100249537', 'hl:1793909603875BE815ED30CF80427250': 'codh:200022050', 'hl:32399C08071CC4F9BEACC53997F08325': 'codh:100249371',
  'hl:7970d9964e0cfeafe30321827a62beae': 'hl:6E29B9B929C04E82EA49008079FA75D8',
};
/** The copy a record is of: みんなで翻刻's entry under either of its prefixes, HNG's text in either of its
 *  sets, the record `COPIES` keeps it with, and any other record's own key. */
export function copyOf({ key, title }: { key: string; title: string | null }): string {
  if (title === null || DATASETS.has(key)) return UNRECORDED;
  if (COPIES[key]) return copyOf({ key: COPIES[key], title });
  const honkoku = /^h[lk]:([0-9a-f]{32})$/i.exec(key), hng = /^hng(?:-kiridashi)?:(.+)$/.exec(key);
  return honkoku ? `honkoku:${honkoku[1].toLowerCase()}` : hng ? `hng:${hng[1]}` : key;
}
const bookIndex = (source: Source) => source.by === 'claimed' ? '' : ` INDEXED BY ${source.by === 'char' ? 'crop_char_book' : 'crop_family_book'}`;
/** One source's crops counted by book, style and withheld flag, with one crop of each to name the book by. */
export function bookCellsQuery(source: Source, kind: 0 | 1) {
  const { where, values } = sourceWhere(source, kind);
  return { sql: `SELECT book,style,quiz,count(*) AS n,min(id) AS sample FROM crops${bookIndex(source)} WHERE ${[...where, ...(source.by === 'char' ? ['kind<2'] : [])].join(' AND ')}`
    + ' GROUP BY book,style,quiz', values };
}
// The books of these crops, and the year the year filter places them by.
export const booksOfQuery = () => `SELECT b.id,b.key,b.title,k.year FROM json_each(?) j CROSS JOIN crops k ON k.id=j.value JOIN books b ON b.id=k.book`;
/** One source's first crops in each of some books, by shuffle and id from `after` on: the rows of the
 *  books' own ranges of the index, at most `limit` a book, and each crop by its key. */
export function bookCropsQuery(source: Source, kind: 0 | 1, books: number[], filter: { group: StyleGroup | null; withheld: boolean }, after: [number, number] | null, limit: number) {
  const found = sourceWhere(source, kind), where = [...found.where, ...(source.by === 'char' ? ['kind<2'] : []), 'book=j.value'], values = [...found.values];
  if (filter.group) {
    const [low, high] = STYLE_GROUPS[filter.group];
    where.push('style>=?'); values.push(low);
    if (high !== null) { where.push('style<=?'); values.push(high) }
  }
  if (!filter.withheld) where.push('quiz=1');
  if (after) { where.push('(shuffle,id)>(?,?)'); values.push(...after) }
  return { sql: `SELECT k.id,k.book,k.shuffle FROM json_each(?) j CROSS JOIN crops k ON k.id IN (SELECT id FROM crops${bookIndex(source)}`
    + ` WHERE ${where.join(' AND ')} ORDER BY shuffle,id LIMIT ?)`, values: [JSON.stringify(books), ...values, limit] };
}
async function bookRecords(env: Env, ctx: ExecutionContext, origin: string, g: Gallery, code_point: string, scope: Scope): Promise<BookRecord[]> {
  const key = new Request(`${origin}/layers/books?v=${encodeURIComponent(await axisVersion(env))}&c=${encodeURIComponent(code_point)}&s=${scope}`);
  return edge(ctx, key, async () => {
    const reads = g.sources.flat().flatMap(source => ([0, 1] as const).map(kind => ({ kind, ...bookCellsQuery(source, kind) })));
    if (!reads.length) return [];
    const results = await env.ATLAS.batch(reads.map(({ sql, values }) => env.ATLAS.prepare(sql).bind(...values))) as D1Result<{ book: number; style: number | null; quiz: number; n: number; sample: number }>[];
    const by = new Map<number, { sample: number; cells: Map<string, BookCell> }>();
    results.forEach((result, i) => {
      for (const row of result.results) {
        const book = by.get(row.book) ?? by.set(row.book, { sample: row.sample, cells: new Map() }).get(row.book)!;
        const group = groupOf(row.style), at = `${reads[i].kind} ${group} ${row.quiz}`, cell = book.cells.get(at);
        if (cell) cell[3] += row.n;
        else book.cells.set(at, [reads[i].kind, group, row.quiz, row.n]);
      }
    });
    const named = await env.ATLAS.prepare(booksOfQuery()).bind(JSON.stringify([...by.values()].map(book => book.sample)))
      .all<{ id: number; key: string; title: string | null; year: number | null }>();
    return named.results.map(row => ({ ...row, cells: [...by.get(row.id)!.cells.values()] }));
  });
}
/** `/layers/books`: a page of the books a gallery's crops come from, as many as hold `limit` crops, each
 *  with its first `per` and the cursor of its next ones; with `document`, the crops of that book from `after` on. */
export async function bookGallery(env: Env, ctx: ExecutionContext, url: URL, tools: CharacterTools) {
  const q = url.searchParams, card = await characterCard(env, q.get('code_point') || '', tools, ctx), { data } = card;
  const scope: Scope = q.get('scope') === 'grapheme' ? 'grapheme' : 'character', group = styleGroup(q), withheld = q.get('withheld') === 'show';
  const per = Math.max(1, integer(q, 'per', 24, BOOK_CROPS)), limit = Math.max(1, integer(q, 'limit', BOOK_CROPS, BOOK_CROPS)), dated = yearOptions(q, fail), years = dated?.years ?? null, document = q.get('document'), after = q.get('after');
  if (dated?.axis === 'composed') throw new Problem(422, 'A gallery by book is narrowed by the dates of the copies.');
  const g = await galleryOf(env, card, scope);
  const styles = { local: none(), corpus: none() }, held = { local: none() };
  const records = (await bookRecords(env, ctx, url.origin, g, data.code_point, scope)).flatMap(record => {
    if (years && (years === 'undated' ? record.year !== null : record.year === null || record.year < years.from || record.year > years.to)) return [];
    const count = { local: 0, corpus: 0 };
    for (const [kind, of, quiz, n] of record.cells) {
      const list = kind === 1 ? 'corpus' : 'local', back = kind === 0 && quiz === 0;
      styles[list][of] += n;
      if (back) held.local[of] += n;
      if ((!group || of === group) && (withheld || !back)) count[list] += n;
    }
    return count.local + count.corpus ? [{ ...record, count, n: count.local + count.corpus }] : [];
  }).sort((a, b) => b.n - a.n || a.id - b.id);
  // A book is named by its record of the most crops, and opens at the one the collection cut crops from.
  const copies = new Map<string, typeof records>();
  for (const record of records) copies.set(copyOf(record), [...copies.get(copyOf(record)) ?? [], record]);
  const found = [...copies].map(([copy, of]) => {
    const recorded = copy !== UNRECORDED, count = { local: of.reduce((n, r) => n + r.count.local, 0), corpus: of.reduce((n, r) => n + r.count.corpus, 0) };
    return { id: of[0].id, recorded, key: recorded ? of[0].key : UNRECORDED, title: recorded ? of[0].title : null, records: of, count, n: count.local + count.corpus,
      page: recorded ? of.find(r => r.count.local)?.key ?? null : null };
  }).sort((a, b) => Number(b.recorded) - Number(a.recorded) || b.n - a.n || a.id - b.id);

  // A book's own pages go by list, shuffle and id; a page of books by their counts and ids.
  let books = found, from: [0 | 1, number, number] | null = null;
  if (document !== null) {
    books = found.filter(book => book.key === document || book.records.some(record => record.key === document));
    if (after) {
      const at = /^([01])\.(\d{1,10})\.([0-9a-z]{1,11})$/.exec(after), crop = at ? cropOf(at[3]) : null;
      if (!at || crop === null) throw new Problem(422, 'Invalid cursor.');
      from = [Number(at[1]) as 0 | 1, Number(at[2]), crop];
    }
  } else if (after) {
    const at = /^(\d{1,10})\.(\d{1,10})$/.exec(after);
    if (!at) throw new Problem(422, 'Invalid cursor.');
    books = found.filter(book => !book.recorded || book.n < Number(at[1]) || (book.n === Number(at[1]) && book.id > Number(at[2])));
  }
  // What is listed, and withheld from it, are the records' as its books are, the hour's. The counts by style
  // are the card's on the first page, where no years narrow them.
  const listed = { local: total(withheld ? styles.local : plus(styles.local, held.local, -1), group), corpus: total(styles.corpus, group) }, back = total(held.local, group);
  if (!years && document === null && !after) {
    const cells = await allCells(env, g);
    for (const kind of [0, 1] as const) { const c = counted(cells, cell => cell.kind === kind); styles[kind ? 'corpus' : 'local'] = plus(c.styles, c.withheld) }
  }
  const page: typeof found = [], rest = books.length;
  for (let crops = 0; page.length < Math.min(rest, GALLERY_BOOKS) && crops < limit; crops += Math.min(per, page[page.length - 1].n)) page.push(books[page.length]);

  type Row = { id: number; book: number; shuffle: number; kind: 0 | 1 };
  const reads = ([0, 1] as const).flatMap(kind => {
    const ids = page.flatMap(book => book.records.filter(record => kind ? record.count.corpus : record.count.local).map(record => record.id));
    if (!ids.length || (from && from[0] > kind)) return [];
    // Of the crops a claim files here, only those of these books are looked up.
    const filed = new Set(ids), here = g.here.filter(r => r.kind === kind && filed.has(r.book)).map(r => r.id);
    return g.sources.flat().map(source => source.by === 'claimed' ? { ...source, ids: here } : source).filter(source => source.by !== 'claimed' || source.ids.length)
      .map(source => ({ kind, ...bookCropsQuery(source, kind, ids, { group, withheld: kind === 1 || withheld }, from && from[0] === kind ? [from[1], from[2]] : null, per + 1) }));
  });
  const rows = new Map<typeof found[number], Row[]>(page.map(book => [book, []])), bookOf = new Map(page.flatMap(book => book.records.map(record => [record.id, book])));
  if (reads.length) (await env.ATLAS.batch(reads.map(({ sql, values }) => env.ATLAS.prepare(sql).bind(...values))) as D1Result<Row>[])
    .forEach((result, i) => { for (const row of result.results) rows.get(bookOf.get(row.book)!)!.push({ ...row, kind: reads[i].kind }) });
  for (const list of rows.values()) list.sort((a, b) => a.kind - b.kind || a.shuffle - b.shuffle || a.id - b.id);
  const shown = [...rows.values()].flatMap(list => list.slice(0, per));
  const items = new Map((await listingItems(env, shown.map(row => row.id))).map(item => [item.id, item]));
  const last = page[page.length - 1];
  return { code_point: data.code_point, scope, per, total: found.length, styles, listed, style_groups: STYLE_NAMES, withheld: withheld ? null : back,
    groups: page.map(book => {
      const list = rows.get(book)!, end = list[per - 1];
      return { document: book.key, records: book.records.map(record => record.key), title: book.title, page: book.page, count: book.count, items: list.slice(0, per).flatMap(row => { const item = items.get(publicId(row.id)); return item ? [{ ...item, origin: row.kind ? 'corpus' : 'collection' }] : [] }),
        next: list.length > per ? `${end.kind}.${end.shuffle}.${publicId(end.id)}` : null };
    }),
    next: document === null && page.length < rest ? `${last.n}.${last.id}` : null };
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
  if (chars.length > FAMILY_MAX || chars.some(c => [...c].length > 4)) throw new Problem(422, `Name at most ${FAMILY_MAX} characters.`);
  return { style: styleGroup(q), production, chars };
}
// A production and every node under it: `printed` takes `printed/woodblock`, compared as text. The
// table holds a few dozen rows.
export const productionsQuery = () => `SELECT id FROM productions WHERE key=? OR substr(key,1,?)=?`;
// Characters by text, through `graphemes`' unique key.
export const graphemeIdsQuery = () => `SELECT id,text FROM graphemes WHERE text IN (SELECT value FROM json_each(?))`;
// A grapheme's family by its head, through `grapheme_head`: the characters a grapheme a form writes holds.
export const writeFamiliesQuery = () => `SELECT id,text,head FROM graphemes WHERE head IN (SELECT value FROM json_each(?))`;
type Ranked = { id: number; decade: number | null; shuffle: number; bucket: number };
const DECADE = (year: string) => `CASE WHEN ${year} IS NULL THEN NULL ELSE (${year} - (((${year} % 10) + 10) % 10)) END`;
/** The statements that read one source's crops for the time axis, the dated and the undated, on either
 *  axis: each decade's first `per` crops by shuffle and id, each with its decade and how many crops the
 *  decade holds. Each ranks the source's crops it reads along its index, so the database sorts them. */
export function chronologyQueries(source: Source, kind: 0 | 1, axis: 'witness' | 'composed',
  filter: { style: StyleGroup | null; productions: number[] | null; withheld: boolean }, composed: [number, number][], per: number) {
  const char = source.by === 'char', claimed = source.by === 'claimed', found = sourceWhere(source, kind);
  const by = [...found.where, ...(char ? ['kind<2'] : [])].join(' AND '), extra: string[] = [], values = [...found.values];
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
    read(`${claimed ? '' : `INDEXED BY ${char ? 'crop_year' : 'crop_family_year'} `}WHERE ${by} AND year IS NOT NULL`, DECADE('year')),
    read(char ? `INDEXED BY ${kind === 0 ? 'crop_undated' : 'crop_undated_corpus'} WHERE ${by} AND kind=${kind} AND year IS NULL` : `WHERE ${by} AND year IS NULL`, 'NULL'),
  ];
  const books = composed.map(([b]) => b).join(',');
  if (!composed.length) return [read(`WHERE ${by}`, 'NULL')];
  return [
    read(`${claimed ? '' : `INDEXED BY ${char ? 'crop_char_book' : 'crop_family_book'} `}WHERE ${by} AND book IN (${books})`,
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

/** The character page (`/layers/characters/:cp`): the card's detail, its 異体字 and words. Its crops
 *  are `/layers/occurrences`, which pages them. */
export async function characterPage(env: Env, ctx: ExecutionContext, url: URL, value: string, tools: CharacterTools) {
  const card = await characterCard(env, value, tools, ctx), { detail } = card;
  const version = await catalogueVersion(env), claimed = claimsKept(env, version);
  // Counts are the publication's and the reviews since: a card built after its generation moved and before
  // its pointer did is the last one's.
  const counted = `${(await currentPointer(env)).pub}:${version}`;
  const [variants, words, usage] = await Promise.all([variantsOf(env, ctx, url.origin, card, counted, claimed),
    wordsOf(env, ctx, url.origin, detail.char, version), familyUsage(env, ctx, url.origin, card, counted, claimed)]);
  // A card with no `members` keeps none: an empty list would read as a family with no forms.
  const listed = detail.grapheme?.members;
  const members = listed && usage ? listed.map((member: Json) => ({ ...member, ...(usage.forms[member.char] ?? {}) })) : listed;
  return { ...detail, ...(listed && usage ? { grapheme: { ...detail.grapheme, members, ...usage.total, sole: usage.sole } } : {}), variants, words, query: detail.code_point };
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
