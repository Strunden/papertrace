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


def stabilize(src_dir, frames_dir):
    """Camera-path smoothing BEFORE styling: per-frame orphan removal and
    temporal medians barely helped (measured: 0.4% ink removed - the noise
    is the whole drawing jittering with the handheld camera, not popping
    strokes). So: accumulate frame-to-frame homographies, smooth the
    trajectory (moving average), warp every source frame onto the smooth
    path and crop in 7% to hide borders. The canvas holds still; only the
    hand moves. Runs on the SOURCE frames so CLAHE + the model see stable
    input too."""
    import cv2
    import numpy as np
    srcs = sorted(os.listdir(src_dir))
    n = len(srcs)
    if n < 3:
        return
    imgs = [cv2.imread(os.path.join(src_dir, f)) for f in srcs]
    grays = [cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) for im in imgs]
    h, w = grays[0].shape

    def homog(a, b):
        pts = cv2.goodFeaturesToTrack(a, 400, 0.01, 12)
        if pts is None or len(pts) < 12:
            return np.eye(3)
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None)
        good = st.reshape(-1) == 1
        if good.sum() < 12:
            return np.eye(3)
        H, _ = cv2.findHomography(pts[good], nxt[good], cv2.RANSAC, 3.0)
        return H if H is not None else np.eye(3)

    # accumulated path: frame i -> frame 0 coordinates
    acc = [np.eye(3)]
    for i in range(1, n):
        acc.append(acc[-1] @ homog(grays[i], grays[i - 1]))
    # smooth the path with a centred moving average (in matrix space - fine
    # for the small inter-frame motions involved)
    R = 6
    out_frames = []
    for i in range(n):
        lo, hi = max(0, i - R), min(n, i + R + 1)
        sm = np.mean(np.stack(acc[lo:hi]), axis=0)
        corr = np.linalg.inv(acc[i]) @ sm      # wait-free: maps i onto smooth path
        corr = np.linalg.inv(corr)
        # centre-crop zoom hides the wandering borders
        z = 1.07
        Z = np.array([[z, 0, w / 2 * (1 - z)], [0, z, h / 2 * (1 - z)], [0, 0, 1]])
        M = Z @ np.linalg.inv(sm) @ acc[i]
        out_frames.append(cv2.warpPerspective(
            imgs[i], M, (w, h), borderMode=cv2.BORDER_REPLICATE))
    for f, im in zip(srcs, out_frames):
        cv2.imwrite(os.path.join(src_dir, f), im, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"stabilized camera path over {n} source frames (smooth radius {R})")

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
    flat:   { file: 'lineart.onnx', snap: 4, layout: 'nchw', norm: '01', out: 'ink', enhance: true, wb: true },
  }[preset];
  ort.env.wasm.wasmPaths = base;
  ort.env.wasm.numThreads = 1;
  const load = async (u) => ort.InferenceSession.create(
    await (await fetch(u)).arrayBuffer(), { executionProviders: ['wasm'] });
  window.__session = await load(base + CFG.file);
  if (CFG.wb) window.__wb = await load(base.replace('/docs/', '/') + 'build/colour_models/whitebox.onnx');
  window.__cfg = CFG;

  // ---- flat-colour helpers (km10 -> whitebox -> km5, the user's pick)
  window.__kmFit = (cv, K) => {
    const g = cv.getContext('2d', { willReadFrequently: true });
    const d = g.getImageData(0, 0, cv.width, cv.height).data;
    const np = cv.width * cv.height;
    const step = Math.max(1, Math.floor(np / 9000));
    const cent = [];
    for (let k = 0; k < K; k++) {
      const i = Math.floor((k + 0.5) / K * np) * 4;
      cent.push([d[i], d[i + 1], d[i + 2]]);
    }
    for (let it = 0; it < 10; it++) {
      const acc = cent.map(() => [0, 0, 0, 0]);
      for (let i = 0; i < np; i += step) {
        const r = d[i * 4], gg = d[i * 4 + 1], b = d[i * 4 + 2];
        let bi = 0, bd = 1e9;
        for (let k = 0; k < K; k++) {
          const dd = (cent[k][0] - r) ** 2 + (cent[k][1] - gg) ** 2 + (cent[k][2] - b) ** 2;
          if (dd < bd) { bd = dd; bi = k; }
        }
        acc[bi][0] += r; acc[bi][1] += gg; acc[bi][2] += b; acc[bi][3]++;
      }
      for (let k = 0; k < K; k++) {
        if (acc[k][3]) cent[k] = [acc[k][0] / acc[k][3], acc[k][1] / acc[k][3], acc[k][2] / acc[k][3]];
      }
    }
    return cent;
  };
  window.__kmAssign = (cv, cent) => {
    const g = cv.getContext('2d', { willReadFrequently: true });
    const im = g.getImageData(0, 0, cv.width, cv.height);
    const d = im.data;
    for (let i = 0; i < cv.width * cv.height; i++) {
      const r = d[i * 4], gg = d[i * 4 + 1], b = d[i * 4 + 2];
      let bi = 0, bd = 1e9;
      for (let k = 0; k < cent.length; k++) {
        const dd = (cent[k][0] - r) ** 2 + (cent[k][1] - gg) ** 2 + (cent[k][2] - b) ** 2;
        if (dd < bd) { bd = dd; bi = k; }
      }
      d[i * 4] = cent[bi][0]; d[i * 4 + 1] = cent[bi][1]; d[i * 4 + 2] = cent[bi][2];
    }
    g.putImageData(im, 0, 0);
    return cv;
  };
  window.__runWb = async (cv) => {
    const k = Math.min(1, 720 / Math.max(cv.width, cv.height));
    const ww = Math.round(cv.width * k / 4) * 4, wh = Math.round(cv.height * k / 4) * 4;
    const c = document.createElement('canvas');
    c.width = ww; c.height = wh;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(cv, 0, 0, ww, wh);
    const d = g.getImageData(0, 0, ww, wh).data;
    const inn = new Float32Array(ww * wh * 3);
    for (let i = 0; i < ww * wh; i++) {
      inn[i * 3] = d[i * 4] / 127.5 - 1;
      inn[i * 3 + 1] = d[i * 4 + 1] / 127.5 - 1;
      inn[i * 3 + 2] = d[i * 4 + 2] / 127.5 - 1;
    }
    const f = {};
    f[window.__wb.inputNames[0]] = new ort.Tensor('float32', inn, [1, wh, ww, 3]);
    const o = (await window.__wb.run(f))[window.__wb.outputNames[0]];
    const oi = new ImageData(ww, wh);
    for (let i = 0; i < ww * wh; i++) {
      oi.data[i * 4] = Math.max(0, Math.min(255, (o.data[i * 3] + 1) * 127.5));
      oi.data[i * 4 + 1] = Math.max(0, Math.min(255, (o.data[i * 3 + 1] + 1) * 127.5));
      oi.data[i * 4 + 2] = Math.max(0, Math.min(255, (o.data[i * 3 + 2] + 1) * 127.5));
      oi.data[i * 4 + 3] = 255;
    }
    const rc = document.createElement('canvas');
    rc.width = ww; rc.height = wh;
    rc.getContext('2d').putImageData(oi, 0, 0);
    return rc;
  };
  return 1;
}
"""

# Lock BOTH palettes to one reference frame - per-frame k-means would swap
# colours between frames (palette flicker).
PALETTE_JS = """
async (refUrls) => {
  // mosaic of frames across the whole clip - one frame biases the palette
  // toward whatever dominates it (a hand in shadow made everything sepia)
  const imgs = [];
  for (const u of refUrls) {
    const im = new Image();
    im.src = u;
    await im.decode();
    imgs.push(im);
  }
  const tw = 480, th = Math.round(imgs[0].height * tw / imgs[0].width);
  const cv = document.createElement('canvas');
  cv.width = tw; cv.height = th * imgs.length;
  const g = cv.getContext('2d');
  imgs.forEach((im, i) => g.drawImage(im, 0, i * th, tw, th));
  window.__pal10 = window.__kmFit(cv, 10);
  const wb = await window.__runWb(window.__kmAssign(cv, window.__pal10));
  window.__pal5 = window.__kmFit(wb, 6);
  return 1;
}
"""

FRAME_JS = """
async ([frameUrl, styleDefaults]) => {
  const cfg = window.__cfg, session = window.__session;
  // The video element's seek loop silently freezes after a while in
  // headless Chromium (delivered the same frame forever) - frames are
  // pre-extracted with ffmpeg and loaded as plain images instead.
  const fimg = new Image();
  fimg.src = frameUrl;
  await fimg.decode();
  const k0 = Math.min(1, 1000 / Math.max(fimg.width, fimg.height));
  const w = Math.round(fimg.width * k0), h = Math.round(fimg.height * k0);
  const src = document.createElement('canvas');
  src.width = w; src.height = h;
  const sctx = src.getContext('2d', { willReadFrequently: true });
  sctx.drawImage(fimg, 0, 0, w, h);

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
    { preset: 'artist', neuralMaps: { artist: map } }));
  const outCv = document.createElement('canvas');
  outCv.width = w; outCv.height = h;
  const oc = outCv.getContext('2d');
  oc.fillStyle = '#f6f1e4';                       // warm paper
  oc.fillRect(0, 0, w, h);
  if (window.__pal10) {
    // km10 -> whitebox -> km5, fixed palettes: flat clean colour under lines
    const wb = await window.__runWb(window.__kmAssign(src, window.__pal10));
    window.__kmAssign(wb, window.__pal5);
    oc.save();
    oc.filter = 'saturate(1.05) brightness(1.16)';
    oc.drawImage(wb, 0, 0, w, h);
    oc.restore();
  }
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
    slice_of = None
    args = sys.argv[1:]
    if "--slice" in args:
        i = args.index("--slice")
        slice_of = (int(args[i + 1]), int(args[i + 2]))
        args = args[:i] + args[i + 3:]
    src = os.path.abspath(args[0])
    fps = float(args[1]) if len(args) > 1 else 15
    preset = args[2] if len(args) > 2 else "artist"
    max_frames = int(args[3]) if len(args) > 3 else 10 ** 6
    workers = int(os.environ.get("VS_WORKERS", str(max(4, (os.cpu_count() or 6) - 1))))
    src_dir = os.path.join(ROOT, "build", "_src_frames")
    frames_dir = os.path.join(ROOT, "build", "_anim_frames")
    if slice_of is None:
        os.makedirs(src_dir, exist_ok=True)
        for f in os.listdir(src_dir):
            os.remove(os.path.join(src_dir, f))
        print("extracting frames...")
        subprocess.run(["ffmpeg", "-y", "-i", src, "-vf", f"fps={fps},scale=1000:-2",
                        "-q:v", "3", os.path.join(src_dir, "s%05d.jpg")],
                       check=True, capture_output=True)
        os.makedirs(frames_dir, exist_ok=True)
        for f in os.listdir(frames_dir):
            os.remove(os.path.join(frames_dir, f))
        stab = os.environ.get("VS_STABILIZE", "1") != "0"
        if stab:
            stabilize(src_dir, frames_dir)
        if workers > 1:
            # one subprocess per slice; each renders frames i % k == w
            procs = [subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), src, str(fps), preset,
                 str(max_frames), "--slice", str(wk), str(workers)])
                for wk in range(workers)]
            fails = [p2.wait() for p2 in procs]
            if any(fails):
                sys.exit("worker failed")
            done = len(os.listdir(frames_dir))
            print(f"workers done: {done} frames")
            stem = os.path.splitext(os.path.basename(src))[0]
            out = os.path.join(ROOT, "build",
                               f"sketch_{stem}_{preset}_{int(fps)}fps"
                               + ("_stab" if stab else "") + ".mp4")
            subprocess.run(["ffmpeg", "-y", "-framerate", str(fps),
                            "-i", os.path.join(frames_dir, "f%05d.jpg"),
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out],
                           check=True, capture_output=True)
            print("video:", out)
            return

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
            page.evaluate(f"async (a) => ({PAGE_JS})(a)",
                          [f"http://127.0.0.1:{port}/docs/", preset])
            srcs = sorted(os.listdir(src_dir))
            n = min(max_frames, len(srcs))
            if preset == "flat":
                refs = [f"http://127.0.0.1:{port}/build/_src_frames/{srcs[int(n * f)]}"
                        for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
                page.evaluate(f"async (a) => ({PALETTE_JS})(a)", refs)
            todo = [i for i in range(n) if slice_of is None or i % slice_of[1] == slice_of[0]]
            print(f"{len(todo)} of {n} frames at {fps} fps ({preset})"
                  + (f" [slice {slice_of[0]}/{slice_of[1]}]" if slice_of else ""))
            style = page.evaluate(f"() => ({APP_STYLE})")
            t_start = time.time()
            for j, i in enumerate(todo):
                furl = f"http://127.0.0.1:{port}/build/_src_frames/{srcs[i]}"
                url = page.evaluate(f"async (a) => ({FRAME_JS})(a)", [furl, style])
                with open(os.path.join(frames_dir, f"f{i:05d}.jpg"), "wb") as f:
                    f.write(base64.b64decode(url.split(",", 1)[1]))
                if j % 25 == 0:
                    el = time.time() - t_start
                    eta = el / max(1, j) * (len(todo) - j)
                    print(f"  {j}/{len(todo)}  ({el:.0f}s elapsed, ~{eta:.0f}s left)")
            b.close()
    finally:
        httpd.shutdown()

    if slice_of is None:
        out = os.path.join(ROOT, "build", "sketch_anim.mp4")
        subprocess.run(["ffmpeg", "-y", "-framerate", str(fps),
                        "-i", os.path.join(frames_dir, "f%05d.jpg"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out],
                       check=True, capture_output=True)
        print("video:", out)


if __name__ == "__main__":
    main()
