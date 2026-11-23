"""Loads docs/index.html in headless Chromium at phone and desktop widths in
both color schemes and checks what a reader relies on: no script errors, no
sideways scrolling on a phone, one latency table row per operation and book,
a replay that opens on a populated book, and arrow keys that step the replay
only while the demo has focus (no page-wide shortcuts, WCAG 2.1.4).

Usage: python3 tests/report_check.py [--page docs/index.html] [--shots DIR]
       [--readme-images docs/img]
"""

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

EXPECTED_OPS = ["Add, resting", "Add, crossing", "Cancel", "Market"]


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
                ops = page.eval_on_selector_all("#lat-table tbody th", "cells => cells.map(c => c.textContent)")
                if ops[::2] != EXPECTED_OPS or ops[1::2] != EXPECTED_OPS:
                    failures.append(f"{label}: latency table rows {ops}, expected each of {EXPECTED_OPS} twice")
                overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                if overflow > 0:
                    failures.append(f"{label}: page scrolls sideways by {overflow}px")
                start = page.evaluate('JSON.parse(document.getElementById("report-data").textContent).replayStart')
                levels = page.eval_on_selector_all("#ladder tr.bid, #ladder tr.ask", "rows => rows.length")
                if levels < 6:
                    failures.append(f"{label}: first replay frame shows {levels} levels, expected at least 6")
                page.keyboard.press("ArrowRight")
                if page.get_attribute("#ladder", "data-step") != str(start):
                    failures.append(f"{label}: ArrowRight outside the demo moved the replay")
                page.focus("#demo")
                for _ in range(5):
                    page.keyboard.press("ArrowRight")
                if page.get_attribute("#ladder", "data-step") != str(start + 5):
                    failures.append(f"{label}: ladder not on step {start + 6} after 5 presses inside the demo")
                if shots:
                    pathlib.Path(shots).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=f"{shots}/report-{width}-{scheme}.png", full_page=True)
                if readme_images and width == 1280 and scheme == "light":
                    pathlib.Path(readme_images).mkdir(parents=True, exist_ok=True)
                    page.add_style_tag(content=".bar{position:static}")
                    page.locator("section:has(#latency) .figure").screenshot(path=f"{readme_images}/latency.png")
                    sweep = page.evaluate(
                        """() => JSON.parse(document.getElementById("report-data").textContent)
                            .replay.findIndex((s) => s.events.filter((e) => e.type === "fill").length >= 2)"""
                    )
                    if sweep >= 0:
                        page.eval_on_selector(
                            "#scrubber",
                            "(el, i) => { el.value = String(i); el.dispatchEvent(new Event('input')); }",
                            sweep,
                        )
                    page.locator("section:has(#replay)").screenshot(path=f"{readme_images}/replay.png")
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
    print(f"report check: {len(failures)} failures across 4 viewport/scheme combinations")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
