"""Checks that build_report.py turns a benchmark run into a valid portfolio
card whose numbers are the run's numbers, since the site displays them as-is.

Run: python3 -m unittest discover -s tests -p 'test_*.py'  (needs jsonschema)
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import jsonschema

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import build_report  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


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

    def test_card_validates_and_matches_bench(self):
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
        map_latency = next(b for b in bench["benchmarks"] if b["name"].startswith("BM_Latency<MapBook>"))
        self.assertEqual(card["metric"]["value"], map_latency["add_resting_p50_ns"])
        self.assertEqual(card["sha"], bench["context"]["git_sha"])
        rows = {row["label"]: row for row in card["chart"]["rows"]}
        self.assertEqual(set(rows), {"add", "cancel", "match"})
        for row in rows.values():
            self.assertLessEqual(row["low"], row["mid"])
            self.assertLessEqual(row["mid"], row["high"])
        self.assertEqual(rows["cancel"]["high"], map_latency["cancel_p99_ns"])
        self.assertEqual(rows["match"]["low"], map_latency["add_crossing_p50_ns"])
        self.assertIn("synthetic", card["data_note"].lower())
        self.assertLessEqual(len(card["takeaway"]), 140)

    def test_sections_have_stable_anchors(self):
        # Shared links like docs/#latency must keep working when the report is rebuilt.
        _, out = self.run_script("--bench", "missing.json", "--replay", "missing.jsonl")
        page = out.read_text()
        for anchor in ("latency", "throughput", "replay"):
            self.assertIn(f'id="{anchor}"', page)

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


class LatencyTakeawayTest(unittest.TestCase):
    """The latency headline must not present a sub-resolution gap as a speedup:
    the timer advances in ticks, so a gap under two ticks can be one sample
    landing on the other side of a tick boundary."""

    def bench(self, tick):
        bench, error = build_report.load_bench(str(FIXTURES / "bench_fixture.json"))
        self.assertIsNone(error)
        bench["clock"]["tick_ns"] = tick
        return bench

    def test_gap_under_two_ticks_reads_as_resolution_limited(self):
        # Fixture p99 gaps are 50 ns; with a 41 ns tick that is 1.2 ticks.
        lat = build_report.takeaway_latency(self.bench(41.0))
        self.assertFalse(lat["resolved"])
        self.assertIn("timer's resolution", lat["headline"])
        self.assertIn("within the timer's resolution", lat["text"])
        self.assertIn("one tick lower", lat["text"])

    def test_gap_of_two_ticks_or_more_is_reported_as_a_difference(self):
        # With a 10 ns tick the same 50 ns gaps are 5 ticks: a real difference.
        lat = build_report.takeaway_latency(self.bench(10.0))
        self.assertTrue(lat["resolved"])
        self.assertNotIn("resolution", lat["headline"] + lat["text"])
        self.assertIn("lower on 4 of 4", lat["headline"])
        self.assertIn("resting adds 200 vs 250 ns", lat["text"])

    def test_page_headline_numbers_come_from_the_bench_file(self):
        bench, _ = build_report.load_bench(str(FIXTURES / "bench_fixture.json"))
        page = build_report.build_page(bench, None, None, "no replay")
        # Fixture: FlatBook 20M msgs/s, MapBook 10M msgs/s.
        self.assertIn("matches 20 million messages a second", page)
        self.assertIn("2.00×", page)
        self.assertIn("20.00 M msgs/s", page)


if __name__ == "__main__":
    unittest.main()
