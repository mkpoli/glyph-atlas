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
