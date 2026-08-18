#!/usr/bin/env python3
import json, os, sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def read(*p):
    return open(os.path.join(ROOT, *p)).read()


extra = sys.argv[1:] or []
scripts = [read("src", "geom.js"), read("src", "aruco.js"), read("src", "tracker.js")]
for e in extra:
    scripts.append(read("src", e))
scripts.append(read("test", "detector_test.js"))
scripts.append(read("test", "tracker_test.js"))
dict_json = read("build", "dictionary.json")

html = ("<html><body><script>window.__DICT__=" + dict_json + ";</script>"
        + "".join(f"<script>{s}</script>" for s in scripts)
        + "</body></html>")

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append("console." + m.type + ": " + m.text)
            if m.type == "error" else None)
    page.set_content(html)
    if errors:
        print("PAGE ERRORS:")
        for e in errors:
            print(" ", e)
        b.close()
        sys.exit(1)
    results = page.evaluate("() => runTests(window.__DICT__).concat(runTrackerTests(window.__DICT__))")
    b.close()

fails = 0
for r in results:
    mark = "PASS" if r["pass"] else "FAIL"
    if not r["pass"]:
        fails += 1
    print(f"[{mark}] {r['name']:42s} {r['detail']}")
print(f"\n{len(results) - fails}/{len(results)} passed")
sys.exit(1 if fails else 0)
