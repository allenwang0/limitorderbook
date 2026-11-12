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
