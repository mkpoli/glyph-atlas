"""Write the collection status row to the live D1 database.

The Worker serves `metadata['collection']` as `/atlas/collection/status`. A catalogue publication
writes it too; this script refreshes it between publications, so the progress panel shows a recent
snapshot. `publish_honkoku.py` calls `push()` on every run.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def push(timeout: int = 120) -> bool:
    """Write the row. A failure is logged to stderr and returned, never raised."""
    try:
        from prepare_publication import status_row

        result = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--command", status_row()],
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
