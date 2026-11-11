# Experimental Helix Floor library

This Windows-only extension combines the tested Floor library workflow with the
current upstream launcher. It needs the pinned Windows HX Edit 3.82 installation,
compatible Floor NAM A2 Lite firmware, and the existing neighboring `helix-lt-nam`
converter checkout. It does not install or change firmware.

Start from the repository with:

```powershell
python .\start-hx-edit-nam.pyw --floor-library
```

The Windows packaged launcher also accepts `--floor-library`. Other device and macOS
modes retain their existing launch paths. The extension's library device actions
require a connected Helix Floor. Close other editors before starting.

## Library and storage

- **IRs** shows the physical shared storage. NAM captures still occupy User IR slots;
  they are decoded by NAM blocks, not ordinary IR blocks.
- **NAM** shows captures kept on this PC, with friendly names, type, IR slots, and date.
  Search, sortable columns, type filters, and classification corrections are local.
  Corrections change labels, not the capture's audio or architecture.
- **Load on Floor** uses the lowest empty permanent slot in **1–127**. **Use on Floor**
  selects an already verified copy. An amber slot is pending verification, not proof
  that its content matched. Multiple verified slots mean multiple installed copies.
- **Slot 128** is reserved for TONE3000 auditions. An occupied slot is not overwritten
  automatically. Auditioning does not keep the capture in the main local library.
  **Add to Library** retains it on the PC; **Save to Helix** installs a permanent copy
  and keeps the local capture. Saving the preset is a separate action.
- Closing an audition restores the original model when the target and slot identity
  can still be verified. Input/EQ/level changes on an existing NAM block are retained.
  Changed targets or saved presets can prevent cleanup; the editor reports that the
  audition remains instead of guessing which slot to clear.

New imports and explicit keeps get a local date. Recovered file timestamps are
estimates, with provenance in the tooltip; a date is not invented from a filename.
The catalog and retained files live under
`%LOCALAPPDATA%\HX Edit NAM\NAM Library`. Back up that folder to retain the library
on another PC. No personal catalog, captures, or account keys are supplied here.
TONE3000 continues using upstream's publishable-key/sign-in flow.

## Verification and Clear protection

Installed-slot numbers come from fresh exported content, not IR names or old slot
assignments. Discovery runs through HX Edit's native export queue, including delayed
completion handling. It refreshes after reconnecting or an inventory change without
opening a second direct USB session. During a connected refresh, the previous display
can remain visible, but stale mappings cannot authorize capture selection. Disconnect
invalidates the session evidence. Batch results publish together.

Clear checks references across the **current preset**, including its snapshot values.
It blocks used slots and refuses deletion when that preset cannot be inspected
reliably. It does **not** inspect every saved preset: a slot unused by the current
preset may still be used elsewhere. Protected/unexportable IRs and unknown payloads
are not labeled as verified captures. A local-library entry is not proof of a device
installation, and this library is not a lossless NAM preset-backup solution.

## Integration and testing

The extension composes temporary resource/module copies; it does not alter the
installed editor. Native hooks are gated by the executable hash and static entry-point
checks. The sortable table is a local Windows helper with a private pipe; native
actions are dispatched on the editor's UI thread. The default launcher does not load
these hooks. Windows packaging includes the extension's source and reuses the existing
WebView2 SDK when compiling its temporary helper.

The reconciled source keeps upstream's disposable import staging, asynchronous Model
and undo completion fences, newer device modes, and Windows/macOS launch paths.
The earlier TONE3000 reopening fix is included as a source overlay (PR #3).

Run the portable suite with Python and Node.js available:

```powershell
python -B -m unittest discover -s tests
```

The combined suite contains 81 tests: 76 pass without proprietary fixtures and five
upstream fixture-dependent tests skip. Tests cover content identity, reconnect
invalidation, atomic publication, Clear references, delayed completion, preview
restoration, and durable imports. The local resource composition, pinned native
contracts, Python/JavaScript syntax, both C# helper builds, Windows launcher build,
and hidden NAM table self-tests were also checked against an installed HX Edit copy.

Owner testing on Helix Floor covered loading/selecting captures, preview cleanup,
used-versus-unused Clear, amber feedback, row selection, all four tabs, and live
sidebar resizing. Audio and physical controls remained responsive in those tests.
Those hardware results are from the development build before this upstream
reconciliation; the reconciled source has been validated offline, without another
device session. LT, Stomp, Effects, and macOS library behavior is not claimed tested.

An intermittent audition failure after library selection recovered after reopening
TONE3000 during development. Keep that limitation visible when testing broader flows.
No claims about firmware stability or DSP capacity are made by this editor change.
