"""Send the first build's page bundles to R2 under v3/pages/. Sent names are kept in out/uploaded.txt,
so a rerun sends only the rest. Later bundles are sent by publish.py itself."""
import sys
from concurrent.futures import ThreadPoolExecutor

from publish import BUCKET, BUNDLE_PREFIX, OUT, wrangler

WORKERS = 4


def put(path):
    for _ in range(4):
        if wrangler("r2", "object", "put", f"{BUCKET}/{BUNDLE_PREFIX}{path.name}", "--file", str(path),
                    "--content-type", "application/json", "--content-encoding", "gzip", "--remote",
                    capture=True).returncode == 0:
            return path.name
    return None


def main():
    sent_file = OUT / "uploaded.txt"
    sent = set(sent_file.read_text().split()) if sent_file.exists() else set()
    pending = sorted(p for p in (OUT / "pages").iterdir() if p.name not in sent)
    print(f"{len(pending)} of {len(sent) + len(pending)} bundles to send", flush=True)
    failed = 0
    with ThreadPoolExecutor(WORKERS) as pool, sent_file.open("a") as log:
        for i, name in enumerate(pool.map(put, pending), 1):
            if name:
                log.write(name + "\n")
                log.flush()
            else:
                failed += 1
            if i % 500 == 0:
                print(f"{i} sent", flush=True)
    if failed:
        sys.exit(f"{failed} bundles failed; rerun to send them")
    print("all bundles sent")


if __name__ == "__main__":
    main()
