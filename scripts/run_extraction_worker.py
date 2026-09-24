"""Launch the extraction worker with redacted process output for its user service."""
import re
import subprocess
import sys
from pathlib import Path


def redact(value):
    value = re.sub(r"/home/[^/\s]+", "~", value)
    private_name = Path.home().name
    if private_name:
        value = re.sub(r"(?<!\w)"+re.escape(private_name)+r"(?!\w)", "<user>", value)
    return value


def main():
    root = Path(__file__).resolve().parents[1]
    command = [str(root/".venv/bin/python"),str(root/"scripts/extract_collection.py"),*sys.argv[1:]]
    with subprocess.Popen(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          text=True,bufsize=1) as process:
        for line in process.stdout:
            print(redact(line),end="",flush=True)
        return process.wait()


if __name__ == "__main__":
    try:
        code = main()
    except OSError as exc:
        print(redact(str(exc)), file=sys.stderr)
        code = 1
    raise SystemExit(code)
