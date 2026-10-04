import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { FavouriteError, favouriteCrops, favouriteIds, setFavourite } from './favourites';

function setup() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  const at = '2026-10-04T00:00:00.000Z';
  db.prepare(`INSERT INTO "user"(id,name,email,emailVerified,createdAt,updatedAt) VALUES('u1','a','a@x',1,?,?),('u2','b','b@x',1,?,?)`).run(at, at, at, at);
  const unit = db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual) VALUES(?,?,'あ','handwritten','kana','pending',0,1,1,0,'{}','{}','{}','{}')`);
  unit.run('a', 'local'); unit.run('b', 'local'); unit.run('old', 'retired');
  db.exec(`INSERT INTO corpus_units(id,character,family,shuffle,object,offset,size) VALUES('k','仿','U+5023',1,'o',0,1)`);
  return { db, env: { DB: d1(db) } as unknown as Env };
}
const itemsFor = async (_env: Env, ids: string[]) => new Map(ids.filter(id => id !== 'old').map(id => [id, { id }]));

describe('favourites', () => {
  it('star a crop and take the star away, for each user apart', async () => {
    const { db, env } = setup();
    await setFavourite(env, 'u1', { crop: 'a', favourite: true });
    await setFavourite(env, 'u1', { crop: 'k', favourite: true });
    await setFavourite(env, 'u1', { crop: 'a', favourite: true });
    await setFavourite(env, 'u2', { crop: 'b', favourite: true });
    expect((await favouriteIds(env, 'u1')).ids.sort()).toEqual(['a', 'k']);
    await setFavourite(env, 'u1', { crop: 'a', favourite: false });
    expect((await favouriteIds(env, 'u1')).ids).toEqual(['k']);
    expect((await favouriteIds(env, 'u2')).ids).toEqual(['b']);
    db.close();
  });
  it('refuse a crop the collection does not hold, or a retired one', async () => {
    const { db, env } = setup();
    for (const crop of ['nope', 'old']) await expect(setFavourite(env, 'u1', { crop, favourite: true })).rejects.toBeInstanceOf(FavouriteError);
    await expect(setFavourite(env, 'u1', { crop: 'a' })).rejects.toBeInstanceOf(FavouriteError);
    db.close();
  });
  it('list a page newest first, a retired crop as its replacement', async () => {
    const { db, env } = setup();
    const star = db.prepare('INSERT INTO favourites(user_id,unit,at) VALUES(?,?,?)');
    star.run('u1', 'a', '2026-10-01'); star.run('u1', 'old', '2026-10-02'); star.run('u1', 'k', '2026-10-03');
    const page = await favouriteCrops(env, 'u1', 0, 2, itemsFor, async (_env, id) => id === 'old' ? 'b' : null);
    expect(page.items.map(i => i.id)).toEqual(['k', 'b']);
    expect([page.total, page.next]).toEqual([3, 2]);
    expect((await favouriteCrops(env, 'u1', 2, 2, itemsFor, async () => null)).next).toBeNull();
    db.close();
  });
});
