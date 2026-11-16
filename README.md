# HX Edit Unbound

**Part of [HX Unbound](https://github.com/Suddl3/hx-unbound).** HX Edit Unbound is Line 6's own HX Edit with HX Unbound's additions for the Helix Floor: a NAM capture library with gear artwork, TONE3000 browsing and previews, a DSP meter and Level presets. It pairs with [HX Unbound Floor firmware](https://github.com/Suddl3/hx-unbound-floor).

![HX Edit Unbound with the NAM tab, the TONE3000 and HX Unbound buttons and the DSP meter](docs/screenshots/hx-edit-unbound.png)

> **A test build.** It runs on Windows with HX Edit 3.82 and has been exercised on one Helix Floor by one maintainer. See [what has and has not been tested](docs/FLOOR-WORKFLOW.md#status-november-14).

## What it adds

### Your NAM captures, with pictures

Every NAM capture gets a gear picture, picked from its name: in the NAM tab, the expanded library (list or grid) and HX Edit's model lists. Sort captures into amps, amp + cab, pedals and cabs, mark them guitar, bass or both, and load one onto the Floor or use it in the selected block.

![The expanded NAM library in grid view](docs/screenshots/nam-library.png)

Don't like a picture? **Change icon…** picks another from the built-in set, or upload your own.

<p align="center"><img src="docs/screenshots/icon-picker.png" alt="Choosing a capture's picture" width="420"></p>

### Level presets

Brings presets and snapshots to one output level, silently. Level plays two seconds of your own recorded playing through each snapshot over USB (nothing comes out of your amp), measures the loudness, and suggests a change to each output block's Level. Keep a lead louder on purpose with an offset, then Apply, with Undo. Recordings are saved per guitar ("Strat · neck"), so you record once. Only output levels change; tone and drive are never touched.

![Level presets after scanning two presets](docs/screenshots/level-presets.png)

### In HX Edit's toolbar

**Browse TONE3000** opens TONE3000 inside HX Edit, to preview captures on the Floor and save the ones you like. The **HX Unbound** menu opens Level presets and the NAM library, and the **DSP meter** shows how full each DSP is, as the Floor reports it.

![The TONE3000 button, the HX Unbound menu and the DSP meter](docs/screenshots/toolbar.png)

### Easy to tell apart

While it runs, HX Edit's window is titled **HX Edit Unbound** and its icon turns violet to teal (left: stock HX Edit).

<p align="center"><img src="docs/screenshots/app-icon.png" alt="Stock HX Edit icon beside the HX Edit Unbound icon" width="420"></p>

Also: NAM categories in the block picker (opt-in), a warning before clearing a slot that presets still use, and HX Edit's window staying put when the Floor reconnects. The full list is in [what's new](docs/CHANGES.md).

## Get started

[Setup and launch](docs/FLOOR-SETUP.md#the-navigation-branch) installs it from source on Windows: official HX Edit 3.82, Python and Git, then one command to launch. Already have a copy? See [updating an existing clone](docs/FLOOR-SETUP.md#updating-an-existing-clone).

Your captures, recordings and settings stay in `%LOCALAPPDATA%\HX Edit NAM`. Nothing here changes the Floor's firmware; for that, see [HX Unbound Floor](https://github.com/Suddl3/hx-unbound-floor).

## Documents

- [What's new](docs/CHANGES.md)
- [Setup and launch](docs/FLOOR-SETUP.md)
- [Workflow and test status](docs/FLOOR-WORKFLOW.md): what each action does, what has run on a Floor, and what is still to test.
- [Product vision and roadmap](docs/PRODUCT-VISION.md), including [how Level presets works](docs/PRODUCT-VISION.md#level-presets-built-november-16).
- [Firmware/editor compatibility](https://github.com/Suddl3/hx-unbound-floor/blob/main/EDITOR.md)

The repository: `src/` holds the app, `tests/` its tests, `docs/` these documents. `floor-navigation` is the current branch; `floor` is the older source preview.

## Attribution

HX Edit Unbound began as a fork of [andreidevs/hx-edit-nam](https://github.com/andreidevs/hx-edit-nam) by Andrei Eronin, who did the original groundwork of bringing TONE3000 and NAM captures into HX Edit, and it uses the NAM/converter projects that work credits. His commits are kept in this repository's history. The project has since grown in its own direction: the HX Unbound Floor workflow, the NAM library, capture artwork, the DSP meter and Level presets. The prior README is preserved in [README_UPSTREAM.md](docs/README_UPSTREAM.md). Stock HX Edit, firmware, personal captures and credentials are not distributed here.

Level presets can use [Neural Amp Modeler](https://github.com/sdatkinson/neural-amp-modeler)'s standard reamp file (its v3 input) as a test signal when you already have it on your PC; the file is not distributed here, only looked for.

The TONE3000 name and wordmark (`src/nav_images/tone3000-logo.png`, used on the Browse TONE3000 button) belong to TONE3000 and are used only to identify and open their service. HX Edit, Helix and Line 6 are trademarks of Yamaha Guitar Group; this project is not affiliated with them.
