"""Uniform frame layout and portable TexturePacker-style JSON."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from . import __version__
from .core import PipelineError


@dataclass(frozen=True)
class Layout:
    columns: int
    rows: int
    frame_width: int
    frame_height: int
    padding: int = 0


def make_layout(size: tuple[int, int], *, columns: int = 1, rows: int = 1,
                frame_size: tuple[int, int] | None = None, padding: int = 0) -> Layout:
    width, height = size
    if min(columns, rows) < 1 or padding < 0:
        raise PipelineError("Columns and rows must be positive; padding cannot be negative.")
    if frame_size:
        fw, fh = frame_size
        if min(fw, fh) < 1 or width % fw or height % fh:
            raise PipelineError("Frame dimensions must divide the sampled sheet exactly.")
        columns, rows = width // fw, height // fh
    if width % columns or height % rows:
        raise PipelineError(
            f"Sampled sheet {width}x{height} is not divisible by the {columns}x{rows} frame grid. "
            "Adjust --size, --scale, --crop, or the frame layout."
        )
    return Layout(columns, rows, width // columns, height // rows, padding)


def pack_sheet(image: Image.Image, layout: Layout) -> tuple[Image.Image, dict]:
    fw, fh, padding = layout.frame_width, layout.frame_height, layout.padding
    cell_width, cell_height = fw + 2 * padding, fh + 2 * padding
    size = (cell_width * layout.columns, cell_height * layout.rows)
    if size[0] * size[1] > 64_000_000:
        raise PipelineError("Packed sheet exceeds the 64 megapixel safety limit; reduce --padding.")
    result = Image.new("RGBA", size)
    frames = {}
    for row in range(layout.rows):
        for column in range(layout.columns):
            index = row * layout.columns + column
            x, y = column * cell_width + padding, row * cell_height + padding
            # Paste without an alpha mask: preserve partial alpha and hidden RGB exactly.
            result.paste(image.crop((column * fw, row * fh,
                                     (column + 1) * fw, (row + 1) * fh)), (x, y))
            frames[f"frame_{index:04d}"] = {
                "frame": {"x": x, "y": y, "w": fw, "h": fh},
                "rotated": False, "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": fw, "h": fh},
                "sourceSize": {"w": fw, "h": fh},
                "pivot": {"x": 0.5, "y": 0.5},
                "row": row, "column": column,
            }
    metadata = {
        "frames": frames,
        "meta": {
            "app": "art-pipeline", "version": __version__, "format": "RGBA8888",
            "size": {"w": result.width, "h": result.height}, "scale": "1",
            "coordinateSystem": "top-left",
            "grid": {"columns": layout.columns, "rows": layout.rows,
                     "frameWidth": fw, "frameHeight": fh, "padding": padding,
                     "spacing": 2 * padding},
        },
    }
    return result, metadata

