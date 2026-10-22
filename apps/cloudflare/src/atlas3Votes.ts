// Model votes on the new storage layout: one object per page, `v3/votes/<build>/<page>.json.gz`
// (scripts/atlas3/votes.py), holding each slot's readings and the cut they were made on. A reading of
// another cut than the crop's is not answered: the crop was re-cut since, and its next publish reads it again.
// An object replaced by a publish shows within about ten minutes (the edge and the isolate each keep it five).

import { pageOf, slotOf } from './atlas3';
import { heldShards } from './atlas3Media';

type Json = any;
export type StoredVotes = { cut: number; models: number | null; loaded?: true; visual?: Json; context?: Json };
const votesObject = heldShards<Record<string, StoredVotes>>();
// Objects sit under the build that numbered the crops, so a rebuilt layout never reads another build's.
export const votesKey = (build: string, page: number) => `v3/votes/${build}/${page}.json.gz`;
const CONTEXT_ENGINE = 'Nearby transcription';

// The layout's build id (`codes` field 'build'); an isolate reads it again after BUILD_HELD milliseconds.
const BUILD_HELD = 60_000;
let build: { at: number; read: Promise<string> } | null = null;
export function layoutBuild(env: Env, now = Date.now()): Promise<string> {
  if (build && now - build.at < BUILD_HELD) return build.read;
  const read = env.ATLAS.prepare(`SELECT key FROM codes WHERE field='build' AND code=0`).first<{ key: string }>().then(row => {
    if (!row) throw new Error('glyph-atlas-v3 names no build');
    return row.key;
  });
  const held = { at: now, read };
  read.catch(() => { if (build === held) build = null; });
  build = held;
  return read;
}

// The crop's readings when they were made on its cut `cut` (its `crops.cut`), else null.
export async function cropVotes(env: Env, id: number, cut: number, ctx?: ExecutionContext, now = Date.now()): Promise<StoredVotes | null> {
  const slot = String(slotOf(id));
  const found = await votesObject(env, votesKey(await layoutBuild(env, now), pageOf(id)),
    page => Object.hasOwn(page, slot) ? page[slot] : undefined, ctx, now);
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
