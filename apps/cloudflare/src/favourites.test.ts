import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { FAVOURITES_HELD, FavouriteError, carryFavourites, favouriteCrops, favouriteQueries, favouriteIds, setFavourite } from './favourites';

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
  it("list pages newest first, moving a retired crop's star to its replacement", async () => {
    const { db, env } = setup();
    const star = db.prepare('INSERT INTO favourites(user_id,unit,at) VALUES(?,?,?)');
    star.run('u1', 'a', '2026-10-01'); star.run('u1', 'old', '2026-10-02'); star.run('u1', 'k', '2026-10-03');
    const first = await favouriteCrops(env, 'u1', null, 2, itemsFor, async (_env, id) => id === 'old' ? 'b' : null);
    expect(first.items.map(i => i.id)).toEqual(['k', 'b']);
    expect([first.total, first.moved]).toEqual([3, true]);
    expect((await favouriteIds(env, 'u1')).ids).toEqual(['k', 'b', 'a']);
    // Taking a star away before the next page is read skips nothing.
    await setFavourite(env, 'u1', { crop: 'k', favourite: false });
    const second = await favouriteCrops(env, 'u1', first.next, 2, itemsFor, async () => null);
    expect(second.items.map(i => i.id)).toEqual(['a']);
    expect(second.next).toBeNull();
    // A page asked for no rows reads one.
    expect((await favouriteCrops(env, 'u1', null, 0, itemsFor, async () => null)).items).toHaveLength(1);
    db.close();
  });
  it("hold each user to the limit, and carry an anonymous user's stars up to it", async () => {
    const { db, env } = setup();
    const star = db.prepare('INSERT INTO favourites(user_id,unit,at) VALUES(?,?,?)');
    db.transaction(() => { for (let n = 0; n < FAVOURITES_HELD - 1; n++) star.run('u1', `x${n}`, '2026-01-01') })();
    await setFavourite(env, 'u1', { crop: 'a', favourite: true });
    await expect(setFavourite(env, 'u1', { crop: 'b', favourite: true })).rejects.toBeInstanceOf(FavouriteError);
    await setFavourite(env, 'u1', { crop: 'a', favourite: true });
    star.run('u2', 'b', '2026-10-02'); star.run('u2', 'a', '2026-10-01');
    await carryFavourites(env, 'u2', 'u1');
    expect((db.query("SELECT count(*) AS n FROM favourites WHERE user_id='u1'").get() as { n: number }).n).toBe(FAVOURITES_HELD);
    expect(db.query("SELECT count(*) AS n FROM favourites WHERE user_id='u2'").get()).toEqual({ n: 0 });
    db.exec("DELETE FROM favourites WHERE user_id='u1' AND unit LIKE 'x%'");
    star.run('u2', 'b', '2026-10-02'); star.run('u2', 'k', '2026-10-01');
    await carryFavourites(env, 'u2', 'u1');
    expect((await favouriteIds(env, 'u1')).ids.sort()).toEqual(['a', 'b', 'k']);
    db.close();
  });
  it('read every page shape from the index, never sorting or scanning the table', () => {
    const { db } = setup();
    for (const [name, sql] of Object.entries(favouriteQueries)) {
      const plan = (db.query('EXPLAIN QUERY PLAN ' + sql).all(...Array((sql.match(/\?/g) ?? []).length).fill('x')) as { detail: string }[]).map(r => r.detail);
      expect({ name, plan: plan.filter(d => /TEMP B-TREE|^SCAN (?!.*USING (COVERING )?INDEX)/.test(d)) }).toEqual({ name, plan: [] });
    }
    db.close();
  });
});
