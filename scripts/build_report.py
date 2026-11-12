"""Build docs/index.html (and optionally site/card.json and the README results
table) from lob_bench JSON output and lob_replay JSONL output.

Usage:
  python3 scripts/build_report.py [--bench results/bench.json]
      [--replay results/replay.jsonl] [--out docs/index.html]
      [--card site/card.json] [--readme README.md]

Python standard library only. Every number on the page comes from the inputs.
"""

import argparse
import datetime
import html
import json
import os
import re
import sys

BOOKS = ["MapBook", "FlatBook"]
OPS = [
    ("add_resting", "add (resting)"),
    ("add_crossing", "add (crossing)"),
    ("cancel", "cancel"),
    ("market", "market"),
]
PERCENTILES = ["p50", "p90", "p99", "p99.9", "max"]
DEFAULT_BOOK = "MapBook"
NO_BENCH = "No benchmark data: run scripts/build_report.py after cmake --build"
NO_REPLAY = "No replay data: run scripts/build_report.py after cmake --build"
README_START = "<!-- results:start -->"
README_END = "<!-- results:end -->"


def load_bench(path):
    """Return (bench dict, error string). bench holds context, latency and throughput."""
    if not path or not os.path.exists(path):
        return None, NO_BENCH
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as error:
        return None, f"{path} is not valid JSON (line {error.lineno})"
    context = raw.get("context", {})
    latency = {}
    throughput = {}
    clock = {}
    for bench in raw.get("benchmarks", []):
        name = bench.get("name", "")
        match = re.match(r"BM_(Latency|Throughput)<(\w+)>", name)
        if name.startswith("BM_ClockOverhead"):
            clock = {
                "tick_ns": bench.get("clock_tick_ns"),
                "overhead_p50_ns": bench.get("timer_overhead_p50_ns"),
            }
        elif match and match.group(1) == "Latency":
            book = match.group(2)
            latency[book] = {}
            for op, _ in OPS:
                if f"{op}_count" not in bench:
                    continue
                latency[book][op] = {"count": int(bench[f"{op}_count"])}
                for p in PERCENTILES:
                    latency[book][op][p] = bench[f"{op}_{p}_ns"]
        elif match and match.group(1) == "Throughput" and bench.get("run_type") != "aggregate":
            throughput[match.group(2)] = bench.get("items_per_second")
    if not latency:
        return None, f"{path} has no BM_Latency results"
    return {
        "context": {
            "cpu": context.get("cpu", "unknown"),
            "compiler": context.get("compiler", "unknown"),
            "cxx_flags": context.get("cxx_flags", "unknown"),
            "git_sha": context.get("git_sha", "unknown"),
            "date": context.get("date", "unknown"),
            "message_mix": context.get("message_mix", "unknown"),
            "seed": context.get("seed", "unknown"),
            "build_type": context.get("library_build_type", "unknown"),
        },
        "clock": clock,
        "latency": latency,
        "throughput": throughput,
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


def format_ns(value):
    return f"{value:,.0f} ns"


def takeaway_latency(bench):
    latency = bench["latency"]
    if not all(book in latency and "cancel" in latency[book] for book in BOOKS):
        return ""
    flat = latency["FlatBook"]["cancel"]["p99"]
    mapped = latency["MapBook"]["cancel"]["p99"]
    return f"FlatBook cancel p99: {format_ns(flat)} vs MapBook {format_ns(mapped)}."


def takeaway_throughput(bench):
    rates = bench["throughput"]
    if not all(book in rates for book in BOOKS):
        return ""
    ratio = rates["FlatBook"] / rates["MapBook"]
    return (
        f"FlatBook processes {rates['FlatBook'] / 1e6:.1f}M msgs/s, "
        f"{ratio:.2f}x MapBook's {rates['MapBook'] / 1e6:.1f}M on the same flow."
    )


def card_rows(bench, book):
    ops = bench["latency"][book]
    rows = []
    for label, op in (("add", "add_resting"), ("cancel", "cancel"), ("match", "add_crossing")):
        stats = ops[op]
        rows.append({"label": label, "low": stats["p50"], "mid": stats["p90"], "high": stats["p99"]})
    return rows


def build_card(bench, book, command):
    context = bench["context"]
    rows = card_rows(bench, book)
    add, cancel, match = rows
    takeaway = (
        f"{book} median add {add['low']:.0f} ns, cancel p99 {cancel['high']:.0f} ns, "
        f"match p99 {match['high']:.0f} ns on {context['cpu']}."
    )
    return {
        "schema_version": 1,
        "repo": "limitorderbook",
        "sha": context["git_sha"],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": command,
        "data_note": (
            f"Seeded synthetic order flow, 55/40/5 add/cancel/market, {book}, "
            f"{context['cpu']}, {context['compiler']}"
        ),
        "metric": {
            "label": "Median add latency",
            "value": add["low"],
            "unit": "ns",
            "higher_is_better": False,
        },
        "takeaway": takeaway[:140],
        "chart": {
            "type": "interval",
            "x_label": "Latency",
            "x_unit": "ns",
            "x_scale": "log",
            "rows": rows,
        },
    }


def readme_table(bench):
    context = bench["context"]
    clock = bench["clock"]
    lines = [
        f"Measured on {context['cpu']} with {context['compiler']}, flags `{context['cxx_flags']}`, "
        f"commit `{context['git_sha'][:12]}`, {context['date'][:10]}. "
        f"Seeded synthetic flow ({context['message_mix']}, seed {context['seed']}), "
        "1,000,000 timed messages after 100,000 warm-up messages.",
        "",
        "| Book | Operation | Samples | p50 | p90 | p99 | p99.9 | max |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for book in BOOKS:
        for op, label in OPS:
            stats = bench["latency"].get(book, {}).get(op)
            if not stats:
                continue
            cells = " | ".join(f"{stats[p]:,.0f}" for p in PERCENTILES)
            lines.append(f"| {book} | {label} | {stats['count']:,} | {cells} |")
    lines.append("")
    lines.append("Latencies in ns. Throughput on the same flow, without per-message timing:")
    lines.append("")
    lines.append("| Book | Messages/s |")
    lines.append("| :--- | ---: |")
    for book in BOOKS:
        rate = bench["throughput"].get(book)
        if rate:
            lines.append(f"| {book} | {rate / 1e6:.2f}M |")
    if clock.get("tick_ns") is not None:
        lines.append("")
        lines.append(
            f"The steady_clock tick on this machine is {clock['tick_ns']:.0f} ns and an empty "
            f"timer pair has a p50 of {clock['overhead_p50_ns']:.0f} ns, so any percentile at or "
            "near one tick is at the clock's resolution, not a measured cost. Throughput gives the "
            "mean cost per message without that limit."
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


def build_page(bench, bench_error, steps, replay_error):
    payload = {
        "books": BOOKS,
        "ops": [{"key": key, "label": label} for key, label in OPS],
        "bench": bench,
        "benchError": bench_error,
        "latencyTakeaway": takeaway_latency(bench) if bench else "",
        "throughputTakeaway": takeaway_throughput(bench) if bench else "",
        "replay": steps,
        "replayError": replay_error,
    }
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    if bench:
        context = bench["context"]
        meta = (
            f"{html.escape(context['cpu'])}, {html.escape(context['compiler'])}, "
            f"flags <code>{html.escape(context['cxx_flags'])}</code>, commit "
            f"<code>{html.escape(context['git_sha'][:12])}</code>, run {html.escape(context['date'][:10])}."
        )
    else:
        meta = "No benchmark run recorded."
    return TEMPLATE.replace("{{META}}", meta).replace("{{DATA}}", data)


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Order Book Report</title>
<style>
:root {
  --bg: #ffffff; --fg: #1d2330; --muted: #5b6475; --grid: #e3e6ec; --panel: #f6f7f9;
  --a: #2b6cb0; --b: #c05621; --bid: #2b6cb0; --ask: #c05621; --outline: #1d2330;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151b; --fg: #e6e9ef; --muted: #9aa3b2; --grid: #2a2f3a; --panel: #1a1e26;
    --a: #63a4e8; --b: #f0965a; --bid: #63a4e8; --ask: #f0965a; --outline: #e6e9ef;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 32px 0 4px; }
p { margin: 4px 0; }
.meta, .note { color: var(--muted); font-size: 0.9rem; }
.takeaway { font-weight: 600; margin: 8px 0; }
.empty { padding: 24px; background: var(--panel); border-radius: 8px; color: var(--muted); }
svg { display: block; width: 100%; height: auto; overflow: visible; }
svg text { fill: var(--fg); font-size: 12px; }
svg .muted { fill: var(--muted); }
.gridline { stroke: var(--grid); }
.controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.controls input[type=range] { flex: 1 1 160px; min-width: 0; }
button { font: inherit; padding: 4px 12px; border-radius: 6px; border: 1px solid var(--grid);
  background: var(--panel); color: var(--fg); cursor: pointer; }
#step-text { font-family: ui-monospace, Menlo, monospace; font-size: 0.85rem; min-height: 3em;
  overflow-wrap: anywhere; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.85rem; color: var(--muted); }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
code { font-family: ui-monospace, Menlo, monospace; font-size: 0.85em; }
</style>
</head>
<body>
<main>
<h1>Limit order book benchmark</h1>
<p class="meta">{{META}}</p>
<p class="note">All order flow on this page is seeded and synthetic, not market data.</p>

<section id="latency">
<h2>Latency per operation</h2>
<p class="takeaway" id="latency-takeaway"></p>
<div class="legend" id="latency-legend"></div>
<div id="latency-chart"></div>
<p class="note" id="clock-note"></p>
</section>

<section id="throughput">
<h2>Throughput</h2>
<p class="takeaway" id="throughput-takeaway"></p>
<div id="throughput-chart"></div>
</section>

<section id="replay">
<h2>Book replay</h2>
<p class="note">examples/demo_flow.txt through MapBook. Bids on the left, asks on the right, one row per price in ticks. Rows that traded in the current step are outlined. Use the arrow keys to step.</p>
<div class="controls" id="replay-controls">
  <button type="button" id="prev" aria-label="Previous step">Prev</button>
  <input type="range" id="scrubber" min="0" value="0" aria-label="Replay step">
  <button type="button" id="next" aria-label="Next step">Next</button>
  <span id="step-count" class="note"></span>
</div>
<p id="step-text"></p>
<div id="ladder"></div>
</section>
</main>
<script type="application/json" id="report-data">{{DATA}}</script>
<script>
(function () {
  "use strict";
  const data = JSON.parse(document.getElementById("report-data").textContent);
  const NS = "http://www.w3.org/2000/svg";
  const colors = { MapBook: "var(--a)", FlatBook: "var(--b)" };

  function el(name, attrs, parent, text) {
    const node = document.createElementNS(NS, name);
    for (const key in attrs) node.setAttribute(key, attrs[key]);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }
  function empty(target, message) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = message;
    target.replaceChildren(div);
  }
  function fmt(value) { return Math.round(value).toLocaleString("en-US"); }
  function chartWidth(target) { return Math.max(300, Math.floor(target.clientWidth || 640)); }

  function marker(shape, x, y, color, parent) {
    if (shape === "p50") return el("circle", { cx: x, cy: y, r: 5, fill: color }, parent);
    if (shape === "p99") return el("rect", { x: x - 5, y: y - 5, width: 10, height: 10, fill: "none", stroke: color, "stroke-width": 2 }, parent);
    return el("path", { d: `M${x},${y - 6} L${x + 6},${y + 5} L${x - 6},${y + 5} Z`, fill: color }, parent);
  }

  function drawLatency() {
    const target = document.getElementById("latency-chart");
    if (!data.bench) { empty(target, data.benchError); return; }
    document.getElementById("latency-takeaway").textContent = data.latencyTakeaway;
    const clock = data.bench.clock;
    if (clock && clock.tick_ns != null) {
      document.getElementById("clock-note").textContent =
        `Clock tick ${fmt(clock.tick_ns)} ns; empty timer p50 ${fmt(clock.overhead_p50_ns)} ns. ` +
        "Values at or near one tick are at the clock's resolution.";
    }
    const legend = document.getElementById("latency-legend");
    legend.innerHTML = "";
    for (const book of data.books) {
      const span = document.createElement("span");
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = colors[book];
      span.append(swatch, book);
      legend.appendChild(span);
    }
    for (const label of ["circle = p50", "square = p99", "triangle = p99.9"]) {
      const span = document.createElement("span");
      span.textContent = label;
      legend.appendChild(span);
    }

    const values = [];
    for (const book of data.books) for (const op of data.ops) {
      const s = (data.bench.latency[book] || {})[op.key];
      if (s) values.push(s.p50, s["p99.9"]);
    }
    const lo = Math.pow(10, Math.floor(Math.log10(Math.max(1, Math.min(...values)))));
    const hi = Math.pow(10, Math.ceil(Math.log10(Math.max(...values))));
    const width = chartWidth(target), left = width < 480 ? 96 : 112, right = 40, top = 8, rowH = 52, bottom = 40;
    const height = top + rowH * data.ops.length + bottom;
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "Latency percentiles per operation, log scale" });
    const x = (v) => left + (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)) * (width - left - right);
    for (let t = lo; t <= hi; t *= 10) {
      el("line", { class: "gridline", x1: x(t), x2: x(t), y1: top, y2: height - bottom }, svg);
      el("text", { x: x(t), y: height - bottom + 16, "text-anchor": "middle", class: "muted" }, svg, fmt(t));
    }
    el("text", { x: left + (width - left - right) / 2, y: height - 6, "text-anchor": "middle" }, svg, "Latency (ns, log scale)");
    data.ops.forEach((op, i) => {
      const row = el("g", { class: "row", "data-op": op.key }, svg);
      const y0 = top + i * rowH;
      el("text", { x: 0, y: y0 + rowH / 2 + 4 }, row, op.label);
      data.books.forEach((book, j) => {
        const s = (data.bench.latency[book] || {})[op.key];
        if (!s) return;
        const y = y0 + 16 + j * 20;
        const color = colors[book];
        el("line", { x1: x(s.p50), x2: x(s["p99.9"]), y1: y, y2: y, stroke: color, "stroke-width": 1.5 }, row);
        for (const p of ["p50", "p99", "p99.9"]) {
          const m = marker(p, x(s[p]), y, color, row);
          el("title", {}, m, `${book} ${op.label} ${p}: ${fmt(s[p])} ns (${fmt(s.count)} samples)`);
        }
        el("text", { x: Math.min(x(s["p99.9"]) + 10, width - 32), y: y + 4, class: "muted" }, row, book === "MapBook" ? "Map" : "Flat");
      });
    });
    target.replaceChildren(svg);
  }

  function drawThroughput() {
    const target = document.getElementById("throughput-chart");
    if (!data.bench) { empty(target, data.benchError); return; }
    const rates = data.bench.throughput;
    const books = data.books.filter((b) => rates[b]);
    if (!books.length) { empty(target, data.benchError || "No throughput results in bench.json"); return; }
    document.getElementById("throughput-takeaway").textContent = data.throughputTakeaway;
    const width = chartWidth(target), left = 80, right = 72, barH = 28, gap = 14, bottom = 36;
    const height = books.length * (barH + gap) + bottom;
    const max = Math.max(...books.map((b) => rates[b]));
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "Throughput in messages per second" });
    books.forEach((book, i) => {
      const y = i * (barH + gap);
      const w = rates[book] / max * (width - left - right);
      el("text", { x: 0, y: y + barH / 2 + 4 }, svg, book);
      const bar = el("rect", { x: left, y, width: w, height: barH, fill: colors[book] }, svg);
      el("title", {}, bar, `${book}: ${fmt(rates[book])} messages/s`);
      el("text", { x: left + w + 6, y: y + barH / 2 + 4 }, svg, `${(rates[book] / 1e6).toFixed(2)}M/s`);
    });
    el("text", { x: left + (width - left - right) / 2, y: height - 8, "text-anchor": "middle" }, svg, "Messages per second, standard mix");
    target.replaceChildren(svg);
  }

  function setupReplay() {
    const target = document.getElementById("ladder");
    const controls = document.getElementById("replay-controls");
    if (!data.replay) { controls.hidden = true; empty(target, data.replayError); return; }
    const steps = data.replay;
    let maxQty = 1;
    for (const s of steps) for (const l of s.bids.concat(s.asks)) maxQty = Math.max(maxQty, l[1]);
    const scrubber = document.getElementById("scrubber");
    scrubber.max = String(steps.length - 1);
    let current = 0;

    function describe(step) {
      const parts = step.events.map((e) => {
        if (e.type === "fill") return `fill ${e.qty} @ ${e.price} vs #${e.resting_id}`;
        if (e.type === "rejected") return `rejected (${e.reason})`;
        return `${e.type} ${e.qty}`;
      });
      return `${step.cmd}  ->  ${parts.join(", ")}`;
    }

    function draw() {
      const step = steps[current];
      scrubber.value = String(current);
      document.getElementById("step-count").textContent = `step ${current + 1} of ${steps.length}`;
      document.getElementById("step-text").textContent = describe(step);
      const fillPrices = new Set(step.events.filter((e) => e.type === "fill").map((e) => e.price));
      const prices = new Set(fillPrices);
      for (const l of step.bids.concat(step.asks)) prices.add(l[0]);
      const sorted = Array.from(prices).sort((a, b) => b - a);
      const bids = new Map(step.bids.map((l) => [l[0], l]));
      const asks = new Map(step.asks.map((l) => [l[0], l]));
      const width = chartWidth(target), mid = width / 2, labelW = 56, rowH = 20, top = 24, bottom = 32;
      const half = mid - labelW / 2 - 8;
      const height = top + Math.max(sorted.length, 1) * rowH + bottom;
      const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "Depth ladder", id: "ladder-svg", "data-step": String(current) });
      el("text", { x: mid - labelW / 2 - 8, y: 14, "text-anchor": "end" }, svg, "Bids (qty)");
      el("text", { x: mid + labelW / 2 + 8, y: 14 }, svg, "Asks (qty)");
      el("text", { x: mid, y: 14, "text-anchor": "middle", class: "muted" }, svg, "price");
      if (!sorted.length) el("text", { x: mid, y: top + 14, "text-anchor": "middle", class: "muted" }, svg, "Book is empty");
      sorted.forEach((price, i) => {
        const y = top + i * rowH;
        const g = el("g", { class: "level", "data-price": String(price) }, svg);
        el("text", { x: mid, y: y + 14, "text-anchor": "middle" }, g, String(price));
        const bid = bids.get(price), ask = asks.get(price);
        if (bid) {
          const w = bid[1] / maxQty * half;
          el("rect", { x: mid - labelW / 2 - 4 - w, y: y + 2, width: w, height: rowH - 4, fill: "var(--bid)" }, g);
          el("text", { x: mid - labelW / 2 - 8 - w, y: y + 14, "text-anchor": "end", class: "muted" }, g, `${bid[1]} (${bid[2]})`);
        }
        if (ask) {
          const w = ask[1] / maxQty * half;
          el("rect", { x: mid + labelW / 2 + 4, y: y + 2, width: w, height: rowH - 4, fill: "var(--ask)" }, g);
          el("text", { x: mid + labelW / 2 + 8 + w, y: y + 14, class: "muted" }, g, `${ask[1]} (${ask[2]})`);
        }
        if (fillPrices.has(price)) {
          el("rect", { x: 2, y: y + 1, width: width - 4, height: rowH - 2, fill: "none", stroke: "var(--outline)", "stroke-width": 1.5, "stroke-dasharray": "4 3", class: "traded" }, g);
        }
      });
      el("text", { x: mid, y: height - 8, "text-anchor": "middle", class: "muted" }, svg, "quantity (order count) per price level, price in ticks");
      target.replaceChildren(svg);
    }

    function go(delta) { current = Math.min(steps.length - 1, Math.max(0, current + delta)); draw(); }
    document.getElementById("prev").addEventListener("click", () => go(-1));
    document.getElementById("next").addEventListener("click", () => go(1));
    scrubber.addEventListener("input", () => { current = Number(scrubber.value); draw(); });
    document.addEventListener("keydown", (event) => {
      if (event.target === scrubber) return;
      if (event.key === "ArrowRight") { go(1); event.preventDefault(); }
      if (event.key === "ArrowLeft") { go(-1); event.preventDefault(); }
    });
    draw();
    return draw;
  }

  drawLatency();
  drawThroughput();
  const redrawReplay = setupReplay();
  window.addEventListener("resize", () => {
    drawLatency();
    drawThroughput();
    if (redrawReplay) redrawReplay();
  });
})();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bench", default="results/bench.json")
    parser.add_argument("--replay", default="results/replay.jsonl")
    parser.add_argument("--out", default="docs/index.html")
    parser.add_argument("--card", help="also write the portfolio card JSON to this path")
    parser.add_argument("--card-book", default=DEFAULT_BOOK, choices=BOOKS)
    parser.add_argument("--card-command", default="scripts/run_bench.sh",
                        help="the command recorded in card.json")
    parser.add_argument("--readme", help="rewrite the results table between the README markers")
    args = parser.parse_args()

    bench, bench_error = load_bench(args.bench)
    steps, replay_error = load_replay(args.replay)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(build_page(bench, bench_error, steps, replay_error))

    if args.card or args.readme:
        if not bench:
            sys.exit(f"cannot write card or README table: {bench_error}")
    if args.card:
        os.makedirs(os.path.dirname(args.card) or ".", exist_ok=True)
        with open(args.card, "w", encoding="utf-8") as handle:
            json.dump(build_card(bench, args.card_book, args.card_command), handle, indent=2)
            handle.write("\n")
    if args.readme:
        update_readme(args.readme, bench)


if __name__ == "__main__":
    main()
