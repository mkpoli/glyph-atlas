"""``python -m kuzushiji_atlas.corpus`` — build and query the occurrence index.

Kept separate from the main ``atlas`` CLI so that this work lands without touching
``cli.py``; a one-line hook there (``app.add_typer``) can adopt it later.

    python -m kuzushiji_atlas.corpus build   --root shared-work --out work/corpus-index
    python -m kuzushiji_atlas.corpus find    𪜈 --limit 20
    python -m kuzushiji_atlas.corpus find    U+2A708 --located --format jsonl
    python -m kuzushiji_atlas.corpus chars   --limit 40
    python -m kuzushiji_atlas.corpus stats
    python -m kuzushiji_atlas.corpus serve-request "/api/corpus/find?char=𪜈&limit=3"
    python -m kuzushiji_atlas.corpus wikisource --search 𪜈
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import index as index_module
from . import sources as corpus_sources
from .api import CorpusAPI, Router
from .occurrence import TOMO


def _build(args: argparse.Namespace) -> int:
    stats = index_module.build_chars(
        args.root, args.out, corpora=args.corpus or None, progress=lambda m: print(m, file=sys.stderr)
    )
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=1))
    return 0


def _chars(args: argparse.Namespace) -> int:
    api = CorpusAPI(args.root, args.out)
    status, _, body = api.handle_get(
        f"/api/corpus/chars?limit={args.limit}" + (f"&q={args.q}" if args.q else "")
    )
    return _emit(status, body, args)


def _find(args: argparse.Namespace) -> int:
    api = CorpusAPI(args.root, args.out)
    query = [f"char={args.char}", f"limit={args.limit}"]
    if args.located:
        query.append("located=1")
    for corpus in args.corpus or []:
        query.append(f"corpus={corpus}")
    for tier in args.tier or []:
        query.append(f"tier={tier}")
    status, _, body = api.handle_get("/api/corpus/find?" + "&".join(query))
    return _emit(status, body, args)


def _build_char(args: argparse.Namespace) -> int:
    """Index one character, bounded. The only way a per-character file is created."""
    status = index_module.build_occurrences(
        args.char, args.root, args.out, corpora=args.corpus or None, max_records=args.max_records
    )
    status["path"] = Path(status["path"]).name
    print(json.dumps(status, ensure_ascii=False, indent=1))
    return 0


def _stats(args: argparse.Namespace) -> int:
    print(json.dumps(index_module.CorpusIndex(args.out).stats().as_dict(), ensure_ascii=False, indent=1))
    return 0


def _serve_request(args: argparse.Namespace) -> int:
    """Answer one request offline: the same code path the server mounts."""
    router = Router(CorpusAPI(args.root, args.out))
    status, content_type, body = router.handle("GET", args.target)
    sys.stderr.write(f"{status} {content_type}\n")
    sys.stdout.write(body.decode("utf-8"))
    sys.stdout.write("\n")
    return 0 if status < 400 else 1


def _wikisource(args: argparse.Namespace) -> int:
    """Discover Wikisource pages, or import them as a searchable corpus."""
    from . import wikisource

    if args.import_to:
        counts = wikisource.import_corpus(
            args.import_to, char=args.search or TOMO, limit=args.limit, cache=args.cache, host=args.host
        )
        print(json.dumps(counts, ensure_ascii=False, indent=1))
        return 0
    if args.page:
        found = wikisource.fetch_pages([args.page], cache=args.cache)
    else:
        found = wikisource.search(args.search or TOMO, limit=args.limit, cache=args.cache)
    print(json.dumps(found, ensure_ascii=False, indent=1))
    return 0


def _emit(status: int, body: bytes, args: argparse.Namespace) -> int:
    payload = json.loads(body.decode("utf-8"))
    if args.format == "jsonl":
        rows = payload.get("items") or payload.get("characters") or []
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, default=str))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    return 0 if status < 400 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kuzushiji_atlas.corpus")
    parser.add_argument("--root", default=corpus_sources.DEFAULT_ROOT)
    parser.add_argument("--out", default=index_module.INDEX_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="one bounded pass over the corpora")
    p.add_argument("--corpus", action="append")
    p.set_defaults(func=_build)

    p = sub.add_parser("chars", help="the bounded character summary")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--q")
    p.set_defaults(func=_chars)

    p = sub.add_parser("find", help="occurrences of one character")
    p.add_argument("char")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--corpus", action="append")
    p.add_argument("--tier", action="append")
    p.add_argument("--located", action="store_true")
    p.add_argument("--format", choices=("json", "jsonl"), default="json")
    p.set_defaults(func=_find)

    p = sub.add_parser("build-char", help="index one character, bounded (never run from a request)")
    p.add_argument("char")
    p.add_argument("--max-records", dest="max_records", type=int, default=index_module.DEFAULT_MAX_RECORDS)
    p.add_argument("--corpus", action="append")
    p.set_defaults(func=_build_char)

    p = sub.add_parser("stats", help="index size and cost")
    p.set_defaults(func=_stats)

    p = sub.add_parser("serve-request", help="answer one HTTP target offline")
    p.add_argument("target")
    p.set_defaults(func=_serve_request)

    p = sub.add_parser("wikisource", help="discover Wikisource pages, or import them as a corpus")
    p.add_argument("--search", default=None, help="character to look for")
    p.add_argument("--page")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--cache", default=None)
    p.add_argument("--host", default="ja.wikisource.org")
    p.add_argument(
        "--import-to",
        dest="import_to",
        default=None,
        help="write a corpus directory here (documents/pages/page_texts)",
    )
    p.set_defaults(func=_wikisource)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
