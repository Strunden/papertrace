#!/usr/bin/env python3
"""Render every trace preset against real photos at the app's true resolution.

Standalone: loads src/styles.js directly into a blank page (no app UI, no
crop flow) and runs applyStyle exactly as the app would - images downscaled
to MAX_SOURCE=1000 on the longest side, app-default detail/threshold.

Usage: .venv/bin/python test/style_sheet.py out_dir img1.jpg [img2.jpg ...]
Writes one contact sheet per input image into out_dir.
"""
import base64
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
STYLES_JS = (ROOT / 'src' / 'styles.js').read_text()

# Mirror the app's defaults (state.style in app.js).
APP_STYLE = "{ detail: 0.85, threshold: 0.18, thickness: 0.15, invert: false, knockWhite: true, colour: [16, 16, 20] }"

RENDER_JS = """
async ([b64, styleDefaults]) => {
  const img = new Image();
  img.src = 'data:image/jpeg;base64,' + b64;
  await img.decode();
  // Same downscale as app.js drawSourceFrom (MAX_SOURCE = 1000).
  const k = Math.min(1, 1000 / Math.max(img.width, img.height));
  const w = Math.max(1, Math.round(img.width * k));
  const h = Math.max(1, Math.round(img.height * k));
  const src = document.createElement('canvas');
  src.width = w; src.height = h;
  src.getContext('2d').drawImage(img, 0, 0, w, h);
  const data = src.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, w, h);

  const pad = 8, labelH = 24, cols = 3;
  const entries = [{ name: 'Original photo', canvas: src }];
  for (const p of STYLE_PRESETS) {
    const t0 = performance.now();
    const out = applyStyle(data, Object.assign({}, styleDefaults, { preset: p.id }));
    const ms = Math.round(performance.now() - t0);
    const tmp = document.createElement('canvas');
    tmp.width = w; tmp.height = h;
    tmp.getContext('2d').putImageData(out, 0, 0);
    const tile = document.createElement('canvas');
    tile.width = w; tile.height = h;
    const tc = tile.getContext('2d');
    tc.fillStyle = '#eeeae0'; tc.fillRect(0, 0, w, h);
    tc.drawImage(tmp, 0, 0);
    entries.push({ name: p.name + ' (' + ms + 'ms)', canvas: tile });
  }
  const rows = Math.ceil(entries.length / cols);
  const sheet = document.createElement('canvas');
  sheet.width = cols * (w + pad) + pad;
  sheet.height = rows * (h + pad + labelH) + pad;
  const g = sheet.getContext('2d');
  g.fillStyle = '#242424'; g.fillRect(0, 0, sheet.width, sheet.height);
  entries.forEach((e, i) => {
    const x = pad + (i % cols) * (w + pad);
    const y = pad + Math.floor(i / cols) * (h + pad + labelH);
    g.drawImage(e.canvas, x, y);
    g.fillStyle = '#fff'; g.font = 'bold 16px sans-serif';
    g.fillText(e.name, x, y + h + 18);
  });
  return sheet.toDataURL('image/jpeg', 0.85);
}
"""


def main():
    out_dir = pathlib.Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    images = sys.argv[2:]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 400, 'height': 300})
        page.goto('about:blank')
        page.add_script_tag(content=STYLES_JS)
        for path in images:
            b64 = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
            data_url = page.evaluate(
                f'async (args) => ({RENDER_JS})(args)',
                [b64, page.evaluate(f'() => ({APP_STYLE})')],
            )
            out = out_dir / (pathlib.Path(path).stem + '_sheet.jpg')
            out.write_bytes(base64.b64decode(data_url.split(',', 1)[1]))
            print(f'{path} -> {out}')
        browser.close()


if __name__ == '__main__':
    main()
