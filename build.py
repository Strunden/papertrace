#!/usr/bin/env python3
"""Inline every source file into one self-contained papertrace.html."""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "docs")   # GitHub Pages serves this folder
os.makedirs(DIST, exist_ok=True)

read = lambda *p: open(os.path.join(*p), encoding="utf-8").read()

JS_FILES = ["geom.js", "aruco.js", "tracker.js", "styles.js", "app.js"]
js = "\n\n".join(f"/* ===== {f} ===== */\n" + read(SRC, f) for f in JS_FILES)
css = read(SRC, "style.css")
dictionary = json.loads(read(BUILD, "dictionary.json"))
flowers = json.loads(read(BUILD, "flowers.json"))

html = read(SRC, "index.template.html")
# Plain replacement, not re.sub: the payloads contain backslashes and $ sequences
# that re would try to interpret as group references.
for token, payload in (("/*__CSS__*/", css),
                       ("/*__DICT__*/null", json.dumps(dictionary, separators=(",", ":"))),
                       ("/*__FLOWERS__*/null", json.dumps(flowers, separators=(",", ":"))),
                       ("/*__JS__*/", js)):
    assert token in html, "missing token " + token
    html = html.replace(token, payload, 1)

# A stray </script> inside injected JSON or JS would end the block early.
body = html.split("<body>", 1)[1]
assert body.count("<script") == body.count("</script>"), "unbalanced script tags"

out = os.path.join(DIST, "index.html")
open(out, "w", encoding="utf-8").write(html)
# Ship the printable tags alongside the app so the Pages site is self-contained.
import shutil
for name in ("papertrace-markers-A4.pdf", "papertrace-markers-Letter.pdf",
             "papertrace-canvas-A4.pdf", "papertrace-canvas-Letter.pdf"):
    shutil.copy(os.path.join(BUILD, name), os.path.join(DIST, name))
print(f"wrote {out}  ({len(html) / 1024:.0f} KB)")
