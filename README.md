# HX Edit NAM — HX Unbound Floor companion

This is the **Floor-focused editor fork maintained by Suddl3 and savedadodo (Red Dragon)**, paired with [HX Unbound Floor](https://github.com/Suddl3/hx-unbound-floor).

The goal is a consistent NAM library and TONE3000 workflow for the Helix Floor. We retain the upstream project's credit and licence, review useful upstream changes selectively, and offer reusable fixes back. We do not automatically replace the Floor workflow when upstream changes its UI.

## Install the current source preview

**Start with [FLOOR-SETUP.md](FLOOR-SETUP.md).** Use the `floor` branch; it contains the published Floor library source based on `f72e7af` (the Candidate 53 tab-order fix), plus these setup documents.

- Windows, stock HX Edit **3.82**, and compatible Floor NAM A2 Lite firmware based on Helix Floor **3.80.0**.
- NAM block contract: **HD2_AmpNAMLite, preset ID 380**, with the original eight-parameter layout.
- Run `python .\start-hx-edit-nam.pyw --floor-library` using the setup guide's virtual environment.
- This is a **source preview**, not a newly packaged installer or a promise that every firmware/editor combination has been hardware-tested.
- The newer private navigation Candidates 54–57 are **not this preview** and are not recommended installation targets. They remain under repair.

The tested development workflow includes a rich NAM sidebar, type/date/IR-slot columns, search and sorting, lowest-empty permanent-slot loading, slot 128 previews, content verification and current-preset Clear protection. The reconciled public source was validated offline; the guide distinguishes that from device testing.

## Projects and update policy

| Project | Responsibility |
| --- | --- |
| [HX Unbound Floor](https://github.com/Suddl3/hx-unbound-floor) | Firmware source, Floor features, build options and hardware findings |
| This repository, `floor` branch | Floor editor workflow, library, desktop integration and compatibility documentation |
| [andreidevs/hx-edit-nam](https://github.com/andreidevs/hx-edit-nam) | Editor upstream; source of improvements reviewed before integration |

Keep firmware and editor releases separate, with explicit compatible block/parameter contracts. An editor update does not flash firmware. Optional firmware features are not enabled by installing this editor.

Before updating, compare upstream commits, record what is adopted/adapted/deferred, and repeat the Floor workflow checks. No automatic upstream merge or scheduled watcher is configured.

## Credit and licence

This fork builds on **andreidevs/hx-edit-nam**, and uses the NAM conversion work credited by that project. Existing source history and attribution are retained. See the [upstream documentation](README_UPSTREAM.md) and [Floor library implementation notes](floor_library/README.md); this documentation update does not change licensing.

Line 6 HX Edit, firmware, personal presets, captures and credentials are not distributed here. Each user supplies their own installation and captures.
