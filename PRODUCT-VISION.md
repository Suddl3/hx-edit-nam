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
| [This editor fork, `floor` branch](https://github.com/Suddl3/hx-edit-nam/tree/floor) | NAM library, desktop editing, TONE3000 workflow and compatible editor models |
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
| Leveling assistant | Separate offline prototype: its verification report records 88 backend tests and 79 interface checks. No live native Apply, device acquisition or real preset saving |
| Optimization and alternate engines | Planned, gated on explicit Floor support and quality comparison; not silently enabled by upstream editor availability |

Use the [Floor pairing guide](https://github.com/Suddl3/hx-unbound-floor/blob/main/EDITOR.md)
and [editor setup guide](https://github.com/Suddl3/hx-edit-nam/blob/floor/FLOOR-SETUP.md)
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
| F01, F03 | Small visible Floor feature, declared-budget display, later measured per-DSP load | Approved direction, not completed UI. Label declared admission budget separately from measured CPU. Diagnostic output must not replace ordinary audio accidentally |
| F02, F04, R01 | Kernel efficiency, optional reduced-layer/pedal engines, full/stereo/capacity research | Research and measured integration. No promised simultaneous count, automatic feature enablement or unsupported firmware/editor combination |
| F05–F09 | On-device NAM categories/browsing, optional modern screen/boot appearance, custom names, metronome, selected HXtra controls | Later Floor work. Adapt to Floor's switches, scribble strips, screen and snapshots; preserve stable preset references and readable labels |
| W01 | Leveling assistant | Offline prototype described below; live audio/native editing remain separate gates |
| W02 | Preset-aware library maintenance | Later dependency index before moving/removing assets used by saved presets |
| W03 | Clear setup, manual launch, update and release management | Pair versions/flags/hashes and recovery instructions; retain known-working launcher. Build firmware from each owner's stock file; no proprietary firmware distribution |
| W04 | Phone/Pi editor access using ToneRelay research | Deferred until desktop/device ownership is reliable |
| Visual backlog | Smooth signal-path zoom, imagery, dark appearance and customization | Retained user goals; not permission to squeeze controls or replace familiar native navigation during repair |

### Leveling assistant: Measure → Review → Apply

The intended workflow uses the **same DI and capture profile** across the selected
preset/snapshots, chooses a measured reference baseline, reviews output trims and
headroom, then explicitly applies approved changes. Preset and snapshot offsets are
additive: +1 dB preset plus +2 dB solo snapshot targets +3 dB relative to baseline.
Keep intentional differences rather than flattening every sound to one level.

Current implementation lives in a separate `feat/hx-edit-leveling-offline` worktree
under `leveling/`, with its own scope/readme/verification record. Its movable helper,
planner, synthetic Apply/Undo, saved-native inspection and read-only capture checklist
are offline. The recorded 88 backend and 79 interface checks validate that scope,
not real guitar loudness or live native edits.

The retained real sample has no proven existing snapshot gain assignment or matched
audio, so native corrections and Apply remain unavailable. Do not fabricate values,
create assignments or infer native gain units from that sample. Native acquisition,
UI-thread editing/readback, actual audio capture, preset Save/recall and a navigation
entrypoint still need integration. Apply/Undo makes prior measurements stale; require
remeasurement rather than inventing a new result. Preserve controller ownership,
unknown preset data, NAM settings and unrelated snapshots, and reject stale plans.

## Next work and acceptance

1. Keep product scope, pairing and launch instructions coherent across the firmware
   and editor repositories.
2. Finish the Floor checks listed under "Still to test on the Floor" in
   FLOOR-WORKFLOW.md: snapshot change and preset change during a preview, and Load on
   Floor from the NAM tab for a capture that is not on the Floor yet.
3. A second maintainer runs the `floor-navigation` branch from source (FLOOR-SETUP.md)
   and reports what differs on their unit.
4. Add a NAM category to the block selector (Amp, Amp + cab, Pedal captures), first as
   a feasibility spike in the editor, coordinated with the firmware's own block menu.
5. Continue the small Floor UI/capability work with Red Dragon and integrate leveling
   only after its native/audio contracts are proven. Identify firmware, editor and
   library changes independently so failures can be isolated.

Offline test counts, USB connection and a responsive host process are not substitutes
for visible restoration and physical panel/audio checks. Earlier mockups are concepts;
historical acceptance packets that require manual binding or an installed-only sidebar
are superseded by this brief and the current [workflow contract](FLOOR-WORKFLOW.md).

The shared cross-project planning discussion is [Floor issue #2](https://github.com/Suddl3/hx-unbound-floor/issues/2).
Implementation incidents, private source artifacts and exact test receipts stay in
their existing research packets; this document is the durable product overview.
