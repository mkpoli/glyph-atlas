import { describe, expect, it } from 'bun:test';
import type { Database } from 'bun:sqlite';
import { formed } from './forms';

// The shared D1 fixtures read R2 directly unless a test supplies an edge cache.
(globalThis as any).caches = { default: { match: async () => undefined, put: async () => {} } };

describe('form decisions outside cluster membership', () => {
  it('keeps the corrected identity without linking to a removed cluster', () => {
    const result = formed({ id: 'u', label: 'は', form_cluster: { id: 'old' } }, {
      id: 'u', cluster: 'old', clustered: 0, form: null, glyph_set: 1, cluster_form: null,
      issue: 'character', issue_character: '川', written_family: 'U+5DDD',
    }, { codePoints: () => 'U+5DDD' });
    expect(result.label).toBe('川');
    expect(result.grapheme).toBe('U+5DDD');
    expect(result.form_cluster).toBeUndefined();
    expect(result.form_decision.basis).toBe('form_glyph');
  });
});

// D1's prepare/bind/first/run/batch over bun:sqlite, a batch in one transaction as D1 runs it.
export function d1(db: Database) {
  const statement = (sql: string, args: unknown[] = []) => ({
    sql, args,
    bind: (...values: unknown[]) => statement(sql, values),
    first: async () => db.query(sql).get(...(args as any[])) ?? null,
    all: async () => ({ results: db.query(sql).all(...(args as any[])) }),
    run: async () => ({ meta: { changes: db.query(sql).run(...(args as any[])).changes } }),
  });
  return {
    // D1 refuses a compound SELECT of more than five terms (`too many terms in compound SELECT`).
    prepare: (sql: string) => {
      if ((sql.match(/\bUNION\b/gi) ?? []).length > 4) throw new Error('D1_ERROR: too many terms in compound SELECT: SQLITE_ERROR');
      return statement(sql);
    },
    batch: async (list: ReturnType<typeof statement>[]) => db.transaction(() => list.map(s => /^\s*SELECT/i.test(s.sql)
      ? { results: db.query(s.sql).all(...(s.args as any[])), meta: { changes: 0 } }
      : { results: [], meta: { changes: db.query(s.sql).run(...(s.args as any[])).changes } }))(),
  };
}
