// Test bridge: exercise the Worker's actual evidence capture against the offline importer.
import { publicId } from '../../atlas3';
import { captureReviews } from '../../reviewCapture';
import { readReviewEvidence } from '../../reviewEvidence';
import type { Current, CropChange } from '../review';

const input = JSON.parse(await Bun.stdin.text());
const objects = new Map<string, ArrayBuffer>();
const env = { MEDIA: {
  put: async (key: string, bytes: ArrayBuffer) => { objects.set(key, bytes); },
  get: async (key: string) => objects.has(key) ? { body: new Response(objects.get(key)).body! } : null,
} } as unknown as Env;
const crop = 16777217, reviews = [], priors = { prior: new Map<number, Record<string, any>>(), legacy: new Map<number, Record<string, any>>() };
// An old-site publication record the moved reviews of the crop carry.
if (input.legacy) priors.legacy.set(crop, input.legacy);
for (const [index, source] of input.records.entries()) {
  const old = source.before, answer = { ...source.answer, id: publicId(crop), revision: 20 + old.revision };
  // The crop's record in its page bundle is the publication the offline store made.
  const { revision: _revision, page_id: _page, ...record } = input.publication.character;
  const row: Current = { id: crop, sourceId: old.id, pageRevision: 1, record: { slot: 1, ...record }, anchored: source.anchored ?? null, boundary: null,
    kind: input.origin === 'corpus' ? 1 : 0, char: 1, label: old.label, state: index ? 1 : 0, revision: 20 + old.revision, logicalRevision: old.revision,
    cut: 0, quiz: 1, cutBox: old.box, settled: 20 + old.revision, data: { ...old, id: publicId(crop), page_id: input.publication.character.page_id, revision: 20 + old.revision } };
  const change: CropChange = { crop, expected: row.revision, cut: 0, kind: 1,
    label: answer.character ?? old.label, named: answer.character ?? null,
    state: !source.kept && (answer.verdict === 'match' || (answer.issue === 'character' && answer.character)) ? 1 : 2, issue: answer.issue === 'character' ? 0 : 2 };
  const sent = input.mode === 'round' ? { id: source.id, grapheme: 'U+624B', answers: [answer] }
    : input.mode === 'batch' ? { id: source.id, character: answer.character, crops: [answer] }
    : { ...answer, id: source.id, client_id: 'reviewer-test' };
  const mode = input.mode === 'round' ? undefined : input.mode === 'batch' ? '@batch' : input.origin === 'corpus' ? '@corpus' : publicId(crop);
  const hash = await captureReviews(env, [{ row, change, answer, kept: source.kept ?? null }], sent, mode, 'reviewer-test', 1790000000000 + index, priors);
  const stored = (await readReviewEvidence(env, hash!))[`crop:${crop}`];
  if (stored.publication_snapshot) priors.prior.set(crop, stored.publication_snapshot);
  reviews.push({ ...stored, event: { ...stored.event, id: `v3:${(input.start ?? 1) + index}` }, crop: publicId(crop),
    current: index === input.records.length - 1, current_revision: input.records.at(-1).before.revision + 1 });
}
process.stdout.write(JSON.stringify({ version: 1, kind: 'atlas-character-reviews', publication: 'fixture-publication', reviews }));
