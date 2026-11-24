# Small seal matcher

Names the three of Unicode's 11,328 small seal characters nearest in drawing to each seal form CODH's
篆書字体データセット cuts from a title other than 説文解字. `scripts/build_tensho_equivalents.py` takes a
seal character from among them only where Unicode gives it to the 親字 the form is filed under.

    PYTHONPATH=src python models/seal/match.py work/tensho/dl --font cache/fonts/Sealie-Medium.ttf \
        --seal-sources cache/ucd/SealSources.txt

It needs the zips of every book of `data/sources/codh-tensho.yaml` (405 MB) and Sealie Medium, which
`scripts/build_seal_font.py` downloads. `match.py` describes the model. The checkpoints are written to
`models/seal/artifacts/` and not committed; `data/tensho/ink.tsv` holds what it found, and
`metrics.json` the report of that run.

## Sources

- CODH 篆書字体データセット v2.0, doi:10.20676/00000390, CC BY-SA 4.0: the crops of TE00039 trained on, and in
  the second round 10,425 crops of the other titles whose first answer agrees with their 親字.
- Sealie (iebb/sealie), SIL Open Font License 1.1: the glyphs trained on.
- Unicode 18.0 `SealSources.txt`: the characters.

## Result

One run (seed 0, 60 epochs a model, 551 s on an RTX 5070 Ti), on forms the answering model was not trained on:

| forms | crops | first answer right | among the first three | among them after the first round |
| --- | --- | --- | --- | --- |
| 説文解字, one crop in ten | 1,038 | 94.6% | 98.4% | 97.8% |
| 新撰篆書字典 巻1, alone under their 親字 | 1,578 | 92.1% | 95.6% | 89.9% |
| 新撰篆書字典 巻2 | 2,263 | 93.6% | 96.8% | 94.2% |
| 新撰篆書字典 巻3 | 2,384 | 94.3% | 97.2% | 94.8% |
| 聯珠篆文 | 12 | 66.7% | 66.7% | 66.7% |

These say how often the small seal form is found. A form alone under its 親字 is scored against the one
seal character of that 親字, which some are not drawn as, so the figures of 新撰篆書字典 are a floor; 12
crops say little of 聯珠篆文.

How often a placement is right was read by eye, on a random 200 of the 6,088 (`sample.tsv`): 198 are the
same parts in the same arrangement as their seal character, some of them in a bronze hand, and two
(彘, 䜌) are another form of the same character. The 95% interval is up to 4 in 100.
