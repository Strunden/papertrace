/* ---------------------------------------------------------------------------
 * worker.js - off-main-thread marker detection.
 *
 * Runs inside a Worker (see the detection section of app.js for how it's
 * spun up). Built by concatenating geom.js + aruco.js + this file with the
 * marker dictionary inlined ahead of them - see build.py.
 *
 * Detection is the one per-frame step expensive enough to jank the display:
 * adaptive thresholding and contour tracing over a few hundred thousand
 * pixels, in plain JS. Running it here means a slow frame (fresh markers,
 * dim light) delays the *pose*, not the *paint* - the main thread keeps
 * drawing the camera feed and handling drag/pinch at full frame rate no
 * matter how long detection takes.
 * ------------------------------------------------------------------------- */
const detector = new MarkerDetector(DICT);
const off = new OffscreenCanvas(4, 4);
const ctx = off.getContext('2d', { willReadFrequently: true });
let gray = null;

self.onmessage = (e) => {
  const { bitmap, w, h } = e.data;
  if (off.width !== w || off.height !== h) {
    off.width = w; off.height = h;
    gray = new Uint8ClampedArray(w * h);
  }
  ctx.drawImage(bitmap, 0, 0, w, h);
  bitmap.close();
  const img = ctx.getImageData(0, 0, w, h).data;
  for (let i = 0, j = 0; j < gray.length; i += 4, j++) {
    gray[j] = (img[i] * 77 + img[i + 1] * 150 + img[i + 2] * 29) >> 8;
  }
  const t0 = performance.now();
  const dets = detector.detect(gray, w, h);
  const ms = performance.now() - t0;
  self.postMessage({ dets, ms, w, h });
};
