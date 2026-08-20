#!/usr/bin/env python3
"""Replay real phone footage through the app and trace the follow-the-hand
feature: which gates fire, whether it engages, how far it zooms.

The recording becomes the camera (HTMLVideoElement.captureStream before the
journey reaches Trace), the journey runs the real screens (daisy -> crop ->
Original style, instant -> print step -> AR), then follow is enabled and
sampled 4x/second for the footage duration.

Usage: .venv/bin/python test/follow_replay.py path/to/footage.mov [seconds]
"""
import http.server, os, socket, subprocess, sys, threading
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    return port


def transcode(src, dst):
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-preset", "veryfast", "-crf", "20", dst],
        check=True, capture_output=True, text=True)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = os.path.abspath(sys.argv[1])
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 18
    footage = os.path.join(HERE, "_replay_footage.mp4")
    print("transcoding to H.264...")
    transcode(src, footage)

    port = free_port()
    os.chdir(ROOT)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=[
                "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                "--autoplay-policy=no-user-gesture-required"])
            page = b.new_page(viewport={"width": 390, "height": 844},
                              device_scale_factor=2, has_touch=True, is_mobile=True)
            page.on("pageerror", lambda e: print("[pageerror]", e))
            page.goto(f"http://127.0.0.1:{port}/docs/index.html")

            page.evaluate(
                """(url) => new Promise((resolve, reject) => {
                    const v = document.createElement('video');
                    v.src = url; v.muted = true; v.playsInline = true; v.loop = true;
                    v.addEventListener('loadedmetadata', () => {
                        v.play().then(() => {
                            navigator.mediaDevices.getUserMedia = async () => v.captureStream();
                            window.__replayVideo = v;
                            resolve(1);
                        }).catch((e) => reject(String(e)));
                    });
                    v.addEventListener('error', () => reject(String(v.error && v.error.message)));
                })""",
                f"http://127.0.0.1:{port}/test/_replay_footage.mp4")

            # journey: daisy -> crop -> Original (instant style) -> trace
            page.wait_for_selector("#pick h1", state="visible", timeout=9000)
            page.click('#lib button[data-key="daisy"]')
            page.wait_for_selector("#crop.on", timeout=9000)
            page.click("#btnCropUse")
            page.wait_for_selector("#stylepick h1", state="visible", timeout=9000)
            page.click('.styleTile:has-text("Original")')
            page.wait_for_timeout(400)
            page.click("#btnStyleNext")
            page.click("#btnTrace")
            page.wait_for_timeout(2500)

            lock = page.evaluate(
                "() => ({ size: markerMap.size, preset: markerMap.presetAdopted,"
                " pose: !!state.pose, status: document.getElementById('status').textContent })")
            print("tracking after 2.5s:", lock)

            page.evaluate("() => { state.followHand = true;"
                          " document.getElementById('followHand').checked = true; }")

            samples = page.evaluate(f"""async () => {{
              const out = [];
              const t0 = performance.now();
              while (performance.now() - t0 < {seconds * 1000}) {{
                await new Promise(r => setTimeout(r, 250));
                out.push({{
                  t: +((performance.now() - t0) / 1000).toFixed(2),
                  zoom: +state.camZoom.toFixed(2),
                  pan: +Math.hypot(state.camPanX, state.camPanY).toFixed(0),
                  gate: followDbg.gate,
                  global: +followDbg.global.toFixed(1),
                  frac: +followDbg.frac.toFixed(4),
                  targets: followDbg.targets,
                  pose: !!state.pose,
                }});
              }}
              return out;
            }}""")

            gates = {}
            max_zoom = 1.0
            for r in samples:
                gates[r["gate"]] = gates.get(r["gate"], 0) + 1
                max_zoom = max(max_zoom, r["zoom"])
            print("\ngate histogram:", dict(sorted(gates.items(), key=lambda kv: -kv[1])))
            print(f"max zoom reached: {max_zoom:.2f}, targets accepted: {samples[-1]['targets']}")
            print("\n t     zoom  pan   gate             global  frac    pose")
            for r in samples[::2]:
                print(f" {r['t']:5.1f} {r['zoom']:5.2f} {r['pan']:4.0f}  "
                      f"{r['gate']:16s} {r['global']:6.1f} {r['frac']:.4f}  {r['pose']}")

            page.screenshot(path=os.path.join(ROOT, "build", "follow_replay_end.png"))
            b.close()
    finally:
        httpd.shutdown()
        if os.path.exists(footage):
            os.remove(footage)


if __name__ == "__main__":
    main()
