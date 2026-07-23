# RAW_selector

A desktop tool for **culling RAW photos by focus** and developing the keepers.
Built for batches of ~4,000 frames, running the same code on Windows and macOS.

Shot on a Sony A6700 (ARW), but the same pipeline works for other bodies —
anything LibRaw 0.22 opens (**CR3 / CR2 / NEF / RAF / ORF / RW2 / DNG**, ~20
formats). It also judges and develops **JPEG and HEIF** directly, for people who
don't shoot RAW.

Select, develop (tone/colour/masks/watermark), and export all work from one
window. See [CHANGELOG.md](CHANGELOG.md) for what changed per release and what to
do when upgrading.

> A much more detailed guide, with measured numbers throughout, is in Korean at
> [README.ko.md](README.ko.md).

## Why it's fast

Full-demosaicing 4,000 frames with rawpy takes 1–2 seconds each — over 1.5
hours. Instead it pulls the **full-size embedded JPEG preview** (6192×4128 on the
A6700). That resolution is plenty for a focus decision, and it finishes in ~30ms
per frame.

Measured (32 cores, 31 workers):

| Frames | Time | |
|---|---|---|
| 2,845 | 87s | cold (full analysis) |
| 2,845 | 0.3s | warm (cache hit) |
| 4,000 (scaled) | ~2 min | |

## Install

```bash
pip install -e ".[gui,dev]"
```

Needs `rawpy`, `opencv-python`, `PySide6`, `exifread`, `PyYAML`, `piexif`,
`pillow`, and `pillow-heif` (the only decoder for `.HIF`/`.HEIC`). Two ONNX
models ship in the repo (`arw_selector/core/models/`):

| Model | Size | Used for |
|---|---|---|
| `face_detection_yunet_2023mar.onnx` | 227KB | Face boxes + 5 points (analysis) |
| `face_mesh_192x192.onnx` | 2.3MB | 468-point mesh — face/eye masks, eye-closed |

Both **fail soft**: if a model is missing the app still runs, it just loses face
detection or the mask/eye-closed features. That failure is silent, so if the
face features seem to be missing, check the startup log.

## Usage

### GUI

```bash
raw-selector
```

Open folder → analyse → review the grid → export.

- Grades show as thumbnail border colour (green keep / amber review / red reject).
- The **grade filter buttons** at the top show each grade's count and share;
  click to see only that grade.
- **Criteria** opens the judging panel. Changing a value **re-grades instantly,
  without re-analysing** (re-analysis is minutes; re-grading is 0.3s, so you can
  feel out the settings with the sliders live).
- **1 / 2 / 3** set the selected frame's grade by hand; **0** returns it to auto.
- **Space** or double-click opens the loupe — check the ROI that was judged,
  develop, change grade, and move between frames all in there.
- **D** develops (selected frames into the loupe), **Q** adds to the queue.
- **Esc** cancels analysis or export.

### CLI

```bash
raw-select D:/shoot                      # analyse, summary only
raw-select D:/shoot --report out.csv     # per-frame detail (.csv or .json)
raw-select D:/shoot --export             # copy into _keep/_review/_reject
raw-select D:/shoot --export --move      # move instead of copy
raw-select D:/shoot --export --grades keep,review
raw-select D:/shoot --export --dry-run   # show what goes where
raw-select D:/shoot --undo               # undo the last export
raw-select --dump-config > config.yaml   # config template
```

Other flags: `--config`, `--no-cache`, `--workers`, `--keep-per-group`,
`--target-keep PCT`, `--keep-above SCORE`, `--recursive` / `--no-recursive`,
`-q/--quiet`, `-v/--verbose`.

The CLI has no develop/mask/watermark — those are GUI-only. The CLI does the
selection and file sorting.

## How it judges

### Focus

1. Extract the embedded preview and apply EXIF orientation.
2. Detect faces with YuNet on a downscaled copy (long edge 1024px).
3. If a face is found, back-project an **ROI around both eyes** to full
   resolution and crop it. Otherwise the sharpest grid tile is taken as the
   subject.
4. Measure Laplacian variance and Tenengrad on the ROI, **each divided by the
   patch variance**.

That last normalisation matters. Laplacian variance scales with the square of
contrast, so without it every low-light / low-contrast scene scores low
regardless of focus — the biggest source of misjudgement.

It has an opposite trap too: an empty dark background (variance 0.9) has a raw
gradient at noise level, but dividing by variance can push it **above** the real
subject. So a **signal gate** zeroes anything below std-dev 5 (sensor noise, no
basis for a focus call), and **tile selection uses the raw gradient** —
normalisation is for comparing *between* images, not tiles *within* one.

`focus.ALGORITHM_VERSION` is part of the cache key, so changing how sharpness is
measured invalidates old scores automatically.

### Grouping

**Capture time is the main signal.** Measured on 2,845 frames, within-burst
intervals had a median of 0.16s while real scene changes were tens to hundreds of
seconds — time separates them cleanly where visual similarity does not.

### Eye-closed

Closed eyes are still in focus, so sharpness never catches them. The main
subject's 468-point mesh gives an **eye aspect ratio (EAR)**; below
`eyes_closed_below` (default 0.20) it subtracts `penalty_eyes_closed` (default
7.5). The **more open** of the two eyes is used (a profile's far eye always reads
closed). When the eyes can't be measured, nothing is subtracted — *unmeasured* is
not *closed*. The penalty is small on purpose: the goal is to push a frame out of
auto-keep into review, not down into reject.

### Grade

```
analysis failed                     -> reject
score >= keep threshold             -> keep
rank within group < keep_per_group  -> keep     (at least 1 per scene)
score < max(reject_below, batch p15)-> reject
>= 10 below the group best          -> reject    (a better near-duplicate)
otherwise                           -> review
```

The default keep threshold is a **target ratio** (`target_keep_ratio`, default
10%), back-solved from the batch's score distribution so the keep share stays
stable when lighting or lens shifts the scores. The most expensive error is a
**false reject**, so judging leans toward reject, and **the group's best frame is
never rejected** under any threshold combination.

## Develop

Double-click (or Space / D) opens the loupe, where you develop, change grade, and
move between frames without breaking flow across hundreds of shots.

Supported adjustments: basic tone (temperature, exposure, highlights, shadows,
whites/blacks, texture, clarity, dehaze, vibrance, saturation), parametric +
drag-edit curves (RGB/R/G/B), detail (sharpening, noise reduction with a
**face-priority** weighting, LED-wall destripe), local masks (brush / radial /
linear / face / eye / background, 11 presets), HSL colour mixer, colour grading,
effects (grain, vignette), optics (lensfun auto + manual distortion/vignetting/
defringe), crop & straighten, an info strip, watermark, and selective EXIF.

The preview and the export run the **same `engine.apply_settings`** — only the
resolution differs. **Full Render** re-develops at screen resolution when you
stop adjusting, to check sharpening/noise/masks at real quality (one at a time —
a 27MP demosaic takes ~2.8GB, and two at once crashes an 8GB machine).

For JPEG/HIF sources, camera profile / colour calibration / lens correction are
locked off (the camera already baked them in); white balance stays adjustable.

## Export

**Export** opens an options dialog — the same selection needs different files
depending on purpose (full-size print, 2048px social, low-res proof). You choose
grades, whether to copy the original RAW, folder splitting (by grade / by GPS
place), move vs copy, whether to render developed images, format (JPEG/PNG/WebP/
TIFF), quality, size, and filename pattern.

Every export writes a JSON log and is fully reversible with `--undo` (it only
deletes files this tool created). GPS location is **never written** into exported
files, and place-grouping is pure coordinate maths with no outside network call.

## Presets

Both judging and develop settings save as presets, in `data/` next to the
executable (repo root when run from source), falling back to the user folder
(`%APPDATA%\raw_selector\` / `~/Library/Application Support/raw_selector/`) only
where that's read-only. They're plain YAML you can edit or hand to someone else.

## Cache

Each shoot folder gets a `.raw_selector_cache/` with the analysis SQLite,
512px grid thumbnails, and undo logs. Two version keys
(`cache.SCHEMA_VERSION`, `focus.ALGORITHM_VERSION`) invalidate it automatically
when the stored fields or the measurement change, so upgrading re-analyses a
folder once on first open.

## Structure

```
arw_selector/
  core/            No UI. Does not import Qt.
    raw_io.py      Preview extraction, EXIF, orientation
    focus.py       Face/eye detection + sharpness + eye-closed (EAR)
    grouping.py    Scene grouping
    scoring.py     Score aggregation, grading
    cache.py       SQLite result cache
    pipeline.py    Parallel batch execution
    export.py      Folder sorting + undo
    develop/       Render pipeline, masks, optics, watermark
    models/        ONNX models (face detection · face mesh)
  gui/             PySide6 (loupe, develop panel, criteria panel, grid)
  cli.py
```

CLI and GUI both go through `core.session.SelectionSession`; different results
for the same folder would be a bug. `core` never imports Qt — analysis runs in
`ProcessPoolExecutor` workers, and pulling Qt into every worker would cost
startup for nothing. User-facing text lives only in the GUI layer, with English
source strings and Korean shipped as a Qt translation.

## Cross-platform

Paths are all `pathlib.Path`; worker functions are module top-level with
picklable args (macOS uses spawn, not fork); extension comparisons are always
`.lower()`. Developed and verified on Windows 11 / Python 3.12; validated on
Apple Silicon macOS 14.

## Licence

The project's own code is MIT (see [LICENSE](LICENSE)). Bundled data and the
libraries used by packaged builds keep their own terms — see
[THIRD_PARTY.md](THIRD_PARTY.md). PySide6 and libheif in particular are LGPL-3.0.
