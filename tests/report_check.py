"""Loads docs/index.html in headless Chromium at phone and desktop widths in
both color schemes and checks what a reader relies on: no script errors, no
sideways scrolling on a phone, one latency row per operation in the chart
variant actually shown at that width, and charts present with JavaScript off.

Usage: python3 tests/report_check.py [--page docs/index.html] [--shots DIR]
       [--readme-images docs/img]
"""

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

EXPECTED_OPS = ["add_resting", "add_crossing", "cancel", "market"]
VISIBLE_ROWS = """() => [...document.querySelectorAll('#fig-latency svg')]
    .filter(s => s.getBoundingClientRect().width > 0)
    .map(s => [...s.querySelectorAll('[data-row]')].map(g => g.dataset.row))"""


def check(page_path, shots, readme_images):
    url = pathlib.Path(page_path).resolve().as_uri()
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium")
        for width in (375, 1280):
            for scheme in ("light", "dark"):
                label = f"{width}px {scheme}"
                context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
                page = context.new_page()
                errors = []
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(url)
                if errors:
                    failures.append(f"{label}: console errors {errors}")
                shown = page.evaluate(VISIBLE_ROWS)
                if shown != [EXPECTED_OPS]:
                    failures.append(f"{label}: visible latency charts {shown}, expected one with {EXPECTED_OPS}")
                overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                if overflow > 0:
                    failures.append(f"{label}: page scrolls sideways by {overflow}px")
                if shots:
                    pathlib.Path(shots).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=f"{shots}/report-{width}-{scheme}.png", full_page=True)
                if readme_images and width == 1280 and scheme == "light":
                    pathlib.Path(readme_images).mkdir(parents=True, exist_ok=True)
                    page.locator("#fig-throughput").screenshot(path=f"{readme_images}/throughput.png")
                context.close()
        context = browser.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(url)
        if page.locator("svg[role=img]").count() < 4:
            failures.append("JavaScript off: charts missing")
        context.close()
        browser.close()
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", default="docs/index.html")
    parser.add_argument("--shots")
    parser.add_argument("--readme-images")
    args = parser.parse_args()
    failures = check(args.page, args.shots, args.readme_images)
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"report check: {len(failures)} failures across 4 viewport/scheme combinations and JavaScript off")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
