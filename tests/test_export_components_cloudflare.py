"""Tests of `scripts/export_components_cloudflare.py`."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "export_components_cloudflare", ROOT / "scripts" / "export_components_cloudflare.py"
)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_a_statement_stays_under_the_byte_limit_in_utf8():
    """Supplementary-plane components take four bytes each; the limit is in bytes."""
    rows = [("𭁟", 2, 9, f"U+{0x20000 + i:05X}", 1, 1) for i in range(20_000)]
    sizes = [
        len(statement.encode()) for statement in export.statements("han_components", "a,b,c,d,e,f", rows)
    ]
    assert len(sizes) > 1 and max(sizes) <= export.STATEMENT_BYTES
