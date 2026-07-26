# RAW Selector — User Manual

A desktop tool for culling RAW photos by focus and developing the keepers.
This manual walks through the whole workflow in order: open a folder, analyse
it, review and cull in the grid, develop the keepers, and export.

## Contents

- [Getting started](#getting-started)
  - [Launching](#launching)
  - [Supported files](#supported-files)
  - [The main window at a glance](#the-main-window-at-a-glance)
  - [The status bar](#the-status-bar)
  - [Preferences](#preferences)
- [Opening photos](#opening-photos)
- [Analysing photos](#analysing-photos)
  - [The Start analysis dialog](#the-start-analysis-dialog)
  - [Progress and cancelling](#progress-and-cancelling)
  - [Camera colour calibration](#camera-colour-calibration)
  - [The analysis cache](#the-analysis-cache)
- [Reviewing and culling](#reviewing-and-culling)
  - [Filtering and sorting](#filtering-and-sorting)
  - [The thumbnail grid](#the-thumbnail-grid)
  - [Grading from the keyboard](#grading-from-the-keyboard)
  - [The score card](#the-score-card)
- [Developing](#developing)
  - [Opening the Develop window](#opening-the-develop-window)
  - [Navigating between photos](#navigating-between-photos)
  - [The viewer](#the-viewer)
  - [Histogram and clipping warnings](#histogram-and-clipping-warnings)
  - [Grading and actions](#grading-and-actions)
  - [The develop panel](#the-develop-panel)
    - [Basic](#basic)
    - [Curve](#curve)
    - [Detail](#detail)
    - [Local adjustments (masks)](#local-adjustments-masks)
    - [Color mixer](#color-mixer)
    - [Color grading](#color-grading)
    - [Effects](#effects)
    - [Optics](#optics)
    - [Crop and straighten](#crop-and-straighten)
    - [Capture info strip](#capture-info-strip)
    - [Watermark](#watermark)
    - [EXIF metadata](#exif-metadata)
  - [JPEG and HEIF sources](#jpeg-and-heif-sources)
- [The export queue and exporting](#the-export-queue-and-exporting)
  - [Adding photos to the queue](#adding-photos-to-the-queue)
  - [The queue panel](#the-queue-panel)
  - [Export options](#export-options)
  - [While an export runs](#while-an-export-runs)
  - [Undoing an export](#undoing-an-export)
- [Presets and criteria](#presets-and-criteria)
  - [Grading criteria — the Criteria panel](#grading-criteria--the-criteria-panel)
    - [Keep criteria](#keep-criteria)
    - [Reject criteria](#reject-criteria)
    - [Score weights](#score-weights)
    - [Scene splitting](#scene-splitting)
  - [Grading presets](#grading-presets)
  - [Develop presets](#develop-presets)
- [Command-line interface](#command-line-interface)
- [Appendix: keyboard shortcuts](#appendix-keyboard-shortcuts)

## Getting started

### Launching

Install the package, then run `raw-selector` for the GUI or `raw-select` for
the command-line tool. Both run the same analysis, so a folder graded in one
looks the same in the other.

### Supported files

The app opens every RAW format LibRaw decodes — ARW, CR3, CR2, NEF, RAF, ORF,
RW2, DNG and more — and also judges and develops JPEG and HEIF files directly.
See SUPPORTED.md in the repository for the full format and camera list.

### The main window at a glance

![Main window](screenshots/main-window.png)

From top to bottom: the toolbar (open, analyse, develop, queue, export, cache,
preferences), the filter-and-sort row, the thumbnail grid, the score card for
the selected photo, and the status bar.

### The status bar

The bar along the bottom shows the current status message, a progress bar, and
a time-left estimate ("about … left") for whatever task is running. The
**Stop** button (or `Esc`) cancels the running task.

### Preferences

The **Preferences** toolbar button opens the "Preferences" dialog with two
tabs:

- **General** — the **Interface language** combo (**System default**, English,
  한국어) takes effect on the next start; a restart notice is shown. The
  **Check for updates** checkbox (off by default) enables update checks, and
  **Check now** runs one immediately with a status readout.
- **About** — application name and version, the licence texts, and a
  licensing note.

If the app ever hits an unhandled error, an "Error" dialog appears and points
to a written error report.

## Opening photos

- **Open folder** opens the "Choose a RAW folder" directory picker and loads
  everything in the folder. The last-used folder is remembered.
- **Open files** opens the "Choose RAW files" picker to load one file or a
  handful instead of a whole folder; analysis starts immediately after
  picking.
- The **Include subfolders** checkbox (on by default) scans the folder
  recursively.

## Analysing photos

### The Start analysis dialog

Click **Analyse** to open the "Start analysis" dialog before anything runs.

![Start analysis dialog](screenshots/analyze-dialog.png)

The dialog shows the photo count and what the cache can reuse ("Cache: {n}
photos can be reused", or "Cache: none — everything will be analysed fresh"),
plus a live estimate of how many photos will be analysed and how long it will
take.

- **Use cached results** — reuse previous results where possible; uncheck it
  to re-analyse everything from scratch.
- **Precision** group:
  - **Noise-robust sharpness** — a sharpness measurement that resists sensor
    noise.
  - **Use camera AF point when no face is found** — falls back to the
    camera's recorded AF point as the focus region when no face is detected
    (Sony, Canon CR3, Nikon).

**Start analysis** begins the run; **Cancel** closes the dialog.

### Progress and cancelling

Progress appears in the status bar ("Analysing {done}/{total} (cached …,
failed …)") with a time estimate. **Stop** (`Esc`) cancels after the photo in
progress finishes; partial results are kept and labelled "Cancelled — results
so far". When analysis completes, a summary line reports the totals ("{total}
photos · {scenes} scenes · keep/review/reject counts").

### Camera colour calibration

When a photo comes from a camera model the app has no colour profile for, a
"New camera model" dialog offers to compute one (**Compute** / **Later**); a
"Computing color calibration" progress dialog with **Cancel** follows. You can
also run it manually at any time with the **Colour calibration** toolbar
button ("Compute color calibration on this PC"); the computed result overrides
the built-in library defaults.

### The analysis cache

The **Cache** toolbar button (its label shows the current size, or "No cache")
opens the "Clear cache" dialog. It lists the analysis entries, thumbnails,
total size, and the estimated time to rebuild; confirm with **Yes** to clear.
Undo records for past exports are kept.

## Reviewing and culling

### Filtering and sorting

![Filter buttons and sort combo above the grid](screenshots/grid-filters.png)

The filter buttons above the grid — **All**, **keep**, **review**,
**reject**, and **Scenes with no keep** — show live counts and narrow the
grid to one grade with a click. The **Sort** combo orders the grid **By
filename**, **Highest score first**, or **Lowest score first** (score sorting
ignores scene grouping).

### The thumbnail grid

Every thumbnail carries a grade band (KEEP / REVIEW / REJECT text and colour)
and a score badge; a ✋ marker flags shots you graded by hand. The tooltip
shows the score, grade, lens, ISO, shutter, aperture, and the grading reasons.

- The **Size** slider in the toolbar scales thumbnails from 90 to 360 px; the
  grid auto-fits its columns with no leftover gap.
- The **Double-click** mode radio buttons in the toolbar choose what a
  double-click opens: **Preview** (the embedded JPEG, instant) or **Develop**
  (demosaiced RAW with accurate colour; the default).
- Selection is extended multi-select; double-click or `Space` opens the
  selected photo in the loupe.

### Grading from the keyboard

With photos selected in the grid: `1` grades keep, `2` review, `3` reject, and
`0` clears the manual grade so the automatic grade applies again. `D` opens
the Develop window for the selection and `Q` adds the selection to the export
queue.

### The score card

Selecting a photo shows the score card along the bottom.

![Score card](screenshots/score-card.png)

The left side is a point-by-point breakdown — rows such as Sharpness, Focus on
the face, Focus missed the face, No face, Face detected, Face size, Eyes
detected, Eyes open, Eyes closed, Eyes not measured, Blown highlights, Crushed
shadows, Lens cap / stray shutter, Clamped to range, and the **Total** — each
with an evidence note explaining the entry. The right side is a fact sheet for
the shot: Captured, Camera, Lens, Focal length (with the 35mm equivalent on
crop bodies), Exposure, AF area, and Location (display only).

To change *how* photos are graded, open the Criteria panel — see
[Grading criteria](#grading-criteria--the-criteria-panel).

## Developing

### Opening the Develop window

Double-click a thumbnail, press `Space`, or use the **Develop** toolbar button
(`D`) to open the loupe, titled "Develop — {name}". A multi-selection opens as
a browsable list, so the same edit can be applied to all of them. The window
is modeless — several can be open at once.

### Navigating between photos

**◀ Previous** (`←`) and **Next ▶** (`→`) move through the list, with a
position readout ("{n} / {total}").

### The viewer

![Loupe](screenshots/loupe.png)

Zoom with the mouse wheel, pan by dragging, and double-click to reset the
view; the current zoom percentage is shown. The header line shows the
filename, Score, ROI sharpness, Frame sharpness, lens/ISO/shutter/aperture,
and the grading reasons; if RAW demosaic fails, a warning notes the viewer is
"showing the embedded JPEG".

- **Original** (`B`) — before/after toggle.
- **Focus** (`F`) — a green box marking the region used for grading.
- **Faces** (`A`) — grey boxes around detected faces with the main subject in
  red; click a face to make it the main subject and re-grade the photo.
- **Eyes** (`E`) — eye contours.
- **AF point** (`P`) — an orange box where the camera focused (Sony, Canon
  CR3, Nikon).
- **Zoom to focus** (`Z`) — fills the screen with the grading region.
- **Full Render** — re-develops at full resolution whenever you stop
  adjusting; when zoomed in, only the visible region is rendered more finely.

The **Original** toggle compared — the untouched source on the left, the
current develop on the right:

![Before and after with the Original toggle](screenshots/before-after.png)

### Histogram and clipping warnings

The histogram sits above the develop panel. Click its centre to cycle the
channel display (RGB / luminance / both); the corner triangles are clip
warnings. The **▼ Shadows** and **▲ Highlights** toggle buttons blink blue and
red overlays on clipped pixels, with a percentage readout ("crushed …% ·
blown …%", or "No clipped pixels to show").

![Histogram with the clipping toggles](screenshots/loupe-clipping.png)

### Grading and actions

The footer holds the grade buttons **keep (1)**, **review (2)**, **reject
(3)** (keys `1` `2` `3`), plus:

- **Apply develop to all** — applies this window's develop settings to every
  shot in the list (crop and straighten are excluded).
- **Add to queue (Q)** (`Q`) — queues this shot with its current develop.
- **Export** — exports this one shot right now (destination picker, then the
  "Export options" dialog).
- **Close**.

### The develop panel

![Develop](screenshots/develop.png)

The develop panel sits on the right (hidden in Preview mode; resizable via the
splitter). At the top is the develop-preset bar — see
[Develop presets](#develop-presets). A vertical strip of section icons jumps
to each section; every section header is collapsible, has an eye button
(**Toggle this section's edits on and off (values kept)**) and a ● marker when
modified. Every slider row has its own reset button, and double-clicking a
slider resets it. **Reset all** at the bottom of the panel clears every edit.

#### Basic

Global tone and colour: **Temperature** (absolute Kelvin, 2000–12000 K),
**Tint**, **Exposure** (EV), **Brightness**, **Contrast**, **Highlights**,
**Shadows**, **Whites**, **Blacks**, **Texture**, **Clarity**, **Dehaze**,
**Vibrance**, and **Saturation**.

#### Curve

![Curve section](screenshots/develop-curve.png)

A point-curve editor with a **Clipping** toggle and channel buttons **RGB** /
**R** / **G** / **B**, plus ↺ **Reset this channel's curve**. Click to add a
point, drag to move it, right-click or double-click to delete it, and
double-click an empty area to reset. Below it are the parametric sliders
**Highlights**, **Lights**, **Darks**, and **Shadows**.

#### Detail

Sharpening and noise reduction: **Sharpening**, **Radius**, the **Noise
method** combo (**Standard (non-local means)**, **High quality (non-local
means, slow)**, **Fast (bilateral filter)**, **Legacy (reproduces old
versions)**), **Noise reduction**, **Passes** (1–4, NL-means only), **Detail
preservation**, **Color noise reduction**, **Color noise radius**, **Shadow
color noise**, **Destripe** (removes LED-lighting banding), and **Face
priority** (weights noise reduction toward faces).

#### Local adjustments (masks)

![Masks section with a stack of masks](screenshots/develop-masks.png)

**＋ Add mask** opens a menu of mask types:

- Portrait: **Under-eye retouch**, **Smooth skin**, **Sharpen irises**,
  **Whiten teeth**, **Brighten face**
- Background: **Emphasize subject (darken background)**, **Blur background
  (bokeh)**
- Light & sky: **Bluer sky**, **Spotlight (darken surroundings)**, **Brighten
  area (radial)**, **Darken area (radial)**
- Manual: **Brush (paint by hand)**

Masks stack in a list with per-mask enable checkboxes, a **Show region**
toggle (red overlay), and **Delete**. Brush masks add **Paint**, **Eraser**,
**Clear all**, and **Brush size** (%) controls. Face and eye masks have an
**Apply to** combo — **Main subject**, **All faces**, or **By number** with a
number spin — plus a face-count readout. Every mask has **Range** (%),
**Strength** (%), **Feather** (%), and **Invert region**, and its own
adjustment sliders: **Exposure**, **Contrast**, **Highlights**, **Shadows**,
**Temperature**, **Saturation**, **Texture**, **Clarity**, **Skin
smoothing**, and **Sharpening**. Radial and linear masks show drag handles on
the image — drag the centre to move, an edge point to resize, an outer point
to rotate.

#### Color mixer

![Color mixer section](screenshots/develop-hsl.png)

Per-colour adjustment over eight bands. Pick the channel (**Hue**,
**Saturation**, or **Luminance**), then move the band sliders: **Red**,
**Orange**, **Yellow**, **Green**, **Aqua**, **Blue**, **Purple**,
**Magenta**.

#### Color grading

Three colour wheels — **Midtones**, **Shadows**, **Highlights** — each with a
**Luminance** slider and a zone reset; drag a wheel to set hue and
saturation, double-click it to reset. **Blending** and **Balance** control how
the zones mix.

#### Effects

Film-style finishing: **Grain**, **Grain size**, **Vignetting**, and
**Vignette midpoint**.

#### Optics

Lens corrections. The **Auto lens profile** checkbox applies the matched lens
profile, with **Distortion** / **Vignetting** / **Chromatic aberration**
sub-checks and a ✓/✗ lens-match readout. If the lens is misidentified, pick
one in the **Lens override** editable combo. **Lens profile folder** opens the
folder where you can drop your own lensfun XML profiles, and **Reload lens
DB** re-reads it, with a coverage readout. **Manage camera color calibration**
opens a dialog to view or delete saved calibrations. Under **Manual
correction** are **Distortion**, **Vignetting**, **Remove purple fringing**,
and **Remove green fringing**; the **Sample colour** eyedroppers **💧 Purple**
and **💧 Green** let you click the fringing in the preview to set the
reference hue, which is shown next to them.

#### Crop and straighten

**✂ Crop directly on the image** toggles on-image cropping: drag a corner to
resize, drag inside to move, double-click to reset to the whole frame. The
**Ratio** combo offers **Free**, **Original ratio**, 1:1, 4:3, 3:2, and 16:9.
**Straighten** rotates ±45°, with **Left** / **Right** / **Top** / **Bottom**
crop sliders for precise edges. **⟲ 90°** and **⟳ 90°** rotate in steps, and
**Flip horizontal** / **Flip vertical** mirror the frame; the current
rotation is shown.

#### Capture info strip

**Add an info strip below the image** renders a caption bar on export. Choose
the **Background** (**Black background / white text** or **White background /
black text**), tick the fields to include — **Filename**, **Camera**,
**Lens**, **Focal length**, **Aperture**, **Shutter**, **ISO**, **Date
taken** — set the **Strip height** (%), and optionally add free text for the
right side (artist name, etc.).

#### Watermark

**Add watermark** overlays text or a PNG image on export. Enter the text and
pick a **Font**, or **Browse** to a PNG file. **Position** places it on a
nine-cell grid (**↖ Top-left** through **· Center** to **↘ Bottom-right**),
refined by **Opacity**, **Size**, **Margin**, **Horizontal offset**,
**Vertical offset**, and **Rotation**. **Color** opens the "Watermark colour"
picker, and **Shadow (legibility on light backgrounds)** adds a drop shadow.

#### EXIF metadata

**Include EXIF on export** (off by default) writes selected metadata into
exported images. Field checkboxes: **Camera (make/model)**, **Lens**,
**Exposure (shutter/aperture/ISO)**, **Focal length**, **Date taken**,
**Artist**, **Copyright**, **Software**, with **Artist name** and **Copyright
notice** text fields. GPS location data is never written.

### JPEG and HEIF sources

For non-RAW sources, sensor-based items (auto lens profile, camera colour
calibration) are locked with an explanatory note; relative white balance still
works.

## The export queue and exporting

### Adding photos to the queue

The queue collects shots — each with its current develop settings — across
folders, so you can export them in one batch. Add from the grid with the
**Add to queue** toolbar button (`Q` on the selection) or from the loupe with
**Add to queue (Q)**.

### The queue panel

![Queue panel](screenshots/queue.png)

The **Queue ▸** toggle button (the count is shown on its label) opens the
queue panel:

- The table shows **File**, **Develop preset**, **Crop**, and **Grade** for
  each row; missing source files are flagged in red. Double-click a row to
  edit that shot in the develop window.
- Each row has a preset combo — **(no edit)**, **(per-photo edit)**, or a
  saved develop preset. A preset's crop and masks never overwrite the row's
  own.
- **Selected rows:** a bulk preset combo plus **Apply** sets many rows at
  once.
- **Remove selected** deletes rows; **Clear** empties the queue after a
  confirmation.
- **Save** / **Load** store the queue as a JSON file; loading merges into the
  existing queue.
- **Export queue** starts the export (destination picker, then the "Export
  options" dialog). The queue is cleared after a fully successful export.

### Export options

There are three ways in: the **Export** toolbar button (the whole analysed
session), **Export** in the loupe (a single shot), and **Export queue**. Each
asks for a destination folder, then opens the "Export options" dialog.

![Export options](screenshots/export-dialog.png)

- **Files** group — **Grades to export** (keep / review / reject checkboxes;
  none selected means all), **Also export the original RAW**, **Also export
  bundled JPG/HIF/XMP**, **Split into folders by grade (_keep / _review /
  _reject)**, **Split into folders by location (GPS)**, and **Move instead of
  copy** (undoable).
- **Developed images** group — **Render developed images** turns on
  rendering; then choose the **Format** (**JPEG (recommended)**, **PNG
  (lossless, large)**, **WebP**, **TIFF (lossless, for print/re-edit)**),
  **Quality** (%), and **Size** (**Original size**, **By long edge**, or
  **Percentage**), with long-edge presets (**Custom**, 1080, 1920 FHD, 2048
  web, 2560 QHD, 3000, 3840 4K/UHD, 4000, 6000 for print) or direct px / %
  entry.
- **Filename** group — a **Pattern** field with click-to-insert token buttons
  **{name}**, **{index}**, **{grade}**, **{date}**, **{time}**, **{score}**.

A live one-line summary shows what will be exported, with an example
filename. **Export** starts; **Cancel** backs out.

### While an export runs

Exports run in the background with progress and a time estimate in the status
bar; **Stop** (`Esc`) cancels. Grading and develop edits are locked while an
export runs, and only one export runs at a time. A completion dialog ("Export
finished" or "Export cancelled") reports the copied, developed, and failed
counts.

### Undoing an export

The **Undo** toolbar button reverts the most recent export in the current
folder — an "Undo" confirmation first, then "Undo finished". It only removes
files the export created.

## Presets and criteria

### Grading criteria — the Criteria panel

The **Criteria ▸** toggle button opens the grading criteria panel. Changing
any value re-grades the whole batch instantly — no re-analysis — so you can
feel out the thresholds live.

![Criteria panel](screenshots/criteria-panel.png)

At the top is the preset bar: a preset combo ("(unsaved)" plus your saved
presets), **Save** ("Save preset" name dialog), **Import**, **Export**, and
**Delete**. **Save to file** / **Load from file** store the grading criteria
as a YAML file ("Save grading criteria" / "Load grading criteria"). **Restore
defaults** returns everything to stock.

#### Keep criteria

- **Aim for a target ratio** with **Target keep ratio** (%) — keeps roughly
  that share of the batch, with a live score-distribution readout (min, mean,
  max, and the resulting cut score).
- **Absolute keep score** (pts) — a fixed score threshold instead.
- **Keeps per scene** — guarantee this many keeps in every scene (0 means "no
  guarantee").
- **Keep quality floor** (pts) — the minimum score a guaranteed keep must
  reach, with a warning readout for scenes left with no keep.

#### Reject criteria

- **Gap to the scene's best** (pts) — reject shots this far behind the best
  of their scene.
- **Absolute floor** (pts) — reject below this score outright.
- **Batch bottom percentile** (%) — reject the bottom slice of the batch.

A scene's best shot is never rejected, whatever the thresholds.

#### Score weights

A live formula readout shows how the score is built. Below it:

- **Face-priority mode** with its sub-values — **Focus missed the face**,
  **Focus on the face**, **No face**, **Eyes open**, **Eyes closed**, and
  **Eyes-closed threshold (EAR)**.
- ROI trust spins — **Eye**, **Face**, **Estimated subject**, **Whole
  frame** — how much each focus-region type is trusted.
- Bonuses — **Face detected**, **Eyes detected**, **Face size**, and **Face
  size for full bonus** (%).
- Penalties — **Blown highlights**, **Crushed shadows**, **Lens cap / stray
  shutter**, plus **Highlight tolerance** and **Shadow tolerance**.

#### Scene splitting

Controls how the batch is divided into scenes: **Scene gap** (s), **Scene
change distance**, **Distance without a time**, and **Largest scene**.

### Grading presets

Grading criteria save as named presets via the Criteria panel's preset bar,
and as standalone YAML files with **Save to file** / **Load from file** — the
files are plain text you can edit or hand to someone else.

### Develop presets

The develop panel's preset bar works the same way: a develop-preset combo
with **Save**, **Import**, **Export**, and **Delete**. Saved develop presets
also appear in the queue panel's per-row combos, so queued shots can take a
look with one click.

## Command-line interface

The `raw-select` command runs the same analysis core as the GUI, for
scripted or headless culling. Usage: `raw-select FOLDER [options]`. Given
just a folder, it analyses and prints a per-grade summary with counts, ratios,
and failures. The CLI covers selection and file sorting; developing is
GUI-only.

- `--config PATH` — load a settings YAML.
- `--report PATH` — write a per-photo report as `.csv` or `.json` (grade,
  score, group, sharpness, focus source, face count, capture time, lens, ISO,
  shutter, reasons, errors).
- `--export [DIR]` — export into grade folders (`_keep` / `_review` /
  `_reject`); with DIR omitted, they are created inside the source folder.
- `--grades LIST` — export only these grades (comma-separated:
  keep,review,reject).
- `--move` — move instead of copy.
- `--dry-run` — show what would go where without doing it.
- `--undo` — revert the most recent export in the folder.
- `--no-cache` — ignore the cache and re-analyse everything.
- `--workers N` — parallel worker count (default: CPU count minus one).
- `--keep-per-group N` — keeps per scene.
- `--target-keep PCT` — target keep ratio in percent; the threshold is
  derived from the batch's score distribution.
- `--keep-above SCORE` — absolute keep score (overrides the target ratio).
- `--recursive` / `--no-recursive` — include or exclude subfolders.
- `-q` / `--quiet` — hide the progress bar.
- `-v` / `--verbose` — detailed logging.
- `--dump-config` — print the current configuration as YAML.

## Appendix: keyboard shortcuts

### Grid (main window)

| Key | Action |
|---|---|
| `1` / `2` / `3` | Grade the selection keep / review / reject |
| `0` | Clear the manual grade (back to automatic) |
| `Space` | Open the selected photo in the loupe |
| `D` | Open the Develop window for the selection |
| `Q` | Add the selection to the export queue |
| `Esc` | Stop the running task (analysis or export) |
| Double-click | Open the photo (**Preview** or **Develop**, per the toolbar mode) |

### Loupe (Develop window)

| Key | Action |
|---|---|
| `←` / `→` | Previous / next photo |
| `1` / `2` / `3` | Grade keep / review / reject |
| `B` | **Original** — before/after toggle |
| `F` | **Focus** overlay (grading region) |
| `A` | **Faces** overlay |
| `E` | **Eyes** overlay |
| `P` | **AF point** overlay |
| `Z` | **Zoom to focus** |
| `Q` | **Add to queue** |
| Mouse wheel | Zoom |
| Drag | Pan |
| Double-click (image) | Reset the view |
