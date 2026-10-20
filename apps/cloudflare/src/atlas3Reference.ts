// Character reference data on the new storage layout: the R2 objects scripts/atlas3/reference.py
// writes under `v3/reference/` (decision record 0007), read by key with no D1 read. A shard is the
// first two hex digits of the SHA-256 of its key's code point(s) as text.

import { COMPONENT_PARTS, COMPONENT_SCAN, rankMatches } from './components';
import { heldShards } from './atlas3Media';

type Json = any;
// [other, relation, source, detail, written, widens], from either end of the edge.
export type VariantEdge = [string, string, string, string, number, number];
export type CharacterObject = {
  grapheme: number; data: Json; detail: Json; variants: VariantEdge[];
  derived: [string, [string, string][][]][]; substitutions: [string, string, number, Json][];
  writes: [string, number][]; writers: [string, number][]; ids: Record<string, number>;
};
export const LIST_PAGE = 500;

const PREFIX = 'v3/reference/';
const read = heldShards<Json>();

export async function referenceShard(key: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key)));
  return digest[0].toString(16).padStart(2, '0');
}

// A card's object by its URL key (`U+3042`, or code points joined by spaces); null when no card has it.
export function characterObject(env: Env, codePoint: string, ctx?: ExecutionContext): Promise<CharacterObject | null> {
  return read(env, `${PREFIX}char/${codePoint.split(' ').join('-')}.json.gz`, ctx);
}

// The `data` of each card in the families of the given heads, by code point: one read per shard.
export async function familySummaries(env: Env, heads: string[], ctx?: ExecutionContext): Promise<Map<string, Json>> {
  const shards = [...new Set(await Promise.all(heads.map(referenceShard)))];
  const found = await Promise.all(shards.map(key => read(env, `${PREFIX}summary/${key}.json.gz`, ctx)));
  return new Map(found.flatMap(shard => Object.entries(shard ?? {})));
}

// A character's ideographic descriptions and regional shapes, or null.
export async function characterStructure(env: Env, char: string, ctx?: ExecutionContext): Promise<{ sequences: string[]; shapes: Json[] | null } | null> {
  const shard = await read(env, `${PREFIX}ids/${await referenceShard(char)}.json.gz`, ctx);
  const found = shard && Object.hasOwn(shard, char) ? shard[char] : null;
  return found ? { sequences: found[0], shapes: found[1] } : null;
}

// [code point, name] of every card whose name is more than `…IDEOGRAPH-<hex>`.
export async function characterNames(env: Env, ctx?: ExecutionContext): Promise<[string, string][]> {
  return await read(env, `${PREFIX}names.json.gz`, ctx) ?? [];
}

// One page of a card list from `offset`: at most two objects for a page of up to LIST_PAGE.
export async function cardList(env: Env, list: 'graphemes' | 'ligatures', offset: number, limit: number, ctx?: ExecutionContext) {
  const first = Math.floor(offset / LIST_PAGE), last = Math.floor((offset + Math.max(limit, 1) - 1) / LIST_PAGE);
  const pages = await Promise.all([...new Set([first, last])].map(n => read(env, `${PREFIX}list/${list}/${n}.json.gz`, ctx)));
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
// A search reads the names object and one list per component, at most COMPONENT_PARTS; the longest
// list, 一, holds 86,700 characters.
export async function componentMatches(env: Env, wanted: Map<string, number>, limit: number, ctx?: ExecutionContext) {
  const none = { codes: [] as string[], more: false };
  const names = await read(env, `${PREFIX}components/names.json.gz`, ctx) as ComponentNames | null;
  if (!names || wanted.size > COMPONENT_PARTS) return none;
  // Two typed forms of one component (⺡ and 氵) ask for it twice.
  const parts = new Map<string, number>();
  for (const [typed, n] of wanted) {
    const component = Object.hasOwn(names.other, typed) ? names.other[typed] : names.same.includes(typed) ? typed : null;
    if (!component) return none;
    parts.set(component, (parts.get(component) ?? 0) + n);
  }
  const lists = await Promise.all([...parts.keys()].map(c => read(env, `${PREFIX}components/${codeKey(c)}.json.gz`, ctx) as Promise<ComponentList | null>));
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
