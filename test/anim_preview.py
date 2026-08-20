#!/usr/bin/env python3
"""Standalone iteration harness for the print-step explainer animation.

Extracts the #howAnim SVG + caption strip from src/index.template.html and
their CSS from src/style.css, renders them on a bare page (no app, no
navigation), and:

  - screenshots a strip of frozen phases (0..1 of the 5.6 s loop) into
    build/anim_phases.png
  - measures anchoring: between pan extremes the phone chrome must move,
    the daisy must not (it is anchored to the sheet, not the phone)

Usage: .venv/bin/python test/anim_preview.py [phase ...]   (defaults below)
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_MS = 5600


def extract():
    tpl = open(os.path.join(ROOT, 'src', 'index.template.html')).read()
    svg = tpl[tpl.index('<svg id="howAnim"'):]
    svg = svg[:svg.index('</svg>') + len('</svg>')]
    cap = tpl[tpl.index('<p id="howCaption"'):]
    cap = cap[:cap.index('</p>') + len('</p>')]

    css = open(os.path.join(ROOT, 'src', 'style.css')).read()
    # everything from the explainer comment block to the end of its
    # reduced-motion guard - keep the slice generous and self-contained
    start = css.index('/* --------------------------------------------- print step: how-it-works */')
    anim_css = css[start:]
    return svg, cap, anim_css


def page_html():
    svg, cap, anim_css = extract()
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
body {{ margin: 0; padding: 30px 20px;
  background: radial-gradient(140% 90% at 50% -10%, #faf6ec 0%, #f2ebdb 55%, #e9e0cc 100%);
  font: 15px/1.45 -apple-system, sans-serif; }}
.stage {{ max-width: 430px; margin: 0 auto; text-align: center; }}
{anim_css}
</style></head><body>
<div class="stage">{svg}{cap}</div>
<script>
  function freeze(frac) {{
    document.getAnimations().forEach(a => {{ a.pause(); a.currentTime = {LOOP_MS} * frac; }});
  }}
  function play() {{ document.getAnimations().forEach(a => a.play()); }}
</script>
</body></html>"""


def main():
    phases = [float(x) for x in sys.argv[1:]] or [0.02, 0.14, 0.3, 0.5, 0.72, 0.9]
    html = page_html()
    out_dir = os.path.join(ROOT, 'build')
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, 'anim_preview.html'), 'w').write(html)

    shots = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={'width': 430, 'height': 420}, device_scale_factor=2)
        page.set_content(html)
        page.wait_for_timeout(300)

        # anchor regression: chrome moves between pan extremes, daisy doesn't
        drift = page.evaluate(f"""async () => {{
          const daisy = document.querySelector('#howAnim g[clip-path] .appear circle');
          const chrome = document.querySelector('#howAnim .phone rect');
          const at = async (frac) => {{
            freeze(frac);
            await new Promise(r => setTimeout(r, 60));
            return {{ d: daisy.getBoundingClientRect().x, p: chrome.getBoundingClientRect().x }};
          }};
          const a = await at(0.0), b = await at(0.5);
          return {{ daisy: +(b.d - a.d).toFixed(2), phone: +(b.p - a.p).toFixed(2) }};
        }}""")
        ok = abs(drift['daisy']) < 0.01 and abs(drift['phone']) > 5
        print(f"[{'PASS' if ok else 'FAIL'}] anchoring: phone moved {drift['phone']}px, "
              f"daisy moved {drift['daisy']}px between pan extremes")

        for frac in phases:
            page.evaluate(f"freeze({frac})")
            page.wait_for_timeout(120)
            path = os.path.join(out_dir, f'anim_{int(frac*100):03d}.png')
            page.screenshot(path=path)
            shots.append((path, f'{int(frac*100)}%'))
        b.close()

    from PIL import Image, ImageDraw
    imgs = [(Image.open(p), lbl) for p, lbl in shots]
    tw = 430
    imgs = [(im.resize((tw, round(im.height * tw / im.width))), lbl) for im, lbl in imgs]
    th = max(im.height for im, _ in imgs)
    pad, lab = 14, 34
    sheet = Image.new('RGB', (len(imgs) * (tw + pad) + pad, th + pad * 2 + lab), (28, 26, 23))
    d = ImageDraw.Draw(sheet)
    for i, (im, lbl) in enumerate(imgs):
        x = pad + i * (tw + pad)
        sheet.paste(im, (x, pad))
        d.text((x, th + pad + 8), lbl, fill=(233, 224, 204))
    out = os.path.join(out_dir, 'anim_phases.png')
    sheet.save(out)
    print('->', out)
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
