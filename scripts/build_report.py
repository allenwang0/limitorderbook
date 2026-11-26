"""Build docs/index.html (and optionally site/card.json and the README results
table) from lob_bench JSON output and lob_replay JSONL output.

Usage:
  python3 scripts/build_report.py [--bench results/bench.json]
      [--replay results/replay.jsonl] [--out docs/index.html]
      [--card site/card.json] [--readme README.md]

Python standard library only. Every number on the page comes from the inputs,
or from constants in the benchmark and test sources (warm-up length, seeds).
The page loads report-kit from docs/kit/ and its own docs/report.local.css
and docs/report.local.js, so build it into docs/.
"""

import argparse
import datetime
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_URL = "https://github.com/allenwang0/limitorderbook"
PAGES_URL = "https://allenwang0.github.io/limitorderbook/"
BOOKS = ["MapBook", "FlatBook"]
OPS = [
    ("add_resting", "Add, resting"),
    ("add_crossing", "Add, crossing"),
    ("cancel", "Cancel"),
    ("market", "Market"),
]
OP_PLURALS = {
    "add_resting": "resting adds",
    "add_crossing": "crossing adds",
    "cancel": "cancels",
    "market": "market orders",
}
PERCENTILES = ["p50", "p90", "p99", "p99.9", "max"]
DEFAULT_BOOK = "MapBook"
NO_BENCH = "No benchmark data: run scripts/build_report.py after cmake --build"
NO_REPLAY = "No replay data: run scripts/build_report.py after cmake --build"
README_START = "<!-- results:start -->"
README_END = "<!-- results:end -->"
# The replay opens on the first step whose book has at least this many price
# levels, so the first frame already shows two populated sides.
REPLAY_START_LEVELS = 6


def label_compiler(version):
    """GCC's __VERSION__ is a bare number such as "14.2.0"; Clang's names itself."""
    return f"GCC {version}" if re.match(r"\d", version) else version


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
    repetitions = 1
    for bench in raw.get("benchmarks", []):
        name = bench.get("name", "")
        repetitions = max(repetitions, int(bench.get("repetitions", 1)))
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
            "compiler": label_compiler(context.get("compiler", "unknown")),
            "cxx_flags": context.get("cxx_flags", "unknown"),
            "git_sha": context.get("git_sha", "unknown"),
            "date": context.get("date", "unknown"),
            "message_mix": context.get("message_mix", "unknown"),
            "seed": context.get("seed", "unknown"),
            "build_type": context.get("library_build_type", "unknown"),
            "library_version": context.get("library_version"),
            "num_cpus": context.get("num_cpus"),
            "load_avg": context.get("load_avg"),
        },
        "clock": clock,
        "latency": latency,
        "throughput": throughput,
        "repetitions": repetitions,
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


def source_number(relpath, pattern):
    """First number captured by pattern in a repo source file, or None.

    Used for constants that the benchmark and tests define but do not write to
    their output (warm-up length, differential-test seeds), so the page quotes
    the code instead of a hand-typed copy.
    """
    try:
        with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
            match = re.search(pattern, handle.read())
    except OSError:
        return None
    return float(match.group(1).replace("'", "")) if match else None


def source_facts():
    seeds = source_number("tests/differential_test.cpp", r'envOr\("LOB_DIFF_SEEDS",\s*([\d\']+)\)')
    ops = source_number("tests/differential_test.cpp", r'envOr\("LOB_DIFF_OPS",\s*([\d\']+)\)')
    try:
        with open(os.path.join(ROOT, "CMakeLists.txt"), encoding="utf-8") as handle:
            sanitizers = "-fsanitize=address,undefined" in handle.read()
    except OSError:
        sanitizers = False
    return {
        "diff_seeds": int(seeds) if seeds else None,
        "diff_ops": int(ops) if ops else None,
        "sanitizers": sanitizers,
        "warmup": source_number("bench/lob_bench.cpp", r"kWarmupMessages\s*=\s*([\d']+)"),
        "crossing_share": source_number("bench/lob_bench.cpp", r"kCrossingAddShare\s*=\s*([\d.]+)"),
    }


def esc(text):
    return html.escape(str(text), quote=True)


def join_names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def tick_words(count):
    words = {1: "one", 2: "two", 3: "three"}
    return f"{words.get(count, count)} tick{'s' if count != 1 else ''}"


def throughput_summary(bench):
    rates = bench["throughput"]
    if not all(rates.get(book) for book in BOOKS):
        return None
    return {
        "flat": rates["FlatBook"],
        "map": rates["MapBook"],
        "ratio": rates["FlatBook"] / rates["MapBook"],
    }


def takeaway_latency(bench):
    """Headline and sentence for the p99 latency comparison.

    Latencies are quantized to the timer's tick. A difference under two ticks
    can come from one sample landing on the other side of a tick boundary, so
    it is reported as within the timer's resolution, not as a speedup.
    """
    latency = bench["latency"]
    if not all(op in latency.get(book, {}) for book in BOOKS for op, _ in OPS):
        return None
    tick = bench["clock"].get("tick_ns")
    deltas = {op: latency["MapBook"][op]["p99"] - latency["FlatBook"][op]["p99"] for op, _ in OPS}
    p99 = {book: [latency[book][op]["p99"] for op, _ in OPS] for book in BOOKS}
    foot = (
        f"p99 FlatBook {min(p99['FlatBook']):.0f}–{max(p99['FlatBook']):.0f} ns · "
        f"MapBook {min(p99['MapBook']):.0f}–{max(p99['MapBook']):.0f} ns · {len(OPS)} operation types"
    )
    if tick and all(abs(delta) < 2 * tick for delta in deltas.values()):
        groups = {}
        for op, _ in OPS:
            groups.setdefault(round(deltas[op] / tick), []).append(OP_PLURALS[op])
        clauses = []
        for ticks in sorted(groups, reverse=True):
            names = join_names(groups[ticks])
            if ticks == 0:
                clauses.append(f"equal on {names}")
            else:
                clauses.append(f"{tick_words(abs(ticks))} {'lower' if ticks > 0 else 'higher'} on {names}")
        most = max(abs(t) for t in groups)
        return {
            "resolved": False,
            "headline": "Per-message latency sits at the timer's resolution, so it can't separate the books",
            "text": (
                f"FlatBook's p99 is {', and '.join(clauses)}. "
                f"Every difference is under two {tick:.0f} ns clock ticks, within the timer's resolution."
            ),
            "metric": "Equal at p99" if most == 0 else f"Within {tick_words(most)}",
            "short": "The books' p99s are equal on every operation." if most == 0 else
            f"The books' p99s differ by at most {tick_words(most)} ({most * tick:.0f} ns) per operation.",
            "foot": foot,
        }
    lower = [op for op, _ in OPS if deltas[op] > 0]
    pairs = ", ".join(
        f"{OP_PLURALS[op]} {latency['FlatBook'][op]['p99']:.0f} vs {latency['MapBook'][op]['p99']:.0f} ns"
        for op, _ in OPS
    )
    tick_note = f" Clock tick {tick:.0f} ns." if tick else ""
    if lower:
        headline = f"FlatBook's p99 latency is lower on {len(lower)} of {len(OPS)} operation types"
    else:
        headline = "FlatBook's p99 latency is not lower than MapBook's on any operation type"
    return {
        "resolved": True,
        "headline": headline,
        "text": f"p99, FlatBook vs MapBook: {pairs}.{tick_note}",
        "metric": f"{len(lower)} of {len(OPS)} lower",
        "short": f"FlatBook's p99 is lower on {len(lower)} of {len(OPS)} operation types (single run).",
        "foot": foot,
    }


def grid_step(bench):
    """Spacing of the latency gridlines: the step the recorded values sit on.

    lob_bench reports the tick as a whole number of ns, but the recorded
    percentiles are multiples of a slightly different step (41.67 ns on Apple
    silicon shows up as 42, 83, 125 ...). The largest recorded multiple gives
    the finest estimate of that step, so the gridlines land on the values.
    """
    tick = bench["clock"].get("tick_ns")
    if not tick:
        return None
    best = (0, tick)
    for book in bench["latency"].values():
        for stats in book.values():
            for p in ("p50", "p90", "p99", "p99.9"):
                k = round(stats[p] / tick)
                if k > best[0]:
                    best = (k, stats[p] / k)
    return best[1]


def replay_start(steps):
    for index, step in enumerate(steps):
        if len(step["bids"]) + len(step["asks"]) >= REPLAY_START_LEVELS:
            return index
    return 0


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
    rate = bench["throughput"].get(book)
    tick = bench["clock"].get("tick_ns")
    takeaway = f"{book} p99: add {add['high']:.0f}, cancel {cancel['high']:.0f}, match {match['high']:.0f} ns"
    if rate:
        takeaway += f"; {rate / 1e6:.1f}M msgs/s ({1e9 / rate:.0f} ns mean)"
    if tick:
        takeaway += f"; {tick:.0f} ns clock tick"
    takeaway += "."
    # A log axis cannot show 0; sub-tick medians are reported as 0 ns.
    scale = "log" if all(row["low"] > 0 for row in rows) else "linear"
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
            "x_scale": scale,
            "rows": rows,
        },
    }


def readme_table(bench):
    context = bench["context"]
    lines = [
        "| Book | Messages/s |",
        "| :--- | ---: |",
    ]
    for book in BOOKS:
        rate = bench["throughput"].get(book)
        if rate:
            lines.append(f"| {book} | {rate / 1e6:.2f}M |")
    lines.append("")
    runs = "single run" if bench["repetitions"] == 1 else f"{bench['repetitions']} runs"
    lines.append(
        f"One thread on {context['cpu']}, {runs}, seeded synthetic flow ({context['message_mix']}). "
        f"Latency percentiles and a replay are in the [report]({PAGES_URL})."
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


# ---------------------------------------------------------------- page parts


def empty_note(message):
    return f'<p class="caveat" role="note"><b>Missing data.</b> {esc(message)}</p>'


def hero_html(bench, bench_error):
    lede = (
        "A limit order book pairs buy and sell orders by price, then by arrival time. "
        "Two C++17 versions run the same seeded order flow: <b>MapBook</b> keeps price levels in a "
        '<span class="mono">std::map</span>; <b>FlatBook</b> keeps levels near the market in a flat array '
        "indexed by price."
    )
    summary = throughput_summary(bench) if bench else None
    if not summary:
        return (
            '<section class="hero wrap" aria-labelledby="h1"><p class="eyebrow">Benchmark · C++17</p>'
            '<h1 id="h1">Limit order book benchmark</h1>'
            f'<p class="lede">{lede}</p><div id="throughput">{empty_note(bench_error or NO_BENCH)}</div></section>'
        )
    context = bench["context"]
    runs = "Single run" if bench["repetitions"] == 1 else f"{bench['repetitions']} runs"
    measured = sum(s["count"] for s in bench["latency"].get("FlatBook", {}).values())
    conditions = "".join(
        f'<span class="{cls}">{esc(text)}</span>'
        for cls, text in (
            ("key", "Synthetic order flow"),
            ("key", runs),
            ("", "One thread"),
            ("", context["cpu"]),
        )
    )
    rows = ""
    top = max(summary["flat"], summary["map"])
    for book, rate, note, color in (
        ("FlatBook", summary["flat"], "flat array", "var(--series-1)"),
        ("MapBook", summary["map"], "std::map", "var(--series-2)"),
    ):
        rows += (
            f'<span class="who">{book}<small>{note}</small></span>'
            f'<span class="track"><span class="b" style="width:{rate / top * 70:.1f}%;background:{color}"></span>'
            f'<span class="v num">{rate / 1e6:.2f} M msgs/s</span></span>'
        )
    label = (
        f"Throughput: FlatBook {summary['flat'] / 1e6:.2f} million messages per second, "
        f"MapBook {summary['map'] / 1e6:.2f} million."
    )
    return f"""<section class="hero wrap" aria-labelledby="h1">
  <p class="eyebrow">Benchmark · C++17</p>
  <h1 id="h1">An order book that matches {summary['flat'] / 1e6:.0f} million messages a second</h1>
  <p class="lede">{lede}</p>
  <p class="conditions">{conditions}</p>
  <div id="throughput">
    <div class="ratio"><span class="fig num">{summary['ratio']:.2f}×</span><span class="cap">FlatBook's throughput relative to MapBook on the same {measured:,} messages.</span></div>
    <div class="paired" role="img" aria-label="{esc(label)}">{rows}</div>
  </div>
</section>"""


def bento_html(bench, facts):
    if not bench:
        return ""
    cards = []
    lat = takeaway_latency(bench)
    if lat:
        cards.append(
            f'<a class="card span-8" href="#latency"><span class="label">Per-message latency, p99</span>'
            f'<span class="metric">{esc(lat["metric"])}</span><span class="cmp">{esc(lat["short"])}</span>'
            f'<span class="foot">{esc(lat["foot"])}</span></a>'
        )
    if facts["diff_seeds"] and facts["diff_ops"]:
        sanit = " The same test also runs under ASan and UBSan." if facts["sanitizers"] else ""
        cards.append(
            f'<div class="card span-4 t-full"><span class="label">Correctness</span>'
            f'<span class="metric num">{facts["diff_seeds"]} <span class="unit">seeds</span></span>'
            f'<span class="cmp">× {facts["diff_ops"]:,} ops each. Both books are checked op by op against a '
            f"naive reference book.{sanit}</span>"
            f'<span class="foot"><a href="{REPO_URL}/blob/main/tests/differential_test.cpp">'
            "tests/differential_test.cpp</a></span></div>"
        )
    context = bench["context"]
    measured = sum(s["count"] for s in bench["latency"].get("FlatBook", {}).values())
    warm = f", after {facts['warmup']:,.0f} warm-up messages" if facts["warmup"] else ""
    cards.append(
        f'<div class="card span-6"><span class="label">Workload</span>'
        f'<span class="metric num">{measured / 1e6:g} M <span class="unit">messages</span></span>'
        f'<span class="cmp">{esc(context["message_mix"])}{warm}.</span>'
        f'<span class="foot">Seed {esc(context["seed"])} · synthetic, not market data</span></div>'
    )
    runs = "1 (no spread available)" if bench["repetitions"] == 1 else str(bench["repetitions"])
    cards.append(
        f'<div class="card span-6"><span class="label">Conditions</span><dl class="kv">'
        f'<dt>CPU</dt><dd>{esc(context["cpu"])}</dd>'
        f'<dt>Compiler</dt><dd>{esc(context["compiler"])}</dd>'
        f'<dt>Flags</dt><dd class="mono">{esc(context["cxx_flags"])}</dd>'
        f"<dt>Runs</dt><dd>{runs}</dd></dl>"
        f'<span class="foot"><a href="#reproduce">Full environment →</a></span></div>'
    )
    return f'<section class="wrap" aria-label="Key results"><div class="bento">{"".join(cards)}</div></section>'


ORDER_TYPES = [
    ("LIMIT", "Limit", "Trades what it can at its price or better, then rests the remainder."),
    ("IOC", "IOC", "Immediate or cancel: trades what it can up to its price, cancels the rest."),
    ("FOK", "FOK", "Fill or kill: trades its full size at once or does nothing."),
    ("POST", "Post-only", "Rests only if it wouldn't trade on arrival."),
    ("MARKET", "Market", "Ignores its price and takes the best prices available."),
]


def replay_html(steps, replay_error):
    if not steps:
        body = empty_note(replay_error or NO_REPLAY)
        lede = ""
    else:
        types = "".join(
            f'<li data-type="{key}"><b>{name}</b><span>{text}</span></li>' for key, name, text in ORDER_TYPES
        )
        lede = (
            f'<p class="lede">Step through {len(steps)} orders. Buyers wait on the left, sellers on the right; '
            "a trade happens when an order reaches the other side.</p>"
        )
        body = f"""<ul class="otypes" aria-label="Order types">{types}</ul>
    <p class="otypes-note">Cancel removes a resting order. Modify keeps the order's place in the queue if it only shrinks; a price change or a larger size sends it to the back.</p>
    <div class="demo card" id="demo" role="region" aria-label="Order book replay demo" tabindex="0">
      <div class="demo-grid">
        <div class="demo-state">
          <div id="ladder"><noscript><p class="caveat">The replay needs JavaScript. The benchmark results above don't.</p></noscript></div>
        </div>
        <div class="demo-side">
          <div aria-live="polite" aria-atomic="true" id="demo-live">
            <p class="eyebrow">Operation</p><p class="op-text" id="op-text"></p>
            <p class="eyebrow">Result</p><p class="res-text" id="res-text"></p>
          </div>
          <p class="eyebrow">Last 5 operations</p><ol class="history" id="history"></ol>
        </div>
      </div>
      <div class="controls" id="replay-controls">
        <button type="button" class="ctl" id="reset" aria-label="Reset to first step">⟲<span class="t"> Reset</span></button>
        <button type="button" class="ctl" id="prev" aria-label="Step back">◀<span class="t"> Back</span></button>
        <button type="button" class="ctl primary" id="play" aria-pressed="false">Play</button>
        <button type="button" class="ctl" id="next" aria-label="Step forward"><span class="t">Step </span>▶</button>
        <button type="button" class="ctl" id="next-trade">Next trade</button>
        <input type="range" id="scrubber" min="0" max="{len(steps) - 1}" value="{replay_start(steps)}" aria-label="Replay step">
        <span class="count num" id="step-count"></span>
        <div class="seg" role="radiogroup" aria-label="Speed" id="speed">
          <button role="radio" aria-checked="false" data-value="1400">0.5×</button><button role="radio" aria-checked="true" data-value="700">1×</button><button role="radio" aria-checked="false" data-value="350">2×</button>
        </div>
      </div>
      <p class="hint">Keys when the demo has focus: Space plays or pauses, ← → step, Home resets.</p>
    </div>
    <p class="caveat" role="note"><b>Replayed through MapBook.</b> The order script is synthetic (examples/demo_flow.txt). The differential test checks that FlatBook produces the same fills on generated flows. Dashed rows traded in the current step.</p>"""
    return f"""<section class="s wrap" aria-labelledby="replay">
    <p class="eyebrow">How a book matches</p>
    <h2 id="replay">An order trades when it reaches the best price on the other side</h2>
    {lede}
    {body}
</section>"""


MECHANISM = """<section class="s wrap" id="mechanism" aria-labelledby="h-mech">
    <p class="eyebrow">Where the difference comes from</p>
    <h2 id="h-mech">FlatBook finds most price levels by index instead of walking a tree</h2>
    <p class="lede">Most messages touch prices near the best bid and ask. FlatBook keeps those levels in a contiguous array, pools its orders so resting orders don't allocate after warm-up, and caches the best price on each side.</p>
    <div class="mech">
      <div class="card">
        <span class="label"><span class="swatch" style="background:var(--series-2)"></span>&nbsp; MapBook</span>
        <h3>Red-black tree of levels</h3>
        <p>Finding a level follows about log₂(n) pointers, and each one can miss the cache. Each level's orders sit in a linked list.</p>
        <svg viewBox="0 0 300 120" role="img" aria-label="Schematic: a binary tree of price levels, three levels deep.">
          <g stroke="var(--axis)" stroke-width="1.5" fill="none"><path d="M150 22 L80 62 M150 22 L220 62 M80 62 L45 100 M80 62 L115 100 M220 62 L185 100 M220 62 L255 100"/></g>
          <g fill="var(--surface-2)" stroke="var(--series-2)" stroke-width="2">
            <rect x="122" y="8" width="56" height="28" rx="6"/><rect x="52" y="48" width="56" height="28" rx="6"/><rect x="192" y="48" width="56" height="28" rx="6"/>
            <rect x="17" y="86" width="56" height="28" rx="6"/><rect x="87" y="86" width="56" height="28" rx="6"/><rect x="157" y="86" width="56" height="28" rx="6"/><rect x="227" y="86" width="56" height="28" rx="6"/></g>
          <g text-anchor="middle"><text x="150" y="27">1004</text><text x="80" y="67">1002</text><text x="220" y="67">1006</text><text x="45" y="105">1001</text><text x="115" y="105">1003</text><text x="185" y="105">1005</text><text x="255" y="105">1007</text></g>
        </svg>
      </div>
      <div class="card">
        <span class="label"><span class="swatch" style="background:var(--series-1)"></span>&nbsp; FlatBook</span>
        <h3>Array indexed by price tick</h3>
        <p>The level for price <span class="mono">p</span> is slot <span class="mono">p − base</span>, and neighbouring levels share cache lines. Prices outside the window fall back to a <span class="mono">std::map</span>.</p>
        <svg viewBox="0 0 300 120" role="img" aria-label="Schematic: four contiguous array slots for prices 1001 to 1004.">
          <g fill="var(--surface-2)" stroke="var(--series-1)" stroke-width="2">
            <rect x="6" y="42" width="68" height="34" rx="4"/><rect x="80" y="42" width="68" height="34" rx="4"/><rect x="154" y="42" width="68" height="34" rx="4"/><rect x="228" y="42" width="68" height="34" rx="4"/></g>
          <g text-anchor="middle"><text x="40" y="64">1001</text><text x="114" y="64">1002</text><text x="188" y="64">1003</text><text x="262" y="64">1004</text>
          <text x="151" y="26">slot = price − 1001</text><text x="151" y="104">one contiguous block</text></g>
        </svg>
      </div>
    </div>
    <p class="caveat" role="note"><b>Design, not a measurement.</b> The benchmark times whole books, so it doesn't show how much each of the three differences contributes.</p>
</section>"""


def latency_html(bench, bench_error):
    lat = takeaway_latency(bench) if bench else None
    if not lat:
        return f"""<section class="s wrap" aria-labelledby="latency">
    <p class="eyebrow">Per-message latency</p>
    <h2 id="latency">Per-message latency</h2>
    {empty_note(bench_error or NO_BENCH)}
</section>"""
    tick = bench["clock"].get("tick_ns")
    measured = sum(s["count"] for s in bench["latency"]["FlatBook"].values())
    runs = "Single run" if bench["repetitions"] == 1 else f"{bench['repetitions']} runs"
    sub = f"{esc(bench['context']['cpu'])} · {measured:,} messages per book · {runs.lower()}"
    caveat = ""
    zero = any(s["p50"] == 0 for book in bench["latency"].values() for s in book.values())
    if zero and tick:
        caveat = (
            '<p class="caveat" role="note"><b>A value of 0 ns</b> means the message finished before the clock '
            "advanced, not that it took no time. Anything under one tick reads as 0 or one tick.</p>"
        )
    grid_note = "Vertical gridlines fall on clock ticks. " if tick else ""
    return f"""<section class="s wrap" aria-labelledby="latency">
    <p class="eyebrow">Per-message latency</p>
    <h2 id="latency">{esc(lat['headline'])}</h2>
    <p class="lede">{esc(lat['text'])}</p>
    <figure class="figure" aria-labelledby="fig-lat-title">
      <div class="fig-head">
        <div><h3 id="fig-lat-title">Latency percentiles by operation</h3><p class="sub">{sub}</p></div>
        <div class="seg" role="radiogroup" aria-label="View" data-view-toggle><button role="radio" aria-checked="true" data-value="chart">Chart</button><button role="radio" aria-checked="false" data-value="table">Table</button></div>
      </div>
      <div class="legend glyphs" aria-hidden="true">
        <span><svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="var(--ink-3)"/></svg>p50</span>
        <span><svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="none" stroke="var(--ink-3)" stroke-width="2"/></svg>p99</span>
        <span><svg width="22" height="10"><line x1="1" y1="5" x2="21" y2="5" stroke="var(--ink-3)" stroke-width="2"/></svg>p50 to p99.9</span>
      </div>
      <div id="lat-plot"></div>
      <div id="lat-table" hidden></div>
      <figcaption>Each row is one operation type, one lane per book. The line runs from p50 to p99.9, the filled dot marks p50 and the ring marks p99. {grid_note}{runs} of {measured:,} messages per book, so no run-to-run spread. The table adds p90, max and sample counts.</figcaption>
    </figure>
    {caveat}
</section>"""


def method_html(bench, facts):
    if not bench:
        return ""
    context = bench["context"]
    summary = throughput_summary(bench)
    measured = sum(s["count"] for s in bench["latency"].get("FlatBook", {}).values())
    warm = facts["warmup"] or 0
    tick = bench["clock"].get("tick_ns")
    step = grid_step(bench)
    para = [
        f"The benchmark generates one seeded stream of {measured + warm:,.0f} messages (seed {esc(context['seed'])}): "
        f"{esc(context['message_mix'])}."
        + (f" {facts['crossing_share'] * 100:.0f}% of adds are priced through the best price, so they trade on arrival."
           if facts["crossing_share"] else "")
        + (f" The first {warm:,.0f} messages warm the book and are not measured." if warm else ""),
        "Throughput is the measured messages divided by the time to process them, with warm-up excluded from "
        "timing and averaged over Google Benchmark's repeated passes (items per second). Latency is one <span class=\"mono\">steady_clock</span> sample around each message, grouped by "
        "operation type afterwards: an add that produced fills counts as crossing.",
    ]
    load = context.get("load_avg")
    noise = ""
    if load and context.get("num_cpus"):
        noise = (
            f" The machine's load average was {min(load):.0f}–{max(load):.0f} on {context['num_cpus']} CPUs "
            "during the run, so other work may have added noise."
        )
    runs = "Each book ran once, so there is no run-to-run spread." if bench["repetitions"] == 1 else (
        f"Each book ran {bench['repetitions']} times.")
    para.append(runs + noise)
    defs = []
    if tick:
        tick_def = f"lob_bench estimates one tick at {tick:.0f} ns"
        if step and abs(step - tick) >= 0.5:
            tick_def += f"; the recorded values are multiples of about {step:.1f} ns"
        defs.append(("Timer resolution", tick_def + "."))
    defs += [
        ("p50, p99, p99.9", "The latency that 50%, 99% or 99.9% of messages beat."),
        ("Resting / crossing add", "An add that rests in the book, or one that trades on arrival."),
    ]
    dl = "".join(f"<dt>{name}</dt><dd>{text}</dd>" for name, text in defs)
    shows = []
    if summary:
        shows.append(f"FlatBook sustains {summary['ratio']:.2f}× MapBook's throughput on this flow, on one thread.")
    if facts["diff_seeds"]:
        shows.append("Both books produce the same fills as a naive reference, checked op by op.")
    lat = takeaway_latency(bench)
    if lat and not lat["resolved"]:
        shows.append("Per-message p99 latency is equal within two clock ticks.")
    doesnt = [
        "Performance on real market data: the flow is synthetic.",
        "Run-to-run spread: each book was measured once." if bench["repetitions"] == 1 else
        "Variation across machines: all runs used one machine.",
        "Latency differences smaller than two clock ticks.",
        "Multiple instruments, threads, networking or a market data feed.",
    ]
    li = lambda items: "".join(f"<li>{t}</li>" for t in items)
    return f"""<section class="s wrap" id="method" aria-labelledby="h-method">
    <p class="eyebrow">Methodology</p>
    <h2 id="h-method">How this was measured</h2>
    <div class="prose method">{''.join(f'<p>{p}</p>' for p in para)}<dl class="defs">{dl}</dl></div>
    <div class="two">
      <div><h3>What this shows</h3><ul>{li(shows)}</ul></div>
      <div><h3>What it doesn't show</h3><ul>{li(doesnt)}</ul></div>
    </div>
</section>"""


def reproduce_html(bench):
    rows = []
    if bench:
        context = bench["context"]
        tick = bench["clock"].get("tick_ns")
        load = context.get("load_avg")
        rows = [
            ("CPU", esc(context["cpu"]) + (f" ({context['num_cpus']} cores)" if context.get("num_cpus") else "")),
            ("Compiler", esc(context["compiler"])),
            ("Flags", f'<span class="mono">{esc(context["cxx_flags"])}</span>'),
            ("Google Benchmark", esc(context.get("library_version") or "unknown")),
            ("Commit recorded by the run", f'<span class="mono">{esc(context["git_sha"][:7])}</span>'),
            ("Run date", esc(context["date"][:10])),
            ("Clock tick", f"{tick:.0f} ns" if tick else "not recorded"),
            ("Load average", ", ".join(f"{v:.1f}" for v in load) if load else "not recorded"),
            ("Repetitions", str(bench["repetitions"])),
            ("Seed", esc(context["seed"])),
            ("Data file", '<span class="mono">results/bench.json</span>'),
        ]
    table = ""
    if rows:
        body = "".join(f'<tr><th scope="row">{k}</th><td>{v}</td></tr>' for k, v in rows)
        table = f"""<details>
      <summary>Full environment · {len(rows)} rows</summary>
      <div class="tablewrap" tabindex="0" role="region" aria-label="Environment table">
        <table><thead><tr><th scope="col">Field</th><th scope="col">Value</th></tr></thead><tbody>{body}</tbody></table>
      </div>
    </details>"""
    return f"""<section class="s wrap" id="reproduce" aria-labelledby="h-repro">
    <p class="eyebrow">Reproduce</p>
    <h2 id="h-repro">Run it yourself</h2>
    <div class="codewrap"><pre><code>git clone {REPO_URL}.git
cd limitorderbook
cmake --workflow --preset default    # Release build + tests
./build/lob_bench                    # latency and throughput benchmark
./build/lob_replay examples/demo_flow.txt
scripts/run_bench.sh                 # rerun everything and rebuild this page</code></pre></div>
    <p class="small-note">Needs CMake 3.25+, Ninja and a C++17 compiler. This page is built from the committed <span class="mono">results/bench.json</span>; CI reruns a shorter benchmark on every push but keeps it as a build artifact only.</p>
    {table}
</section>"""


def build_page(bench, bench_error, steps, replay_error):
    facts = source_facts()
    payload = {
        "ops": [{"key": key, "label": label} for key, label in OPS],
        "latency": bench["latency"] if bench else None,
        "gridStep": grid_step(bench) if bench else None,
        "tick": bench["clock"].get("tick_ns") if bench else None,
        "replay": steps,
        "replayStart": replay_start(steps) if steps else 0,
    }
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    footer = "No benchmark run recorded."
    description = "A C++17 limit order book benchmarked against a std::map design on a seeded synthetic order flow."
    if bench:
        context = bench["context"]
        footer = (
            f'Data from the run at commit '
            f'<span class="mono">{esc(context["git_sha"][:7])}</span> · {esc(context["date"][:10])}'
        )
        summary = throughput_summary(bench)
        if summary:
            description = (
                f"A C++17 limit order book whose flat-array design processes {summary['flat'] / 1e6:.2f} million "
                f"messages a second, {summary['ratio']:.2f}× a std::map design on the same seeded flow."
            )
    parts = [
        hero_html(bench, bench_error),
        bento_html(bench, facts),
        replay_html(steps, replay_error),
        MECHANISM if bench else "",
        latency_html(bench, bench_error),
        method_html(bench, facts),
        reproduce_html(bench),
    ]
    return (
        TEMPLATE.replace("{{DESCRIPTION}}", esc(description))
        .replace("{{MAIN}}", "\n".join(p for p in parts if p))
        .replace("{{FOOTER}}", footer)
        .replace("{{DATA}}", data)
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Limit order book benchmark · Allen Wang</title>
<meta name="description" content="{{DESCRIPTION}}">
<link rel="icon" href="data:,">
<meta name="theme-color" content="#f5f5f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0b0b0b" media="(prefers-color-scheme: dark)">
<script>try{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}</script>
<link rel="stylesheet" href="kit/report.css">
<link rel="stylesheet" href="report.local.css">
<script src="kit/report.js" defer></script>
<script src="kit/charts.js" defer></script>
<script src="report.local.js" defer></script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="bar">
  <div class="wrap">
    <a class="back" href="https://allenwang0.github.io/">← Allen Wang</a>
    <span class="title" aria-hidden="true">Limit order book benchmark</span>
    <a class="pill" href="https://github.com/allenwang0/limitorderbook">Code ↗</a>
  </div>
</header>
<main id="main">
{{MAIN}}
</main>
<footer>
  <div class="wrap">
    <span><a href="https://allenwang0.github.io/">← Allen Wang</a><a href="https://github.com/allenwang0/limitorderbook">Code ↗</a></span>
    <span>{{FOOTER}}</span>
  </div>
</footer>
<script type="application/json" id="report-data">{{DATA}}</script>
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
