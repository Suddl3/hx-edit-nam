# HX Unbound Floor: firmware and editor product vision

Updated November 13, 2026, incorporating November 12's decisions. This is the current product brief for Suddl3 and Red Dragon
(`savedadodo`). It records desired behavior and its status; it does not declare
unfinished features shipped or authorize device writes, firmware flashes or publication.
The immediate repair contract is [FLOOR-WORKFLOW.md](FLOOR-WORKFLOW.md).

## The product

Find a capture by name and type, hear it on the intended Floor block, keep or cancel
it predictably, and know what is installed and what fits. Preserve HX Edit's familiar
signal path and full vertical parameter list while improving browsing and appearance.
Compatible A2 Lite captures should work directly; retraining is optional, not a tax
on every audition.

| Project | Responsibility |
| --- | --- |
| [HX Unbound Floor](https://github.com/Suddl3/hx-unbound-floor) | Floor firmware, device UI, DSP behavior, build options and hardware findings |
| [HX Edit Unbound](https://github.com/Suddl3/hx-edit-unbound) (this editor) | NAM library, desktop editing, TONE3000 workflow and compatible editor models |
| [HX Unbound hub](https://github.com/Suddl3/hx-unbound) | Community/project directory; earlier independent editor/librarian/API work remains a distinct track |

Maintain these two product repositories together, preserve attribution, selectively
adopt useful work from editor upstream, the Floor port, NAM upstream and HXtra, and
offer reusable fixes back. An upstream update does not automatically replace our
workflow. No scheduled watcher or automatic upstream merge is currently configured.

## Current state

| Area | Status and evidence boundary |
| --- | --- |
| Accepted desktop reference | Candidate 53 lineage: owner-tested sidebar, tabs/resizing, preview, library and protection workflows |
| Public editor channel | `floor` source preview: code `f72e7af` plus setup docs `c4757ca`; offline validated, not a one-click installer or proof of every firmware/editor pairing |
| New navigation integration | Candidate63, published as the `floor-navigation` branch. On one Floor: preview and second preview, Close/End restore, Use on Floor from the NAM tab, Save to PC + Floor from the card, preview replacing User IR 128, recovery after the editor closed mid-preview, and a launch that reads no slots have each run at least once. Not accepted as the everyday build yet; still to test: snapshot or preset change during a preview, and Load on Floor from the NAM tab for a new capture. See FLOOR-WORKFLOW.md |
| Firmware integration | Red Dragon's PRs #1, #3 and #4 merged. His reported hardware evidence is separate from subsequent offline repairs. Default DSP cost remains 3600; screen mirroring remains off unless explicitly built in |
| DSP display and broader Floor UI | Planned. Screen-mirror tooling aids research but is not the proposed on-device DSP display |
| Level presets | Built (HX Unbound menu): silent USB measuring with the player's own recording, review, Apply with Undo and saving; tested on one Floor |
| Optimization and alternate engines | Planned, gated on explicit Floor support and quality comparison; not silently enabled by upstream editor availability |

Use the [Floor pairing guide](https://github.com/Suddl3/hx-unbound-floor/blob/main/EDITOR.md)
and [editor setup guide](https://github.com/Suddl3/hx-edit-unbound/blob/floor/FLOOR-SETUP.md)
for versions. The current normal contract is stock Windows HX Edit 3.82, Floor 3.80
base, `HD2_AmpNAMLite` / preset ID 380 and the original eight parameters. ID 555,
reduced-layer models and specialized NAM Pedal engines need separate paired support.
A pedal **capture category** does not imply the specialized pedal **runtime**.

## Approved desktop workflow

### Familiar editing and one library

- Keep the full signal path, available empty blocks, normal selectors and broad
  vertical controls. Edits should remain immediate and synchronized with the Floor,
  including physical changes, preset recalls and snapshots where supported.
- Keep sidebar order **Presets → IRs → NAM → Favorites**, live width dragging and
  double-click reset. No white drag flash, debug pixel counter or all-row selection.
- Preserve the original visual direction: recognizable stock-category/model icons,
  a polished dark theme, and smooth wheel/trackpad signal-path zoom rather than a
  zoom dropdown. These are design goals; the broader visual refresh is not complete.
- NAM immediately shows kept computer captures with sortable **Name, Type, IR slot,
  Date**, search, category filters and classification correction. Local rows must
  not disappear while waiting for USB verification.
- An expanded library is another view of the same collection, selection, filters,
  installed proof and actions. Avoid duplicate catalogs and ambiguous Library /
  All captures destinations.
- Keep amp-only, amp+cab and pedal captures distinguishable from ordinary cabinet
  or other IRs. Retain Unsorted when classification is uncertain. Category edits
  change metadata, not audio, engine identity or stored preset structure.
- Preserve friendly names, original files/hashes, source links and date evidence.
  Recover original download dates when evidence exists; cloned/restored file dates
  are not proof of an exact original date. Keep provenance in details without a
  wide repetitive label. Improve IR/Model captions as well as library names;
  changing device labels must not destroy capture identity.

### TONE3000 browsing and targeting

- Put **Browse TONE3000** in the main banner near snapshot/undo controls, with a
  recognizable colorful treatment. It is a browsing action, not an amp model.
- Open embedded by default, with optional **Pop out / Dock** preserving the same
  browser session. Explicit opening should reveal the window, not leave it behind
  HX Edit. Keep toolbars/actions consistent and native block headers out of the way.
- Automatically recognize the selected compatible NAM block on opening. Browsing
  can work without a target; auditioning requires a known compatible selected block.
  Do not require a redundant Use selected block click in the normal opening flow.
- Changing block selection automatically ends/restores the old preview before any
  new target is used. Delayed downloads or callbacks must never edit whichever
  block happens to become selected. Docking and view changes do not retarget.
- Normal browsing never creates/replaces a NAM block automatically. Any future
  capture-driven block creation must be a clear, explicit action with compatibility
  checks and preservation of existing effects and routing.
- Avoid polling that steals focus or stalls mouse/Alt-Tab. Use the native editor's
  device session; a separate scanner must not silently compete for USB ownership.

### Preview, keep, install and save mean different things

| Action | Product behavior |
| --- | --- |
| Preview | Temporarily play the chosen compatible capture through the selected block using reserved User IR 128 |
| Keep on PC | Retain the capture in the local NAM collection; no permanent device install |
| Load on Floor | Install into the **lowest empty slot 1–127**, retain a local copy when available, and select it on the intended block |
| Use on Floor | Select an existing content-verified installed copy without uploading a duplicate |
| End preview / Escape / Close / X | Restore the original capture on its original block, retain current Input/EQ adjustments, and release only the preview owned by this session |
| Preset Save | Separately persist the preset; capture installation must not masquerade as preset Save |

Repeated previews reuse 128 and retain current amp settings. Previewed captures do
not accumulate in the kept library unless requested. Unknown retained content in
128 is never silently overwritten or deleted. Cleanup uncertainty must be reported
honestly rather than claiming the original sound returned when it did not.

### Installed state, storage and protection

- Physical storage remains **one shared 128-slot pool**, not fixed NAM/IR partitions.
  Reserve 128 for preview in our workflow. Show counts for NAM, true IR, unidentified
  and empty storage, distinguishing the preview reservation from permanent capacity.
- Show verified physical slot number(s). Multiple slots may legitimately hold the
  same capture; verify content rather than guessing from filenames. Do not equate
  unknown installation state with a missing local capture or proven uninstalled state.
- Opening NAM may request verification. Keep displayed results and rows steady,
  refresh in the background and commit results together. Only the changing row gets
  an amber pending slot, visible even when selected, followed by verified state.
- Recheck genuinely changed slots. Block selection, opening IRs, Pop out/Dock and
  preview-128 changes must not restart a full scan. Pause export verification during
  audition/import. After reconnect, invalidate permission to use stale mappings and
  refresh without hiding the local collection.
- Block Clear for slots referenced by the current preset and relevant snapshots;
  allow proven unrelated slots. A future index of all saved presets is separate
  work, not a claim made by current-preset protection.
- Preserve NAM blocks, parameters, routing, snapshots, metadata and captures through
  supported operations. Stock HX Edit backup/restore is not assumed NAM-lossless.
  Flash workflows must preserve/reapply desired Global Settings explicitly.

## Roadmap and boundaries

Broader HX Edit parity remains a product goal: preset/setlist browsing, save/recall,
export/import and backups; snapshots and their names/colors/behavior; block creation,
replacement, move/copy/paste, bypass and parameters; routing/splits/merges/I/O;
controller and footswitch assignments; favorites; IR management. Inheriting the
native editor provides a base, not blanket verification of every operation with
custom NAM blocks. Track preservation and hardware evidence per supported workflow.

| IDs | Direction | Status / completion boundary |
| --- | --- | --- |
| B01, N01–N07 | Reproducible pair; native-feeling browsing/categories; named compatible selection; slot proof; preview; explicit keep/install/save; preservation | Current milestone. Repair accepted behavior before adding another navigation layer; require broad owner acceptance after offline tests |
| N08–N09 | One-action optional Optimize and original/optimized A/B | Planned. Progress/cancel, cache by source+mode+converter, original retained, supported Floor variants only. Level-matched comparison; ESR is not a perceptual accuracy percentage. No mandatory retraining or claim it is lossless |
| N10–N12 | Migration, favorites/list or grid choice, guitar/bass filters, friendly names everywhere | Preserve current metadata first; broaden selectors only when evidence supports categories. Sorting library views does not move device slots |
| F01, F03 | Small visible Floor feature, declared-budget display, later measured per-DSP load | Declared budget: shown in HX Edit's toolbar since November 14 ("DSP 1 27/75", the Floor's own figures, labeled as declared cost, not measured load); on the Floor itself by the firmware's DSP meter build. Measured per-DSP load still to come. Diagnostic output must not replace ordinary audio accidentally |
| F02, F04, R01 | Kernel efficiency, optional reduced-layer/pedal engines, full/stereo/capacity research | Research and measured integration. No promised simultaneous count, automatic feature enablement or unsupported firmware/editor combination |
| F05–F09 | On-device NAM categories/browsing, optional modern screen/boot appearance, custom names, metronome, selected HXtra controls | Later Floor work. Adapt to Floor's switches, scribble strips, screen and snapshots; preserve stable preset references and readable labels |
| W01 | Level presets | Built and tested on one Floor (described below); wider preset and device coverage still to test |
| W02 | Preset-aware library maintenance | Later dependency index before moving/removing assets used by saved presets |
| W03 | Clear setup, manual launch, update and release management | Pair versions/flags/hashes and recovery instructions; retain known-working launcher. Build firmware from each owner's stock file; no proprietary firmware distribution |
| W04 | Phone/Pi editor access using ToneRelay research | Deferred until desktop/device ownership is reliable |
| Visual backlog | Smooth signal-path zoom, imagery, dark appearance and customization | Retained user goals; not permission to squeeze controls or replace familiar native navigation during repair |
| Favorites: still to test | Replacing a favorite from a block on another slot (its icon may lag a moment); the "used by presets" Clear warning naming the favorites it removes (unit-tested only); the 16-character limit of the suggested favorite name; grid view selecting the capture tile in a NAM category (it highlights NAM A2 Lite). Not done: favorites of the HX NAM pedal model keep HX Edit's own icon (only NAM A2 Lite has a record per NAM category) | Logged on the Floor test of Nov 15; none blocks use |
| Polish list | Library confirmations (Remove, Replace) are browser dialogs titled "hx-edit-nam.invalid says": make them in-page; hide empty NAM categories (NAM Cab) so the block picker fits one screen; a settings toggle between HX Edit's own model pictures and the 3D set (`helix_model_icons`, a build option today); the 3D stock-gear pictures as a group in the capture icon picker | Small, owner-requested refinements; none changes device behaviour |

### Optimize: versions of one capture (approved Nov 15)

Savings on the Floor come from a lighter block, not from a lighter file: the NAM A2 Lite block
runs all 23 layers whatever the capture holds. So Optimize ships together with Floor firmware for
a lighter engine (NAM Pedal first: about 8% of a DSP against about 36%, fitted in about 30 s, good
for boosts, overdrives and preamp pedals; layer skipping for amps later). Figures so far are from a
Helix LT; the Floor is measured before any number is shown.

- **One row per capture, with versions.** The library card shows chips such as `Original · Light ·
  Pedal`. **Optimize** adds a version (chosen from the gear type: pedals get Pedal, amps get Light
  once it exists); the original is never replaced. A version takes a Floor slot only when used.
- **Quality shown honestly.** A level-matched A/B through the Floor before using it; a badge
  ("sounds the same" / "may sound different", from the fit's error and band deviations). Not
  offered where it fails (amps and cabs for Pedal).
- **Switch per block.** Picking another version for the selected NAM block changes the block type
  and slot together (a Pedal file in a NAM A2 Lite block would be silent): instant, this preset
  only, undoable.
- **Switch everywhere, on request.** "Use Original everywhere" lists the presets using the capture
  (preset usage is already tracked) and changes them after a confirmation, since it edits saved
  presets.
- **Built so far (Nov 15, experiment builds only, HX_NAV_OPTIMIZE=1):** versions stored on the
  original's row, the Optimize button (explains refusals), a real NAM Pedal fit in about 40 s with
  its quality badge, Remove version. Waiting on the NAM Pedal firmware block: using a version,
  A/B on the Floor, per-block switching. Not yet in the NAM tab's narrow list (the expanded library only).
- **A/B on the PC meanwhile (Nov 16, same flag).** In the version panel, **Listen: Original | Pedal**
  plays the player's saved guitar (Level presets' recording; else NAM's v3 input file) through the
  original and through the pedal version as the NAM Pedal kernel computes it, level-matched at -18
  LUFS, on the PC's default output (never the Floor), beside the quality badge and the level gap
  between the two. Renders are kept per guitar and version, so a second listen is instant. A/B
  through the Floor's own amp and cab is planned in `docs/OPTIMIZE-AB.md`, not built.
- **On the Floor itself** a version cannot be switched live (a snapshot cannot change a block's
  model); the editor is where versions change. To be tried in live use and adjusted.

### Level presets (built, November 16)

What a player feels on stage is the final output of each preset and snapshot, so that is what is
levelled: relative output, measured at the Floor's real output (stock blocks and captures alike).
The player then sets one overall volume on their amp, PA or master and it stays consistent. Open it
from the **HX Unbound** menu button beside TONE3000: **Level presets…**.

- **Silent measuring.** Each chosen preset is loaded (MIDI Program Change) and each snapshot recalled;
  its input is switched to USB 7/8 and its outputs to USB 1/2 in the edit buffer only, a two-second
  test signal is played in and the output recorded back over USB (WDM-KS, 48 kHz). Nothing comes out
  of the amp, and the routing is put back afterwards (also after a crash, the next time Level opens).
- **Loudness, BS.1770.** Each snapshot is read two to four times (more when the sound moves on its
  own: modulation, delays) and the middle reading counts; steady sounds repeat within 0.1-0.3 dB.
  Snapshots that measure alike show as one row. Peak dBFS is offered too; LUFS is the default.
- **The test signal is the player's own playing.** "Record my guitar" (a 3-2-1 countdown, then 10
  seconds of normal playing) keeps two typical seconds of it on this PC, played back as recorded, so
  amps and drives see a real guitar's level. Recordings are saved by name ("Strat · neck") to reuse or
  delete later. Neural Amp Modeler's standard reamp file (v3 input) is used instead when it is already
  on the PC (its guitar part, at the last recorded guitar's level); nothing is bundled.
- **Review, then Apply.** The target is the middle of the scan, as loud as a chosen preset, or a typed
  level. Each snapshot shows its change to the output block's Level, held back (never clipped) past
  12 dB or when its peak would pass -1 dBFS. Offsets keep a sound louder or quieter on purpose (a lead
  +2 dB); they are remembered per preset and snapshot.
- **Only output levels change.** Tone, drive and NAM settings are never touched. Output levels held by
  a Snapshots controller keep their per-snapshot values. Apply to the loaded preset is left unsaved;
  Apply to several presets saves each one. Undo puts the old levels back (also after a restart), and
  each preset card notes when it was last leveled, with which guitar and to what.
- **Captures are not re-levelled.** The converter already applies NAM's own "Normalized" output.
- **Not built:** an always-on automatic leveler (it fights playing dynamics and solo boosts), and
  applying without loading each preset.
- **Tested** on one Helix Floor (presets 005, 030, 031): scans, multi-preset Apply and Undo, rescans
  on target after Apply, recordings, the NAM file. Not yet: other setlists in depth, presets whose
  output is on an expression pedal (refused, by design), a Helix LT or Stomp.

## Next work and acceptance

1. Keep product scope, pairing and launch instructions coherent across the firmware
   and editor repositories.
2. Finish the Floor checks listed under "Still to test on the Floor" in
   FLOOR-WORKFLOW.md: snapshot change and preset change during a preview, and Load on
   Floor from the NAM tab for a capture that is not on the Floor yet.
3. A second maintainer runs the `main` branch from source (FLOOR-SETUP.md)
   and reports what differs on their unit.
4. Add a NAM category to the block selector (Amp, Amp + cab, Pedal captures), first as
   a feasibility spike in the editor, coordinated with the firmware's own block menu.
5. Continue the small Floor UI/capability work with Red Dragon. Level presets is
   integrated; test it on more presets and setlists before widening it. Identify firmware, editor and
   library changes independently so failures can be isolated.

Offline test counts, USB connection and a responsive host process are not substitutes
for visible restoration and physical panel/audio checks. Earlier mockups are concepts;
historical acceptance packets that require manual binding or an installed-only sidebar
are superseded by this brief and the current [workflow contract](FLOOR-WORKFLOW.md).

The shared cross-project planning discussion is [Floor issue #2](https://github.com/Suddl3/hx-unbound-floor/issues/2).
Implementation incidents, private source artifacts and exact test receipts stay in
their existing research packets; this document is the durable product overview.
