"""Fetch the pinned NDLkotenOCR-Lite recognizer into the ignored local model cache."""
from __future__ import annotations

import hashlib
import shutil
import urllib.request
from pathlib import Path

REVISION = "ede4283845cdc0ba2bda8b7ebfc3dc80b33c92c8"
BASE = f"https://raw.githubusercontent.com/ndl-lab/ndlkotenocr-lite/{REVISION}/"
FILES = [
    ("src/model/parseq-ndl-32x384-tiny-10.onnx", "parseq.onnx",
     "fd75b2c435d053fab9f9e563393ee5b6654a41434b2358b50fb63498a2c85b79"),
    ("src/config/NDLmoji.yaml", "characters.yaml",
     "775eb37e6b09ad0a97b762d48c916c60e7ce8879a4628ddb190ce037d0a15772"),
    ("LICENCE", "LICENCE", "12538e73c4a1e05fc0c0b75d9d4de657f139d94ebc5f44b03a84cc1f793358f0"),
]


def fetch(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for remote, name, digest in FILES:
        destination = directory / name
        if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
            print(f"Verified {name}")
            continue
        temporary = destination.with_suffix(destination.suffix + ".download")
        try:
            with urllib.request.urlopen(BASE + remote, timeout=60) as response, temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Checksum mismatch: {name}")
            temporary.replace(destination)
            print(f"Downloaded {name} ({destination.stat().st_size:,} bytes)")
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    fetch(Path(__file__).resolve().parents[1] / "cache/models/ndlkotenocr-lite")
