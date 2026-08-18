/* ---------------------------------------------------------------------------
 * aruco.js - square fiducial detector.
 *
 * Pipeline: adaptive threshold -> Moore-neighbour border following ->
 * Douglas-Peucker quad fitting -> subpixel corners by edge line intersection ->
 * projective bit sampling -> dictionary match with Hamming correction.
 * ------------------------------------------------------------------------- */

const DIR8 = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];

class MarkerDetector {
  constructor(dict) {
    this.grid = dict.gridSize;             // inner grid, e.g. 4
    this.modules = dict.modules;           // grid + 2 (black border ring)
    this.maxCorrect = dict.maxCorrectableBits;
    this.rotTable = new Map();             // code -> {id, rot}
    dict.codes.forEach((code, id) => {
      for (let r = 0; r < 4; r++) {
        this.rotTable.set(dict.rotations[String(id)][r], { id, rot: r });
      }
    });
    this.codes = dict.codes;
    this.rotations = dict.rotations;

    this.minPerimeterRatio = 0.035;        // of max(w,h)*4
    this.maxPerimeterRatio = 4.0;
    this.polyEps = 0.038;
    this.borderErrorsAllowed = 2;
    this.minContrast = 22;                 // black/white module separation

    this._buf = {};
  }

  _ensure(w, h) {
    const b = this._buf;
    if (b.w !== w || b.h !== h) {
      b.w = w; b.h = h;
      b.bin = new Uint8Array(w * h);
      b.seen = new Uint8Array(w * h);
      b.integral = new Float64Array((w + 1) * (h + 1));
    }
    return b;
  }

  /* ------------------------------------------------------- thresholding */
  adaptiveThreshold(gray, w, h, blockSize, C) {
    const b = this._ensure(w, h);
    const I = b.integral, bin = b.bin;
    const iw = w + 1;
    for (let x = 0; x <= w; x++) I[x] = 0;
    for (let y = 0; y < h; y++) {
      let rowSum = 0;
      I[(y + 1) * iw] = 0;
      for (let x = 0; x < w; x++) {
        rowSum += gray[y * w + x];
        I[(y + 1) * iw + (x + 1)] = I[y * iw + (x + 1)] + rowSum;
      }
    }
    const r = blockSize >> 1;
    for (let y = 0; y < h; y++) {
      const y0 = Math.max(0, y - r), y1 = Math.min(h - 1, y + r);
      for (let x = 0; x < w; x++) {
        const x0 = Math.max(0, x - r), x1 = Math.min(w - 1, x + r);
        const area = (x1 - x0 + 1) * (y1 - y0 + 1);
        const sum = I[(y1 + 1) * iw + (x1 + 1)] - I[y0 * iw + (x1 + 1)]
                  - I[(y1 + 1) * iw + x0] + I[y0 * iw + x0];
        // 1 = "ink" (dark)
        bin[y * w + x] = gray[y * w + x] * area < sum - C * area ? 1 : 0;
      }
    }
    return bin;
  }

  /* --------------------------------------------------- border following */
  traceContours(bin, w, h, minPerim, maxPerim) {
    const seen = this._buf.seen;
    seen.fill(0);
    const contours = [];
    const maxSteps = Math.min(maxPerim * 2 + 16, 4 * (w + h) + 64);

    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        const idx = y * w + x;
        if (!bin[idx] || seen[idx] || bin[idx - 1]) continue;   // need a left edge
        // Moore-neighbour trace, clockwise, starting backtrack = west.
        const pts = [];
        let cx = x, cy = y, back = 4, firstDir = -1, steps = 0, closed = false;
        while (steps < maxSteps) {
          let dir = -1;
          for (let k = 1; k <= 8; k++) {
            const d = (back + k) & 7;
            const nx = cx + DIR8[d][0], ny = cy + DIR8[d][1];
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            if (bin[ny * w + nx]) { dir = d; break; }
          }
          if (dir < 0) break;                                    // isolated pixel
          if (steps > 0 && cx === x && cy === y && dir === firstDir) { closed = true; break; }
          if (firstDir < 0) firstDir = dir;
          pts.push(cx, cy);
          seen[cy * w + cx] = 1;
          cx += DIR8[dir][0]; cy += DIR8[dir][1];
          back = (dir + 4) & 7;
          steps++;
        }
        seen[idx] = 1;
        if (closed && pts.length >= minPerim * 2 && pts.length <= maxPerim * 2) {
          contours.push(pts);
        }
      }
    }
    return contours;
  }

  /* ------------------------------------------------------ quad fitting */
  approxQuad(pts, eps) {
    const n = pts.length / 2;
    const px = (i) => pts[(i % n) * 2], py = (i) => pts[(i % n) * 2 + 1];

    // Split the closed contour at its two most distant points, then run
    // Douglas-Peucker on each open chain.
    let i1 = 0, best = -1;
    for (let i = 1; i < n; i++) {
      const d = (px(i) - px(0)) ** 2 + (py(i) - py(0)) ** 2;
      if (d > best) { best = d; i1 = i; }
    }
    let i2 = 0; best = -1;
    for (let i = 0; i < n; i++) {
      const d = (px(i) - px(i1)) ** 2 + (py(i) - py(i1)) ** 2;
      if (d > best) { best = d; i2 = i; }
    }
    if (i1 === i2) return null;

    const dp = (a, b, out) => {
      const stack = [[a, b]];
      const keep = [];
      while (stack.length) {
        const [s, e] = stack.pop();
        if (e - s < 2) continue;
        const x0 = px(s), y0 = py(s), x1 = px(e), y1 = py(e);
        const dx = x1 - x0, dy = y1 - y0;
        const len = Math.hypot(dx, dy) || 1;
        let far = -1, fd = eps;
        for (let i = s + 1; i < e; i++) {
          const d = Math.abs(dy * (px(i) - x0) - dx * (py(i) - y0)) / len;
          if (d > fd) { fd = d; far = i; }
        }
        if (far > 0) { keep.push(far); stack.push([s, far], [far, e]); }
      }
      keep.sort((p, q) => p - q);
      out.push(a, ...keep);
    };

    const lo = Math.min(i1, i2), hi = Math.max(i1, i2);
    const idx = [];
    dp(lo, hi, idx);
    dp(hi, lo + n, idx);
    const uniq = [...new Set(idx.map((i) => i % n))].sort((a, b) => a - b);
    if (uniq.length !== 4) return null;
    return uniq;
  }

  static isConvexQuad(c) {
    let sign = 0;
    for (let i = 0; i < 4; i++) {
      const a = c[i], b = c[(i + 1) % 4], d = c[(i + 2) % 4];
      const cr = (b[0] - a[0]) * (d[1] - b[1]) - (b[1] - a[1]) * (d[0] - b[0]);
      if (cr === 0) continue;
      const s = cr > 0 ? 1 : -1;
      if (sign === 0) sign = s; else if (s !== sign) return false;
    }
    return sign !== 0;
  }

  /**
   * Walk the grey-level profile across an edge and return the subpixel position
   * of the 50% crossing. The contour itself runs through pixel *centres*, which
   * biases every side inward by about half a pixel; this removes that bias and
   * takes corner accuracy from ~1.3px to ~0.2px.
   */
  refineLineOnGray(gray, w, h, line, halfLen, nx, ny) {
    const [mx, my, ux, uy] = line;
    const SAMPLES = 13, SPAN = 3.5, STEP = 0.5;
    const bilinear = (x, y) => {
      if (x < 0 || y < 0 || x > w - 1.001 || y > h - 1.001) return -1;
      const x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0, i = y0 * w + x0;
      return gray[i] * (1 - fx) * (1 - fy) + gray[i + 1] * fx * (1 - fy)
           + gray[i + w] * (1 - fx) * fy + gray[i + w + 1] * fx * fy;
    };

    const px = [], py = [];
    for (let s = 0; s < SAMPLES; s++) {
      const t = (s / (SAMPLES - 1) - 0.5) * 1.5 * halfLen;   // middle 75% of the side
      const bx = mx + ux * t, by = my + uy * t;
      const prof = [];
      let lo = Infinity, hi = -Infinity;
      for (let d = -SPAN; d <= SPAN + 1e-9; d += STEP) {
        const v = bilinear(bx + nx * d, by + ny * d);
        if (v < 0) { prof.length = 0; break; }
        prof.push(v);
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      if (prof.length < 5 || hi - lo < 25) continue;
      const mid = (lo + hi) / 2;
      let cross = NaN;
      for (let i = 0; i < prof.length - 1; i++) {
        if (prof[i] <= mid && prof[i + 1] > mid) {
          const f = (mid - prof[i]) / (prof[i + 1] - prof[i]);
          cross = -SPAN + (i + f) * STEP;
          break;
        }
      }
      if (!isFinite(cross)) continue;
      px.push(bx + nx * cross); py.push(by + ny * cross);
    }
    if (px.length < 5) return null;

    let sx = 0, sy = 0;
    for (let i = 0; i < px.length; i++) { sx += px[i]; sy += py[i]; }
    const cx = sx / px.length, cy = sy / px.length;
    let sxx = 0, syy = 0, sxy = 0;
    for (let i = 0; i < px.length; i++) {
      const dx = px[i] - cx, dy = py[i] - cy;
      sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
    }
    const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
    return [cx, cy, Math.cos(theta), Math.sin(theta)];
  }

  /* Refine corners by fitting a line to each side and intersecting neighbours. */
  refineCorners(pts, cornerIdx, gray, w, h) {
    const n = pts.length / 2;
    const lines = [];
    const halfLens = [];
    for (let s = 0; s < 4; s++) {
      const a = cornerIdx[s], b = cornerIdx[(s + 1) % 4];
      let count = (b - a + n) % n;
      if (count < 8) return null;
      const trim = Math.max(1, Math.round(count * 0.18));
      let sx = 0, sy = 0, m = 0;
      for (let k = trim; k <= count - trim; k++) {
        const i = (a + k) % n;
        sx += pts[i * 2]; sy += pts[i * 2 + 1]; m++;
      }
      if (m < 4) return null;
      const mx = sx / m, my = sy / m;
      let sxx = 0, syy = 0, sxy = 0;
      for (let k = trim; k <= count - trim; k++) {
        const i = (a + k) % n;
        const dx = pts[i * 2] - mx, dy = pts[i * 2 + 1] - my;
        sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
      }
      // Principal direction of the point cloud = total-least-squares line.
      const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
      lines.push([mx, my, Math.cos(theta), Math.sin(theta)]);
      const ax = pts[a * 2], ay = pts[a * 2 + 1];
      const bx = pts[(b % n) * 2], by = pts[(b % n) * 2 + 1];
      halfLens.push(Math.hypot(bx - ax, by - ay) / 2);
    }

    if (gray) {
      let gx = 0, gy = 0;
      for (const L of lines) { gx += L[0]; gy += L[1]; }
      gx /= 4; gy /= 4;
      for (let s = 0; s < 4; s++) {
        const L = lines[s];
        let nx = -L[3], ny = L[2];
        if ((L[0] - gx) * nx + (L[1] - gy) * ny < 0) { nx = -nx; ny = -ny; }
        const better = this.refineLineOnGray(gray, w, h, L, halfLens[s], nx, ny);
        if (better) lines[s] = better;
      }
    }

    const out = [];
    for (let c = 0; c < 4; c++) {
      const L1 = lines[(c + 3) % 4], L2 = lines[c];
      const det = L1[2] * -L2[3] - L1[3] * -L2[2];
      if (Math.abs(det) < 1e-9) return null;
      const rx = L2[0] - L1[0], ry = L2[1] - L1[1];
      const t = (rx * -L2[3] - ry * -L2[2]) / det;
      const x = L1[0] + t * L1[2], y = L1[1] + t * L1[3];
      if (!isFinite(x) || !isFinite(y)) return null;
      out.push([x, y]);
    }
    return out;
  }

  /* --------------------------------------------------------- bit reading */
  readBits(gray, w, h, corners) {
    const M = this.modules;
    const H = solveHomography(
      [[0, 0], [M, 0], [M, M], [0, M]],
      corners,
    );
    if (!H) { this.lastBitFail='homography'; return null; }

    const sample = (u, v) => {
      const x = (H[0] * u + H[1] * v + H[2]) / (H[6] * u + H[7] * v + H[8]);
      const y = (H[3] * u + H[4] * v + H[5]) / (H[6] * u + H[7] * v + H[8]);
      if (x < 0 || y < 0 || x > w - 1.001 || y > h - 1.001) return -1;
      const x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0;
      const i = y0 * w + x0;
      return gray[i] * (1 - fx) * (1 - fy) + gray[i + 1] * fx * (1 - fy)
           + gray[i + w] * (1 - fx) * fy + gray[i + w + 1] * fx * fy;
    };

    const means = new Float64Array(M * M);
    const OFF = [-0.24, 0, 0.24];
    for (let r = 0; r < M; r++) {
      for (let c = 0; c < M; c++) {
        let sum = 0, cnt = 0;
        for (const dy of OFF) {
          for (const dx of OFF) {
            const s = sample(c + 0.5 + dx, r + 0.5 + dy);
            if (s < 0) { this.lastBitFail='offscreen'; return null; }
            sum += s; cnt++;
          }
        }
        means[r * M + c] = sum / cnt;
      }
    }

    // Otsu over the module means.
    const hist = new Int32Array(256);
    for (let i = 0; i < means.length; i++) hist[Math.max(0, Math.min(255, means[i] | 0))]++;
    const total = means.length;
    let sumAll = 0;
    for (let t = 0; t < 256; t++) sumAll += t * hist[t];
    // The histogram is extremely sparse - two tight clusters with a wide empty
    // gap - so between-class variance is flat right across that gap and taking
    // the argmax pins the threshold to the dark cluster itself. Track the class
    // means at the optimum and split halfway between them instead.
    let sumB = 0, wB = 0, maxVar = -1, bestB = 0, bestF = 255;
    for (let t = 0; t < 256; t++) {
      wB += hist[t];
      if (!wB) continue;
      const wF = total - wB;
      if (!wF) break;
      sumB += t * hist[t];
      const mB = sumB / wB, mF = (sumAll - sumB) / wF;
      const between = wB * wF * (mB - mF) * (mB - mF);
      if (between > maxVar) { maxVar = between; bestB = mB; bestF = mF; }
    }
    if (maxVar < 0) { this.lastBitFail = 'mono'; return null; }
    const contrast = bestF - bestB;
    const thr = (bestB + bestF) / 2;

    let blackN = 0, whiteN = 0;
    const bits = new Uint8Array(M * M);
    for (let i = 0; i < means.length; i++) {
      if (means[i] > thr) { bits[i] = 1; whiteN++; } else { blackN++; }
    }
    if (!blackN || !whiteN) { this.lastBitFail='mono'; return null; }
    if (contrast < this.minContrast) { this.lastBitFail='contrast='+contrast.toFixed(0); return null; }

    // The 1-module ring must be black.
    let borderErrors = 0;
    for (let r = 0; r < M; r++) {
      for (let c = 0; c < M; c++) {
        if (r === 0 || c === 0 || r === M - 1 || c === M - 1) borderErrors += bits[r * M + c];
      }
    }
    if (borderErrors > this.borderErrorsAllowed) { this.lastBitFail='border='+borderErrors; return null; }

    const N = this.grid;
    let code = 0;
    for (let r = 0; r < N; r++) {
      for (let c = 0; c < N; c++) code = (code << 1) | bits[(r + 1) * M + (c + 1)];
    }
    return { code, contrast };
  }

  match(code) {
    const exact = this.rotTable.get(code);
    if (exact) return { ...exact, errors: 0 };
    if (this.maxCorrect <= 0) return null;
    let best = null;
    for (const [c, v] of this.rotTable) {
      let x = c ^ code, d = 0;
      while (x) { d += x & 1; x >>>= 1; }
      if (d <= this.maxCorrect && (!best || d < best.errors)) best = { ...v, errors: d };
    }
    return best;
  }

  /**
   * @param gray Uint8ClampedArray|Uint8Array luminance, length w*h
   * @returns [{id, corners:[[x,y] x4], errors, contrast}]
   */
  detect(gray, w, h) {
    const maxDim = Math.max(w, h);
    const block = Math.max(7, (Math.round(maxDim / 26) | 1));
    const bin = this.adaptiveThreshold(gray, w, h, block, 7);

    const minPerim = Math.max(24, Math.round(maxDim * this.minPerimeterRatio) * 4);
    const maxPerim = Math.round(maxDim * this.maxPerimeterRatio);
    const contours = this.traceContours(bin, w, h, minPerim, maxPerim);

    const stats = { contours: contours.length, notQuad: 0, notConvex: 0, tooSmall: 0,
                    badBits: 0, noMatch: 0, ok: 0 };
    this.stats = stats;

    this.rejects = [];
    const found = [];
    for (const pts of contours) {
      const n = pts.length / 2;
      let perim = 0;
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        perim += Math.hypot(pts[j * 2] - pts[i * 2], pts[j * 2 + 1] - pts[i * 2 + 1]);
      }
      const idx = this.approxQuad(pts, perim * this.polyEps);
      if (!idx) { stats.notQuad++; continue; }

      let quad = idx.map((i) => [pts[i * 2], pts[i * 2 + 1]]);
      if (!MarkerDetector.isConvexQuad(quad)) { stats.notConvex++; continue; }
      let minSide = Infinity;
      for (let i = 0; i < 4; i++) {
        const a = quad[i], b = quad[(i + 1) % 4];
        minSide = Math.min(minSide, Math.hypot(b[0] - a[0], b[1] - a[1]));
      }
      if (minSide < maxDim * 0.022) { stats.tooSmall++; continue; }

      const refined = this.refineCorners(pts, idx, gray, w, h) || quad;
      // Enforce clockwise winding in image coords (y down).
      let ordered = refined;
      if (polygonArea(ordered) < 0) ordered = [ordered[0], ordered[3], ordered[2], ordered[1]];

      const read = this.readBits(gray, w, h, ordered);
      if (!read) { stats.badBits++; (this.rejects||(this.rejects=[])).push({why:this.lastBitFail,quad:ordered.map(q=>[Math.round(q[0]),Math.round(q[1])])}); continue; }
      const m = this.match(read.code);
      if (!m) { stats.noMatch++; continue; }

      // Shifting the corner list forward by one rotates the sampled grid 90 deg
      // counter-clockwise, so a grid observed as CW^rot(canonical) is undone by
      // shifting forward by `rot`.
      const corners = [0, 1, 2, 3].map((i) => ordered[(i + m.rot) % 4]);
      stats.ok++;
      found.push({ id: m.id, corners, errors: m.errors, contrast: read.contrast });
    }

    // Drop duplicates (same id seen twice) keeping the larger, cleaner quad.
    const byId = new Map();
    for (const f of found) {
      const area = Math.abs(polygonArea(f.corners));
      const prev = byId.get(f.id);
      if (!prev || area > prev.area) byId.set(f.id, { ...f, area });
    }
    return [...byId.values()];
  }
}
