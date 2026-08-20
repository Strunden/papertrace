# Design brief: the "how PaperTrace works" explainer animation

## Where it lives
The **Print the sheet** step (`#printstep` in `src/index.template.html`) — the last
screen before the camera starts. The user has already picked a picture and a style;
this screen must make them *get* what is about to happen, wordlessly.

## The story it must tell (in one ~5.6 s loop)
1. There is a **printed sheet of paper** on the table: 8 black square marks around
   the border, an empty rectangular frame in the middle. Nothing else — the paper
   is blank inside the frame.
2. A **phone, held in a hand, pans slowly over the sheet** (the classic AR scan
   motion). Its screen is a *window*: it shows exactly the scene behind it, in
   1:1 alignment — sheet, marks, frame, and anything else physically there.
3. The marks **pulse** as they are recognised (detection feedback).
4. The user's **picture (the daisy) appears — ONLY inside the screen**, anchored
   to the sheet's frame. It must NEVER appear on the bare paper.
5. A **hand with a pencil traces** on the real paper. The traced pencil stroke is
   the only thing that appears on the paper itself.
6. Loop.

## Hard invariants (violating any of these fails the review)
- **Anchoring**: when the phone moves, the daisy and everything seen through the
  screen stay fixed relative to the sheet. The window moves; the world does not.
  `test/anim_preview.py` measures this (phone must move > 5 px between pan
  extremes, daisy exactly 0) and must print PASS.
- **Screen-only picture**: the daisy exists only inside the phone-screen clip.
- **Camera truth** (replaces the old 1:1-continuity rule after the Round-1
  review): the view through the screen must be geometrically consistent with
  the phone's pose relative to the sheet. The Round-1 winner showed an
  upright phone over an obliquely-projected sheet whose screen displayed a
  perfectly rectified straight-down view - physically impossible, and the
  flaw Frank called out. Fix it one of two honest ways: (a) pose the phone
  parallel to the sheet (drawn foreshortened, tilted just enough that the
  viewer still sees its screen) so a near-rectified view is truthful, or
  (b) keep the upright phone but render the screen content with the
  perspective the camera would actually see from that pose (skewed sheet,
  daisy skewed onto it). Whichever you choose, a viewer who understands
  cameras must not be able to call the geometry wrong.
- **Reduced motion**: `prefers-reduced-motion` shows a meaningful static final
  state (everything visible, no animation).
- The two labels ("printed sheet", "phone") and the synced 3-phase caption strip
  (`#howCaption`, phases: finding marks → picture appears on screen → trace on
  paper) stay, though you may restyle and re-time them.

## Visual language — ROUND 2: the style is YOURS
Round 1 locked the style and produced three near-identical siblings. This
round, the visual language is the exploration. You own it. Constraints, all
practical rather than aesthetic:
- The graphic sits on the screen's warm paper ground (`#faf6ec…#e9e0cc`
  radial) — it must harmonise with that ground, not fight it. Beyond that,
  choose your own palette (a strong accent or spot colours are welcome), your
  own line weight philosophy, your own level of abstraction.
- Legible at 300–430 px wide. No photorealism, no emoji, no raster images.
- The surrounding screen uses a serif display face; your in-graphic
  typography (labels, annotations) may match it or deliberately contrast.

Reference conventions worth copying remain: Google ARCore onboarding —
device in motion over the surface, anchored content demonstrating
persistence, detection feedback. But HOW that looks is your call.

**A named style direction will be assigned per variant.** Commit to it hard —
a timid version of a style reads as a mistake; a committed one reads as a
choice.

## Round 2 composition base (decided)
Frank picked Round 1's Variant B ("perspective scene") as the winning
composition: desk in oblique projection, sheet lying on it, phone held above
with a cast shadow, screen as a camera viewfinder, hand tracing on the paper.
That composition is now the BASE in your working tree - restyle it, repose
the phone for camera truth, redraw every element in your assigned style, but
keep the scene's cast (sheet on desk, hovering phone, viewfinder, tracing
hand) and the six-beat story.

## Technical constraints
- Everything lives in two places: the `<svg id="howAnim" …>…</svg>` block in
  `src/index.template.html` and the CSS from the
  `/* … print step: how-it-works */` comment to the end of its reduced-motion
  guard in `src/style.css`. Touch nothing else.
- Pure inline SVG + CSS animations. No JS, no external assets. Keep the loop at
  5.6 s unless you re-time captions to match. `transform` on the clip rect and
  chrome must use identical keyframes (translate only — rotation desyncs them).
- Must look right at 300–430 px wide on a phone.

## Iteration loop (use it — do not eyeball blind)
```
.venv/bin/python test/anim_preview.py
```
regenerates `build/anim_phases.png` (six frozen phases side by side) and runs
the anchoring check. Read the PNG after every change. Iterate at least three
times before calling it done. `build/anim_preview.html` is the standalone page
(`freeze(0..1)` / `play()` in the console) if you want to scrub.

## What success looks like
- A stranger seeing three stills (20%, 50%, 75%) can retell the story: "phone
  finds marks on a printed page, the picture shows up on its screen sitting on
  the page, you trace it with a pencil."
- The anchoring demo is *felt*: the window slides, the daisy stays glued to the
  paper.
- Shapes look drawn by an illustrator, not programmed: confident line weight,
  believable (stylised is fine) hands, a phone that reads as a phone at 40 px.
- Harness prints PASS; reduced-motion state is a complete, readable diagram.

## Deliverable (per variant)
Modified `src/index.template.html` + `src/style.css` in your worktree, a final
`build/anim_phases.png`, and a short note: what you changed, what you copied
from which reference convention, and anything you deliberately traded away.
