#!/usr/bin/env python3
"""Juno-title-sequence look: collage of a coloured subject cutout over a
line-drawn, uncoloured background.

U2-Net (u2netp) segments the salient subject. Composite: paper + full
sketch lines everywhere; a white "sticker" silhouette (dilated mask) pastes
over the background; the subject's colour (lightly posterized, subtle
grain) fills the cutout; ink lines return on top inside the cutout.
Variant adds one flat background tone from the scene's dominant colour.

Usage: .venv/bin/python test/juno_sheet.py out_dir img1.jpg [...]
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
  ort.env.wasm.wasmPaths = base + 'docs/';
  ort.env.wasm.numThreads = 1;
  const load = async (u) => ort.InferenceSession.create(
    await (await fetch(base + u)).arrayBuffer(), { executionProviders: ['wasm'] });
  window.__artist = await load('docs/lineart.onnx');
  window.__seg = await load('build/colour_models/u2netp.onnx');
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

  // ---- artist ink lines (exact app pipeline)
  const snap = 4;
  const kk = Math.min(1, 512 / Math.max(w, h));
  const nw = Math.max(snap, Math.round(w * kk / snap) * snap);
  const nh = Math.max(snap, Math.round(h * kk / snap) * snap);
  const pc = document.createElement('canvas');
  pc.width = nw; pc.height = nh;
  const pctx = pc.getContext('2d', { willReadFrequently: true });
  pctx.drawImage(src, 0, 0, nw, nh);
  const pim = claheEnhance(pctx.getImageData(0, 0, nw, nh), 2.2, 8);
  const pd = pim.data, npx = nw * nh;
  const ain = new Float32Array(npx * 3);
  for (let i = 0; i < npx; i++) {
    ain[i] = pd[i * 4] / 255;
    ain[npx + i] = pd[i * 4 + 1] / 255;
    ain[2 * npx + i] = pd[i * 4 + 2] / 255;
  }
  const af = {};
  af[window.__artist.inputNames[0]] = new ort.Tensor('float32', ain, [1, 3, nh, nw]);
  const aout = (await window.__artist.run(af))[window.__artist.outputNames[0]];
  const inkMap = new Float32Array(aout.data.length);
  for (let i = 0; i < inkMap.length; i++) inkMap[i] = 1 - Math.max(0, Math.min(1, aout.data[i]));
  const styled = applyStyle(sctx.getImageData(0, 0, w, h), Object.assign({}, style,
    { preset: 'artist', neuralMaps: { artist: { data: inkMap, w: aout.dims[3], h: aout.dims[2] } } }));
  const inkCv = document.createElement('canvas');
  inkCv.width = w; inkCv.height = h;
  inkCv.getContext('2d').putImageData(styled, 0, 0);

  // ---- U2-Net subject mask
  const S = 320;
  const mc = document.createElement('canvas');
  mc.width = S; mc.height = S;
  const mg = mc.getContext('2d', { willReadFrequently: true });
  mg.drawImage(src, 0, 0, S, S);
  const md = mg.getImageData(0, 0, S, S).data;
  const sin = new Float32Array(S * S * 3);
  const MEAN = [0.485, 0.456, 0.406], STD = [0.229, 0.224, 0.225];
  for (let i = 0; i < S * S; i++) {
    for (let c = 0; c < 3; c++) {
      sin[c * S * S + i] = (md[i * 4 + c] / 255 - MEAN[c]) / STD[c];
    }
  }
  const sf = {};
  sf[window.__seg.inputNames[0]] = new ort.Tensor('float32', sin, [1, 3, S, S]);
  const sres = await window.__seg.run(sf);
  const sm = sres[window.__seg.outputNames[0]].data;
  let mn = 1e9, mx = -1e9;
  for (let i = 0; i < sm.length; i++) { mn = Math.min(mn, sm[i]); mx = Math.max(mx, sm[i]); }
  const maskCv = document.createElement('canvas');
  maskCv.width = S; maskCv.height = S;
  const mim = new ImageData(S, S);
  for (let i = 0; i < S * S; i++) {
    const v = (sm[i] - mn) / (mx - mn) > 0.5 ? 255 : 0;
    mim.data[i * 4 + 3] = v;
    mim.data[i * 4] = 255; mim.data[i * 4 + 1] = 255; mim.data[i * 4 + 2] = 255;
  }
  maskCv.getContext('2d').putImageData(mim, 0, 0);
  const maskFrac = (() => {
    let c2 = 0;
    for (let i = 0; i < S * S; i++) if (mim.data[i * 4 + 3]) c2++;
    return c2 / (S * S);
  })();

  const maskAt = (grow, blur) => {
    // dilate by drawing the mask multiple times offset, then blur
    const c2 = document.createElement('canvas');
    c2.width = w; c2.height = h;
    const g2 = c2.getContext('2d');
    if (blur) g2.filter = 'blur(' + blur + 'px)';
    for (let a = 0; a < 8; a++) {
      g2.drawImage(maskCv, Math.cos(a * 0.785) * grow, Math.sin(a * 0.785) * grow, w, h);
    }
    return c2;
  };

  // subject colour: lightly posterized, subtle grain
  const subjCv = (() => {
    const q = document.createElement('canvas');
    const qw = Math.round(w / 2.5), qh = Math.round(h / 2.5);
    q.width = qw; q.height = qh;
    const qg = q.getContext('2d', { willReadFrequently: true });
    qg.filter = 'blur(1px) saturate(1.15) brightness(1.05)';
    qg.drawImage(src, 0, 0, qw, qh);
    const qi = qg.getImageData(0, 0, qw, qh);
    for (let i = 0; i < qi.data.length; i++) {
      if (i % 4 !== 3) qi.data[i] = Math.round(qi.data[i] / 42) * 42;
    }
    qg.putImageData(qi, 0, 0);
    const c2 = document.createElement('canvas');
    c2.width = w; c2.height = h;
    const g2 = c2.getContext('2d');
    g2.drawImage(q, 0, 0, w, h);
    // clip to subject
    g2.globalCompositeOperation = 'destination-in';
    g2.drawImage(maskAt(0, 2), 0, 0);
    return c2;
  })();

  // flat background tone = median colour of the non-subject region, muted
  const bgTone = (() => {
    const d2 = sctx.getImageData(0, 0, w, h).data;
    const mctx = maskAt(0, 0).getContext('2d', { willReadFrequently: true });
    const a2 = mctx.getImageData(0, 0, w, h).data;
    const rs = [], gs = [], bs = [];
    for (let i = 0; i < w * h; i += 97) {
      if (a2[i * 4 + 3] < 128) { rs.push(d2[i * 4]); gs.push(d2[i * 4 + 1]); bs.push(d2[i * 4 + 2]); }
    }
    const med = (arr) => { arr.sort((x, y2) => x - y2); return arr[arr.length >> 1] || 200; };
    let r = med(rs), g3 = med(gs), b2 = med(bs);
    const l = 0.2126 * r + 0.7152 * g3 + 0.0722 * b2;
    // muted, lightened, pulled toward its own hue
    const t = 0.55;
    r = r * t + 205 * (1 - t); g3 = g3 * t + 208 * (1 - t); b2 = b2 * t + 205 * (1 - t);
    return 'rgb(' + [r, g3, b2].map(Math.round).join(',') + ')';
  })();

  const compose = (withTone) => {
    const c2 = document.createElement('canvas');
    c2.width = w; c2.height = h;
    const g2 = c2.getContext('2d');
    g2.fillStyle = withTone ? bgTone : '#faf7ef';
    g2.fillRect(0, 0, w, h);
    if (withTone) {
      // paper shows where the drawing is: white behind the line work
      g2.fillStyle = '#faf7ef';
      g2.fillRect(0, Math.round(h * 0.28), w, h);
    }
    g2.drawImage(inkCv, 0, 0);                       // full sketch everywhere
    if (maskFrac > 0.03 && maskFrac < 0.9) {
      g2.drawImage(maskAt(9, 3), 0, 0);              // white sticker border
      g2.drawImage(subjCv, 0, 0);                    // subject colour cutout
      // ink back on top, only inside the subject
      const inkClip = document.createElement('canvas');
      inkClip.width = w; inkClip.height = h;
      const ig = inkClip.getContext('2d');
      ig.drawImage(inkCv, 0, 0);
      ig.globalCompositeOperation = 'destination-in';
      ig.drawImage(maskAt(1, 2), 0, 0);
      g2.drawImage(inkClip, 0, 0);
    }
    return c2;
  };

  // New Yorker cartoon shading: ONE very light flat tone over the darker
  // half of the scene - soft edges, no texture, lines carry everything
  const lightShade = (() => {
    const lw = Math.round(w / 6), lh = Math.round(h / 6);
    const c2 = document.createElement('canvas');
    c2.width = lw; c2.height = lh;
    const g2 = c2.getContext('2d', { willReadFrequently: true });
    g2.filter = 'blur(3px)';
    g2.drawImage(src, 0, 0, lw, lh);
    const im2 = g2.getImageData(0, 0, lw, lh);
    for (let i = 0; i < lw * lh; i++) {
      const l = 0.2126 * im2.data[i * 4] + 0.7152 * im2.data[i * 4 + 1] + 0.0722 * im2.data[i * 4 + 2];
      const on = l < 168 ? 255 : 0;
      im2.data[i * 4] = 120; im2.data[i * 4 + 1] = 124; im2.data[i * 4 + 2] = 132;
      im2.data[i * 4 + 3] = on * 0.16;
    }
    g2.putImageData(im2, 0, 0);
    const big = document.createElement('canvas');
    big.width = w; big.height = h;
    const bg = big.getContext('2d');
    bg.filter = 'blur(4px)';
    bg.drawImage(c2, 0, 0, w, h);
    return big;
  })();
  const lightTile = (() => {
    const c2 = document.createElement('canvas');
    c2.width = w; c2.height = h;
    const g2 = c2.getContext('2d');
    g2.fillStyle = '#faf7ef'; g2.fillRect(0, 0, w, h);
    g2.drawImage(lightShade, 0, 0);
    g2.drawImage(inkCv, 0, 0);
    return c2;
  })();

  const tiles = {
    'light shading': lightTile,
    'sketch only': (() => { const c2 = document.createElement('canvas'); c2.width = w; c2.height = h;
      const g2 = c2.getContext('2d'); g2.fillStyle = '#faf7ef'; g2.fillRect(0, 0, w, h);
      g2.drawImage(inkCv, 0, 0); return c2; })(),
    'juno cutout': compose(false),
    'juno + flat tone': compose(true),
  };

  const pad = 8, labelH = 24;
  const names = Object.keys(tiles);
  const sheet = document.createElement('canvas');
  sheet.width = names.length * (w + pad) + pad;
  sheet.height = h + pad * 2 + labelH;
  const g = sheet.getContext('2d');
  g.fillStyle = '#242424'; g.fillRect(0, 0, sheet.width, sheet.height);
  names.forEach((name, i) => {
    const x = pad + i * (w + pad);
    g.drawImage(tiles[name], x, pad);
    g.fillStyle = '#fff'; g.font = 'bold 16px sans-serif';
    g.fillText(name + '  (subject ' + Math.round(maskFrac * 100) + '%)', x, pad + h + 18);
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
            page.on("console", lambda m2: print("[console]", m2.text))
            page.on("pageerror", lambda e: print("[pageerror]", e))
            page.goto(f"http://127.0.0.1:{port}/docs/index.html")
            page.set_content("<body></body>")
            page.add_script_tag(url=f"http://127.0.0.1:{port}/docs/ort.min.js")
            page.add_script_tag(content=STYLES_JS)
            page.add_script_tag(content=CLAHE_JS)
            page.evaluate(f"async (a) => ({SETUP_JS})(a)", f"http://127.0.0.1:{port}/")
            style = page.evaluate(f"() => ({APP_STYLE})")
            for path in sys.argv[2:]:
                b64 = base64.b64encode(open(path, "rb").read()).decode()
                url = page.evaluate(f"async (a) => ({RENDER_JS})(a)", [b64, style])
                out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + "_juno.jpg")
                open(out, "wb").write(base64.b64decode(url.split(",", 1)[1]))
                print(path, "->", out)
            b.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
