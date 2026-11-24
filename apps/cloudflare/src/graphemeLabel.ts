// What stands for a grapheme (字種) where the site names one: its 旧字体, its head and its 简体字, each once
// (圖図图, 語语, 假仮), where the 常用漢字表 and the 通用规范汉字表 give them; else its head. The label is for showing
// alone: the grapheme's key, address, place in a list and search are its head's. The table is
// scripts/build_grapheme_labels.py's.
import LABELS from './graphemeLabels.json';

/** The label of the grapheme headed by `char`; a character that heads no such grapheme is its own. */
export const graphemeLabel = (char: string): string => Object.hasOwn(LABELS, char) ? (LABELS as Record<string, string>)[char] : char;
/** The label as it is set: the 简体字 that ends it apart, for a glyph drawn the Chinese way, from the 旧字体 and
 *  the head, drawn the Japanese way as every character of the site is. */
export function labelParts(char: string): { text: string; simplified: string } {
  if (!Object.hasOwn(LABELS, char)) return { text: char, simplified: '' };
  const chars = [...graphemeLabel(char)], simplified = chars.at(-1) === char ? '' : chars.pop()!;
  return { text: chars.join(''), simplified };
}
/** How many characters the label is, by which a place of fixed size draws them: 1, 2 or 3. */
export const labelSize = (char: string): number => [...graphemeLabel(char)].length;
