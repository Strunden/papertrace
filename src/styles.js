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

  switch (o.preset) {
    case 'sketch': {
      // Finer scale and a lower confidence bar than 'clean' - catches the
      // soft internal shading edges clean's coarser pass throws away, at
      // the cost of a noisier, more hand-drawn line.
      const sigma = (0.45 + (1 - detail) * 1.5) * Math.max(0.6, scale);
      const ink = dogInk(denoised, w, h, sigma, 0.45 + threshold * 2.0, 2.2);
      for (let i = 0; i < n; i++) alpha[i] = ink[i];
      break;
    }
    case 'clean': {
      const sigma = (0.7 + (1 - detail) * 2.6) * Math.max(0.7, scale);
      const ink = dogInk(denoised, w, h, sigma, 0.9 + threshold * 3.6, 0.35);
      for (let i = 0; i < n; i++) alpha[i] = ink[i];
      break;
    }
    case 'bold': {
      const sigma = (1.2 + (1 - detail) * 3.4) * Math.max(0.7, scale);
      const g = gaussBlur(denoised, w, h, sigma);
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
      const g = gaussBlur(denoised, w, h, sigma);
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
      const sigma = (1.2 + (1 - detail) * 4.0) * Math.max(0.7, scale);
      const rCh = new Float32Array(n), gCh = new Float32Array(n), bCh = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        const a = src[i * 4 + 3] / 255;
        rCh[i] = src[i * 4] * a + 255 * (1 - a);
        gCh[i] = src[i * 4 + 1] * a + 255 * (1 - a);
        bCh[i] = src[i * 4 + 2] * a + 255 * (1 - a);
      }
      const rB = gaussBlur(rCh, w, h, sigma);
      const gB = gaussBlur(gCh, w, h, sigma);
      const bB = gaussBlur(bCh, w, h, sigma);
      const levels = Math.max(2, Math.round(3 + threshold * 5));
      const step = 256 / levels;
      const qi = new Int32Array(n);
      rgb = new Uint8ClampedArray(n * 3);
      for (let i = 0; i < n; i++) {
        const qr = Math.min(levels - 1, Math.floor(rB[i] / step));
        const qg = Math.min(levels - 1, Math.floor(gB[i] / step));
        const qb = Math.min(levels - 1, Math.floor(bB[i] / step));
        qi[i] = (qr * levels + qg) * levels + qb;
        rgb[i * 3] = Math.round((qr + 0.5) * step);
        rgb[i * 3 + 1] = Math.round((qg + 0.5) * step);
        rgb[i * 3 + 2] = Math.round((qb + 0.5) * step);
      }
      const outlineCol = o.colour || [16, 16, 20];
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = y * w + x;
          const edge = (x + 1 < w && qi[i + 1] !== qi[i]) || (y + 1 < h && qi[i + w] !== qi[i]);
          if (edge) { rgb[i * 3] = outlineCol[0]; rgb[i * 3 + 1] = outlineCol[1]; rgb[i * 3 + 2] = outlineCol[2]; }
          alpha[i] = 1;
        }
      }
      break;
    }
    case 'stencil': {
      const g = gaussBlur(denoised, w, h, 0.5 + (1 - detail) * 2.5);
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
