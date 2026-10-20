// Model votes on the new storage layout: one object per page, `v3/votes/<page>.json.gz`
// (scripts/atlas3/votes.py), holding each slot's readings and the cut they were made on. A reading of
// another cut than the crop's is not answered: the crop was re-cut since, and its next publish reads it again.

import { pageOf, slotOf } from './atlas3';
import { heldShards } from './atlas3Media';

type Json = any;
export type StoredVotes = { cut: number; models: number | null; visual?: Json; context?: Json };
const votesObject = heldShards<Record<string, StoredVotes>>();
export const votesKey = (page: number) => `v3/votes/${page}.json.gz`;
const CONTEXT_ENGINE = 'Nearby transcription';

// The crop's readings when they were made on its cut `cut` (its `crops.cut`), else null.
export async function cropVotes(env: Env, id: number, cut: number, ctx?: ExecutionContext, now = Date.now()): Promise<StoredVotes | null> {
  const page = await votesObject(env, votesKey(pageOf(id)), ctx, now), slot = String(slotOf(id));
  const found = page && Object.hasOwn(page, slot) ? page[slot] : null;
  return found && found.cut === cut ? found : null;
}

// What the suggestions routes answer from a crop's readings (null for none) and the engines its
// `models` code names, as votes.py's `restore`.
export function restoreVotes(engines: { name: string }[] | null, found: StoredVotes | null) {
  const unavailable = { status: 'unavailable', candidates: [] as Json[] };
  const out: { visual: Json; context: Json } = { visual: unavailable, context: { ...unavailable, basis: 'transcription' } };
  if (found?.visual && engines) {
    const names = engines.map(e => e.name);
    const named = (items: Json[]) => items.map(c => typeof c.engine === 'number' ? { ...c, engine: names[c.engine] } : c);
    out.visual = { ...found.visual, status: 'ready', engines, verified: false,
      candidates: named(found.visual.candidates), votes: named(found.visual.votes) };
  }
  if (found?.context) out.context = { ...found.context, status: 'ready', basis: 'transcription',
    candidates: found.context.candidates.map(([text, offset, before, after]: Json[]) =>
      ({ text, engine: CONTEXT_ENGINE, offset, before, after })) };
  return out;
}
