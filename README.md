# HX Edit NAM — HX Unbound Floor companion

The maintained Floor-focused editor is [Suddl3/hx-edit-nam](https://github.com/Suddl3/hx-edit-nam/tree/floor), paired with [HX Unbound Floor firmware](https://github.com/Suddl3/hx-unbound-floor).

**This is the `floor-navigation` branch: newer than the `floor` source preview, and still a test build.** It adds the Browse TONE3000 banner button, the rich NAM tab, previews on User IR 128 that put the original capture back, and remembered slots. It has been exercised on one Helix Floor by one maintainer; see [what has and has not been tested](FLOOR-WORKFLOW.md#status-november-14).

### New on November 14

- **Capture icons**: a gear picture for every NAM capture, picked from its name, in the NAM tab, the expanded library and the NAM model lists.
- **Change icon…**: choose another picture, go back to Automatic, or upload your own (and delete your uploads).
- **DSP meter** in HX Edit's toolbar: each DSP's use as the Floor reports it ("DSP 1 27/75"), turning yellow and red as a path fills up.
- **Your window stays put** when the Floor reconnects (maximized stays maximized).
- A tidier NAM tab: readable names when narrow, short dates, and Expand always opens the library.

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

Volume leveling remains an isolated offline prototype; it is not live preset normalization. Optional optimization and new runtime models require separately verified Floor compatibility. Read PRODUCT-VISION.md before broadening scope.

## Attribution

This work builds on [andreidevs/hx-edit-nam](https://github.com/andreidevs/hx-edit-nam) and the NAM/converter projects it credits. The prior README is preserved in [README_UPSTREAM.md](README_UPSTREAM.md) as upstream reference, not the current Floor setup guide. This documentation change does not change licensing. Stock HX Edit, firmware, personal captures and credentials are not distributed here.

The TONE3000 name and wordmark (`nav_images/tone3000-logo.png`, used on the Browse TONE3000 button) belong to TONE3000 and are used only to identify and open their service.
