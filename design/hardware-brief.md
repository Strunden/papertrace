# Hardware design brief: the PaperTrace drawing station

A physical product that replaces the printed throwaway sheet: a **foldable,
elegant drawing block** with permanent tracking markers, a swappable drawing
surface (postcard, sketch paper, anything), and an integrated phone holder —
so tracing is hands-free and nothing is reprinted, ever.

---

## 1 · How the system works today (the contract your design must honour)

PaperTrace is a web app: the phone camera looks at a printed sheet, computer
vision locks onto **eight square fiducial markers**, and the app renders the
user's picture *through the phone screen* as if lying on the paper, anchored
inside a **drawing frame**. The user traces what they see on the screen onto
the real paper with a real pencil.

**The printed sheet's exact geometry** (A4, generated, all dimensions mm):

- **Drawing frame**: 101.6 × 152.4 mm — exactly a 4×6" postcard, portrait,
  centred on the page. The user's picture is auto-stretched to fill this
  frame corner-to-corner. This is the area your product must make swappable.
- **Markers**: 8 black squares, **20 mm** each, sitting on a white quiet zone
  of **4.8 mm** on every side (total white footprint 29.6 × 29.6 mm). The
  quiet zone is functional — the detector needs the white border.
- **Placement**: the 8 marker footprints hug the frame with a **5 mm gap**
  from the frame edge: one diagonally off each frame corner, one centred on
  each frame edge (top-mid, bottom-mid, left-mid, right-mid). Total tracked
  cluster ≈ **171 × 222 mm**.
- Each marker carries a unique ID (0–7) from a custom dictionary; they are
  not interchangeable and each has a defined position AND orientation
  (all printed upright). Artwork for the exact patterns comes from the
  existing PDF (`papertrace-sheet-A4.pdf`) or the generator (`gen_canvas.py`).

**Tracking rules that shape the industrial design:**

- The app knows this layout as a *preset*: the moment **3+ markers**
  (non-collinear) are in view, the whole cluster locks instantly. After
  that, **any single visible marker** keeps the picture locked. So the
  drawing hand may cover several markers at once — as long as one remains
  visible somewhere, tracking holds. Markers on all four sides of the frame
  is the load-bearing property: keep it.
- Positions are tracked in *relative* units (marker-widths). Therefore a
  **uniformly scaled** version of the whole layout (frame + gaps + markers
  all ×k) matches the existing app with **zero software changes**. Any
  *non-uniform* change (different frame ratio, different marker offsets)
  works too but needs a one-line preset update in the app — allowed, just
  say so in the final drawings.
- The camera does NOT need to look straight down. Perspective is fine; the
  practical envelope is roughly 0–45° off vertical. It needs even light and
  no glare on the markers.

**Material truths from the CV system:**

- Markers must be **matte**, high-contrast (near-black on near-white),
  crisp-edged squares. Gloss, lamination shine, or embossing shadows blind
  the detector. Printed cardstock, matte vinyl, screen print, or engraving
  filled matte all work; clear-coat only if dead matte.
- The markers and the drawing frame must be **rigid relative to each
  other**. The cluster IS the tracked object; if a marker flexes or a
  hinge lets one panel sag 2 mm, the picture swims. Fold mechanisms must
  produce a dead-flat, repeatable deployed state.

**Phone/camera envelope math** (for the holder):

- The camera must see the full ~171 × 222 mm cluster with margin. A typical
  phone main camera has ≈ 50–55° vertical FOV (4:3). Height required to
  frame 222 mm + 20% margin: h ≈ 133 / tan(≈26°) ≈ **270–320 mm** above the
  surface if pointing straight down; a tilted camera (20–30°) at similar
  distance also works and lets the screen face the user more comfortably.
- The user looks at the **screen** while drawing, so screen visibility from
  a seated position matters as much as camera coverage. Sweet spot: camera
  sees the sheet, screen tilted toward the user's eyes, phone NOT directly
  over where the drawing hand works (shadow + collision).
- Phones vary: 60–80 mm wide, 7–10 mm thick, cameras in a corner block.
  The mount must not occlude the camera and should work in portrait.
  MagSafe is a bonus, not a base mechanism (Android exists).
- The phone stays put for a whole session: the mount must survive table
  vibration from drawing and be adjustable once, stable thereafter.

**Today's pain points your product exists to kill:**
1. The sheet is consumable — markers get reprinted with every drawing.
2. The phone must be held or improvised against a stack of books.
3. The drawing area and the paper are the same object — you draw ON the
   printed sheet, so nice paper/postcards can't be used without re-taping.

---

## 2 · The product

**One object, folded: a compact block. Unfolded: a drawing station.**

- A **rigid deck** carrying the 8 markers permanently, surrounding an
  **exchange area** where the user attaches whatever they draw on:
  a 4×6" postcard fits exactly; larger/smaller paper should also clamp in
  (think: corner pockets, a spring clip bar, flat magnets under the deck,
  or a slight recess — designer's choice, but swapping must be a 5-second,
  one-hand action that registers the paper in a repeatable position).
- An **integrated phone holder** rising from the deck (arm, gantry, or
  folding tower) placing the camera in the envelope above, hands-free,
  with screen angled to the seated user. It must fold flat with everything
  else.
- **Folds into a block** — self-protecting (markers and deck surface inside
  when closed), bag-friendly, nothing dangling. Target closed footprint:
  roughly book-sized; thickness is negotiable, elegance is not.
- **Elegant**: this sits on a desk next to sketchbooks and good pencils.
  Materials that age well (bamboo/ply, anodised aluminium, quality
  polymer, linen-textured board). The marker graphics are part of the
  product's face — treat them as designed elements, not stickers slapped on.

**Nice-to-haves** (only if they don't cost elegance): a shallow pencil
tray; a slight working tilt (10–15°) for the drawing surface with the
tracking preset unaffected (tilt is fine — rigidity is what matters); a
place to store spare postcards inside the folded block; an integrated
soft light strip for evening drawing (markers need even light).

## 3 · What to deliver

1. 2–3 distinct concepts (fold principle + silhouette), then one developed
   direction chosen with rationale.
2. Dimensioned drawings: deployed and folded states, marker positions to
   the millimetre (state clearly: "identical to A4 sheet layout" or "scaled
   ×k" or "new geometry — app preset update required").
3. The paper-attachment mechanism in detail (the 5-second swap).
4. The phone mount: adjustment ranges, camera sightline check for a
   60–80 mm phone at the stated FOV envelope, stability strategy.
5. Materials + finish spec, with the matte-markers constraint addressed.
6. A cost/complexity note: could this be a flat-pack laser-cut kit first?

## 4 · Open questions the designer may answer either way

- One fixed frame size (4×6") vs. an adapter system for A5/A6/square.
- Marker application: printed insert vs. engraved/inlaid permanent.
- Whether the phone holder doubles as the "lid" of the folded block.
