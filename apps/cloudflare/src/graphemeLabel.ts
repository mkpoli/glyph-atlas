// What stands for a grapheme (字種) where the site names one: its head, and where that is a 新字体 the
// 常用漢字表 prints the 旧字体 of, the two side by side, the old form first (假仮, 國国). The label is for
// showing alone: the grapheme's key, address, place in a list and search are its head's. The table is
// scripts/build_grapheme_labels.py's.
import OLD_FORMS from './graphemeLabels.json';

/** The label of the grapheme headed by `char`; a character that heads no such grapheme is its own. */
export const graphemeLabel = (char: string): string => Object.hasOwn(OLD_FORMS, char) ? (OLD_FORMS as Record<string, string>)[char] + char : char;
