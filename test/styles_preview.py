#!/usr/bin/env python3
"""Render every trace preset over two source images into one contact sheet."""
import os, sys, json
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
read = lambda *p: open(os.path.join(ROOT, *p)).read()

flower = read("build", "flower_rose.svg")
html = f"""<html><body style="margin:0;background:#eef1f5">
<script>{read("src", "styles.js")}</script>
<div id="grid"></div>
<script>
const SIZE = 260;

/* A synthetic "photograph": smooth shading, hard edges, fine texture. */
function photoCanvas() {{
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const x = c.getContext('2d');
  const sky = x.createLinearGradient(0, 0, 0, 512);
  sky.addColorStop(0, '#8fb6d8'); sky.addColorStop(1, '#e8ddc8');
  x.fillStyle = sky; x.fillRect(0, 0, 512, 512);
  x.fillStyle = '#6b7f5a';
  x.beginPath(); x.moveTo(0, 350); x.lineTo(150, 250); x.lineTo(300, 340);
  x.lineTo(420, 265); x.lineTo(512, 330); x.lineTo(512, 512); x.lineTo(0, 512);
  x.closePath(); x.fill();
  const sun = x.createRadialGradient(360, 120, 8, 360, 120, 90);
  sun.addColorStop(0, '#fff9e0'); sun.addColorStop(1, 'rgba(255,249,224,0)');
  x.fillStyle = sun; x.fillRect(250, 10, 240, 240);
  const ball = x.createRadialGradient(180, 150, 10, 210, 190, 120);
  ball.addColorStop(0, '#ffffff'); ball.addColorStop(0.55, '#c0553f'); ball.addColorStop(1, '#40160f');
  x.fillStyle = ball; x.beginPath(); x.arc(200, 180, 95, 0, 7); x.fill();
  x.strokeStyle = '#3b4b33'; x.lineWidth = 6;
  for (let i = 0; i < 9; i++) {{
    x.beginPath(); x.moveTo(40 + i * 52, 512); x.lineTo(56 + i * 52, 380 + (i % 3) * 30); x.stroke();
  }}
  const d = x.getImageData(0, 0, 512, 512);
  for (let i = 0; i < d.data.length; i += 4) {{
    const nz = (Math.random() * 2 - 1) * 9;
    d.data[i] += nz; d.data[i + 1] += nz; d.data[i + 2] += nz;
  }}
  x.putImageData(d, 0, 0);
  return c;
}}

function svgCanvas(svg) {{
  return new Promise((res) => {{
    const img = new Image();
    img.onload = () => {{
      const c = document.createElement('canvas');
      c.width = c.height = 512;
      const x = c.getContext('2d');
      x.drawImage(img, 0, 0, 512, 512);
      res(c);
    }};
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent({json.dumps(flower)});
  }});
}}

function tile(label, imageData, dark) {{
  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:relative;width:' + SIZE + 'px;height:' + SIZE + 'px;'
    + 'background:' + (dark ? '#2c2f36' : '#f7f4ec')
    + ';outline:1px solid #ccd;overflow:hidden';
  const c = document.createElement('canvas');
  c.width = imageData.width; c.height = imageData.height;
  c.getContext('2d').putImageData(imageData, 0, 0);
  c.style.cssText = 'width:100%;height:100%;display:block';
  wrap.appendChild(c);
  const t = document.createElement('div');
  t.textContent = label;
  t.style.cssText = 'position:absolute;left:0;bottom:0;right:0;font:11px system-ui;'
    + 'background:rgba(0,0,0,.6);color:#fff;padding:3px 6px';
  wrap.appendChild(t);
  return wrap;
}}

window.run = async () => {{
  const grid = document.getElementById('grid');
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(7,' + SIZE + 'px);gap:5px;padding:5px';
  const sources = [['photo', photoCanvas()], ['line art', await svgCanvas()]];
  const stats = [];
  for (const [srcName, canvas] of sources) {{
    const ctx = canvas.getContext('2d');
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
    for (const p of STYLE_PRESETS) {{
      const t0 = performance.now();
      const out = applyStyle(data, {{ preset: p.id, detail: 0.85, threshold: 0.18,
                                     thickness: 0.15, colour: [16, 16, 20] }});
      const ms = performance.now() - t0;
      let ink = 0, bad = 0;
      for (let i = 3; i < out.data.length; i += 4) {{
        if (out.data[i] > 8) ink++;
        if (!Number.isFinite(out.data[i])) bad++;
      }}
      const frac = ink / (out.width * out.height);
      stats.push({{ src: srcName, preset: p.id, ink: +frac.toFixed(4),
                   ms: +ms.toFixed(1), bad }});
      grid.appendChild(tile(srcName + ' / ' + p.name + '  ' + (frac * 100).toFixed(1) + '%', out, false));
    }}
  }}
  // White lines on the dark tile, to check the colour option works.
  const data = sources[0][1].getContext('2d').getImageData(0, 0, 512, 512);
  grid.appendChild(tile('white lines on dark',
    applyStyle(data, {{ preset: 'clean', detail: 0.85, threshold: 0.18, thickness: 0.2,
                       colour: [255, 255, 255] }}), true));
  return stats;
}};
</script></body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 7 * 265 + 20, "height": 900})
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.set_content(html)
    stats = page.evaluate("() => window.run()")
    page.wait_for_timeout(250)
    page.screenshot(path=os.path.join(ROOT, "build", "style_sheet.png"), full_page=True)
    b.close()

for s in stats:
    flag = "  <-- suspicious" if (s["ink"] < 0.002 or s["ink"] > 0.75 or s["bad"]) else ""
    print(f"{s['src']:9s} {s['preset']:9s} ink={s['ink']*100:5.2f}%  {s['ms']:6.1f}ms{flag}")
if errs:
    print("PAGE ERRORS:", errs)
    sys.exit(1)
