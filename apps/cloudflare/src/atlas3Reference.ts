// Character reference data on the new storage layout: the R2 objects scripts/atlas3/reference.py
// writes under `v3/reference/` (decision record 0007), read by key with no D1 read. A shard is the
// first two hex digits of the SHA-256 of its key's code point(s) as text.

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
