# Contributing

## Annotations

Every annotation saved on glyphatlas.org or sent by pull request is released under CC BY-SA 4.0
(`LICENSE-DATA`): reviews and the problems they flag, corrected characters and readings, crop boxes
and splits, the written form chosen for a crop and the crops grouped under it, 字母, variants,
document metadata, and notes. This holds for a visitor who saves without signing in to an account,
whose saves are made under an anonymous name. A contributor keeps the copyright, if any, in their
annotations and is credited in `ATTRIBUTION.md` of every release that contains them, under their
display name. Saving or submitting an annotation is agreement to these terms.

## Sources

A new upstream gets one file in `data/sources/` with the licence and the URL of the page that states
it, the format, and the counts as read on a named date. Images and texts under NC, ND, research-only
or permission-only terms may be registered and used for coordinates, and never enter a release build.

## Corrections and withdrawal

A holding institution, a transcriber or a contributor who finds a record that misstates rights,
misattributes a source, or reproduces material they did not license may open an issue or write to
the maintainer. Records named in a request are removed from the next release build while the
request is checked, and the release notes list what was removed and why.

## Code

Python 3.12, managed with `uv`. `uv run pytest` and `uv run ruff check` before a pull request. One
change per pull request.
