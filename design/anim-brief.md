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
- **Through-window continuity**: geometry (marks, frame, pencil, hands) continues
  across the screen edge with no offset. The screen is a re-render of reality
  plus the daisy.
- **Reduced motion**: `prefers-reduced-motion` shows a meaningful static final
  state (everything visible, no animation).
- The two labels ("printed sheet", "phone") and the synced 3-phase caption strip
  (`#howCaption`, phases: finding marks → picture appears on screen → trace on
  paper) stay, though you may restyle and re-time them.

## Visual language
Match the app's paper aesthetic: warm paper ground (`#faf6ec…#e9e0cc` radial),
ink `#26221b`/`#2b261d`, muted ink `#5a5342`/`#6f695c`, hand fill `#f6efe0`,
pencil gold `#c9a227`. Serif display comes from the surrounding screen; the
graphic itself is ink-outline flat illustration. No gradients inside the SVG, no
photorealism, no emoji.

Reference conventions to copy (researched): Google ARCore onboarding guidance —
hand+device in motion over the surface, anchored content demonstrating
persistence, highlight feedback on detection, instruction visuals consistent
with the app. Study any AR-app "scan your surface" onboarding you know for
shape quality and motion feel; the current version's weakness is **crude
shapes** (hands especially) and **stiff motion**, not the architecture.

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
