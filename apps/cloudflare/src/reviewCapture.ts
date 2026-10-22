import { literal } from './input';
import { pageOf } from './atlas3';
import { Problem } from './busy';
import { readLegacyReviews, readReviewEvidence, storeReviewEvidence } from './reviewEvidence';
import type { Current, CropChange } from './routes/review';

type Json = Record<string, any>;
const codePoints = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const INTERNAL = new Set(['slot', 'record_place', 'image_place', 'context_image_place']);

/** `kept` is the issue a batch confirmation leaves the crop flagged with. */
export type ReviewedInput = { row: Current; change: CropChange; answer: Json; kept?: string | null };

/** A collected crop's publication baseline, the record its reviews are imported from:
 *  - after a publication spoke for the crop (`review_revisions`): its record in the page bundle it was read
 *    from, at the store's revision that publication recorded;
 *  - else, when the old site reviewed it: the old site's publication record, as the moved reviews carry it,
 *    so the store imports those reviews and these in one chain;
 *  - else its bundle record at its revision when the first review under that record was made, which every
 *    later review under the same record keeps. */
export function baseline(row: Current, found: Baselines): Json {
  const character = { ...Object.fromEntries(Object.entries(row.record).filter(([key]) => !INTERNAL.has(key))),
    id: row.sourceId, ...(row.data.page_id ? { page_id: row.data.page_id } : {}) };
  if (row.anchored !== null) return { character: { ...character, revision: row.anchored } };
  const legacy = found.legacy.get(row.id);
  if (legacy) return legacy;
  const prior = found.prior.get(row.id);
  const { revision: _revision, ...before } = prior?.character ?? {};
  const same = prior && JSON.stringify(before) === JSON.stringify(character);
  return { character: { ...character, revision: same ? prior.character.revision : row.logicalRevision } };
}

export type Baselines = { prior: Map<number, Json>; legacy: Map<number, Json> };
/** For each crop, the baseline of its latest review saved here with its evidence, and the old site's
 *  publication record when the old site reviewed it. Each object is read once. An object that exists but
 *  cannot be read, or a moved review whose page has no archive, refuses the save with a 503 to retry,
 *  since a baseline frozen without it would be wrong for good; a crop with neither takes its bundle record. */
export async function priorBaselines(env: Env, crops: number[]): Promise<Baselines> {
  const found: Baselines = { prior: new Map(), legacy: new Map() };
  if (!crops.length) return found;
  const [native, moved] = await env.ATLAS.batch<{ crop: number; source: string }>([
    env.ATLAS.prepare(`SELECT j.value AS crop,(SELECT t.evidence FROM events f JOIN submissions t ON t.id=f.submission
      WHERE f.crop=j.value AND f.kind IN (0,1,2) AND f.legacy IS NULL AND t.evidence IS NOT NULL ORDER BY f.revision DESC LIMIT 1) AS source
      FROM json_each(?) j`).bind(JSON.stringify(crops)),
    env.ATLAS.prepare(`SELECT j.value AS crop,(SELECT f.legacy FROM events f WHERE f.crop=j.value AND f.legacy IS NOT NULL
      AND f.kind IN (0,1,2) ORDER BY f.revision DESC LIMIT 1) AS source FROM json_each(?) j`).bind(JSON.stringify(crops)),
  ]);
  const hashes = [...new Set(native.results.filter(r => r.source).map(r => r.source))];
  const objects = new Map(await Promise.all(hashes.map(async hash => [hash, await readReviewEvidence(env, hash)] as const)));
  for (const r of native.results) {
    const prior = r.source ? objects.get(r.source)?.[`crop:${r.crop}`]?.publication_snapshot : undefined;
    if (prior) found.prior.set(r.crop, prior);
  }
  const pages = [...new Set(moved.results.filter(r => r.source).map(r => pageOf(r.crop)))];
  const archives = new Map(await Promise.all(pages.map(async page => [page, await readLegacyReviews(env, page)] as const)));
  for (const r of moved.results) {
    if (!r.source) continue;
    const archive = archives.get(pageOf(r.crop));
    if (!archive) throw new Problem(503, 'The reviews moved from the old site are not archived yet.');
    const snapshot = archive[`legacy:${r.source}`]?.publication_snapshot;
    if (snapshot?.character && typeof snapshot.character === 'object') found.legacy.set(r.crop, snapshot);
  }
  return found;
}

/** Freeze the validated view before the transaction. The change's revision, cut and page guards
 *  ensure the evidence still describes the crop when its event commits. */
export async function captureReviews(env: Env, inputs: ReviewedInput[], sent: Json, mode: string | undefined, user: string, at: number,
  priors: Baselines): Promise<string | null> {
  if (!inputs.length) return null;
  const records: Record<string, Json> = {};
  for (const { row, change, answer, kept } of inputs) {
    const character: Json = { ...Object.fromEntries(Object.entries(row.data).filter(([key]) => !INTERNAL.has(key))),
      id: row.sourceId, revision: row.logicalRevision };
    const snapshot = { character, image_sha256: character.image_sha256 ?? null };
    const translated = { ...answer, id: row.sourceId, revision: row.logicalRevision };
    const round = mode === undefined, batch = mode === '@batch';
    const evidence = { kind: round ? 'visual-quiz' : 'character-review',
      ...(round ? { round: sent.id, grapheme: sent.grapheme, label: row.label, answer: translated }
        : batch ? { batch: sent.id, answer: translated } : { request: { ...sent, ...answer, id: sent.id, revision: row.logicalRevision,
          ...(mode === '@corpus' ? { identity: row.sourceId } : {}) } }),
      verdict: answer.verdict, issue: answer.issue ?? null, note: answer.note ?? '', ...(kept ? { kept_issue: kept } : {}),
      suggested_character: change.named ? codePoints(change.named) : null,
      suggested_text: change.text ?? null, snapshot,
      correction: { unicode: change.label ? codePoints(change.label) : null, box: answer.box ?? character.box ?? null },
      ...(answer.box ? { recrop: { from: character.box, to: answer.box, pixels: character.image_sha256 ?? null } } : {}) };
    const event = { target_type: 'unit', target_id: row.sourceId, field: 'review',
      old: row.state === 1 ? 'reviewed' : row.state === 2 ? 'disputed' : 'machine',
      new: change.state === 1 ? 'reviewed' : 'disputed', role: 'reviewer', actor: user,
      evidence: JSON.stringify(evidence), at: new Date(at).toISOString() };
    if (row.kind === 1) {
      records[`crop:${row.id}`] = { origin: 'corpus', event: { ...event, new: { verdict: answer.verdict,
        issue: answer.issue ?? null, character: change.named ?? null, correction: change.text ?? null, note: answer.note ?? '' } },
        reviewed: character, source_update: { identity: row.sourceId, source_revision: character.source_revision,
          source: character.source, box: character.box,
          original_character: character.source_code_point ? literal(character.source_code_point) : character.source_label,
          proposed_character: change.named ?? null, issue: answer.issue ?? null, applied_upstream: false } };
    } else {
      records[`crop:${row.id}`] = { event, reviewed: snapshot, expected_revision: row.logicalRevision,
        publication_snapshot: baseline(row, priors) };
    }
  }
  return storeReviewEvidence(env, records);
}
