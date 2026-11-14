# Floor editor workflow reconciliation

Owner decision: November 12, 2026. Maintain **Suddl3/hx-edit-nam** as the editor
companion to **Suddl3/hx-unbound-floor**. Review and selectively integrate useful
upstream work; contribute separable fixes upstream later. Nothing in this
decision authorizes publishing the current changes or changing firmware.

Branch: `floor-navigation`, based on upstream
`97d2783d28f4aeb95a326cf815127570cf58e1c7` (v0.6.0). `origin` is
`Suddl3/hx-edit-nam`; `upstream` is `andreidevs/hx-edit-nam`. The older public
preview is the `floor` branch; this branch is the newer navigation work. See
[FLOOR-SETUP.md](FLOOR-SETUP.md) to run it and [PRODUCT-VISION.md](PRODUCT-VISION.md)
for the complete product scope. This document is the workflow contract.

## Status, November 13

This source is the owner's Candidate63 test build. On one Helix Floor, each of
these has run at least once: preview and a second preview on the same block;
Close / End preview putting the original capture back; Use on Floor from the NAM
tab; Save to PC + Floor from the tone card; a preview replacing the content of
User IR 128; recovery of a preview left on a block when the editor was closed;
and a launch that reads no slots back because they were remembered.

The offline suite is 249 tests. It is not a substitute for the checks below.

### NAM categories in the block selector (opt-in, `--nam-category`)

An experiment added after Candidate63, off unless the launcher is given
`--nam-category`. With it:

- the block selector gains four categories after Looper (NAM Amp, NAM Amp + cab,
  NAM Pedal, NAM Other), under a thin line; Amp > NAM is removed;
- each lists the NAM A2 Lite block and the captures of that type that are on the
  Floor; picking a capture sets the block to it;
- the block in the signal path shows the icon of its capture's type;
- the open list follows the selected block: it shows the block's own category with
  its capture highlighted, and refills when the startup slot check finishes, a block
  is added or selected, or a capture's type is changed in the NAM tab;
- **Model lock:** a padlock left of the Model value. A NAM block from a preset, or
  one whose capture was picked from a NAM category, is locked: the Model control
  ignores clicks, drags and the mouse wheel. Click the padlock to unlock (and again
  to lock). A plain NAM A2 Lite block picked from its own row starts unlocked and
  has no padlock. The lock belongs to the editor, per block of the loaded preset; it
  is never saved in the preset and resets when another preset is loaded. Picks from
  a NAM category, the NAM tab and previews still change a locked block.

Seen on one Floor: all of the above, on both DSPs, across snapshot changes and a
block drag. Known flaws: just after launch HX Edit's own marker in the category
list can stay on NAM Other; a capture that is not in the PC library shows as NAM
Other; while locked, the Model control's right-click menu is unavailable too; an
Undo of a plain NAM A2 Lite pick keeps that block without a padlock until the
preset is loaded again; an older WAV import and the original .nam of the same
capture are not recognised as one (saving the .nam to the Floor adds a second
slot). The icons are placeholders drawn for this project.

### Previews: tested on the Floor

Each of these ended the preview with the block back on its own capture (or on the
capture it was moved to) and User IR 128 deleted: Close, Escape, selecting another
block, a snapshot change, loading another preset, Undo, changing an unlocked Model
during the preview, switching to another profile of the tone, and closing HX Edit
during a preview (the next launch restores the block). Save to PC + Floor wrote the
capture to the lowest free slot and the NAM category list showed it at once.

### Still to test on the Floor

- Load on Floor from the NAM tab for a capture that is not on the Floor yet:
  only that row should go amber, then green.

### Known gaps

- Notices can open behind HX Edit.
- Remembered slots are trusted by slot name, and one Floor is assumed.
- The NAM categories are an opt-in experiment (above). No storage or DSP counters yet.

This is one owner's testing on one unit, not a release.

## Owner decisions, November 13 (supersede the points they touch below)

Made by the owner (Suddl3) after the Candidate59–61 test sessions. Built into Candidate62.

- **User IR 128 is the preview slot and nothing else.** A new preview always
  replaces whatever is in it. This supersedes "unknown retained 128 must not be
  overwritten or cleared automatically" in item 4.
- **A preview left by a closed editor is recovered.** The editor records the
  block and the capture it played before; on the next launch it puts that capture
  back and releases 128. A block on 128 with no record can still take Use on
  Floor / Load on Floor.
- **No slot scan on every launch.** Each slot read is remembered with the slot's
  name (`floor-memory.json`); only new, renamed or changed slots are read again.
  This supersedes "invalidate session proof … never load disk mappings" in item
  6 for slots whose name is unchanged. Slots the Floor reports as changed are
  still read back.
- **Card wording:** Save to PC / Save to PC + Floor, always the lowest free slot,
  no slot picker. The NAM tab keeps Load on Floor / Use on Floor.
- Adding or removing a block, or loading another preset, does not discard slot
  proof.

## Product contract

The accepted Candidate53 workflow is the feature-preservation baseline. Later
upstream capabilities are additions; adopting them must not silently remove the
tested sidebar, preview cleanup, slot protection or import behavior.

1. **One NAM collection.** The NAM sidebar immediately lists kept PC captures
   with sortable Name, Type, IR slot and Date, search, category filtering and
   classification correction. Its contents must not wait for a USB scan.
   An optional expanded library uses the same records, selection, filters,
   verification state and actions. It is a larger view, not a second library.
   Remove the redundant All captures route once that shared sidebar is restored.
2. **Keep the familiar editor.** Retain Presets → IRs → NAM → Favorites, working
   live sidebar resizing and the original full parameter-list area. IRs remains
   the physical shared slot list. NAM is the capture-oriented library.
3. **TONE3000 is discovery.** Keep Browse TONE3000 in the top banner, with embedded
   default and optional Pop out/Dock. Automatically target the selected compatible
   NAM block. Browsing without a target is allowed; auditioning requires one.
   Keep navigation controls in a consistent location across catalog/library.
   Native block headers must not cover browser controls.
4. **Preview is temporary.** Slot128 is reserved for preview. Escape, Close and
   End preview must restore the original capture on the original block, retain
   current Input/EQ settings, and release only the preview we own. Changing block
   selection automatically ends the old preview before targeting another block.
   Neither docking nor changing library/catalog views should end or retarget it.
   Unknown retained128 must not be overwritten or cleared automatically.
5. **Make destinations explicit.** Present actions consistently as Preview,
   Keep on PC, Load on Floor / Use on Floor, and End preview. Installing a capture
   uses the lowest empty permanent slot1–127, keeps a local copy when available,
   and selects it on the target block. Using an existing verified copy avoids a
   duplicate upload. Capture installation does not Save the preset.
6. **Verify without obscuring browsing.** Opening NAM may request installed-slot
   verification, but kept local rows stay visible. Keep existing displayed slot
   results stable while a refresh runs, and commit refreshed results together.
   Only the changed row gets pending amber. Block selection, opening IRs,
   Pop out/Dock and preview128 mutations must not restart a full scan. Pause
   verification during audition/import. Recheck genuinely changed permanent
   slots; invalidate session proof after reconnect without hiding local captures.
   Unknown notifications stay conservative until their semantics are verified.
7. **Preserve protections and provenance.** Keep content-based slot matching,
   used-slot Clear protection, friendly names, recovered-date provenance and
   classification corrections. Never equate an unavailable mapping with a
   missing local capture. Metadata and captures must survive every integration.

## What the current buttons actually do

| Current label | Current implementation | Reconciled presentation |
| --- | --- | --- |
| All captures / Library | Opens the same PC library HTML page | NAM / Expand library |
| Add to library | Keeps the preview capture on the PC; no permanent device install | Keep on PC |
| Save to Helix | Copies preview to a chosen permanent slot, selects it, keeps a PC copy and attempts preview cleanup; no preset Save | Load on Floor |
| Load on Floor | Installs local capture into lowest empty1–127 and selects it | Load on Floor |
| Use on Floor | Selects an already verified installed copy | Use on Floor |
| Audition | Temporarily loads a capture into128 | Preview |

Candidate58 uses the reconciled labels. The old labels in this table document
their mapping, not separate destinations. Keep permanent installation and preset
Save distinct in both behavior and confirmation text.

## Reconciliation findings from Candidate57

- Candidate53's `NamLibrary.cs` provided the rich native sidebar. It was not
  included/launched by the current integration.
- Upstream commit `d7968d3` reimplemented features of our earlier PR#4 in its
  existing views, including the full-height HTML library. Current integration
  kept that choice and added IR-slot and Load/Use behavior there. The metadata
  remains present; the sidebar omission is our integration parity regression.
- Candidate57 trace records two26-slot scans. Ordinary
  `EditBuffer_HelixCursorChange` notifications invalidated all slot proof and
  completed-slot tracking between them. Validate this notification's native
  contract and separate selection state from storage state.
- The first scan intentionally hid installed rows until atomic commit; it then
  rendered18 rows while the NAM pane was hidden. Opening the browser afterward
  was not the cause of that successful render. An always-present local collection
  avoids the misleading blank first scan.
- The docked helper can lose native sibling z-order on block selection while its
  unchanged bounds suppress layout work. Fix layering in response to relevant
  native layout/selection events without foreground/focus work on a timer.
- Escape depended on helper/WebView focus. One close passed local
  restoration checks and deleted128; a later close failed during shutdown.
  Neither proves the owner's visible Model restoration. Treat the owner-reported
  Escape regression as requiring physical retest. Log the initiating action, cleanup rejection reason,
  original slot and observed slot separately from rendered caption state.

## Implementation and acceptance order

1. Repair storage invalidation, cleanup lifecycle and focus routing offline.
   Replay the whole reported workflow, including ordinary block selection,
   native-focus Escape, browser-focus Escape, docking with unchanged bounds,
   concurrent scan, library switching and application shutdown.
2. Restore the rich sidebar using the shared current library model. Retain the
   useful expanded upstream view, but unify its actions/navigation and remove
   duplicate destinations. Verify feature parity against Candidate53 explicitly.
3. Package one consolidated candidate only after those checks pass. One owner
   batch then checks library visibility, repeated previews, Input preservation,
   Escape, Pop out/Dock, permanent installation and used/unused Clear behavior.
   Stop if physical controls or audio fail. Unit counts alone are not acceptance.

Candidate57 is withdrawn from further owner trials. Candidate53 remains the last
accepted launcher; no automatic rollback or live launch is part of this plan.
The Floor remains untouched while these repairs are developed.

## Upstream watch and integration policy

- Keep the upstream remote and pinned baseline so new commits can be compared.
  Check releases/commits before each integration batch and when the owner asks.
  No scheduled watcher or automatic merge is configured by this document.
- Record each reviewed upstream change as adopt, adapt, defer or incompatible,
  including its commit, editor behavior and any required firmware capability.
- Do not enable LT-specific, reduced-layer or other engine features merely
  because the editor exposes them. Require a compatible tested Floor baseline.
- Run the workflow/parity checks above before replacing the accepted launcher.
- Offer focused fixes upstream after local verification and owner publication
  approval. Maintaining this fork does not require abandoning upstream cooperation.
