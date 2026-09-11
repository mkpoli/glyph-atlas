from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from . import registry, tables

app = typer.Typer(help="Build and inspect the character-shape dataset.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Build and inspect the character-shape dataset."""


@app.command()
def sources() -> None:
    """List the registered upstream sources with their licences."""
    for source in registry.load():
        typer.echo(f"{source.id:<20} {source.licence.value:<14} {source.name}")


if __name__ == "__main__":
    app()


import_app = typer.Typer(help="Import an upstream dataset into the tables.")
app.add_typer(import_app, name="import")


@import_app.command("codh")
def import_codh(
    zip_path: Annotated[Path, typer.Argument(help="per-book zip from codh.rois.ac.jp/char-shape")],
    out: Annotated[Path, typer.Option(help="directory for documents, pages and units")] = Path("work/codh"),
    title: Annotated[str | None, typer.Option(help="book title, when known")] = None,
) -> None:
    """Import one book of the 日本古典籍くずし字データセット."""
    from .importers import codh

    document, pages, units = codh.read(zip_path, title=title)
    tables.write(out / "documents.parquet", [document])
    tables.write(out / "pages.parquet", pages)
    n = tables.write(out / "units.parquet", units)
    typer.echo(f"{document.id}: {len(pages)} pages, {n} units -> {out}")
