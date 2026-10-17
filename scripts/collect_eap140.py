"""Collect the page images of EAP140, the Tangut Collection, from its IIIF manifests.

    uv run python scripts/collect_eap140.py [--out work/eap140]

The volumes are those `data/sources/eap140.yaml` lists. No transcription exists, so the collection
holds documents and pages only. A rerun reads the manifest cache and fetches only what failed.
"""
import argparse
import json
from pathlib import Path

from glyph_atlas.importers.iiif_collection import collect

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--out", type=Path, default=Path("work/eap140"))
args = parser.parse_args()
summary = collect(Path("data/sources/eap140.yaml"), args.out)
print(json.dumps({key: summary[key] for key in ("volumes", "pages")} | {"unavailable": len(summary["unavailable"])}))
