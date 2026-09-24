"""Browser QA of the built page (SPEC M3): Playwright/Chromium at 1440 px and 390 px.

Checks: no console errors (font-loading failures are reported but tolerated), no horizontal overflow, all eight
sections present and visible, 100 table rows, a verdict filter narrows the table to the right count, a row
expands to show its evidence. Full-page screenshots go to results/qa/. Exit 0 when every check passes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ["top", "result", "patterns", "agent", "verification", "table", "proof", "limitations"]
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}


def check_page(page, url: str, expected: dict) -> list[str]:
    problems: list[str] = []
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(url, wait_until="load")
    page.wait_for_timeout(500)
    real_errors = [e for e in errors if "fonts.g" not in e and "Failed to load resource" not in e]
    if real_errors:
        problems.append(f"console errors: {real_errors[:3]}")
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if overflow > 0:
        problems.append(f"horizontal overflow of {overflow}px")
    for sid in SECTIONS:
        box = page.locator(f"#{sid}").bounding_box()
        if not box or box["height"] <= 0:
            problems.append(f"section #{sid} missing or not visible")
    n_rows = page.locator("tr.app-row").count()
    if n_rows != expected["rows"]:
        problems.append(f"expected {expected['rows']} table rows, found {n_rows}")
    page.click(".filters .group[data-key=verdict] button[data-value=buildable_gated]")
    shown = int(page.inner_text("#shown"))
    if shown != expected["gated"]:
        problems.append(f"gated filter shows {shown}, expected {expected['gated']}")
    page.click(".filters .group[data-key=verdict] button[data-value=buildable_gated]")
    first = page.locator("tr.app-row").first
    first.click()
    detail = page.locator(f"#{first.get_attribute('aria-controls')}")
    if not detail.is_visible():
        problems.append("clicking a row did not reveal its evidence")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", default=str(ROOT / "site"))
    ap.add_argument("--out", default=str(ROOT / "results" / "qa"))
    args = ap.parse_args(argv)
    site = Path(args.site)
    if not (site / "index.html").exists():
        print(f"not found: {site / 'index.html'} (run python scripts/build_site.py first)")
        return 2
    results = json.loads((site / "results.json").read_text(encoding="utf-8"))
    expected = {"rows": len(results["rows"]), "gated": results["patterns"]["verdict_counts"]["buildable_gated"]}
    from playwright.sync_api import sync_playwright
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (w, h) in VIEWPORTS.items():
            page = browser.new_page(viewport={"width": w, "height": h})
            problems = check_page(page, (site / "index.html").resolve().as_uri(), expected)
            page.screenshot(path=str(out / f"{name}_{w}.png"), full_page=True)
            page.close()
            status = "PASS" if not problems else "FAIL"
            failures += bool(problems)
            print(f"{status} {name} {w}x{h}: " + ("all checks passed" if not problems else "; ".join(problems)))
        browser.close()
    print(f"screenshots: {out}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
