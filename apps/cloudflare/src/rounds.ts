// Kept out of the Worker's entry module: workerd reads every export of that module as an entrypoint,
// and a plain value there stops local Miniflare from starting the Worker.
// The most crops one round deals and saves. A saved crop costs at most five D1 queries (two to find a
// corpus glyph's row, one character lookup, its event and its new row) and one R2 read, so a full round
// of corrected, never-reviewed corpus glyphs stays near 870 of the 1,000 a Worker invocation may run.
export const ROUND_MAX = 144
