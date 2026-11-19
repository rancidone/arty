# JSON specification reference

Specs are UTF-8 JSON objects. File paths resolve relative to the spec containing
them, including in MCP calls; absolute paths are allowed. Specs are trusted local
inputs, not a filesystem sandbox. Output directories are supplied on the CLI and
must be new. Unknown application fields are retained as metadata, not validated
as a project contract. See [runnable examples](../examples/README.md).

Identities (`asset`, `revision`, layer/candidate `id`) start with an ASCII letter
or digit and contain only letters, digits, `_`, `.`, or `-`. Layer IDs `preview`
and `preview-2x` are reserved, case insensitive. Windows device names such as `CON`, `NUL`, and `COM1` (including extensions),
trailing dots/spaces, and names longer than 255 UTF-16 units are rejected on all
platforms. Layer and fitting candidate IDs must be unique ignoring case. Source
snapshot filenames must also be portable and must not collide ignoring case.

All coordinates use pixel edges, origin at top left, x right, y down. Rectangles
are `[x,y,width,height]`; sizes are `[width,height]`. Roots describe shared
registration; `null` means unknown. Transforms do not infer or update anchors.

## Build spec

| Field | Required / default | Meaning |
| --- | --- | --- |
| `asset`, `revision` | Required | Asset identity and revision label; CLI `--revision` overrides the label |
| `canvas` | Required | Positive integer dimensions; every adapted layer must match |
| `layers` | Required, nonempty | Layer records, described below |
| `draw_order` | Required | Every layer ID exactly once, back to front |
| `root` | `null` | Shared native registration coordinate `[x,y]` |
| `constraints` | `{}` | Checks applied to all layers |
| `references` | `[]` | Saved visual authorities/comparisons |
| `dependencies` | `[]` | Additional files saved for provenance |
| `export_2x` | `false` | Exact nearest-neighbor preview at twice native size |
| `unknown` | Attachments, grips, orientation, timing | Unmeasured properties |
| `artistic_limitations` | `[]` | Explicit unresolved limitations; any entries block approval |

A layer requires `id` and `path`. Optional `sha256` pins exact input bytes;
`operations: []` preserves those bytes in the delivered layer. `root` defaults to
the shared root and must equal it. `constraints: {}` overrides global constraints
by key. `pixel_origin` is a provenance note, defaulting to
`source-derived; hidden pixels unknown`.

References require `path`; optional fields are `sha256`, `status` (default
`unapproved`), `note`, and `comparison` (default `visual_only`). Use
`registered_comparable` for same-size visual/difference inspection, or
`registered_exact` to also require exact RGBA composition equality during
verification. Use `visual_only` for different poses, sizes or scales. Dependencies
require `path` and optionally `sha256`.

| Constraint | Meaning |
| --- | --- |
| `rgba: true` | Delivered layer must be PNG RGBA mode |
| `binary_alpha: true` | Only alpha 0 and 255 |
| `palette: ["#RRGGBB", ...]` | Allowed visible RGB colors |
| `max_isolated_pixels: N` | Maximum visible pixels with no four-connected visible neighbor |
| `allowed_bounds: [x,y,w,h]` | Visible bounds must lie within this rectangle |
| `clean_transparent_rgb: true` | Fully transparent pixels must have zero RGB |

Constraints report failures; they never clean or alter artwork automatically.

## Operations

A layer's `operations` array runs in order. All sampling is explicit; no blur,
automatic palette reduction, segmentation or inferred cleanup is performed.

| `op` | Parameters | Behavior |
| --- | --- | --- |
| `sample_grid` | `size: [w,h]` or `grid` | Center-pixel sampling; `size` is the whole layer |
| `nearest` | `size: [w,h]` | Nearest-neighbor resize |
| `place` | `canvas: [w,h]`, `offset: [x,y]` | Copy onto transparent canvas; refuses visible loss |
| `alpha_threshold` | `value: 1..255` | Set alpha to 0 below threshold, otherwise 255 |
| `retain_rect` | `rect: [x,y,w,h]` | Explicit destructive selection, retaining canvas |
| `palette_map` | `colors: {"#RRGGBB":"#RRGGBB"}` | Map RGB exactly; preserve alpha |
| `crop` | `rect`, optional `allow_pixel_loss: false` | Change canvas; refuses visible loss by default |
| `rotate` | `degrees_cw` | Finite clockwise angle, nearest-neighbor, expanded canvas |
| `mask` | `mode: "keep"` or `"erase"`, `polygons` and/or `rects` | Clear unselected/selected pixels to RGBA0, retaining canvas |

Mask polygons are arrays of at least three integer `[x,y]` points within the image,
with inclusive boundaries; rectangles use right/bottom-exclusive xywh. At least
one selection is required. Arbitrary rotation and reduction can change clusters;
inspect the result.

An exact `grid` object uses `width`, `height`, `scale_x`, `scale_y`, optional
`offset_x`/`offset_y` (default 0), and optional `x_edges`/`y_edges`. Prefer copying
the geometry fields from `analyze --json`'s `sampling` object; omit report-only
`method`, `filter`, `rule`, `geometry`, `colorMode`, and `sourcePixelsPreserved`.
Refined edge arrays have output dimension + 1 entries. Centers must remain
inside the source. Use `size` when no measured boundaries exist.

## Atlas spec

Required fields are `asset`, `revision`, `cell: [w,h]`, `grid: [columns,rows]`,
and `frames` (an array). Each frame requires `path` and `cell: [column,row]`;
optional `id` labels it, `sha256` pins source identity, and `grounded_row` requires
its last visible pixel row to equal that cell-local row. Duplicate or out-of-range
cells and mismatched frame dimensions fail. Unfilled cells stay transparent.

Optional fields shared with build specs are `root`, `constraints`, `references`,
`dependencies`, `export_2x`, `unknown`, and `artistic_limitations`.
The canvas is derived from cell × grid; packing produces one layer named `atlas`.
No frame sequence or timing is inferred.

## Fitting spec

| Field | Required / default | Meaning |
| --- | --- | --- |
| `build_spec` | Required | Path to a build spec, resolved relative to the fitting spec |
| `layer` | Required | Existing layer ID to vary |
| `view_rect` | Required | Display crop inside the build canvas |
| `zoom` | `4` | Integer 1..16; each zoomed crop limited to four megapixels |
| `candidates` | Required | 1..12 uniquely named candidates |

Each candidate requires `id` and `operations` and optionally a display `label`.
Its operations **replace** the target layer's operations. Other build fields and
references remain in force: an exact registered reference may therefore reject
an intentionally changed candidate. Use comparable visual references when
exploring changes. Paths within the build spec resolve relative to that build
spec, not the fitting spec.

The result contains candidate revisions, `comparison.png`, and `fit.json` with
per-candidate technical results and preview hashes. Any technical failure makes
the CLI exit 2 while retaining drafts. No candidate is automatically selected or
artistically approved. See [resource limits](usage.md#what-detection-can-and-cannot-recover).
