#!/usr/bin/env python3
"""End-to-end test of dist/papertrace.html.

getUserMedia is replaced with a canvas stream showing a synthetic desk: a sheet
of paper with printed tags on it, moving as if hand-held. That exercises the real
pipeline - camera element, detector, map, WebGL overlay and UI - in one go.
"""
import json, os, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "papertrace.html")
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
    const k = 1.0 + 0.06 * Math.sin(t * 0.7);
    const rot = 0.10 * Math.sin(t * 0.5);
    const shear = 0.05 * Math.sin(t * 0.37);
    cx.setTransform(k * Math.cos(rot), k * Math.sin(rot),
                    -k * Math.sin(rot) + shear, k * Math.cos(rot),
                    W / 2 + 26 * Math.sin(t * 0.6), H / 2 + 18 * Math.cos(t * 0.45));
    cx.fillStyle = '#f4f1e8';                        // the sheet
    cx.fillRect(-330, -250, 660, 500);
    for (const tag of TAGS) drawTag(tag);
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


def run(headed=False):
    failures, notes = [], []
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

        page.add_init_script(f"({FAKE_CAMERA})({json.dumps(DICT)})")
        page.goto("file://" + DIST)
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

        check("start screen visible", page.is_visible("#start h1"))
        check("no boot errors", not errors, "; ".join(errors[:3]))

        # ---------------------------------------------------------- camera
        page.click("#btnStart")
        page.wait_for_timeout(1200)
        vw = page.evaluate("() => document.getElementById('video').videoWidth")
        check("camera feed running", vw == 960, f"videoWidth={vw}")

        # Let the map settle over a few seconds of simulated motion.
        page.wait_for_timeout(3500)
        status = page.inner_text("#status")
        mapsize = page.evaluate("() => markerMap.size")
        check("tags detected and anchored", mapsize >= 4, f"{mapsize}/5 anchored, status '{status}'")
        check("reports a lock", "locked" in status, f"status '{status}'")

        pose = page.evaluate("() => !!state.pose")
        check("pose available", pose)

        # ------------------------------------------------------- rendering
        drew = page.evaluate("""() => {
          render();
          const gl = overlay.gl;
          const px = new Uint8Array(gl.canvas.width * gl.canvas.height * 4);
          gl.readPixels(0, 0, gl.canvas.width, gl.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
          let opaque = 0;
          for (let i = 3; i < px.length; i += 4) if (px[i] > 12) opaque++;
          return opaque / (gl.canvas.width * gl.canvas.height);
        }""")
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

        # ------------------------------------------------------------- UI
        open_tab('style')
        for preset in ["Sketch", "Bold outline", "Contour map", "Stencil", "Ghost", "Original"]:
            page.click(f'#presets .chip:has-text("{preset}")')
            page.wait_for_timeout(320)
        page.click('#presets .chip:has-text("Clean lines")')
        page.wait_for_timeout(350)
        check("every style preset applies", not errors, "; ".join(errors[:3]))
        page.screenshot(path=os.path.join(ROOT, "build", "shot_style.png"))

        page.eval_on_selector("#opacity", "e => { e.value = 55; e.dispatchEvent(new Event('input')); }")
        page.eval_on_selector("#threshold", "e => { e.value = 70; e.dispatchEvent(new Event('input')); }")
        page.wait_for_timeout(400)
        check("sliders apply", page.evaluate("() => Math.abs(state.opacity - 0.55) < 0.01"))

        open_tab('image')
        count = page.eval_on_selector_all("#lib button", "els => els.length")
        check("image library populated", count >= 10, f"{count} entries")
        page.click('#lib button[data-key="sunflower"]')
        page.wait_for_timeout(700)
        check("library image loads", page.evaluate("() => state.selected === 'sunflower'"))
        page.screenshot(path=os.path.join(ROOT, "build", "shot_library.png"))

        # --------------------------------------------------- placement
        open_tab('place')
        before = page.evaluate("() => ({ ...state.place })")
        page.mouse.move(215, 430)
        page.mouse.down()
        for i in range(1, 9):
            page.mouse.move(215 + i * 8, 430 + i * 5)
            page.wait_for_timeout(20)
        page.mouse.up()
        page.wait_for_timeout(150)
        after = page.evaluate("() => ({ ...state.place })")
        moved = abs(after["x"] - before["x"]) + abs(after["y"] - before["y"])
        check("drag moves the image", moved > 0.02, f"moved {moved:.3f} paper units")

        page.click("#btnLock")
        page.wait_for_timeout(120)
        locked_before = page.evaluate("() => ({ ...state.place })")
        page.mouse.move(215, 430)
        page.mouse.down()
        page.mouse.move(300, 500)
        page.mouse.up()
        page.wait_for_timeout(150)
        locked_after = page.evaluate("() => ({ ...state.place })")
        check("lock blocks dragging",
              abs(locked_after["x"] - locked_before["x"]) < 1e-9)
        page.click("#btnLock")

        page.click("#btnFit")
        page.wait_for_timeout(120)
        check("fit to tags works", page.evaluate("() => state.place.scale > 0.2"))

        # Regression: freehand uses a different coordinate frame, so it needs its
        # own placement - reusing the tag-map one throws the image off-screen.
        open_tab('tags')
        page.click("#freehand")
        page.wait_for_timeout(700)
        free_ink = page.evaluate("""() => {
          render();
          const gl = overlay.gl;
          const px = new Uint8Array(gl.canvas.width * gl.canvas.height * 4);
          gl.readPixels(0, 0, gl.canvas.width, gl.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
          let opaque = 0;
          for (let i = 3; i < px.length; i += 4) if (px[i] > 12) opaque++;
          return opaque / (gl.canvas.width * gl.canvas.height);
        }""")
        check("freehand keeps the image on screen", free_ink > 0.004,
              f"{free_ink*100:.2f}% of the screen has ink")
        page.click("#freehand")
        page.wait_for_timeout(900)
        check("returning from freehand restores tag anchoring",
              page.evaluate("() => !state.freehand && !!state.pose"))

        # -------------------------------------------------------- tags UI
        open_tab('tags')
        page.wait_for_timeout(700)
        info = page.inner_text("#mapinfo")
        check("tag panel reports the map", "anchored" in info, info)

        page.click("#btnTags2")
        page.wait_for_timeout(500)
        tags = page.eval_on_selector_all("#tagwrap .tagcell", "e => e.length")
        check("printable tag sheet renders", tags == len(DICT["codes"]), f"{tags} tags")
        page.screenshot(path=os.path.join(ROOT, "build", "shot_tags.png"))
        page.click("#printsheet [data-close]")
        page.wait_for_timeout(200)

        # ------------------------------------------------------- recovery
        open_tab('tags')
        # Read the size in the same synchronous turn as the click - a detection
        # frame can (correctly) start re-learning tags before the next timeout.
        after_reset = page.evaluate(
            "() => { document.getElementById('btnReset').click(); return markerMap.size; }")
        check("reset clears the map", after_reset == 0, f"size {after_reset} right after reset")
        page.wait_for_timeout(2500)
        check("re-learns after a reset", page.evaluate("() => markerMap.size") >= 3,
              f"{page.evaluate('() => markerMap.size')} anchored again")

        perf = page.evaluate("() => ({ fps: state.fps, ms: state.detectMs, w: state.detW })")
        notes.append(f"detector {perf['w']}px, {perf['ms']:.1f}ms/frame, {perf['fps']:.0f} fps "
                     f"(software GL in CI - real phones are far faster)")

        check("no errors during the whole run", not errors, "; ".join(errors[:4]))
        b.close()

    for n in notes:
        print("       " + n)
    return failures


if __name__ == "__main__":
    fails = run(headed="--headed" in sys.argv)
    print()
    print("FAILED: " + ", ".join(fails) if fails else "all app checks passed")
    sys.exit(1 if fails else 0)
