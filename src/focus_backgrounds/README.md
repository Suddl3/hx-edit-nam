# Focus View backgrounds

Scene backgrounds for the Helix Floor's Focus View (HX Unbound firmware): the big picture of the selected
block's gear sits in a scene (a table, a stage, deep space, a rainy city...) instead of on plain black.

**Every picture here is newly generated.** The scenes are AI image renders made from short written
descriptions (empty surfaces, no gear, no text), then framed and toned for the Floor's screen by
`focus_bg.py`. *Black* and *Soft Glow* are drawn by code (`make_plain_plates.py`), no AI. The gear in the previews is the project's own render set (`../helix_model_icons`). No
Line 6 artwork is included, copied or traced.

## Contents

- `backgrounds/` – 25 plain backgrounds, 800 × 480, full colour PNG. These are what goes to the unit.
- `previews/` – the same backgrounds with one piece of gear and a stand-in for the Floor's UI, dithered to
  the screen's 15-bit colour, to judge the look and legibility. Not for the unit.
- `contact-sheet.png` – all 25 previews on one sheet.
- `focus_backgrounds.json` – the manifest: screen facts, each style's files, status, preview gear and the
  settings it was made with.
- `focus_bg.py`, `styles.json`, `make_plain_plates.py` – the generator. `python focus_bg.py pack <folder>` rebuilds everything.
  It needs the source plates (1536 px, shared as a separate pack, not in git) in `plates/` or
  `FOCUS_PLATES`, and the gear renders' `full/` set in `../helix_model_icons` or `HELIX_ICONS`. Pillow only.

Groups (`group` in the manifest): 18 *scene* backgrounds (table, stage, space, synthwave...) and 7
*utility* ones for I/O, split/merge, FX loop and looper blocks: Patch Bay, Rack, Road Cases, Front of
House, Anodized Panel, Soft Glow and plain Black.

Status: `locked` = chosen (Synthwave, Neon Rain); `variant` = an alternative of a locked style (Neon Rain
simple); `candidate` = still open.

## Screen layout the backgrounds follow

From the firmware findings (`hx-unbound-floor`, `helix-floor/docs/FINDINGS.md`, 2026-11-13/14):

- 800 × 480, **RGB555** (R << 10 | G << 5 | B, little-endian, rows from the top), 32 levels per channel.
  Convert with an ordered dither (`focus_bg.rgb555`, `to_rgb555_bytes`) or gradients band.
- The gear picture box is 256 × 256 at x 272..528, y 68..324 (the eMMC picture store's size).
- y < 52 is pure black (the stock title bar), fading in until y 96. From y 300 the scene fades out and
  y ≥ 342 is pure black (the parameter area). The stock widgets paint on black, so they sit on the
  background without a seam.
- The area right behind the gear is kept calm; key features (sun, galaxy, aurora) sit beside or above it.

## How they could get onto the Floor (proposal, nothing built yet)

- **Storage is not the limit.** The Focus View pictures already live on the eMMC past every partition
  (sector 0x400000 and up, written over USB by `floor_emmc_art.py`, ~1.7 GiB free there). A background is
  750 KB in RGB555; all 25 are ~19 MB. Do **not** grow Entry 9 (the image partition) for them: it has ~1.9 MB
  spare and overrunning it destroyed user data once (FOCUS11).
- **Slots, not themes, in the firmware.** The firmware reads a small table *category → background slot*
  and draws that slot when the selected block's category changes; the PC side (HX Edit / the upload tool)
  decides which scene goes in which slot (theme packs, one per category, user choice) and uploads them.
  Changing the assignment is then a re-upload, not a firmware build.
- **Gear on the background:** simplest for the firmware is that the PC uploads each gear picture already
  composited onto its 256 × 256 crop of the background it will be shown on, so the firmware only copies
  pixels (no alpha blending in the tight code cave). Alternative: store the gear with alpha and blend on
  the unit. To decide with the firmware side.
- `assignment` in the manifest is `null` until that is decided.

## Title font (suggestion)

The previews draw the title with an anti-aliased font (DejaVu Sans Condensed, freely redistributable)
to show what the screen can do. On the unit this would be a 4-bit anti-aliased glyph sheet baked on the
PC (~40 KB for two sizes), blended over the background by the firmware; or each model's title
pre-rendered as an image next to its picture. Not built yet.
