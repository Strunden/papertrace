#!/usr/bin/env python3
"""Colourisation experiments for the Artist-sketch style: line + wash.

For each input image, renders the artist ink lines (exact app pipeline)
over four underlays and composes a labelled contact sheet:
  1. none        - the shipped look (paper only)
  2. soft wash   - blurred, desaturated original (watercolour feel)
  3. posterized  - flat quantized colour areas (screen-print feel)
  4. paprika     - the paprika model's painterly colours as underpaint

Usage: .venv/bin/python test/colour_sheet.py out_dir img1.jpg [img2 ...]
"""
import base64, http.server, os, re, socket, sys, threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STYLES_JS = open(os.path.join(ROOT, "src", "styles.js")).read()
m = re.search(r"function claheEnhance\(.*?\n\}\n",
              open(os.path.join(ROOT, "src", "app.js")).read(), re.S)
CLAHE_JS = m.group(0)

APP_STYLE = ("{ detail: 0.85, threshold: 0.18, thickness: 0.15,"
             " invert: false, knockWhite: true, colour: [16, 16, 20] }")

SETUP_JS = """
async (base) => {
  ort.env.wasm.wasmPaths = base;
  ort.env.wasm.numThreads = 1;
  const load = async (f) => ort.InferenceSession.create(
    await (await fetch(base + f)).arrayBuffer(), { executionProviders: ['wasm'] });
  window.__artist = await load('lineart.onnx');
  window.__paprika = await load('paprika.onnx');
  return 1;
}
"""

RENDER_JS = """
async ([b64, style]) => {
  const img = new Image();
  img.src = 'data:image/jpeg;base64,' + b64;
  await img.decode();
  const k0 = Math.min(1, 1000 / Math.max(img.width, img.height));
  const w = Math.round(img.width * k0), h = Math.round(img.height * k0);
  const src = document.createElement('canvas');
  src.width = w; src.height = h;
  const sctx = src.getContext('2d', { willReadFrequently: true });
  sctx.drawImage(img, 0, 0, w, h);

  const prep = (snap, norm, layout, enhance) => {
    const k = Math.min(1, 512 / Math.max(w, h));
    const nw = Math.max(snap, Math.round(w * k / snap) * snap);
    const nh = Math.max(snap, Math.round(h * k / snap) * snap);
    const c = document.createElement('canvas');
    c.width = nw; c.height = nh;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(src, 0, 0, nw, nh);
    let im = ctx.getImageData(0, 0, nw, nh);
    if (enhance) im = claheEnhance(im, 2.2, 8);
    const d = im.data, npx = nw * nh;
    const input = new Float32Array(npx * 3);
    const nv = (v) => norm === 'pm1' ? v / 127.5 - 1 : v / 255;
    for (let i = 0; i < npx; i++) {
      if (layout === 'nhwc') {
        input[i * 3] = nv(d[i * 4]); input[i * 3 + 1] = nv(d[i * 4 + 1]);
        input[i * 3 + 2] = nv(d[i * 4 + 2]);
      } else {
        input[i] = nv(d[i * 4]); input[npx + i] = nv(d[i * 4 + 1]);
        input[2 * npx + i] = nv(d[i * 4 + 2]);
      }
    }
    return { input, dims: layout === 'nhwc' ? [1, nh, nw, 3] : [1, 3, nh, nw] };
  };
  const run = async (sess, p) => {
    const feeds = {};
    feeds[sess.inputNames[0]] = new ort.Tensor('float32', p.input, p.dims);
    return (await sess.run(feeds))[sess.outputNames[0]];
  };

  // ink lines (artist)
  const aout = await run(window.__artist, prep(4, '01', 'nchw', true));
  const ink = new Float32Array(aout.data.length);
  for (let i = 0; i < ink.length; i++) ink[i] = 1 - Math.max(0, Math.min(1, aout.data[i]));
  const styled = applyStyle(sctx.getImageData(0, 0, w, h), Object.assign({}, style,
    { preset: 'artist', neuralMaps: { artist: { data: ink, w: aout.dims[3], h: aout.dims[2] } } }));
  const inkCv = document.createElement('canvas');
  inkCv.width = w; inkCv.height = h;
  inkCv.getContext('2d').putImageData(styled, 0, 0);

  // paprika colours
  const pout = await run(window.__paprika, prep(32, 'pm1', 'nhwc', false));
  const pw = pout.dims[2], ph = pout.dims[1];
  const pim = new ImageData(pw, ph);
  for (let i = 0; i < pw * ph; i++) {
    pim.data[i * 4] = (pout.data[i * 3] + 1) * 127.5;
    pim.data[i * 4 + 1] = (pout.data[i * 3 + 1] + 1) * 127.5;
    pim.data[i * 4 + 2] = (pout.data[i * 3 + 2] + 1) * 127.5;
    pim.data[i * 4 + 3] = 255;
  }
  const papCv = document.createElement('canvas');
  papCv.width = pw; papCv.height = ph;
  papCv.getContext('2d').putImageData(pim, 0, 0);

  // Marker-fill machinery: product-sketch look. Colour goes on in rough
  // diagonal strokes (deterministic, so an animation won't flicker), paper
  // shows through between strokes, and bright areas stay unpainted the way
  // a marker artist leaves highlights white.
  const rnd = (i) => { const x = Math.sin(i * 127.13 + 7.7) * 43758.5453; return x - Math.floor(x); };
  // Short overlapping marker swipes, not edge-to-edge rain: each stripe is a
  // row of dashes with round caps, random lengths and gaps.
  const streakMask = (angle, cover) => {
    const mc = document.createElement('canvas');
    mc.width = w; mc.height = h;
    const g2 = mc.getContext('2d');
    g2.translate(w / 2, h / 2);
    g2.rotate(angle);
    g2.lineCap = 'round';
    g2.strokeStyle = '#fff';
    const span = Math.hypot(w, h);
    const step = w / 26;
    let i = 0;
    for (let y = -span / 2; y < span / 2; y += step) {
      i++;
      const wob = (rnd(i + 50) - 0.5) * step * 0.5;
      let x = -span / 2 + rnd(i + 150) * span * 0.1;
      while (x < span / 2) {
        i++;
        const seg = span * (0.06 + rnd(i + 300) * 0.2);
        const gap = span * (0.015 + rnd(i + 350) * 0.08);
        if (rnd(i) < cover) {
          g2.globalAlpha = 0.5 + rnd(i + 250) * 0.45;
          g2.lineWidth = step * (0.55 + rnd(i + 100) * 0.5);
          g2.beginPath();
          g2.moveTo(x, y + wob);
          g2.lineTo(Math.min(x + seg, span / 2), y + wob + (rnd(i + 400) - 0.5) * step * 0.3);
          g2.stroke();
        }
        x += seg + gap;
      }
    }
    return mc;
  };
  // Product-sketch palette: saturated colour stays, near-neutral pixels
  // become a light cool grey (the classic Copic shading marker), so desks
  // and shadows don't turn to mud.
  const copicRemap = (cv, greyLight) => {
    const c2 = cv.getContext('2d', { willReadFrequently: true });
    const im = c2.getImageData(0, 0, cv.width, cv.height);
    const d2 = im.data;
    for (let i = 0; i < d2.length; i += 4) {
      const r = d2[i], g3 = d2[i + 1], b2 = d2[i + 2];
      const chroma = Math.max(r, g3, b2) - Math.min(r, g3, b2);
      if (chroma < 34) {
        const l = (0.2126 * r + 0.7152 * g3 + 0.0722 * b2) / 255;
        const t = Math.min(1, Math.max(0, (1 - l) * 1.1));
        d2[i] = 199 + (158 - 199) * t + greyLight;
        d2[i + 1] = 205 + (166 - 205) * t + greyLight;
        d2[i + 2] = 214 + (178 - 214) * t + greyLight;
      }
    }
    c2.putImageData(im, 0, 0);
    return cv;
  };
  const posterize = (levels, sat, bright, res) => {
    const small = document.createElement('canvas');
    const sw2 = res, sh2 = Math.round(h * res / w);
    small.width = sw2; small.height = sh2;
    const sc = small.getContext('2d', { willReadFrequently: true });
    sc.filter = 'blur(1.5px) saturate(' + sat + ') brightness(' + bright + ')';
    sc.drawImage(src, 0, 0, sw2, sh2);
    const im = sc.getImageData(0, 0, sw2, sh2);
    const q = 255 / (levels - 1);
    for (let i = 0; i < im.data.length; i++) {
      if (i % 4 !== 3) im.data[i] = Math.round(Math.round(im.data[i] / q) * q);
    }
    sc.putImageData(im, 0, 0);
    return small;
  };
  // white-highlight knockout: erase fill where the (blurred) photo is bright
  const highlightCut = (fillCv, thresh) => {
    const lc = document.createElement('canvas');
    lc.width = w; lc.height = h;
    const g2 = lc.getContext('2d', { willReadFrequently: true });
    g2.filter = 'blur(' + Math.round(w / 120) + 'px)';
    g2.drawImage(src, 0, 0, w, h);
    const im = g2.getImageData(0, 0, w, h);
    const cut = fillCv.getContext('2d', { willReadFrequently: true });
    const fim = cut.getImageData(0, 0, w, h);
    for (let i = 0; i < w * h; i++) {
      const r = im.data[i * 4], g3 = im.data[i * 4 + 1], b2 = im.data[i * 4 + 2];
      const l = 0.2126 * r + 0.7152 * g3 + 0.0722 * b2;
      const chroma = Math.max(r, g3, b2) - Math.min(r, g3, b2);
      // only true paper-white highlights get spared out - bright SATURATED
      // colour (a yellow daisy heart) keeps its marker fill
      if (l > thresh && chroma < 46) {
        const f = Math.min(1, (l - thresh) / 22);
        fim.data[i * 4 + 3] *= (1 - f);
      }
    }
    cut.putImageData(fim, 0, 0);
  };
  const markerFill = (g, colourCv, opts) => {
    const fill = document.createElement('canvas');
    fill.width = w; fill.height = h;
    const fg = fill.getContext('2d');
    fg.imageSmoothingEnabled = opts.smooth !== false;
    fg.drawImage(colourCv, 0, 0, w, h);
    // first marker pass
    fg.globalCompositeOperation = 'destination-in';
    fg.drawImage(streakMask(-0.6, opts.cover), 0, 0);
    fg.globalCompositeOperation = 'source-over';
    // second pass at a slightly different angle darkens the overlap the way
    // layered marker does
    const second = document.createElement('canvas');
    second.width = w; second.height = h;
    const sg = second.getContext('2d');
    sg.drawImage(colourCv, 0, 0, w, h);
    sg.globalCompositeOperation = 'destination-in';
    sg.drawImage(streakMask(-0.45, opts.cover * 0.55), 0, 0);
    fg.globalAlpha = 0.5;
    fg.drawImage(second, 0, 0);
    fg.globalAlpha = 1;
    highlightCut(fill, opts.highlight);
    g.save();
    g.globalCompositeOperation = 'multiply';
    g.globalAlpha = opts.alpha;
    g.drawImage(fill, 0, 0);
    g.restore();
  };

  const underlays = {
    'sketch only': (g) => {},
    'marker': (g) => markerFill(g, copicRemap(posterize(5, 1.35, 1.1, 160), 0),
                                { cover: 0.8, highlight: 205, alpha: 0.88 }),
    'marker light': (g) => markerFill(g, copicRemap(posterize(5, 1.3, 1.16, 140), 14),
                                      { cover: 0.62, highlight: 196, alpha: 0.8 }),
    'marker paprika': (g) => markerFill(g, copicRemap(papCv, 6),
                                        { cover: 0.75, highlight: 205, alpha: 0.85 }),
  };

  const pad = 8, labelH = 24;
  const names = Object.keys(underlays);
  const sheet = document.createElement('canvas');
  sheet.width = names.length * (w + pad) + pad;
  sheet.height = h + pad * 2 + labelH;
  const g = sheet.getContext('2d');
  g.fillStyle = '#242424'; g.fillRect(0, 0, sheet.width, sheet.height);
  names.forEach((name, i) => {
    const x = pad + i * (w + pad);
    g.save();
    g.translate(x, pad);
    g.fillStyle = '#f6f1e4';
    g.fillRect(0, 0, w, h);
    underlays[name](g);
    g.drawImage(inkCv, 0, 0);
    g.restore();
    g.fillStyle = '#fff'; g.font = 'bold 16px sans-serif';
    g.fillText(name, x, pad + h + 18);
  });
  return sheet.toDataURL('image/jpeg', 0.88);
}
"""


def main():
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    os.chdir(ROOT)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            page.on("pageerror", lambda e: print("[pageerror]", e))
            page.goto(f"http://127.0.0.1:{port}/docs/index.html")
            page.set_content("<body></body>")
            page.add_script_tag(url=f"http://127.0.0.1:{port}/docs/ort.min.js")
            page.add_script_tag(content=STYLES_JS)
            page.add_script_tag(content=CLAHE_JS)
            page.evaluate(f"async (a) => ({SETUP_JS})(a)", f"http://127.0.0.1:{port}/docs/")
            style = page.evaluate(f"() => ({APP_STYLE})")
            for path in sys.argv[2:]:
                b64 = base64.b64encode(open(path, "rb").read()).decode()
                url = page.evaluate(f"async (a) => ({RENDER_JS})(a)", [b64, style])
                out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + "_colour.jpg")
                open(out, "wb").write(base64.b64decode(url.split(",", 1)[1]))
                print(path, "->", out)
            b.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
