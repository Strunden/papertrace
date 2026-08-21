#!/usr/bin/env python3
"""Pen + watercolour look (the user's reference): artist ink lines over a
soft tonal wash. The wash follows VALUE (shadow shapes), not colour
regions - light/mid/dark washes layered like real watercolour, pigment
pooling at wash edges, granulation, wobbled boundaries. Compared against
neural painterly underlays (fast-neural-style, AnimeGAN Hayao).

Usage: .venv/bin/python test/watercolour_sheet.py out_dir img1.jpg [...]
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
  window.__models = {};
  try { window.__models.rain = await load('build/colour_models/rain-princess-9.onnx'); } catch (e) { console.log('rain: ' + e); }
  try { window.__models.hayao = await load('build/colour_models/hayao.onnx'); } catch (e) { console.log('hayao: ' + e); }
  return Object.keys(window.__models);
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

  // ---- ink lines, exact app pipeline
  const prep = () => {
    const snap = 4;
    const k = Math.min(1, 512 / Math.max(w, h));
    const nw = Math.max(snap, Math.round(w * k / snap) * snap);
    const nh = Math.max(snap, Math.round(h * k / snap) * snap);
    const c = document.createElement('canvas');
    c.width = nw; c.height = nh;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(src, 0, 0, nw, nh);
    let im = claheEnhance(ctx.getImageData(0, 0, nw, nh), 2.2, 8);
    const d = im.data, npx = nw * nh;
    const input = new Float32Array(npx * 3);
    for (let i = 0; i < npx; i++) {
      input[i] = d[i * 4] / 255;
      input[npx + i] = d[i * 4 + 1] / 255;
      input[2 * npx + i] = d[i * 4 + 2] / 255;
    }
    return { input, dims: [1, 3, nh, nw] };
  };
  const p = prep();
  const feeds = {};
  feeds[window.__artist.inputNames[0]] = new ort.Tensor('float32', p.input, p.dims);
  const aout = (await window.__artist.run(feeds))[window.__artist.outputNames[0]];
  const ink = new Float32Array(aout.data.length);
  for (let i = 0; i < ink.length; i++) ink[i] = 1 - Math.max(0, Math.min(1, aout.data[i]));
  const styled = applyStyle(sctx.getImageData(0, 0, w, h), Object.assign({}, style,
    { preset: 'artist', neuralMaps: { artist: { data: ink, w: aout.dims[3], h: aout.dims[2] } } }));
  const inkCv = document.createElement('canvas');
  inkCv.width = w; inkCv.height = h;
  inkCv.getContext('2d').putImageData(styled, 0, 0);

  // ---- watercolour wash machinery (deterministic)
  const rnd = (i) => { const x = Math.sin(i * 127.13 + 7.7) * 43758.5453; return x - Math.floor(x); };
  // low-res working luma + hue
  const rw = Math.round(w / 3), rh = Math.round(h / 3);
  const lc = document.createElement('canvas');
  lc.width = rw; lc.height = rh;
  const lg = lc.getContext('2d', { willReadFrequently: true });
  lg.filter = 'blur(3px)';
  lg.drawImage(src, 0, 0, rw, rh);
  const lim = lg.getImageData(0, 0, rw, rh).data;
  const n = rw * rh;
  const luma = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    luma[i] = (0.2126 * lim[i * 4] + 0.7152 * lim[i * 4 + 1] + 0.0722 * lim[i * 4 + 2]) / 255;
  }
  // value noise for wobble + granulation
  const noise = (nx, ny, seed) => {
    const gx = Math.floor(nx), gy = Math.floor(ny);
    const fx = nx - gx, fy = ny - gy;
    const s = (a, b) => rnd((a * 57 + b) * 131 + seed);
    const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    return s(gx, gy) * (1 - u) * (1 - v) + s(gx + 1, gy) * u * (1 - v)
         + s(gx, gy + 1) * (1 - u) * v + s(gx + 1, gy + 1) * u * v;
  };
  const box = (arr, W2, H2, r) => {
    const o = new Float32Array(arr.length);
    for (let y = 0; y < H2; y++) {
      for (let x = 0; x < W2; x++) {
        let s2 = 0, c2 = 0;
        for (let dy = -r; dy <= r; dy += r || 1) {
          for (let dx = -r; dx <= r; dx += r || 1) {
            const xx = x + dx, yy = y + dy;
            if (xx >= 0 && yy >= 0 && xx < W2 && yy < H2) { s2 += arr[yy * W2 + xx]; c2++; }
          }
        }
        o[y * W2 + x] = s2 / c2;
      }
    }
    return o;
  };
  const washLayer = (thresh, alpha, seed) => {
    // wobbled soft membership: how far below the luma threshold, sampled at
    // a noise-displaced position so the wash never aligns exactly with the
    // photo (hand overshoot)
    const m2 = new Float32Array(n);
    for (let y = 0; y < rh; y++) {
      for (let x = 0; x < rw; x++) {
        const ox = (noise(x / 26, y / 26, seed) - 0.5) * 10;
        const oy = (noise(x / 26, y / 26, seed + 9) - 0.5) * 10;
        const xx = Math.max(0, Math.min(rw - 1, Math.round(x + ox)));
        const yy = Math.max(0, Math.min(rh - 1, Math.round(y + oy)));
        const t = (thresh - luma[yy * rw + xx]) / 0.10;
        m2[y * rw + x] = Math.max(0, Math.min(1, t));
      }
    }
    // pigment pools at the wash boundary: edge = |mask - blur(mask)|
    const bl = box(m2, rw, rh, 3);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const edge = Math.abs(m2[i] - bl[i]);
      out[i] = alpha * (m2[i] + 2.6 * edge);
    }
    return out;
  };
  const washCanvas = (tint, tintAmt) => {
    const L = [washLayer(0.82, 0.16, 3), washLayer(0.62, 0.17, 17), washLayer(0.42, 0.20, 31)];
    const im2 = new ImageData(rw, rh);
    for (let i = 0; i < n; i++) {
      const x = i % rw, y = (i / rw) | 0;
      const gran = 0.72 + 0.28 * noise(x / 7, y / 7, 71) + 0.12 * (noise(x / 2.3, y / 2.3, 99) - 0.5);
      let a = (L[0][i] + L[1][i] + L[2][i]) * gran;
      a = Math.min(0.62, a);
      // payne's grey, optionally pulled toward the photo's own hue
      let cr = 92, cg = 104, cb = 122;
      if (tintAmt > 0) {
        const l2 = Math.max(1, lim[i * 4] + lim[i * 4 + 1] + lim[i * 4 + 2]);
        cr = cr * (1 - tintAmt) + 255 * (lim[i * 4] / l2) * 2.6 * tintAmt;
        cg = cg * (1 - tintAmt) + 255 * (lim[i * 4 + 1] / l2) * 2.6 * tintAmt;
        cb = cb * (1 - tintAmt) + 255 * (lim[i * 4 + 2] / l2) * 2.6 * tintAmt;
      }
      im2.data[i * 4] = cr; im2.data[i * 4 + 1] = cg; im2.data[i * 4 + 2] = cb;
      im2.data[i * 4 + 3] = a * 255;
    }
    const c2 = document.createElement('canvas');
    c2.width = rw; c2.height = rh;
    c2.getContext('2d').putImageData(im2, 0, 0);
    return c2;
  };

  // ---- neural underlay helper (any colour model output, washed down)
  const neuralWash = async (sess, kind) => {
    if (!sess) return null;
    try {
      const size = kind === 'rain' ? 224 : 256;
      const c2 = document.createElement('canvas');
      const nw2 = size, nh2 = kind === 'rain' ? 224 : Math.round(h * size / w / 32) * 32 || 32;
      c2.width = nw2; c2.height = nh2;
      const g2 = c2.getContext('2d', { willReadFrequently: true });
      g2.drawImage(src, 0, 0, nw2, nh2);
      const d2 = g2.getImageData(0, 0, nw2, nh2).data;
      const npx = nw2 * nh2;
      const input = new Float32Array(npx * 3);
      if (kind === 'rain') {                       // NCHW 0-255
        for (let i = 0; i < npx; i++) {
          input[i] = d2[i * 4]; input[npx + i] = d2[i * 4 + 1]; input[2 * npx + i] = d2[i * 4 + 2];
        }
      } else {                                     // NHWC -1..1
        for (let i = 0; i < npx; i++) {
          input[i * 3] = d2[i * 4] / 127.5 - 1;
          input[i * 3 + 1] = d2[i * 4 + 1] / 127.5 - 1;
          input[i * 3 + 2] = d2[i * 4 + 2] / 127.5 - 1;
        }
      }
      const f2 = {};
      f2[sess.inputNames[0]] = new ort.Tensor('float32', input,
        kind === 'rain' ? [1, 3, nh2, nw2] : [1, nh2, nw2, 3]);
      const o = (await sess.run(f2))[sess.outputNames[0]];
      const ow = o.dims[kind === 'rain' ? 3 : 2], oh = o.dims[1 === 1 && kind === 'rain' ? 2 : 1];
      const im2 = new ImageData(ow, oh);
      const opx = ow * oh;
      for (let i = 0; i < opx; i++) {
        let r, g3, b2;
        if (kind === 'rain') { r = o.data[i]; g3 = o.data[opx + i]; b2 = o.data[2 * opx + i]; }
        else { r = (o.data[i * 3] + 1) * 127.5; g3 = (o.data[i * 3 + 1] + 1) * 127.5; b2 = (o.data[i * 3 + 2] + 1) * 127.5; }
        im2.data[i * 4] = Math.max(0, Math.min(255, r));
        im2.data[i * 4 + 1] = Math.max(0, Math.min(255, g3));
        im2.data[i * 4 + 2] = Math.max(0, Math.min(255, b2));
        im2.data[i * 4 + 3] = 255;
      }
      const oc = document.createElement('canvas');
      oc.width = ow; oc.height = oh;
      oc.getContext('2d').putImageData(im2, 0, 0);
      return oc;
    } catch (e) { console.log(kind + ' failed: ' + e); return null; }
  };
  // ---- the user's sequencing idea: flatten colours FIRST (graphic /
  // vectorised feel), then let something organic apply them to the paper
  const posterCv = (() => {
    const pw2 = Math.round(w / 4), ph2 = Math.round(h / 4);
    const c2 = document.createElement('canvas');
    c2.width = pw2; c2.height = ph2;
    const g2 = c2.getContext('2d', { willReadFrequently: true });
    g2.filter = 'blur(2px) saturate(1.25) brightness(1.08)';
    g2.drawImage(src, 0, 0, pw2, ph2);
    const im2 = g2.getImageData(0, 0, pw2, ph2);
    for (let i = 0; i < im2.data.length; i++) {
      if (i % 4 !== 3) im2.data[i] = Math.round(im2.data[i] / 85) * 85;
    }
    g2.putImageData(im2, 0, 0);
    return c2;
  })();
  // organic application v1: wash physics per flat-colour region - pigment
  // pools where the flat colours meet, wobbled boundaries, granulation
  const posterWashCv = (() => {
    const pc = posterCv.getContext('2d', { willReadFrequently: true });
    const pw2 = posterCv.width, ph2 = posterCv.height;
    const q = pc.getImageData(0, 0, pw2, ph2).data;
    const im2 = new ImageData(pw2, ph2);
    const idx = (x, y) => (Math.max(0, Math.min(ph2 - 1, y)) * pw2 + Math.max(0, Math.min(pw2 - 1, x))) * 4;
    for (let y = 0; y < ph2; y++) {
      for (let x = 0; x < pw2; x++) {
        const ox = Math.round((noise(x / 9, y / 9, 5) - 0.5) * 6);
        const oy = Math.round((noise(x / 9, y / 9, 55) - 0.5) * 6);
        const i4 = idx(x + ox, y + oy);
        const r = q[i4], g3 = q[i4 + 1], b2 = q[i4 + 2];
        // flat-colour boundary? pigment pools there
        let edge = 0;
        for (const [dx, dy] of [[2, 0], [0, 2], [-2, 0], [0, -2]]) {
          const j = idx(x + ox + dx, y + oy + dy);
          edge = Math.max(edge,
            Math.abs(q[j] - r) + Math.abs(q[j + 1] - g3) + Math.abs(q[j + 2] - b2));
        }
        const l = (0.2126 * r + 0.7152 * g3 + 0.0722 * b2) / 255;
        const gran = 0.7 + 0.3 * noise(x / 5, y / 5, 71);
        let a = l > 0.93 ? 0 : (0.34 + Math.min(0.3, edge / 300)) * gran;
        im2.data[(y * pw2 + x) * 4] = r;
        im2.data[(y * pw2 + x) * 4 + 1] = g3;
        im2.data[(y * pw2 + x) * 4 + 2] = b2;
        im2.data[(y * pw2 + x) * 4 + 3] = a * 255;
      }
    }
    const c2 = document.createElement('canvas');
    c2.width = pw2; c2.height = ph2;
    c2.getContext('2d').putImageData(im2, 0, 0);
    return c2;
  })();

  const rainCv = await neuralWash(window.__models.rain, 'rain');
  // organic application v2: the painting model re-renders the FLAT colours
  const srcBackup = src;
  let posterRainCv = null;
  if (window.__models.rain) {
    const big = document.createElement('canvas');
    big.width = w; big.height = h;
    big.getContext('2d').drawImage(posterCv, 0, 0, w, h);
    const swap = sctx.getImageData(0, 0, w, h);
    sctx.drawImage(big, 0, 0);
    posterRainCv = await neuralWash(window.__models.rain, 'rain');
    sctx.putImageData(swap, 0, 0);
  }

  const underlays = {
    'sketch only': (g) => {},
    'watercolour mono': (g) => { g.drawImage(washCanvas(null, 0), 0, 0, w, h); },
    'watercolour tinted': (g) => { g.drawImage(washCanvas(null, 0.5), 0, 0, w, h); },
  };
  underlays['flat colours + wash physics'] = (g) => {
    g.save(); g.globalCompositeOperation = 'multiply';
    g.drawImage(posterWashCv, 0, 0, w, h); g.restore();
  };
  if (posterRainCv) underlays['flat colours -> paint model'] = (g) => {
    g.save(); g.filter = 'saturate(0.85) brightness(1.2) blur(1px)';
    g.globalAlpha = 0.6; g.globalCompositeOperation = 'multiply';
    g.drawImage(posterRainCv, 0, 0, w, h); g.restore();
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
    g.fillStyle = '#faf7ef';
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
            page.on("console", lambda m2: print("[console]", m2.text))
            page.on("pageerror", lambda e: print("[pageerror]", e))
            page.goto(f"http://127.0.0.1:{port}/docs/index.html")
            page.set_content("<body></body>")
            page.add_script_tag(url=f"http://127.0.0.1:{port}/docs/ort.min.js")
            page.add_script_tag(content=STYLES_JS)
            page.add_script_tag(content=CLAHE_JS)
            loaded = page.evaluate(f"async (a) => ({SETUP_JS})(a)", f"http://127.0.0.1:{port}/")
            print("colour models loaded:", loaded)
            style = page.evaluate(f"() => ({APP_STYLE})")
            for path in sys.argv[2:]:
                b64 = base64.b64encode(open(path, "rb").read()).decode()
                url = page.evaluate(f"async (a) => ({RENDER_JS})(a)", [b64, style])
                out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + "_wc.jpg")
                open(out, "wb").write(base64.b64decode(url.split(",", 1)[1]))
                print(path, "->", out)
            b.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
