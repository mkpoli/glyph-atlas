// The sources the atlas plans to take in, one row per printing or manuscript: which copy is processed,
// who holds it, and how far it has come. A work with no edition listed has none chosen yet.
//
// Titles, edition statements and holders are written as their catalogues give them, so they are not
// translated; a holder no source names is left empty. `href` opens the holder's scan, or, once a copy is published, its books on this site
// (`book`: the word the book list is searched by).
//
// Status: 'published' (crops are on the site), 'underway' (pages collected or being cut), 'planned'.
//
// Editions were checked against each holder's catalogue record or manifest on 2026-11-20.

const ndl = pid => `https://dl.ndl.go.jp/pid/${pid}`
const naj = file => `https://www.digital.archives.go.jp/file/${file}`
const commons = title => `https://commons.wikimedia.org/wiki/File:${encodeURIComponent(title.replaceAll(' ', '_'))}`
const archive = id => `https://archive.org/details/${id}`
const harvard = urn => `https://nrs.lib.harvard.edu/URN-3:FHCL:${urn}`

const NDL = '国立国会図書館'
const NAJ = '国立公文書館（内閣文庫）'
const NCL = '國家圖書館（臺北）'
const NLC = '中國國家圖書館'
const HYL = 'Harvard-Yenching Library'
const PKU = '北京大學圖書館'
const ZHSY = '國家圖書館出版社（中華再造善本）'
const BL = 'British Library'
const BNF = 'Bibliothèque nationale de France'
const IOM = 'Institute of Oriental Manuscripts, Russian Academy of Sciences'

/** @typedef {{ edition: string, holder: string, href?: string, book?: string, status: 'published' | 'underway' | 'planned' }} Edition */
/** @typedef {{ title: string, by?: string, lang?: string, editions: Edition[] }} Work */

/** @type {{ key: string, works: Work[] }[]} */
export const STAGES = [
  { key: 'dictionaries', works: [
    { title: '類聚名義抄', lang: 'ja', editions: [
      { edition: '観智院本（貴重図書複製会、1937）', holder: NDL, book: '類聚名義抄', status: 'published' }] },
    { title: '篆隷萬象名義', by: '空海', lang: 'ja', editions: [
      { edition: '高山寺本（崇文叢書、1926–1927）', holder: NDL, book: '篆隷萬象名義', status: 'published' }] },
    { title: '新撰字鏡', by: '昌住', lang: 'ja', editions: [
      { edition: '天治本（六合館、1916）', holder: NDL, book: '新撰字鏡', status: 'published' }] },
    { title: '音同', editions: [
      { edition: '甲種本（Инв. № 207），《俄藏黑水城文獻》第7冊', holder: IOM, book: '音同（甲種本）', status: 'published' },
      { edition: '乙種本（Инв. № 208, 209, 4775），《俄藏黑水城文獻》第7冊', holder: IOM, book: '音同（乙種本）', status: 'published' }] },
    { title: '龍龕手鏡', by: '釋行均', editions: [
      { edition: '高麗本（京城帝國大學法文學部影印，1929）', holder: NDL, href: ndl(1194507), status: 'planned' },
      { edition: '宋刻本（中華再造善本影印）', holder: NLC, href: commons('ZHSY000072 龍龕手鑑四卷 (遼)釋行均 撰 宋刻本.pdf'), status: 'underway' },
      { edition: '宋本（日本古典全集刊行會影印，1934）', holder: NDL, href: ndl(1126592), status: 'planned' }] },
    { title: '改併五音類聚四聲篇海', by: '韓孝彥、韓道昭', editions: [
      { edition: '元前至元間刊本', holder: NCL, href: commons('NCL-01089 改併五音類聚四聲篇.pdf'), status: 'planned' },
      { edition: '明成化丁亥（1467）重刊本', holder: PKU, href: archive('02076727.cn'), status: 'planned' },
      { edition: '明萬曆己丑（1589）重刊本（芝山開元寺）', holder: NDL, href: ndl(2552924), status: 'planned' },
      { edition: '明萬曆十七年（1589）刊本（近衛文庫）', holder: '京都大学附属図書館', href: 'https://rmda.kulib.kyoto-u.ac.jp/item/rb00008716', status: 'planned' }] },
    { title: '新修累音引證群籍玉篇', by: '邢準', editions: [
      { edition: '金刻本（中華再造善本影印）', holder: ZHSY, href: commons('ZHSY000526 新修絫音引證群籍玉篇 十二冊 金邢準撰 金刻本.pdf'), status: 'planned' }] },
    { title: '大廣益會玉篇', by: '顧野王撰、孫強增字、陳彭年等重修', editions: [
      { edition: '元延祐二年（1315）圓沙書院刻本（中華再造善本影印）', holder: ZHSY, href: commons('ZHSY000524 大廣益會玉篇 六冊 南朝·梁顧野生撰 唐孫強增字 宋陳彭年等重修 元延祐二年圓沙書院刻本.pdf'), status: 'planned' },
      { edition: '明宣德間清江書堂刊本', holder: NDL, href: ndl(2592019), status: 'planned' }] },
    { title: '類篇', by: '司馬光等', editions: [
      { edition: '清初毛氏汲古閣影宋抄本（中華再造善本影印）', holder: ZHSY, href: commons('ZHSY100362 類篇 宋司馬光等撰 清初毛氏汲古閣影宋抄本.pdf'), status: 'planned' },
      { edition: '清康熙四十五年（1706）揚州詩局刻本', holder: '天津圖書館', href: commons('NLC892-GBZX0301012293-286461 類篇四十五卷 十卷 第1冊.pdf'), status: 'planned' },
      { edition: '清光緒二年（1876）歸安姚覲元川東官舍刻本', holder: NLC, href: archive('nlc-892-312002104694-328181'), status: 'planned' }] },
    { title: '切韻', by: '陸法言', editions: [
      { edition: 'S.2071（切三）', holder: BL, status: 'planned' },
      { edition: 'S.2055（切二）', holder: BL, status: 'planned' },
      { edition: 'P.2017', holder: BNF, href: 'https://gallica.bnf.fr/ark:/12148/btv1b8300638b', status: 'planned' }] },
    { title: '刊謬補缺切韻', by: '王仁昫', editions: [
      { edition: 'P.2011（王一）', holder: BNF, status: 'planned' },
      { edition: '裴務齊正字本（王二）', holder: '故宮博物院', status: 'planned' },
      { edition: '宋濂跋本（王三）', holder: '故宮博物院', status: 'planned' }] },
    { title: '廣韻', by: '陳彭年、丘雍等', editions: [
      { edition: '清康熙四十三年（1704）序張氏澤存堂刊本', holder: '東京大學東洋文化研究所', href: commons('IOC.UTokyo-000145 廣韻五卷康熙四十三年序張氏澤存堂刊本 卷首.pdf'), status: 'underway' },
      { edition: '張氏澤存堂刊本（韻典網照片）', holder: '', href: 'https://github.com/nk2028/tshet-uinh-images/tree/54ebf944fb7cb73eaafe563a68fc77f2d6740b24/廣韻/澤存堂本', status: 'underway' },
      { edition: '大宋重修廣韻 宋刊本', holder: NDL, href: ndl(2545272), status: 'planned' },
      { edition: '鉅宋廣韻 宋乾道五年（1169）建寧府黃三八郎刻本（中華再造善本影印）', holder: '上海圖書館', href: commons('ZHSY000074 鉅宋廣韻五卷 (宋)陳彭年等 撰 宋乾道五年建寧府黃三八郎刻本.pdf'), status: 'planned' }] },
    { title: '集韻', by: '丁度等', editions: [
      { edition: '宋刻本（中華再造善本影印）', holder: NLC, href: commons('ZHSY000075 集韻十卷 (宋)丁度等 撰 宋刻本.pdf'), status: 'planned' },
      { edition: '清康熙四十五年（1706）揚州使院刊本', holder: NCL, href: commons('NCL-01074 01 集韻.pdf'), status: 'planned' }] },
    { title: '改併五音集韻', by: '韓道昭', editions: [
      { edition: '金崇慶元年（1212）荊珍刻本', holder: NLC, href: commons('NLC892-412004000251-401912 崇慶新雕改併五音集韻 十五卷 第1冊.pdf'), status: 'planned' },
      { edition: '元前至元二十六年（1289）琴臺張仁刊本', holder: NCL, href: commons('NCL-01097 改併五音集韻.pdf'), status: 'planned' },
      { edition: '明成化六年至七年（1470–1471）金臺大隆福寺刊本', holder: NCL, href: commons('NCL-01099 改併五音集韻.pdf'), status: 'planned' }] },
    { title: '古今韻會舉要', by: '黃公紹編、熊忠舉要', editions: [
      { edition: '元刊本', holder: NAJ, href: naj(3003216), status: 'planned' },
      { edition: '元刻本（中華再造善本影印）', holder: ZHSY, href: commons('ZHSY000537 古今韻會舉要 三十二冊 宋黃公紹輯 元熊忠舉要 元刻本.pdf'), status: 'planned' }] },
    { title: '新校經史海篇直音', editions: [
      { edition: '明初刊藍印本', holder: NCL, href: commons('NCL-01012 經史海篇直音.pdf'), status: 'planned' },
      { edition: '明刊本', holder: HYL, href: harvard(4006515), status: 'planned' },
      { edition: '明刊本', holder: NAJ, href: naj(1072081), status: 'planned' }] },
    { title: '直音篇', by: '章黼', editions: [
      { edition: '明成化十七年（1481）刻，嘉靖二十四年、萬曆九年遞修本', holder: HYL, href: harvard(2530911), status: 'planned' },
      { edition: '明萬曆六年（1578）資政左室刊本', holder: NAJ, href: naj(1070357), status: 'planned' }] },
    { title: '併音連聲韻學集成', by: '章黼', editions: [
      { edition: '新編 明成化十七年（1481）刻，嘉靖二十四年、萬曆九年遞修本', holder: HYL, href: harvard(2530911), status: 'planned' },
      { edition: '重訂 明萬曆三十四年（1606）練川明德書院刊本', holder: NAJ, href: naj(1071464), status: 'planned' },
      { edition: '重訂 明萬曆三十四年（1606）練川明德書院刊本', holder: NCL, href: commons('NCL-01135 01 重訂併音連聲韻學集成.pdf'), status: 'planned' }] },
    { title: '古俗字略', by: '陳士元', editions: [
      { edition: '明刊本', holder: NAJ, href: naj(1072003), status: 'planned' },
      { edition: '歸雲別集本 清道光十三年（1833）寶善堂', holder: '清華大學圖書館', href: commons('CADAL01024820 歸雲別集·古俗字略.djvu'), status: 'planned' }] },
    { title: '合併字學篇韻便覽', by: '徐孝輯、張元善校刊', editions: [
      { edition: '版本未詳', holder: PKU, href: commons('CADAL02046203 合併字學篇韻便覽（一）.djvu'), status: 'planned' },
      { edition: '合併字學集韻 明萬曆三十四年（1606）永城張元善校刊本', holder: NCL, href: commons('NCL-01151 01 合併字學集韻.pdf'), status: 'planned' },
      { edition: '合併字學集韻 明刻本（館藏著錄作明嘉靖間）', holder: NLC, href: commons('NLC892-CBM0512-368901 合併字學集韻 十卷 一卷聲領率譜一卷 第1冊.pdf'), status: 'planned' }] },
    { title: '字彙', by: '梅膺祚', editions: [
      { edition: '明萬曆四十三年（1615）刊本', holder: HYL, href: harvard(3964984), status: 'planned' },
      { edition: '明萬曆四十三年（1615）江東梅氏原刊本', holder: NCL, href: commons('NCL-01037 1 字彙.pdf'), status: 'planned' },
      { edition: '明刊本', holder: NAJ, href: naj(1070075), status: 'planned' }] },
    { title: '正字通', by: '張自烈', editions: [
      { edition: '清白鹿書院刊本', holder: NAJ, href: naj(1070079), status: 'planned' },
      { edition: '清畏堂刊本 康熙二十四年（1685）序', holder: HYL, href: harvard(4322345), status: 'planned' },
      { edition: '清畏堂 清康熙間刻本', holder: NLC, href: archive('nlc-892-312002104797-327428'), status: 'planned' }] },
    { title: '爾雅', editions: [] },
    { title: '方言', by: '揚雄', editions: [] },
    { title: '釋名', by: '劉熙', editions: [] },
    { title: '廣雅', by: '張揖', editions: [] },
    { title: '經典釋文', by: '陸德明', editions: [] },
    { title: '一切經音義', by: '玄應', editions: [] },
    { title: '一切經音義', by: '慧琳', editions: [] },
    { title: '干祿字書', by: '顏元孫', editions: [] },
    { title: '五經文字', by: '張參', editions: [] },
    { title: '九經字樣', by: '唐玄度', editions: [] },
    { title: '佩觿', by: '郭忠恕', editions: [] },
    { title: '汗簡', by: '郭忠恕', editions: [] },
    { title: '古文四聲韻', by: '夏竦', editions: [] },
    { title: '六書故', by: '戴侗', editions: [] },
    { title: '洪武正韻', by: '樂韶鳳等', editions: [] },
    { title: '中原音韻', by: '周德清', editions: [] },
    { title: '蒙古字韻', editions: [] },
    { title: '字彙補', by: '吳任臣', editions: [] },
    { title: '康熙字典', by: '張玉書等', editions: [] },
  ] },
  { key: 'collections', works: [] },
  { key: 'miscellanies', works: [] },
  { key: 'technical', works: [] },
  { key: 'gazetteers', works: [] },
]

/** The language a holder's name is written in, where it is not Chinese. */
export const HOLDER_LANG = { [NDL]: 'ja', [NAJ]: 'ja', '京都大学附属図書館': 'ja', [HYL]: 'en', [IOM]: 'en', [BL]: 'en', [BNF]: 'fr', '東京大學東洋文化研究所': 'ja' }

/** How many rows stand at each status; a work with no edition chosen is one planned row. */
export function tally(stages = STAGES) {
  const count = { published: 0, underway: 0, planned: 0 }
  for (const stage of stages) for (const work of stage.works) {
    if (!work.editions.length) count.planned++
    for (const edition of work.editions) count[edition.status]++
  }
  return count
}
