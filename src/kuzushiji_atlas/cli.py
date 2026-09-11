"""The `atlas` command line.

Sub-applications group the commands by the table or the service they act on. The logic lives in the
modules: this file parses arguments, calls one function, and prints what it returns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from . import registry, tables

app = typer.Typer(help="Build and inspect the character-shape dataset.", no_args_is_help=True)
tables_app = typer.Typer(help="Write, read, validate and merge the dataset tables.", no_args_is_help=True)
images_app = typer.Typer(help="Fetch page images, fill their sizes, cut crops.", no_args_is_help=True)
rights_app = typer.Typer(help="Resolve licences and report the rights of a dataset.", no_args_is_help=True)
import_app = typer.Typer(help="Import an upstream dataset into the tables.", no_args_is_help=True)
pilot_app = typer.Typer(help="Assemble and measure the pilot packages.", no_args_is_help=True)
eval_app = typer.Typer(help="Measure a prediction against adjudicated truth.", no_args_is_help=True)
review_app = typer.Typer(help="Serve and apply editorial reviews.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Build and inspect the character-shape dataset."""


@app.command()
def sources() -> None:
    """List the registered upstream sources with their licences."""
    for source in registry.load():
        typer.echo(f"{source.id:<20} {source.licence.value:<14} {source.name}")


# Tables -----------------------------------------------------------------------------------------


@tables_app.command("validate")
def tables_validate(
    directory: Annotated[Path, typer.Argument(help="dataset directory")],
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="print nothing when valid")] = False,
) -> None:
    """Check the referential integrity of every table in a dataset directory."""
    errors = tables.Dataset(directory).validate()
    for error in errors:
        typer.echo(error)
    if errors:
        typer.echo(f"{len(errors)} errors in {directory}")
        raise typer.Exit(1)
    if not quiet:
        typer.echo(f"{directory}: valid")


@tables_app.command("merge")
def tables_merge(
    directories: Annotated[list[Path], typer.Argument(help="dataset directories to concatenate")],
    out: Annotated[Path, typer.Option(help="output dataset directory")],
    command: Annotated[str | None, typer.Option(help="command recorded in MANIFEST.json")] = None,
) -> None:
    """Concatenate dataset directories, collapse identical rows and write the result."""
    datasets = [tables.Dataset(directory) for directory in directories]
    try:
        counts = datasets[0].merge(datasets[1:], out, command=command)
    except tables.DuplicateIdError as error:
        typer.echo(str(error))
        raise typer.Exit(1) from error
    for name, rows in counts.items():
        typer.echo(f"{name:<12} {rows:>10}")
    typer.echo(f"-> {out}")


@tables_app.command("stats")
def tables_stats(directory: Annotated[Path, typer.Argument(help="dataset directory")]) -> None:
    """Print the row counts of a dataset directory and its manifest."""
    import pyarrow.parquet as pq

    dataset = tables.Dataset(directory)
    total = 0
    for name, path in dataset.tables.items():
        if path is None:
            typer.echo(f"{name:<12} {'-':>10}")
            continue
        rows = sum(pq.ParquetFile(file).metadata.num_rows for file in sorted(path.glob("*.parquet"))) if path.is_dir() else pq.ParquetFile(path).metadata.num_rows
        total += rows
        typer.echo(f"{name:<12} {rows:>10}")
    typer.echo(f"{'total':<12} {total:>10}")
    manifest = directory / tables.MANIFEST_NAME
    if manifest.exists():
        typer.echo(json.dumps(json.loads(manifest.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))


# Images -----------------------------------------------------------------------------------------


@images_app.command("info")
def images_info(
    pages: Annotated[Path, typer.Argument(help="pages table to fill width and height in")],
    limit: Annotated[int | None, typer.Option(help="stop after this many pages")] = None,
) -> None:
    """Fill the width and height of every page from its IIIF `info.json`."""
    from . import images

    filled, failed = images.fill_sizes(pages, limit=limit)
    typer.echo(f"{filled} pages sized, {failed} failed -> {pages}")
    if failed:
        raise typer.Exit(1)


@images_app.command("fetch")
def images_fetch(
    pages: Annotated[Path, typer.Argument(help="pages table")],
    limit: Annotated[int | None, typer.Option(help="stop after this many pages")] = None,
    document: Annotated[str | None, typer.Option(help="only this document id")] = None,
    pages_filter: Annotated[str | None, typer.Option("--pages", help="comma-separated page ids")] = None,
) -> None:
    """Fetch full-size page images into the image cache."""
    from . import images

    wanted = pages_filter.split(",") if pages_filter else None
    fetched, skipped, failed = images.fetch_pages(pages, limit=limit, document=document, pages=wanted)
    typer.echo(f"{fetched} fetched, {skipped} already cached, {failed} failed")
    if failed:
        raise typer.Exit(1)


@images_app.command("crop")
def images_crop(
    units: Annotated[Path, typer.Argument(help="units table")],
    out: Annotated[Path, typer.Option(help="directory for the crops")],
    limit: Annotated[int | None, typer.Option(help="stop after this many units")] = None,
) -> None:
    """Write one JPEG per unit, named after the unit id."""
    from . import images

    written, skipped = images.write_crops(units, out, limit=limit)
    typer.echo(f"{written} crops written, {skipped} skipped -> {out}")


# Rights -----------------------------------------------------------------------------------------


@rights_app.command("table")
def rights_table(
    out: Annotated[Path | None, typer.Option(help="write the Markdown here instead of stdout")] = None,
) -> None:
    """Print the licence vocabulary as Markdown for docs/licensing.md."""
    from . import rights

    markdown = rights.markdown_table()
    if out:
        out.write_text(markdown, encoding="utf-8")
        typer.echo(f"{len(markdown.splitlines())} lines -> {out}")
    else:
        typer.echo(markdown)


# Import -----------------------------------------------------------------------------------------


@import_app.command("codh")
def import_codh(
    zip_path: Annotated[Path | None, typer.Argument(help="per-book zip from codh.rois.ac.jp/char-shape")] = None,
    all_books: Annotated[bool, typer.Option("--all", help="import every book of the dataset")] = False,
    books: Annotated[str | None, typer.Option("--books", help="comma-separated book ids")] = None,
    from_zip: Annotated[Path | None, typer.Option("--from-zip", help="a zip holding every book")] = None,
    out: Annotated[Path, typer.Option(help="directory for documents, pages and units")] = Path("work/codh"),
    title: Annotated[str | None, typer.Option(help="book title, when known")] = None,
) -> None:
    """Import the 日本古典籍くずし字データセット."""
    from .importers import codh

    if all_books or from_zip or books:
        counts = codh.import_all(out, books=books.split(",") if books else None, from_zip=from_zip)
        for name, rows in counts.items():
            typer.echo(f"{name:<12} {rows:>10}")
        return
    if zip_path is None:
        raise typer.BadParameter("give a zip path, or --all")
    document, pages, units = codh.read(zip_path, title=title)
    tables.write_table(out / "documents.parquet", [document])
    tables.write_table(out / "pages.parquet", pages)
    tables.write_table(out / "units.parquet", units)
    tables.Dataset(out).merge([], out, command=f"atlas import codh {zip_path}")
    typer.echo(f"{document.id}: {len(pages)} pages, {len(units)} units -> {out}")


for name, module in (
    ("tables", tables_app),
    ("images", images_app),
    ("rights", rights_app),
    ("import", import_app),
    ("pilot", pilot_app),
    ("eval", eval_app),
    ("review", review_app),
):
    app.add_typer(module, name=name)


if __name__ == "__main__":
    app()
