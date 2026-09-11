# Licensing

The dataset licence is CC BY-SA 4.0. It covers the annotations and the compilation made here. Each
record also carries the rights of its image and of its text, because the upstream terms differ and
because a scan of a public-domain page may carry no copyright of its own.

## How upstream terms compose

| Upstream terms | In a CC BY-SA 4.0 release |
| --- | --- |
| Public domain, PDM 1.0, CC0 | included; the record keeps the public-domain mark |
| CC BY 4.0 | included with attribution |
| CC BY-SA 4.0 | included with attribution |
| CC BY-SA 3.0 | included as an adaptation, attribution and the 3.0 notice kept |
| CC BY-SA 2.1 JP (MJ table, NINJAL images) | to be confirmed against Creative Commons guidance on ported licences before a release |
| Bespoke free-reuse terms (京都大学 RMDA, 国立公文書館) | included when the terms allow copying, modification and redistribution; their conditions are met in the record |
| NC, ND, research-only, permission-required | never in a release build; the record may exist with coordinates and no crop |
| Unstated | treated as permission-required until the holder answers |

## Image holders seen in the upstreams

| Holder | Terms | Evidence |
| --- | --- | --- |
| 国立国会図書館, インターネット公開（保護期間満了） | free use, no application | https://www.ndl.go.jp/jp/use/reproduction/index.html |
| 国文学研究資料館 (国書データベース) | per item: PDM, CC BY, CC BY-SA, CC BY-NC, CC BY-NC-SA, CC BY-ND, CC BY-NC-ND, all rights reserved | https://kokusho.nijl.ac.jp/page/terms.html |
| CODH 日本古典籍データセット | CC BY-SA 4.0 | http://codh.rois.ac.jp/pmjt/ |
| 東京大学史料編纂所 くずし字データセット | CC BY 4.0 | https://lab.hi.u-tokyo.ac.jp/datasets/kuzushiji |
| 京都大学貴重資料デジタルアーカイブ | free reuse with credit, change notice and link; some holdings need approval | https://rmda.kulib.kyoto-u.ac.jp/reuse |
| 国立公文書館デジタルアーカイブ | free reuse; metadata CC0 | https://www.digital.archives.go.jp/secondary-use |
| 早稲田大学古典籍総合データベース | permission required | https://www.waseda.jp/library/user/using-images/ |
| 慶應義塾大学 | all rights reserved | https://dcollections.lib.keio.ac.jp/ja/about |
| 奈良文化財研究所 木簡庫 | all rights reserved | https://www.nabunken.go.jp/info/law.html |

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
- CHISE IDS: GPL-2.0-or-later, excluded.

## Attribution

Each release carries `ATTRIBUTION.md`, generated from the rights fields: one entry per source and per
holder present in the release, with the attribution string the holder asks for. The CODH string is
『日本古典籍くずし字データセット』（国文研ほか所蔵／CODH加工）doi:10.20676/00000340.

## Copyright in crops

A faithful scan of a public-domain page and a rectangle cut from it by coordinates add no creative
expression under Article 2 of the Japanese Copyright Act, so the crop itself is likely unprotected;
a compilation may be protected under Articles 12 and 12-2. CC BY-SA 4.0 claims nothing over
material that needs no permission (its section 2(a)(2)). The dataset therefore states its own rights
as applying to the annotations and the compilation. This is a reading of the statute, not legal
advice.
