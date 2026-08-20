#!/usr/bin/env python3
"""Prove browser-side hand tracking (MediaPipe HandLandmarker, the exact
WASM runtime the phone would run) works on real footage BEFORE wiring it
into the app.

Plays the footage in Chromium, runs detectForVideo on every video frame,
draws the skeleton + index fingertip onto a canvas, records that canvas as
an annotated video, and reports detection rate / fingertip jitter / per-
frame latency.

Usage: .venv/bin/python test/hand_proof_web.py path/to/footage.mov
"""
import http.server, os, socket, subprocess, sys, threading
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PAGE = """<!doctype html><body style="margin:0"><canvas id="cv"></canvas><script type="module">
const vision = await import('/test/mp/vision_bundle.mjs');
const files = await vision.FilesetResolver.forVisionTasks('/test/mp');
const lm = await vision.HandLandmarker.createFromOptions(files, {
  baseOptions: { modelAssetPath: '/test/mp/hand_landmarker.task', delegate: 'CPU' },
  runningMode: 'VIDEO', numHands: 1,
  minHandDetectionConfidence: 0.3, minTrackingConfidence: 0.3 });

const v = document.createElement('video');
v.src = '/test/_replay_footage.mp4'; v.muted = true;
await new Promise(r => { v.onloadedmetadata = r; });
const cv = document.getElementById('cv');
cv.width = v.videoWidth / 2; cv.height = v.videoHeight / 2;
const ctx = cv.getContext('2d');

const chunks = [];
const rec = new MediaRecorder(cv.captureStream(30),
  { mimeType: 'video/webm;codecs=vp8', videoBitsPerSecond: 4_000_000 });
rec.ondataavailable = e => chunks.push(e.data);
rec.start(250);

window.__stats = { frames: 0, hits: 0, tips: [], lat: [] };
let ts = 0;
function step() {
  v.requestVideoFrameCallback(() => {
    const t0 = performance.now();
    const res = lm.detectForVideo(v, ts += 33.34);
    window.__stats.lat.push(performance.now() - t0);
    window.__stats.frames++;
    ctx.drawImage(v, 0, 0, cv.width, cv.height);
    if (res.landmarks && res.landmarks.length) {
      window.__stats.hits++;
      const pts = res.landmarks[0];
      ctx.fillStyle = '#ffb300';
      for (const p of pts) {
        ctx.beginPath();
        ctx.arc(p.x * cv.width, p.y * cv.height, 4, 0, 7); ctx.fill();
      }
      const tip = pts[8];
      window.__stats.tips.push([v.currentTime, tip.x, tip.y]);
      ctx.strokeStyle = '#00e676'; ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.arc(tip.x * cv.width, tip.y * cv.height, 14, 0, 7); ctx.stroke();
    } else {
      ctx.fillStyle = '#ff1744'; ctx.font = 'bold 40px sans-serif';
      ctx.fillText('NO HAND', 30, 60);
    }
    if (!v.ended) step();
  });
}
v.play(); step();
v.onended = () => {
  rec.onstop = async () => {
    const buf = await new Blob(chunks).arrayBuffer();
    window.__webm = Array.from(new Uint8Array(buf));
    window.__done = true;
  };
  rec.stop();
};
window.__ready = true;
</script></body>"""


def main():
    src = os.path.abspath(sys.argv[1])
    footage = os.path.join(HERE, "_replay_footage.mp4")
    print("transcoding to H.264...")
    subprocess.run(["ffmpeg", "-y", "-i", src, "-an", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
                    footage], check=True, capture_output=True)
    with open(os.path.join(HERE, "_hand_proof.html"), "w") as f:
        f.write(PAGE)

    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    os.chdir(ROOT)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=[
                "--use-gl=angle", "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--autoplay-policy=no-user-gesture-required"])
            page = b.new_page()
            page.on("pageerror", lambda e: print("[pageerror]", e))
            page.goto(f"http://127.0.0.1:{port}/test/_hand_proof.html")
            page.wait_for_function("() => window.__ready", timeout=120000)
            page.wait_for_function("() => window.__done", timeout=300000)

            st = page.evaluate("() => ({frames: __stats.frames, hits: __stats.hits,"
                               " tips: __stats.tips, lat: __stats.lat})")
            webm = bytes(page.evaluate("() => window.__webm"))
            b.close()
    finally:
        httpd.shutdown()

    raw = os.path.join(ROOT, "build", "_hand_proof.webm")
    with open(raw, "wb") as f:
        f.write(webm)
    out = os.path.join(ROOT, "build", "hand_proof.mp4")
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "23", out],
                   check=True, capture_output=True)
    os.remove(raw)

    import math
    tips, lat = st["tips"], sorted(st["lat"])
    jumps = sorted(math.hypot(b[1] - a[1], b[2] - a[2])
                   for a, b in zip(tips, tips[1:]) if b[0] - a[0] < 0.2)
    print(f"frames: {st['frames']}, hand detected: {st['hits']}"
          f" ({100 * st['hits'] / max(1, st['frames']):.0f}%)")
    if jumps:
        print(f"fingertip inter-frame jump (frac of frame): median"
              f" {jumps[len(jumps) // 2]:.4f}, p95 {jumps[int(len(jumps) * .95)]:.4f}")
    print(f"detect latency ms (desktop CPU wasm): median {lat[len(lat) // 2]:.0f},"
          f" p95 {lat[int(len(lat) * .95)]:.0f}")
    print("video:", out)


if __name__ == "__main__":
    main()
