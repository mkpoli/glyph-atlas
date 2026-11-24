// The hentaigana of each kana: the encoded letters filed under it in `data/vocab/characters.tsv`, which
// `tools/crop-readings.test.mjs` compares with this table.
export const HENTAIGANA = new Map([
  ['あ', '𛀂𛀃𛀄𛀅'], ['い', '𛀆𛀇𛀈𛀉'], ['う', '𛀊𛀋𛀌𛀍𛀎'], ['え', '𛀏𛀐𛀑𛀒𛀓'],
  ['お', '𛀔𛀕𛀖'], ['か', '𛀗𛀘𛀙𛀚𛀛𛀜𛀝𛀞𛀟𛀠𛀡𛀢'], ['き', '𛀣𛀤𛀥𛀦𛀧𛀨𛀩𛀪'], ['く', '𛀫𛀬𛀭𛀮𛀯𛀰𛀱'],
  ['け', '𛀲𛀳𛀴𛀵𛀶𛀷'], ['こ', '𛀸𛀹𛀺𛀻'], ['さ', '𛀼𛀽𛀾𛀿𛁀𛁁𛁂𛁃'], ['し', '𛁄𛁅𛁆𛁇𛁈𛁉'],
  ['す', '𛁊𛁋𛁌𛁍𛁎𛁏𛁐𛁑'], ['せ', '𛁒𛁓𛁔𛁕𛁖'], ['そ', '𛁗𛁘𛁙𛁚𛁛𛁜𛁝'], ['た', '𛁞𛁟𛁠𛁡'],
  ['ち', '𛁢𛁣𛁤𛁥𛁦𛁧𛁨'], ['つ', '𛁩𛁪𛁫𛁬𛁭'], ['て', '𛁮𛁯𛁰𛁱𛁲𛁳𛁴𛁵𛁶'], ['と', '𛁷𛁸𛁹𛁺𛁻𛁼𛁽'],
  ['な', '𛁾𛁿𛂀𛂁𛂂𛂃𛂄𛂅𛂆'], ['に', '𛂇𛂈𛂉𛂊𛂋𛂌𛂍𛂎'], ['ぬ', '𛂏𛂐𛂑'], ['ね', '𛂒𛂓𛂔𛂕𛂖𛂗𛂘'],
  ['の', '𛂙𛂚𛂛𛂜𛂝'], ['は', '𛂞𛂟𛂠𛂡𛂢𛂣𛂤𛂥𛂦𛂧𛂨'], ['ひ', '𛂩𛂪𛂫𛂬𛂭𛂮𛂯'], ['ふ', '𛂰𛂱𛂲'],
  ['へ', '𛂳𛂴𛂵𛂶𛂷𛂸𛂹'], ['ほ', '𛂺𛂻𛂼𛂽𛂾𛂿𛃀𛃁'], ['ま', '𛃂𛃃𛃄𛃅𛃆𛃇𛃈'], ['み', '𛃉𛃊𛃋𛃌𛃍𛃎𛃏'],
  ['む', '𛃐𛃑𛃒𛃓𛄝𛄞'], ['め', '𛃔𛃕𛃖'], ['も', '𛃗𛃘𛃙𛃚𛃛𛃜'], ['や', '𛃝𛃞𛃟𛃠𛃡𛃢'],
  ['ゆ', '𛃣𛃤𛃥𛃦'], ['よ', '𛃧𛃨𛃩𛃪𛃫𛃬'], ['ら', '𛃭𛃮𛃯𛃰'], ['り', '𛃱𛃲𛃳𛃴𛃵𛃶𛃷'],
  ['る', '𛃸𛃹𛃺𛃻𛃼𛃽'], ['れ', '𛃾𛃿𛄀𛄁'], ['ろ', '𛄂𛄃𛄄𛄅𛄆𛄇'], ['わ', '𛄈𛄉𛄊𛄋𛄌'],
  ['ゐ', '𛄍𛄎𛄏𛄐𛄑'], ['ゑ', '𛄒𛄓𛄔𛄕'], ['を', '𛄖𛄗𛄘𛄙𛄚𛄛𛄜'],
])
const KANA = new Map([...HENTAIGANA].flatMap(([kana, letters]) => [...letters].map(letter => [letter, kana])))

/** A text as ordinary kana: each hentaigana as the kana it is a letter of. */
export const plainKana = text => [...text.normalize('NFC')].map(c => KANA.get(c) ?? c).join('').normalize('NFC')
