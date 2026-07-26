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
| pillow-heif | 1.5.0 | **binary wheel: GPL-2.0** (bundles libheif/libde265 LGPL-3.0, x265 GPL-2.0, libaom BSD-3) — see below |
| exifread | 3.5.1 | BSD-3-Clause (per the project's PyPI page; not in installed metadata) |

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

### pillow-heif carries GPL-2.0 code

Earlier revisions of this file called `pillow-heif` Apache-2.0. **That was
wrong**, and the correction matters because the affected binaries already
ship. The wheel states its own terms in
`pillow_heif-1.5.0.dist-info/licenses/LICENSES_bundled.txt`, first line:

> License for "pillow-heif" binary wheels: GPLv2, due to base library licenses.

The bundled libraries are libheif (LGPL-3.0), libde265 (LGPL-3.0),
**x265 (GPL-2.0)** and libaom (BSD-3-Clause). The x265 shared library is
physically present — `pillow_heif/.dylibs/libx265.216.dylib`, and libheif
links it through `@loader_path` — so a built app contains it too (verified
in `build/RAW_selector.app/Contents/MacOS/`).

pillow-heif is what decodes `.HIF` and `.HEIC`, which is the only way to
open those files: OpenCV, Pillow and LibRaw all refuse them (measured on a
real Sony `.HIF`, `ftyp heix`). So the dependency is not optional today.

Two consequences worth stating plainly, without pretending to give legal
advice:

- GPL-2.0 has no linking exception, and its obligations attach to
  **distribution**, not to use. They therefore apply to releases already
  published, not only to future ones.
- HEVC **patent** licensing is a separate matter from software licensing —
  neither GPL-2.0 nor a commercial x265 licence covers it, and it applies
  to decoding as well as encoding.

Deciding what to do about this (add the required notices and source offer,
drop HEIF support, or seek different terms) is a project-owner call, not a
documentation change. This file's job is to state the facts accurately.

Versions and license fields above were read from installed package
metadata, except pillow-heif's, which comes from the wheel's own bundled
licence file — the metadata field (`BSD-3-Clause`) disagrees with it.
Re-check when bumping the package.

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
- **MIT License** (copyright Shiqi Yu). Verified 2026-07-25 against the
  upstream licence file:
  <https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE>
- MIT permits redistribution, so bundling this file in a release is fine as
  long as the copyright/permission notice is preserved (this section serves
  as that notice; the full MIT text is in the OpenCV Zoo repository).

### Face landmark model — `arw_selector/core/models/face_mesh_192x192.onnx`

- 2.3 MB, 468-point face mesh. Two layers of provenance, both permissive:
  - **Original model: MediaPipe Face Mesh (Google), Apache-2.0.**
    <https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE>
  - **ONNX conversion: PINTO_model_zoo `032_FaceMesh`
    (`20_new_onnx_postprocess_N-batch/face_mesh_192x192.onnx`), MIT**
    (copyright Katsuya Hyodo).
    <https://github.com/PINTO0309/PINTO_model_zoo/blob/main/LICENSE>
  - Verified 2026-07-25. Apache-2.0 and MIT both permit redistribution;
    keep this attribution with any release that ships the file.

Both licences (MIT, Apache-2.0) permit redistribution, so no removal is
required. Both models also fail soft anyway: if they are missing the app
still builds and runs, it just loses face detection and the eye/mask
features — and a missing model is silent, so `--selftest` checks for both
explicitly.
