/* Synthetic-scene tests for MarkerDetector. Renders markers under a known
 * perspective homography, then checks ids, corner accuracy and orientation. */

function makeGrid(code, gridSize, modules) {
  const g = [];
  for (let r = 0; r < modules; r++) g.push(new Array(modules).fill(0)); // 0 = black
  for (let r = 0; r < gridSize; r++) {
    for (let c = 0; c < gridSize; c++) {
      g[r + 1][c + 1] = (code >> (gridSize * gridSize - 1 - (r * gridSize + c))) & 1;
    }
  }
  return g;
}

/** Render markers on a plane, viewed through homography H (plane -> image). */
function renderScene(opts) {
  const { w, h, H, markers, dict, blur = 0, noise = 0, shading = 0.25, ss = 2 } = opts;
  const Hinv = matInv(H);
  const gray = new Float32Array(w * h);
  const grids = markers.map((m) => makeGrid(dict.codes[m.id], dict.gridSize, dict.modules));
  const M = dict.modules;

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let acc = 0;
      for (let sy = 0; sy < ss; sy++) {
        for (let sx = 0; sx < ss; sx++) {
          // Pixel (x,y) is an area sample centred on the integer coordinate,
          // matching the detector's sampling convention.
          const px = x - 0.5 + (sx + 0.5) / ss, py = y - 0.5 + (sy + 0.5) / ss;
          const [qx, qy] = matApply(Hinv, px, py);
          let v = 236;                                     // paper white
          for (let k = 0; k < markers.length; k++) {
            const m = markers[k];
            const ct = Math.cos(-m.angle), st = Math.sin(-m.angle);
            const dx = qx - m.cx, dy = qy - m.cy;
            const lx = dx * ct - dy * st, ly = dx * st + dy * ct;
            const half = m.size / 2;
            if (lx < -half || lx > half || ly < -half || ly > half) continue;
            const c = Math.min(M - 1, Math.floor((lx + half) / m.size * M));
            const r = Math.min(M - 1, Math.floor((ly + half) / m.size * M));
            v = grids[k][r][c] ? 240 : 22;
            break;
          }
          acc += v;
        }
      }
      let v = acc / (ss * ss);
      // Uneven lighting across the frame, then optional sensor noise.
      v *= 1 - shading * (x / w) * 0.6 - shading * (y / h) * 0.4;
      if (noise) v += (Math.random() * 2 - 1) * noise;
      gray[y * w + x] = v;
    }
  }

  let src = gray;
  if (blur > 0) {
    const tmp = new Float32Array(w * h), out = new Float32Array(w * h);
    const rad = Math.max(1, Math.round(blur));
    const k = [];
    let ksum = 0;
    for (let i = -rad; i <= rad; i++) { const g = Math.exp(-(i * i) / (2 * blur * blur)); k.push(g); ksum += g; }
    for (let i = 0; i < k.length; i++) k[i] /= ksum;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let s = 0;
      for (let i = -rad; i <= rad; i++) s += k[i + rad] * gray[y * w + Math.min(w - 1, Math.max(0, x + i))];
      tmp[y * w + x] = s;
    }
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let s = 0;
      for (let i = -rad; i <= rad; i++) s += k[i + rad] * tmp[Math.min(h - 1, Math.max(0, y + i)) * w + x];
      out[y * w + x] = s;
    }
    src = out;
  }

  const u8 = new Uint8ClampedArray(w * h);
  for (let i = 0; i < u8.length; i++) u8[i] = Math.max(0, Math.min(255, src[i]));
  return u8;
}

function truthCorners(m, H) {
  const half = m.size / 2;
  const local = [[-half, -half], [half, -half], [half, half], [-half, half]];
  const ct = Math.cos(m.angle), st = Math.sin(m.angle);
  return local.map(([lx, ly]) => matApply(H,
    m.cx + lx * ct - ly * st,
    m.cy + lx * st + ly * ct));
}

/** Perspective view of the plane: tilt about x and y, then project. */
function viewHomography(w, h, tiltX, tiltY, scale, tx, ty, roll = 0) {
  const R = trs(w / 2 + tx, h / 2 + ty, roll, scale, scale);
  const P = new Float64Array([1, 0, 0, 0, 1, 0, tiltX, tiltY, 1]);
  return matMul(R, P);
}

function runTests(dict) {
  const det = new MarkerDetector(dict);
  const results = [];
  const log = (name, pass, detail) => results.push({ name, pass, detail });

  const scenarios = [
    { name: 'flat, 4 tags', w: 640, h: 480, tilt: [0, 0], scale: 1, roll: 0, blur: 0.8, noise: 2,
      markers: [
        { id: 0, cx: -150, cy: -110, size: 90, angle: 0 },
        { id: 1, cx: 160, cy: -120, size: 90, angle: Math.PI / 2 },
        { id: 2, cx: -140, cy: 120, size: 90, angle: Math.PI },
        { id: 3, cx: 150, cy: 130, size: 90, angle: -Math.PI / 2 },
      ] },
    { name: 'steep tilt', w: 640, h: 480, tilt: [0.00075, 0.0011], scale: 1.15, roll: 0.15, blur: 1.0, noise: 3,
      markers: [
        { id: 4, cx: -120, cy: -40, size: 100, angle: 0.4 },
        { id: 5, cx: 120, cy: -100, size: 100, angle: -0.9 },
        { id: 6, cx: -60, cy: 120, size: 100, angle: 2.2 },
      ] },
    { name: 'rotated 37 deg + blur', w: 640, h: 480, tilt: [0.0003, -0.0005], scale: 1, roll: 0.646, blur: 1.6, noise: 4,
      markers: [
        { id: 7, cx: -120, cy: -60, size: 110, angle: 0.646 },
        { id: 8, cx: 130, cy: 80, size: 110, angle: 0.646 + Math.PI / 2 },
      ] },
    { name: 'small tags (far away)', w: 640, h: 480, tilt: [0.0004, 0.0004], scale: 1, roll: -0.2, blur: 0.7, noise: 2,
      markers: [
        { id: 9, cx: -110, cy: -70, size: 48, angle: 0.2 },
        { id: 10, cx: 100, cy: 60, size: 48, angle: 1.9 },
      ] },
    { name: 'mixed sizes, low contrast', w: 800, h: 600, tilt: [0.0006, 0.0002], scale: 1.1, roll: 0.05, blur: 1.2, noise: 5,
      markers: [
        { id: 11, cx: -200, cy: -120, size: 150, angle: 0 },
        { id: 12, cx: 90, cy: -140, size: 70, angle: 0.8 },
        { id: 13, cx: -40, cy: 140, size: 105, angle: -1.3 },
      ] },
  ];

  for (const sc of scenarios) {
    const H = viewHomography(sc.w, sc.h, sc.tilt[0], sc.tilt[1], sc.scale, 0, 0, sc.roll);
    const gray = renderScene({ w: sc.w, h: sc.h, H, markers: sc.markers, dict,
                               blur: sc.blur, noise: sc.noise });
    const t0 = performance.now();
    const found = det.detect(gray, sc.w, sc.h);
    const ms = performance.now() - t0;

    const wantIds = sc.markers.map((m) => m.id).sort((a, b) => a - b);
    const gotIds = found.map((f) => f.id).sort((a, b) => a - b);
    log(`${sc.name}: ids`, JSON.stringify(wantIds) === JSON.stringify(gotIds),
        `want ${wantIds} got ${gotIds} (${ms.toFixed(1)}ms)`);

    let worst = 0, worstId = -1;
    for (const m of sc.markers) {
      const f = found.find((x) => x.id === m.id);
      if (!f) continue;
      const gt = truthCorners(m, H);
      for (let i = 0; i < 4; i++) {
        const d = Math.hypot(f.corners[i][0] - gt[i][0], f.corners[i][1] - gt[i][1]);
        if (d > worst) { worst = d; worstId = m.id; }
      }
    }
    log(`${sc.name}: corner accuracy`, worst < 0.35,
        `max corner error ${worst.toFixed(3)}px (tag ${worstId})`);
  }

  // False positives on textured, marker-free scenes.
  {
    const w = 640, h = 480;
    const gray = new Uint8ClampedArray(w * h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const v = 128 + 90 * Math.sin(x / 7) * Math.cos(y / 11)
                + 40 * Math.sin((x + y) / 3) + (Math.random() * 2 - 1) * 25;
        gray[y * w + x] = Math.max(0, Math.min(255, v));
      }
    }
    const found = new MarkerDetector(dict).detect(gray, w, h);
    log('no false positives on texture', found.length === 0, `found ${found.length}`);
  }
  {
    // Squares that are NOT markers (blank frames, checkerboard) must be rejected.
    const w = 640, h = 480;
    const gray = new Uint8ClampedArray(w * h).fill(235);
    const box = (x0, y0, s, fill) => {
      for (let y = y0; y < y0 + s; y++) for (let x = x0; x < x0 + s; x++) gray[y * w + x] = fill;
    };
    box(60, 60, 120, 20); box(80, 80, 80, 240);
    box(300, 200, 140, 20);
    for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) {
      if ((x + y) % 2) box(310 + x * 15, 210 + y * 15, 15, 240);
    }
    const found = new MarkerDetector(dict).detect(gray, w, h);
    log('rejects non-marker squares', found.length === 0, `found ${found.length}`);
  }

  return results;
}
