// Character reference data on the new storage layout: the R2 objects scripts/atlas3/reference.py
// writes under `v3/reference/` (decision record 0007), read by key with no D1 read. A shard is the
// first hex digits of the SHA-256 of its key as UTF-8 text: three of the family head's code point(s)
// (`U+3042`) for a summary, two of the character itself (`好`) for the IDS.

import { COMPONENT_PARTS, COMPONENT_SCAN, rankMatches } from './components';
import { heldObjects } from './atlas3Media';

type Json = any;
// [other, relation, source, detail, written, widens], from either end of the edge.
export type VariantEdge = [string, string, string, string, number, number];
// Cards by family head, then code point, as a summary shard holds them.
export type Families = Record<string, Record<string, Json>>;
export type CharacterObject = {
  grapheme: number; data: Json; detail: Json; variants: VariantEdge[]; variant_families: Families;
  derived: [string, [string, string][][]][]; substitutions: [string, string, number, Json][];
  writes: [string, number][]; writers: [string, number][]; ids: Record<string, number>;
};
export const LIST_PAGE = 500;

const PREFIX = 'v3/reference/';
export const SUMMARY_DIGITS = 3, IDS_DIGITS = 2;
// Each kind of object has its own isolate copies, as many as a request of that kind reads: a character
// page one card, a search one reading, the list a component search answers up to 48 families, and the
// component search up to COMPONENT_PARTS lists.
const readCard = heldObjects<Json>(32), readSummary = heldObjects<Json>(64), readIds = heldObjects<Json>(16), readReadings = heldObjects<Json>(16),
  readNames = heldObjects<Json>(1), readList = heldObjects<Json>(4), readComponentNames = heldObjects<Json>(1),
  readComponents = heldObjects<Json>(COMPONENT_PARTS);

export async function referenceShard(key: string, digits: number): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key)));
  return [...digest.slice(0, Math.ceil(digits / 2))].map(b => b.toString(16).padStart(2, '0')).join('').slice(0, digits);
}

// A card's object by its URL key (`U+3042`, or code points joined by spaces); null when no card has it.
export function characterObject(env: Env, codePoint: string, ctx?: ExecutionContext): Promise<CharacterObject | null> {
  return readCard(env, `${PREFIX}char/${codePoint.split(' ').join('-')}.json.gz`, ctx);
}

// Every card of the families, by code point.
export const familyCards = (families: Families) => new Map(Object.values(families).flatMap(cards => Object.entries(cards)));

// The `data` of each card in the families of the given heads, by code point, and no other family's:
// one read per shard.
export async function familySummaries(env: Env, heads: string[], ctx?: ExecutionContext): Promise<Map<string, Json>> {
  const wanted = [...new Set(heads)], keys = await Promise.all(wanted.map(head => referenceShard(head, SUMMARY_DIGITS)));
  const shards = new Map(await Promise.all([...new Set(keys)].map(async key =>
    [key, await readSummary(env, `${PREFIX}summary/${key}.json.gz`, ctx)] as const)));
  return new Map(wanted.flatMap((head, i) => {
    const shard = shards.get(keys[i]);
    return shard && Object.hasOwn(shard, head) ? Object.entries(shard[head] as Record<string, Json>) : [];
  }));
}

// A kana reading's kanji, most attested first, and the families of the first of them, or null.
export async function readingKanji(env: Env, reading: string, ctx?: ExecutionContext): Promise<{ kanji: string[]; families: Families } | null> {
  const shard = await readReadings(env, `${PREFIX}readings/${await referenceShard(reading, SUMMARY_DIGITS)}.json.gz`, ctx);
  return shard && Object.hasOwn(shard, reading) ? shard[reading] : null;
}

// A character's ideographic descriptions and regional shapes, or null.
export async function characterStructure(env: Env, char: string, ctx?: ExecutionContext): Promise<{ sequences: string[]; shapes: Json[] | null } | null> {
  const shard = await readIds(env, `${PREFIX}ids/${await referenceShard(char, IDS_DIGITS)}.json.gz`, ctx);
  const found = shard && Object.hasOwn(shard, char) ? shard[char] : null;
  return found ? { sequences: found[0], shapes: found[1] } : null;
}

// Every card's name: `names` as [code point, name, occurrences], and the names that are a prefix and
// the hex of the code point as [prefix, code points as steps, occurrences].
type NameList = { names: [string, string, number][]; plain: [string, number[], number[]][] };
const hex = (point: number) => point.toString(16).toUpperCase().padStart(4, '0');
export type NameIndex = { occurrences: Map<string, number>; named: (term: string, limit: number) => string[] };

// Upper case for ASCII letters only, the case SQL LIKE ignores.
const asciiUpper = (text: string) => text.replace(/[a-z]/g, c => c.toUpperCase());
const byPoints = (a: number[], b: number[]) => {
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
};
// Each copy of the list the isolate holds is decoded once.
const decodedNames = new WeakMap<NameList, NameIndex>();
function decodeNames(list: NameList): NameIndex {
  const occurrences = new Map<string, number>(), cards: [number[], string, string][] = [];
  for (const [codePoint, name, n] of list.names) {
    occurrences.set(codePoint, n);
    cards.push([codePoint.split(' ').map(p => parseInt(p.slice(2), 16)), codePoint, asciiUpper(name)]);
  }
  for (const [prefix, steps, counts] of list.plain) {
    for (let k = 0, point = 0; k < steps.length; k++) {
      point = k ? point + steps[k] : steps[0];
      const code = hex(point);
      occurrences.set(`U+${code}`, counts[k]);
      cards.push([[point], `U+${code}`, asciiUpper(prefix) + code]);
    }
  }
  cards.sort((a, b) => byPoints(a[0], b[0]));
  // The cards whose name holds the term, ignoring ASCII case as SQL LIKE does, in code point order.
  const named = (term: string, limit: number) => {
    const wanted = asciiUpper(term), found: string[] = [];
    for (const [, codePoint, name] of cards) {
      if (found.length >= limit) break;
      if (name.includes(wanted)) found.push(codePoint);
    }
    return found;
  };
  return { occurrences, named };
}

// Every card by its code point(s) with how often the character occurs, and a search of the names in
// code point order; null when the list is not in R2.
export async function nameIndex(env: Env, ctx?: ExecutionContext): Promise<NameIndex | null> {
  const list = await readNames(env, `${PREFIX}names.json.gz`, ctx) as NameList | null;
  if (!list) return null;
  let index = decodedNames.get(list);
  if (!index) decodedNames.set(list, index = decodeNames(list));
  return index;
}

// One page of a card list from `offset`: at most two objects for a page of up to LIST_PAGE.
export async function cardList(env: Env, list: 'graphemes' | 'ligatures', offset: number, limit: number, ctx?: ExecutionContext) {
  const first = Math.floor(offset / LIST_PAGE), last = Math.floor((offset + Math.max(limit, 1) - 1) / LIST_PAGE);
  const pages = await Promise.all([...new Set([first, last])].map(n => readList(env, `${PREFIX}list/${list}/${n}.json.gz`, ctx)));
  const items = pages.flatMap(page => page?.items ?? []), start = offset - first * LIST_PAGE;
  return { items: items.slice(start, start + limit), total: pages[0]?.total ?? 0 };
}

// A component's list: [tier, size, code points, flags] groups in tier, size, code point order. Each
// code point after a group's first is the step from the one before; a flag is n * 2 + direct.
type ComponentList = { total: number; groups: [number, number, number[], number[]][] };
type ComponentNames = { same: string; other: Record<string, string> };
type Group = { points: number[]; flags: number[] };

const codeKey = (text: string) => [...text].map(c => `U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}`).join('-');

// A list's groups by tier and size, each decoded the first time a lookup reaches it.
function groupsOf(list: ComponentList) {
  const at = new Map(list.groups.map((g, i) => [`${g[0]},${g[1]}`, i]));
  const decoded = new Map<number, Group>();
  return (tier: number, size: number): Group | null => {
    const i = at.get(`${tier},${size}`);
    if (i === undefined) return null;
    let group = decoded.get(i);
    if (!group) {
      const [, , steps, flags] = list.groups[i], points = new Array<number>(steps.length);
      for (let k = 0, p = 0; k < steps.length; k++) points[k] = p = k ? p + steps[k] : steps[0];
      decoded.set(i, group = { points, flags });
    }
    return group;
  };
}

function indexOf(points: number[], point: number): number {
  let lo = 0, hi = points.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid] === point) return mid;
    if (points[mid] < point) lo = mid + 1; else hi = mid - 1;
  }
  return -1;
}

// The component search on the R2 lists, answering as components.ts answers from D1: the rarest
// component's first COMPONENT_SCAN characters, each looked up in the list of every other component.
// A search reads the names object and one list per component, at most COMPONENT_PARTS. The longest
// list, 一, holds 86,700 characters in 429 KB of JSON, 134 KB gzipped; the eight longest together
// are 2.2 MB of JSON. Another list's groups are decoded only when a lookup reaches them.
export async function componentMatches(env: Env, wanted: Map<string, number>, limit: number, ctx?: ExecutionContext) {
  const none = { codes: [] as string[], more: false };
  const names = await readComponentNames(env, `${PREFIX}components/names.json.gz`, ctx) as ComponentNames | null;
  // An index loaded empty answers nothing; say so where the Worker's logs show it.
  if (names && !names.same) console.warn('component index: components/names.json.gz names no component');
  if (!names || wanted.size > COMPONENT_PARTS) return none;
  // Two typed forms of one component (⺡ and 氵) ask for it twice.
  const parts = new Map<string, number>();
  for (const [typed, n] of wanted) {
    const component = Object.hasOwn(names.other, typed) ? names.other[typed] : names.same.includes(typed) ? typed : null;
    if (!component) return none;
    parts.set(component, (parts.get(component) ?? 0) + n);
  }
  const lists = await Promise.all([...parts.keys()].map(c => readComponents(env, `${PREFIX}components/${codeKey(c)}.json.gz`, ctx) as Promise<ComponentList | null>));
  if (lists.some(list => !list)) return none;
  const [driver, ...others] = [...parts.values()].map((n, i) => ({ n, list: lists[i]! })).sort((a, b) => a.list.total - b.list.total);
  const lookups = others.map(other => ({ n: other.n, group: groupsOf(other.list) }));
  const found: { code_point: string; tier: number; size: number; direct: number }[] = [];
  let scanned = 0;
  scan: for (const [tier, size, steps, flags] of driver.list.groups) {
    for (let k = 0, point = 0; k < steps.length; k++) {
      if (scanned++ >= COMPONENT_SCAN) break scan;
      point = k ? point + steps[k] : steps[0];
      if (flags[k] >> 1 < driver.n) continue;
      let direct = flags[k] & 1, held = true;
      for (const other of lookups) {
        const group = other.group(tier, size), at = group ? indexOf(group.points, point) : -1;
        if (at < 0 || group!.flags[at] >> 1 < other.n) { held = false; break; }
        direct += group!.flags[at] & 1;
      }
      if (held) found.push({ code_point: `U+${point.toString(16).toUpperCase().padStart(4, '0')}`, tier, size, direct });
    }
  }
  const ranked = rankMatches(found);
  return { codes: ranked.slice(0, limit).map(row => row.code_point), more: ranked.length > limit || driver.list.total > COMPONENT_SCAN };
}
