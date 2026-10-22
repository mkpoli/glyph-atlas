// Old crop URLs on the new storage layout: a text id known at the cutover to its integer crop id, from
// the alias shards in R2 (scripts/atlas3/aliases.py), with no D1 read. Shard
// `v3/aliases/<first three hex digits of the text id's SHA-256>.json.gz` maps each text id to its id.

import { heldShards } from './atlas3Media';

const aliasShard = heldShards<Record<string, number>>();

export async function aliasShardKey(textId: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(textId)));
  const hex = (digest[0] << 4 | digest[1] >> 4).toString(16).padStart(3, '0');
  return `v3/aliases/${hex}.json.gz`;
}

// The crop an old text id names, or null when the alias map does not hold it.
export async function aliasCrop(env: Env, textId: string, ctx?: ExecutionContext, now = Date.now()): Promise<number | null> {
  if (!textId) return null;
  return await aliasShard(env, await aliasShardKey(textId), shard => {
    const id = Object.hasOwn(shard, textId) ? shard[textId] : undefined;
    return typeof id === 'number' ? id : undefined;
  }, ctx, now);
}

// The crops a list of old text ids name, as `aliasCrop` reads each, with one shard read per shard the
// list touches. An id the map does not hold is left out, with no reread of its shard.
export async function aliasCrops(env: Env, textIds: string[], ctx?: ExecutionContext, now = Date.now()): Promise<Map<string, number>> {
  const byShard = new Map<string, string[]>();
  for (const textId of new Set(textIds.filter(Boolean))) {
    const key = await aliasShardKey(textId);
    byShard.set(key, [...byShard.get(key) ?? [], textId]);
  }
  const found = new Map<string, number>();
  await Promise.all([...byShard].map(([key, ids]) => aliasShard(env, key, shard => {
    for (const textId of ids) if (Object.hasOwn(shard, textId) && typeof shard[textId] === 'number') found.set(textId, shard[textId]);
    return true;
  }, ctx, now)));
  return found;
}
