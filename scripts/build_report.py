"""Build docs/index.html (and optionally site/card.json and the README results
block) from committed results: lob_bench JSON and lob_replay JSONL.

Usage:
  python3 scripts/build_report.py [--bench results/bench.json]
      [--replay results/replay.jsonl] [--out docs/index.html]
      [--card site/card.json] [--readme README.md]

Python standard library only. Every number on the page is computed here from
the inputs or from constants in the committed sources (benchmark mix, window
size, differential-test size); none is typed by hand. Page components and
charts come from the vendored report kit in report-kit/.
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "report-kit"))
import report_kit as rk  # noqa: E402
from report_kit import Raw, esc  # noqa: E402

REPO = "allenwang0/limitorderbook"
REPO_URL = f"https://github.com/{REPO}"
PAGE_URL = "https://allenwang0.github.io/limitorderbook/"
HOME_URL = "https://allenwang0.github.io/"
BOOKS = ["MapBook", "FlatBook"]
OPS = [
    ("add_resting", "add (resting)"),
    ("add_crossing", "add (crossing)"),
    ("cancel", "cancel"),
    ("market", "market"),
]
PERCENTILES = ["p50", "p90", "p99", "p99.9", "max"]
LADDER = ["p50", "p90", "p99", "p99.9"]
DEFAULT_BOOK = "MapBook"
NO_BENCH = "No benchmark data: run scripts/build_report.py after cmake --build"
NO_REPLAY = "No replay data: run scripts/build_report.py after cmake --build"
README_START = "<!-- results:start -->"
README_END = "<!-- results:end -->"
THIN = " "


def label_compiler(version):
    """GCC's __VERSION__ is a bare number such as "14.2.0"; Clang's names itself."""
    return f"GCC {version}" if re.match(r"\d", version) else version


def spread(runs):
    return {"runs": runs, "median": statistics.median(runs), "min": min(runs), "max": max(runs)}


# ---------------------------------------------------------------- inputs

def load_bench(path):
    """Return (bench dict, error string). Uses every repetition (run_type
    "iteration"); Google Benchmark's own aggregate rows are ignored."""
    if not path or not os.path.exists(path):
        return None, NO_BENCH
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as error:
        return None, f"{path} is not valid JSON (line {error.lineno})"
    context = raw.get("context", {})
    latency_runs, throughput_runs, iterations, ticks, overheads = {}, {}, {}, [], []
    for bench in raw.get("benchmarks", []):
        if bench.get("run_type", "iteration") != "iteration":
            continue
        name = bench.get("name", "")
        match = re.match(r"BM_(Latency|Throughput)<(\w+)>", name)
        if name.startswith("BM_ClockOverhead"):
            ticks.append(bench.get("clock_tick_ns"))
            overheads.append(bench.get("timer_overhead_p50_ns"))
        elif match and match.group(1) == "Latency":
            book = latency_runs.setdefault(match.group(2), {})
            for op, _ in OPS:
                if f"{op}_count" not in bench:
                    continue
                entry = book.setdefault(op, {"count": int(bench[f"{op}_count"])})
                for p in PERCENTILES:
                    entry.setdefault(p, []).append(bench[f"{op}_{p}_ns"])
        elif match and match.group(1) == "Throughput":
            throughput_runs.setdefault(match.group(2), []).append(bench["items_per_second"])
            iterations.setdefault(match.group(2), []).append(bench.get("iterations"))
    if not latency_runs:
        return None, f"{path} has no BM_Latency results"
    latency = {
        book: {op: {"count": e["count"], **{p: spread(e[p]) for p in PERCENTILES}} for op, e in ops.items()}
        for book, ops in latency_runs.items()
    }
    throughput = {book: spread(runs) for book, runs in throughput_runs.items()}
    real_ms = sum(b.get("real_time", 0) * b.get("iterations", 1) for b in raw.get("benchmarks", [])
                  if b.get("run_type", "iteration") == "iteration" and b.get("time_unit") == "ms")
    return {
        "context": {
            "cpu": context.get("cpu", "unknown"),
            "num_cpus": context.get("num_cpus"),
            "compiler": label_compiler(context.get("compiler", "unknown")),
            "cxx_flags": context.get("cxx_flags", "unknown"),
            "git_sha": context.get("git_sha", "unknown"),
            "date": context.get("date", "unknown"),
            "message_mix": context.get("message_mix", "unknown"),
            "seed": context.get("seed", "unknown"),
            "build_type": context.get("library_build_type", "unknown"),
            "library_version": context.get("library_version"),
            "load_before": [float(v) for v in context.get("loadavg_before", context.get("load_avg", []))],
            "load_after": [float(v) for v in context.get("loadavg_after", [])],
        },
        "reps": max((len(v) for v in throughput_runs.values()), default=len(ticks) or 1),
        "clock": {
            "tick_ns": statistics.median(ticks) if ticks else None,
            "overhead_p50_ns": statistics.median(overheads) if overheads else None,
        },
        "latency": latency,
        "throughput": throughput,
        "iterations": iterations,
        "timed_seconds": real_ms / 1000,
    }, None


def load_replay(path):
    """Return (list of steps, error string)."""
    if not path or not os.path.exists(path):
        return None, NO_REPLAY
    steps = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                return None, f"{path} line {number} is not valid JSON"
            if not all(key in step for key in ("cmd", "events", "bids", "asks")):
                return None, f"{path} line {number} is missing cmd, events, bids or asks"
            steps.append(step)
    if not steps:
        return None, f"{path} has no replay lines"
    return steps, None


def source_text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def cpp_int(expr):
    """Evaluate a C++ integer expression such as 10'000 - 2'048."""
    expr = expr.replace("'", "")
    if not re.fullmatch(r"[\d\s+\-]+", expr):
        raise ValueError(f"unsupported constant expression {expr!r}")
    total, sign = 0, 1
    for token in re.findall(r"\d+|[+-]", expr):
        if token in "+-":
            sign = 1 if token == "+" else -1
        else:
            total += sign * int(token)
    return total


def constant(rel, name):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*([^;]+);", source_text(rel))
    if not match:
        raise ValueError(f"{name} not found in {rel}")
    value = match.group(1).strip()
    return float(value) if "." in value else cpp_int(value)


def env_default(rel, var):
    match = re.search(rf'envOr\("{var}",\s*([\d\']+)\)', source_text(rel))
    if not match:
        raise ValueError(f"{var} default not found in {rel}")
    return cpp_int(match.group(1))


def line_range(rel, start, end=None):
    """1-based line numbers of the first line containing `start` and of the
    next line after it containing `end` (or start alone)."""
    lines = source_text(rel).splitlines()
    first = next(i for i, line in enumerate(lines, 1) if start in line)
    if end is None:
        return first, first
    last = next(i for i, line in enumerate(lines, 1) if i >= first and end in line)
    return first, last


def facts_from_sources():
    base = constant("include/lob/FlatBook.h", "kDefaultBasePrice")
    window = constant("include/lob/FlatBook.h", "kDefaultWindow")
    return {
        "window_lo": base,
        "window_hi": base + window - 1,
        "window": window,
        "warmup": constant("bench/lob_bench.cpp", "kWarmupMessages"),
        "measured": constant("bench/lob_bench.cpp", "kMeasuredMessages"),
        "crossing_share": constant("bench/lob_bench.cpp", "kCrossingAddShare"),
        "diff_seeds": env_default("tests/differential_test.cpp", "LOB_DIFF_SEEDS"),
        "diff_ops": env_default("tests/differential_test.cpp", "LOB_DIFF_OPS"),
    }


def git_last_commit(rel):
    """Commit that last changed a committed file, so result links point at a SHA."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%H", "--", rel], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


# ---------------------------------------------------------------- derived numbers

def derive(bench):
    """Every number the page and README show, computed from bench.json."""
    rates = bench["throughput"]
    flat, mapped = rates["FlatBook"], rates["MapBook"]
    tick = bench["clock"]["tick_ns"]
    latency = bench["latency"]
    counts = {op: latency["MapBook"][op]["count"] for op, _ in OPS if op in latency["MapBook"]}
    total = sum(counts.values())
    p50s = [latency[b][op]["p50"]["median"] for b in BOOKS for op, _ in OPS if op in latency.get(b, {})]
    at_floor = sum(1 for v in p50s if tick is None or v <= tick * 1.05)
    tail = {(b, op): latency[b][op]["p99.9"] for b in BOOKS for op, _ in OPS if op in latency.get(b, {})}
    tail_swing = max((t["max"] / t["min"] for t in tail.values() if t["min"] > 0), default=1.0)
    adds = counts.get("add_resting", 0) + counts.get("add_crossing", 0)
    return {
        "flat": flat["median"],
        "map": mapped["median"],
        "ratio": flat["median"] / mapped["median"],
        "ratio_floor": flat["min"] / mapped["max"],
        "flat_ns": 1e9 / flat["median"],
        "map_ns": 1e9 / mapped["median"],
        "tick": tick,
        "reps": bench["reps"],
        "mix": {
            "add": adds / total if total else 0,
            "cancel": counts.get("cancel", 0) / total if total else 0,
            "market": counts.get("market", 0) / total if total else 0,
        },
        "crossing_of_adds": counts.get("add_crossing", 0) / adds if adds else 0,
        "samples": total,
        "p50_at_floor": at_floor,
        "p50_total": len(p50s),
        "tail_swing": tail_swing,
        "tail_max": max(t["median"] for t in tail.values()),
        "tail_max_key": max(tail, key=lambda k: tail[k]["median"]),
    }


def m_rate(value):
    """Messages per second as millions, 3 significant figures, thin space before M."""
    return f"{value / 1e6:.1f}{THIN}M"


def ratio_text(value):
    return f"{value:.2f}×"


def ns(value):
    return f"{value:,.0f}{THIN}ns"


def pct(value):
    return f"{value * 100:.1f}%" if value < 0.1 else f"{value * 100:.0f}%"


def pct1(value):
    return f"{value * 100:.1f}%"


def runs_text(n):
    return "single run" if n == 1 else f"median of {n} runs"


# ---------------------------------------------------------------- card and README

def card_rows(bench, book):
    ops = bench["latency"][book]
    rows = []
    for label, op in (("add", "add_resting"), ("cancel", "cancel"), ("match", "add_crossing")):
        stats = ops[op]
        rows.append({"label": label, "low": stats["p50"]["median"], "mid": stats["p90"]["median"],
                     "high": stats["p99"]["median"]})
    return rows


def utc_stamp(date):
    try:
        moment = datetime.datetime.fromisoformat(date)
    except ValueError:
        return "1970-01-01T00:00:00Z"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_card(bench, book, command):
    """Portfolio card. Headline = the throughput ratio, the report's hero;
    per-operation medians sit at the clock tick and are not a headline."""
    context = bench["context"]
    d = derive(bench)
    rows = card_rows(bench, book)
    takeaway = (f"FlatBook {d['flat'] / 1e6:.1f}M vs MapBook {d['map'] / 1e6:.1f}M msgs/s, "
                f"{runs_text(d['reps'])}, one core, {context['cpu']}.")
    scale = "log" if all(row["low"] > 0 for row in rows) else "linear"
    return {
        "schema_version": 1,
        "repo": "limitorderbook",
        "sha": context["git_sha"],
        "generated_at": utc_stamp(context["date"]),
        "command": command,
        "data_note": (
            f"Seeded synthetic order flow, 55/40/5 add/cancel/market, {runs_text(d['reps'])}, "
            f"{context['cpu']}, {context['compiler']}; latency chart: {book} p50/p90/p99"
        ),
        "metric": {
            "label": "FlatBook throughput vs MapBook",
            "value": round(d["ratio"], 2),
            "unit": "×",
            "higher_is_better": True,
        },
        "takeaway": takeaway[:140],
        "chart": {
            "type": "interval",
            "x_label": "Latency",
            "x_unit": "ns",
            "x_scale": scale,
            "rows": rows,
        },
    }


def readme_table(bench):
    context = bench["context"]
    d = derive(bench)
    lines = ["| Book | Messages/s | Time per message |", "| :--- | ---: | ---: |"]
    for book in BOOKS:
        rate = bench["throughput"][book]["median"]
        lines.append(f"| {book} | {rate / 1e6:.1f}M | {1e9 / rate:.0f} ns |")
    lines.append("")
    lines.append(
        f"FlatBook: **{ratio_text(d['ratio'])}** MapBook's throughput. {runs_text(d['reps']).capitalize()}, "
        f"one thread on {context['cpu']}, seeded synthetic flow ({context['message_mix']})."
    )
    return "\n".join(lines)


def update_readme(path, bench):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if README_START not in text or README_END not in text:
        sys.exit(f"{path} has no {README_START} / {README_END} markers")
    head, rest = text.split(README_START, 1)
    _, tail = rest.split(README_END, 1)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{head}{README_START}\n{readme_table(bench)}\n{README_END}{tail}")


# ---------------------------------------------------------------- figures

def throughput_chart(bench, uid, width, whiskers):
    rows = []
    for book in BOOKS:
        t = bench["throughput"][book]
        row = {"label": book, "value": t["median"] / 1e6, "accent": book == "FlatBook"}
        if whiskers and len(t["runs"]) > 1:
            row.update(lo=t["min"] / 1e6, hi=t["max"] / 1e6)
        rows.append(row)
    d = derive(bench)
    title = f"FlatBook processes {ratio_text(d['ratio'])} as many messages per second as MapBook"
    desc = "; ".join(
        f"{r['label']} {r['value']:.1f} million messages per second"
        + (f" (range {r['lo']:.1f} to {r['hi']:.1f})" if "lo" in r else "")
        for r in rows
    ) + f"; {runs_text(d['reps'])}."
    return rk.bar_chart(rows, uid=uid, width=width, title=title, desc=desc,
                        axis_title="Throughput (million messages/s), bars from zero",
                        fmt=lambda v: f"{v:.1f}{THIN}M/s")


def latency_chart(bench, uid, width):
    tick = bench["clock"]["tick_ns"]
    rows = []
    for op, label in OPS:
        values = {b: {p: bench["latency"][b][op][p]["median"] for p in LADDER}
                  for b in BOOKS if op in bench["latency"].get(b, {})}
        rows.append({"key": op, "label": label, "values": values})
    top = max(v for r in rows for s in r["values"].values() for v in s.values())
    hi = next(m * 10 ** e for e in range(1, 9) for m in (1, 2, 5) if m * 10 ** e >= top * 1.15)
    desc = " ".join(
        f"{r['label']}: " + ", ".join(
            f"{b} p50 {s['p50']:.0f}, p99 {s['p99']:.0f}, p99.9 {s['p99.9']:.0f} ns" for b, s in r["values"].items()
        ) + "." for r in rows
    )
    narrow = width < 420
    return rk.ladder_chart(
        rows, uid=uid, width=width,
        title="Per-operation latency percentiles, nanoseconds on a log scale",
        desc=f"Clock tick {tick:.0f} ns. {desc}",
        series=[("MapBook", "MapBook", False), ("FlatBook", "FlatBook", True)],
        points=LADDER, domain=(20, hi),
        axis_title="Latency (ns, log scale)",
        fmt=lambda v: f"{v:,.0f}",
        floor=tick, floor_label=f"{tick:.0f} ns tick" if narrow else f"{tick:.0f} ns clock tick",
        zero_label="shaded: read 0 ns" if narrow else "shaded: read as 0 ns (under one tick)",
    )


def storage_svg(uid, width, facts):
    """Schematic of both books' storage. Side by side when wide, stacked when narrow."""
    stacked = width < 600
    pw = width if stacked else (width - 32) / 2
    ph = 262
    T = rk.svg_text
    f = rk.coord

    def map_panel(ox, oy):
        p = [T(ox, oy + 16, "MapBook", "c-value"),
             T(ox, oy + 34, "std::map<Price, Level> per side", "c-tick")]
        nodes = [(0.22, 78, "9998"), (0.10, 132, "9997"), (0.36, 132, "9999")]
        cx = lambda r: ox + pw * r
        p.append(f'<path class="c-line" d="M{f(cx(.22))} {oy + 78}L{f(cx(.10))} {oy + 132}M{f(cx(.22))} {oy + 78}L{f(cx(.36))} {oy + 132}"/>')
        for r, y, label in nodes:
            p.append(f'<circle class="c-node" cx="{f(cx(r))}" cy="{oy + y}" r="21"/>')
            p.append(T(cx(r), oy + y + 4, label, "c-tick", "middle"))
        # one level's queue: heap nodes linked by pointers, in arrival order
        bw = pw * .12
        prev = (cx(.36) + 21, oy + 132)
        for i, r in enumerate((.50, .67, .84)):
            bx, by = cx(r), oy + 121
            p.append(f'<path class="c-line" d="M{f(prev[0])} {f(prev[1])}H{f(bx)}"/>')
            p.append(f'<rect class="c-node" x="{f(bx)}" y="{f(by)}" width="{f(bw)}" height="22" rx="5"/>')
            p.append(T(bx + bw / 2, by + 15.5, f"#{i + 1}", "c-tick", "middle"))
            prev = (bx + bw, by + 11)
        p.append(T(cx(.50), oy + 104, "std::list per level", "c-tick"))
        p.append(T(cx(.50), oy + 172, "oldest first", "c-tick"))
        p.append(f'<rect class="c-node-sunk" x="{f(ox)}" y="{oy + 214}" width="{f(pw)}" height="44" rx="10"/>')
        p.append(T(ox + 12, oy + 232, "unordered_map<OrderId, Locator>", "c-tick"))
        p.append(T(ox + 12, oy + 249, "id → level and list iterators", "c-tick"))
        return p

    def flat_panel(ox, oy):
        p = [T(ox, oy + 16, "FlatBook", "c-value c-hi"),
             T(ox, oy + 34, "flat level window + pooled order nodes", "c-tick")]
        cells = 12
        cw = pw / cells
        p.append(T(ox, oy + 62, f"Level window: {facts['window']:,} ticks", "c-tick"))
        for i in range(cells):
            cls = "c-accent" if i in (4, 5, 7) else "c-node"
            p.append(f'<rect class="{cls}" x="{f(ox + i * cw + 1)}" y="{oy + 70}" width="{f(cw - 2)}" height="24" rx="3"/>')
        p.append(T(ox, oy + 110, f"{facts['window_lo']:,}", "c-tick"))
        p.append(T(ox + pw, oy + 110, f"{facts['window_hi']:,}", "c-tick", "end"))
        p.append(T(ox + pw / 2, oy + 110, "index = price − base", "c-tick", "middle"))
        p.append(T(ox, oy + 138, "vector<Node> pool, uint32 prev/next", "c-tick"))
        for i in range(cells):
            cls = "c-node-sunk" if i in (2, 9, 10) else "c-node"
            p.append(f'<rect class="{cls}" x="{f(ox + i * cw + 1)}" y="{oy + 146}" width="{f(cw - 2)}" height="24" rx="3"/>')
        a, b, c = (ox + 1.5 * cw, ox + 4.5 * cw, ox + 7.5 * cw)
        p.append(f'<path class="c-line-accent" d="M{f(a)} {oy + 170}Q{f((a + b) / 2)} {oy + 186} {f(b)} {oy + 170}'
                 f'M{f(b)} {oy + 170}Q{f((b + c) / 2)} {oy + 186} {f(c)} {oy + 170}"/>')
        p.append(T(ox + pw, oy + 200, "grey slots: free list", "c-tick", "end"))
        half = (pw - 8) / 2
        for i, (l1, l2) in enumerate((("std::map fallback", "prices outside window"),
                                      ("IdIndex", "open addressing, Fibonacci hash"))):
            bx = ox + i * (half + 8)
            p.append(f'<rect class="c-node-sunk" x="{f(bx)}" y="{oy + 214}" width="{f(half)}" height="44" rx="10"/>')
            p.append(T(bx + 10, oy + 232, l1, "c-tick"))
            p.append(T(bx + 10, oy + 249, l2 if half > 200 else l2.split(",")[0], "c-tick"))
        return p

    parts = map_panel(0, 0)
    if stacked:
        parts += flat_panel(0, ph + 28)
        height = ph * 2 + 28
    else:
        parts += flat_panel(pw + 32, 0)
        height = ph
    desc = (f"MapBook: a std::map of price levels per side, each level a std::list of resting orders, and an "
            f"unordered_map from order id to iterators. FlatBook: a {facts['window']:,}-entry level array for ticks "
            f"{facts['window_lo']:,} to {facts['window_hi']:,}, a std::map for prices outside it, a vector of order "
            f"nodes linked by 32-bit indices with a free list, and an open-addressing id index.")
    return rk.svg(uid, width, height, "Storage layout of MapBook and FlatBook", desc, "".join(parts))


def mini_storage_svg(uid):
    """Bento card schematic: pointer hops vs one array index."""
    T, f = rk.svg_text, rk.coord
    p = [T(0, 22, "MapBook", "c-label")]
    pts = [(96, 16), (140, 40), (184, 16), (228, 40)]
    p.append('<path class="c-line" d="' + "".join(("M" if i == 0 else "L") + f"{x} {y}" for i, (x, y) in enumerate(pts)) + '"/>')
    for x, y in pts:
        p.append(f'<circle class="c-node" cx="{x}" cy="{y}" r="9"/>')
    p.append(T(250, 34, "tree walk", "c-tick"))
    p.append(T(0, 86, "FlatBook", "c-label c-hi"))
    for i in range(10):
        p.append(f'<rect class="{"c-accent" if i == 4 else "c-node"}" x="{86 + i * 16}" y="72" width="14" height="22" rx="3"/>')
    p.append(T(250, 88, "one index", "c-tick"))
    return rk.svg(uid, 330, 104, "Price lookup: tree walk versus one array index",
                   "MapBook follows pointers down a tree; FlatBook indexes one array slot.", "".join(p))


# ---------------------------------------------------------------- primer example

def primer_example(steps):
    """First replay step where an incoming order fills two resting orders queued
    at one price: shows price-time priority on real replay output."""
    for i, step in enumerate(steps[1:], start=1):
        fills = [e for e in step["events"] if e["type"] == "fill"]
        if len(fills) < 2 or len({e["price"] for e in fills[:2]}) != 1:
            continue
        before = steps[i - 1]
        side = step["cmd"].split()[2]
        book = before["asks"] if side == "B" else before["bids"]
        level = next((lvl for lvl in book if lvl[0] == fills[0]["price"]), None)
        if not level or level[2] != 2:
            continue
        ids = [str(e["resting_id"]) for e in fills[:2]]
        placed = []
        for rid in ids:
            src = [s for s in steps[:i] if s["cmd"].split()[1] == rid and s["cmd"].split()[0] in ("ADD", "MOD")]
            if not src:
                break
            placed.append(src[-1])
        if len(placed) != 2:
            continue
        return {"placed": placed, "before": before, "step": step, "fills": fills, "level": level}
    return None


def describe_events(step):
    out = []
    for e in step["events"]:
        if e["type"] == "fill":
            out.append(f"fill {e['qty']} @ {e['price']} vs #{e['resting_id']}")
        elif e["type"] == "rejected":
            out.append(f"rejected ({e.get('reason', '')})")
        else:
            out.append(f"{e['type']} {e.get('qty', '')}".strip())
    return out


def primer(steps, replay_error, results_sha):
    concept = rk.tag("p", Raw(
        "A <em>limit order</em> names a side, a price and a quantity, and rests in the book until something trades "
        "against it. Bids are sorted highest first, asks lowest first, and orders at one price queue by arrival. "
        "An incoming order that crosses trades against the best price first, then the oldest order at that price, "
        "and every fill prints at the resting order's price."))
    if not steps:
        return Raw(f'<div class="prose">{concept}{rk.tag("p", replay_error)}</div>')
    ex = primer_example(steps)
    if not ex:
        return Raw(f'<div class="prose">{concept}</div>')
    (a, b), step, fills, level = ex["placed"], ex["step"], ex["fills"], ex["level"]
    first, second = fills[0], fills[1]
    incoming = step["cmd"].split()
    listing = "\n".join(f"line {s['line']}  {s['cmd']}" + "".join(f"\n  → {e}" for e in describe_events(s))
                         for s in (a, b, step))
    side_word = "sell" if incoming[2] == "S" else "buy"
    rest = int(incoming[4]) - sum(e["qty"] for e in fills)
    body = rk.join([
        rk.tag("p", Raw(
            f"In the committed replay of <code>examples/demo_flow.txt</code>, orders #{esc(first['resting_id'])} and "
            f"#{esc(second['resting_id'])} both bid {level[0]} ticks, in that order. A {side_word} of "
            f"{esc(incoming[4])} at {esc(incoming[3])} then arrives:")),
        rk.code(listing, "Replay excerpt"),
        rk.tag("p", Raw(
            f"#{esc(first['resting_id'])} fills first ({first['qty']}) because it arrived first; "
            f"#{esc(second['resting_id'])} gets the remaining {second['qty']}. Both fills print at {first['price']}, "
            f"the resting price, not the seller's {esc(incoming[3])}."
            + (f" The order is <code>{esc(incoming[5])}</code> (immediate or cancel), so any unfilled remainder "
               f"would be cancelled; here {rest} remain." if len(incoming) > 5 else ""))),
    ])
    before = ex["before"]
    bids, asks = before["bids"][:3], before["asks"][:3]
    rows = [[str(p), "", f"{q} ({n})"] for p, q, n in reversed(asks)]
    rows += [[str(p), f"{q} ({n})" + (" ← fills" if p == level[0] else ""), ""] for p, q, n in bids]
    ladder = rk.table(f"Book before line {step['line']}: quantity (orders) at the top 3 prices, in ticks, each side",
                      ["Price", "Bids", "Asks"], rows, numeric={0, 1, 2})
    src = f"{REPO_URL}/blob/{results_sha}/results/replay.jsonl" if results_sha else None
    caption = rk.tag("p", Raw(
        "From <code>results/replay.jsonl</code>, the output of <code>lob_replay</code> running MapBook over a "
        "seeded synthetic flow (not market data)." + (f' <a class="src" href="{esc(src)}">Replay data</a>' if src else "")),
        class_="muted")
    return Raw(f'<div class="bento"><div class="span-8" style="--p:1">{rk.note("Price, then time", concept + body)}</div>'
               f'<div class="span-4" style="--p:2">{ladder}{caption}</div></div>')


# ---------------------------------------------------------------- page

def evidence_rows(bench, d, facts, src_sha, res_sha):
    def blob(rel, sha, start=None, end=None):
        anchor = ""
        if start:
            anchor = f"#L{start}" + (f"-L{end}" if end and end != start else "")
        return f"{REPO_URL}/blob/{sha}/{rel}{anchor}"

    bench_href = blob("results/bench.json", res_sha or src_sha)
    clock = line_range("bench/lob_bench.cpp", "void BM_ClockOverhead", "clock_tick_ns")
    diff = line_range("tests/differential_test.cpp", "TEST(Differential", "0 mismatches")
    win = line_range("include/lob/FlatBook.h", "Window of", "kDefaultWindow =")
    cancel = line_range("src/FlatBook.cpp", "Result FlatBook::cancelOrder", "return {Status::Accepted")
    mix = line_range("bench/lob_bench.cpp", "constexpr double kAddShare", "kMeasuredMessages =")
    return [
        ("E1", f"FlatBook {m_rate(d['flat'])} vs MapBook {m_rate(d['map'])} messages/s, {runs_text(d['reps'])}",
         "results/bench.json", bench_href, "Measured"),
        ("E2", f"FlatBook throughput {ratio_text(d['ratio'])} MapBook's", "ratio of the E1 medians", bench_href, "Derived"),
        ("E3", f"Average time per message {d['flat_ns']:.0f} ns vs {d['map_ns']:.0f} ns", "10⁹ ÷ E1 medians",
         bench_href, "Derived"),
        ("E4", f"Clock tick {d['tick']:.0f} ns", f"BM_ClockOverhead, bench/lob_bench.cpp:{clock[0]}–{clock[1]}",
         blob("bench/lob_bench.cpp", src_sha, *clock), "Measured"),
        ("E5", f"0 mismatches over {facts['diff_seeds']} seeds × {facts['diff_ops']:,} operations",
         f"tests/differential_test.cpp:{diff[0]}–{diff[1]}, run by ctest in CI",
         blob("tests/differential_test.cpp", src_sha, *diff), "Test"),
        ("E6", "Same tests pass under AddressSanitizer and UndefinedBehaviorSanitizer",
         "CMakePresets.json asan preset; CI job asan-ubsan-differential", blob("CMakePresets.json", src_sha), "Test"),
        ("E7", f"Mix {pct1(d['mix']['add'])} / {pct1(d['mix']['cancel'])} / {pct1(d['mix']['market'])} "
         f"add/cancel/market measured; {pct(facts['crossing_share'])} of adds priced to cross",
         f"bench/lob_bench.cpp:{mix[0]}–{mix[1]}; counts in results/bench.json",
         blob("bench/lob_bench.cpp", src_sha, *mix), "Code + measured"),
        ("E8", f"Flat window covers ticks {facts['window_lo']:,}–{facts['window_hi']:,}, fixed",
         f"include/lob/FlatBook.h:{win[0]}–{win[1]}", blob("include/lob/FlatBook.h", src_sha, *win), "Code"),
        ("E9", "Cancel is designed for average O(1): index lookup, unlink, level update",
         f"src/FlatBook.cpp:{cancel[0]}–{cancel[1]}", blob("src/FlatBook.cpp", src_sha, *cancel), "Code"),
    ]


def build_page(bench, bench_error, steps, replay_error, results_sha=None):
    if not bench:
        main = rk.join([rk.section("throughput", "No benchmark results yet", rk.tag("p", bench_error)),
                        rk.section("latency", "Latency", rk.tag("p", bench_error)),
                        rk.section("replay", "Replay", rk.tag("p", replay_error or ""))])
        return rk.page(title="Limit order book · Allen Wang", description="Limit order book benchmark report.",
                       canonical=PAGE_URL, header=rk.report_header(HOME_URL, "← Allen Wang", [("Source", REPO_URL)]),
                       main=main, footer=rk.footer("No benchmark run recorded."))
    ctx = bench["context"]
    d = derive(bench)
    facts = facts_from_sources()
    src_sha = ctx["git_sha"]
    res_sha = results_sha or src_sha
    ev = {row[0]: row for row in evidence_rows(bench, d, facts, src_sha, res_sha)}
    R = lambda eid: rk.ref(eid)
    runs = runs_text(d["reps"])
    mix = ctx["message_mix"]
    measured = f"{facts['measured'] / 1e6:g}{THIN}M"
    bench_href = ev["E1"][3]

    hero = rk.hero(
        eyebrow="Systems · C++17 · Benchmark",
        title="Limit order book",
        subtitle=Raw("A price-time-priority matching engine, built twice — a <code>std::map</code> version and a "
                     "flat-array version — so their speed can be compared directly."),
        value=ratio_text(d["ratio"]),
        label=Raw(f"FlatBook's throughput relative to MapBook{R('E2')}"),
        support=Raw(f"{m_rate(d['flat'])} vs {m_rate(d['map'])} messages per second, {runs}{R('E1')}"),
        context=f"One core, {ctx['cpu']}, {measured} synthetic messages per run ({mix}).",
        actions=[("Source", REPO_URL, True), ("Run the benchmark", "#reproduce", False)],
        figure=rk.variants(throughput_chart(bench, "hero-d", 420, False), throughput_chart(bench, "hero-t", 640, False),
                           throughput_chart(bench, "hero-p", 300, False)),
    )

    nav = rk.report_nav([("findings", "Findings"), ("replay", "Matching"), ("throughput", "Throughput"),
                         ("latency", "Latency"), ("storage", "Storage"), ("cancel", "Cancels"),
                         ("correctness", "Correctness"), ("methodology", "Method"), ("limitations", "Limits"),
                         ("reproduce", "Reproduce"), ("evidence", "Evidence")])

    findings = rk.bento([
        (8, 2, rk.join([
            rk.tag("h3", "Orders live in one array, linked by index", class_="card-heading"),
            Raw(f'<div style="max-width:400px;margin:4px 0 16px">{mini_storage_svg("mini")}</div>'),
            rk.tag("p", Raw(f"MapBook finds a price level by walking a tree; FlatBook indexes one slot of a "
                            f"{facts['window']:,}-entry array and links orders by 32-bit index.{R('E8')} "
                            f'<a href="#storage">Storage layout</a>')),
        ])),
        (4, 1, rk.join([
            rk.tag("h3", "Checked against a reference", class_="card-heading"),
            rk.metric("Mismatches", "0"),
            rk.tag("p", Raw(f"{facts['diff_seeds'] * facts['diff_ops']:,} random operations; MapBook, FlatBook and a "
                            f"narrow-window FlatBook compared with a naive reference book after every step; same tests "
                            f"clean under ASan and UBSan.{R('E5')}{R('E6')}")),
        ])),
        (6, 3, rk.join([
            rk.tag("h3", "Time per message", class_="card-heading"),
            rk.comparison([("FlatBook", d["flat_ns"], ns(d["flat_ns"]), True),
                           ("MapBook", d["map_ns"], ns(d["map_ns"]), False)], max(d["flat_ns"], d["map_ns"], d["tick"])),
            rk.tag("p", Raw(f"Average, derived from throughput.{R('E3')} Both sit below the {d['tick']:.0f}{THIN}ns "
                            f"clock tick{R('E4')}, which is why throughput, not per-message timing, is the headline.")),
        ])),
        (6, 4, rk.join([
            rk.tag("h3", "Fast inside a fixed window", class_="card-heading"),
            rk.tag("p", Raw(f"Prices in ticks {facts['window_lo']:,}–{facts['window_hi']:,} use the flat array; anything "
                            f"outside falls back to <code>std::map</code>. The window never moves, and it is centred on "
                            f"the synthetic flow's prices.{R('E8')}")),
        ])),
    ])
    findings_sec = rk.section("findings", "What the measurements show", findings, eyebrow="Findings")

    primer_sec = rk.section("replay", "A limit order book matches buyers and sellers by price, then by arrival",
                            primer(steps, replay_error, results_sha), eyebrow="Primer")

    # Throughput
    tput_fig = rk.figure(
        "fig-throughput",
        f"FlatBook sustains {ratio_text(d['ratio'])} MapBook's throughput",
        rk.variants(throughput_chart(bench, "tp-d", 1000, True), throughput_chart(bench, "tp-t", 640, True),
                    throughput_chart(bench, "tp-p", 300, True)),
        rk.join([rk.tag("p", Raw(
            f"Bars: {runs}; whiskers: fastest and slowest of the {d['reps']} repetitions. Each repetition times "
            f"{facts['measured']:,} messages after {facts['warmup']:,} untimed warm-up messages, seed {esc(ctx['seed'])}, "
            f"{esc(ctx['compiler'])} <code>{esc(ctx['cxx_flags'])}</code>, one core of an {esc(ctx['cpu'])}.{R('E1')}"))]),
        question="How many messages per second does each book process on the same flow?",
        table=rk.data_table("Throughput per book (million messages/s)", ["Book", "Median", "Min", "Max", "Runs"],
                            [[b, f"{bench['throughput'][b]['median'] / 1e6:.2f}", f"{bench['throughput'][b]['min'] / 1e6:.2f}",
                              f"{bench['throughput'][b]['max'] / 1e6:.2f}", str(len(bench['throughput'][b]['runs']))]
                             for b in BOOKS], numeric={1, 2, 3, 4}),
        data_href=bench_href,
    )
    tput_body = rk.join([
        tput_fig,
        Raw('<div class="prose" style="margin-top:32px">'),
        rk.tag("p", Raw(
            f"The gap is not a product of run-to-run noise: the slowest FlatBook repetition still processed "
            f"{ratio_text(d['ratio_floor'])} as many messages as the fastest MapBook repetition.")),
        rk.tag("p", Raw(
            "For a price inside the window, FlatBook reaches its level with one array index, and its order nodes sit "
            "contiguously in one vector. MapBook walks a red-black tree — O(log n) pointer hops — and allocates a list "
            "node per resting order. The measured gap is consistent with that difference; this benchmark does not "
            "isolate which part contributes most.")),
        Raw("</div>"),
    ])
    tput_sec = rk.section("throughput", f"FlatBook processes {ratio_text(d['ratio'])} as many messages per second",
                          tput_body, eyebrow="Result")

    # Latency
    tick = d["tick"]
    tail_book, tail_op = d["tail_max_key"]
    tail_label = dict(OPS)[tail_op]
    lat_fig = rk.figure(
        "fig-latency",
        f"{d['p50_at_floor']} of {d['p50_total']} medians are at or below one {tick:.0f}{THIN}ns clock tick",
        rk.variants(latency_chart(bench, "lat-d", 1000), latency_chart(bench, "lat-t", 640),
                    latency_chart(bench, "lat-p", 320)),
        rk.join([rk.tag("p", Raw(
            f"Each row: p50 (filled dot), p90, p99 and p99.9 (ring, value labelled) of one operation, from "
            f"{facts['measured']:,} individually timed messages per run; each percentile is the {runs}. "
            f"<code>steady_clock</code> advances in {tick:.0f}{THIN}ns steps, so a call that starts and ends within "
            f"one tick reads 0{THIN}ns; those are drawn in the shaded band.{R('E4')}"))]),
        question="How long does a single add, cancel or market order take, and can the clock resolve it?",
        table=rk.data_table(
            "Latency percentiles (ns), median across runs, with p99.9 range across runs",
            ["Operation", "Book", "Samples per run", "p50", "p90", "p99", "p99.9", "p99.9 min–max"],
            [[label, b, f"{bench['latency'][b][op]['count']:,}"]
             + [f"{bench['latency'][b][op][p]['median']:,.0f}" for p in LADDER]
             + [f"{bench['latency'][b][op]['p99.9']['min']:,.0f}–{bench['latency'][b][op]['p99.9']['max']:,.0f}"]
             for op, label in OPS for b in BOOKS if op in bench["latency"].get(b, {})],
            numeric={2, 3, 4, 5, 6, 7}),
        data_href=bench_href,
    )
    lat_body = rk.join([
        lat_fig,
        Raw('<div class="prose" style="margin-top:32px">'),
        rk.tag("p", Raw(
            f"Medians read 0 or one tick, so per-operation timing cannot separate the two books. Only the tail rises "
            f"clearly above resolution: the largest p99.9 is {tail_book} {tail_label} at {ns(d['tail_max'])}. Across "
            f"the {d['reps']} runs, one series' p99.9 moved by up to {d['tail_swing']:.1f}×, so the tail is shown but "
            f"not compared.")),
        Raw("</div>"),
    ])
    lat_sec = rk.section("latency", "Single operations are too fast for the clock to separate", lat_body,
                         eyebrow="Resolution")

    # Storage
    store_fig = rk.figure(
        "fig-storage", "MapBook chases pointers; FlatBook indexes arrays",
        rk.variants(storage_svg("st-d", 1000, facts), storage_svg("st-t", 640, facts), storage_svg("st-p", 300, facts)),
        rk.tag("p", Raw(f"Schematic, not to scale. Filled cells are occupied price levels; arcs are 32-bit "
                        f"<code>prev</code>/<code>next</code> links.{R('E8')}")),
        question="Where does each book keep price levels and orders?",
    )
    decisions = rk.table(
        "Design decisions in FlatBook",
        ["Choice", "Alternative", "Consequence"],
        [
            [f"Level array for {facts['window']:,} ticks", "std::map of levels (MapBook)",
             "One index per lookup inside the window; a fixed range chosen up front"],
            ["Order nodes in one vector, uint32 links", "std::list node per order",
             "Nodes reused through a free list; links are half the size of pointers"],
            ["Open-addressing id index, Fibonacci hash", "std::unordered_map",
             "Sequential ids spread across slots; table doubles at load factor 0.5"],
            ["std::map for prices outside the window", "Reject or re-centre", "Correct everywhere; slower outside"],
        ], wrap_cols={1, 2})
    store_sec = rk.section("storage", "Orders live in one pooled array, linked by index",
                           rk.join([store_fig, Raw('<div style="margin-top:32px">'), decisions, Raw("</div>")]),
                           eyebrow="Mechanism")

    # Cancel
    cancel_body = rk.join([
        Raw('<div class="prose">'),
        rk.tag("p", Raw(f"FlatBook's cancel is designed for average O(1): find the node in the id index, unlink it "
                        f"from its level's queue, and update the level total.{R('E9')} Three cases cost more:")),
        Raw("<ol>"),
        rk.tag("li", Raw(f"Emptying the best level triggers a linear scan of up to {facts['window']:,} ticks for the "
                         f"next occupied level.")),
        rk.tag("li", "Prices outside the window live in std::map, so their levels cost O(log n)."),
        rk.tag("li", "The id index keeps its load factor at or below 0.5 and doubles when it would exceed it; the "
                     "growth step is O(n)."),
        Raw("</ol></div>"),
    ])
    cancel_sec = rk.section("cancel", "Cancels are constant-time on average, with three exceptions", cancel_body,
                            eyebrow="Complexity")

    # Correctness
    corr = rk.table(
        "Differential test: what is compared after every operation",
        ["Aspect", "Coverage"],
        [
            ["Books", "MapBook, FlatBook, FlatBook with a 16-tick window (most prices in the std::map fallback)"],
            ["Reference", "NaiveBook: a plain sorted list, written for clarity"],
            ["Operations", "add, cancel, modify"],
            ["Order types", "limit, market, immediate-or-cancel, fill-or-kill, post-only"],
            ["Checks", "result, full event stream, depth to 10 levels per side, best bid and ask, order count, invariants"],
            ["Size", f"{facts['diff_seeds']} seeds × {facts['diff_ops']:,} operations = "
                     f"{facts['diff_seeds'] * facts['diff_ops']:,}"],
        ], wrap_cols={1})
    corr_body = rk.join([
        corr,
        rk.tag("p", Raw(f"The run stops at the first mismatch and prints the seed and operation; CI runs it in the "
                        f"Release build and again under AddressSanitizer and UndefinedBehaviorSanitizer.{R('E5')}{R('E6')} "
                        f"The benchmark itself sends only limit adds, cancels and market orders; the other order types "
                        f"are covered by tests, not timed."), class_="prose"),
    ])
    corr_sec = rk.section("correctness",
                          f"Both books agree with a naive reference on {facts['diff_seeds'] * facts['diff_ops'] / 1e6:g} "
                          f"million random operations", corr_body, eyebrow="Correctness")

    # Methodology
    load_b = " / ".join(f"{v:.2f}" for v in ctx["load_before"]) or None
    load_a = " / ".join(f"{v:.2f}" for v in ctx["load_after"]) or None
    iters = "; ".join(f"{b} {min(v)}" + (f"–{max(v)}" if max(v) != min(v) else "") for b, v in bench["iterations"].items())
    method = rk.methodology([
        ("Hardware", f"{ctx['cpu']}, {ctx['num_cpus']} cores; benchmark uses one thread" if ctx["num_cpus"] else ctx["cpu"]),
        ("Operating system", None),
        ("Compiler", Raw(f"{esc(ctx['compiler'])}, <code>{esc(ctx['cxx_flags'])}</code>, CMake Release preset")),
        ("Harness", f"Google Benchmark {ctx['library_version']} ({ctx['build_type']} build)"),
        ("Code measured", Raw(f'<a class="src" href="{REPO_URL}/tree/{esc(src_sha)}">{esc(src_sha[:7])}</a>')),
        ("Data", Raw(f"Seeded synthetic flow, seed {esc(ctx['seed'])}; target mix {esc(mix)}, measured "
                     f"{pct1(d['mix']['add'])} / {pct1(d['mix']['cancel'])} / {pct1(d['mix']['market'])}; "
                     f"{pct(d['crossing_of_adds'])} of adds crossed.{R('E7')}")),
        ("Warm-up and size", f"{facts['warmup']:,} untimed messages, then {facts['measured']:,} timed, per iteration"),
        ("Repetitions", f"{d['reps']}; iterations per repetition: {iters}"),
        ("Throughput", "items per second from Google Benchmark CPU time; median across repetitions, whiskers min–max"),
        ("Latency timer", f"std::chrono::steady_clock around each call; measured tick {tick:.0f} ns; percentiles "
                          f"within each run (nearest rank), then the median across runs"),
        ("Load average", f"{load_b} before, {load_a} after (1 / 5 / 15 min); shared machine, not idle"
         if load_b and load_a else load_b),
        ("Run date", ctx["date"][:10]),
    ])
    method_sec = rk.section("methodology", "Methodology", method, eyebrow="Conditions")

    limits = rk.limitations([
        ("One core, one CPU.", f"All numbers are single-threaded on an {ctx['cpu']}; other CPUs and compilers may "
                               f"give a different ratio."),
        ("Shared machine.", f"Load average {load_b} at the start of the run. Whiskers show spread within this session "
                            f"only; absolute rates move between sessions more than the ratio does."),
        ("One synthetic flow.", "No sweep over book depth, price spread or window size, so where FlatBook's advantage "
                                "ends is not measured."),
        ("Window tuned to the flow.", f"Ticks {facts['window_lo']:,}–{facts['window_hi']:,} are centred on the "
                                      f"synthetic prices; a flow that drifts out of the window falls back to std::map."),
        ("No allocation measurement.", "The benchmark does not count heap allocations, so this report makes no "
                                       "claim about them."),
        ("Unstable tail.", f"p99.9 moved by up to {d['tail_swing']:.1f}× across runs; tails are shown, not compared."),
        ("Matching core only.", "No networking, market-data feed or multiple instruments."),
    ])
    limits_sec = rk.section("limitations", "Results cover one core, one CPU and one synthetic workload", limits,
                            eyebrow="Limitations")

    steps_html = rk.reproduce([
        ("Build in Release and run all tests (CMake 3.25+, Ninja, a C++17 compiler; downloads GoogleTest and "
         "Google Benchmark). Expect “100% tests passed”.", "cmake --workflow --preset default"),
        (Raw(f"Run the benchmark ({d['reps']} repetitions, about {bench['timed_seconds']:.0f}{THIN}s of timed work "
             f"here), the replay, and rebuild this page, the card and the README table from the results."),
         "scripts/run_bench.sh"),
        (Raw(f"Check the differential test line: “differential: {facts['diff_seeds']} seeds x {facts['diff_ops']} ops = "
             f"{facts['diff_seeds'] * facts['diff_ops']} ops, 0 mismatches”."),
         "./build/lob_differential_test"),
        ("Sanitizer build (Debug, ASan + UBSan); the differential test is much slower here.",
         "cmake --workflow --preset asan"),
        ("Check that every number on this page matches results/bench.json.",
         "python3 -m unittest discover -s tests -p 'test_*.py'"),
    ])
    repro_sec = rk.section("reproduce", "Reproduce it in five commands", steps_html, eyebrow="Reproduce",
                           intro=Raw(f'Commands run from a clone of <a class="src" href="{REPO_URL}">{REPO}</a>.'))

    ev_table = rk.table("Evidence ledger", ["ID", "Claim", "Source", "Class"],
                        [[Raw(f'<span id="evidence-{eid.lower()}">{eid}</span>'), claim,
                          Raw(f'<a class="src" href="{esc(href)}">{esc(src)}</a>'), cls]
                         for eid, claim, src, href, cls in ev.values()], wrap_cols={1, 2})
    ev_sec = rk.section("evidence", "Every number traces to a committed file", ev_table, eyebrow="Sources")

    main = rk.join([hero, nav, findings_sec, primer_sec, tput_sec, lat_sec, store_sec, cancel_sec, corr_sec,
                    method_sec, limits_sec, repro_sec, ev_sec])
    footer = rk.footer(
        Raw(f"Generated by <code>scripts/build_report.py</code> from <code>results/bench.json</code> · "
            f"code {esc(src_sha[:7])} · run {esc(ctx['date'][:10])} on {esc(ctx['cpu'])}"),
        Raw(f'<a href="{HOME_URL}">Allen Wang</a> · <a href="{REPO_URL}">Source</a>'))
    return rk.page(
        title="Limit order book · Allen Wang",
        description=(f"FlatBook, a flat-array C++17 limit order book, processes {ratio_text(d['ratio'])} the messages "
                     f"per second of a std::map version ({runs}, one core, {ctx['cpu']})."),
        canonical=PAGE_URL,
        header=rk.report_header(HOME_URL, "← Allen Wang", [("Source", REPO_URL)]),
        main=main, footer=footer)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bench", default="results/bench.json")
    parser.add_argument("--replay", default="results/replay.jsonl")
    parser.add_argument("--out", default="docs/index.html")
    parser.add_argument("--card", help="also write the portfolio card JSON to this path")
    parser.add_argument("--card-book", default=DEFAULT_BOOK, choices=BOOKS)
    parser.add_argument("--card-command", default="scripts/run_bench.sh",
                        help="the command recorded in card.json")
    parser.add_argument("--readme", help="rewrite the results block between the README markers")
    args = parser.parse_args()

    bench, bench_error = load_bench(args.bench)
    steps, replay_error = load_replay(args.replay)
    results_sha = git_last_commit(os.path.relpath(os.path.abspath(args.bench), ROOT)) if bench else None
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(build_page(bench, bench_error, steps, replay_error, results_sha))

    if args.card or args.readme:
        if not bench:
            sys.exit(f"cannot write card or README table: {bench_error}")
    if args.card:
        os.makedirs(os.path.dirname(args.card) or ".", exist_ok=True)
        with open(args.card, "w", encoding="utf-8") as handle:
            json.dump(build_card(bench, args.card_book, args.card_command), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    if args.readme:
        update_readme(args.readme, bench)


if __name__ == "__main__":
    main()
