# Licensing

The dataset licence is CC BY-SA 4.0. It covers the annotations and the compilation made here. Each
record also carries the rights of its image and of its text, because the upstream terms differ and
because a scan of a public-domain page may carry no copyright of its own.

## How upstream terms compose

| Upstream terms | In a CC BY-SA 4.0 release |
| --- | --- |
| Public domain, PDM 1.0 | included; the record keeps the public-domain mark, which describes a status and is never replaced by the dataset licence |
| CC0 1.0 | included; a waiver, recorded as such |
| CC BY 4.0 | included with attribution |
| CC BY-SA 4.0 | included with attribution |
| CC BY-SA 3.0 | included as an adaptation, attribution and the 3.0 notice kept |
| CC BY-SA 2.1 JP (MJ table, NINJAL images) | included as an adaptation: article 5 of that licence allows derivative works under a newer version or another jurisdiction's licence with the same elements; notices and modifications recorded |
| 공공누리 제1유형(출처표시), KOGL Type 1 (국립한글박물관) | included with the source named; the record keeps the KOGL notice, and the image itself is never described as CC BY-SA |
| Bespoke free-reuse terms (京都大学 RMDA, 国立公文書館, 木簡庫) | included when the terms allow copying, modification and redistribution; the terms URI and the obligations are stored on the record, and the image itself is never described as CC BY-SA |
| NC, ND, research-only, permission-required or unstated, on a work dated up to 1900 or undated | included as public domain (see below); the holder's statement is kept in `holder_terms` |
| NC, ND, research-only, permission-required or unstated, on a work dated after 1900 | never in a release build; the record may exist with coordinates and no crop, provided the text and metadata it carries are themselves clear |

## Image holders seen in the upstreams

| Holder | Terms | Evidence |
| --- | --- | --- |
| 国立国会図書館, インターネット公開（保護期間満了） | free use, no application | https://www.ndl.go.jp/jp/use/reproduction/index.html |
| 国文学研究資料館 (国書データベース) | per item: PDM, CC BY, CC BY-SA, CC BY-NC, CC BY-NC-SA, CC BY-ND, CC BY-NC-ND, RightsStatements labels, all rights reserved | https://kokusho.nijl.ac.jp/page/terms.html |
| CODH 日本古典籍データセット | CC BY-SA 4.0 | http://codh.rois.ac.jp/pmjt/ |
| 東京大学史料編纂所 くずし字データセット | CC BY 4.0 | https://lab.hi.u-tokyo.ac.jp/datasets/kuzushiji |
| 漢字字体規範史データセット（HNG）基本データセット | CC BY-SA 4.0, or GPL-2.0-or-later at the user's choice; the atlas takes CC BY-SA 4.0 | https://github.com/chise/hng-basic-data/blob/e2174a30844b8100c34af1c0dbe1e301f186883e/README.md |
| 京都大学貴重資料デジタルアーカイブ | free reuse with credit, change notice and link; some holdings need approval; a bulk crop package is to be confirmed with the library | https://rmda.kulib.kyoto-u.ac.jp/reuse |
| Gallica (Bibliothèque nationale de France) | non-commercial reuse free with the source line; commercial reuse under a paid licence; metadata Etalab open licence | https://gallica.bnf.fr/edit/und/conditions-dutilisation-des-contenus-de-gallica |
| 国立公文書館デジタルアーカイブ | free reuse; metadata CC0 | https://www.digital.archives.go.jp/secondary-use |
| ADEAC (会津若松市立会津図書館, 岡山市立図書館 and other municipal archives) | per item, stated in the IIIF manifest's `rights` and 権利関係 metadata; the items seen are CC BY 4.0 with the holder as `requiredStatement`. 岡山市立図書館 asks to be told of use in publications, broadcasts, exhibitions and products, and for a copy of any publication; items marked 独自規定あり need an enquiry | https://adeac.jp/okayama-city/terms-of-use |
| e国宝 (国立文化財機構) | private use only; licensing through the image office of the institution holding the object | https://emuseum.nich.go.jp/about |
| 琉球大学附属図書館 貴重資料 | own holdings: secondary use without application so far as it infringes no copyright; 阪巻・宝玲文庫 belongs to the University of Hawaiʻi at Mānoa Library, which the page names for enquiries | https://www.lib.u-ryukyu.ac.jp/okinawa/valuable-reference/ |
| 早稲田大学古典籍総合データベース | permission required | https://www.waseda.jp/library/user/using-images/ |
| 慶應義塾大学 | all rights reserved | https://dcollections.lib.keio.ac.jp/ja/about |
| 奈良文化財研究所 木簡庫 | images surveyed by 奈良文化財研究所: free reuse including commercial, with a source line and a modification notice; other institutions' tablets follow their own terms | https://mokkanko.nabunken.go.jp/ja/?c=help |
| 국립한글박물관 아카이브 | per record in `koglCdId`; `CD00167` is 공공누리 제1유형(출처표시), and only those records are collected | https://archives.hangeul.go.kr/ko/M000000614/html/view |
| 국가유산청 국가유산 검색 Open API | per photograph in `imageNuri`; `A` is 공공누리 제1유형(출처표시), and only those photographs are collected | https://www.khs.go.kr/html/HtmlPage.do?pg=/publicinfo/pbinfo3_0202.jsp&mn=NS_04_04_02 |
| 국립중앙도서관 디지털도서관 원문보기 | no 공공누리 mark; `[관외이용-무료]` means copyright has expired or the rightsholder gave permission to read outside the library, not that reuse beyond viewing and printing is licensed; recorded as `restricted` | https://www.nl.go.kr/NL/contents/N70600000000.do |

Honkoku-Lines' provider table lists 29 institutions with the licence of each
(https://huggingface.co/datasets/yuta1984/honkoku-lines); it is the reference for holders reached
through みんなで翻刻.

## Text

- みんなで翻刻 transcriptions: CC BY-SA 4.0, stated in the honkoku-data README and on the project wiki.
  No terms page exists on the application itself.
- Honkoku-Lines transcriptions and metadata: CC BY-SA 4.0; construction code MIT.
- NDL古典籍OCR学習用データセット: CC BY-SA 4.0.
- Unicode data files and the IVD: Unicode License v3, notice reproduced in `ATTRIBUTION.md`.
- GlyphWiki glyphs: free for any use without attribution, per the site's licence page.
- CHISE IDS: GPL-2.0-or-later, excluded from the CC BY-SA core; a separately licensed aggregate is
  possible in principle and is not planned.
- Component tables (`data/vocab/han-ids.tsv`, `data/vocab/han-component-forms.tsv`): BabelStone
  IDS.TXT, whose header states that IDS sequences are facts not eligible for copyright and waives any
  claim to the file's format, used as public domain and credited; Unicode's
  EquivalentUnifiedIdeograph.txt (Unicode License v3); cjkvi-variants `radical-variants.txt`, used as
  public domain like the rest of that database. BabelStone's data descends in part from Kawabata
  Taichi's IDS, which derives from CHISE; the atlas reads only BabelStone's file.
- 異体字 relations (`data/vocab/kanji-variants.tsv`): Unihan and UnicodeData (Unicode License v3),
  Wikidata P5475 (CC0), yitizi (CC0), OpenCC (Apache-2.0), MJ縮退マップ (CC BY-SA 2.1 JP), HNG
  (CC BY-SA 4.0) and cjkvi-variants. A variant relation between two characters is a fact without
  creative expression, so a relation table that states no licence (cjkvi-variants) is used as public
  domain, and Wikidata's statements are used under CC0 whichever dictionary they cite.

## Attribution

Each release carries `ATTRIBUTION.md`, generated from the rights fields: one entry per source and per
holder present in the release, with the attribution string the holder asks for. The CODH string is
『日本古典籍くずし字データセット』（国文研ほか所蔵／CODH加工）doi:10.20676/00000340.

## Copyright in crops

A faithful photograph of a flat original is not a work under Article 2 of the Japanese Copyright
Act: the Tokyo District Court held so for photographs reproducing prints (東京地判平成10年11月30日,
版画写真事件; summary at https://ja.wikipedia.org/wiki/版画写真事件 and
https://iplaw.hatenadiary.org/entry/19981130/p1). A scan of a page of a pre-modern book, and a
rectangle cut from it by coordinates, is such a reproduction of a public-domain work, so it carries
no copyright of its own. In the EU, Article 14 of Directive (EU) 2019/790 states the same for
reproductions of public-domain visual works.

The dataset therefore records the page images of its books as public domain whatever terms their
holder attaches to the photographs. `Document` applies the rule: where `image_rights` holds a
restricting statement (NC, ND, RS-NOC-CR, restricted or unstated), `licence` becomes PD and the
statement moves to `holder_terms`, where it stays as evidence of what the holder asks. A document
dated after 1900 may be a work still in copyright and keeps its holder's terms; an undated document
is taken as pre-modern, which is the dataset's scope. Holder terms are contractual and bind those who
accepted them; the attribution and source lines holders ask for are kept on every record.

A compilation may be protected under Articles 12 and 12-2. CC BY-SA 4.0 claims nothing over material
that needs no permission (its section 2(a)(2)). The dataset states its own rights as applying to the
annotations and the compilation. Contributors agree to CC BY-SA 4.0 for their annotations; a
correction and withdrawal procedure for holders and contributors is in `CONTRIBUTING.md`.
