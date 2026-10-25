# Special forms

Abbreviated forms of a character that no code point covers, such as the ん-like and zigzag forms of 候 in
Japanese manuscripts. Each file here records which forms a person found in one character's crops.

- `<code>.yaml`: the forms, the shape clusters each is made of, and notes on the evidence. The format is
  described in `src/glyph_atlas/special_forms/decide.py`.
- `<code>.tsv`: the form of every crop under those decisions (`id`, `site_cluster`, `shape_cluster`,
  `form`), as `report` writes it. It keeps the result when the run itself is gone.
- `drawings/<name>.svg`: a normalised representative of a form, one filled path in a 1000-unit em. These
  are editorial drawings, not attested glyphs.

## Adding a character

```sh
uv sync --all-extras --group dev
systemd-run --user --slice=batch.slice -p MemorySwapMax=0 --same-dir -- uv run atlas forms special run U+XXXX
uv run atlas forms special report U+XXXX
```

`run` needs the corpus pixels under `work/` and a CUDA GPU; it takes 15 to 30 minutes for 12,000 crops and needs at least 500 usable ones. With no
decisions file yet, `report` writes `work/special-forms/U+XXXX/proposal.yaml`, a first grouping of the shape
clusters into six unnamed forms, and a `report.html` built from it. Look at the shape clusters on the
report, then copy the proposal here as `U+XXXX.yaml`, merge or split groups, name the forms, and
mark the form that is the character written out with `written: true`. Run `report` again to apply it, and copy `work/special-forms/U+XXXX/forms.tsv` here as `U+XXXX.tsv`.

The shape cluster numbers belong to one run (`run` in the file). Training again gives other numbers, and
the report then warns that the file was written for another run.
