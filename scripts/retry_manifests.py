"""Retry the entry manifests a previous みんなで翻刻 import could not read.

`atlas import honkoku-data` fetches each entry's IIIF manifest into `cache/manifests/<sha256 of
url>.json` and never caches a failure, so a manifest that failed is retried by the next run. Two
things stop that from being a plain re-run:

* the importer fetches every manifest it needs, including the ~1,000 that answer HTTP 500 every time,
  so a full pass spends hours being refused by a server that is already known to say no;
* it reconstitutes `work/honkoku-data` while the review service is serving that dataset.

This does neither. It reads the manifest URLs the clone names, keeps the ones that are not in the
cache, fetches those through the importer's own `fetch_manifest` — so the pause per host, the retries,
the backoff and the cache format are the ones the importer already uses — and reports what each host
answered. Nothing is written outside the manifest cache. Run `atlas import honkoku-data` afterwards to
let the newly readable documents take their image and rights evidence from their manifests.

A manifest that is already cached is never re-fetched, so an interrupted run continues where it
stopped and a finished one does nothing:

    .venv/bin/python scripts/retry_manifests.py --dry-run
    .venv/bin/python scripts/retry_manifests.py
    .venv/bin/python scripts/retry_manifests.py --hosts gallica.bnf.fr
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kuzushiji_atlas import net
from kuzushiji_atlas.importers import honkoku_data

#: Hosts that answer the same refusal every time. Asking them again costs minutes an entry and cannot
#: succeed, and they are skipped unless the caller names them: `khirin-a.rekihaku.ac.jp` answers
#: HTTP 500 for 941 manifests, `iiif.khirin-labs.org` no longer resolves, and `honkoku.org` serves a
#: 404 page for the manifests its own info.tsv files name.
DEAD = frozenset({"khirin-a.rekihaku.ac.jp", "iiif.khirin-labs.org", "honkoku.org"})


def wanted_urls(clone: Path, cache: Path | None) -> dict[str, list[str]]:
    """Every manifest URL the clone names, grouped by host, that the cache does not already hold."""
    urls: set[str] = set()
    infos = sorted(clone.glob("*/info.tsv")) + sorted(clone.glob("v3/*/info.tsv"))
    for info in infos:
        with open(info, encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                url = (row.get("manifestUrl") or "").strip()
                if url:
                    urls.add(url)
    grouped: dict[str, list[str]] = defaultdict(list)
    for url in sorted(urls):
        if honkoku_data.manifest_path(url, cache).is_file():
            continue
        grouped[url.split("/")[2].lower()].append(url)
    return dict(grouped)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", type=Path, default=honkoku_data.default_clone(),
                        help="the honkoku-data clone whose info.tsv files name the manifests")
    parser.add_argument("--cache", type=Path, default=None,
                        help="the cache holding manifests/, defaulting to cache/ of the repository")
    parser.add_argument("--hosts", default=None,
                        help="comma-separated hosts to retry; the default is every host not known dead")
    parser.add_argument("--dry-run", action="store_true", help="report what would be fetched and stop")
    parser.add_argument("--workers", type=int, default=honkoku_data.WORKERS,
                        help="hosts fetched at once, one request per host at a time")
    args = parser.parse_args()

    clone = args.clone.expanduser()
    if not clone.is_dir():
        print(f"no clone at {clone}", file=sys.stderr)
        return 2
    grouped = wanted_urls(clone, args.cache)
    if args.hosts:
        keep = {host.strip().lower() for host in args.hosts.split(",") if host.strip()}
        grouped = {host: urls for host, urls in grouped.items() if host in keep}
    else:
        skipped = {host: len(urls) for host, urls in grouped.items() if host in DEAD}
        grouped = {host: urls for host, urls in grouped.items() if host not in DEAD}
        for host, count in sorted(skipped.items()):
            print(f"skipping {host}: {count} manifests, known to refuse")
    if not grouped:
        print("every wanted manifest is already cached")
        return 0

    total = sum(len(urls) for urls in grouped.values())
    print(f"{total} manifests to fetch across {len(grouped)} hosts")
    for host, urls in sorted(grouped.items(), key=lambda item: -len(item[1])):
        print(f"  {host:<34}{len(urls):>6}  {net.host_pause(host):>4.1f} s apart, "
              f"about {len(urls) * net.host_pause(host) / 60:.0f} min")
    if args.dry_run:
        return 0

    found, abandoned = honkoku_data.manifests(
        [url for urls in grouped.values() for url in urls], cache=args.cache, workers=args.workers
    )
    failed = Counter()
    got = 0
    for url, (manifest, error) in found.items():
        if manifest is None:
            host = url.split("/")[2].lower()
            failed[(host, (error or "unknown").split(":")[0][:60])] += 1
        else:
            got += 1
    print()
    print(f"read {got} of {total} manifests")
    for (host, reason), count in failed.most_common():
        print(f"  still unreadable  {host:<32}{count:>6}  {reason}")
    for host in abandoned:
        print(f"  abandoned after repeated failure: {host}")
    if got:
        print()
        print("next: .venv/bin/atlas import honkoku-data   (to take the new evidence into the tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
