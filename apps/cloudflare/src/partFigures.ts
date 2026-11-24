// The forms a character takes when a part of it is written as a registered figure (遇 as ⿺辶󠄁禺󠄁: 辶
// U+8FB6 U+E0101 and 禺 U+79BA U+E0101), read off its descriptions (docs/decisions/0013-finer-forms.md).
// Only a sequence the composer draws is named, since a form holding any other could not be shown.
import { FIGURE_DESCRIPTIONS, PART_FIGURES } from './partFiguresData';
import { arity, FORM_LONGEST } from './representation';

/** How deep a part may sit: in the character's own description (1), or in that of one of its parts (2),
 *  as the derived forms' substitutions may (refs.DERIVED_IDS_DEPTH). */
const DEPTH = 2;
/** How many parts of one form are written as figures, and how many forms of a character are listed. */
const FIGURES = 3;
const SHOWN = 48;

type Tree = string | { op: string; parts: Tree[] };
/** A leaf of the description, with its own description where it may be opened. */
type Leaf = { char: string; unit: string; open: Node | null };
type Node = Leaf | { op: string; parts: Node[] };
/** One part at one depth, wherever the description has it: 巨 in ⿰巨巨 is written the same way twice.
 *  `share` is how much of the character its largest place takes, each operator dividing what it has
 *  evenly. */
type Unit = { key: string; char: string; share: number };
type Chosen = Map<string, string>;
export type FigureForm = { char: string; route: { was: string; became: string }[] };

function parse(text: string): Tree | null {
  const chars = [...text];
  let at = 0;
  const read = (): Tree | null => {
    const char = chars[at++];
    if (char === undefined) return null;
    const parts: Tree[] = [];
    for (let i = arity(char); i > 0; i--) {
      const part = read();
      if (part === null) return null;
      parts.push(part);
    }
    return parts.length ? { op: char, parts } : char;
  };
  const tree = read();
  return at === chars.length ? tree : null;
}
const leaves = (tree: Tree): string[] => typeof tree === 'string' ? [tree] : tree.parts.flatMap(leaves);

/** The parts a description names, whose own descriptions `figureForms` opens. */
export const partsOf = (description: string): string[] => {
  const tree = parse(description);
  return tree === null || typeof tree === 'string' ? [] : [...new Set(leaves(tree))];
};

type Described = (part: string) => string | null | undefined;
/** Whether `fine` is `coarse` with some of its parts written out (⿱明⿱穴工 of ⿱明空). */
function writtenOut(coarse: Tree, fine: Tree, described: Described): boolean {
  if (typeof coarse !== 'string') return typeof fine !== 'string' && coarse.op === fine.op && coarse.parts.every((part, i) => writtenOut(part, fine.parts[i], described));
  if (coarse === fine) return true;
  const own = typeof fine === 'string' ? null : described(coarse), tree = own ? parse(own) : null;
  return tree !== null && typeof tree !== 'string' && writtenOut(tree, fine, described);
}

/** The forms a character described as `descriptions` takes with one to FIGURES of its parts written as
 *  registered figures, the largest parts first, at most SHOWN: a form is as small as the smallest part
 *  it changes, and of two as small the one that changes fewer comes first. `described` gives a part's
 *  own description. A form a registered figure of the whole already is (遇󠄃 is ⿺辶禺󠄁) is left out. */
export function figureForms(descriptions: string[], described: Described): FigureForm[] {
  const trees = descriptions.map(parse).filter((tree): tree is Tree => tree !== null && typeof tree !== 'string');
  const found = trees.filter((tree, i) => !trees.some((other, k) => k !== i && writtenOut(other, tree, described) && (k < i || !writtenOut(tree, other, described))))
    .flatMap(tree => formsOf(tree, described));
  const seen = new Set<string>();
  return found.sort((a, b) => b.smallest - a.smallest || a.route.length - b.route.length)
    .filter(form => [...form.char].length <= FORM_LONGEST && !FIGURE_DESCRIPTIONS.has(form.char) && !seen.has(form.char) && seen.add(form.char))
    .slice(0, SHOWN).map(({ char, route }) => ({ char, route }));
}

function formsOf(tree: Tree, described: Described): (FigureForm & { smallest: number })[] {
  const units = new Map<string, Unit>();
  const build = (part: Tree, share: number, depth: number): Node => {
    if (typeof part !== 'string') return { op: part.op, parts: part.parts.map(inner => build(inner, share / part.parts.length, depth)) };
    const key = `${depth}${part}`;
    if (Object.hasOwn(PART_FIGURES, part)) units.set(key, { key, char: part, share: Math.max(share, units.get(key)?.share ?? 0) });
    const own = depth < DEPTH ? described(part) : null, opened = own ? parse(own) : null;
    return { char: part, unit: key, open: opened === null || typeof opened === 'string' ? null : build(opened, share, depth + 1) };
  };
  const root = build(tree, 1, 1);
  const changed = (node: Node, chosen: Chosen): boolean => 'op' in node ? node.parts.some(part => changed(part, chosen))
    : chosen.has(node.unit) || (node.open !== null && changed(node.open, chosen));
  // A part written as a figure is not opened, so a figure inside it goes unwritten there.
  const write = (node: Node, chosen: Chosen, written: Set<string>): string => {
    if ('op' in node) return node.op + node.parts.map(part => write(part, chosen, written)).join('');
    const selector = chosen.get(node.unit);
    if (selector) { written.add(node.unit); return node.char + selector }
    return node.open !== null && changed(node.open, chosen) ? write(node.open, chosen, written) : node.char;
  };
  const all = [...units.values()], found: (FigureForm & { smallest: number })[] = [];
  const choose = (from: number, used: Unit[], chosen: Chosen) => {
    const written = new Set<string>(), char = write(root, chosen, written);
    if (used.length && written.size === used.length) found.push({ char, smallest: Math.min(...used.map(unit => unit.share)),
      route: used.map(unit => ({ was: unit.char, became: unit.char + chosen.get(unit.key) })) });
    if (used.length === FIGURES) return;
    for (let i = from; i < all.length; i++)
      for (const selector of PART_FIGURES[all[i].char]) choose(i + 1, [...used, all[i]], new Map([...chosen, [all[i].key, selector]]));
  };
  choose(0, [], new Map());
  return found;
}
