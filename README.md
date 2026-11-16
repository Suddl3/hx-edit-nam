# HX Edit Unbound

**Part of [HX Unbound](https://github.com/Suddl3/hx-unbound).** HX Edit Unbound runs Line 6's own HX Edit with HX Unbound's additions: a NAM capture library, TONE3000 browsing and previews, capture artwork, a DSP meter and Level presets. It pairs with [HX Unbound Floor firmware](https://github.com/Suddl3/hx-unbound-floor).

**`floor-navigation` is the current branch: newer than the `floor` source preview, and still a test build.** It adds the Browse TONE3000 banner button, the rich NAM tab, previews on User IR 128 that put the original capture back, and remembered slots. It has been exercised on one Helix Floor by one maintainer; see [what has and has not been tested](FLOOR-WORKFLOW.md#status-november-14).

### New on November 16

- **Level presets**, from the new **HX Unbound** menu button beside TONE3000: brings presets and snapshots to one output level. It measures each snapshot silently over USB with a short recording of your own playing (saved by guitar, reusable), suggests output-level changes toward one target, keeps intentional offsets (a lead +2 dB), then Applies with Undo. Only output levels change. [How it works](PRODUCT-VISION.md#level-presets-built-november-16).

### New on November 14

- **Capture icons**: a gear picture for every NAM capture, picked from its name, in the NAM tab, the expanded library and the NAM model lists.
- **Change icon…**: choose another picture, go back to Automatic, or upload your own (and delete your uploads).
- **DSP meter** in HX Edit's toolbar: each DSP's use as the Floor reports it ("DSP 1 27/75"), turning yellow and red as a path fills up.
- **Your window stays put** when the Floor reconnects (maximized stays maximized).
- A tidier NAM tab: readable names when narrow, short dates, and Expand always opens the library.
- **Guitar / bass / both** per capture, with filters in both library views and an All / Guitar / Bass dropdown in every NAM category and in HX Edit's Amp and Preamp, which remembers your last side.
- A **NAM Cab** category in the block picker; favorites stay HX Edit's own (they work with NAM blocks, and go when their capture's slot is cleared).
- **Grid views**: capture pictures in the block picker's grid, and a grid view for the expanded library.
- Slot numbers show as soon as the Floor connects.
- **A warning before clearing a slot** that presets you have opened still use.

Details and what was tested: [Status, November 14](FLOOR-WORKFLOW.md#status-november-14).

## Start here

- [Product vision and feature roadmap](PRODUCT-VISION.md): the shared product goals and current status.
- [Setup and launch](FLOOR-SETUP.md#the-navigation-branch): how to run this branch from source.
- [Updating an existing clone](FLOOR-SETUP.md#updating-an-existing-clone), including the new opt-in [NAM categories in the block selector](FLOOR-SETUP.md#the-nam-categories---nam-category).
- [Workflow contract and test status](FLOOR-WORKFLOW.md): what each action does, what has run on a Floor, and what is still to test.
- [Firmware/editor compatibility](https://github.com/Suddl3/hx-unbound-floor/blob/main/EDITOR.md): supported model and parameter contract.

Our fork controls the Floor workflow. Useful upstream improvements are reviewed and selectively integrated, with attribution retained and reusable fixes offered back. `origin` is our repository; `upstream` is andreidevs/hx-edit-nam. No automatic upstream merge is configured.

## Current milestone

One NAM collection across the NAM tab and its expanded view, full HX Edit controls, and consistent TONE3000 browsing, preview and save actions. Opt-in NAM categories in the block selector with a Model lock. Capture icons and a DSP meter (declared costs, as the Floor reports them). Next: storage, and measured DSP load once the firmware can report it.

Level presets measures and sets output levels on request; it is not an always-on leveler. Optional optimization and new runtime models require separately verified Floor compatibility. Read PRODUCT-VISION.md before broadening scope.

## Attribution

HX Edit Unbound began as a fork of [andreidevs/hx-edit-nam](https://github.com/andreidevs/hx-edit-nam) by Andrei Eronin, who did the original groundwork of bringing TONE3000 and NAM captures into HX Edit, and it uses the NAM/converter projects that work credits. His commits are kept in this repository's history. The project has since grown in its own direction: the HX Unbound Floor workflow, the NAM library, capture artwork, the DSP meter and Level presets. The prior README is preserved in [README_UPSTREAM.md](README_UPSTREAM.md). Stock HX Edit, firmware, personal captures and credentials are not distributed here.

Level presets can use [Neural Amp Modeler](https://github.com/sdatkinson/neural-amp-modeler)'s standard reamp file (its v3 input) as a test signal when you already have it on your PC; the file is not distributed here, only looked for.

The TONE3000 name and wordmark (`nav_images/tone3000-logo.png`, used on the Browse TONE3000 button) belong to TONE3000 and are used only to identify and open their service.
