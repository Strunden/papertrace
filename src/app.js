/* ---------------------------------------------------------------------------
 * app.js - camera, overlay rendering, gestures and UI.
 * ------------------------------------------------------------------------- */

const DICT = window.__DICT__;
const FLOWERS = window.__FLOWERS__;
const MAX_SOURCE = 1000;          // longest side we style, in px

const $ = (id) => document.getElementById(id);
const el = {
  video: $('video'), gl: $('gl'), hud: $('hud'), touch: $('touch'),
  sheet: $('sheet'), dock: $('dock'), status: $('status'), statusDot: $('statusDot'),
  toast: $('toast'), busy: $('busy'), lib: $('lib'), file: $('file'),
  stylePreviews: $('stylePreviews'), taglist: $('taglist'),
  mapinfo: $('mapinfo'), placeinfo: $('placeinfo'), cropCanvas: $('cropCanvas'),
};

const state = {
  running: false,
  freeze: false,
  mirrored: false,
  camZoom: 1,
  camPanX: 0,
  camPanY: 0,
  locked: false,
  freehand: false,
  showGrid: false,
  gridN: 3,
  opacity: 0.85,
  // 'original' works instantly for everything (the built-in flowers ARE
  // line art); the Artist sketch tile is one tap away for photos.
  style: { preset: 'original' },   // applyStyle carries the tuned defaults
  place: { x: 0, y: 0, scale: 3, scaleY: null, angle: 0, flip: false },
  presetFrame: null,
  placed: false,
  userPlaced: false,
  fitSig: '',
  aspect: 1,
  detW: 480, detH: 360,
  detectMs: 12,
  pose: null,
  poseDetW: 480,
  slowFrames: 0,
  fastFrames: 0,
  lastResult: null,
  selected: null,
  starting: false,
  tagPlace: null,
  freePlace: null,
  fps: 0,
};

const detector = new MarkerDetector(DICT);
const markerMap = new MarkerMap();

/* --------------------------------------------------------------- helpers */
let toastTimer = null;
function toast(msg, ms = 2200) {
  el.toast.textContent = msg;
  el.toast.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('on'), ms);
}
const busy = (on) => el.busy.classList.toggle('on', on);
const raf = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

/* ------------------------------------------------------- WebGL overlay --
 * The reference image has to sit on the paper in true perspective, not just
 * scaled and rotated. Rather than warping pixels on the CPU, the four corners
 * are given the homography's w component directly in gl_Position.w, so the
 * rasteriser's own perspective-correct interpolation does the projective
 * texture mapping for free.
 */
class Overlay {
  constructor(canvas) {
    this.canvas = canvas;
    // Mobile browsers drop the GL context on backgrounding or memory pressure.
    // Without this the tracker keeps running and the status still says
    // "locked", but the picture is simply gone until the page is reloaded.
    canvas.addEventListener('webglcontextlost', (e) => {
      e.preventDefault();
      this.hasTex = false; this.gl = null;
    });
    canvas.addEventListener('webglcontextrestored', () => {
      this.init(this.canvas);
      if (typeof restyle === 'function') restyle(false);
    });
    this.init(canvas);
  }

  init(canvas) {
    const opts = { alpha: true, premultipliedAlpha: true, antialias: true,
                   depth: false, preserveDrawingBuffer: true };
    this.gl = canvas.getContext('webgl', opts) || canvas.getContext('experimental-webgl', opts);
    if (!this.gl) return;
    const gl = this.gl;
    const vs = this.compile(gl.VERTEX_SHADER, `
      attribute vec3 aPos; attribute vec2 aUV; varying vec2 vUV;
      void main() { vUV = aUV; gl_Position = vec4(aPos.xy, 0.0, aPos.z); }`);
    const fs = this.compile(gl.FRAGMENT_SHADER, `
      precision mediump float;
      uniform sampler2D uTex; uniform float uOpacity;
      varying vec2 vUV;
      void main() {
        // The texture is uploaded premultiplied, so rgb already carries alpha.
        // Multiplying by it again here would square it and wash the image out.
        vec4 c = texture2D(uTex, vUV);
        gl_FragColor = vec4(c.rgb * uOpacity, c.a * uOpacity);
      }`);
    const p = gl.createProgram();
    gl.attachShader(p, vs); gl.attachShader(p, fs); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) { this.gl = null; return; }
    gl.useProgram(p);
    this.prog = p;
    this.aPos = gl.getAttribLocation(p, 'aPos');
    this.aUV = gl.getAttribLocation(p, 'aUV');
    this.uOpacity = gl.getUniformLocation(p, 'uOpacity');
    this.buf = gl.createBuffer();
    this.data = new Float32Array(20);
    this.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    this.hasTex = false;
  }

  get ok() { return !!this.gl && !this.gl.isContextLost(); }

  compile(type, src) {
    const gl = this.gl;
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.error(gl.getShaderInfoLog(s));
    }
    return s;
  }

  setTexture(canvas) {
    if (!this.gl) return;
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
    this.hasTex = true;
  }

  clear() {
    if (!this.gl) return;
    this.gl.viewport(0, 0, this.gl.canvas.width, this.gl.canvas.height);
    this.gl.clearColor(0, 0, 0, 0);
    this.gl.clear(this.gl.COLOR_BUFFER_BIT);
  }

  /** @param corners [{x,y,w}] for the unit square's 4 corners, in gl pixels */
  draw(corners, opacity) {
    const gl = this.gl;
    if (!gl || !this.hasTex) return;
    const W = gl.canvas.width, H = gl.canvas.height;
    // Both arrays are indexed by CORNER number (0=TL, 1=TR, 2=BR, 3=BL);
    // `order` is only the sequence a triangle strip needs to visit them in.
    const uv = [[0, 0], [1, 0], [1, 1], [0, 1]];
    const order = [0, 1, 3, 2];                  // triangle strip
    let maxW = 0;
    for (const c of corners) maxW = Math.max(maxW, c.w);
    if (!(maxW > 0)) return;
    for (let k = 0; k < 4; k++) {
      const c = corners[order[k]];
      const ndcX = (c.x / c.w) / W * 2 - 1;
      const ndcY = 1 - (c.y / c.w) / H * 2;
      const w = c.w / maxW;
      this.data[k * 5] = ndcX * w;
      this.data[k * 5 + 1] = ndcY * w;
      this.data[k * 5 + 2] = w;
      this.data[k * 5 + 3] = uv[order[k]][0];
      this.data[k * 5 + 4] = uv[order[k]][1];
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
    gl.bufferData(gl.ARRAY_BUFFER, this.data, gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(this.aPos);
    gl.vertexAttribPointer(this.aPos, 3, gl.FLOAT, false, 20, 0);
    gl.enableVertexAttribArray(this.aUV);
    gl.vertexAttribPointer(this.aUV, 2, gl.FLOAT, false, 20, 12);
    gl.uniform1f(this.uOpacity, opacity);
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}

const overlay = new Overlay(el.gl);
const hudCtx = el.hud.getContext('2d');

/* ------------------------------------------------------------- geometry */
function dpr() { return Math.min(2, window.devicePixelRatio || 1); }

/** Combines pan, digital zoom and the mirror flip into one CSS transform on
 * the video/overlay layers. translate() is listed first (outermost), so
 * camPanX/Y are plain final screen pixels regardless of zoom or mirror -
 * dragging by dx always moves the view by dx on screen. videoToCssMatrix()
 * deliberately does NOT include any of this (see its own comment) - it's
 * display-only, detection and the AR overlay math stay untouched. */
function updateCamTransform() {
  const t = `translate(${state.camPanX}px, ${state.camPanY}px) scale(${state.camZoom})`
    + (state.mirrored ? ' scaleX(-1)' : '');
  el.video.style.transform = t;
  el.gl.style.transform = t;
  el.hud.style.transform = t;
}

/** Camera pan is only meaningful up to the point the zoomed video still
 * covers the viewport - beyond that you'd see empty space past its edge. */
function clampCamPan() {
  const vw = el.video.videoWidth || 1280, vh = el.video.videoHeight || 720;
  const cw = window.innerWidth, ch = window.innerHeight;
  const coverS = Math.max(cw / vw, ch / vh) * state.camZoom;
  const maxPanX = Math.max(0, (vw * coverS - cw) / 2);
  const maxPanY = Math.max(0, (vh * coverS - ch) / 2);
  state.camPanX = Math.max(-maxPanX, Math.min(maxPanX, state.camPanX));
  state.camPanY = Math.max(-maxPanY, Math.min(maxPanY, state.camPanY));
}

function setCamZoom(z) {
  state.camZoom = Math.max(1, Math.min(4, z));
  clampCamPan();
  $('camZoom').value = String(Math.round(state.camZoom * 100));
  $('camZoomOut').textContent = state.camZoom.toFixed(1) + '×';
  updateCamTransform();
}

/** object-fit: cover mapping from video pixels to CSS pixels. Digital zoom
 * (state.camZoom) is applied separately, as a pure CSS transform on the
 * video/overlay layers (see updateCamTransform) - this matrix stays in the
 * real, unzoomed coordinate space that detection and this drawing both
 * still use; only the on-screen presentation is scaled. */
function videoToCssMatrix() {
  const vw = el.video.videoWidth || 1280, vh = el.video.videoHeight || 720;
  const cw = window.innerWidth, ch = window.innerHeight;
  const s = Math.max(cw / vw, ch / vh);
  return new Float64Array([s, 0, (cw - vw * s) / 2, 0, s, (ch - vh * s) / 2, 0, 0, 1]);
}

function paperToCss() {
  const H = state.pose;
  if (!H) return null;
  const vw = el.video.videoWidth || 1280;
  // Use the width the pose was actually measured at, not the current setting -
  // they differ on the frame the detector changes resolution.
  const k = vw / state.poseDetW;                   // detection px -> video px
  const detToVideo = new Float64Array([k, 0, 0, 0, k, 0, 0, 0, 1]);
  return matMul(videoToCssMatrix(), matMul(detToVideo, H));
}

/** Unit square -> the placed image rectangle, in paper units. */
function rectMatrix() {
  const p = state.place;
  const W = p.scale * (p.flip ? -1 : 1);
  // scaleY, when set, stretches independently of the loaded image's own
  // aspect ratio - used to fill a known printed frame exactly (see
  // state.presetFrame) regardless of what picture is loaded into it.
  // Ignoring the picture's aspect ratio otherwise, height tracks width.
  const Hh = p.scaleY != null ? p.scaleY : p.scale / state.aspect;
  const c = Math.cos(p.angle), s = Math.sin(p.angle);
  return new Float64Array([
    c * W, -s * Hh, p.x - 0.5 * (c * W - s * Hh),
    s * W, c * Hh, p.y - 0.5 * (s * W + c * Hh),
    0, 0, 1,
  ]);
}

/* --------------------------------------------------------------- camera */
let stream = null, track = null;

function insecureContext() {
  return !window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia;
}

async function startCamera() {
  if (state.running || state.starting) return;      // double tap on Start
  if (insecureContext()) { showScreen('setup'); return; }
  state.starting = true;
  try { await startCameraInner(); } finally { state.starting = false; }
}

async function startCameraInner() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' },
               width: { ideal: 1920 }, height: { ideal: 1080 } },
    });
  } catch (err) {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    } catch (e2) {
      showScreen('setup');
      $('setupErr').textContent = 'Camera error: ' + (e2 && e2.message ? e2.message : e2);
      $('setupErr').style.display = 'block';
      return;
    }
  }
  el.video.srcObject = stream;
  await el.video.play().catch(() => {});
  track = stream.getVideoTracks()[0];
  const caps = track.getCapabilities ? track.getCapabilities() : {};
  $('btnTorch').disabled = !(caps && caps.torch);
  state.running = true;
  hideScreens();
  resize();
  requestWakeLock();
  loop();
  toast('Point the camera at your tags and sweep across them once');
  const settings = track.getSettings ? track.getSettings() : {};
  dlog(`camera started: ${settings.width}x${settings.height}@${settings.frameRate || '?'}fps `
     + `worker=${!!detWorker} vfc=${useVFC} detW=${state.detW}`);
  enableGyroCoasting();
}

/* --------------------------------------------------------- gyro coasting */
// Vision-only tracking freezes the pose exactly in place during a brief
// motion-blur dropout (see the debug-log work earlier - up to ~2s gaps on a
// real recording). Real IMU fusion (what ARKit does) instead coasts the pose
// using device rotation, which stays informative far longer than
// double-integrated accelerometer position would (that drifts almost
// immediately and isn't used here). This is a hand-rolled, rotation-only
// approximation: gyro can't tell us how the phone translated, only how it
// turned, so it softens "frozen at a stale angle" without pretending to
// replace vision. Resets to zero the instant vision reacquires a real pose,
// so drift never compounds across gaps.
let gyroReady = false;
let lastGyroT = null;
state.gyroRollDelta = 0;

function onDeviceMotion(e) {
  const now = performance.now();
  const dt = lastGyroT == null ? 0 : (now - lastGyroT) / 1000;
  lastGyroT = now;
  // alpha = rotation rate (deg/s) about the axis pointing out of the screen -
  // for a phone held camera-down over paper, that's the same axis the
  // camera's image itself rotates about, i.e. in-plane image rotation.
  const rate = e.rotationRate && e.rotationRate.alpha;
  if (dt > 0 && dt < 0.5 && typeof rate === 'number' && isFinite(rate)) {
    state.gyroRollDelta += rate * dt * Math.PI / 180;
  }
}

async function enableGyroCoasting() {
  if (gyroReady) return;
  try {
    if (typeof DeviceMotionEvent === 'undefined') return;
    if (typeof DeviceMotionEvent.requestPermission === 'function') {
      const perm = await DeviceMotionEvent.requestPermission();
      if (perm !== 'granted') { dlog('gyro coasting: motion permission denied'); return; }
    }
    window.addEventListener('devicemotion', onDeviceMotion);
    gyroReady = true;
    dlog('gyro coasting: enabled');
  } catch (e) {
    dlog('gyro coasting: unavailable (' + e + ')');
  }
}

/** Rotates a paper->image homography's output about its own on-screen
 * centre - used to nudge a held pose along with the phone's own rotation
 * instead of leaving it perfectly static. */
function rotateAboutCenter(H, deltaRad) {
  const quad = REF_SQUARE.map((p) => matApply(H, p[0], p[1]));
  let cx = 0, cy = 0;
  for (const p of quad) { cx += p[0] / 4; cy += p[1] / 4; }
  const c = Math.cos(deltaRad), s = Math.sin(deltaRad);
  const R = new Float64Array([
    c, -s, cx - c * cx + s * cy,
    s, c, cy - s * cx - c * cy,
    0, 0, 1,
  ]);
  return matMul(R, H);
}

let wakeLock = null;
async function requestWakeLock() {
  try {
    if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen');
  } catch (e) { /* not critical */ }
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && state.running) requestWakeLock();
});

/* -------------------------------------------------------------- debug log */
// A local, in-memory flight recorder for "why isn't this tracking" reports.
// Nothing here leaves the device except when the user taps Save debug log -
// it also mirrors to console.debug for anyone who plugs the phone into a Mac
// and opens it in Safari's Develop > [device] remote inspector.
const DBG_MAX = 4000;
const dbgLog = [];
const dbgStart = performance.now();
function dlog(msg) {
  const t = ((performance.now() - dbgStart) / 1000).toFixed(2);
  const line = `[${t}s] ${msg}`;
  dbgLog.push(line);
  if (dbgLog.length > DBG_MAX) dbgLog.shift();
  console.debug('papertrace:', line);
}

function saveDebugLog() {
  const t = trackStats;
  const header = [
    `PaperTrace debug log - ${new Date().toISOString()}`,
    `UA: ${navigator.userAgent}`,
    `worker: ${!!detWorker}  requestVideoFrameCallback: ${useVFC}`,
    `detW: ${state.detW}  markers anchored: ${markerMap.size}`,
    `pose continuity: tracking=${t.frames ? (100 * t.tracking / t.frames).toFixed(0) : 0}% `
      + `holding=${t.frames ? (100 * t.holding / t.frames).toFixed(0) : 0}% `
      + `overlay-gone=${t.frames ? (100 * t.nullPose / t.frames).toFixed(0) : 0}%  `
      + `longest overlay-gone streak: ${t.maxNullMs.toFixed(0)}ms`,
    '',
  ].join('\n');
  const blob = new Blob([header + dbgLog.join('\n') + '\n'], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'papertrace-debug.txt';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast('Saved debug log');
}

/* ------------------------------------------------------------ detection */
// Detection is the one per-frame step expensive enough to jank the display -
// adaptive thresholding and contour tracing over a few hundred thousand
// pixels, in plain JS. Where the browser supports it, it runs in a Worker
// (see worker.js) so a slow frame delays the *pose*, not the *paint*: the
// camera feed and drag/pinch gestures keep moving at full frame rate no
// matter how long a frame takes to decode.
const detCanvas = document.createElement('canvas');
const detCtx = detCanvas.getContext('2d', { willReadFrequently: true });
let grayBuf = null;

let lastDbgSummary = 0;
function logDetectionSummary(stats) {
  const now = performance.now();
  if (now - lastDbgSummary < 1000) return;
  lastDbgSummary = now;
  const res = state.lastResult;
  if (!res) return;
  const vis = (res.visible || []).map((d) => d.id).join(',') || '-';
  const parts = [
    `visible=[${vis}]`,
    `known=${res.known ?? 0}/${markerMap.size}`,
    `detW=${state.detW} detectMs=${state.detectMs.toFixed(1)} fps=${state.fps.toFixed(0)}`,
  ];
  if (stats) {
    parts.push(`raw: contours=${stats.contours} quads=${stats.contours - stats.notQuad - stats.notConvex} `
      + `decoded=${stats.ok} noMatch=${stats.noMatch} badBits=${stats.badBits} tooSmall=${stats.tooSmall}`);
  }
  if (res.reg) {
    const r = res.reg;
    parts.push(`reg: anchorSide=${r.anchorSide.toFixed(0)}px(need ${r.minRegisterSide}) anchorOk=${r.anchorOk} `
      + `cand=${r.candidates} tooSmall=${r.tooSmall} notMoved=${r.notMoved} badFit=${r.badFit} `
      + `converged=${r.converged} keyframes=${r.keyframes}`);
  }
  dlog(parts.join('  ·  '));
}

// Precise pose-continuity accounting, cheap enough to run every frame - the
// 1Hz debug summary samples too coarsely to tell "briefly flickered" from
// "the overlay was actually gone for a second". `nullMs` is what the user
// feels as the overlay vanishing; `holdMs` is a frozen-but-present overlay,
// which is far less jarring even though the map "lost" the tag either way.
const trackStats = { frames: 0, tracking: 0, holding: 0, nullPose: 0,
                     curNullMs: 0, maxNullMs: 0, lastT: null };
function recordTrackStats(res) {
  const now = performance.now();
  const dt = trackStats.lastT == null ? 0 : now - trackStats.lastT;
  trackStats.lastT = now;
  trackStats.frames++;
  if (res.tracking) {
    trackStats.tracking++;
    trackStats.curNullMs = 0;
  } else if (res.holding) {
    trackStats.holding++;
    trackStats.curNullMs = 0;
  } else {
    trackStats.nullPose++;
    trackStats.curNullMs += dt;
    trackStats.maxNullMs = Math.max(trackStats.maxNullMs, trackStats.curNullMs);
  }
}

function applyDetections(dets, ms, detW, stats) {
  const res = markerMap.update(dets);
  state.detectMs = state.detectMs * 0.85 + ms * 0.15;

  state.lastResult = res;
  recordTrackStats(res);
  if (res.H) {
    if (res.tracking) {
      state.pose = res.H;
      state.gyroRollDelta = 0;   // fresh vision pose - any drift since is stale, drop it
    } else {
      // ponytail: gyro-coasting (rotateAboutCenter) is built but disabled -
      // its sign/scale depends on rear-camera sensor mounting convention,
      // which varies by phone and can't be derived reliably from the spec
      // alone. Applying it wrong actively spins the pose during every hold,
      // which reads as worse than the plain freeze below. Re-enable once
      // verified against a real device's debug log (compare predicted vs.
      // actual on-screen rotation direction during a deliberate twist).
      state.pose = res.H;
    }
    state.poseDetW = detW;
  }
  if (!res.H && !res.holding) state.pose = null;
  if (res.presetMatched) {
    toast('Recognized the printed canvas - all ' + res.presetMatched.length + ' tags anchored instantly');
    dlog(`PRESET MATCHED ${res.presetMatched.join(',')}  (${markerMap.size} anchored, no sweep needed)`);
    const preset = PRESET_LAYOUTS.find((p) => p.frame
      && res.presetMatched.every((id) => Object.prototype.hasOwnProperty.call(p.entries, id)));
    if (preset) state.presetFrame = preset.frame;
  } else if (res.registered && res.registered.length) {
    toast('Learned tag ' + res.registered.join(', ') + '  (' + markerMap.size + ' anchored)');
    dlog(`REGISTERED ${res.registered.join(',')}  (${markerMap.size} anchored)`);
  }
  if (res.dropped && res.dropped.length) {
    dlog(`DROPPED ${res.dropped.join(',')}  - kept disagreeing with the rest of the map, will relearn`);
  }
  logDetectionSummary(stats);
  // Until the user positions the image themselves, keep re-fitting as the map
  // grows - the first fit happens off one tag and is necessarily a guess.
  // Trade resolution for speed to stay inside a frame budget - but only after
  // the pressure is sustained, and rescale the tracker's image-space state with
  // it, or the overlay jumps by the ratio between the two resolutions.
  state.slowFrames = state.detectMs > 26 ? state.slowFrames + 1 : 0;
  state.fastFrames = state.detectMs < 11 ? state.fastFrames + 1 : 0;
  if (state.slowFrames > 10 && state.detW > 352) setDetW(state.detW - 64);
  else if (state.fastFrames > 45 && state.detW < 640) setDetW(state.detW + 64);

  if (state.pose && !state.userPlaced) {
    const sig = markerMap.size + ':' + state.aspect.toFixed(3);
    if (sig !== state.fitSig) {
      state.fitSig = sig;
      if (state.presetFrame) fitToFrame(state.presetFrame); else fitToTags();
      state.placed = true;
    }
  }
}

function detectFrameSync(target, dh) {
  detCtx.drawImage(el.video, 0, 0, target, dh);
  const img = detCtx.getImageData(0, 0, target, dh).data;
  const g = grayBuf;
  for (let i = 0, j = 0; j < g.length; i += 4, j++) {
    g[j] = (img[i] * 77 + img[i + 1] * 150 + img[i + 2] * 29) >> 8;
  }
  const t0 = performance.now();
  const dets = detector.detect(g, target, dh);
  const ms = performance.now() - t0;
  applyDetections(dets, ms, target, detector.stats);
}

/* ---- worker offload, with a same-thread fallback if it's unavailable --- */
let detWorker = null, workerBusy = false;
try {
  if (window.__WORKERSRC__ && typeof Worker !== 'undefined' && typeof OffscreenCanvas !== 'undefined') {
    const blobUrl = URL.createObjectURL(new Blob([window.__WORKERSRC__], { type: 'text/javascript' }));
    detWorker = new Worker(blobUrl);
    detWorker.onmessage = (e) => {
      workerBusy = false;
      applyDetections(e.data.dets, e.data.ms, e.data.w, e.data.stats);
    };
    detWorker.onerror = (e) => {
      workerBusy = false; detWorker = null;
      dlog('worker error, falling back to main-thread detection: ' + (e.message || e));
    };
  }
} catch (e) { detWorker = null; }

// requestVideoFrameCallback ties detection to actual new camera frames rather
// than the display's refresh rate, so a phone whose camera negotiates 24-30fps
// isn't re-processing the same frame two or three times over. Falls back to
// running on every render tick where it isn't supported.
const useVFC = typeof el.video.requestVideoFrameCallback === 'function';
let newVideoFrame = true;
if (useVFC) {
  const markFresh = () => { newVideoFrame = true; el.video.requestVideoFrameCallback(markFresh); };
  el.video.requestVideoFrameCallback(markFresh);
}

function detectFrame() {
  if (useVFC) {
    if (!newVideoFrame) return;
    newVideoFrame = false;
  }
  const vw = el.video.videoWidth, vh = el.video.videoHeight;
  if (!vw || !vh) return;
  const target = state.detW;
  const dh = Math.round(target * vh / vw);
  if (detCanvas.width !== target || detCanvas.height !== dh) {
    detCanvas.width = target; detCanvas.height = dh;
    grayBuf = new Uint8ClampedArray(target * dh);
  }
  state.detH = dh;

  if (!detWorker) { detectFrameSync(target, dh); return; }
  if (workerBusy) return;   // previous frame still decoding - skip, keep the last pose
  workerBusy = true;
  createImageBitmap(el.video, { resizeWidth: target, resizeHeight: dh })
    .then((bitmap) => detWorker.postMessage({ bitmap, w: target, h: dh }, [bitmap]))
    .catch(() => { workerBusy = false; });
}

function setDetW(next) {
  const k = next / state.detW;
  state.detW = next;
  state.slowFrames = 0; state.fastFrames = 0;
  markerMap.rescaleImageSpace(k);
  if (state.pose) {
    state.pose = matMul(new Float64Array([k, 0, 0, 0, k, 0, 0, 0, 1]), state.pose);
    state.poseDetW = next;
  }
}

/** Freehand mode pins the overlay to the screen instead of the paper. */
function freehandPose() {
  const w = state.detW, h = state.detH || Math.round(w * 0.75);
  const k = Math.min(w, h) / 5;
  return new Float64Array([k, 0, w / 2, 0, k, h / 2, 0, 0, 1]);
}

/* ------------------------------------------------------------ rendering */
function resize() {
  const d = dpr();
  for (const c of [el.gl, el.hud]) {
    c.width = Math.round(window.innerWidth * d);
    c.height = Math.round(window.innerHeight * d);
  }
}
window.addEventListener('resize', resize);
window.addEventListener('orientationchange', () => setTimeout(resize, 300));

function render() {
  const d = dpr();
  overlay.clear();
  hudCtx.setTransform(1, 0, 0, 1, 0, 0);
  hudCtx.clearRect(0, 0, el.hud.width, el.hud.height);
  hudCtx.scale(d, d);

  const M = paperToCss();
  if (M && overlay.hasTex) {
    const full = matMul(new Float64Array([d, 0, 0, 0, d, 0, 0, 0, 1]),
                        matMul(M, rectMatrix()));
    const corners = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([u, v]) => {
      const r = matApplyRaw(full, u, v);
      return { x: r[0], y: r[1], w: r[2] };
    });
    if (corners.every((c) => c.w > 1e-6)) overlay.draw(corners, state.opacity);
  }
  drawHud(M);
}

function drawHud(M) {
  const res = state.lastResult;
  const c = hudCtx;
  const toCss = (u, v) => matApply(M, u, v);

  if (res && res.visible) {
    const vw = el.video.videoWidth || 1280;
    const V = matMul(videoToCssMatrix(),
                     new Float64Array([vw / state.detW, 0, 0, 0, vw / state.detW, 0, 0, 0, 1]));
    for (const d of res.visible) {
      const known = markerMap.map.has(d.id);
      c.strokeStyle = known ? 'rgba(87,217,163,.9)' : 'rgba(255,200,87,.85)';
      c.lineWidth = 2;
      c.beginPath();
      d.corners.forEach((p, i) => {
        const q = matApply(V, p[0], p[1]);
        i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]);
      });
      c.closePath();
      c.stroke();
      const ctr = matApply(V, (d.corners[0][0] + d.corners[2][0]) / 2,
                              (d.corners[0][1] + d.corners[2][1]) / 2);
      c.fillStyle = known ? 'rgba(87,217,163,.95)' : 'rgba(255,200,87,.95)';
      c.font = '600 11px system-ui';
      c.textAlign = 'center'; c.textBaseline = 'middle';
      // The whole hud canvas gets CSS-mirrored for display when state.mirrored
      // is on, which would draw this text backwards - counter-flip just the
      // glyphs around their own anchor so the number still reads correctly.
      c.save();
      c.translate(ctr[0], ctr[1]);
      if (state.mirrored) c.scale(-1, 1);
      c.fillText(String(d.id), 0, 0);
      c.restore();
    }
  }

  if (!M || !overlay.hasTex) return;
  const R = matMul(M, rectMatrix());
  const quad = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([u, v]) => matApply(R, u, v));

  if (state.showGrid) {
    c.strokeStyle = 'rgba(108,200,255,.45)';
    c.lineWidth = 1;
    for (let i = 1; i < state.gridN; i++) {
      const t = i / state.gridN;
      for (const [a, b] of [[[t, 0], [t, 1]], [[0, t], [1, t]]]) {
        c.beginPath();
        for (let k = 0; k <= 8; k++) {
          const u = a[0] + (b[0] - a[0]) * k / 8, v = a[1] + (b[1] - a[1]) * k / 8;
          const p = matApply(R, u, v);
          k ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]);
        }
        c.stroke();
      }
    }
  }

  if (!state.locked) {
    c.strokeStyle = 'rgba(108,200,255,.9)';
    c.lineWidth = 2;
    c.setLineDash([7, 5]);
    c.beginPath();
    quad.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
    c.closePath(); c.stroke();
    c.setLineDash([]);
    c.fillStyle = 'rgba(108,200,255,.95)';
    for (const p of quad) { c.beginPath(); c.arc(p[0], p[1], 5, 0, 7); c.fill(); }
  }
}

let lastFrame = performance.now(), frames = 0;
function loop() {
  if (!state.running) return;
  if (!state.freeze && document.visibilityState === 'visible') {
    if (state.freehand) {
      state.pose = freehandPose();
      state.poseDetW = state.detW;
      state.lastResult = { visible: [], known: 0, tracking: true };
    } else if (el.video.readyState >= 2) {
      detectFrame();
    }
  }
  render();
  updateStatus();
  frames++;
  const now = performance.now();
  if (now - lastFrame > 700) {
    state.fps = frames * 1000 / (now - lastFrame);
    frames = 0; lastFrame = now;
  }
  requestAnimationFrame(loop);
}

/* ---------------------------------------------------------------- status */
function updateStatus() {
  const r = state.lastResult;
  let cls = 'bad', text = 'Looking for tags';
  if (state.freehand) { cls = 'warn'; text = 'Freehand (no tags)'; }
  else if (!r) { cls = 'bad'; text = 'Starting'; }
  else if (r.tracking && r.known >= 2) { cls = 'good'; text = r.known + ' tags locked'; }
  else if (r.tracking && r.known === 1) { cls = 'warn'; text = '1 tag - show another'; }
  else if (r.holding) { cls = 'warn'; text = 'Holding - tags hidden'; }
  else if (markerMap.size) { cls = 'bad'; text = 'Tags lost'; }
  el.statusDot.className = 'dot ' + cls;
  el.status.textContent = text;
}

/* ------------------------------------------------------------- placement */
function tagBounds() {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, n = 0;
  for (const [, e] of markerMap.map) {
    for (const p of MarkerMap.corners(e)) {
      minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]);
      minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]);
    }
    n++;
  }
  if (!n) return null;
  return { minX, minY, maxX, maxY, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2,
           w: maxX - minX, h: maxY - minY, n };
}

function fitToTags() {
  const b = tagBounds();
  if (!b) {
    Object.assign(state.place, { x: 0, y: 0, scale: 3, scaleY: null, angle: 0 });
  } else if (b.n === 1) {
    Object.assign(state.place, { x: b.cx, y: b.cy + b.h * 1.6, scale: Math.max(2.5, b.w * 3), scaleY: null });
  } else {
    const fit = Math.min(b.w, b.h * state.aspect) * 0.92;
    Object.assign(state.place, { x: b.cx, y: b.cy, scale: Math.max(1, fit), scaleY: null });
  }
  updatePlaceInfo();
}

/**
 * Places the loaded image to exactly fill a known printed frame (see
 * state.presetFrame), stretched to the frame's own shape rather than
 * letterboxed to the image's aspect ratio - the frame is the whole point of
 * the printed canvas sheet, so whatever picture is loaded should fill it
 * exactly, not just roughly overlap it.
 */
function fitToFrame(frame) {
  Object.assign(state.place, {
    x: frame.cx, y: frame.cy, scale: frame.w, scaleY: frame.h, angle: frame.angle, flip: false,
  });
  updatePlaceInfo();
}

function updatePlaceInfo() {
  const p = state.place;
  el.placeinfo.textContent =
    `size ${p.scale.toFixed(2)} tag-widths  ·  rotation ${(p.angle * 180 / Math.PI).toFixed(0)}°`
    + (p.flip ? '  ·  mirrored' : '');
}

/* -------------------------------------------------------------- gestures */
const pointers = new Map();
let gesture = null;

el.touch.addEventListener('pointerdown', (e) => {
  if (state.locked) return;
  el.touch.setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  beginGesture();
});
el.touch.addEventListener('pointermove', (e) => {
  if (!pointers.has(e.pointerId)) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  applyGesture();
});
const endPointer = (e) => {
  if (!pointers.has(e.pointerId)) return;
  pointers.delete(e.pointerId);
  beginGesture();
};
el.touch.addEventListener('pointerup', endPointer);
el.touch.addEventListener('pointercancel', endPointer);

el.cropCanvas.addEventListener('pointerdown', (e) => {
  el.cropCanvas.setPointerCapture(e.pointerId);
  cropPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  beginCropGesture();
});
el.cropCanvas.addEventListener('pointermove', (e) => {
  if (!cropPointers.has(e.pointerId)) return;
  cropPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  applyCropGesture();
});
const endCropPointer = (e) => {
  if (!cropPointers.has(e.pointerId)) return;
  cropPointers.delete(e.pointerId);
  beginCropGesture();
};
el.cropCanvas.addEventListener('pointerup', endCropPointer);
el.cropCanvas.addEventListener('pointercancel', endCropPointer);

// Gestures drive the camera view (pan + zoom), not the traced image - the
// image is placed automatically (fitToFrame/fitToTags) and fine-tuned with
// the Rotate/Upright/Mirror buttons instead. Plain screen-pixel deltas, no
// paper-space math needed: dragging by dx should move the view by dx on
// screen regardless of zoom, mirror, or whether a pose even exists yet.
function beginGesture() {
  gesture = {
    pts: [...pointers.values()].map((p) => ({ x: p.x, y: p.y })),
    zoom: state.camZoom, panX: state.camPanX, panY: state.camPanY,
  };
}

function applyGesture() {
  if (!gesture) return;
  const now = [...pointers.values()].map((p) => ({ x: p.x, y: p.y }));
  if (now.length !== gesture.pts.length) return;
  const g = gesture;

  if (now.length === 1) {
    state.camPanX = g.panX + (now[0].x - g.pts[0].x);
    state.camPanY = g.panY + (now[0].y - g.pts[0].y);
  } else if (now.length >= 2) {
    const [a0, b0] = g.pts, [a1, b1] = now;
    const d0 = Math.hypot(b0.x - a0.x, b0.y - a0.y);
    const d1 = Math.hypot(b1.x - a1.x, b1.y - a1.y);
    if (d0 < 1e-6) return;
    const m0 = { x: (a0.x + b0.x) / 2, y: (a0.y + b0.y) / 2 };
    const m1 = { x: (a1.x + b1.x) / 2, y: (a1.y + b1.y) / 2 };
    state.camZoom = Math.max(1, Math.min(4, g.zoom * (d1 / d0)));
    state.camPanX = g.panX + (m1.x - m0.x);
    state.camPanY = g.panY + (m1.y - m0.y);
  }
  clampCamPan();
  $('camZoom').value = String(Math.round(state.camZoom * 100));
  $('camZoomOut').textContent = state.camZoom.toFixed(1) + '×';
  updateCamTransform();
}

/* ------------------------------------------------------------ the image */
const srcCanvas = document.createElement('canvas');
const srcSmall = document.createElement('canvas');   // half-res, for live sliders
const srcThumb = document.createElement('canvas');   // tiny, for the style-picker gallery
const outCanvas = document.createElement('canvas');

function drawSourceFrom(bitmap, w, h) {
  const k = Math.min(1, MAX_SOURCE / Math.max(w, h));
  srcCanvas.width = Math.max(1, Math.round(w * k));
  srcCanvas.height = Math.max(1, Math.round(h * k));
  const c = srcCanvas.getContext('2d');
  c.clearRect(0, 0, srcCanvas.width, srcCanvas.height);
  c.drawImage(bitmap, 0, 0, srcCanvas.width, srcCanvas.height);
  srcSmall.width = Math.max(1, srcCanvas.width >> 1);
  srcSmall.height = Math.max(1, srcCanvas.height >> 1);
  const cs = srcSmall.getContext('2d');
  cs.clearRect(0, 0, srcSmall.width, srcSmall.height);
  cs.drawImage(bitmap, 0, 0, srcSmall.width, srcSmall.height);
  const kt = Math.min(1, 168 / Math.max(w, h));
  srcThumb.width = Math.max(1, Math.round(w * kt));
  srcThumb.height = Math.max(1, Math.round(h * kt));
  const ct = srcThumb.getContext('2d');
  ct.clearRect(0, 0, srcThumb.width, srcThumb.height);
  ct.drawImage(bitmap, 0, 0, srcThumb.width, srcThumb.height);
  state.aspect = srcCanvas.width / srcCanvas.height;
  neuralMaps = {};               // new picture -> stale neural maps
}

function loadImageEl(src) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = src;
  });
}

/**
 * Picking a new image is a strong, explicit "start over" action - it should
 * always end up placed on the page, regardless of whatever placement state
 * a *previous* image was left in. Without this, restyle()'s own re-fit is
 * indirect and gated behind !state.userPlaced: if the user ever tapped
 * Rotate/Mirror on an earlier image, userPlaced stays true forever and a
 * freshly loaded image silently never gets placed at all - "downstream
 * nothing happens", even though the texture itself loaded fine.
 */
function placeNewImage() {
  state.userPlaced = false;
  state.fitSig = '';
  if (state.pose) {
    if (state.presetFrame) fitToFrame(state.presetFrame); else fitToTags();
    state.placed = true;
  }
}

async function chooseLibrary(key) {
  const f = FLOWERS[key];
  if (!f) return;
  busy(true); await raf();
  try {
    const img = await loadImageEl('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(f.svg));
    drawSourceFrom(img, 1000, 1000);
    state.selected = key;
    markSelected();
    await restyle(true);
    placeNewImage();
    buildStylePreviews();
  } finally { busy(false); }
}

/**
 * createImageBitmap decodes directly from the File - no object URL, no <img>
 * element, and (with imageOrientation) it applies EXIF rotation itself. Phone
 * photos are routinely EXIF-rotated (the pixels are sideways, a tag says how
 * to display them), which <img> handles inconsistently across browsers; this
 * doesn't. Falls back to the old <img>-based path for anything that can't be
 * decoded this way.
 */
async function decodeUserImage(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch (e) { /* fall through to the <img> path below */ }
  }
  const url = URL.createObjectURL(file);
  try {
    return await loadImageEl(url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function loadUserFile(file) {
  busy(true); await raf();
  try {
    const bitmap = await decodeUserImage(file);
    const w = bitmap.naturalWidth || bitmap.width, h = bitmap.naturalHeight || bitmap.height;
    if (!w || !h) throw new Error('decoded image has no size');
    openCrop(bitmap, w, h, file.name);
  } catch (e) {
    toast('Could not read that image - try exporting it as JPEG or PNG');
  } finally {
    busy(false);
  }
}

/* -------------------------------------------------------------- cropping */
// A photo is rarely already framed the way you want to trace it - this lets
// you pan/zoom to the part you actually want before it's committed. Reuses
// the same screen-pixel pan/pinch-zoom pattern as the camera view (see
// beginGesture/applyGesture) for a consistent feel.
let cropSrc = null;             // { bitmap, w, h, name }
let cropView = { zoom: 1, panX: 0, panY: 0 };
let cropGesture = null;

function cropFrameRect() {
  const cw = window.innerWidth, ch = window.innerHeight;
  const aspect = state.presetFrame ? state.presetFrame.w / state.presetFrame.h : 0.8;
  const maxW = cw * 0.86, maxH = ch * 0.62;
  let fw = maxW, fh = fw / aspect;
  if (fh > maxH) { fh = maxH; fw = fh * aspect; }
  return { x: (cw - fw) / 2, y: (ch - fh) / 2 - 18, w: fw, h: fh };
}

function cropBaseScale() {
  const cw = window.innerWidth, ch = window.innerHeight;
  return Math.max(cw / cropSrc.w, ch / cropSrc.h);
}

function clampCropPan() {
  const cw = window.innerWidth, ch = window.innerHeight;
  const s = cropBaseScale() * cropView.zoom;
  const frame = cropFrameRect();
  // The frame must stay fully inside the (scaled) image on every side.
  const maxPanX = Math.max(0, (cropSrc.w * s - frame.w) / 2 - (cw / 2 - frame.x - frame.w / 2));
  const maxPanY = Math.max(0, (cropSrc.h * s - frame.h) / 2 - (ch / 2 - frame.y - frame.h / 2));
  cropView.panX = Math.max(-maxPanX, Math.min(maxPanX, cropView.panX));
  cropView.panY = Math.max(-maxPanY, Math.min(maxPanY, cropView.panY));
}

function openCrop(bitmap, w, h, name) {
  cropSrc = { bitmap, w, h, name };
  const frame = cropFrameRect();
  // Start zoomed so the frame is already fully covered (cover-fit within the
  // frame, not the whole screen) - an empty/blank crop on open would be
  // confusing, and this is what every other crop tool defaults to.
  const s0 = cropBaseScale();
  const coverFrame = Math.max(frame.w / (cropSrc.w * s0), frame.h / (cropSrc.h * s0));
  cropView = { zoom: Math.max(1, coverFrame), panX: 0, panY: 0 };
  clampCropPan();
  showScreen('crop');
  renderCrop();
}

function renderCrop() {
  const c = el.cropCanvas;
  const d = dpr();
  const cw = window.innerWidth, ch = window.innerHeight;
  c.width = Math.round(cw * d); c.height = Math.round(ch * d);
  const ctx = c.getContext('2d');
  ctx.setTransform(d, 0, 0, d, 0, 0);
  ctx.fillStyle = '#0d0f13';
  ctx.fillRect(0, 0, cw, ch);

  const s = cropBaseScale() * cropView.zoom;
  const dw = cropSrc.w * s, dh = cropSrc.h * s;
  const dx = cw / 2 + cropView.panX - dw / 2, dy = ch / 2 + cropView.panY - dh / 2;
  ctx.drawImage(cropSrc.bitmap, dx, dy, dw, dh);

  // Dim everything outside the crop frame so it's obvious what's kept.
  const f = cropFrameRect();
  ctx.fillStyle = 'rgba(13,15,19,.72)';
  ctx.fillRect(0, 0, cw, f.y);
  ctx.fillRect(0, f.y + f.h, cw, ch - f.y - f.h);
  ctx.fillRect(0, f.y, f.x, f.h);
  ctx.fillRect(f.x + f.w, f.y, cw - f.x - f.w, f.h);
  ctx.strokeStyle = 'rgba(255,255,255,.9)';
  ctx.lineWidth = 2;
  ctx.strokeRect(f.x, f.y, f.w, f.h);
}

const cropPointers = new Map();
function beginCropGesture() {
  cropGesture = {
    pts: [...cropPointers.values()].map((p) => ({ x: p.x, y: p.y })),
    zoom: cropView.zoom, panX: cropView.panX, panY: cropView.panY,
  };
}

function applyCropGesture() {
  if (!cropGesture) return;
  const now = [...cropPointers.values()].map((p) => ({ x: p.x, y: p.y }));
  if (now.length !== cropGesture.pts.length) return;
  const g = cropGesture;
  if (now.length === 1) {
    cropView.panX = g.panX + (now[0].x - g.pts[0].x);
    cropView.panY = g.panY + (now[0].y - g.pts[0].y);
  } else if (now.length >= 2) {
    const [a0, b0] = g.pts, [a1, b1] = now;
    const d0 = Math.hypot(b0.x - a0.x, b0.y - a0.y);
    const d1 = Math.hypot(b1.x - a1.x, b1.y - a1.y);
    if (d0 < 1e-6) return;
    const m0 = { x: (a0.x + b0.x) / 2, y: (a0.y + b0.y) / 2 };
    const m1 = { x: (a1.x + b1.x) / 2, y: (a1.y + b1.y) / 2 };
    cropView.zoom = Math.max(1, Math.min(8, g.zoom * (d1 / d0)));
    cropView.panX = g.panX + (m1.x - m0.x);
    cropView.panY = g.panY + (m1.y - m0.y);
  }
  clampCropPan();
  renderCrop();
}

async function commitCrop() {
  if (!cropSrc) return;
  const s = cropBaseScale() * cropView.zoom;
  const cw = window.innerWidth, ch = window.innerHeight;
  const f = cropFrameRect();
  // Inverse of the draw transform in renderCrop(): screen -> source-bitmap px.
  const sx = (f.x - cw / 2 - cropView.panX) / s + cropSrc.w / 2;
  const sy = (f.y - ch / 2 - cropView.panY) / s + cropSrc.h / 2;
  const sw = f.w / s, sh = f.h / s;

  const out = document.createElement('canvas');
  out.width = Math.max(1, Math.round(sw));
  out.height = Math.max(1, Math.round(sh));
  out.getContext('2d').drawImage(cropSrc.bitmap, sx, sy, sw, sh, 0, 0, out.width, out.height);

  busy(true); await raf();
  try {
    drawSourceFrom(out, out.width, out.height);
    state.selected = null;
    markSelected();
    await restyle(true);
    placeNewImage();
    buildStylePreviews();
    toast('Loaded ' + cropSrc.name);
  } finally {
    busy(false);
  }
  cancelCrop();
}

function cancelCrop() {
  if (cropSrc && cropSrc.bitmap && cropSrc.bitmap.close) cropSrc.bitmap.close();
  cropSrc = null;
  cropGesture = null;
  hideScreens();
  if (!state.running) showScreen('start');
}

/** While a slider is moving, restyle the half-size copy so the AR loop keeps up. */
/* ---------------------------------------------------- neural line drawing */
// The 'artist' preset runs the Informative Drawings model (Chan et al. 2022,
// MIT) in-browser via onnxruntime-web. Runtime + model are self-hosted next
// to index.html (~28 MB total) and fetched lazily on first use; Cache
// Storage keeps them across sessions since GitHub Pages sends short
// max-age headers. The line map is computed once per source image and
// cached - the style sliders only remap it, no re-inference.
// One entry per neural preset. out: 'ink' = greyscale line map [1,1,h,w],
// 'ink-rgb' = b&w drawing in an RGB tensor (converted to ink via luma),
// 'rgb' = painted colour picture. layout/norm follow each model's training.
const NEURAL_CFG = {
  artist:   { file: 'lineart.onnx',  size: '~28 MB', snap: 4,  layout: 'nchw', norm: '01',  out: 'ink' },
  rough:    { file: 'rough.onnx',    size: '~16 MB', snap: 4,  layout: 'nchw', norm: '01',  out: 'ink' },
  ink:      { file: 'inkbrush.onnx', size: '~4 MB',  snap: 32, layout: 'nhwc', norm: 'pm1', out: 'ink-rgb' },
  paprika:  { file: 'paprika.onnx',  size: '~9 MB',  snap: 32, layout: 'nhwc', norm: 'pm1', out: 'rgb' },
};
let neuralMaps = {};         // preset id -> ink {data,w,h} or rgb {chans,w,h}
let neuralBusy = false;
const ortSessions = {};      // model file -> InferenceSession

async function cachedFetch(url) {
  try {
    const cache = await caches.open('papertrace-models-v1');
    const hit = await cache.match(url);
    if (hit) return await hit.arrayBuffer();
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    await cache.put(url, resp.clone());
    return await resp.arrayBuffer();
  } catch (e) {
    // Cache Storage can be unavailable (private mode); plain fetch still works.
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.arrayBuffer();
  }
}

function loadScriptOnce(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    const sc = document.createElement('script');
    sc.src = src;
    sc.onload = resolve;
    sc.onerror = () => reject(new Error('failed to load ' + src));
    document.head.appendChild(sc);
  });
}

async function ensureNeuralMap(kind, quiet) {
  const cfg = NEURAL_CFG[kind];
  if (!cfg || neuralBusy || neuralMaps[kind] || !srcCanvas.width) return;
  neuralBusy = true;
  if (!quiet) busy(true);
  try {
    if (!ortSessions[cfg.file]) {
      if (!quiet) toast('Downloading the ' + kind + ' model (one-time, ' + cfg.size + ')...', 6000);
      await loadScriptOnce('ort.min.js');
      ort.env.wasm.wasmPaths = './';
      ort.env.wasm.numThreads = 1;   // no COOP/COEP headers on static hosting
      const model = await cachedFetch(cfg.file);
      ortSessions[cfg.file] = await ort.InferenceSession.create(model, { executionProviders: ['wasm'] });
      dlog(kind + ' model loaded');
    }
    const session = ortSessions[cfg.file];
    if (!quiet) toast(cfg.out === 'rgb' ? 'Painting your picture...' : 'Drawing your picture...', 4000);
    // All models are fully convolutional; 512px is the quality/speed sweet
    // spot (~1-3 s in single-threaded WASM). snap keeps each conv stack happy.
    const k = Math.min(1, 512 / Math.max(srcCanvas.width, srcCanvas.height));
    const w = Math.max(cfg.snap, Math.round(srcCanvas.width * k / cfg.snap) * cfg.snap);
    const h = Math.max(cfg.snap, Math.round(srcCanvas.height * k / cfg.snap) * cfg.snap);
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    ctx.drawImage(srcCanvas, 0, 0, w, h);
    const d = ctx.getImageData(0, 0, w, h).data;
    const npx = w * h;
    const input = new Float32Array(npx * 3);
    const nv = (v) => cfg.norm === 'pm1' ? v / 127.5 - 1 : v / 255;
    if (cfg.layout === 'nhwc') {
      for (let i = 0; i < npx; i++) {
        input[i * 3] = nv(d[i * 4]);
        input[i * 3 + 1] = nv(d[i * 4 + 1]);
        input[i * 3 + 2] = nv(d[i * 4 + 2]);
      }
    } else {
      for (let i = 0; i < npx; i++) {
        input[i] = nv(d[i * 4]);
        input[npx + i] = nv(d[i * 4 + 1]);
        input[2 * npx + i] = nv(d[i * 4 + 2]);
      }
    }
    const dims = cfg.layout === 'nhwc' ? [1, h, w, 3] : [1, 3, h, w];
    const feeds = {};
    feeds[session.inputNames[0]] = new ort.Tensor('float32', input, dims);
    const t0 = performance.now();
    const res = await session.run(feeds);
    dlog(kind + ' inference ' + Math.round(performance.now() - t0) + 'ms at ' + w + 'x' + h);
    const out = res[session.outputNames[0]];
    if (cfg.out === 'ink') {
      // [1,1,h,w], 0..1, dark = line
      const ink = new Float32Array(out.data.length);
      for (let i = 0; i < ink.length; i++) {
        ink[i] = 1 - Math.max(0, Math.min(1, out.data[i]));
      }
      neuralMaps[kind] = { data: ink, w: out.dims[3], h: out.dims[2] };
    } else {
      // NHWC rgb, -1..1
      const ow = out.dims[2], oh = out.dims[1], on = ow * oh;
      if (cfg.out === 'ink-rgb') {
        const ink = new Float32Array(on);
        for (let i = 0; i < on; i++) {
          const r = (out.data[i * 3] + 1) * 127.5;
          const g = (out.data[i * 3 + 1] + 1) * 127.5;
          const b = (out.data[i * 3 + 2] + 1) * 127.5;
          const luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
          ink[i] = 1 - Math.max(0, Math.min(1, luma));
        }
        neuralMaps[kind] = { data: ink, w: ow, h: oh };
      } else {
        const chans = [new Float32Array(on), new Float32Array(on), new Float32Array(on)];
        for (let i = 0; i < on; i++) {
          chans[0][i] = (out.data[i * 3] + 1) * 127.5;
          chans[1][i] = (out.data[i * 3 + 1] + 1) * 127.5;
          chans[2][i] = (out.data[i * 3 + 2] + 1) * 127.5;
        }
        neuralMaps[kind] = { chans, w: ow, h: oh };
      }
    }
  } catch (e) {
    dlog(kind + ' model failed: ' + e);
    if (!quiet) toast('Could not load the ' + kind + ' model - check your connection');
    if (state.style.preset === kind) state.style.preset = 'original';
  } finally {
    neuralBusy = false;
    if (!quiet) busy(false);
  }
}

// Render every neural style's preview in the background, one model at a
// time, filling gallery tiles in as they finish. Placeholder tiles saying
// "tap to draw" read as broken - a preview gallery should just show the
// previews. Models still download only once (Cache Storage) and only when
// an image is actually loaded with the Style tab open.
let neuralQueueRunning = false;
async function ensureAllNeuralMaps() {
  if (neuralQueueRunning || !srcCanvas.width) return;
  neuralQueueRunning = true;
  try {
    let announced = false;
    for (const kind of Object.keys(NEURAL_CFG)) {
      if (neuralMaps[kind]) continue;
      if (!announced) {
        toast('Preparing style previews - each model downloads once', 3500);
        announced = true;
        buildStylePreviews();          // relabel placeholders to "drawing..."
      }
      await ensureNeuralMap(kind, true);
      if (!neuralMaps[kind]) break;    // offline or failed - stop the queue
      buildStylePreviews();
      if (state.style.preset === kind) restyle(false, false);
    }
  } finally {
    neuralQueueRunning = false;
    buildStylePreviews();
  }
}



async function restyle(showBusy, quick) {
  if (!srcCanvas.width) return;
  if (showBusy) { busy(true); await raf(); }
  const kind = state.style.preset;
  if (NEURAL_CFG[kind] && !neuralMaps[kind]) {
    if (showBusy) busy(false);
    await ensureNeuralMap(kind);
    if (!neuralMaps[kind]) return;   // failed; preset was reverted, retoast'd
    if (showBusy) busy(true);
    buildStylePreviews();
  }
  const from = quick && srcSmall.width ? srcSmall : srcCanvas;
  try {
    const data = from.getContext('2d').getImageData(0, 0, from.width, from.height);
    const out = applyStyle(data, { ...state.style, neuralMaps });
    outCanvas.width = out.width; outCanvas.height = out.height;
    outCanvas.getContext('2d').putImageData(out, 0, 0);
    overlay.setTexture(outCanvas);
    state.aspect = out.width / out.height;
    if (!state.userPlaced && state.pose) { state.fitSig = ''; }
  } finally { if (showBusy) busy(false); }
}

/* ------------------------------------------------------------------- UI */
function buildLibrary() {
  el.lib.innerHTML = '';
  for (const [key, f] of Object.entries(FLOWERS)) {
    const b = document.createElement('button');
    b.dataset.key = key;
    b.title = f.label;
    b.innerHTML = f.svg;
    b.addEventListener('click', () => chooseLibrary(key));
    el.lib.appendChild(b);
  }
  // A bundled sample photo exercises the full photo pipeline (crop, neural
  // styles) with one tap - handy for trying the app without the phone.
  const sample = document.createElement('button');
  sample.dataset.key = 'samplePhoto';
  sample.title = 'Sample photo';
  sample.innerHTML = '<img src="sample.jpg" alt="Sample photo">';
  sample.addEventListener('click', async () => {
    busy(true);
    try {
      const resp = await fetch('sample.jpg');
      const blob = await resp.blob();
      state.selected = 'samplePhoto';
      markSelected();
      await loadUserFile(new File([blob], 'sample.jpg', { type: 'image/jpeg' }));
    } catch (e) {
      toast('Could not load the sample photo');
    } finally { busy(false); }
  });
  el.lib.appendChild(sample);

  const add = document.createElement('button');
  add.className = 'add';
  add.innerHTML = '<div>+<br>Your<br>image</div>';
  add.addEventListener('click', () => el.file.click());
  el.lib.appendChild(add);
}
function markSelected() {
  el.lib.querySelectorAll('button').forEach((b) =>
    b.classList.toggle('on', b.dataset.key === state.selected));
}



/**
 * The style picker shows each preset already rendered on your actual
 * picture, not a name to interpret - "Bold outline" tells you nothing
 * until you've tried it once, but a thumbnail tells you immediately.
 * Cheap enough to regenerate on every relevant change: each preset only
 * has to run on a ~96px thumbnail, not the full image.
 */
function buildStylePreviews() {
  el.stylePreviews.innerHTML = '';
  if (!srcThumb.width) return;
  const data = srcThumb.getContext('2d', { willReadFrequently: true })
    .getImageData(0, 0, srcThumb.width, srcThumb.height);
  for (const p of STYLE_PRESETS) {
    const tile = document.createElement('canvas');
    tile.width = data.width; tile.height = data.height;
    const tctx = tile.getContext('2d');
    tctx.fillStyle = '#eeeae0';           // paper-ish, so transparent lines read
    tctx.fillRect(0, 0, tile.width, tile.height);
    if (NEURAL_CFG[p.id] && !neuralMaps[p.id]) {
      // Model hasn't run yet - a placeholder invites the tap that loads it.
      tctx.fillStyle = '#8a8578';
      tctx.font = `${Math.round(tile.height * 0.3)}px serif`;
      tctx.textAlign = 'center'; tctx.textBaseline = 'middle';
      tctx.fillText(NEURAL_CFG[p.id].out === 'rgb' ? '\uD83C\uDFA8' : '\u270E', tile.width / 2, tile.height * 0.42);
      tctx.font = `600 ${Math.max(9, Math.round(tile.height * 0.09))}px sans-serif`;
      tctx.fillText(neuralQueueRunning ? 'drawing...' : 'tap to draw', tile.width / 2, tile.height * 0.72);
    } else {
      const out = applyStyle(data, { ...state.style, preset: p.id, neuralMaps });
      const tmp = document.createElement('canvas');
      tmp.width = out.width; tmp.height = out.height;
      tmp.getContext('2d').putImageData(out, 0, 0);
      tctx.drawImage(tmp, 0, 0);          // alpha-composited, unlike putImageData
    }

    const b = document.createElement('button');
    b.className = 'styleTile' + (p.id === state.style.preset ? ' on' : '');
    b.title = p.hint;
    b.appendChild(tile);
    const label = document.createElement('span');
    label.textContent = p.name;
    b.appendChild(label);
    b.addEventListener('click', () => {
      state.style.preset = p.id;
      el.stylePreviews.querySelectorAll('.styleTile').forEach((t) => t.classList.remove('on'));
      b.classList.add('on');
      restyle(true);
    });
    el.stylePreviews.appendChild(b);
  }
}

function bindSlider(id, get, set, commit) {
  const inp = $(id), out = $(id + 'Out');
  inp.value = String(Math.round(get() * 100));
  out.textContent = inp.value + '%';
  inp.addEventListener('input', () => {
    out.textContent = inp.value + '%';
    set(Number(inp.value) / 100);
  });
  if (commit) inp.addEventListener('change', commit);
}

function updateTagPanel() {
  const live = new Set((state.lastResult && state.lastResult.visible || []).map((d) => d.id));
  el.taglist.innerHTML = '';
  const ids = [...markerMap.map.keys()].sort((a, b) => a - b);
  if (!ids.length) {
    el.taglist.innerHTML = '<span>no tags learned yet</span>';
  } else {
    for (const id of ids) {
      const s = document.createElement('span');
      s.textContent = 'tag ' + id + (markerMap.seedId === id ? ' (origin)' : '');
      if (live.has(id)) s.className = 'live';
      el.taglist.appendChild(s);
    }
  }
  const q = state.lastResult && state.lastResult.err !== undefined
    ? state.lastResult.err.toFixed(2) + 'px' : '-';
  el.mapinfo.textContent =
    `${markerMap.size} anchored · fit error ${q} · detector ${state.detW}px `
    + `· ${state.detectMs.toFixed(0)}ms · ${state.fps.toFixed(0)} fps`;
}
setInterval(() => { if (state.running) updateTagPanel(); }, 600);

/* tabs */
let openTab = null;
function selectTab(name) {
  openTab = openTab === name ? null : name;
  el.sheet.classList.toggle('open', !!openTab);
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('on', t.dataset.tab === openTab));
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('on', p.dataset.panel === openTab));
  if (openTab === 'style') { buildStylePreviews(); ensureAllNeuralMaps(); }
}
document.querySelectorAll('.tab').forEach((t) =>
  t.addEventListener('click', () => selectTab(t.dataset.tab)));

/* screens */
function showScreen(id) {
  hideScreens();
  $(id).classList.add('on');
}
function hideScreens() {
  document.querySelectorAll('.screen').forEach((s) => s.classList.remove('on'));
}

/* ---------------------------------------------------------- tag printing */
function markerSvg(id, mm) {
  const M = DICT.modules, N = DICT.gridSize, code = DICT.codes[id];
  let cells = '';
  for (let r = 0; r < M; r++) {
    for (let c = 0; c < M; c++) {
      const inner = r > 0 && c > 0 && r < M - 1 && c < M - 1;
      const bit = inner ? (code >> (N * N - 1 - ((r - 1) * N + (c - 1)))) & 1 : 0;
      if (!bit) cells += `<rect x="${c}" y="${r}" width="1.02" height="1.02" fill="#000"/>`;
    }
  }
  const quiet = 1.5;
  const total = M + quiet * 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${mm * total / M}mm" `
       + `height="${mm * total / M}mm" viewBox="0 0 ${total} ${total}">`
       + `<rect width="${total}" height="${total}" fill="#fff"/>`
       + `<g transform="translate(${quiet},${quiet})">${cells}</g></svg>`;
}

function buildPrintSheet() {
  const size = Number($('tagSize').value);
  const wrap = $('tagwrap');
  wrap.innerHTML = '';
  for (let i = 0; i < DICT.codes.length; i++) {
    const d = document.createElement('div');
    d.className = 'tagcell';
    d.innerHTML = markerSvg(i, size) + `<b>tag ${i}</b>`;
    wrap.appendChild(d);
  }
}

/* -------------------------------------------------------------- snapshot */
function snapshot() {
  const c = document.createElement('canvas');
  c.width = window.innerWidth * dpr();
  c.height = window.innerHeight * dpr();
  const x = c.getContext('2d');
  // drawImage reads the source elements' real pixels, ignoring whatever CSS
  // transform is mirroring them on screen - mirror the export the same way
  // by hand, or a saved snapshot would silently not match what was visible.
  if (state.mirrored) { x.translate(c.width, 0); x.scale(-1, 1); }
  const vw = el.video.videoWidth, vh = el.video.videoHeight;
  if (vw) {
    const s = Math.max(c.width / vw, c.height / vh);
    x.drawImage(el.video, (c.width - vw * s) / 2, (c.height - vh * s) / 2, vw * s, vh * s);
  }
  x.drawImage(el.gl, 0, 0);
  x.drawImage(el.hud, 0, 0);
  c.toBlob((blob) => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'papertrace.png';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast('Saved snapshot');
  }, 'image/png');
}

/* ------------------------------------------------------------------ wire */
function wire() {
  $('btnStart').addEventListener('click', startCamera);
  $('btnHelp').addEventListener('click', () => showScreen('help'));
  $('btnHelp2').addEventListener('click', () => showScreen('help'));
  document.querySelectorAll('[data-close]').forEach((b) =>
    b.addEventListener('click', () => { hideScreens(); if (!state.running) showScreen('start'); }));

  $('btnFreeze').addEventListener('click', (e) => {
    state.freeze = !state.freeze;
    e.currentTarget.classList.toggle('on', state.freeze);
    toast(state.freeze ? 'Frame frozen' : 'Live');
  });
  $('btnTorch').addEventListener('click', async (e) => {
    if (!track) return;
    const on = !e.currentTarget.classList.contains('on');
    try {
      await track.applyConstraints({ advanced: [{ torch: on }] });
      // iOS Safari has a known bug: applyConstraints({torch:false}) resolves
      // but leaves the LED lit. Restarting the track (fresh camera session)
      // is the documented workaround - it actually releases the flash.
      if (!on) {
        const deviceId = track.getSettings ? track.getSettings().deviceId : null;
        track.stop();
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: deviceId ? { deviceId: { exact: deviceId } }
                           : { facingMode: { ideal: 'environment' } },
        });
        el.video.srcObject = stream;
        await el.video.play().catch(() => {});
        track = stream.getVideoTracks()[0];
      }
      e.currentTarget.classList.toggle('on', on);
    } catch (err) { toast('Torch not available'); }
  });
  $('btnMirror').addEventListener('click', (e) => {
    state.mirrored = !state.mirrored;
    e.currentTarget.classList.toggle('on', state.mirrored);
    updateCamTransform();
    toast(state.mirrored ? 'Mirrored to match your table' : 'Mirror off');
  });
  $('btnShot').addEventListener('click', snapshot);

  $('btnLock').addEventListener('click', () => {
    state.locked = !state.locked;
    $('btnLock').textContent = state.locked ? 'Unlock camera view' : 'Lock camera view';
    $('btnLock').classList.toggle('primary', !state.locked);
    toast(state.locked ? 'Camera view locked - drag/pinch disabled' : 'Drag to pan, pinch to zoom the camera');
  });
  $('btnFit').addEventListener('click', () => {
    state.userPlaced = false; state.fitSig = '';
    if (state.presetFrame) fitToFrame(state.presetFrame); else fitToTags();
    toast(state.presetFrame ? 'Fitted to the printed frame' : 'Fitted to your tags');
  });
  $('btnFlip').addEventListener('click', () => { state.userPlaced = true; state.place.flip = !state.place.flip; updatePlaceInfo(); });
  $('btnRotL').addEventListener('click', () => { state.userPlaced = true; state.place.angle -= Math.PI / 36; updatePlaceInfo(); });
  $('btnRotR').addEventListener('click', () => { state.userPlaced = true; state.place.angle += Math.PI / 36; updatePlaceInfo(); });
  $('btnUpright').addEventListener('click', () => { state.userPlaced = true; state.place.angle = 0; updatePlaceInfo(); });
  $('btnReset').addEventListener('click', () => {
    markerMap.reset();
    state.pose = null; state.placed = false; state.userPlaced = false; state.fitSig = '';
    state.presetFrame = null;
    toast('Anchors cleared - sweep across your tags again');
  });

  $('camZoom').addEventListener('input', (e) => {
    state.camZoom = Number(e.target.value) / 100;
    $('camZoomOut').textContent = state.camZoom.toFixed(1) + '×';
    updateCamTransform();
  });
  $('grid').addEventListener('change', (e) => { state.showGrid = e.target.checked; });
  $('gridN').addEventListener('input', (e) => {
    state.gridN = Number(e.target.value);
    $('gridNOut').textContent = state.gridN + '×' + state.gridN;
  });
  $('freehand').addEventListener('change', (e) => {
    // Freehand uses a completely different frame (origin at the screen centre,
    // 1 unit = a fifth of the frame), so a placement measured against the tag
    // map means nothing here. Swap the two placements rather than reusing one.
    const keep = { ...state.place };
    state.freehand = e.target.checked;
    if (state.freehand) {
      state.tagPlace = keep;
      Object.assign(state.place, state.freePlace || { x: 0, y: 0, scale: 3, angle: 0, flip: keep.flip });
    } else {
      state.freePlace = keep;
      Object.assign(state.place, state.tagPlace || { x: 0, y: 0, scale: 3, angle: 0, flip: keep.flip });
      state.fitSig = '';
    }
    updatePlaceInfo();
    toast(state.freehand
      ? 'Freehand: the image is pinned to the screen, not the paper'
      : 'Back to tag anchoring');
  });
  bindSlider('opacity', () => state.opacity, (v) => { state.opacity = v; });

  el.file.addEventListener('change', (e) => {
    if (e.target.files && e.target.files[0]) loadUserFile(e.target.files[0]);
    e.target.value = '';
  });
  $('btnCropCancel').addEventListener('click', cancelCrop);
  $('btnCropUse').addEventListener('click', commitCrop);
  window.addEventListener('resize', () => { if (cropSrc) { clampCropPan(); renderCrop(); } });

  $('btnTags').addEventListener('click', () => { buildPrintSheet(); showScreen('printsheet'); });
  $('btnTags2').addEventListener('click', () => { buildPrintSheet(); showScreen('printsheet'); });
  $('tagSize').addEventListener('change', buildPrintSheet);
  $('btnPrint').addEventListener('click', () => window.print());
  $('btnDebugLog').addEventListener('click', saveDebugLog);
}

/* ------------------------------------------------------------------ boot */
buildLibrary();
wire();
updatePlaceInfo();
chooseLibrary('rose');
if (insecureContext()) $('insecure').style.display = 'block';
showScreen('start');
