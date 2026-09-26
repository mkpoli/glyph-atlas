import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { formed, leastTypicalQuery } from './forms';

describe('form decisions outside cluster membership', () => {
  it('keeps the corrected identity without linking to a removed cluster', () => {
    const result = formed({ id: 'u', label: 'は', form_cluster: { id: 'old' } }, {
      id: 'u', cluster: 'old', clustered: 0, form: null, glyph_set: 1, cluster_form: null,
      issue: 'character', issue_character: '川', issue_family: 'U+5DDD',
    }, { codePoints: () => 'U+5DDD' });
    expect(result.label).toBe('川');
    expect(result.grapheme).toBe('U+5DDD');
    expect(result.form_cluster).toBeUndefined();
    expect(result.form_decision.basis).toBe('form_glyph');
  });

  it('does not offer excluded glyphs among a surviving cluster’s outliers', () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort())
      db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    db.exec(`INSERT INTO form_clusters(id,family,label,count,coherence,shape_position,size_position,representatives)
      VALUES('c','U+306F','c',1,1,0,0,'[]');
      INSERT INTO form_units(id,family,cluster,rank,similarity,split,clustered) VALUES
        ('kept','U+306F','c',12,0.8,'2222222',1),('excluded','U+306F','c',13,0.7,'',0);`);
    expect(db.query(leastTypicalQuery()).all('U+306F')).toEqual([
      { cluster: 'c', id: 'kept', image: null, rank: 12 },
    ]);
    db.close();
  });
});
