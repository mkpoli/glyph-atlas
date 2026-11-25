// How a crop is read at its place in the text: its `read_as` and `bears_ruby` slots on its effective cut
// (data/ledger.json). A run of crops read together (諸器 しょき) carries one claim per crop, each with the
// whole run and the crop's place in it, so every slot keeps its own crop's evidence. The writes are
// `setReading` in `routes/claims.ts`.
import { cropSlots, slotHolders, slotValuesHeld } from './cropPanels';
import { slotBacking } from './cropWords';
import { canonical } from './input';
import { PREDICATES } from './ledger';

type Json = Record<string, any>;
export const READING_PREDICATES = ['read_as', 'bears_ruby'];
export const readingMethodsQuery = () => 'SELECT id,method FROM assertions WHERE id IN (SELECT value FROM json_each(?))';

// The distinct values a slot holds, each with who holds it and, where its claims name one, what it rests on.
// Readers who agree hold one value between them.
function heldValues(row: Json, methods: Map<string, string>): Json[] {
  const author = new Map<string, string>((row.claims as Json[]).flatMap(c => c.members.map((m: Json) => [m.assertion, c.asserted_by])));
  const found = new Map<string, { value: Json; held: string[] }>();
  // A slot that stands names its members once, whoever made them; its supporting claims all hold that value.
  const members: Json[] = row.status === 'disputed' ? slotValuesHeld(row) : slotValuesHeld(row).flatMap((m: Json) => slotBacking(row).map(assertion => ({ ...m, assertion })));
  for (const m of members) {
    const key = canonical(m.value), one = found.get(key) ?? { value: m.value as Json, held: [] as string[] };
    one.held.push(m.assertion); found.set(key, one);
  }
  return [...found.values()].map(({ value, held }) => ({ ...value, by: [...new Set(held.flatMap(id => author.has(id) ? [author.get(id)!] : []))],
    ...(methods.size ? { methods: PREDICATES.read_as.methods!.filter(method => held.some(id => methods.get(id) === method)) } : {}) }));
}

/** Each crop's reading: the status of its slot, the readings it holds (each standing one while
 *  disputed) with who holds each and what it rests on, and under `ruby` the same of the ruby beside its
 *  run. A crop with neither is absent. */
export async function cropReadings(env: Env, ids: number[]): Promise<Map<number, Json>> {
  const found = new Map<number, Json>(), rows = await cropSlots(env, ids, READING_PREDICATES);
  if (!rows.length) return found;
  const backing = [...new Set(rows.filter(row => row.predicate === 'read_as').flatMap(slotBacking))];
  const methods = new Map(backing.length ? (await env.ATLAS.prepare(readingMethodsQuery()).bind(JSON.stringify(backing)).all<{ id: string; method: string }>())
    .results.map(r => [r.id, r.method]) : []);
  for (const row of rows) {
    const crop = found.get(row.crop) ?? { status: null, values: [], methods: [], by: [], ruby: null };
    const slot = (of: Map<string, string>): Json => ({ status: row.status, values: heldValues(row, of), by: slotHolders(row) });
    if (row.predicate === 'bears_ruby') crop.ruby = slot(new Map());
    else {
      const held = new Set(slotBacking(row).map(id => methods.get(id)));
      Object.assign(crop, slot(methods), { methods: PREDICATES.read_as.methods!.filter(method => held.has(method)) });
    }
    found.set(row.crop, crop);
  }
  return found;
}
