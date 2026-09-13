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
audit_app = typer.Typer(help="Draw a blind audit sample and publish its precision.", no_args_is_help=True)
ainu_app = typer.Typer(help="Derive line boxes for the アイヌ関連資料 records.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Build and inspect the character-shape dataset."""


@app.command()
def sources() -> None:
    """List the registered upstream sources with their licences."""
    for source in registry.load():
        typer.echo(f"{source.id:<20} {source.licence.value:<14} {source.name}")


@app.command()
def coverage(
    out: Annotated[Path, typer.Option(help="write the coverage table here")] = Path("work/coverage.tsv"),
    directories: Annotated[
        list[Path] | None,
        typer.Argument(help="dataset directories to join; the three transcription sets by default"),
    ] = None,
) -> None:
    """Join the entry ids of the transcription datasets and count the pages without lines."""
    from . import coverage as coverage_module

    wanted = directories or [
        Path("work/honkoku-lines"),
        Path("work/ndl-minhon"),
        Path("work/honkoku-data"),
    ]
    counts = coverage_module.build(list(wanted), out)
    for name, value in counts.items():
        typer.echo(f"{name:<40} {value:>8}")
    typer.echo(f"-> {out}")


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
    fetched, skipped, failed = images.fetch_pages(pages, limit=limit, document=document, pages_filter=wanted)
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


@rights_app.command("resolve")
def rights_resolve(
    directory: Annotated[Path, typer.Argument(help="dataset directory to reconcile")],
    limit: Annotated[int | None, typer.Option(help="most manifests to fetch in this run")] = None,
    recheck: Annotated[bool, typer.Option("--recheck", help="refetch the holder terms pages")] = False,
    out: Annotated[Path | None, typer.Option(help="write the recheck details here")] = None,
) -> None:
    """Gather the rights evidence of every document and set its image rights by precedence."""
    from . import reconcile

    counts = reconcile.resolve_directory(directory, limit=limit, recheck=recheck, out=out)
    for name, value in counts.items():
        typer.echo(f"{name:<18} {value:>8}")


@rights_app.command("report")
def rights_report(
    directory: Annotated[Path, typer.Argument(help="dataset directory to report on")],
    out: Annotated[Path | None, typer.Option(help="write the Markdown here instead of stdout")] = None,
    limit: Annotated[int | None, typer.Option(help="most disagreement rows to list")] = None,
) -> None:
    """Print the records by licence and by eligibility, with the disagreements listed."""
    from . import reconcile

    markdown = reconcile.report(directory, limit=limit)
    if out:
        out.write_text(markdown, encoding="utf-8")
        typer.echo(f"-> {out}")
    else:
        typer.echo(markdown)


@rights_app.command("attribution")
def rights_attribution(
    directory: Annotated[Path, typer.Argument(help="dataset directory to credit")],
    out: Annotated[Path, typer.Option(help="write ATTRIBUTION.md here")] = Path("ATTRIBUTION.md"),
) -> None:
    """Write the credit lines and obligations of every source and holder present."""
    from . import reconcile

    markdown = reconcile.attribution(directory)
    out.write_text(markdown, encoding="utf-8")
    typer.echo(f"{len(markdown.splitlines())} lines -> {out}")


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


# Pilot and evaluation ---------------------------------------------------------------------------


@pilot_app.command("export")
def pilot_export(
    out: Annotated[Path, typer.Argument(help="directory for the page packages")],
    directory: Annotated[Path, typer.Option(help="dataset directory holding the lines")] = Path("work/honkoku-lines"),
    group: Annotated[str | None, typer.Option(help="calibration or heldout")] = None,
    items: Annotated[str | None, typer.Option(help="comma-separated item ids")] = None,
    page_ids: Annotated[str | None, typer.Option("--pages", help="comma-separated page ids")] = None,
    no_images: Annotated[bool, typer.Option("--no-images", help="leave the page images out")] = False,
) -> None:
    """Write one package per selected pilot page."""
    from . import pilot

    counts = pilot.export(
        out,
        directory,
        group=group,
        items=items.split(",") if items else None,
        pages=page_ids.split(",") if page_ids else None,
        images=not no_images,
    )
    for name, value in counts.items():
        typer.echo(f"{name:<8} {value:>6}")
    typer.echo(f"-> {out}")


@pilot_app.command("images")
def pilot_images(
    directory: Annotated[Path, typer.Argument(help="dataset directory holding the pages")],
    group: Annotated[str | None, typer.Option(help="calibration or heldout")] = None,
    items: Annotated[str | None, typer.Option(help="comma-separated item ids")] = None,
    pause: Annotated[float | None, typer.Option(help="seconds between requests to one host")] = None,
    per_item: Annotated[int | None, typer.Option(help="keep the first n pages of each item")] = None,
) -> None:
    """Fetch the page images of the selected pilot pages into the cache."""
    from . import pilot

    counts = pilot.fetch_page_images(
        directory,
        group=group,
        items=items.split(",") if items else None,
        pause=pause,
        per_item=per_item,
    )
    for name, value in counts.items():
        typer.echo(f"{name:<8} {value:>6}")


@eval_app.command("alignment")
def eval_alignment(
    truth: Annotated[Path, typer.Option(help="dataset directory of adjudicated units")],
    pred: Annotated[Path, typer.Option(help="dataset directory of predicted units")],
    pages: Annotated[str | None, typer.Option(help="comma-separated page ids")] = None,
    policy: Annotated[str, typer.Option(help="equivalence policy for the label comparison")] = "align-v1",
    out: Annotated[Path | None, typer.Option(help="write the Markdown here instead of stdout")] = None,
) -> None:
    """Match predicted units to truth units and print the measures."""
    from . import evaluate, refs, tables

    wanted = set(pages.split(",")) if pages else None
    truth_units = _units_of(tables.Dataset(truth), wanted)
    predicted_units = _units_of(tables.Dataset(pred), wanted)

    def label(unit: object) -> str:
        text = unit.unicode or unit.reading or ""
        if not unit.unicode:
            return text
        try:
            chars = refs.from_code_points([unit.unicode])
        except (ValueError, KeyError):
            return text
        return min(refs.equivalents(chars, policy) or {chars}, default=chars)

    report = evaluate.compare(truth_units, predicted_units, label_of=label)
    markdown = report.markdown()
    if out:
        out.write_text(markdown + "\n", encoding="utf-8")
        typer.echo(f"-> {out}")
    else:
        typer.echo(markdown)


def _units_of(dataset: tables.Dataset, pages: set[str] | None) -> list:
    if dataset.tables["units"] is None:
        raise typer.BadParameter("the directory has no units table")
    found = []
    for batch in dataset.scan("units"):
        found.extend(unit for unit in batch if pages is None or unit.page_id in pages)
    return found


# Review -----------------------------------------------------------------------------------------


@review_app.command("serve")
def review_serve(
    directory: Annotated[Path, typer.Argument(help="dataset directory to review")],
    port: Annotated[int, typer.Option(help="port to listen on")] = 8770,
    host: Annotated[str, typer.Option(help="interface to bind")] = "127.0.0.1",
) -> None:
    """Serve the review interface and its API over one dataset directory."""
    from .review import server

    server.serve(directory, port=port, host=host)


@review_app.command("apply")
def review_apply(directory: Annotated[Path, typer.Argument(help="dataset directory to write back")]) -> None:
    """Write the reviewed state to the tables and the events to reviews.jsonl."""
    from .review import store

    for name, rows in store.apply(directory).items():
        typer.echo(f"{name:<12} {rows:>10}")


@review_app.command("replay")
def review_replay(directory: Annotated[Path, typer.Argument(help="dataset directory to rebuild")]) -> None:
    """Rebuild the review state from the tables and the log and check that it matches."""
    from .review import store

    counts = store.replay(directory)
    for name, rows in counts.items():
        typer.echo(f"{name:<12} {rows:>10}")


@import_app.command("honkoku-lines")
def import_honkoku_lines(
    out: Annotated[Path, typer.Option(help="directory for documents, pages and lines")] = Path("work/honkoku-lines"),
    items: Annotated[str | None, typer.Option(help="comma-separated item ids")] = None,
    licence: Annotated[str | None, typer.Option(help="comma-separated image licences to keep")] = None,
    limit: Annotated[int | None, typer.Option(help="stop after this many lines")] = None,
) -> None:
    """Import Honkoku-Lines from the cached Hugging Face files."""
    from .importers import honkoku_lines

    counts = honkoku_lines.import_all(
        out,
        items=items.split(",") if items else None,
        licences=licence.split(",") if licence else None,
        limit=limit,
    )
    for name, rows in counts.items():
        typer.echo(f"{name:<16} {rows:>10}")
    typer.echo(f"-> {out}")


@import_app.command("kokatsuji")
def import_kokatsuji(
    zip_path: Annotated[Path | None, typer.Option("--zip", help="the archive of the 古活字データセット")] = None,
    out: Annotated[Path, typer.Option(help="directory for the tables")] = Path("work/kokatsuji"),
) -> None:
    """Import the 古活字データセット."""
    from .importers import kokatsuji

    counts = kokatsuji.import_all(out, zip_path=zip_path)
    for name, rows in counts.items():
        typer.echo(f"{name:<12} {rows:>10}")
    typer.echo(f"-> {out}")


@import_app.command("ndl-minhon")
def import_ndl_minhon(
    zip_path: Annotated[Path | None, typer.Option("--zip", help="the NDL archive")] = None,
    unpacked: Annotated[Path | None, typer.Option(help="an unpacked copy of the archive")] = None,
    out: Annotated[Path, typer.Option(help="directory for the tables")] = Path("work/ndl-minhon"),
    limit: Annotated[int | None, typer.Option(help="stop after this many pages")] = None,
) -> None:
    """Import the NDL古典籍OCR学習用データセット."""
    from .importers import ndl_minhon

    counts = ndl_minhon.import_all(out, zip_path=zip_path, unpacked=unpacked, limit=limit)
    for name, rows in counts.items():
        typer.echo(f"{name:<12} {rows:>10}")
    typer.echo(f"-> {out}")


@import_app.command("honkoku-data")
def import_honkoku_data(
    clone: Annotated[Path | None, typer.Option(help="a clone of honkoku-data")] = None,
    projects: Annotated[str | None, typer.Option(help="comma-separated project ids")] = None,
    limit: Annotated[int | None, typer.Option(help="stop after this many entries")] = None,
    out: Annotated[Path, typer.Option(help="directory for the tables")] = Path("work/honkoku-data"),
) -> None:
    """Import みんなで翻刻データ v3 and the manifests."""
    from .importers import honkoku_data

    counts = honkoku_data.import_all(
        out,
        clone=clone,
        projects=projects.split(",") if projects else None,
        limit=limit,
    )
    for name, rows in counts.items():
        typer.echo(f"{name:<12} {rows:>10}")
    typer.echo(f"-> {out}")


@import_app.command("ainu-records")
def import_ainu_records(
    out: Annotated[Path, typer.Option(help="directory for the tables")] = Path("work/ainu-records"),
    limit: Annotated[int | None, typer.Option(help="stop after this many entries")] = None,
    only: Annotated[str | None, typer.Option(help="comma-separated entry ids")] = None,
) -> None:
    """Import the アイヌ関連資料 project of みんなで翻刻."""
    from .importers import ainu_records

    counts = ainu_records.import_all(
        out, limit=limit, only=only.split(",") if only else None
    )
    for name, rows in counts.items():
        typer.echo(f"{name:<14} {rows:>10}")
    typer.echo(f"-> {out}")


@import_app.command("hilab")
def import_hilab(
    download: Annotated[bool, typer.Option("--download", help="extract the crops into the cache")] = False,
    limit: Annotated[int | None, typer.Option(help="stop after this many members")] = None,
    out: Annotated[Path, typer.Option(help="directory for the table")] = Path("work/hilab"),
) -> None:
    """Import the 東京大学史料編纂所 くずし字データセット."""
    from .importers import hilab

    counts = hilab.import_all(out, download=download, limit=limit)
    for name, rows in counts.items():
        typer.echo(f"{name:<16} {rows:>10}")
    typer.echo(f"-> {out}")


@audit_app.command("sample")
def audit_sample(
    directory: Annotated[Path, typer.Argument(help="dataset directory holding the run's units")],
    n: Annotated[int, typer.Option(help="units to draw")] = 2000,
    strata: Annotated[str, typer.Option(help="comma-separated strata: document, script, kind")] = "document,script",
    seed: Annotated[int, typer.Option(help="seed of the draw")] = 0,
    run: Annotated[str | None, typer.Option(help="only units of this alignment run")] = None,
    sample_id: Annotated[str | None, typer.Option("--id", help="name of the sample")] = None,
    replace: Annotated[bool, typer.Option("--replace", help="redraw over an existing sample")] = False,
) -> None:
    """Draw a blind audit sample of the units the pipeline accepted."""
    from . import audit

    counts = audit.sample(
        directory,
        n=n,
        strata=[name.strip() for name in strata.split(",") if name.strip()],
        seed=seed,
        run=run,
        sample_id=sample_id,
        replace=replace,
    )
    for name, value in counts.items():
        typer.echo(f"{name:<18} {value:>8}")


@audit_app.command("report")
def audit_report(
    directory: Annotated[Path, typer.Argument(help="dataset directory the sample was drawn from")],
    sample: Annotated[str, typer.Option(help="sample id")],
    out: Annotated[Path | None, typer.Option(help="write the Markdown here instead of stdout")] = None,
) -> None:
    """Score a stored audit sample and print the precision it shows."""
    from . import audit

    markdown = audit.report(directory, sample=sample, out=out)
    if out:
        typer.echo(f"-> {out}")
    else:
        typer.echo(markdown)


@ainu_app.command("derive")
def ainu_derive(
    directory: Annotated[Path, typer.Argument(help="dataset directory of the imported records")] = Path("work/ainu-records"),
    run: Annotated[str, typer.Option(help="run configuration under models/align/runs/<name>.yaml")] = "pilot-v1",
    onnx: Annotated[Path | None, typer.Option(help="detector export to derive the columns with")] = None,
    score: Annotated[float | None, typer.Option(help="detector score cutoff")] = None,
    limit: Annotated[int | None, typer.Option(help="stop after this many pages")] = None,
    align: Annotated[bool, typer.Option("--align/--no-align", help="place characters in the derived boxes")] = True,
    cache: Annotated[Path | None, typer.Option(help="read and write the page detections here")] = None,
) -> None:
    """Give the transcribed lines boxes, then align them: the plan's step 2.

    The boxes are a proposal: each one is the union of the ink columns the detector found, it is
    written only where every column holds enough ink for the line it is paired with, and the line
    records that the atlas derived it. A run that refuses a page withdraws the box an earlier run
    wrote there, so a stricter run leaves nothing of a looser one behind.
    """
    from . import ainu
    from . import align as align_module

    if limit is not None and limit < 0:
        raise typer.BadParameter(f"--limit must not be negative, got {limit}")
    onnx_path = onnx or ainu.DEFAULT_ONNX
    detector_score = score if score is not None else ainu.SCORE
    pages = None
    if limit is not None:
        # `--limit 0` selects no page and derives nothing, rather than everything.
        pages = [page.id for page in tables.Dataset(directory).read("pages")][:limit]
        if not pages:
            typer.echo("no page selected")
            return
    counts = ainu.derive_dataset(directory, pages=pages, cache=cache, onnx_path=onnx_path,
                                 score=detector_score)
    for name, value in counts.items():
        typer.echo(f"{name:<14} {value:>10}")
    if not align:
        return
    run_path = Path("models/align/runs") / f"{run}.yaml"
    if not run_path.exists():
        raise typer.BadParameter(f"{run_path} does not exist")
    config = align_module.load_run(run_path, run)
    for name, value in sorted(align_module.run_directory(directory, config, pages=pages).items()):
        typer.echo(f"{name:<14} {value:>10}")


@app.command()
def export(
    datasets: Annotated[list[Path], typer.Argument(help="dataset directories to merge into the release")],
    out: Annotated[Path, typer.Option(help="release directory to write")],
    licence: Annotated[str, typer.Option(help="licence the release claims")] = "CC-BY-SA-4.0",
    review: Annotated[str, typer.Option(help="comma-separated review states to admit")] = "reviewed,double-reviewed,adjudicated",
    include_machine: Annotated[bool, typer.Option("--include-machine", help="also admit machine units")] = False,
    crops: Annotated[bool, typer.Option("--crops", help="materialise crops where the image rights allow")] = False,
    normalisation: Annotated[str, typer.Option(help="named normalisation policy")] = "export-v1",
    version: Annotated[str | None, typer.Option(help="release version, e.g. 0.1")] = None,
    limit: Annotated[int | None, typer.Option(help="cap the units, for a scratch build")] = None,
) -> None:
    """Write a release directory: the tables, the crops, and the release documents."""
    from . import export as export_module

    counts = export_module.release(
        list(datasets),
        out,
        licence=licence,
        review=[state.strip() for state in review.split(",") if state.strip()],
        include_machine=include_machine,
        crops=crops,
        normalisation=normalisation,
        version=version,
        limit=limit,
        command="atlas export " + " ".join(str(path) for path in datasets) + f" --out {out}",
    )
    for name, value in sorted(counts.items()):
        typer.echo(f"{name:<14} {value:>10}")
    typer.echo(f"-> {out}")


@app.command()
def align(
    directory: Annotated[Path, typer.Argument(help="dataset directory holding the lines to align")],
    run: Annotated[str, typer.Option(help="run configuration under models/align/runs/<name>.yaml")] = "pilot-v1",
    document: Annotated[str | None, typer.Option(help="only this document id")] = None,
    page_ids: Annotated[str | None, typer.Option("--pages", help="comma-separated page ids")] = None,
    limit: Annotated[int | None, typer.Option(help="stop after this many lines")] = None,
    group: Annotated[str | None, typer.Option(help="a pilot group: calibration or heldout")] = None,
    per_item: Annotated[int | None, typer.Option(help="with a group, the first n pages of each item")] = None,
) -> None:
    """Align the transcription lines of a dataset to the character boxes a detector finds."""
    from . import align as align_module

    run_path = Path("models/align/runs") / f"{run}.yaml"
    if not run_path.exists():
        raise typer.BadParameter(f"{run_path} does not exist")
    config = align_module.load_run(run_path, run)
    chosen = page_ids.split(",") if page_ids else None
    if group:
        from . import pilot

        chosen = [row["page_id"] for row in pilot.pages_for(group, per_item=per_item)]
    counts = align_module.run_directory(
        directory,
        config,
        document=document,
        pages=chosen,
        limit=limit,
    )
    for name, value in counts.items():
        typer.echo(f"{name:<14} {value:>10}")


for name, module in (
    ("tables", tables_app),
    ("images", images_app),
    ("rights", rights_app),
    ("import", import_app),
    ("pilot", pilot_app),
    ("eval", eval_app),
    ("review", review_app),
    ("audit", audit_app),
    ("ainu", ainu_app),
):
    app.add_typer(module, name=name)


if __name__ == "__main__":
    app()
