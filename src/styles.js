/* ---------------------------------------------------------------------------
 * styles.js - turn any picture into something you can actually trace.
 *
 * Everything outputs straight-alpha RGBA: line pixels are opaque, everything
 * else is fully transparent, so the camera shows through the gaps instead of
 * a white rectangle sitting on your paper.
 *
 * The line work itself comes from a neural model (Informative Drawings, run
 * in app.js) - classical edge/region filters were tried and retired: pixel
 * statistics can't tell an eye from a jacket wrinkle, so their output was
 * never worth tracing. This file only maps images and the model's line map
 * onto the paper.
 * ------------------------------------------------------------------------- */

const STYLE_PRESETS = [
  { id: 'artist',   name: 'Artist sketch', hint: 'Clean, delicate line drawing by a neural model trained on artist drawings. Downloads ~28 MB once, then works offline.' },
  { id: 'rough',    name: 'Rough sketch', hint: 'Loose construction-line sketch, like an underdrawing. Downloads ~16 MB once.' },
  { id: 'ink',      name: 'Ink brush',   hint: 'Bold brush-and-ink strokes. Great for markers and strong outlines. Downloads ~4 MB once.' },
  { id: 'painting', name: 'Watercolour', hint: 'Soft watercolour-style painting (Hayao style) - a colour guide for painting what you traced. Downloads ~8 MB once.' },
  { id: 'vivid',    name: 'Vivid paint', hint: 'Crisp, vivid painted look (Shinkai style) - a brighter colour guide. Downloads ~8 MB once.' },
  { id: 'paprika',  name: 'Paprika',     hint: 'Warm, poster-like painted look (Paprika style) - a bolder colour guide. Downloads ~9 MB once.' },
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

/** Bilinear resample of a single-channel float map to (w, h). */
function resampleMap(map, w, h) {
  if (map.w === w && map.h === h) return map.data;
  const out = new Float32Array(w * h);
  const sx = (map.w - 1) / Math.max(1, w - 1);
  const sy = (map.h - 1) / Math.max(1, h - 1);
  for (let y = 0; y < h; y++) {
    const fy = y * sy, y0 = Math.floor(fy), y1 = Math.min(map.h - 1, y0 + 1), ty = fy - y0;
    for (let x = 0; x < w; x++) {
      const fx = x * sx, x0 = Math.floor(fx), x1 = Math.min(map.w - 1, x0 + 1), tx = fx - x0;
      const a = map.data[y0 * map.w + x0] * (1 - tx) + map.data[y0 * map.w + x1] * tx;
      const b = map.data[y1 * map.w + x0] * (1 - tx) + map.data[y1 * map.w + x1] * tx;
      out[y * w + x] = a * (1 - ty) + b * ty;
    }
  }
  return out;
}

function smoothstep(a, b, x) {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a || 1e-6)));
  return t * t * (3 - 2 * t);
}

/* --------------------------------------------------------------- driver */

/**
 * @param {ImageData} imageData source pixels (already scaled down)
 * @param {object} o {preset, threshold, thickness, invert, colour, knockWhite,
 *                    neuralMaps} - threshold/thickness are 0..1
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

  const threshold = o.threshold ?? 0.18;
  const thickness = o.thickness ?? 0.15;
  const scale = Math.max(w, h) / 1000;
  const thickR = Math.round(thickness * 6 * Math.max(1, scale));

  let alpha = new Float32Array(n);
  let rgb = null;                        // set only by ghost / original

  switch (o.preset) {
    case 'artist':
    case 'rough':
    case 'ink': {
      // The neural line drawing (ink strength 0..1) is computed
      // asynchronously in app.js - this case only maps it onto the paper.
      // Without a map yet, output stays transparent; the app shows progress.
      const map = o.neuralMaps && o.neuralMaps[o.preset];
      if (map) {
        const ink = resampleMap(map, w, h);
        // Keep the model's soft pencil greys; threshold trims faint marks.
        const lo = 0.06 + threshold * 0.3;
        for (let i = 0; i < n; i++) alpha[i] = smoothstep(lo, lo + 0.3, ink[i]);
      }
      break;
    }
    case 'painting':
    case 'vivid':
    case 'paprika': {
      // The painted rendering from app.js, shown the way ghost/original show
      // the photo - a colour reference, not lines.
      const map = o.neuralMaps && o.neuralMaps[o.preset];
      rgb = new Uint8ClampedArray(n * 3);     // stays transparent without a map
      if (map) {
        const r = resampleMap({ data: map.chans[0], w: map.w, h: map.h }, w, h);
        const g = resampleMap({ data: map.chans[1], w: map.w, h: map.h }, w, h);
        const b = resampleMap({ data: map.chans[2], w: map.w, h: map.h }, w, h);
        for (let i = 0; i < n; i++) {
          rgb[i * 3] = r[i]; rgb[i * 3 + 1] = g[i]; rgb[i * 3 + 2] = b[i];
          const luma = (0.2126 * r[i] + 0.7152 * g[i] + 0.0722 * b[i]) / 255;
          alpha[i] = o.knockWhite === false ? 1
            : Math.min(1, Math.max(0, (1 - luma) * 1.25 + (threshold - 0.5) * 0.6));
        }
      }
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
