# Optimize A/B: hearing a version before it is used

Status, November 16: **A/B on the PC is built** (experiment builds, `HX_NAV_OPTIMIZE=1`). **A/B through
the Floor is written down here only**, for when the NAM Pedal firmware block lands.

## On the PC (built)

In the version panel of a capture that has a Pedal version: **Listen: Original | Pedal | Stop**, the
quality badge (Same / May differ / Not compared) and the level match.

- **The guitar.** The player's last saved guitar from Level presets ("Record my guitar", two typical
  seconds, played twice with a short gap). Without one, six seconds of the guitar part of NAM's v3
  input file (from 64 s), set to a typical guitar's peak (-14 dBFS). Neither: the panel says how to
  get one.
- **The original** is the capture's biggest model (the one the version was fitted to and the badge
  measured against), run by neural-amp-modeler, at the level a NAM block plays it (its loudness
  metadata brought to the converter's loudness target). **The pedal version** is the fitter's own
  `nam_pedal.play()`, the NAM Pedal kernel's arithmetic, on the coefficients read back from the kept
  User IR (a test checks the round trip against the pinned fitter's `ir_words`).
- **Level match.** Both brought to -18 LUFS (BS.1770, the same meter as Level presets), both lowered
  together if either would peak above -1 dBFS. The panel says how far apart they were before matching
  ("the Pedal version came out 1.4 dB quieter"): that is the level jump the player would get on the
  Floor when switching.
- **Plays on the PC's default output**, never the Floor: when Windows' default output is the Helix,
  Listen refuses and says to pick the PC's speakers or headphones.
- **Cache.** `%LOCALAPPDATA%\HX Edit NAM\ab-renders\<key>\` holds original.wav, pedal.wav (16-bit,
  48 kHz) and match.json. The key is the guitar file (and the part used), the original capture, the
  version file and a renderer revision: re-recording the guitar or optimizing again makes a new pair.
  The 24 most recently heard pairs are kept.
- **Code.** `src/nam_ab.py` (editor side: source, cache, render job, player), `src/nam_ab_render.py`
  (child process in the training Python, like the fitter), wiring in `tone_preview.py` (`listen:<id>:
  original|pedal`, `listenStop`) and `library_card.html` (`renderListen`). Tests:
  `tests/test_optimize_ab.py`. First listen about 10-15 s (torch start-up and the render), later ones
  instant.

Measured once on this PC (an amp capture, so a poor pedal fit, 20% held-out ESR): render 8.5 s in the
training Python, 13 s from the editor including finding that Python; Pedal version 1.4 dB quieter.

## Through the Floor (not built)

The Floor's own amp, cab and effects after the capture are what the player really hears, so the
better A/B runs the rest of the preset on the Floor. Level presets already has every piece needed:
`nam_level_measure.measure()` plays a WAV into USB 7/8 (the preset's input, switched in the edit
buffer only) and records the main output on USB 1/2 with the outputs on USB only, so nothing comes
out of the amp, and `level-restore.json` puts the routing back after a crash.

**Step 1, possible before the pedal block exists: the renders through the rest of the preset.**

1. On Listen through Floor, measure as Level does, but feed `original.wav` and then `pedal.wav` (the
   PC renders above, before level matching, `peak=None` so they go in as rendered) instead of a DI.
2. In the edit buffer only, bypass the NAM block (the renders already are its output) and, for
   exactness, everything before it on its path. Simplest first version: offer it only when the NAM
   block is the first block on its path (the usual place for a pedal) and say so otherwise.
3. Record both outputs, measure each one's loudness (BS.1770, as Level), and level-match on the PC.
   Play them back on the PC; optionally "Play through my rig": the same two renders played into the
   Floor live, each with the trim just measured, outputs left as they are, so the player hears both
   on their amp at the same loudness.
4. Put the bypass and routing back; never save the preset (owner rule). Nothing goes into a User IR
   slot in this step.

**Step 2, once NAM Pedal (block 768) is in the firmware and the Floor profile says `pedal: true`.**

1. Put the pedal version in a User IR slot: ask first, show which slot, never clear a slot without
   asking (owner rule).
2. Play the player's DI take into USB 7/8 twice: once with the NAM block on the original, once with
   the block switched to NAM Pedal on the version's slot (the per-block switch PRODUCT-VISION
   describes: block type and slot together, edit buffer only). Record, level-match, play back on the
   PC, then switch the block back.
3. The same recording doubles as the port's acceptance test: the Floor's NAM Pedal output against
   `nam_pedal.play()` on the PC for the same DI should differ by no more than float rounding. A
   larger difference is a kernel bug, not a fit problem.

Open before building either step: how the editor bypasses one block in the edit buffer (Level only
switches inputs and outputs today), and whether Floor A/B should live on the Level page (which owns
the audio device) or in the version panel calling into it.
