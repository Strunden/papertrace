# PaperTrace AR

Hold your phone over your sketchbook and the reference picture appears lying on
the page, in correct perspective. Printed tags keep it locked to the paper while
you draw, so the overlay stays put even though the phone is in your hand.

Everything runs in the browser. No account, no upload, no network requests once
the page has loaded.

---

## Files

| File | What it is |
|---|---|
| `papertrace.html` | The whole app. One self-contained file — open it or host it. |
| `papertrace-markers-A4.pdf` | Printable tracking tags, A4, with instructions on page 1. Cut out and stick anywhere. |
| `papertrace-markers-Letter.pdf` | Same, US Letter. |
| `papertrace-canvas-A4.pdf` | Ready-to-draw page: 4 tags pre-printed in the corners, blank paper in between. Nothing to cut out. |
| `papertrace-canvas-Letter.pdf` | Same, US Letter. |

The app can also print its own tag sheet (**Tags → Tag sheet to print**), so the
HTML file is enough on its own if you'd rather not keep the PDFs around.

---

## Getting it onto your phone

Browsers only allow camera access on a **secure** page — `https://` or
`localhost`. A file opened from the Files app does not qualify on iPhone, so the
page has to be served from somewhere. Any of these takes about a minute:

- **Netlify Drop** — drag `papertrace.html` onto `app.netlify.com/drop`. It hands
  you an https link immediately, no account. Open that link on your phone. This
  is the quickest route.
- **GitHub Pages** — commit the file to a repo and switch Pages on.
- **A tunnel from your Mac** — serve the folder (`python3 -m http.server 8000`)
  and expose it with `cloudflared tunnel --url http://localhost:8000`.

On a desktop or laptop, opening the file directly usually works in Chrome, which
is handy for trying out the trace styles before printing anything.

---

## Using it

1. **Print the tags** at 100% scale (no "fit to page"). Cut them out, keeping the
   white border — the tracker needs it. Sticker paper is nicer than plain, but
   matte, not glossy: gloss reflects and blinds the tracker.
2. **Stick 3–6 tags** around your drawing area. Any arrangement — scattered,
   rotated, mixed sizes. They do not need to form a square or a known pattern.
3. **Open the app, start the camera, and sweep slowly across all the tags once.**
   The app measures where each tag sits relative to the others and builds a map
   of your desk. After that, any single tag in view is enough to hold the pose.
4. **Pick an image**, choose a trace style, drag and pinch it into position, then
   **Lock placement** so a stray touch can't nudge it, and draw.

### The one thing that matters most

**Keep two tags in view when you can.** Two tags pin the perspective down
properly. With only one, the tilt of the page has to be inferred from four
corners, and sub-pixel measurement error gets amplified the further you look
from that tag — so the overlay wanders by a millimetre or two. It still works,
it's just visibly less steady. Measured on synthetic scenes: two tags give
~0.15 px mean error, one tag ~1.3 px, both at 640 px detection width.

That's also why the tags should be **spread around** the drawing rather than
clustered in one corner: your drawing hand will cover some of them, and you want
at least two of the survivors visible.

Other things that help: even diffuse light (glare on a tag hides it), flat tags
(a curled one bends the perspective estimate), bigger tags for longer range
(20 mm sketchbook / 30 mm A4 on a desk / 45 mm easel), and a phone stand — not
because tracking needs it, but because it frees both hands.

If the overlay ever drifts, **Tags → Reset anchors** and sweep again.

---

## Trace styles

Photographs are hard to trace directly, so the app converts whatever you load
into line work.

| Style | Use it for |
|---|---|
| **Clean lines** | The everyday default. Crisp single-weight outline. |
| **Sketch** | Softer, pencil-like; keeps more shading detail. |
| **Bold outline** | Only the strongest edges, thickened. Good in bright light. |
| **Contour map** | Tones become nested bands, like a colouring book. The one for painting. |
| **Stencil** | Solid black shapes. Lettering, logos, silhouettes. |
| **Ghost** | The photo itself, faded — for shading reference rather than outlines. |
| **Original** | Untouched, for artwork that is already line art. |

**Detail** sets how much fine texture survives, **Threshold** how much of the
image counts as an edge, **Thickness** fattens the lines for bright conditions.
**Knock out white background** makes paper-white areas transparent so you can see
your real page through them — leave it on for line art.

Nine flower drawings are built in as vector art, so they stay sharp at any zoom.
**Image → + Your image** loads anything from your camera roll or files.

---

## Freehand mode

**Tags → Freehand mode** pins the image to the screen instead of the paper. It's
there for a quick look before you've printed anything. It will drift as soon as
you move the phone, which is exactly the problem the tags solve.

---

## How the tracking works, briefly

Each tag is a square with a known pattern. Seeing one square gives enough
geometry to recover the plane's pose, which is what lets the overlay sit *on* the
page rather than floating on the screen.

The tags don't need a known layout because the app builds one. The first tag it
sees defines the paper's coordinate frame; every other tag is placed into that
frame from frames where it's seen alongside an already-known tag. Two things keep
that map accurate:

- Each tag is known to be a *square*, so a raw back-projection is replaced by the
  best-fit square. That discards four degrees of freedom of pure noise.
- Frames that saw two or more tags together are kept as keyframes, and the whole
  map is periodically re-solved from them, with each tag's position estimated
  from poses that *exclude that tag*. Without the leave-one-out part the solve
  just reproduces whatever the map already says and never corrects a bad early
  guess.

The overlay is drawn in WebGL with the homography's `w` fed straight into
`gl_Position.w`, so the GPU's own perspective-correct interpolation does the
projective texture mapping.

---

## Debugging tracking issues

If tags won't anchor, open the **Tags** panel and tap **Save debug log** after
pointing the camera at them for a few seconds - it downloads a text file
recording, frame by frame, what was detected and exactly why each tag did or
didn't register (too small in frame, phone not moving enough, bad fit, etc).

To test *without a phone in hand at all*: `test/replay.py` feeds a recorded
video into the real, unmodified app as a fake camera stream
(`HTMLVideoElement.captureStream()` standing in for `getUserMedia`), headless,
and prints that same debug log plus a screenshot of the last frame. Requires
`ffmpeg` (footage gets transcoded to H.264 first - headless Chromium can't
decode iPhone's default HEVC) and the dev deps in `.venv` (`pip install
playwright reportlab numpy pillow && playwright install chromium`).

```
.venv/bin/python test/replay.py path/to/footage.mov
```

---

## Privacy

Camera frames, the images you load, and everything derived from them stay in the
browser. Nothing is uploaded and the app makes no network requests after load.
It also stores nothing — settings reset when you reload the page.

---

## Browser support

Needs `getUserMedia` and WebGL: iOS Safari 15+, Chrome/Edge on Android and
desktop, Firefox. Rear camera is requested automatically. The torch button only
lights up if your device exposes torch control (most Android phones do; iOS
Safari does not).
