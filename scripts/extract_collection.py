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
parser.add_argument("--ndl-cpu",action="store_true",
                    help="run NDL's sequence model on the CPU (faster than CUDA for its one-crop reads)")
parser.add_argument("--ndl-threads",type=int,default=2,help="CPU threads for NDL's model with --ndl-cpu")
parser.add_argument("--switch-ndl",action="store_true",
                    help="re-pin the queue to this worker's NDL provider; only once every worker has stopped")
parser.add_argument("--supplement-every",type=int,default=2,
                    help="take a supplement of a page completed under an earlier policy every N pages; 0 never")
args = parser.parse_args()
if min(args.pages,args.seconds,args.max_lines,args.ndl_threads) < 1 or args.pause < 0 or args.supplement_every < 0:
    parser.error("positive page/time/line/thread limits and nonnegative pause and supplement interval required")
queue = Queue(args.root)
# Any number of workers may run at once: each claims its own pages. Seeding and re-scoring the queue
# are done by whichever worker holds the maintenance lock, and skipped by the others.
if args.seed:
    with tables.locked(args.root/"maintenance"):
        print(json.dumps({"seeded":queue.seed(args.source)},ensure_ascii=False),flush=True)
store = None
if args.publish_to:
    from glyph_atlas.review.store import Store
    store = Store(args.publish_to)
if args.publish_only:
    if store is None:
        parser.error("--publish-only requires --publish-to")
    with tables.locked(args.root/"publish"):
        print(json.dumps({"published_pages":publish_completed(queue,store)}))
    print(json.dumps(queue.status(),ensure_ascii=False,indent=2))
elif args.status:
    print(json.dumps(queue.status(),ensure_ascii=False,indent=2))
else:
    queue.status(state="initializing")
    try:
        with tables.locked(args.root/"maintenance",timeout=0):
            if not args.no_prioritize:
                counts = load_char_counts(args.counts) if args.counts.exists() else {}
                queue.prioritize(counts)
            # `<root>/focus.txt` names documents to extract first, one id per line (`#` starts a
            # comment); read at every batch, so editing it steers the running service.
            focus = args.root/"focus.txt"
            wanted = [line.split("#")[0].strip() for line in focus.read_text().splitlines()] if focus.exists() else []
            print(json.dumps({"focused_pending":queue.focus(w for w in wanted if w)}),flush=True)
            if args.supplement_every:
                print(json.dumps({"supplements_seeded":queue.seed_supplements()}),flush=True)
    except TimeoutError:
        print(json.dumps({"maintenance":"another worker holds it"}),flush=True)
    try:
        engine = Engine(ndl_cpu=args.ndl_cpu,ndl_threads=args.ndl_threads)
        queue.pin("ndl_provider",engine.ndl_provider,replace=args.switch_ndl)
        result = run(queue,engine,pages=args.pages,seconds=args.seconds,pause=args.pause,
                     max_lines=args.max_lines,store=store,supplement_every=args.supplement_every)
    except Exception as exc:  # noqa: BLE001 — publish a redacted worker failure for the supervisor
        error = type(exc).__name__+": "+str(exc).replace(str(Path.home()),"~")[:500]
        print(json.dumps(queue.status(state="failed",error=error),ensure_ascii=False,indent=2))
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=False,indent=2))
