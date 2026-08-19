/* ---------------------------------------------------------------------------
 * styles.js - turn any picture into something you can actually trace.
 *
 * Everything outputs straight-alpha RGBA: line pixels are opaque, everything
 * else is fully transparent, so the camera shows through the gaps instead of
 * a white rectangle sitting on your paper.
 * ------------------------------------------------------------------------- */

const STYLE_PRESETS = [
  { id: 'clean',    name: 'Clean lines', hint: 'Crisp single-weight outline. The default for tracing.' },
  { id: 'sketch',   name: 'Sketch',      hint: 'Softer pencil-like lines that keep shading detail.' },
  { id: 'bold',     name: 'Bold outline', hint: 'Only the strongest edges, thickened. Good in bright light.' },
  { id: 'contour',  name: 'Contour map', hint: 'Tone boundaries, like a colouring book. Good for painting.' },
  { id: 'paint',    name: 'Paint by numbers', hint: 'Flat colour regions with an outline - trace the lines, then match paint or marker colour to each one.' },
  { id: 'stencil',  name: 'Stencil',     hint: 'Solid black shapes. Good for lettering and silhouettes.' },
  { id: 'ghost',    name: 'Ghost',       hint: 'The photo itself, faded. Best for shading reference.' },
  { id: 'original', name: 'Original',    hint: 'Untouched, for artwork that is already line art.' },
];

const LINE_COLOURS = [
  { id: 'black', name: 'Black', rgb: [16, 16, 20] },
  { id: 'white', name: 'White', rgb: [255, 255, 255] },
  { id: 'red',   name: 'Red',   rgb: [235, 45, 60] },
  { id: 'cyan',  name: 'Cyan',  rgb: [40, 215, 235] },
  { id: 'lime',  name: 'Lime',  rgb: [140, 240, 60] },
];

/* ------------------------------------------------------------ primitives */

/** Box sizes whose repeated application approximates a Gaussian (Kutskir). */
function boxesForGauss(sigma, n) {
  const wIdeal = Math.sqrt((12 * sigma * sigma / n) + 1);
  let wl = Math.floor(wIdeal);
  if (wl % 2 === 0) wl--;
  const wu = wl + 2;
  const mIdeal = (12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4);
  const m = Math.round(mIdeal);
  const sizes = [];
  for (let i = 0; i < n; i++) sizes.push(i < m ? wl : wu);
  return sizes;
}

function boxBlurH(src, dst, w, h, r) {
  if (r < 1) { dst.set(src); return; }
  const iarr = 1 / (r + r + 1);
  for (let y = 0; y < h; y++) {
    const row = y * w;
    let acc = src[row] * (r + 1);
    for (let j = 0; j < r; j++) acc += src[row + Math.min(j, w - 1)];
    for (let x = 0; x < w; x++) {
      // Sliding window [x-r, x+r]: stepping to x adds x+r and drops x-r-1.
      acc += src[row + Math.min(x + r, w - 1)] - src[row + Math.max(x - r - 1, 0)];
      dst[row + x] = acc * iarr;
    }
  }
}

function boxBlurV(src, dst, w, h, r) {
  if (r < 1) { dst.set(src); return; }
  const iarr = 1 / (r + r + 1);
  for (let x = 0; x < w; x++) {
    let acc = src[x] * (r + 1);
    for (let j = 0; j < r; j++) acc += src[Math.min(j, h - 1) * w + x];
    for (let y = 0; y < h; y++) {
      acc += src[Math.min(y + r, h - 1) * w + x] - src[Math.max(y - r - 1, 0) * w + x];
      dst[y * w + x] = acc * iarr;
    }
  }
}

function gaussBlur(src, w, h, sigma) {
  const out = Float32Array.from(src);
  if (sigma < 0.35) return out;
  const tmp = new Float32Array(w * h);
  for (const size of boxesForGauss(sigma, 3)) {
    const r = (size - 1) / 2;
    boxBlurH(out, tmp, w, h, r);
    boxBlurV(tmp, out, w, h, r);
  }
  return out;
}

/** Separable max filter - dilates lines to make them thicker. */
function dilate(src, w, h, r) {
  if (r < 1) return src;
  const tmp = new Float32Array(w * h);
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let m = 0;
      for (let i = -r; i <= r; i++) {
        const v = src[y * w + Math.min(w - 1, Math.max(0, x + i))];
        if (v > m) m = v;
      }
      tmp[y * w + x] = m;
    }
  }
  for (let x = 0; x < w; x++) {
    for (let y = 0; y < h; y++) {
      let m = 0;
      for (let i = -r; i <= r; i++) {
        const v = tmp[Math.min(h - 1, Math.max(0, y + i)) * w + x];
        if (v > m) m = v;
      }
      out[y * w + x] = m;
    }
  }
  return out;
}

function integralMean(gray, w, h, block, out) {
  const iw = w + 1;
  const I = new Float64Array(iw * (h + 1));
  for (let y = 0; y < h; y++) {
    let rowSum = 0;
    for (let x = 0; x < w; x++) {
      rowSum += gray[y * w + x];
      I[(y + 1) * iw + (x + 1)] = I[y * iw + (x + 1)] + rowSum;
    }
  }
  const r = block >> 1;
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - r), y1 = Math.min(h - 1, y + r);
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - r), x1 = Math.min(w - 1, x + r);
      const area = (x1 - x0 + 1) * (y1 - y0 + 1);
      out[y * w + x] = (I[(y1 + 1) * iw + (x1 + 1)] - I[y0 * iw + (x1 + 1)]
                      - I[(y1 + 1) * iw + x0] + I[y0 * iw + x0]) / area;
    }
  }
  return out;
}

/**
 * Kuwahara filter - structure-preserving smoothing, integral-image
 * accelerated so the radius is free.
 *
 * This is the difference between tracing a photo and tracing noise. A plain
 * blur softens leaf/grass/skin texture and real edges alike, so the edge
 * detector downstream either sees both or neither. Kuwahara instead replaces
 * each pixel with the mean of the least-varying quadrant around it: texture
 * averages away, but a quadrant that straddles a real edge has high variance
 * and never gets picked, so edges stay crisp. Busy photos (forests, streets)
 * collapse into flat paintable regions with clean boundaries.
 *
 * `channels` (optional) are smoothed with the same quadrant choice as the
 * luma, which keeps colour regions aligned with the structure.
 */
function kuwahara(luma, w, h, r, channels) {
  const iw = w + 1;
  function integral(src) {
    const I = new Float64Array(iw * (h + 1));
    for (let y = 0; y < h; y++) {
      let row = 0;
      for (let x = 0; x < w; x++) {
        row += src[y * w + x];
        I[(y + 1) * iw + x + 1] = I[y * iw + x + 1] + row;
      }
    }
    return I;
  }
  const sq = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) sq[i] = luma[i] * luma[i];
  const Is = integral(luma), Iq = integral(sq);
  const Ic = (channels || []).map(integral);
  const rect = (I, x0, y0, x1, y1) =>
    I[(y1 + 1) * iw + x1 + 1] - I[y0 * iw + x1 + 1] - I[(y1 + 1) * iw + x0] + I[y0 * iw + x0];

  const out = new Float32Array(w * h);
  const outC = (channels || []).map(() => new Float32Array(w * h));
  for (let y = 0; y < h; y++) {
    const yA0 = Math.max(0, y - r), yB1 = Math.min(h - 1, y + r);
    for (let x = 0; x < w; x++) {
      const xA0 = Math.max(0, x - r), xB1 = Math.min(w - 1, x + r);
      // quadrants: [x0,y0,x1,y1] all inclusive, each containing the pixel
      let best = Infinity, bx0 = x, by0 = y, bx1 = x, by1 = y, bArea = 1;
      for (let q = 0; q < 4; q++) {
        const x0 = q & 1 ? x : xA0, x1 = q & 1 ? xB1 : x;
        const y0 = q & 2 ? y : yA0, y1 = q & 2 ? yB1 : y;
        const area = (x1 - x0 + 1) * (y1 - y0 + 1);
        const s = rect(Is, x0, y0, x1, y1);
        const v = rect(Iq, x0, y0, x1, y1) / area - (s / area) * (s / area);
        if (v < best) { best = v; bx0 = x0; by0 = y0; bx1 = x1; by1 = y1; bArea = area; }
      }
      const i = y * w + x;
      out[i] = rect(Is, bx0, by0, bx1, by1) / bArea;
      for (let c = 0; c < Ic.length; c++) outC[c][i] = rect(Ic[c], bx0, by0, bx1, by1) / bArea;
    }
  }
  return { luma: out, channels: outC };
}

/**
 * Remove ink islands smaller than minArea pixels. Busy photos leave the edge
 * detectors with a dusting of tiny disconnected specks ("confetti") that no
 * one wants to trace; the real subject survives as large connected strokes.
 */
function despeckle(alpha, w, h, minArea) {
  const n = w * h;
  // A long 1px contour line is low-area but high-extent - it's a stroke, not
  // a speck. Drop a component only when it is small in BOTH senses.
  const minSpan = Math.max(24, Math.round((w + h) / 40));
  const seen = new Uint8Array(n);
  const stack = new Int32Array(n);
  const comp = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    if (seen[i] || alpha[i] < 0.3) continue;
    let top = 0, size = 0;
    let x0 = w, x1 = 0, y0 = h, y1 = 0;
    stack[top++] = i; seen[i] = 1;
    while (top > 0) {
      const p = stack[--top];
      comp[size++] = p;
      const px = p % w, py = (p / w) | 0;
      if (px < x0) x0 = px; if (px > x1) x1 = px;
      if (py < y0) y0 = py; if (py > y1) y1 = py;
      // 8-connectivity: 1px lines step diagonally, touching only at corners.
      // 4-connectivity would shatter every diagonal stroke into single-pixel
      // "components" and delete them all as specks.
      const xl = px > 0, xr = px < w - 1, yu = py > 0, yd = py < h - 1;
      if (xl && !seen[p - 1] && alpha[p - 1] >= 0.3) { seen[p - 1] = 1; stack[top++] = p - 1; }
      if (xr && !seen[p + 1] && alpha[p + 1] >= 0.3) { seen[p + 1] = 1; stack[top++] = p + 1; }
      if (yu && !seen[p - w] && alpha[p - w] >= 0.3) { seen[p - w] = 1; stack[top++] = p - w; }
      if (yd && !seen[p + w] && alpha[p + w] >= 0.3) { seen[p + w] = 1; stack[top++] = p + w; }
      if (xl && yu && !seen[p - w - 1] && alpha[p - w - 1] >= 0.3) { seen[p - w - 1] = 1; stack[top++] = p - w - 1; }
      if (xr && yu && !seen[p - w + 1] && alpha[p - w + 1] >= 0.3) { seen[p - w + 1] = 1; stack[top++] = p - w + 1; }
      if (xl && yd && !seen[p + w - 1] && alpha[p + w - 1] >= 0.3) { seen[p + w - 1] = 1; stack[top++] = p + w - 1; }
      if (xr && yd && !seen[p + w + 1] && alpha[p + w + 1] >= 0.3) { seen[p + w + 1] = 1; stack[top++] = p + w + 1; }
    }
    if (size < minArea && (x1 - x0) + (y1 - y0) < minSpan) {
      for (let k = 0; k < size; k++) alpha[comp[k]] = 0;
    }
  }
  return alpha;
}

/**
 * Merge quantised regions smaller than minRegion into their dominant
 * neighbour. This is the step every real paint-by-numbers generator has:
 * without it a busy photo shatters into hundreds of outlined islands no one
 * could trace, let alone paint. Runs a few passes so merges cascade.
 */
function mergeSmallRegions(qi, cols, w, h, minRegion, passes) {
  const n = w * h;
  const label = new Int32Array(n);
  const stack = new Int32Array(n);
  const comp = new Int32Array(n);
  for (let pass = 0; pass < passes; pass++) {
    label.fill(0);
    let next = 0, merged = 0;
    for (let i = 0; i < n; i++) {
      if (label[i]) continue;
      const myQi = qi[i];
      next++;
      let top = 0, size = 0;
      stack[top++] = i; label[i] = next;
      while (top > 0) {
        const p = stack[--top];
        comp[size++] = p;
        const px = p % w, py = (p / w) | 0;
        if (px > 0 && !label[p - 1] && qi[p - 1] === myQi) { label[p - 1] = next; stack[top++] = p - 1; }
        if (px < w - 1 && !label[p + 1] && qi[p + 1] === myQi) { label[p + 1] = next; stack[top++] = p + 1; }
        if (py > 0 && !label[p - w] && qi[p - w] === myQi) { label[p - w] = next; stack[top++] = p - w; }
        if (py < h - 1 && !label[p + w] && qi[p + w] === myQi) { label[p + w] = next; stack[top++] = p + w; }
      }
      if (size >= minRegion) continue;
      // Vote for the neighbouring value with the longest shared border.
      const counts = new Map();
      for (let k = 0; k < size; k++) {
        const p = comp[k];
        const px = p % w, py = (p / w) | 0;
        for (const nb of [px > 0 ? p - 1 : -1, px < w - 1 ? p + 1 : -1,
                          py > 0 ? p - w : -1, py < h - 1 ? p + w : -1]) {
          if (nb < 0 || qi[nb] === myQi) continue;
          const e = counts.get(qi[nb]);
          if (e) e.count++; else counts.set(qi[nb], { count: 1, sample: nb });
        }
      }
      let best = null;
      for (const e of counts.values()) if (!best || e.count > best.count) best = e;
      if (!best) continue;
      const bq = qi[best.sample];
      const sr = cols ? cols[best.sample * 3] : 0;
      const sg = cols ? cols[best.sample * 3 + 1] : 0;
      const sb = cols ? cols[best.sample * 3 + 2] : 0;
      for (let k = 0; k < size; k++) {
        const p = comp[k];
        qi[p] = bq;
        if (cols) { cols[p * 3] = sr; cols[p * 3 + 1] = sg; cols[p * 3 + 2] = sb; }
      }
      merged++;
    }
    if (!merged) break;
  }
}

/**
 * Difference-of-Gaussians edge response, normalised by its own mean absolute
 * deviation.
 *
 * The normalisation is the whole point. A fixed threshold on the raw response
 * has no idea whether it is looking at a crisp scan or a grainy phone photo, so
 * it either misses soft edges or turns every speck of sensor noise into ink.
 * Measuring the image's own response level first makes one threshold work for
 * both. `soft` widens the transition for a pencil-ish edge.
 */
function dogInk(gray, w, h, sigma, k, soft) {
  const n = w * h;
  const g1 = gaussBlur(gray, w, h, sigma);
  const g2 = gaussBlur(gray, w, h, sigma * 1.6);
  const diff = new Float32Array(n);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    diff[i] = g1[i] - g2[i];
    sum += Math.abs(diff[i]);
  }
  const mad = sum / n || 1e-6;
  // The response is negative on the dark side of an edge; keeping the sign
  // gives one line per edge instead of the double ridge |DoG| would produce.
  const lo = -k * mad, hi = -k * mad * (1 + soft);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = smoothstep(lo, hi, diff[i]);
  return out;
}

function smoothstep(a, b, x) {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a || 1e-6)));
  return t * t * (3 - 2 * t);
}

/**
 * Grey value below which a given fraction of the image's pixels fall.
 * Otsu assumes the image is roughly bimodal (a clear light group and a clear
 * dark group); a light subject on a light background has no such split, and
 * Otsu's variance-maximising cut can land almost anywhere, sometimes leaving
 * stencil with almost nothing inked. Picking the cut directly from the
 * desired ink fraction always inks *something*, and ties the threshold
 * slider straight to "how much of the picture becomes ink".
 */
function percentileThreshold(v, n, frac) {
  const hist = new Int32Array(256);
  for (let i = 0; i < n; i++) hist[Math.max(0, Math.min(255, (v[i] * 255) | 0))]++;
  const target = frac * n;
  let acc = 0;
  for (let t = 0; t < 256; t++) {
    acc += hist[t];
    if (acc >= target) return t / 255;
  }
  return 1;
}

/* --------------------------------------------------------------- driver */

/**
 * @param {ImageData} imageData source pixels (already scaled down)
 * @param {object} o {preset, detail, threshold, thickness, invert, colour, knockWhite}
 *                   detail/threshold/thickness are 0..1
 * @returns {ImageData} straight-alpha RGBA
 */
function applyStyle(imageData, o) {
  const w = imageData.width, h = imageData.height;
  const src = imageData.data;
  const n = w * h;
  const gray = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    // Rec.709 luma, and un-premultiply against white so transparent source
    // pixels read as paper rather than as black edges.
    const a = src[i * 4 + 3] / 255;
    const r = src[i * 4] * a + 255 * (1 - a);
    const g = src[i * 4 + 1] * a + 255 * (1 - a);
    const b = src[i * 4 + 2] * a + 255 * (1 - a);
    gray[i] = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  }

  const detail = o.detail ?? 0.5;
  const threshold = o.threshold ?? 0.5;
  const thickness = o.thickness ?? 0.2;
  const scale = Math.max(w, h) / 1000;
  const thickR = Math.round(thickness * 6 * Math.max(1, scale));

  let alpha = new Float32Array(n);
  let rgb = null;                        // set only by ghost / original

  // Phone photos carry enough sensor grain to swamp any edge detector, so
  // everything that looks for edges works from a lightly denoised copy.
  const denoised = gaussBlur(gray, w, h, 0.8 * Math.max(1, scale));

  // Structure image for the edge presets: Kuwahara flattens texture (leaves,
  // grass, brick, skin grain) into flat regions while keeping real edges
  // crisp, so the detectors trace shapes instead of noise. Two passes: the
  // first softens texture, the second collapses what's left into flat
  // paintable regions. The detail slider widens the radius.
  const kr = Math.round((3.5 + (1 - detail) * 6) * Math.max(1, scale));
  const needsStructure = o.preset === 'bold'
    || o.preset === 'contour' || o.preset === 'stencil';
  const structure = needsStructure
    ? kuwahara(kuwahara(denoised, w, h, kr).luma, w, h, kr).luma : null;

  // Shared by 'clean' and 'paint': flatten colour along with luma, then
  // quantise into regions. The region boundaries are the closed "colouring
  // book" outlines a person would actually trace - unlike raw edge response,
  // they stay coherent even on texture-heavy photos (forests, streets).
  function flattenedRegions(levels) {
    const rCh = new Float32Array(n), gCh = new Float32Array(n), bCh = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const a = src[i * 4 + 3] / 255;
      rCh[i] = src[i * 4] * a + 255 * (1 - a);
      gCh[i] = src[i * 4 + 1] * a + 255 * (1 - a);
      bCh[i] = src[i * 4 + 2] * a + 255 * (1 - a);
    }
    const k1 = kuwahara(denoised, w, h, kr, [rCh, gCh, bCh]);
    const k2 = kuwahara(k1.luma, w, h, kr, k1.channels);
    const ch = k2.channels.map((c) => gaussBlur(c, w, h, 1));
    const step = 256 / levels;
    const qi = new Int32Array(n);
    const cols = new Uint8ClampedArray(n * 3);
    for (let i = 0; i < n; i++) {
      const qr = Math.min(levels - 1, Math.floor(ch[0][i] / step));
      const qg = Math.min(levels - 1, Math.floor(ch[1][i] / step));
      const qb = Math.min(levels - 1, Math.floor(ch[2][i] / step));
      qi[i] = (qr * levels + qg) * levels + qb;
      cols[i * 3] = Math.round((qr + 0.5) * step);
      cols[i * 3 + 1] = Math.round((qg + 0.5) * step);
      cols[i * 3 + 2] = Math.round((qb + 0.5) * step);
    }
    // Less detail -> larger minimum region -> fewer, bigger paintable shapes.
    const minRegion = Math.round(n * (3e-4 + (1 - detail) * 2e-3));
    mergeSmallRegions(qi, cols, w, h, minRegion, 3);
    return { luma: k2.luma, qi, cols };
  }

  switch (o.preset) {
    case 'sketch': {
      // One light Kuwahara pass (half radius) keeps the soft shading edges
      // that clean's heavy flattening throws away; a wide DoG transition
      // renders them as broad pencil strokes instead of speckle.
      const soft = kuwahara(denoised, w, h, Math.max(1, kr >> 1)).luma;
      const sigma = (0.9 + (1 - detail) * 2.0) * Math.max(0.7, scale);
      const ink = dogInk(soft, w, h, sigma, 0.55 + threshold * 2.2, 2.5);
      for (let i = 0; i < n; i++) alpha[i] = ink[i];
      break;
    }
    case 'clean': {
      // Region boundaries carry the drawing; a coarse DoG on the flattened
      // luma adds the line-like details (branches, eyelids, lettering) that
      // colour quantisation alone misses.
      const reg = flattenedRegions(Math.max(3, Math.round(4 + threshold * 6)));
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = y * w + x;
          alpha[i] = ((x + 1 < w && reg.qi[i + 1] !== reg.qi[i])
                   || (y + 1 < h && reg.qi[i + w] !== reg.qi[i])) ? 1 : 0;
        }
      }
      const sigma = (1.3 + (1 - detail) * 2.6) * Math.max(0.7, scale);
      const ink = dogInk(reg.luma, w, h, sigma, 1.2 + threshold * 3.6, 0.35);
      for (let i = 0; i < n; i++) alpha[i] = Math.max(alpha[i], ink[i]);
      break;
    }
    case 'bold': {
      const sigma = (1.2 + (1 - detail) * 3.4) * Math.max(0.7, scale);
      const g = gaussBlur(structure, w, h, sigma);
      const mag = new Float32Array(n);
      let sum = 0;
      for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
          const i = y * w + x;
          const gx = -g[i - w - 1] - 2 * g[i - 1] - g[i + w - 1]
                   + g[i - w + 1] + 2 * g[i + 1] + g[i + w + 1];
          const gy = -g[i - w - 1] - 2 * g[i - w] - g[i - w + 1]
                   + g[i + w - 1] + 2 * g[i + w] + g[i + w + 1];
          mag[i] = Math.hypot(gx, gy);
          sum += mag[i];
        }
      }
      // Mean magnitude, not the peak: one specular highlight shouldn't decide
      // the threshold for the whole picture.
      const avg = sum / n || 1e-6;
      const cut = avg * (1.2 + threshold * 7);
      for (let i = 0; i < n; i++) alpha[i] = smoothstep(cut * 0.55, cut, mag[i]);
      break;
    }
    case 'contour': {
      const sigma = (1.2 + (1 - detail) * 4.5) * Math.max(0.7, scale);
      const g = gaussBlur(structure, w, h, sigma);
      // Quantise across the picture's actual tonal range - a low-contrast
      // photo would otherwise fall entirely inside one or two fixed bands.
      let lo = Infinity, hi = -Infinity;
      for (let i = 0; i < n; i++) { if (g[i] < lo) lo = g[i]; if (g[i] > hi) hi = g[i]; }
      const span = Math.max(1e-4, hi - lo);
      const levels = Math.max(3, Math.round(4 + threshold * 14));
      const lv = new Int16Array(n);
      for (let i = 0; i < n; i++) {
        lv[i] = Math.min(levels - 1, Math.floor((g[i] - lo) / span * levels));
      }
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = y * w + x;
          const v = lv[i];
          alpha[i] = ((x + 1 < w && lv[i + 1] !== v) || (y + 1 < h && lv[i + w] !== v)) ? 1 : 0;
        }
      }
      break;
    }
    case 'paint': {
      // Posterize in full colour (not just tone) and outline the region
      // boundaries - the only preset carrying colour through to the page,
      // so tracing gives you both the lines and which colour goes where.
      const reg = flattenedRegions(Math.max(2, Math.round(3 + threshold * 5)));
      rgb = reg.cols;
      const outlineCol = o.colour || [16, 16, 20];
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = y * w + x;
          const edge = (x + 1 < w && reg.qi[i + 1] !== reg.qi[i])
                    || (y + 1 < h && reg.qi[i + w] !== reg.qi[i]);
          if (edge) { rgb[i * 3] = outlineCol[0]; rgb[i * 3 + 1] = outlineCol[1]; rgb[i * 3 + 2] = outlineCol[2]; }
          alpha[i] = 1;
        }
      }
      break;
    }
    case 'stencil': {
      const g = gaussBlur(structure, w, h, 0.5 + (1 - detail) * 2.5);
      // The slider directly sets how much of the picture becomes ink
      // (see percentileThreshold) rather than relying on Otsu, which can
      // pick a near-useless split on a light subject over a light background.
      const frac = 0.12 + threshold * 0.45;
      const t = percentileThreshold(g, n, frac);
      for (let i = 0; i < n; i++) alpha[i] = smoothstep(t + 0.02, t - 0.02, g[i]);
      break;
    }
    case 'ghost':
    case 'original': {
      rgb = new Uint8ClampedArray(n * 3);
      for (let i = 0; i < n; i++) {
        if (o.preset === 'ghost') {
          const v = Math.min(255, Math.max(0, gray[i] * 255));
          rgb[i * 3] = v; rgb[i * 3 + 1] = v; rgb[i * 3 + 2] = v;
        } else {
          const a = src[i * 4 + 3] / 255;
          rgb[i * 3] = src[i * 4] * a + 255 * (1 - a);
          rgb[i * 3 + 1] = src[i * 4 + 1] * a + 255 * (1 - a);
          rgb[i * 3 + 2] = src[i * 4 + 2] * a + 255 * (1 - a);
        }
        // Knocking out paper-white keeps the camera visible through the
        // background instead of pasting a white sheet over your real one.
        alpha[i] = o.knockWhite === false ? 1
          : Math.min(1, Math.max(0, (1 - gray[i]) * 1.25 + (threshold - 0.5) * 0.6));
      }
      break;
    }
    default:
      throw new Error('unknown preset: ' + o.preset);
  }

  if (!rgb) {
    // Sub-ink fog (partial alphas from smoothstep tails) reads as grey dust
    // on busy photos - snap it to nothing before sizing the islands.
    for (let i = 0; i < n; i++) if (alpha[i] < 0.3) alpha[i] = 0;
    despeckle(alpha, w, h, Math.max(8, Math.round(n * 8e-5)));
  }
  if (o.invert && !rgb) for (let i = 0; i < n; i++) alpha[i] = 1 - alpha[i];
  if (thickR > 0 && !rgb) alpha = dilate(alpha, w, h, thickR);

  const out = new ImageData(w, h);
  const dst = out.data;
  const col = o.colour || [16, 16, 20];
  for (let i = 0; i < n; i++) {
    if (rgb) {
      dst[i * 4] = rgb[i * 3];
      dst[i * 4 + 1] = rgb[i * 3 + 1];
      dst[i * 4 + 2] = rgb[i * 3 + 2];
    } else {
      dst[i * 4] = col[0];
      dst[i * 4 + 1] = col[1];
      dst[i * 4 + 2] = col[2];
    }
    const a = alpha[i];
    dst[i * 4 + 3] = a > 0 ? (a < 1 ? a * 255 : 255) : 0;
  }
  return out;
}
