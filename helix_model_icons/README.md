# Helix model icons

A set of 3D product renders, one for every stock model icon that HX Edit shows (the files in
HX Edit's `res/icons_models` folder). They use the same render style as the NAM capture icons in
`../nam_capture_icons`, so stock gear and NAM captures look like one set. Unlike the NAM pack,
these carry lettering: each render shows the model name and labels printed on the matching stock
HX Edit icon, so the models stay easy to recognise.

**Every picture here is newly generated.** They are AI image renders made from short written
descriptions of each unit's shape, colour, materials and lettering. No Line 6 artwork is included,
copied or traced; the stock icons were only used as a reference when writing the descriptions.
Model and maker names shown on the gear belong to their respective owners.

## Contents

This repository keeps only what HX Edit uses: `hxedit/` (below) and `icons.json`. The larger sets
are kept out of git to keep clones small and are shared as a separate pack:

- `full/` – the largest clean cut of each render (about 250–592 px square, transparent
  background), for larger screens such as an on-Floor focus view. Not in git.
- `128/` – the same renders at 128 × 128 px, matching `nam_capture_icons/`. Not in git.
- `icons.json` – maps every stock file name (for example `AMP_HX_BASS_Agua51.png`) to its render:
  category, display name, both image paths, the lettering asked for, and the sheet and cell it was
  cut from.
- `contact-sheet.png` – every render, labelled with the models it covers. Not in git.

## Coverage

433 stock files are covered by 422 renders. Files share a render only where the stock icons
have the same look and the same lettering (identical stock art, or channels with one shared label).
`shared_with` in `icons.json` lists the other files that use the same picture.

The `icon-inputs*` and `icon-outputs*` files are strips of small flat UI glyphs, one per input or
output choice. Each strip gets one representative picture (a cable, an output panel or an audio
interface), not one picture per glyph.

Lettering is AI-rendered and was checked by eye; small text on some renders may be soft or
simplified. Display names are derived from the stock file names and may differ slightly from the
names HX Edit shows.

## hxedit/

The same pictures made for HX Edit itself: one per file in HX Edit's `res/icons_models`, named
after it and at its size (256, 156, 148, 128 or 72 px, with the stock picture's margins). The six
input/output frame strips (`icon-inputs*`, `icon-outputs*`) have no replacement and stay stock.
`nam_catalog.patch_model_art` copies them into a prepared copy of HX Edit (test builds: the
`HX_NAV_MODEL_ART=1` build option).

