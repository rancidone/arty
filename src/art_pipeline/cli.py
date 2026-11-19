"""Command-line analysis and conversion."""

from __future__ import annotations

import argparse
from io import BytesIO
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import warnings

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from . import __version__
from .core import Detection, PipelineError, detect_grid, grid_from_scale, grid_from_size, sample
from .sheet import make_layout, pack_sheet


def _numbers(text: str, count: int, *, integer: bool = False) -> tuple:
    try:
        pieces = text.lower().replace("x", ",").split(",")
        values = tuple((int if integer else float)(piece) for piece in pieces)
        if len(values) != count or any(not math.isfinite(value) for value in values):
            raise ValueError
        return values
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError(f"Expected {count} finite numbers separated by commas or 'x'.") from None


def _scale(text: str) -> tuple[float, float]:
    if "," not in text and "x" not in text.lower():
        text = f"{text},{text}"
    return _numbers(text, 2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arty",
        description="Pixel-grid recovery and local art production.",
        epilog="Production commands: generate, edit, import, build, verify, review, approve, reject, inspect, rebuild, atlas, extract, fit. Run arty COMMAND --help.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("analyze", "Inspect pixel-grid evidence and a proposed sampling grid."),
                            ("convert", "Sample and export a PNG sprite sheet plus JSON.")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("input", type=Path)
        selection = command.add_mutually_exclusive_group()
        selection.add_argument("--scale", type=_scale, metavar="N|X,Y",
                               help="Source pixels per output pixel, e.g. 6 or 5.5,6.")
        selection.add_argument("--size", type=lambda text: _numbers(text, 2, integer=True),
                               metavar="WIDTHxHEIGHT", help="Exact output size before padding.")
        command.add_argument("--offset", type=lambda text: _numbers(text, 2), metavar="X,Y",
                             help="Grid origin inside the crop; requires --scale (default: 0,0).")
        command.add_argument("--crop", type=lambda text: _numbers(text, 4, integer=True),
                             metavar="X,Y,W,H", help="Source-pixel rectangle to process.")
        command.add_argument("--max-scale", type=float, default=128,
                             help="Largest recovered median pixel scale to accept (default: 128).")
        command.add_argument("--min-confidence", type=float, default=0.65,
                             help="Minimum automatic edge-alignment score (default: 0.65).")
        command.add_argument("--refine-intensity", type=float, default=0.25,
                             help="Perfect Pixel boundary refinement, from 0 to 0.5 (default: 0.25).")
        command.add_argument("--preview", type=Path, metavar="PNG",
                             help="Save an inspection image with a grid overlay and enlarged result.")
        command.add_argument("--overwrite", action="store_true", help="Replace existing output files.")
        if name == "analyze":
            command.add_argument("--json", action="store_true", help="Print the full report as JSON.")
        else:
            command.add_argument("-o", "--output", type=Path, required=True, metavar="PNG")
            command.add_argument("--columns", type=int, default=1,
                                 help="Number of frame columns in the source (default: 1).")
            command.add_argument("--rows", type=int, default=1,
                                 help="Number of frame rows in the source (default: 1).")
            command.add_argument("--frame-size", type=lambda text: _numbers(text, 2, integer=True),
                                 metavar="WIDTHxHEIGHT", help="Sampled frame size; alternative to --columns/--rows.")
            command.add_argument("--padding", type=int, default=0,
                                 help="Add transparent pixels around each frame (default: 0).")
            command.add_argument("--frames-dir", type=Path, help="Also export each frame as a separate PNG.")
            command.add_argument("--force", action="store_true",
                                 help="Allow an unreliable automatic grid. Inspect the preview first.")
    return parser


def _read_image(path: Path, crop: tuple | None) -> tuple[Image.Image, dict]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as opened:
            if getattr(opened, "n_frames", 1) != 1:
                raise PipelineError("Input must be a single sprite-sheet image, not an animation.")
            if opened.width * opened.height > 64_000_000:
                raise PipelineError("Input exceeds the 64 megapixel safety limit.")
            oriented = ImageOps.exif_transpose(opened)
            size = oriented.size
            rectangle = crop or (0, 0, *size)
            x, y, width, height = rectangle
            if (min(x, y) < 0 or min(width, height) < 1
                    or x + width > size[0] or y + height > size[1]):
                raise PipelineError("Crop must be a positive rectangle entirely inside the oriented source image.")
            image = oriented.crop((x, y, x + width, y + height)).convert("RGBA")
            return image, {"image": path.name, "size": list(size), "crop": list(rectangle)}


def _analyze(args: argparse.Namespace, image: Image.Image, source: dict):
    if not math.isfinite(args.max_scale) or args.max_scale < 2:
        raise PipelineError("--max-scale must be finite and at least 2.")
    if not math.isfinite(args.min_confidence) or not 0 <= args.min_confidence <= 1:
        raise PipelineError("--min-confidence must be between 0 and 1.")
    if not math.isfinite(args.refine_intensity) or not 0 <= args.refine_intensity <= 0.5:
        raise PipelineError("--refine-intensity must be between 0 and 0.5.")
    if args.offset is not None and args.scale is None:
        raise PipelineError("--offset requires --scale; use --crop to set the region with --size.")
    messages = []
    detection = Detection(None).to_dict()
    detection.update(attempted=False, reliable=None)
    if args.size:
        grid = grid_from_size(image.size, args.size)
        method = "explicit-size"
    elif args.scale:
        grid = grid_from_scale(image.size, args.scale, args.offset or (0.0, 0.0))
        method = "explicit-scale"
        trailing_x = image.width - grid.offset_x - grid.width * grid.scale_x
        trailing_y = image.height - grid.offset_y - grid.height * grid.scale_y
        if max(grid.offset_x, grid.offset_y, trailing_x, trailing_y) > 0.1:
            messages.append("Only complete grid cells are sampled; leading/trailing source margins are excluded.")
    else:
        recovered = detect_grid(image, max_scale=args.max_scale, min_confidence=args.min_confidence,
                                refine_intensity=args.refine_intensity)
        detection = recovered.to_dict()
        detection["refineIntensity"] = args.refine_intensity
        grid = recovered.grid
        method = "automatic"
        if not recovered.reliable:
            messages.append((recovered.error or "The automatic grid has weak edge alignment.")
                            + " Inspect the source and supply --scale or --size.")
        if grid:
            xs, ys = grid.boundaries()
            if min(xs[0], ys[0]) < 0 or xs[-1] > image.width or ys[-1] > image.height:
                messages.append("Refined edge cells extend beyond the source; their sampled centers are inside it.")
            if max(xs[0], ys[0], image.width - xs[-1], image.height - ys[-1]) > 0.1:
                messages.append("The recovered grid excludes source margins; inspect the preview or use --crop/--size.")
    report = {
        "version": __version__, "source": source,
        "detection": detection,
        "sampling": ({"method": method, **grid.to_dict(), "filter": "none",
                      "rule": "floor((cell_start + cell_end) * 0.5)",
                      "colorMode": "RGBA8", "sourcePixelsPreserved": True} if grid else None),
        "warnings": messages,
    }
    return grid, report


def _png(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _preview(source: Image.Image, sampled: Image.Image, grid) -> Image.Image:
    # The preview may be displayed at a different size; exported pixels never use resize.
    ratio = min(1.0, 768 / max(source.size))
    shown = (max(1, round(source.width * ratio)), max(1, round(source.height * ratio)))
    left = source.resize(shown, Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(left)
    x_edges, y_edges = grid.boundaries()
    for boundary in x_edges:
        x = round(boundary * ratio)
        draw.line((x, 0, x, left.height - 1), fill=(255, 40, 170, 255))
    for boundary in y_edges:
        y = round(boundary * ratio)
        draw.line((0, y, left.width - 1, y), fill=(255, 40, 170, 255))
    zoom = max(1, min(8, 768 // max(sampled.size)))
    right = sampled.resize((sampled.width * zoom, sampled.height * zoom), Image.Resampling.NEAREST)
    if max(right.size) > 768:
        scale = 768 / max(right.size)
        right = right.resize((max(1, round(right.width * scale)), max(1, round(right.height * scale))),
                             Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (left.width + right.width + 24, max(left.height, right.height) + 36),
                       (28, 30, 36, 255))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), "Source + proposed grid", fill="white")
    draw.text((left.width + 16, 8), f"Sampled: {sampled.width} x {sampled.height}", fill="white")
    canvas.paste(left, (8, 28))
    canvas.paste(right, (left.width + 16, 28))
    return canvas


def _publish_new(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except OSError as error:
        # Windows rename refuses existing destinations, including a racing writer.
        # It remains an atomic publication on filesystems lacking hard links.
        # POSIX rename overwrites, so it must never be used as that fallback.
        import errno
        unsupported = error.errno in {errno.EPERM, errno.EACCES, errno.ENOSYS,
                                      errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV} or getattr(error, "winerror", None) in {1, 50}
        if sys.platform != "win32" or not unsupported:
            raise
        os.rename(temporary, destination)
    else:
        temporary.unlink()


def _write_files(outputs: list[tuple[Path, bytes]], source: Path, overwrite: bool) -> None:
    resolved = [path.resolve() for path, _ in outputs]
    if len(resolved) != len(set(resolved)):
        raise PipelineError("Output paths collide; use distinct paths for the PNG, preview, JSON, and frames.")
    if source.resolve() in resolved:
        raise PipelineError("An output would replace the input image; choose a different output path.")
    for path, _ in outputs:
        if path.exists() and (not overwrite or not path.is_file()):
            raise PipelineError(f"Output exists: {path}. Choose another path or use --overwrite for files.")
    # Stage all files before publishing, so a failed encode/write leaves existing files alone.
    staged: list[tuple[Path, Path]] = []
    try:
        for path, content in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".arty-", delete=False) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, path))
                handle.write(content)
        for temporary, destination in staged:
            if overwrite:
                os.replace(temporary, destination)
            else:
                # A racing conversion must not silently overwrite another process's output.
                _publish_new(temporary, destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def _print_report(report: dict) -> None:
    x, y = report["detection"]["x"], report["detection"]["y"]
    sampling = report["sampling"]
    if sampling:
        qualifier = "Median cell size" if sampling["geometry"] == "refined" else "Grid"
        print(f"{qualifier}: {sampling['scale_x']:.6g} x {sampling['scale_y']:.6g} source pixels per output pixel")
        print(f"Sampled size: {sampling['width']} x {sampling['height']}")
        print(f"Grid origin: {sampling['offset_x']:.6g}, {sampling['offset_y']:.6g} (inside crop)")
    else:
        print("No usable automatic grid recovered.")
    if x and y:
        print(f"Edge alignment: X {x['confidence']:.1%}, Y {y['confidence']:.1%}")
    for message in report["warnings"]:
        print(f"Warning: {message}", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    image, source = _read_image(args.input, args.crop)
    grid, report = _analyze(args, image, source)
    if args.command == "convert":
        if args.output.suffix.lower() != ".png":
            raise PipelineError("--output must have a .png extension for lossless output.")
        if report["detection"]["attempted"] and (grid is None or (
                not report["detection"]["reliable"] and not args.force)):
            raise PipelineError(
                f"{report['detection']['error'] or 'Cannot confidently identify a pixel grid.'} "
                "Run 'arty analyze INPUT --json', then provide --scale or --size. "
                "--force can accept weak evidence only when a usable grid was recovered."
            )
        if args.frame_size and (args.columns != 1 or args.rows != 1):
            raise PipelineError("Use --frame-size or --columns/--rows, not both.")
    if grid is None:
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_report(report)
        return 2
    sampled = sample(image, grid)
    outputs = []
    if args.command == "convert":
        layout = make_layout(sampled.size, columns=args.columns, rows=args.rows,
                             frame_size=args.frame_size, padding=args.padding)
        packed, metadata = pack_sheet(sampled, layout)
        metadata["meta"]["image"] = args.output.name
        metadata["meta"]["recovery"] = report
        outputs.extend(((args.output, _png(packed)),
                        (args.output.with_suffix(".json"), (json.dumps(metadata, indent=2) + "\n").encode())))
        if args.frames_dir:
            for name, frame in metadata["frames"].items():
                r = frame["frame"]
                frame_image = packed.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
                outputs.append((args.frames_dir / f"{name}.png", _png(frame_image)))
    if args.preview:
        outputs.append((args.preview, _png(_preview(image, sampled, grid))))
    _write_files(outputs, args.input, args.overwrite)
    if args.command == "analyze" and args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
        if args.command == "convert":
            print(f"Wrote {args.output} and {args.output.with_suffix('.json')} ({layout.columns * layout.rows} frames)")
    return 0


def main(argv: list[str] | None = None) -> int:
    from .production_cli import COMMANDS, main as production_main
    actual = list(sys.argv[1:] if argv is None else argv)
    if actual and actual[0] in COMMANDS:
        return production_main(actual)
    args = _parser().parse_args(actual)
    try:
        return run(args)
    except (PipelineError, OSError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        print(f"arty: {error}", file=sys.stderr)
        return 2

