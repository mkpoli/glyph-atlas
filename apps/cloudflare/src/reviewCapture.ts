import { literal } from './input';
import { readReviewEvidence, storeReviewEvidence } from './reviewEvidence';
import type { Current, CropChange } from './routes/review';

type Json = Record<string, any>;
const codePoints = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const INTERNAL = new Set(['slot', 'record_place', 'image_place', 'context_image_place']);

export type ReviewedInput = { row: Current; change: CropChange; answer: Json };

/** A collected crop's publication baseline: its record in the page bundle it was read from, as the
 *  offline store published it. Its revision is the crop's revision as that store counts it when the
 *  first review under this record was made; every later review under the same record keeps it, so a
 *  chain of reviews shares one baseline. */
export function baseline(row: Current, prior: Json | undefined): Json {
  const character = { ...Object.fromEntries(Object.entries(row.record).filter(([key]) => !INTERNAL.has(key))),
    id: row.sourceId, page_id: row.data.page_id };
  const { revision: _revision, ...before } = prior?.character ?? {};
  return { character: { ...character, revision: prior && JSON.stringify(before) === JSON.stringify(character)
    ? prior.character.revision : row.logicalRevision } };
}

/** The baseline of each crop's latest review saved on this site with its evidence, by crop. */
export async function priorBaselines(env: Env, crops: number[]): Promise<Map<number, Json>> {
  if (!crops.length) return new Map();
  const rows = (await env.ATLAS.prepare(`SELECT e.crop,s.evidence FROM json_each(?) j
    JOIN events e ON e.id=(SELECT f.id FROM events f JOIN submissions t ON t.id=f.submission
      WHERE f.crop=j.value AND f.kind IN (0,1,2) AND f.legacy IS NULL AND t.evidence IS NOT NULL ORDER BY f.revision DESC LIMIT 1)
    JOIN submissions s ON s.id=e.submission`).bind(JSON.stringify(crops)).all<{ crop: number; evidence: string }>()).results;
  const found = new Map<number, Json>();
  await Promise.all(rows.map(async row => {
    const prior = (await readReviewEvidence(env, row.evidence))[`crop:${row.crop}`]?.publication_snapshot;
    if (prior) found.set(row.crop, prior);
  }));
  return found;
}

/** Freeze the validated view before the transaction. The change's revision, cut and page guards
 *  ensure the evidence still describes the crop when its event commits. */
export async function captureReviews(env: Env, inputs: ReviewedInput[], sent: Json, mode: string | undefined, user: string, at: number,
  priors: Map<number, Json>): Promise<string | null> {
  if (!inputs.length) return null;
  const records: Record<string, Json> = {};
  for (const { row, change, answer } of inputs) {
    const character: Json = { ...Object.fromEntries(Object.entries(row.data).filter(([key]) => !INTERNAL.has(key))),
      id: row.sourceId, revision: row.logicalRevision };
    const snapshot = { character, image_sha256: character.image_sha256 ?? null };
    const translated = { ...answer, id: row.sourceId, revision: row.logicalRevision };
    const round = mode === undefined, batch = mode === '@batch';
    const evidence = { kind: round ? 'visual-quiz' : 'character-review',
      ...(round ? { round: sent.id, grapheme: sent.grapheme, label: row.label, answer: translated }
        : batch ? { batch: sent.id } : { request: { ...sent, ...answer, id: sent.id, revision: row.logicalRevision,
          ...(mode === '@corpus' ? { identity: row.sourceId } : {}) } }),
      verdict: answer.verdict, issue: answer.issue ?? null, note: answer.note ?? '',
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
        publication_snapshot: baseline(row, priors.get(row.id)) };
    }
  }
  return storeReviewEvidence(env, records);
}
