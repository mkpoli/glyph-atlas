// The hentaigana of each kana, each with its 字母: the encoded letters filed under it in
// `data/vocab/characters.tsv`, which `tools/crop-readings.test.mjs` compares with this table.
const LETTERS = [
  ['あ', '𛀂安𛀃愛𛀄阿𛀅惡'], ['い', '𛀆以𛀇伊𛀈意𛀉移'], ['う', '𛀊宇𛀋宇𛀌憂𛀍有𛀎雲'],
  ['え', '𛀏盈𛀐縁𛀑衣𛀒衣𛀓要'], ['お', '𛀔於𛀕於𛀖隱'], ['か', '𛀗佳𛀘加𛀙可𛀚可𛀛嘉𛀜我𛀝歟𛀞賀𛀟閑𛀠香𛀡駕𛀢家'],
  ['き', '𛀣喜𛀤幾𛀥幾𛀦支𛀧木𛀨祈𛀩貴𛀪起'], ['く', '𛀫久𛀬久𛀭九𛀮供𛀯倶𛀰具𛀱求'], ['け', '𛀲介𛀳介𛀴希𛀵氣𛀶計𛀷遣'],
  ['こ', '𛀸古𛀹故𛀺許𛀻期'], ['さ', '𛀼乍𛀽佐𛀾佐𛀿左𛁀差𛁁散𛁂斜𛁃沙'], ['し', '𛁄之𛁅之𛁆事𛁇四𛁈志𛁉新'],
  ['す', '𛁊受𛁋壽𛁌數𛁍數𛁎春𛁏春𛁐須𛁑須'], ['せ', '𛁒世𛁓世𛁔世𛁕勢𛁖聲'], ['そ', '𛁗所𛁘所𛁙曾𛁚曾𛁛楚𛁜蘇𛁝處'],
  ['た', '𛁞堂𛁟多𛁠多𛁡當'], ['ち', '𛁢千𛁣地𛁤智𛁥知𛁦知𛁧致𛁨遲'], ['つ', '𛁩川𛁪川𛁫津𛁬都𛁭徒'],
  ['て', '𛁮亭𛁯低𛁰傳𛁱天𛁲天𛁳天𛁴帝𛁵弖𛁶轉'], ['と', '𛁷土𛁸度𛁹東𛁺登𛁻登𛁼砥𛁽等'], ['な', '𛁾南𛁿名𛂀奈𛂁奈𛂂奈𛂃菜𛂄那𛂅那𛂆難'],
  ['に', '𛂇丹𛂈二𛂉仁𛂊兒𛂋爾𛂌爾𛂍耳𛂎而'], ['ぬ', '𛂏努𛂐奴𛂑怒'], ['ね', '𛂒年𛂓年𛂔年𛂕根𛂖熱𛂗禰𛂘子'],
  ['の', '𛂙乃𛂚濃𛂛能𛂜能𛂝農'], ['は', '𛂞八𛂟半𛂠婆𛂡波𛂢盤𛂣盤𛂤破𛂥者𛂦者𛂧葉𛂨頗'], ['ひ', '𛂩悲𛂪日𛂫比𛂬避𛂭非𛂮飛𛂯飛'],
  ['ふ', '𛂰不𛂱婦𛂲布'], ['へ', '𛂳倍𛂴弊𛂵弊𛂶遍𛂷邊𛂸邊𛂹部'], ['ほ', '𛂺保𛂻保𛂼報𛂽奉𛂾寶𛂿本𛃀本𛃁豐'],
  ['ま', '𛃂万𛃃末𛃄末𛃅滿𛃆滿𛃇萬𛃈麻'], ['み', '𛃉三𛃊微𛃋美𛃌美𛃍美𛃎見𛃏身'], ['む', '𛃐武𛃑無𛃒牟𛃓舞𛄝无𛄞无'],
  ['め', '𛃔免𛃕面𛃖馬'], ['も', '𛃗母𛃘毛𛃙毛𛃚毛𛃛茂𛃜裳'], ['や', '𛃝也𛃞也𛃟屋𛃠耶𛃡耶𛃢夜'],
  ['ゆ', '𛃣游𛃤由𛃥由𛃦遊'], ['よ', '𛃧代𛃨余𛃩與𛃪與𛃫與𛃬餘'], ['ら', '𛃭羅𛃮良𛃯良𛃰良'],
  ['り', '𛃱利𛃲利𛃳李𛃴梨𛃵理𛃶里𛃷離'], ['る', '𛃸流𛃹留𛃺留𛃻留𛃼累𛃽類'], ['れ', '𛃾禮𛃿禮𛄀連𛄁麗'],
  ['ろ', '𛄂呂𛄃呂𛄄婁𛄅樓𛄆路𛄇露'], ['わ', '𛄈倭𛄉和𛄊和𛄋王𛄌王'], ['ゐ', '𛄍井𛄎井𛄏居𛄐爲𛄑遺'],
  ['ゑ', '𛄒惠𛄓衞𛄔衞𛄕衞'], ['を', '𛄖乎𛄗乎𛄘尾𛄙緒𛄚越𛄛遠𛄜遠'],
]
/** The hentaigana of each kana: `{ letter, jibo }`. */
export const HENTAIGANA = new Map(LETTERS.map(([kana, pairs]) => [kana, [...pairs].flatMap((c, i, all) => i % 2 ? [] : [{ letter: c, jibo: all[i + 1] }])]))
const KANA = new Map([...HENTAIGANA].flatMap(([kana, letters]) => letters.map(({ letter }) => [letter, kana])))

/** A text as ordinary kana: each hentaigana as the kana it is a letter of. */
export const plainKana = text => [...text.normalize('NFC')].map(c => KANA.get(c) ?? c).join('').normalize('NFC')

/** The kana written before the caret, and the letters it may be written with: itself, then its hentaigana.
 *  A voiced kana's are its unvoiced letters, voiced. Null where the kana has no hentaigana. */
export function lettersAt(text, caret) {
  const before = text.slice(0, caret).match(/\P{M}\p{M}*$/u)?.[0]
  if (!before) return null
  const [base, ...marks] = plainKana(before).normalize('NFD'), found = HENTAIGANA.get(base)
  if (!found) return null
  const mark = marks.join('')
  return { from: caret - before.length, to: caret, now: before.normalize('NFC'),
    letters: [{ letter: (base + mark).normalize('NFC'), jibo: null }, ...found.map(({ letter, jibo }) => ({ letter: letter + mark, jibo }))] }
}
