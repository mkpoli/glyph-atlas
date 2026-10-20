"""Write the collection status to glyph-atlas-v3's `site` table.

The Worker serves `site['collection']` as `/atlas/collection/status`. Each publication's answers
(`atlas3/answers.py`) write it too; this script refreshes it between publications, so the progress panel
shows a recent snapshot. `publish_honkoku.py` calls `push()` on every run.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def site_row(key: str, value) -> str:
    """The statement that sets one `site` value, as JSON."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return f"INSERT INTO site(key, value) VALUES({quote(key)}, {quote(text)}) ON CONFLICT(key) DO UPDATE SET value=excluded.value"


def status():
    """The collection status the progress panel shows, from the collection's working folders."""
    from export_cloudflare import public_status

    from glyph_atlas.review import collection
    return public_status(collection.status((ROOT / "work").resolve()))


def push(timeout: int = 120) -> bool:
    """Write the row. A failure is logged to stderr and returned, never raised."""
    try:
        result = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas-v3", "--remote", "--command",
                                 site_row("collection", status())],
                                cwd=ROOT / "apps" / "cloudflare", capture_output=True, text=True, check=False, timeout=timeout)
    except Exception as error:  # noqa: BLE001 - a status write must not stop a publication
        print(f"collection status not written: {type(error).__name__}", file=sys.stderr)
        return False
    if result.returncode:
        print(f"collection status not written: wrangler exited {result.returncode}", file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(0 if push() else 1)
