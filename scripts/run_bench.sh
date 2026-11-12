#!/usr/bin/env bash
# Rebuild, run lob_bench and lob_replay, then regenerate docs/index.html,
# site/card.json and the README results table from their output.
# Extra arguments go to lob_bench.
set -euo pipefail
cd "$(dirname "$0")/.."

cmake --preset default
cmake --build --preset default --target lob_bench lob_replay
mkdir -p results
./build/lob_bench --benchmark_out=results/bench.json --benchmark_out_format=json "$@"
python3 -I -c '
import json, sys
path = sys.argv[1]
with open(path) as f:
    run = json.load(f)
for key in ("host_name", "executable"):
    run["context"].pop(key, None)
with open(path, "w") as f:
    json.dump(run, f, indent=2)
    f.write("\n")
' results/bench.json
./build/lob_replay examples/demo_flow.txt > results/replay.jsonl
python3 -I scripts/build_report.py --card site/card.json --readme README.md
