import base64, http.server, os, re, socket, sys, threading
from playwright.sync_api import sync_playwright
ROOT = "/Users/frank/Sites/PaperTrace"
STYLES_JS = open(os.path.join(ROOT, "src", "styles.js")).read()
m = re.search(r"function claheEnhance\(.*?\n\}\n", open(os.path.join(ROOT, "src", "app.js")).read(), re.S)
CLAHE_JS = m.group(0)
APP_STYLE = "{ detail: 0.85, threshold: 0.18, thickness: 0.15, invert: false, knockWhite: true, colour: [16, 16, 20] }"

RENDER = """
async ([b64, style]) => {
  const img = new Image(); img.src = 'data:image/jpeg;base64,' + b64; await img.decode();
  const k0 = Math.min(1, 1000 / Math.max(img.width, img.height));
  const w = Math.round(img.width * k0), h = Math.round(img.height * k0);
  const src = document.createElement('canvas'); src.width = w; src.height = h;
  const sctx = src.getContext('2d', { willReadFrequently: true });
  sctx.drawImage(img, 0, 0, w, h);

  // artist lines
  const kk = Math.min(1, 512 / Math.max(w, h));
  const nw = Math.max(4, Math.round(w * kk / 4) * 4), nh = Math.max(4, Math.round(h * kk / 4) * 4);
  const pc = document.createElement('canvas'); pc.width = nw; pc.height = nh;
  const pctx = pc.getContext('2d', { willReadFrequently: true });
  pctx.drawImage(src, 0, 0, nw, nh);
  const pim = claheEnhance(pctx.getImageData(0, 0, nw, nh), 2.2, 8);
  const pd = pim.data, npx = nw * nh;
  const ain = new Float32Array(npx * 3);
  for (let i = 0; i < npx; i++) { ain[i] = pd[i*4]/255; ain[npx+i] = pd[i*4+1]/255; ain[2*npx+i] = pd[i*4+2]/255; }
  const af = {}; af[window.__artist.inputNames[0]] = new ort.Tensor('float32', ain, [1,3,nh,nw]);
  const aout = (await window.__artist.run(af))[window.__artist.outputNames[0]];
  const inkMap = new Float32Array(aout.data.length);
  for (let i = 0; i < inkMap.length; i++) inkMap[i] = 1 - Math.max(0, Math.min(1, aout.data[i]));
  const styled = applyStyle(sctx.getImageData(0,0,w,h), Object.assign({}, style,
    { preset: 'artist', neuralMaps: { artist: { data: inkMap, w: aout.dims[3], h: aout.dims[2] } } }));
  const inkCv = document.createElement('canvas'); inkCv.width = w; inkCv.height = h;
  inkCv.getContext('2d').putImageData(styled, 0, 0);

  // whitebox: NHWC, /127.5-1, dims multiple of 4
  const ww = Math.round(w * Math.min(1, 720 / Math.max(w,h)) / 4) * 4;
  const wh = Math.round(h * Math.min(1, 720 / Math.max(w,h)) / 4) * 4;
  const wc = document.createElement('canvas'); wc.width = ww; wc.height = wh;
  const wg = wc.getContext('2d', { willReadFrequently: true });
  wg.drawImage(src, 0, 0, ww, wh);
  const wd = wg.getImageData(0, 0, ww, wh).data;
  const win = new Float32Array(ww * wh * 3);
  for (let i = 0; i < ww * wh; i++) {
    win[i*3] = wd[i*4]/127.5-1; win[i*3+1] = wd[i*4+1]/127.5-1; win[i*3+2] = wd[i*4+2]/127.5-1;
  }
  const wf = {}; wf[window.__wb.inputNames[0]] = new ort.Tensor('float32', win, [1, wh, ww, 3]);
  const wout = (await window.__wb.run(wf))[window.__wb.outputNames[0]];
  const wim = new ImageData(ww, wh);
  for (let i = 0; i < ww * wh; i++) {
    wim.data[i*4] = Math.max(0, Math.min(255, (wout.data[i*3]+1)*127.5));
    wim.data[i*4+1] = Math.max(0, Math.min(255, (wout.data[i*3+1]+1)*127.5));
    wim.data[i*4+2] = Math.max(0, Math.min(255, (wout.data[i*3+2]+1)*127.5));
    wim.data[i*4+3] = 255;
  }
  const wbCv = document.createElement('canvas'); wbCv.width = ww; wbCv.height = wh;
  wbCv.getContext('2d').putImageData(wim, 0, 0);

  const tiles = {
    'whitebox raw': (g) => { g.drawImage(wbCv, 0, 0, w, h); },
    'whitebox + lines': (g) => {
      g.save(); g.filter = 'saturate(1.05) brightness(1.1)';
      g.drawImage(wbCv, 0, 0, w, h); g.restore();
      g.drawImage(inkCv, 0, 0);
    },
    'whitebox light + lines': (g) => {
      g.save(); g.filter = 'saturate(0.9) brightness(1.25)'; g.globalAlpha = 0.75;
      g.globalCompositeOperation = 'multiply';
      g.drawImage(wbCv, 0, 0, w, h); g.restore();
      g.drawImage(inkCv, 0, 0);
    },
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
    g.save(); g.translate(x, pad);
    g.fillStyle = '#faf7ef'; g.fillRect(0, 0, w, h);
    tiles[name](g); g.restore();
    g.fillStyle = '#fff'; g.font = 'bold 16px sans-serif';
    g.fillText(name, x, pad + h + 18);
  });
  return sheet.toDataURL('image/jpeg', 0.88);
}
"""

os.chdir(ROOT)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page()
    page.on("pageerror", lambda e: print("[pageerror]", e))
    page.on("console", lambda m2: print("[console]", m2.text))
    page.goto(f"http://127.0.0.1:{port}/docs/index.html")
    page.set_content("<body></body>")
    page.add_script_tag(url=f"http://127.0.0.1:{port}/docs/ort.min.js")
    page.add_script_tag(content=STYLES_JS)
    page.add_script_tag(content=CLAHE_JS)
    page.evaluate("""async (base) => {
      ort.env.wasm.wasmPaths = base + 'docs/';
      ort.env.wasm.numThreads = 1;
      const load = async (u) => ort.InferenceSession.create(
        await (await fetch(base + u)).arrayBuffer(), { executionProviders: ['wasm'] });
      window.__artist = await load('docs/lineart.onnx');
      window.__wb = await load('build/colour_models/whitebox.onnx');
      return 1;
    }""", f"http://127.0.0.1:{port}/")
    style = page.evaluate(f"() => ({APP_STYLE})")
    os.makedirs("build/wb_test", exist_ok=True)
    for path in sys.argv[1:]:
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        url = page.evaluate(f"async (a) => ({RENDER})(a)", [b64, style])
        out = os.path.join("build/wb_test", os.path.splitext(os.path.basename(path))[0] + "_wb.jpg")
        open(out, "wb").write(base64.b64decode(url.split(",", 1)[1]))
        print(path, "->", out)
    b.close()
httpd.shutdown()
