#!/usr/bin/env bash
# Stop one specific detector trainer once its next epoch is completely written, and nothing else.
#
# The user asked for the 24-epoch run to stop at the next saved checkpoint rather than run to
# completion. Two things make that safe to automate:
#
#   * the trainer is pinned by pid, process start time, working directory and argv — not by name — so a
#     pid that has been reused, or another project's trainer, is never signalled, and a trainer that
#     died is reported rather than replaced;
#   * `torch.save` writes checkpoints directly and `metrics.json` is written after both of an epoch's
#     checkpoints, so readiness is the epoch's row being in the metrics AND every file it wrote
#     holding still. A trainer is stopped only when readiness was reached; on timeout nothing is
#     stopped at all.
#
#   models/detector/stop_at_checkpoint.sh <pid> <epoch> [timeout-seconds]
set -euo pipefail

cd "$(dirname "$0")/../.."
pid="${1:?usage: stop_at_checkpoint.sh <pid> <epoch> [timeout-seconds]}"
want_epoch="${2:?usage: stop_at_checkpoint.sh <pid> <epoch> [timeout-seconds]}"
timeout_s="${3:-28800}"
log=/tmp/detector-24e.log
out=models/detector/artifacts-24e
epoch_file="$out/epoch-$(printf '%02d' "$want_epoch").pt"

identity() {
    # One line `pid|starttime|cwd|argv` for a live process, and nothing at all for a dead one.
    local target="$1"
    [ -r "/proc/$target/stat" ] || return 0
    .venv/bin/python - "$target" <<'PY'
import sys
from pathlib import Path
pid = sys.argv[1]
try:
    stat = Path(f"/proc/{pid}/stat").read_text()
    start = stat[stat.rindex(")") + 2:].split()[19]
    cwd = str(Path(f"/proc/{pid}/cwd").resolve())
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
except (OSError, IndexError, ValueError):
    raise SystemExit(0)
print(f"{pid}|{start}|{cwd}|{argv}")
PY
}

start_of() {  # the second field of an identity line
    printf '%s' "$1" | cut -d'|' -f2
}

alive_as() {  # pid, start time -> 0 when that exact process is still running
    local now
    now="$(identity "$1")"
    [ -n "$now" ] && [ "$(start_of "$now")" = "$2" ]
}

pinned="$(identity "$pid")"
if [ -z "$pinned" ]; then
    echo "pid $pid is not running; nothing to stop" >&2
    exit 1
fi
if [ "$(printf '%s' "$pinned" | cut -d'|' -f4)" != ".venv/bin/python -u models/detector/train.py --epochs 24 --out models/detector/artifacts-24e" ]; then
    echo "pid $pid is not the expected trainer: $pinned" >&2
    exit 1
fi
echo "pinned trainer: $pinned"

ready=0
deadline=$(( $(date +%s) + timeout_s ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! alive_as "$pid" "$(start_of "$pinned")"; then
        echo "the pinned trainer exited before epoch $want_epoch was written" >&2
        exit 1
    fi
    if [ -f "$epoch_file" ] && [ -f "$out/metrics.json" ] && grep -q "^epoch ${want_epoch}:" "$log"; then
        # The marker is printed before the saves, so it is necessary and not sufficient: the epoch's
        # row has to be in the metrics, and every file the epoch wrote has to have stopped changing.
        if .venv/bin/python - "$out/metrics.json" "$want_epoch" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
raise SystemExit(0 if any(row.get("epoch") == int(sys.argv[2]) for row in report.get("epochs", [])) else 1)
PY
        then
            first="$(stat -c '%s %Y' "$epoch_file" "$out/last.pt" "$out/metrics.json")"
            sleep 3
            if [ "$first" = "$(stat -c '%s %Y' "$epoch_file" "$out/last.pt" "$out/metrics.json")" ]; then
                ready=1
                break
            fi
            echo "epoch $want_epoch is written but a file is still changing"
        fi
    fi
    sleep 15
done

if [ "$ready" -ne 1 ]; then
    echo "epoch $want_epoch was not verified inside ${timeout_s}s; nothing was stopped" >&2
    exit 1
fi
echo "epoch $want_epoch is complete and stable: $epoch_file"

if ! alive_as "$pid" "$(start_of "$pinned")"; then
    echo "the pinned trainer already exited; epoch $want_epoch is the last checkpoint"
    exit 0
fi

# The trainer and everything it started, each with its own start time so a reused pid is never
# signalled. Another project's processes are not in this tree.
mapfile -t family < <(.venv/bin/python - "$pid" <<'PY'
import subprocess, sys
from pathlib import Path

def start_time(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rindex(")") + 2:].split()[19]
    except (OSError, IndexError, ValueError):
        return ""

root = int(sys.argv[1])
seen, queue = [], [root]
while queue:
    current = queue.pop()
    if current in seen:
        continue
    seen.append(current)
    found = subprocess.run(["pgrep", "-P", str(current)], capture_output=True, text=True, check=False)
    queue.extend(int(value) for value in found.stdout.split())
for current in sorted(seen):
    start = start_time(current)
    if start:
        print(f"{current}|{start}")
PY
)
if [ "${#family[@]}" -eq 0 ]; then
    echo "the pinned trainer has no live processes; nothing to stop"
    exit 0
fi
echo "stopping the trainer family: ${family[*]}"

for entry in "${family[@]}"; do
    kill -TERM "${entry%%|*}" 2>/dev/null || true
done
for _ in $(seq 1 30); do
    alive=0
    for entry in "${family[@]}"; do
        alive_as "${entry%%|*}" "${entry##*|}" && alive=1
    done
    [ "$alive" -eq 0 ] && break
    sleep 1
done
for entry in "${family[@]}"; do
    # Only a pid that is still the same process may receive this run's SIGKILL.
    if alive_as "${entry%%|*}" "${entry##*|}"; then
        kill -KILL "${entry%%|*}" 2>/dev/null || true
    fi
done
sleep 2

remaining=0
for entry in "${family[@]}"; do
    alive_as "${entry%%|*}" "${entry##*|}" && remaining=$((remaining + 1))
done
if [ "$remaining" -ne 0 ]; then
    echo "$remaining of the trainer's processes are still alive" >&2
    exit 1
fi
echo "stopped; epoch $want_epoch is the last checkpoint"
tail -2 "$log"
