// How a crop is read at its place in the text: its `read_as` and `bears_ruby` slots on its effective cut
// (data/ledger.json). A run of crops read together (諸器 しょき) carries one claim per crop, each with the
// whole run and the crop's place in it, so every slot keeps its own crop's evidence. The writes are
// `setReading` in `routes/claims.ts`.
import { cropSlots, slotHolders, slotValuesHeld } from './cropPanels';
import { slotBacking } from './cropWords';
import { PREDICATES } from './ledger';

type Json = Record<string, any>;
export const READING_PREDICATES = ['read_as', 'bears_ruby'];
export const readingMethodsQuery = () => 'SELECT id,method FROM assertions WHERE id IN (SELECT value FROM json_each(?))';

/** Each crop's reading: the status of its slot, the readings it holds (each standing one while
 *  disputed), what they rest on, who holds them, and under `ruby` the same of the ruby beside its run.
 *  A crop with neither is absent. */
export async function cropReadings(env: Env, ids: number[]): Promise<Map<number, Json>> {
  const found = new Map<number, Json>(), rows = await cropSlots(env, ids, READING_PREDICATES);
  if (!rows.length) return found;
  const backing = [...new Set(rows.filter(row => row.predicate === 'read_as').flatMap(slotBacking))];
  const methods = new Map(backing.length ? (await env.ATLAS.prepare(readingMethodsQuery()).bind(JSON.stringify(backing)).all<{ id: string; method: string }>())
    .results.map(r => [r.id, r.method]) : []);
  for (const row of rows) {
    const values = slotValuesHeld(row).map((m: Json) => m.value), slot: Json = { status: row.status, values, by: slotHolders(row) };
    const crop = found.get(row.crop) ?? { status: null, values: [], methods: [], by: [], ruby: null };
    if (row.predicate === 'bears_ruby') crop.ruby = slot;
    else {
      const held = new Set(slotBacking(row).map(id => methods.get(id)));
      Object.assign(crop, slot, { methods: PREDICATES.read_as.methods!.filter(method => held.has(method)) });
    }
    found.set(row.crop, crop);
  }
  return found;
}
