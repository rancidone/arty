# Usage

Requires Python 3.11+. All operations run locally except explicit `generate`/`edit`.
The existing `analyze`/`convert` commands remain available for Perfect Pixel grid
recovery. Detection confidence is grid evidence, never artistic acceptance.

Follow the [README quickstart](../README.md#install-and-try-it-offline) to clone
and install with a virtual environment and pip. From that activated checkout,
install development and optional MCP dependencies with:

```sh
python -m pip install -e '.[dev,mcp]'
arty --help
python -m pytest -q
```

Alternatively, install [uv](https://docs.astral.sh/uv/getting-started/installation/),
then run `uv sync --frozen --extra dev --extra mcp` from the checkout. Prefix
commands with `uv run` when using that environment. Ordinary CLI installs do not
need the development or MCP extras. Build, atlas and fitting examples that use
only committed files are in [examples](../examples/README.md); see the
[spec reference](specs.md) for JSON fields and defaults.

PNG sources are copied unchanged
into each revision's `inputs/<sha256>/<original filename>`. Use a new output folder
and revision for every change. Build failures never trigger generation. Failed
technical checks retain a draft revision and exit 2; inspect its verification report.
This applies to build, atlas, fit (if any candidate fails), import, extraction,
rebuild, and live generate/edit. `verify` and `inspect` also exit 2 on technical
failure. Successful dry runs exit 0 without creating a revision. A recorded
rejection exits 0: it records a decision rather than accepting the asset.

## Pixel-grid recovery

The `analyze` and `convert` commands recover enlarged pixel grids using Perfect Pixel
and copy one source pixel from the center of each recovered cell. Exports include
a lossless RGBA PNG, TexturePacker-style frame JSON and optional individual frames.
The JSON is portable metadata, not a built-in engine importer.

## Inspect, then convert

```sh
# Inspect estimated pixel scale, grid origin, and edge-alignment scores.
uv run arty analyze generated.png --preview grid-preview.png

# Recover pixels and split the sheet into 4 columns and 2 rows of equal frames.
uv run arty convert generated.png -o output/hero.png \
  --columns 4 --rows 2 --preview output/preview.png

# Optionally export each frame separately and add 1px transparent frame borders.
uv run arty convert generated.png -o output/hero-padded.png \
  --columns 4 --rows 2 --padding 1 --frames-dir output/frames
```

`analyze --json` prints a machine-readable report. The preview puts the proposed
grid over the source and shows an enlarged version of the sampled output. Preview
display scaling uses nearest-neighbor; exported sheets use direct array indexing.
The JSON identifies the backend/version and stores the actual `x_edges` and
`y_edges` for the refined grid. Reported scales are median cell widths and heights;
cell spacing may vary across generated art.

Automatic conversion refuses a grid when either axis has weak or insufficient
evidence. If detection fails entirely, `analyze --json` reports the reason with
`sampling: null` and exit code 2; no preview or conversion is written. Explicit
controls bypass the backend entirely:

```sh
# Each intended pixel occupies 6x6 source pixels.
uv run arty convert generated.png -o output/hero.png --scale 6

# Non-square or fractional source spacing is supported.
uv run arty convert generated.png -o output/hero.png --scale 5.5,6

# Known final sheet dimensions: copy center pixels into exactly this grid.
uv run arty convert generated.png -o output/hero.png \
  --size 128x64 --columns 4 --rows 2

# Remove an outside border in source pixels and set a grid origin inside the crop.
uv run arty convert generated.png -o output/hero.png \
  --crop 12,8,768,384 --scale 6 --offset 0,0 --frame-size 32x32
```

`--size` is the size of the whole sampled sheet before padding, not one frame.
`--frame-size` sets frame dimensions in output pixels and derives the number of
columns and rows; it is an alternative to `--columns`/`--rows`.
If no frame layout is supplied, the entire sheet is one frame.

`--scale` defaults to a grid origin of `0,0`. `--offset` requires `--scale` and is
measured in source pixels relative to the crop. In explicit-scale mode, only
complete cells are kept; excluded margins are reported. Automatic refinement may
produce edge cells that extend past the source boundary; these are accepted only
when their sampled centers lie inside the source, and a warning is reported.
Excluded source margins are also reported. Use a crop or explicit size to control
those margins. Frame dimensions must divide the sampled sheet exactly; the tool
will not silently trim or stretch frames to satisfy a layout.

`--refine-intensity` controls Perfect Pixel's local boundary search, from 0 to 0.5
(default: 0.25); reducing it can help when boundaries drift into nearby details.
`--max-scale` sets the largest recovered median scale accepted by the quality gate
(default: 128), rather than changing the backend's detection algorithm.
`--min-confidence` adjusts the default alignment threshold of 0.65. `--force`
accepts an uncertain recovered grid after visual inspection; it cannot invent a
grid when recovery fails. Existing output files require `--overwrite`; the source
image is protected even with that flag. Success returns exit code 0, and invalid
inputs or an unreliable automatic conversion return 2.

## Engine import

**Godot 4:** Copy the PNG into the project. Use `Sprite2D` with `hframes` equal to
the exported column count and `vframes` equal to its row count for an unpadded
sheet. Set the node's Texture Filter to Nearest (or set the project's default
canvas texture filter). For animation, use `AnimatedSprite2D`/`SpriteFrames` and
add frames from a sprite sheet or use the individual PNGs. Padded sheets require
regions matching the JSON frame rectangles, rather than plain `hframes`/`vframes`.
See the official [sprite-sheet animation guide](https://docs.godotengine.org/en/stable/tutorials/2d/2d_sprite_animation.html)
and [Sprite2D reference](https://docs.godotengine.org/en/stable/classes/class_sprite2d.html).

**Unity:** Import the PNG as **Sprite (2D and UI)**, set Sprite Mode to **Multiple**,
Filter Mode to **Point (no filter)**, disable mipmaps, and choose uncompressed
texture storage. Keep Max Size at least as large as the exported sheet. In Sprite
Editor, slice an unpadded sheet by Grid by Cell Size using the exported frame width
and height. With padding `p`, use offset `p,p` and spacing `2p,2p`, or import the
individual frame PNGs as Single sprites. Pixels Per Unit controls world size, not
the source grid. See the official [sprite import settings](https://docs.unity3d.com/2022.3/Documentation/Manual/texture-type-sprite.html)
and [slicing guide](https://docs.unity3d.com/2022.3/Documentation/Manual/sprite-automatic-slicing.html).

All JSON rectangles use **top-left** coordinates and row-major ordering. Unity
scripts working with bottom-left rectangles must convert
`unity_y = sheet_height - frame.y - frame.h`. No `.meta`, `.import`, or project
configuration files are generated or modified.

## What detection can and cannot recover

Perfect Pixel estimates grid size using FFT/gradient analysis, then refines its
boundaries using Sobel edges. Our adapter calls its detection and refinement
functions directly, retains the recovered boundaries, and implements the same
center-index sampling on the untouched source RGBA array. It does not use the
backend's square correction or median/majority sampling. Detection sees the image
over a neutral gray analysis background so hidden RGB cannot invent edges; this
does not alter exported pixels or remove transparency.

Our quality gate measures how well visible color/alpha edges align with those
recovered boundaries. It requires enough spatially distributed edges on both axes
and rejects weak grids, flat/noisy images, and malformed backend coordinates.
Known backend failure cases are returned as actionable errors rather than Python
tracebacks. Explicit `--scale` and `--size` work even when detection cannot run.

An image that really is an enlarged pixel grid can be recovered exactly. With
generated art, this is not always possible: blocks may have inconsistent widths,
different sprites may use different grids, or the model may have drawn new detail
inside apparent pixels. A strong alignment score is evidence of a repeating grid,
not proof of the artist's intended resolution. Regular details and frame dividers
can also create competing scales. The backend favors approximately square source
pixels; automatic recovery of very small blocks, fractional scales, or strongly
stretched sheets can fail or return the wrong dimensions. Explicit `--size` or
`--scale` supports fractional and non-square source spacing without interpolation.
Review the preview or specify a known size.

The tool preserves the **selected source RGBA8 values**. It cannot reconstruct an
unknown palette before JPEG compression or antialiasing, remove baked-in
backgrounds or labels, infer animation timing, realign drifting sprites, or
identify uneven frame layouts automatically. It does not invent new pixels to
repair an irregular grid. A nontransparent generated background stays opaque.
Crop and process regions separately if their grids differ.

For each output coordinate `(x,y)`, automatic sampling is exactly:

```text
source_x = floor((x_edges[x] + x_edges[x+1]) * 0.5)
source_y = floor((y_edges[y] + y_edges[y+1]) * 0.5)
output[x,y] = source[source_x,source_y]
```

With an explicit uniform `--scale` or `--size`, this becomes:

```text
source_x = floor(offset_x + (x + 0.5) * scale_x)
source_y = floor(offset_y + (y + 0.5) * scale_y)
output[x,y] = source[source_x,source_y]
```

Images are oriented according to EXIF, cropped, and normalized to RGBA8 before
sampling. High-bit-depth/HDR inputs are not kept at their original precision.
Recovery inputs and packed outputs are limited to 64 megapixels. Production
commands (import/build/atlas/fit/API) limit each image to 16 megapixels and total
registered layer pixels per build to 32 megapixels (128 MB of decoded RGBA layer
data, before Pillow copies and previews). Measurements use 256×256 tiles plus a
one-pixel halo rather than per-pixel Python objects. Visible palettes are capped
at 65,536 colors to bound measurement metadata; exceeding the cap is an explicit
failure, never palette reduction. A paid API result failing these checks is
retained as a draft with its exact returned bytes. These are allocation bounds,
not a fixed process-memory guarantee; use small canvases and few layers on
memory-constrained systems. Production
PNG inputs reject animations and decompression-bomb warnings. Production canvas,
resize, placement, rotation and atlas dimensions are checked before allocation;
review displays are bounded before enlargement.

## Reproducible demo and development

```sh
uv run python scripts/make_demo.py
uv run arty convert examples/slime-enlarged.png -o examples/slime-recovered.png \
  --columns 3 --rows 2 --preview examples/preview.png --overwrite
uv run pytest -q
```

The demo is a deterministic test fixture, not an imagegen output: its 96x64
original is enlarged to 1152x768, so recovery can be compared against a known
answer. Tests cover the actual Perfect Pixel backend at common integer scales and
1920x1080 input size, fractional/stretched grids with explicit controls, shifted
origins, mild edge blur, low-confidence rejection, invalid backend results, RGBA
preservation, crop controls, padding, frame coordinates, and output protection.
Actual generated sheets should also be inspected because they may not obey any
single pixel grid.

## Generate and edit

```sh
export OPENAI_API_KEY='your-key' # PowerShell: $env:OPENAI_API_KEY='your-key'
arty generate --prompt prompts/hero.txt --asset hero --revision source-r01 \
  -o work/hero/source-r01 --model gpt-image-2.6-sunburst --size 1024x1024 --dry-run
# Remove --dry-run only when you intend to spend credits.
arty edit --prompt prompts/change-hair.txt --reference base.png \
  --reference hair-reference.png --asset hero --revision source-r02 \
  -o work/hero/source-r02 --quality high --background transparent --dry-run
# Optional: --mask edit-mask.png (alpha PNG matching the first input).
```

Reference-guided generation uses `images.edit`; text-only generation uses
`images.generate`. One PNG per call; split multi-output commissions into explicit
requests. Output bytes are never re-encoded. Model, parameters, prompt/reference
hashes, response identity, usage and output identity are recorded without keys.
SDK retries twice and uses a 180-second timeout; repeating a request may generate
new artwork and incur another charge. No automatic model substitution.

The implementation was checked against OpenAI SDK 3.52.0 signatures and the
[official Images guide](https://developers.openai.com/api/docs/guides/image-generation)
on 2026-11-16. Current Sunburst/Flare size rules: multiples of 16, ratio 1:3..3:1,
655360..8294400 total pixels, neither edge above 3840. 512x512 is invalid. Explicit
capability profiles reject unknown models until documented. Account access is
verified only by a live request. Output format is always PNG; transparent background
is supported. Live API calls are not part of tests.

## Offline import, build and revisions

```sh
arty import generated.png --asset hero --revision source-r01 -o work/hero/source-r01
arty build specs/hero.json -o work/hero/native-r01
arty verify work/hero/native-r01
arty inspect work/hero/native-r01
arty review work/hero/native-r01 -o work/reviews/hero-r01.png
arty rebuild work/hero/native-r01 --revision native-r02 -o work/hero/native-r02
```

Example JSON spec (paths relative to the spec file):

```json
{
  "asset": "hero", "revision": "native-r01", "canvas": [128,128], "root": null,
  "layers": [
    {"id":"hair-back", "path":"../sources/back.png"},
    {"id":"base", "path":"../sources/base.png", "sha256":"replace-with-real-hash"},
    {"id":"face", "path":"../sources/face.png"},
    {"id":"hair-front", "path":"../sources/front.png"}
  ],
  "draw_order":["hair-back","base","face","hair-front"],
  "constraints":{"binary_alpha":true,"rgba":true}, "export_2x":true,
  "references":[{"path":"../sources/authority.png", "status":"approved visual authority", "comparison":"visual_only"}],
  "unknown":["attachments","orientation","timing"], "artistic_limitations":[]
}
```

Omit `sha256` when no identity pin exists; the actual hash is always recorded.
Unknown roots remain null. Optional per-layer `root` must equal the shared root.
An unchanged layer retains source bytes exactly. Editable source snapshots remain
available; edit copies and import/build a new revision. Rebuild uses saved snapshots,
requires their identity verification and never calls the API.

Explicit per-layer `operations` run in order:

- `{"op":"sample_grid","size":[128,128]}` uses the existing center-pixel sampler;
  or supply `grid` with exact `Grid` fields including measured `x_edges/y_edges`.
- `{"op":"nearest","size":[256,256]}` explicit nearest-neighbor resizing.
- `{"op":"place","canvas":[512,512],"offset":[20,30]}` full-canvas registration;
  refuses visible pixel loss at edges.
- `{"op":"alpha_threshold","value":128}` explicit binary-alpha conversion.
- `{"op":"retain_rect","rect":[0,0,256,256]}` explicit destructive selection,
  retaining the canvas. Never an implicit cleanup or scalp mask.
- `{"op":"palette_map","colors":{"#123456":"#654321"}}` explicit RGB mapping.

These operations are draft adaptations, not proof that the design survives native
resolution. Keep intermediates and review the silhouette/face/equipment against
visual authority. Hidden pixels from a flattened reference cannot be claimed as
exact extractions. Per-layer `pixel_origin` records this distinction. Additional
constraints: `palette` (hex colors), `max_isolated_pixels`, `allowed_bounds` (xywh),
`clean_transparent_rgb`. Measurements always report alpha counts, partial alpha,
RGB palette/counts, visible bounds, isolated pixels and hidden RGB. Isolated pixels
are review signals; eyes and highlights may intentionally be isolated.

## Atlases

```sh
arty atlas examples/specs/atlas.json -o work/atlas/r01
arty extract work/atlas/r01 -o work/atlas/cells-r01
```

See the [atlas spec reference](specs.md#atlas-spec) and the
[runnable atlas example](../examples/README.md). Duplicate cells and mismatched
frame dimensions fail. Empty cells remain empty; frame IDs and timing are never
invented. `grounded_row` can be specified per frame when a source contract
establishes a sole row. Extraction preserves cell root.

## Review and explicit decisions

```sh
# Only after a human actually reviews the comparison:
arty approve work/hero/native-r01 --by 'Reviewer name' \
  --reference sources/authority.png --note 'Reviewed silhouette, face and fit against authority'
arty reject work/hero/native-r01 --by 'Reviewer name' --note 'Crown curl lost volume'
```

Approval requires successful current verification and a reference whose hash is
saved in the revision. Declared artistic limitations block approval; resolve them
in a new revision. Technical pass never sets artistic approval. Decisions are
append-only adjacent records (`<revision>.decisions/`), bound to manifest, input,
and output hashes. Inspect reports invalidation after any such file changes.
Rejections remain valid when the unchanged revision fails technical checks;
approvals additionally require current technical success.
Reviews are written outside the immutable revision. Only explicitly registered
comparisons get pixel-difference views; different scale/pose references are visual
comparisons. Review sidecars bind the board to revision hashes. Both review output paths refuse
existing files, including symlinks and files created by concurrent writers. If a
concurrent writer blocks publication, one newly published output may remain;
choose a new output path for the next attempt. Verification rejects unknown kinds,
missing required contents and malformed identity records before checking hashes.

## Troubleshooting

- Dimension error: inspect actual PNG size and metadata; explicitly adapt/register a
  draft rather than assuming every sprite has the atlas dimensions.
- Partial alpha/noise: inspect measurements; manually edit or declare a threshold,
  never auto-clean silently. A checkerboard baked into RGB is not transparency.
- Identity mismatch: stop processing that source and reconcile the correct revision.
- Existing revision: choose a new revision; no overwrite flag is provided.
- Stale `.lock` after a process kill: confirm no writer is running before removing
  that revision's lock. Normal exceptions clean up staging and locks.
- API error: inspect the sanitized error type/status; check environment credentials,
  model access and billing locally. Keys never belong in prompts/manifests/config.
- API output contract failure: exact returned bytes are still saved for diagnosis;
  build/adapt locally from them, without regenerating automatically.

Tests run offline using synthetic images and mocked SDK requests. The CI matrix
covers Python 3.11–3.14 on macOS, Linux and Windows; see the actual workflow
results before claiming a platform has passed. Release checks additionally install
the wheel and source archive into isolated environments outside the checkout and
run the tests and examples. See [contributing](../CONTRIBUTING.md).

## Fit an overlay locally

`arty fit fitting.json -o work/fitting-r01` builds explicit candidates from
one build spec and writes a labeled `comparison.png`. It calls no API and makes no
automatic artistic choice. Each candidate is a complete immutable build with source
snapshots, measurements and verification; `fit.json` records transforms and hashes.

```json
{
  "build_spec":"character.json", "layer":"weapon",
  "view_rect":[0,0,128,128], "zoom":4,
  "candidates":[
    {"id":"small", "label":"Small sword", "operations":[
      {"op":"mask","mode":"keep","polygons":[[[10,10],[30,10],[30,40],[10,40]]]},
      {"op":"crop","rect":[10,10,21,31]},
      {"op":"nearest","size":[14,21]},
      {"op":"rotate","degrees_cw":10},
      {"op":"place","canvas":[1024,1024],"offset":[70,80]}
    ]}
  ]
}
```

Candidate operations replace that layer's operations. Input paths in the build spec
remain relative to that build spec. `view_rect` is a display crop, never an asset
crop; candidates retain the full declared canvas. Up to 12 candidates, unique IDs.
Failed technical candidates remain available for diagnosis; inspect their reports.

`mask` accepts editable integer pixel-index `polygons` (inclusive boundary) and
`rects` (xywh, right/bottom exclusive), with `mode:keep` or `erase`. Removed pixels
become RGBA0; selected RGBA values are unchanged. A foreground hand layer can copy
selected original base pixels above a weapon; new painted hand corrections must
be imported and identified as authored artwork. No anatomical segmentation is
inferred. Keep project-specific selections and transforms with project assets.

`crop` changes canvas explicitly and refuses visible pixel loss unless
`allow_pixel_loss:true` is specified. Masking first makes selection intentional.
`rotate` uses clockwise degrees, nearest-neighbor sampling and an expanded canvas;
placement is subsequently explicit in output coordinates. Arbitrary rotation and
reduction can change pixel clusters: inspect them visually. No blur or palette
quantization is applied. Native root metadata stays in the build spec; transformed
prop attachment coordinates are not inferred or advertised as measured.

### User configuration

Live SDK client creation loads `~/.config/arty/.env` (or
`$XDG_CONFIG_HOME/arty/.env`), then the editable arty checkout's
`.env` as fallback only when the module lives in `src/art_pipeline` beneath an
arty `pyproject.toml`. Wheel installations never search parent directories
for `.env`. Existing process environment values take precedence over
all files; Arty user configuration takes precedence over legacy
`~/.config/art-pipeline/.env`, which remains supported, then checkout configuration.
Use owner-only permissions for files containing credentials. Files contain
`KEY=value` assignments, optional `export`, quoted values and comments. They are
parsed as data; shell commands and variable interpolation are not executed.
Offline commands and dry runs do not load credentials. The installed editable
command uses this same client setup. Python callers needing custom retry settings
can use `art_pipeline.images_api.create_client(max_retries=0)`.

API rejection errors printed by the CLI include the server's structured message,
type, code, parameter and request ID when present. Credential values are redacted;
headers, request payloads and arbitrary exception text are not dumped.

Layer IDs `preview` and `preview-2x` (case insensitive) are reserved for generated
outputs. Atlas reference and dependency paths resolve relative to the atlas spec.
