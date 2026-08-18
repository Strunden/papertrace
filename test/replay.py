#!/usr/bin/env python3
"""Replay a recorded video through the real, unmodified app - headless, no
camera, no phone, no person needed. The footage is fed in as a fake camera
stream via HTMLVideoElement.captureStream(), so the app's actual detection
Worker, tracker.js registration logic and debug logger run exactly as they
would on a phone; the only difference is where the pixels come from.

iPhones default to HEVC (.mov), which headless Chromium can't decode, so the
footage is always transcoded to H.264 first via ffmpeg - record however you
like and hand this the raw file.

Usage:
    .venv/bin/python test/replay.py path/to/footage.mov [max_seconds]

Prints the same debug log the in-app "Save debug log" button produces, plus
a screenshot of the last frame (tags in view, coloured by known/unknown).
"""
import http.server, os, shutil, socket, subprocess, sys, threading
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def transcode(src, dst):
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-preset", "veryfast", "-crf", "20", dst],
        check=True, capture_output=True, text=True,
    )


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = os.path.abspath(sys.argv[1])
    if not os.path.exists(src):
        print("no such file:", src)
        sys.exit(1)
    max_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60

    footage = os.path.join(HERE, "_replay_footage.mp4")
    print("transcoding to H.264 (headless Chromium can't decode iPhone HEVC)...")
    try:
        transcode(src, footage)
    except FileNotFoundError:
        print("ffmpeg not found - install it (brew install ffmpeg) and retry")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print("ffmpeg failed:\n" + e.stderr[-2000:])
        sys.exit(1)

    port = free_port()
    os.chdir(ROOT)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    footage_url = f"http://127.0.0.1:{port}/test/_replay_footage.mp4"
    app_url = f"http://127.0.0.1:{port}/docs/index.html"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("pageerror", lambda e: print("[pageerror]", e))
            page.goto(app_url)

            # Feed the recording in as a fake getUserMedia stream, installed
            # before Start camera is clicked. The app never knows the
            # difference between this and a live phone camera.
            meta = page.evaluate(
                """(url) => new Promise((resolve, reject) => {
                    const v = document.createElement('video');
                    v.src = url; v.muted = true; v.playsInline = true; v.loop = true;
                    v.addEventListener('loadedmetadata', () => {
                        v.play().then(() => {
                            navigator.mediaDevices.getUserMedia = async () => v.captureStream();
                            window.__replayVideo = v;
                            resolve({ duration: v.duration, w: v.videoWidth, h: v.videoHeight });
                        }).catch((e) => reject(String(e)));
                    });
                    v.addEventListener('error', () => reject(String(v.error && v.error.message)));
                })""",
                footage_url,
            )
            print(f"footage: {meta['w']}x{meta['h']}, {meta['duration']:.1f}s"
                  f" (looped up to {max_seconds:.0f}s of playback)")

            page.click("#btnStart")
            page.wait_for_timeout(min(max(meta["duration"], 1), max_seconds) * 1000 + 1500)

            log = page.evaluate("() => dbgLog.join('\\n')")
            anchored = page.evaluate("() => markerMap.size")
            shot_path = os.path.join(HERE, "_replay_last_frame.png")
            page.screenshot(path=shot_path)
            browser.close()
    finally:
        httpd.shutdown()
        os.remove(footage)

    log_path = os.path.join(HERE, "_replay_log.txt")
    with open(log_path, "w") as f:
        f.write(log + "\n")

    print()
    print(log)
    print()
    print(f"{anchored} tag(s) anchored by end of playback.")
    print(f"full log:   {log_path}")
    print(f"last frame: {shot_path}")


if __name__ == "__main__":
    main()
