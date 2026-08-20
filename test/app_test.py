#!/usr/bin/env python3
"""End-to-end test of docs/index.html against the current UI.

getUserMedia is replaced with a canvas stream showing a synthetic desk: a sheet
of paper with printed tags on it, moving as if hand-held. That exercises the
real pipeline - camera element, detector, map, WebGL overlay and UI - in one
go. The page is served over a local http server (not file://) because the
starter pictures and the neural models are fetch()ed at runtime.

The desk deliberately reuses canvas-preset tag ids (0-4) in a non-canvas
arrangement with per-tag rotations: since the tracker can now take a learned
map over with the known-canvas preset, this doubles as a regression test that
the preset does NOT falsely adopt an unrelated desk.
"""
import http.server, json, os, socket, sys, threading
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
DICT = json.load(open(os.path.join(ROOT, "build", "dictionary.json")))

FAKE_CAMERA = """
(dict) => {
  const W = 960, H = 720;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const cx = cv.getContext('2d');
  const M = dict.modules, N = dict.gridSize;

  const TAGS = [
    { id: 0, x: -230, y: -150, s: 92, a: 0.05 },
    { id: 1, x: 235, y: -155, s: 92, a: -0.6 },
    { id: 2, x: -235, y: 150, s: 92, a: 1.9 },
    { id: 3, x: 230, y: 158, s: 92, a: 2.7 },
    { id: 4, x: 10, y: -175, s: 92, a: 0.9 },
  ];

  function drawTag(t) {
    const code = dict.codes[t.id];
    const m = t.s / M;
    cx.save();
    cx.translate(t.x, t.y);
    cx.rotate(t.a);
    cx.translate(-t.s / 2, -t.s / 2);
    cx.fillStyle = '#fff';
    cx.fillRect(-m * 1.5, -m * 1.5, t.s + m * 3, t.s + m * 3);
    cx.fillStyle = '#111';
    for (let r = 0; r < M; r++) {
      for (let c = 0; c < M; c++) {
        const inner = r > 0 && c > 0 && r < M - 1 && c < M - 1;
        const bit = inner ? (code >> (N * N - 1 - ((r - 1) * N + (c - 1)))) & 1 : 0;
        if (!bit) cx.fillRect(c * m, r * m, m * 1.02, m * 1.02);
      }
    }
    cx.restore();
  }

  let t0 = null;
  function frame(now) {
    if (t0 === null) t0 = now;
    const t = (now - t0) / 1000;
    cx.setTransform(1, 0, 0, 1, 0, 0);
    cx.fillStyle = '#4a4f57';                       // desk
    cx.fillRect(0, 0, W, H);
    // Hand-held wobble: drift, gentle rotation, slow zoom, a little shear.
    // window.__stillCam freezes the mount (the drawing-station scenario).
    if (window.__stillCam) {
      cx.setTransform(1, 0, 0, 1, W / 2, H / 2);
    } else {
      const k = 1.0 + 0.06 * Math.sin(t * 0.7);
      const rot = 0.10 * Math.sin(t * 0.5);
      const shear = 0.05 * Math.sin(t * 0.37);
      cx.setTransform(k * Math.cos(rot), k * Math.sin(rot),
                      -k * Math.sin(rot) + shear, k * Math.cos(rot),
                      W / 2 + 26 * Math.sin(t * 0.6), H / 2 + 18 * Math.cos(t * 0.45));
    }
    cx.fillStyle = '#f4f1e8';                        // the sheet
    cx.fillRect(-330, -250, 660, 500);
    for (const tag of TAGS) drawTag(tag);
    // window.__handBlob: a dark "drawing hand" orbiting slowly on the sheet.
    if (window.__handBlob) {
      const bx = 90 + 55 * Math.cos(t * 2.2), by = 40 + 40 * Math.sin(t * 2.2);
      cx.fillStyle = '#6b5842';
      cx.beginPath();
      cx.ellipse(bx, by, 46, 30, 0.5, 0, Math.PI * 2);
      cx.fill();
      window.__blobPos = { x: bx, y: by };
    }
    // window.__realish: real-sensor dirt - auto-exposure flicker and noise
    // specks. A synthetic-clean feed hides threshold bugs (it did).
    if (window.__realish) {
      cx.setTransform(1, 0, 0, 1, 0, 0);
      cx.fillStyle = (Math.sin(t * 6.3) > 0 ? '#ffffff' : '#000000');
      cx.globalAlpha = 0.015 + 0.02 * Math.abs(Math.sin(t * 5.1));
      cx.fillRect(0, 0, W, H);
      cx.globalAlpha = 0.5;
      for (let i = 0; i < 260; i++) {
        const nx = (Math.sin(i * 127.1 + t * 91.7) * 0.5 + 0.5) * W;
        const ny = (Math.sin(i * 311.7 + t * 57.3) * 0.5 + 0.5) * H;
        cx.fillStyle = (i & 1) ? '#fff' : '#000';
        cx.fillRect(nx, ny, 2, 2);
      }
      cx.globalAlpha = 1;
    }
    window.__frames = (window.__frames || 0) + 1;
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  const stream = cv.captureStream(30);
  window.__fakeStream = stream;
  navigator.mediaDevices = navigator.mediaDevices || {};
  navigator.mediaDevices.getUserMedia = async () => stream;
  Object.defineProperty(window, 'isSecureContext', { get: () => true });
}
"""

INK_FRACTION = """() => {
  render();
  const gl = overlay.gl;
  const px = new Uint8Array(gl.canvas.width * gl.canvas.height * 4);
  gl.readPixels(0, 0, gl.canvas.width, gl.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
  let opaque = 0;
  for (let i = 3; i < px.length; i += 4) if (px[i] > 12) opaque++;
  return opaque / (gl.canvas.width * gl.canvas.height);
}"""


def serve_docs():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=DOCS, **k)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def run(headed=False):
    failures, notes = [], []
    httpd, port = serve_docs()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not headed, args=[
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--autoplay-policy=no-user-gesture-required",
        ])
        page = b.new_page(viewport={"width": 430, "height": 880},
                          device_scale_factor=2, has_touch=True, is_mobile=True)
        errors = []
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console.error: " + m.text)
                if m.type == "error" else None)

        throttle = float(os.environ.get("PT_THROTTLE", "0") or 0)
        if throttle > 1:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
            print(f"[env] CPU throttled {throttle}x (phone-like); timeouts stretched")
            page.set_default_timeout(30000 * throttle)
        page.add_init_script(f"({FAKE_CAMERA})({json.dumps(DICT)})")
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_timeout(600)

        def open_tab(name):
            # selectTab toggles, so clicking an already-open tab would close it.
            cur = page.evaluate("() => openTab")
            if cur != name:
                page.click(f'.tab[data-tab="{name}"]')
                page.wait_for_timeout(260)

        def check(name, ok, detail=""):
            print(f"[{'PASS' if ok else 'FAIL'}] {name:44s} {detail}")
            if not ok:
                failures.append(name)

        check("splash visible", page.is_visible("#start h1"))
        check("hero art drew itself", page.eval_on_selector_all(
            "#heroArt path", "els => els.length") > 10)
        check("no boot errors", not errors, "; ".join(errors[:3]))

        # --------------------------------------- setup: picture, then style
        # The splash auto-advances into the library; the camera does NOT
        # start until the end of the journey.
        try:
            page.wait_for_selector("#pick h1", state="visible", timeout=8000)
            check("splash reveals the picture library", True)
        except Exception:
            check("splash reveals the picture library", False)
        check("camera not started during setup", not page.evaluate("() => state.running"))

        count = page.eval_on_selector_all("#lib button", "els => els.length")
        check("starter library populated", count == 6, f"{count} tiles")
        page.click('#lib button[data-key="daisy"]')
        try:
            page.wait_for_selector("#crop.on", timeout=8000)
            check("crop screen opens", True)
        except Exception:
            check("crop screen opens", False)
        page.click("#btnCropUse")
        page.wait_for_timeout(800)
        check("picture committed", page.evaluate("() => hasPicture"),
              f"srcCanvas {page.evaluate('() => srcCanvas.width')}px")
        check("crop lands on the style screen", page.is_visible("#stylepick h1"))
        check("pending styles show an animated loading tile",
              page.eval_on_selector_all(".tilePh.loading", "els => els.length") >= 1)

        # The artist model auto-applies once its map is computed (local fetch,
        # wasm inference in a worker - give it a while under software GL).
        try:
            page.wait_for_function("() => !!neuralMaps.artist", timeout=90000)
            check("artist map computed", True)
        except Exception:
            check("artist map computed", False, "timed out after 90s")
        page.wait_for_timeout(600)
        check("artist style auto-applied", page.evaluate("() => state.style.preset") == "artist",
              f"preset={page.evaluate('() => state.style.preset')}")

        tiles = page.eval_on_selector_all(".styleTile span", "els => els.map(e => e.textContent)")
        check("style gallery lists all presets",
              tiles[:1] == ["Artist sketch"] and "Ghost" in tiles and "Original" in tiles,
              f"tiles={tiles}")
        for preset in ["Ghost", "Original"]:      # instant, no model needed
            page.click(f'.styleTile:has-text("{preset}")')
            page.wait_for_timeout(350)
        check("instant styles apply", page.evaluate("() => state.style.preset") == "original"
              and not errors, "; ".join(errors[:3]))
        page.screenshot(path=os.path.join(ROOT, "build", "shot_style.png"))

        # ------------------------------------------------------ print step
        page.click("#btnStyleNext")
        page.wait_for_timeout(300)
        check("style continues to the print step", page.is_visible("#printstep h1"))

        # ---------------------------------------------------------- camera
        page.click("#btnTrace")
        page.wait_for_timeout(1500)
        vw = page.evaluate("() => document.getElementById('video').videoWidth")
        check("camera starts on Trace it", vw == 960, f"videoWidth={vw}")

        # Let the map settle over a few seconds of simulated motion.
        page.wait_for_timeout(3500)
        status = page.inner_text("#status")
        mapsize = page.evaluate("() => markerMap.size")
        check("tags detected and anchored", mapsize >= 4, f"{mapsize}/5 anchored, status '{status}'")
        check("reports a lock", "locked" in status, f"status '{status}'")
        check("desk does not falsely adopt the canvas preset",
              not page.evaluate("() => markerMap.presetAdopted"))

        pose = page.evaluate("() => !!state.pose")
        check("pose available", pose)

        # Picture/Style dock buttons reopen the setup screens over the camera.
        page.click("#dockPicture")
        page.wait_for_timeout(300)
        check("dock reopens picture screen", page.is_visible("#pick h1"))
        check("library offers a way back while tracing",
              page.is_visible("#pick [data-close]"))
        page.click("#pick [data-close]")
        page.wait_for_timeout(300)
        check("closing setup returns to camera", not page.is_visible("#pick h1"))

        # ------------------------------------------------------- rendering
        drew = page.evaluate(INK_FRACTION)
        check("overlay actually rendered", drew > 0.002, f"{drew*100:.2f}% of the screen has ink")

        page.screenshot(path=os.path.join(ROOT, "build", "shot_main.png"))

        # Regression: the detector changes resolution to stay inside its frame
        # budget, and the pose is expressed in detection pixels. If the two get
        # out of step the overlay lurches across the screen mid-session.
        jump = page.evaluate("""() => {
          const probe = () => { const M = paperToCss(); return M ? matApply(M, 0, 0) : null; };
          let worst = 0;
          for (const w of [352, 640, 416, 576]) {
            const before = probe();
            setDetW(w);
            const after = probe();
            if (before && after) worst = Math.max(worst, Math.hypot(after[0] - before[0], after[1] - before[1]));
          }
          return worst;
        }""")
        check("resolution changes do not move the overlay", jump < 0.5, f"worst jump {jump:.3f} css px")

        # Regression: the texture is uploaded premultiplied, so the shader must
        # not multiply by alpha a second time - that halves every soft edge and
        # washes the Ghost and Original presets out.
        alpha = page.evaluate("""() => {
          const c = document.createElement('canvas'); c.width = 2; c.height = 1;
          const g2 = c.getContext('2d');
          const d = g2.createImageData(2, 1);
          d.data.set([255, 255, 255, 255, 255, 255, 255, 128]);
          g2.putImageData(d, 0, 0);
          overlay.setTexture(c);
          overlay.clear();
          const gl = overlay.gl, W = gl.canvas.width, H = gl.canvas.height;
          overlay.draw([{x:0,y:0,w:1},{x:W,y:0,w:1},{x:W,y:H,w:1},{x:0,y:H,w:1}], 1.0);
          const a = new Uint8Array(4), b = new Uint8Array(4);
          gl.readPixels((W*0.25)|0, (H*0.5)|0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, a);
          gl.readPixels((W*0.75)|0, (H*0.5)|0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, b);
          return { opaque: Array.from(a), half: Array.from(b) };
        }""")
        # Premultiplied framebuffer: 50% white must read ~128, not ~64.
        ok_a = alpha["opaque"][0] > 245 and alpha["opaque"][3] > 245
        ok_h = 112 < alpha["half"][0] < 145 and 112 < alpha["half"][3] < 145
        check("alpha is not premultiplied twice", ok_a and ok_h,
              f"opaque {alpha['opaque']}, 50% {alpha['half']} (expect ~[128,128,128,128])")
        # Regression: texture coordinates must follow the corners, not the order
        # the triangle strip happens to visit them in - getting that wrong maps
        # the image as a bowtie, mirroring half of it.
        quad = page.evaluate("""() => {
          const c = document.createElement('canvas'); c.width = 2; c.height = 2;
          const g2 = c.getContext('2d');
          const d = g2.createImageData(2, 2);
          d.data.set([255,0,0,255,  0,255,0,255,      // top row: red,   green
                      255,255,0,255, 0,0,255,255]);   // bottom:   yellow, blue
          g2.putImageData(d, 0, 0);
          overlay.setTexture(c);
          overlay.clear();
          const gl = overlay.gl, W = gl.canvas.width, H = gl.canvas.height;
          overlay.draw([{x:0,y:0,w:1},{x:W,y:0,w:1},{x:W,y:H,w:1},{x:0,y:H,w:1}], 1.0);
          const at = (fx, fy) => {
            const p = new Uint8Array(4);
            // readPixels origin is bottom-left, so flip y to talk in screen terms.
            gl.readPixels((W*fx)|0, (H*(1-fy))|0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p);
            return Array.from(p).slice(0, 3);
          };
          return { tl: at(0.25,0.25), tr: at(0.75,0.25), br: at(0.75,0.75), bl: at(0.25,0.75) };
        }""")
        want = {"tl": [255,0,0], "tr": [0,255,0], "br": [0,0,255], "bl": [255,255,0]}
        near = lambda a, b: all(abs(x-y) < 40 for x, y in zip(a, b))
        ok = all(near(quad[k], want[k]) for k in want)
        check("texture is mapped the right way round", ok,
              f"tl={quad['tl']} tr={quad['tr']} br={quad['br']} bl={quad['bl']}")

        page.evaluate("() => restyle(false, false)")
        page.wait_for_timeout(400)

        # --------------------------------------------- camera view gestures
        open_tab('place')
        page.eval_on_selector("#opacity", "e => { e.value = 55; e.dispatchEvent(new Event('input')); }")
        page.wait_for_timeout(300)
        check("opacity slider applies", page.evaluate("() => Math.abs(state.opacity - 0.55) < 0.01"))
        # Pan only means anything once zoomed in - drive the zoom slider first.
        page.eval_on_selector("#camZoom", "e => { e.value = 200; e.dispatchEvent(new Event('input')); }")
        page.wait_for_timeout(200)
        check("camera zoom applies", page.evaluate("() => state.camZoom") == 2.0,
              f"camZoom={page.evaluate('() => state.camZoom')}")

        before = page.evaluate("() => ({ x: state.camPanX, y: state.camPanY })")
        page.mouse.move(215, 300)
        page.mouse.down()
        for i in range(1, 9):
            page.mouse.move(215 + i * 8, 300 + i * 5)
            page.wait_for_timeout(20)
        page.mouse.up()
        page.wait_for_timeout(150)
        after = page.evaluate("() => ({ x: state.camPanX, y: state.camPanY })")
        moved = abs(after["x"] - before["x"]) + abs(after["y"] - before["y"])
        check("drag pans the camera view", moved > 5, f"moved {moved:.1f} px")

        page.click("#btnLock")
        page.wait_for_timeout(120)
        locked_before = page.evaluate("() => ({ x: state.camPanX, y: state.camPanY })")
        page.mouse.move(215, 300)
        page.mouse.down()
        page.mouse.move(300, 380)
        page.mouse.up()
        page.wait_for_timeout(150)
        locked_after = page.evaluate("() => ({ x: state.camPanX, y: state.camPanY })")
        check("lock blocks camera dragging",
              abs(locked_after["x"] - locked_before["x"]) < 1e-9)
        page.click("#btnLock")
        page.eval_on_selector("#camZoom", "e => { e.value = 100; e.dispatchEvent(new Event('input')); }")

        page.click("#btnFit")
        page.wait_for_timeout(120)
        check("fit to tags works", page.evaluate("() => state.place.scale > 0.2"))

        # ------------------------------------------ tracking tools (in View)
        page.eval_on_selector('[data-panel="place"] details:last-of-type', "e => e.open = true")
        page.wait_for_timeout(700)
        info = page.inner_text("#mapinfo")
        check("tracking section reports the map", "anchored" in info, info)

        # Regression: freehand uses a different coordinate frame, so it needs its
        # own placement - reusing the tag-map one throws the image off-screen.
        page.click("#freehand")
        page.wait_for_timeout(700)
        free_ink = page.evaluate(INK_FRACTION)
        check("freehand keeps the image on screen", free_ink > 0.004,
              f"{free_ink*100:.2f}% of the screen has ink")
        page.click("#freehand")
        page.wait_for_timeout(900)
        check("returning from freehand restores tag anchoring",
              page.evaluate("() => !state.freehand && !!state.pose"))

        # ---------------------------------------- recrop responsiveness
        # Regression: with a neural style active, committing a NEW crop used
        # to await that model's inference with the busy overlay hidden -
        # "Use this crop" felt dead for the whole run. It must land on the
        # style screen immediately; the style re-renders when its map lands.
        page.click("#dockPicture")
        page.wait_for_timeout(300)
        page.click('#lib button[data-key="lily"]')
        page.wait_for_selector("#crop.on", timeout=8000)
        # Regression: zoom used to clamp at screen-cover, so a picture could
        # never be scaled down to fit inside the frame. Zoom out and commit -
        # the uncovered margins must come through transparent (they trace as
        # blank paper).
        page.evaluate("() => { cropView.zoom = 0.5; clampCropPan(); renderCrop(); }")
        page.wait_for_timeout(150)
        check("crop can zoom below cover", page.evaluate("() => cropView.zoom") == 0.5)
        import time as _time
        t0 = _time.time()
        page.click("#btnCropUse")
        try:
            page.wait_for_selector("#stylepick h1", state="visible", timeout=3000)
            fast = (_time.time() - t0) < 3.0
        except Exception:
            fast = False
        check("recrop commits without blocking on inference", fast,
              f"{(_time.time()-t0)*1000:.0f}ms to style screen")
        page.wait_for_function("() => !!neuralMaps.artist", timeout=90000)
        page.wait_for_timeout(400)
        check("new picture's artist map re-renders", page.evaluate(
            "() => state.style.preset === 'artist' && outCanvas.width > 1"))
        corner = page.evaluate(
            "() => srcCanvas.getContext('2d').getImageData(1, 1, 1, 1).data[3]")
        check("zoomed-out crop margins are transparent", corner == 0, f"corner alpha {corner}")
        page.click("#btnStyleNext")   # camera already running: straight back to AR
        page.wait_for_timeout(400)
        check("mid-session Continue skips the print step",
              not page.is_visible("#printstep h1") and not page.is_visible("#stylepick h1"))

        # ---------------------------------------------------- recent uploads
        page.evaluate("""async () => {
          const resp = await fetch('/start-dog.jpg');
          const blob = await resp.blob();
          const dt = new DataTransfer();
          dt.items.add(new File([blob], 'mydog.jpg', { type: 'image/jpeg' }));
          const input = document.getElementById('file');
          input.files = dt.files;
          input.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        page.wait_for_selector("#crop.on", timeout=8000)
        page.click("#btnCropUse")
        page.wait_for_selector("#stylepick h1", state="visible", timeout=8000)
        page.click("#btnBackToPick")
        try:
            page.wait_for_selector('#lib button[data-key^="recent"]', timeout=8000)
            check("upload appears as a recent tile", True)
        except Exception:
            check("upload appears as a recent tile", False)
        page.click('#lib button[data-key^="recent"]')
        page.wait_for_selector("#stylepick h1", state="visible", timeout=8000)
        check("recent tile reloads the picture", page.evaluate("() => hasPicture"))
        page.click("#btnStyleNext")
        page.wait_for_timeout(400)

        # ------------------------------------------------------- recovery
        # Read the size in the same synchronous turn as the click - a detection
        # frame can (correctly) start re-learning tags before the next timeout.
        after_reset = page.evaluate(
            "() => { document.getElementById('btnReset').click(); return markerMap.size; }")
        check("reset clears the map", after_reset == 0, f"size {after_reset} right after reset")
        try:
            page.wait_for_function("() => markerMap.size >= 3", timeout=8000)
            relearned = True
        except Exception:
            relearned = False
        check("re-learns after a reset", relearned,
              f"{page.evaluate('() => markerMap.size')} anchored again")

        # ------------------------------------------------- exit trace mode
        # Last, because stopping the fake stream ends it for good.
        page.click("#btnExit")
        page.wait_for_timeout(400)
        check("exit stops the camera", not page.evaluate("() => state.running"))
        check("exit lands on the library", page.is_visible("#pick h1"))
        check("share is offered outside tracing", page.is_visible("#btnShare"))

        perf = page.evaluate("() => ({ fps: state.fps, ms: state.detectMs, w: state.detW })")
        notes.append(f"detector {perf['w']}px, {perf['ms']:.1f}ms/frame, {perf['fps']:.0f} fps "
                     f"(software GL in CI - real phones are far faster)")

        check("no errors during the whole run", not errors, "; ".join(errors[:4]))
        b.close()
    httpd.shutdown()

    for n in notes:
        print("       " + n)
    return failures


if __name__ == "__main__":
    fails = run(headed="--headed" in sys.argv)
    print()
    print("FAILED: " + ", ".join(fails) if fails else "all app checks passed")
    sys.exit(1 if fails else 0)
