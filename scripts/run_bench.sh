#!/usr/bin/env bash
# Rebuild, run lob_bench and lob_replay, then regenerate docs/index.html,
# site/card.json and the README results table from their output.
# Runs 10 repetitions by default; extra arguments go to lob_bench and
# override the defaults. The 1/5/15-minute load averages before and after
# the run are stored in the JSON context so the report can state them.
set -euo pipefail
cd "$(dirname "$0")/.."

cmake --preset default
cmake --build --preset default --target lob_bench lob_replay
loadavg() {
  sysctl -n vm.loadavg 2>/dev/null | tr -d '{}' || cut -d' ' -f1-3 /proc/loadavg
}
mkdir -p results
load_before="$(loadavg)"
./build/lob_bench --benchmark_repetitions=10 --benchmark_report_aggregates_only=false \
  --benchmark_out=results/bench.json --benchmark_out_format=json "$@"
load_after="$(loadavg)"
python3 -I -c '
import json, sys
path, before, after = sys.argv[1:4]
with open(path) as f:
    run = json.load(f)
for key in ("host_name", "executable"):
    run["context"].pop(key, None)
run["context"]["loadavg_before"] = before.split()
run["context"]["loadavg_after"] = after.split()
with open(path, "w") as f:
    json.dump(run, f, indent=2)
    f.write("\n")
' results/bench.json "$load_before" "$load_after"
./build/lob_replay examples/demo_flow.txt > results/replay.jsonl
python3 -I scripts/build_report.py --card site/card.json --readme README.md
