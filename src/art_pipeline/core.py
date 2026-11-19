"""Perfect Pixel grid recovery and exact RGBA sampling. No image resizer is used."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from importlib.metadata import version
from io import StringIO
from math import floor

import numpy as np
from PIL import Image
from perfect_pixel import perfect_pixel_noCV2 as _backend


class PipelineError(ValueError):
    """An actionable problem with input, detection, or layout."""


@dataclass(frozen=True)
class AxisDetection:
    scale: float
    offset: float
    confidence: float
    evidence_edges: int
    reliable: bool


@dataclass(frozen=True)
class Grid:
    width: int
    height: int
    scale_x: float
    scale_y: float
    offset_x: float = 0.0
    offset_y: float = 0.0
    x_edges: tuple[float, ...] | None = None
    y_edges: tuple[float, ...] | None = None

    def boundaries(self) -> tuple[np.ndarray, np.ndarray]:
        xs = (np.asarray(self.x_edges) if self.x_edges is not None
              else self.offset_x + np.arange(self.width + 1) * self.scale_x)
        ys = (np.asarray(self.y_edges) if self.y_edges is not None
              else self.offset_y + np.arange(self.height + 1) * self.scale_y)
        return xs, ys

    def to_dict(self) -> dict:
        result = asdict(self)
        result["geometry"] = "refined" if self.x_edges is not None else "uniform"
        return result


@dataclass(frozen=True)
class Detection:
    grid: Grid | None
    x: AxisDetection | None = None
    y: AxisDetection | None = None
    error: str | None = None

    @property
    def reliable(self) -> bool:
        return bool(self.grid and self.x and self.y and self.x.reliable and self.y.reliable)

    def to_dict(self) -> dict:
        return {
            "backend": "perfect-pixel", "backendVersion": version("perfect-pixel"),
            "attempted": True, "reliable": self.reliable, "error": self.error,
            "x": asdict(self.x) if self.x else None,
            "y": asdict(self.y) if self.y else None,
            "confidenceMeaning": "Visible edge alignment to recovered boundaries; not a probability of exact recovery.",
        }


def _edge_profile(rgba: np.ndarray, axis: int) -> np.ndarray:
    # Quality assessment alone uses premultiplied color. Source RGBA is never changed.
    if rgba.shape[axis] < 2:
        return np.zeros(rgba.shape[axis], dtype=np.float64)
    alpha = rgba[:, :, 3].astype(np.float32) / 255.0
    edge = np.abs(np.diff(alpha, axis=axis))
    for channel in range(3):
        color = rgba[:, :, channel].astype(np.float32) / 255.0 * alpha
        np.maximum(edge, np.abs(np.diff(color, axis=axis)), out=edge)
    np.maximum(edge - 3.0 / 255.0, 0.0, out=edge)
    profile = edge.mean(axis=1 - axis, dtype=np.float64)
    profile = np.maximum(profile - np.percentile(profile, 30), 0.0)
    return np.concatenate(([0.0], profile))


def _assess_axis(profile: np.ndarray, edges: np.ndarray, min_confidence: float,
                 max_scale: float) -> AxisDetection:
    positions = np.arange(len(profile))
    cells = np.clip(np.searchsorted(edges, positions, side="right") - 1, 0, len(edges) - 2)
    phase = (positions - edges[cells]) / (edges[cells + 1] - edges[cells])
    total = float(profile.sum())
    score = float(np.dot(profile, np.cos(2 * np.pi * phase)) / total) if total > 0 else 0.0
    confidence = float(np.clip(score, 0, 1))
    peaks = np.flatnonzero(
        (profile[1:-1] >= profile[:-2]) & (profile[1:-1] > profile[2:])
        & (profile[1:-1] > profile.max() * 0.08)
    ) + 1
    scale = float(np.median(np.diff(edges)))
    spread = float(peaks[-1] - peaks[0]) / scale if len(peaks) > 1 else 0.0
    reliable = bool(confidence >= min_confidence and len(peaks) >= 6 and spread >= 3
                    and 2 <= scale <= max_scale)
    return AxisDetection(scale, float(edges[0]), confidence, len(peaks), reliable)


def _validate_edges(values, extent: int) -> np.ndarray:
    edges = np.asarray(values, dtype=np.float64)
    if (edges.ndim != 1 or len(edges) < 2 or len(edges) > extent + 1
            or not np.isfinite(edges).all() or np.any(np.diff(edges) < 1)):
        raise PipelineError("Perfect Pixel returned an invalid or overlapping grid.")
    centers = np.floor((edges[1:] + edges[:-1]) * 0.5)
    if centers[0] < 0 or centers[-1] >= extent:
        raise PipelineError("Perfect Pixel returned sampling coordinates outside the source.")
    return edges


def detect_grid(image: Image.Image, *, max_scale: float = 128,
                min_confidence: float = 0.65, refine_intensity: float = 0.25) -> Detection:
    """Delegate detection/refinement to the pinned NumPy backend, then assess its grid.

    Calling its component functions retains the actual boundaries for previews and
    metadata. No square correction, median sampling, or majority sampling is used.
    """
    rgba = np.asarray(image.convert("RGBA"))
    if min(image.size) < 8:
        return Detection(None, error="Image is too small for automatic grid recovery.")
    # A neutral analysis backdrop makes alpha edges visible and ignores hidden RGB.
    # Sampling still indexes the original, unmodified RGBA image.
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    visible = (rgba[:, :, :3] * alpha + 127 * (1 - alpha)).astype(np.uint8)
    if float(np.ptp(visible.astype(np.float32), axis=(0, 1)).max()) < 3:
        return Detection(None, error="Image has no visible pixel-grid evidence.")
    try:
        # Upstream prints progress to stdout. Keep analyze --json machine-readable.
        with redirect_stdout(StringIO()):
            counts = _backend.detect_grid_scale(visible)
            if counts is None or len(counts) != 2 or any(value is None for value in counts):
                return Detection(None, error="Perfect Pixel could not estimate a grid.")
            if any(not np.isfinite(value) or value < 1 or value > extent
                   for value, extent in zip(counts, image.size)):
                return Detection(None, error="Perfect Pixel returned invalid grid dimensions.")
            xs, ys = _backend.refine_grids(visible, int(round(counts[0])), int(round(counts[1])),
                                         refine_intensity)
        xs = _validate_edges(xs, image.width)
        ys = _validate_edges(ys, image.height)
    except (TypeError, ValueError, IndexError, ArithmeticError) as error:
        return Detection(None, error=f"Perfect Pixel could not recover a usable grid ({type(error).__name__}).")
    x = _assess_axis(_edge_profile(rgba, 1), xs, min_confidence, max_scale)
    y = _assess_axis(_edge_profile(rgba, 0), ys, min_confidence, max_scale)
    grid = Grid(len(xs) - 1, len(ys) - 1, x.scale, y.scale, x.offset, y.offset,
                tuple(map(float, xs)), tuple(map(float, ys)))
    return Detection(grid, x, y)


def grid_from_scale(size: tuple[int, int], scale: tuple[float, float],
                    offset: tuple[float, float] = (0.0, 0.0)) -> Grid:
    counts = []
    for extent, step, start in zip(size, scale, offset):
        if not np.isfinite(step) or step < 1:
            raise PipelineError("Pixel scale must be finite and at least 1; upscaling is not supported.")
        if not np.isfinite(start) or start < 0 or start >= extent:
            raise PipelineError("Offset must be finite, nonnegative, and inside the cropped image.")
        count = (extent - start) / step
        count = round(count) if abs(count - round(count)) < 0.02 else floor(count)
        if count < 1:
            raise PipelineError("The requested grid has no complete pixels inside the source.")
        counts.append(count)
    return Grid(*counts, *scale, *offset)


def grid_from_size(size: tuple[int, int], target: tuple[int, int]) -> Grid:
    if min(target) < 1 or any(out > source for out, source in zip(target, size)):
        raise PipelineError("Target dimensions must be positive and no larger than the source.")
    return Grid(*target, size[0] / target[0], size[1] / target[1])


def sample(image: Image.Image, grid: Grid) -> Image.Image:
    """Copy the source RGBA pixel containing each cell's center, with no blending."""
    x_edges, y_edges = grid.boundaries()
    xs = np.floor((x_edges[1:] + x_edges[:-1]) * 0.5).astype(int)
    ys = np.floor((y_edges[1:] + y_edges[:-1]) * 0.5).astype(int)
    if (min(grid.width, grid.height) < 1 or len(xs) != grid.width or len(ys) != grid.height
            or xs.min() < 0 or ys.min() < 0 or xs.max() >= image.width or ys.max() >= image.height):
        raise PipelineError("The sampling grid extends outside the source image.")
    rgba = np.asarray(image.convert("RGBA"))
    return Image.fromarray(rgba[ys[:, None], xs[None, :]])
