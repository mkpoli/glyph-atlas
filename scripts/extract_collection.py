"""Slow, resumable character extraction; commits one immutable page at a time."""
import argparse
import json
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.extraction_queue import Engine, Queue, load_char_counts, publish_completed, run

parser = argparse.ArgumentParser()
parser.add_argument("--root",type=Path,default=Path("work/character-extraction"))
parser.add_argument("--source",type=Path,default=Path("work/honkoku-lines"))
parser.add_argument("--counts",type=Path,default=Path("work/corpus-index/chars.parquet"))
parser.add_argument("--no-prioritize",action="store_true",
                     help="skip coverage-first re-scoring of the queue before this run")
parser.add_argument("--publish-to",type=Path)
parser.add_argument("--publish-only",action="store_true")
parser.add_argument("--seed",action="store_true")
parser.add_argument("--status",action="store_true")
parser.add_argument("--pages",type=int,default=3)
parser.add_argument("--seconds",type=int,default=600)
parser.add_argument("--pause",type=float,default=10)
parser.add_argument("--max-lines",type=int,default=64)
parser.add_argument("--supplement-every",type=int,default=2,
                    help="take a supplement of a page completed under an earlier policy every N pages; 0 never")
args = parser.parse_args()
if min(args.pages,args.seconds,args.max_lines) < 1 or args.pause < 0 or args.supplement_every < 0:
    parser.error("positive page/time/line limits and nonnegative pause and supplement interval required")
queue = Queue(args.root)
with tables.locked(args.root/"worker",timeout=0):
    if args.seed:
        print(json.dumps({"seeded":queue.seed(args.source)},ensure_ascii=False),flush=True)
    store = None
    if args.publish_to:
        from glyph_atlas.review.store import Store
        store = Store(args.publish_to)
    if args.publish_only:
        if store is None:
            parser.error("--publish-only requires --publish-to")
        print(json.dumps({"published_pages":publish_completed(queue,store)}))
        print(json.dumps(queue.status(),ensure_ascii=False,indent=2))
    elif args.status:
        print(json.dumps(queue.status(),ensure_ascii=False,indent=2))
    else:
        queue.status(state="initializing")
        if not args.no_prioritize:
            counts = load_char_counts(args.counts) if args.counts.exists() else {}
            queue.prioritize(counts)
        try:
            engine = Engine()
            if args.supplement_every:
                print(json.dumps({"supplements_seeded":queue.seed_supplements(engine.classifier.classes)}),
                      flush=True)
            result = run(queue,engine,pages=args.pages,seconds=args.seconds,pause=args.pause,
                         max_lines=args.max_lines,store=store,supplement_every=args.supplement_every)
        except Exception as exc:  # noqa: BLE001 — publish a redacted worker failure for the supervisor
            error = type(exc).__name__+": "+str(exc).replace(str(Path.home()),"~")[:500]
            print(json.dumps(queue.status(state="failed",error=error),ensure_ascii=False,indent=2))
            raise SystemExit(1) from None
        print(json.dumps(result,ensure_ascii=False,indent=2))
