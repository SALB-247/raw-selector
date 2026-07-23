# Third-party components

The project's own source code is MIT licensed (see `LICENSE`). Everything
listed here belongs to someone else and keeps its own terms.

Versions and license fields below were read from the installed package
metadata on 2026-07-22, not from memory. Re-check them when you bump a
dependency.

## Runtime dependencies

| Component | Version | License |
|---|---|---|
| PySide6 | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| opencv-python | 5.0.0.93 | Apache-2.0 |
| rawpy | 0.27.0 | MIT |
| numpy | 2.4.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| PyYAML | 6.0.3 | MIT |
| piexif | 1.1.3 | MIT |
| Pillow | 12.2.0 | MIT-CMU |
| lensfunpy | 1.18.0 | MIT |
| pillow-heif | 1.5.0 | Apache-2.0 (bundles libheif 1.23.1, LGPL-3.0) |
| exifread | 3.5.1 | not declared in package metadata — verify before relying on it |

### PySide6 deserves attention

PySide6 is offered under **LGPL-3.0** (or a commercial licence). The binary
distribution built by `build_windows.py` ships Qt shared libraries, which
brings LGPL obligations with it — most importantly, recipients must be able
to replace the Qt libraries with their own build.

The build keeps Qt as separate `.dll` files next to the executable rather
than folding them into one file, which is what makes that replacement
possible. **Do not switch the build to `--onefile` without re-reading the
LGPL terms.** (`--onefile` is already discouraged in `BUILD.md` for
unrelated reasons: slow start-up and problems with worker processes.)

`rawpy` wraps **LibRaw**, which is dual-licensed (LGPL-2.1 / CDDL-1.0).
Same reasoning applies to the shared library shipped alongside.

`pillow-heif` is Apache-2.0, but the **libheif** it carries is LGPL-3.0 —
the same obligation as Qt. It is what decodes `.HIF` and `.HEIC`, which is
the only way to open those files: OpenCV, Pillow and LibRaw all refuse
them (measured on a real Sony `.HIF`, `ftyp heix`).

Versions and license fields above were read from installed package
metadata; libheif's license comes from its upstream project, not from the
Python wrapper's metadata, so re-check it when bumping `pillow-heif`.

## Bundled data

### Lens profiles — `data/lensfun/`

- Source: https://github.com/lensfun/lensfun (`data/db`)
- Licence: Creative Commons Attribution-ShareAlike 3.0
- Full text: `data/lensfun/COPYING.CC_BY-SA_3.0`
- Modified: no, shipped as-is

The app converts version-2 database files into a version-1 copy under
`.v1cache/` at runtime because the bundled lensfun library only reads
version 1. That cache is generated, not redistributed.

### Face detection model — `arw_selector/core/models/face_detection_yunet_2023mar.onnx`

- 227 KB, YuNet, from the OpenCV Zoo model collection
- **Licence not yet documented here.** Confirm the terms in the upstream
  repository and record them before publishing a release that includes
  this file.

### Face landmark model — `arw_selector/core/models/face_mesh_192x192.onnx`

- 2.3 MB, 468-point face mesh, converted from MediaPipe
- **Licence not yet documented here.** Confirm the terms upstream and
  record them here.

Both models fail soft: if they are missing the app still builds and runs,
it just loses face detection and the eye/mask features. That makes it easy
to remove them from a distribution if the licence turns out to require it —
but it also means a missing model is silent, so `--selftest` checks for
both explicitly.
