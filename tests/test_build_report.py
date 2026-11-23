"""Checks that the published report, README block and portfolio card say what
the committed benchmark results say, since readers take those numbers as-is.

The expected values are recomputed here straight from the JSON, independently
of build_report.py's own helpers, so a generator bug that shifts a number (wrong
aggregate row, rounding before dividing, one repetition instead of the median)
fails a test instead of reaching the page.

Run: python3 -m unittest discover -s tests -p 'test_*.py'  (needs jsonschema)
"""

import html
import json
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile
import unittest

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
BENCH = ROOT / "results" / "bench.json"
REPLAY = ROOT / "results" / "replay.jsonl"


def medians(bench_path):
    """Median items_per_second per book over repetition rows only."""
    runs = {}
    for row in json.loads(pathlib.Path(bench_path).read_text())["benchmarks"]:
        match = re.match(r"BM_Throughput<(\w+)>", row["name"])
        if match and row.get("run_type", "iteration") == "iteration":
            runs.setdefault(match.group(1), []).append(row["items_per_second"])
    return {book: statistics.median(values) for book, values in runs.items()}, {b: len(v) for b, v in runs.items()}


def text_of(page):
    """Visible text with tags stripped and entities decoded, for phrase checks."""
    return html.unescape(re.sub(r"<[^>]+>", "", page))


class BuildReportTest(unittest.TestCase):
    def run_script(self, *extra):
        tmp = pathlib.Path(tempfile.mkdtemp())
        out = tmp / "index.html"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_report.py"), "--out", str(out), *extra],
            check=True,
            cwd=ROOT,
        )
        return tmp, out

    def test_hero_ratio_and_rates_match_committed_bench(self):
        # The hero is the one number a 5-second reader keeps; it must be the
        # ratio of the committed medians, not of one run or a rounded value.
        _, out = self.run_script("--bench", str(BENCH), "--replay", str(REPLAY))
        page = out.read_text()
        rates, counts = medians(BENCH)
        ratio = rates["FlatBook"] / rates["MapBook"]
        hero = re.search(r'<p class="hero-metric"><span class="value">([^<]+)</span>', page)
        self.assertIsNotNone(hero, "hero metric missing")
        self.assertEqual(hero.group(1), f"{ratio:.2f}×")
        self.assertGreater(ratio, 1, "owner rule: the hero never states a zero or negative result")
        text = text_of(page)
        n = counts["FlatBook"]
        self.assertGreaterEqual(n, 10, "the report promises at least 10 repetitions")
        self.assertIn(f"{rates['FlatBook'] / 1e6:.1f} M vs {rates['MapBook'] / 1e6:.1f} M messages per second, "
                      f"median of {n} runs", text)
        self.assertIn(f"FlatBook processes {ratio:.2f}× as many messages per second", text)
        self.assertIn(f"{1e9 / rates['FlatBook']:.0f} ns", text, "time per message derived from the median")

    def test_committed_page_and_readme_are_current(self):
        # Pages publishes docs/ as committed; a stale page would show numbers
        # that are no longer in results/bench.json.
        _, out = self.run_script("--bench", str(BENCH), "--replay", str(REPLAY))
        self.assertEqual(out.read_text(), (ROOT / "docs" / "index.html").read_text(),
                         "docs/index.html is stale: run python3 scripts/build_report.py --card site/card.json "
                         "--readme README.md and commit")
        readme = (ROOT / "README.md").read_text()
        rates, _ = medians(BENCH)
        ratio = rates["FlatBook"] / rates["MapBook"]
        block = readme.split("<!-- results:start -->")[1].split("<!-- results:end -->")[0]
        self.assertIn(f"| FlatBook | {rates['FlatBook'] / 1e6:.1f}M |", block)
        self.assertIn(f"| MapBook | {rates['MapBook'] / 1e6:.1f}M |", block)
        self.assertIn(f"**{ratio:.2f}×**", block)
        self.assertNotIn("58.95", readme, "old single-run number left in the README")

    def test_unsupported_claims_stay_out(self):
        # No allocation counter exists and no exchange rulebook is cited, so
        # neither claim may appear on the page or in the README.
        _, out = self.run_script("--bench", str(BENCH), "--replay", str(REPLAY))
        for name, text in (("page", text_of(out.read_text())), ("README", (ROOT / "README.md").read_text())):
            for phrase in ("CME", "Nasdaq", "No allocation for", "no heap allocation", "allocation after warm-up"):
                self.assertNotIn(phrase, text, f"{name} contains unsupported claim {phrase!r}")

    def test_primer_example_is_real_and_shows_time_priority(self):
        # The primer quotes replay output; the order filled first must be the
        # one that arrived first at that price.
        _, out = self.run_script("--bench", str(BENCH), "--replay", str(REPLAY))
        text = text_of(out.read_text())
        steps = [json.loads(line) for line in REPLAY.read_text().splitlines() if line.strip()]
        quoted = [s for s in steps if f"line {s['line']}  {s['cmd']}" in text]
        self.assertEqual(len(quoted), 3, "primer should quote two resting orders and the crossing order")
        fills = [e for e in quoted[2]["events"] if e["type"] == "fill"]
        arrival = [str(s["cmd"].split()[1]) for s in quoted[:2]]
        self.assertEqual([str(f["resting_id"]) for f in fills[:2]], arrival)

    def test_card_validates_and_headlines_the_ratio(self):
        card_path = pathlib.Path(tempfile.mkdtemp()) / "card.json"
        self.run_script(
            "--bench", str(FIXTURES / "bench_fixture.json"),
            "--replay", str(FIXTURES / "replay_small.jsonl"),
            "--card", str(card_path),
        )
        card = json.loads(card_path.read_text())
        schema = json.loads((FIXTURES / "card.schema.json").read_text())
        jsonschema.validate(card, schema)

        bench = json.loads((FIXTURES / "bench_fixture.json").read_text())
        rates, _ = medians(FIXTURES / "bench_fixture.json")
        self.assertEqual(card["metric"]["value"], round(rates["FlatBook"] / rates["MapBook"], 2))
        self.assertTrue(card["metric"]["higher_is_better"])
        self.assertEqual(card["sha"], bench["context"]["git_sha"])
        map_latency = next(b for b in bench["benchmarks"] if b["name"].startswith("BM_Latency<MapBook>"))
        rows = {row["label"]: row for row in card["chart"]["rows"]}
        self.assertEqual(set(rows), {"add", "cancel", "match"})
        for row in rows.values():
            self.assertLessEqual(row["low"], row["mid"])
            self.assertLessEqual(row["mid"], row["high"])
        self.assertEqual(rows["cancel"]["high"], map_latency["cancel_p99_ns"])
        self.assertEqual(rows["match"]["low"], map_latency["add_crossing_p50_ns"])
        self.assertIn("synthetic", card["data_note"].lower())
        self.assertIn("single run", card["data_note"], "a one-run fixture must not be called a median")
        self.assertLessEqual(len(card["takeaway"]), 140)

    def test_sections_have_stable_anchors(self):
        # Shared links like docs/#latency must keep working when the report is rebuilt.
        for args in (("--bench", "missing.json", "--replay", "missing.jsonl"),
                     ("--bench", str(BENCH), "--replay", str(REPLAY))):
            _, out = self.run_script(*args)
            page = out.read_text()
            for anchor in ("latency", "throughput", "replay"):
                self.assertIn(f'<h2 id="{anchor}">', page)

    def test_missing_inputs_show_empty_state(self):
        _, out = self.run_script("--bench", "missing.json", "--replay", "missing.jsonl")
        page = out.read_text()
        self.assertIn("No benchmark data: run scripts/build_report.py after cmake --build", page)

    def test_malformed_replay_line_reports_line_number(self):
        bad = pathlib.Path(tempfile.mkdtemp()) / "replay.jsonl"
        good = (FIXTURES / "replay_small.jsonl").read_text().splitlines()
        bad.write_text("\n".join([good[0], "{not json", good[1]]) + "\n")
        _, out = self.run_script("--bench", str(FIXTURES / "bench_fixture.json"), "--replay", str(bad))
        self.assertIn("line 2 is not valid JSON", out.read_text())


if __name__ == "__main__":
    unittest.main()
