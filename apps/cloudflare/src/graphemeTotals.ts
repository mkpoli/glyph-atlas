// How many crops a grapheme (字種) holds: every crop and corpus glyph its gallery can list, withheld ones
// among them. They are its characters' crops, the crops its source names it for with no character, and
// the crops a grapheme claim files under it from elsewhere, less those a claim files away. The card of a
// character and the list of every grapheme count through here, so a grapheme has one total.
import type { FiledAt } from './atlas3Gallery';

/** The crops of one character filed under one grapheme, '' for the crops with no character; `claimed`
 *  where a claim filed some of them in or away. */
export type Row = { label: string; grapheme: string; collected: number; corpus: number; claimed?: true };
export type Rows = Map<string, Row>;
export type Under = { label: string; grapheme: string };

export function addCrops(rows: Rows, { label, grapheme }: Under, kind: number, n: number): Row {
  const at = `${label} ${grapheme}`, row = rows.get(at) ?? { label, grapheme, collected: 0, corpus: 0 };
  row[kind === 1 ? 'corpus' : 'collected'] += n;
  rows.set(at, row);
  return row;
}
/** Moves each claimed crop from its place, where its character or its family files it (null for a crop in
 *  none of the rows), to the grapheme its claim names, whose gallery lists it. No row is below nothing: a
 *  count its publication lacks, or a claim on a crop its place does not count, takes nothing from one. */
export function fileClaims(rows: Rows, claims: FiledAt[], place: (claim: FiledAt) => Under | null) {
  for (const row of rows.values()) { row.collected = Math.max(0, row.collected); row.corpus = Math.max(0, row.corpus) }
  for (const claim of claims) {
    const from = place(claim), side = claim.kind === 1 ? 'corpus' : 'collected';
    if (from?.grapheme === claim.head) continue;
    const held = from && rows.get(`${from.label} ${from.grapheme}`);
    if (held && held[side] > 0) addCrops(rows, held, claim.kind, -1).claimed = true;
    addCrops(rows, { label: from?.label ?? '', grapheme: claim.head }, claim.kind, 1).claimed = true;
  }
}
export const crops = (row: Pick<Row, 'collected' | 'corpus'>) => row.collected + row.corpus;
/** The label of the one character that holds every crop of a grapheme, by grapheme: its page shows the
 *  gallery the grapheme's does. A grapheme whose crops claims moved, or whose crops have no character or
 *  several, has none. */
export function soleForms(rows: Iterable<Row>): Map<string, string> {
  const by = new Map<string, Row[]>();
  for (const row of rows) if (crops(row) > 0) by.set(row.grapheme, [...by.get(row.grapheme) ?? [], row]);
  return new Map([...by].filter(([, [first, ...rest]]) => !rest.length && first.label && !first.claimed).map(([grapheme, [row]]) => [grapheme, row.label]));
}
