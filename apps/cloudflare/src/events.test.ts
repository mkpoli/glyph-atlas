import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
const record = (fields: Record<string, unknown>) => JSON.stringify({ id: 'u', label: 'あ', state: 'pending', image: '/atlas/media/a.webp', ...fields });

describe('a review event', () => {
  it('keeps the crop\'s tone and image size, which the record it writes does not carry', () => {
    const db = migrated();
    db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
      VALUES('u','local','あ','handwritten','kana','pending',0,1,1,0,?,'{}','{}','{}')`)
      .run(record({ tone: '#d8cfbf', image_size: [47, 51], context_box: { x: 0, y: 0, w: 9, h: 9 }, context_image: '/atlas/media/c.webp' }));
    db.exec(`INSERT INTO submissions(id,actor,request,response,at) VALUES('s','a','{}','{}','t')`);
    const event = db.prepare(`INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at)
      VALUES(?,'s','u','a',?,'{}',?,'{}','{}',?,'t')`);
    const stored = () => JSON.parse((db.query('SELECT data FROM units').get() as { data: string }).data);
    event.run('e1', 0, record({ label: 'い', state: 'checked' }), 'review');
    expect(stored()).toMatchObject({ label: 'い', state: 'checked', tone: '#d8cfbf', image_size: [47, 51], context_image: '/atlas/media/c.webp' });
    // An undo restores a record saved before the crop had a tone.
    event.run('e2', 1, record({}), 'undo');
    expect(stored()).toMatchObject({ label: 'あ', tone: '#d8cfbf', image_size: [47, 51] });
    db.close();
  });
  it('leaves a crop without a tone as the record it writes', () => {
    const db = migrated();
    db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
      VALUES('u','local','あ','handwritten','kana','pending',0,1,1,0,?,'{}','{}','{}')`).run(record({}));
    db.exec(`INSERT INTO submissions(id,actor,request,response,at) VALUES('s','a','{}','{}','t')`);
    db.prepare(`INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at)
      VALUES('e1','s','u','a',0,'{}',?,'{}','{}','review','t')`).run(record({ state: 'checked' }));
    const data = JSON.parse((db.query('SELECT data FROM units').get() as { data: string }).data);
    expect(data).toEqual(JSON.parse(record({ state: 'checked' })));
    db.close();
  });
});
