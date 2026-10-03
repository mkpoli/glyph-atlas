import { describe, it, expect } from 'bun:test';
import { described, retried, transient } from './busy';
import worker from './index';

describe('a busy database is told apart from a broken request', () => {
  it('takes the errors D1 documents as retryable for busy', () => {
    for (const message of ['D1_ERROR: D1 DB is overloaded. Requests queued for too long.',
      'D1_ERROR: D1 DB is overloaded. Too many requests queued.',
      'D1_ERROR: Internal error in D1 DB storage caused object to be reset.',
      'D1_ERROR: D1 DB storage operation exceeded timeout which caused object to be reset.',
      'D1_ERROR: Network connection lost.', 'D1 DB reset because its code was updated.',
      'D1_ERROR: Cannot resolve D1 DB due to transient issue on remote node.',
      'SQLITE_BUSY: database is locked'])
      expect(transient(new Error(message))).toBe(true);
  });
  it('reads the errors a failure was caused by', () => {
    expect(transient(new Error('D1_ERROR', { cause: new Error('D1 DB is overloaded. Too many requests queued.') }))).toBe(true);
  });
  it('takes a session Better Auth could not read for busy', () => {
    const failed = Object.assign(new Error('Failed to get session'), { name: 'APIError', statusCode: 500, body: { code: 'FAILED_TO_GET_SESSION' } });
    expect(transient(failed)).toBe(true);
    expect(transient(Object.assign(new Error('Unauthorized'), { name: 'APIError', statusCode: 401, body: { code: 'UNAUTHORIZED' } }))).toBe(false);
  });
  it('leaves a query error and a bug alone', () => {
    expect(transient(new Error('D1_ERROR: no such table: nope: SQLITE_ERROR'))).toBe(false);
    expect(transient(new TypeError("Cannot read properties of undefined (reading 'id')"))).toBe(false);
    expect(transient(undefined)).toBe(false);
  });
  it('logs the message and its cause', () => {
    expect(described(new Error('D1_ERROR', { cause: new Error('overloaded') })))
      .toEqual({ error: 'Error', message: 'D1_ERROR', cause1_error: 'Error', cause1_message: 'overloaded' });
  });
});

describe('a read waits out a busy database within its budget', () => {
  const busy = () => new Error('D1_ERROR: D1 DB is overloaded. Requests queued for too long.');
  const instant = async () => {};
  it('answers once the database is back', async () => {
    let calls = 0;
    const outcome = await retried(async () => { if (++calls < 3) throw busy(); return 'row' }, 4000, instant);
    expect(outcome).toEqual({ value: 'row', attempts: 3 });
  });
  it('gives up on an error that is not busy at once', async () => {
    let calls = 0;
    const outcome = await retried(async () => { calls++; throw new Error('no such table') }, 4000, instant);
    expect(calls).toBe(1);
    expect(outcome.attempts).toBe(1);
  });
  it('gives up once the budget is spent', async () => {
    let calls = 0;
    const outcome = await retried(async () => { calls++; throw busy() }, 1000, ms => new Promise(done => setTimeout(done, ms)));
    expect('error' in outcome).toBe(true);
    expect(calls).toBeGreaterThan(1);
    expect(calls).toBeLessThan(10);
  });
  it('does not retry when there is no budget', async () => {
    let calls = 0;
    await retried(async () => { calls++; throw busy() }, 0, instant);
    expect(calls).toBe(1);
  });
});

describe('the Worker answers a busy database with 503 and a retry time', () => {
  const ctx = { waitUntil() {}, passThroughOnException() {} } as unknown as ExecutionContext;
  // A D1 whose every query fails `failures` times with `error` before answering.
  const database = (error: Error, failures = Infinity) => {
    let calls = 0;
    const statement = { bind: () => statement, first: async () => { if (calls++ < failures) throw error; return { value: '"2026-10-01"' } } };
    return { env: { DB: { prepare: () => statement } } as unknown as Env, calls: () => calls };
  };
  const overloaded = new Error('D1_ERROR: D1 DB is overloaded. Requests queued for too long.');
  it('a read that finds the database back answers as usual', async () => {
    const { env, calls } = database(overloaded, 2);
    const response = await worker.fetch(new Request('https://glyphatlas.org/health'), env, ctx);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, published_at: '2026-10-01' });
    expect(calls()).toBe(3);
  });
  it('a read that keeps finding it busy is answered busy', async () => {
    const { env } = database(overloaded);
    const response = await worker.fetch(new Request('https://glyphatlas.org/health'), env, ctx);
    expect(response.status).toBe(503);
    expect(response.headers.get('retry-after')).toBe('5');
    expect(await response.json()).toEqual({ detail: 'The database is updating. Try again in a moment.', code: 'busy' });
  });
  it('another failure is a 500 that names nothing internal', async () => {
    const { env, calls } = database(new Error('D1_ERROR: no such column: secret: SQLITE_ERROR'));
    const response = await worker.fetch(new Request('https://glyphatlas.org/health'), env, ctx);
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ detail: 'The request could not be completed.', code: 'failed' });
    expect(calls()).toBe(1);
  });
});
