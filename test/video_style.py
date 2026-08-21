#!/usr/bin/env python3
"""Run a video through a PaperTrace neural style frame by frame -> animation.

Reproduces the app's exact Artist-sketch pipeline per frame: CLAHE
enhancement (extracted verbatim from app.js), 512px snap-4 NCHW 0-1 input,
lineart.onnx via the same onnxruntime-web build the app ships, then
applyStyle() from src/styles.js compositing the ink map onto warm paper.

Usage: .venv/bin/python test/video_style.py footage.mov [fps] [preset]
Writes build/sketch_anim.mp4.
"""
import http.server, os, re, socket, subprocess, sys, threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STYLES_JS = open(os.path.join(ROOT, "src", "styles.js")).read()
APP_JS = open(os.path.join(ROOT, "src", "app.js")).read()
# claheEnhance is defined in app.js; lift it verbatim so the input prep is
# identical to the app's
m = re.search(r"function claheEnhance\(.*?\n\}\n", APP_JS, re.S)
CLAHE_JS = m.group(0)

PAGE_JS = """
async ([base, preset]) => {
  const CFG = {
    artist: { file: 'lineart.onnx', snap: 4, layout: 'nchw', norm: '01', out: 'ink', enhance: true },
    rough:  { file: 'rough.onnx',   snap: 4, layout: 'nchw', norm: '01', out: 'ink', enhance: true },
  }[preset];
  ort.env.wasm.wasmPaths = base;
  ort.env.wasm.numThreads = 1;
  const model = await (await fetch(base + CFG.file)).arrayBuffer();
  window.__session = await ort.InferenceSession.create(model, { executionProviders: ['wasm'] });
  window.__cfg = CFG;

  const v = document.createElement('video');
  v.src = '/test/_replay_footage.mp4'; v.muted = true;
  await new Promise(r => { v.onloadedmetadata = r; });
  window.__video = v;
  return { dur: v.duration, w: v.videoWidth, h: v.videoHeight };
}
"""

FRAME_JS = """
async ([t, styleDefaults]) => {
  const v = window.__video, cfg = window.__cfg, session = window.__session;
  await new Promise(r => { v.onseeked = r; v.currentTime = t; });
  // app-resolution source (MAX_SOURCE = 1000)
  const k0 = Math.min(1, 1000 / Math.max(v.videoWidth, v.videoHeight));
  const w = Math.round(v.videoWidth * k0), h = Math.round(v.videoHeight * k0);
  const src = document.createElement('canvas');
  src.width = w; src.height = h;
  const sctx = src.getContext('2d', { willReadFrequently: true });
  sctx.drawImage(v, 0, 0, w, h);

  // nnPrepInput, as in app.js
  const k = Math.min(1, 512 / Math.max(w, h));
  const nw = Math.max(cfg.snap, Math.round(w * k / cfg.snap) * cfg.snap);
  const nh = Math.max(cfg.snap, Math.round(h * k / cfg.snap) * cfg.snap);
  const c = document.createElement('canvas');
  c.width = nw; c.height = nh;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(src, 0, 0, nw, nh);
  let img = ctx.getImageData(0, 0, nw, nh);
  if (cfg.enhance) img = claheEnhance(img, 2.2, 8);
  const d = img.data, npx = nw * nh;
  const input = new Float32Array(npx * 3);
  for (let i = 0; i < npx; i++) {
    input[i] = d[i * 4] / 255;
    input[npx + i] = d[i * 4 + 1] / 255;
    input[2 * npx + i] = d[i * 4 + 2] / 255;
  }
  const feeds = {};
  feeds[session.inputNames[0]] = new ort.Tensor('float32', input, [1, 3, nh, nw]);
  const res = await session.run(feeds);
  const out = res[session.outputNames[0]];
  const ink = new Float32Array(out.data.length);
  for (let i = 0; i < ink.length; i++) ink[i] = 1 - Math.max(0, Math.min(1, out.data[i]));
  const map = { data: ink, w: out.dims[3], h: out.dims[2] };

  const frame = sctx.getImageData(0, 0, w, h);
  const styled = applyStyle(frame, Object.assign({}, styleDefaults,
    { preset: window.__preset, neuralMaps: { [window.__preset]: map } }));
  const outCv = document.createElement('canvas');
  outCv.width = w; outCv.height = h;
  const oc = outCv.getContext('2d');
  oc.fillStyle = '#f6f1e4';                       // warm paper
  oc.fillRect(0, 0, w, h);
  const tmp = document.createElement('canvas');
  tmp.width = w; tmp.height = h;
  tmp.getContext('2d').putImageData(styled, 0, 0);
  oc.drawImage(tmp, 0, 0);
  return outCv.toDataURL('image/jpeg', 0.92);
}
"""

APP_STYLE = ("{ detail: 0.85, threshold: 0.18, thickness: 0.15,"
             " invert: false, knockWhite: true, colour: [16, 16, 20] }")


def main():
    src = os.path.abspath(sys.argv[1])
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 15
    preset = sys.argv[3] if len(sys.argv) > 3 else "artist"
    max_frames = int(sys.argv[4]) if len(sys.argv) > 4 else 10 ** 6
    footage = os.path.join(HERE, "_replay_footage.mp4")
    print("transcoding to H.264...")
    subprocess.run(["ffmpeg", "-y", "-i", src, "-an", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "18",
                    footage], check=True, capture_output=True)

    frames_dir = os.path.join(ROOT, "build", "_anim_frames")
    os.makedirs(frames_dir, exist_ok=True)
    for f in os.listdir(frames_dir):
        os.remove(os.path.join(frames_dir, f))

    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    os.chdir(ROOT)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    import base64, time
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            page = b.new_page()
            page.on("pageerror", lambda e: print("[pageerror]", e))
            page.goto(f"http://127.0.0.1:{port}/test/_hand_proof.html")  # any served page
            page.set_content("<body></body>")
            page.add_script_tag(url=f"http://127.0.0.1:{port}/docs/ort.min.js")
            page.add_script_tag(content=STYLES_JS)
            page.add_script_tag(content=CLAHE_JS)
            page.evaluate(f"window.__preset = '{preset}'")
            meta = page.evaluate(f"async (a) => ({PAGE_JS})(a)",
                                 [f"http://127.0.0.1:{port}/docs/", preset])
            n = min(max_frames, int(meta["dur"] * fps))
            print(f"{meta['dur']:.1f}s of footage -> {n} frames at {fps} fps ({preset})")
            style = page.evaluate(f"() => ({APP_STYLE})")
            t_start = time.time()
            for i in range(n):
                url = page.evaluate(f"async (a) => ({FRAME_JS})(a)", [i / fps, style])
                with open(os.path.join(frames_dir, f"f{i:05d}.jpg"), "wb") as f:
                    f.write(base64.b64decode(url.split(",", 1)[1]))
                if i % 25 == 0:
                    el = time.time() - t_start
                    eta = el / max(1, i) * (n - i)
                    print(f"  {i}/{n}  ({el:.0f}s elapsed, ~{eta:.0f}s left)")
            b.close()
    finally:
        httpd.shutdown()
        os.remove(footage)

    out = os.path.join(ROOT, "build", "sketch_anim.mp4")
    subprocess.run(["ffmpeg", "-y", "-framerate", str(fps),
                    "-i", os.path.join(frames_dir, "f%05d.jpg"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out],
                   check=True, capture_output=True)
    print("video:", out)


if __name__ == "__main__":
    main()
