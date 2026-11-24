// The forms a character takes when a part of it is written as a registered figure (遇 as ⿺辶󠄁禺󠄁: 辶
// U+8FB6 U+E0101 and 禺 U+79BA U+E0101), read off its descriptions (docs/decisions/0013-finer-forms.md).
// Only a sequence the composer draws is named, since a form holding any other could not be shown.
import { PART_FIGURES } from './partFiguresData';
import { arity, FORM_LONGEST } from './representation';

/** How deep a part may sit: in the character's own description (1), or in that of one of its parts (2),
 *  as the derived forms' substitutions may (refs.DERIVED_IDS_DEPTH). */
const DEPTH = 2;
/** How many parts of one form are written as figures, and how many forms are listed. */
const FIGURES = 3;
const SHOWN = 48;

type Tree = string | { op: string; parts: Tree[] };
/** A leaf of the description, with its own description where it may be opened. */
type Leaf = { char: string; unit: string; open: Node | null };
type Node = Leaf | { op: string; parts: Node[] };
/** One part at one depth, wherever the description has it: 木 in 林 is written the same way twice.
 *  `share` is how much of the character its largest place takes, each operator dividing what it has
 *  evenly; `inside` are the parts it lies in. */
type Unit = { key: string; char: string; share: number; at: number; inside: Set<string> };
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

/** The forms `description` takes with one to FIGURES of its parts written as registered figures, the
 *  largest parts first: a form is as small as the smallest part it changes, and of two as small the
 *  one that changes fewer comes first. `described` gives a part's own description. */
export function figureForms(description: string, described: (part: string) => string | null | undefined): FigureForm[] {
  const tree = parse(description);
  if (tree === null || typeof tree === 'string') return [];
  const units = new Map<string, Unit>();
  const build = (part: Tree, share: number, depth: number, inside: string[]): Node => {
    if (typeof part !== 'string') return { op: part.op, parts: part.parts.map(inner => build(inner, share / part.parts.length, depth, inside)) };
    const key = `${depth}${part}`;
    if (Object.hasOwn(PART_FIGURES, part)) {
      const unit = units.get(key) ?? { key, char: part, share, at: units.size, inside: new Set<string>() };
      unit.share = Math.max(unit.share, share);
      for (const outer of inside) unit.inside.add(outer);
      units.set(key, unit);
    }
    const own = depth < DEPTH ? described(part) : null, opened = own ? parse(own) : null;
    return { char: part, unit: key, open: opened === null || typeof opened === 'string' ? null : build(opened, share, depth + 1, [...inside, key]) };
  };
  const root = build(tree, 1, 1, []);
  const changed = (node: Node, chosen: Chosen): boolean => 'op' in node ? node.parts.some(part => changed(part, chosen))
    : chosen.has(node.unit) || (node.open !== null && changed(node.open, chosen));
  const write = (node: Node, chosen: Chosen): string => {
    if ('op' in node) return node.op + node.parts.map(part => write(part, chosen)).join('');
    const selector = chosen.get(node.unit);
    if (selector) return node.char + selector;
    return node.open !== null && changed(node.open, chosen) ? write(node.open, chosen) : node.char;
  };
  const all = [...units.values()];
  const found: { text: string; used: Unit[]; chosen: Chosen }[] = [];
  const choose = (from: number, used: Unit[], chosen: Chosen) => {
    if (used.length) found.push({ text: write(root, chosen), used, chosen });
    if (used.length === FIGURES) return;
    for (let i = from; i < all.length; i++) {
      const unit = all[i];
      // A part written as a figure is not opened as well.
      if (used.some(other => unit.inside.has(other.key) || other.inside.has(unit.key))) continue;
      for (const selector of PART_FIGURES[unit.char]) choose(i + 1, [...used, unit], new Map([...chosen, [unit.key, selector]]));
    }
  };
  choose(0, [], new Map());
  const smallest = (used: Unit[]) => Math.min(...used.map(unit => unit.share));
  const seen = new Set<string>();
  return found.filter(form => [...form.text].length <= FORM_LONGEST)
    .sort((a, b) => smallest(b.used) - smallest(a.used) || a.used.length - b.used.length)
    .filter(form => !seen.has(form.text) && seen.add(form.text))
    .slice(0, SHOWN)
    .map(({ text, used, chosen }) => ({ char: text, route: used.map(unit => ({ was: unit.char, became: unit.char + chosen.get(unit.key) })) }));
}
