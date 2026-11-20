# What's new

## November 19

- **Drag and drop onto the chain**: captures from the NAM tab and the library window, and (with NAM categories) any model from the block picker. Dropping on a block replaces it, an empty cell adds one, between blocks inserts.
- **HX Edit's IR list**: the middle of a block now replaces it; NAM slots become NAM blocks and show in violet.
- **Block picker** (NAM categories): a click selects, double-click or Enter applies, the arrows only move the selection, and the model the block plays is shown in yellow.
- A NAM block is only made where the DSP has room.

These change how parts of HX Edit behave: see [how it behaves differently from stock HX Edit](../README.md#how-it-behaves-differently-from-stock-hx-edit).

## November 16

- **Level presets**, from the new **HX Unbound** menu button beside TONE3000: brings presets and snapshots to one output level. It measures each snapshot silently over USB with a short recording of your own playing (saved by guitar, reusable), suggests output-level changes toward one target, keeps intentional offsets (a lead +2 dB), then Applies with Undo. Only output levels change. [How it works](PRODUCT-VISION.md#level-presets-built-november-16).
- **HX Edit Unbound**: the project is now its own repository. While it runs, HX Edit's window is titled "HX Edit Unbound" and shows a violet-to-teal version of HX Edit's icon, so it is easy to tell apart from a stock HX Edit.
- A tidier repository: the app's code is in `src/`, the documents in `docs/`.

## November 14

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
