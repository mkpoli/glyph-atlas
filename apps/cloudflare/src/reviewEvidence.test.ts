import { describe, expect, it } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { evidenceKey, legacyKey, readLegacyReviews, readReviewEvidence, storeReviewEvidence } from './reviewEvidence';

function bucket() {
  const objects = new Map<string, ArrayBuffer>();
  return { objects, env: { MEDIA: {
    put: async (key: string, bytes: ArrayBuffer) => { objects.set(key, bytes); },
    get: async (key: string) => objects.has(key) ? { body: new Response(objects.get(key)).body! } : null,
  } } as unknown as Env };
}

describe('immutable review evidence', () => {
  it('distinguishes concurrent attempts with the same submission key by their full content', async () => {
    const { env } = bucket();
    const first = { 'crop:17': { event: { evidence: '{ "note": "first" }' }, reviewed: { revision: 2 } } };
    const second = { 'crop:17': { event: { evidence: '{ "note": "second" }' }, reviewed: { revision: 3 } } };
    const [a, b] = await Promise.all([storeReviewEvidence(env, first), storeReviewEvidence(env, second)]);
    expect(a).not.toBe(b);
    expect(await storeReviewEvidence(env, first)).toBe(a);
    expect(await readReviewEvidence(env, a)).toEqual(first);
    expect(await readReviewEvidence(env, b)).toEqual(second);
  });

  it('fails on a missing or corrupted object', async () => {
    const { env, objects } = bucket();
    await expect(readReviewEvidence(env, '0'.repeat(64))).rejects.toThrow('unavailable');
    const hash = await storeReviewEvidence(env, {});
    const corrupt = gzipSync('{"version":1,"reviews":{"lost":{}}}');
    objects.set(evidenceKey(hash), corrupt.buffer.slice(corrupt.byteOffset, corrupt.byteOffset + corrupt.byteLength));
    await expect(readReviewEvidence(env, hash)).rejects.toThrow('integrity');
  });

  it('answers 503 for bytes that do not unzip, and for an archive that holds no object', async () => {
    const { env, objects } = bucket();
    const hash = await storeReviewEvidence(env, {});
    objects.set(evidenceKey(hash), new TextEncoder().encode('not gzip').buffer as ArrayBuffer);
    await expect(readReviewEvidence(env, hash)).rejects.toMatchObject({ status: 503 });
    const empty = gzipSync('null');
    objects.set(legacyKey(3), empty.buffer.slice(empty.byteOffset, empty.byteOffset + empty.byteLength));
    await expect(readLegacyReviews(env, 3)).rejects.toMatchObject({ status: 503 });
    objects.set(legacyKey(4), new TextEncoder().encode('not gzip').buffer as ArrayBuffer);
    await expect(readLegacyReviews(env, 4)).rejects.toMatchObject({ status: 503 });
  });

  it('does not return a reference when R2 refuses the write', async () => {
    const env = { MEDIA: { put: async () => { throw new Error('R2 unavailable'); } } } as unknown as Env;
    await expect(storeReviewEvidence(env, {})).rejects.toThrow('R2 unavailable');
  });
});
