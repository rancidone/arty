import numpy as np
import pytest
from PIL import Image, ImageFilter

from art_pipeline.core import (
    Grid, PipelineError, detect_grid, grid_from_scale, grid_from_size, sample,
)
from art_pipeline.sheet import make_layout, pack_sheet


@pytest.fixture
def original():
    rng = np.random.default_rng(12)
    palette = np.array([
        [19, 31, 47, 0], [25, 30, 40, 255], [215, 78, 65, 127], [255, 215, 120, 255],
    ], dtype=np.uint8)
    return Image.fromarray(palette[rng.integers(0, len(palette), (24, 32))])


@pytest.mark.parametrize("scale_x,scale_y", [
    (4, 4), (6, 6), (8, 8), (12, 12),
])
def test_automatic_recovery_is_pixel_exact(original, scale_x, scale_y):
    source = original.resize((round(original.width * scale_x), round(original.height * scale_y)),
                             Image.Resampling.NEAREST)
    detection = detect_grid(source)
    assert detection.reliable
    assert detection.x.scale == pytest.approx(scale_x, abs=0.01)
    assert detection.y.scale == pytest.approx(scale_y, abs=0.01)
    assert detection.to_dict()["backend"] == "perfect-pixel"
    np.testing.assert_array_equal(sample(source, detection.grid), original)


def test_explicit_offset_preserves_shifted_grid(original):
    enlarged = original.resize((192, 144), Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (197, 151))
    canvas.paste(enlarged, (3, 2))
    grid = grid_from_scale(canvas.size, (6, 6), (3, 2))
    np.testing.assert_array_equal(sample(canvas, grid), original)


@pytest.mark.parametrize("scale_x,scale_y", [(2, 2), (2.25, 2.5), (5.5, 5.5), (7.25, 4.5), (60, 45)])
def test_explicit_size_preserves_fractional_and_stretched_pixels(original, scale_x, scale_y):
    source = original.resize((round(original.width * scale_x), round(original.height * scale_y)),
                             Image.Resampling.NEAREST)
    np.testing.assert_array_equal(sample(source, grid_from_size(source.size, original.size)), original)


def test_refined_cell_centers_preserve_rgba_without_uniform_resizing():
    # A drifting grid must sample each recovered cell, not resize to the cell count.
    source = Image.fromarray(np.random.default_rng(1).integers(0, 256, (18, 24, 4), dtype=np.uint8))
    grid = Grid(3, 2, 8, 9, x_edges=(0, 4, 12, 24), y_edges=(0, 6, 18))
    expected = np.asarray(source)[np.array([3, 12])[:, None], np.array([2, 8, 18])[None, :]]
    np.testing.assert_array_equal(sample(source, grid), expected)


def test_noisy_edges_still_reveal_grid(original):
    source = original.resize((192, 144), Image.Resampling.NEAREST).filter(ImageFilter.GaussianBlur(0.6))
    detection = detect_grid(source)
    assert detection.reliable
    assert (detection.x.scale, detection.y.scale) == (6, 6)
    # Preserve actual source values, including alpha: do not "repair" blurred colors.
    output = sample(source, detection.grid)
    pixels = np.asarray(source)
    xs, ys = detection.grid.boundaries()
    cx, cy = ((xs[:-1] + xs[1:]) / 2).astype(int), ((ys[:-1] + ys[1:]) / 2).astype(int)
    np.testing.assert_array_equal(output, pixels[cy[:, None], cx[None, :]])


@pytest.mark.parametrize("kind", ["flat", "noise", "rectangle", "gradient", "hidden-rgb"])
def test_non_grid_images_are_uncertain(kind):
    rng = np.random.default_rng(17)
    if kind == "flat":
        image = Image.new("RGBA", (192, 144), "red")
    elif kind == "noise":
        image = Image.fromarray(rng.integers(0, 256, (144, 192, 4), dtype=np.uint8))
    elif kind == "rectangle":
        image = Image.new("RGBA", (192, 144))
        image.paste("red", (24, 24, 168, 120))
    elif kind == "gradient":
        pixels = np.tile(np.arange(192, dtype=np.uint8), (144, 1))
        image = Image.fromarray(pixels).convert("RGBA")
    else:
        pixels = rng.integers(0, 256, (144, 192, 4), dtype=np.uint8)
        pixels[:, :, 3] = 0
        image = Image.fromarray(pixels)
    assert not detect_grid(image).reliable


@pytest.mark.parametrize("size", [(1, 1), (1, 10), (10, 1)])
def test_single_pixel_axes_are_uncertain_without_crashing(size):
    result = detect_grid(Image.new("RGBA", size, "red"))
    assert not result.reliable and result.grid is None


def test_exact_sampling_uses_source_rgba_and_center_coordinates():
    source = Image.fromarray(np.random.default_rng(0).integers(0, 256, (35, 47, 4), dtype=np.uint8))
    grid = grid_from_scale(source.size, (5.25, 4.5), (2, 1))
    output = sample(source, grid)
    expected = np.array([[source.getpixel((int(2 + (x + .5) * 5.25), int(1 + (y + .5) * 4.5)))
                          for x in range(grid.width)] for y in range(grid.height)], dtype=np.uint8)
    np.testing.assert_array_equal(output, expected)
    source_colors = set(map(tuple, np.asarray(source).reshape(-1, 4)))
    assert set(map(tuple, np.asarray(output).reshape(-1, 4))) <= source_colors


def test_size_override_and_no_upscaling(original):
    grid = grid_from_size(original.size, (13, 9))
    assert sample(original, grid).size == (13, 9)
    with pytest.raises(PipelineError, match="no larger"):
        grid_from_size(original.size, (33, 24))


@pytest.mark.parametrize("scale,offset", [((0, 2), (0, 0)), ((float("nan"), 2), (0, 0)),
                                            ((6, 6), (-1, 0)), ((6, 6), (0, 999))])
def test_invalid_grids_fail(scale, offset):
    with pytest.raises(PipelineError):
        grid_from_scale((192, 144), scale, offset)


def test_padding_and_metadata_preserve_every_frame(original):
    layout = make_layout(original.size, columns=4, rows=3, padding=2)
    output, metadata = pack_sheet(original, layout)
    assert output.size == (48, 36)
    assert len(metadata["frames"]) == 12
    for index, frame in enumerate(metadata["frames"].values()):
        r = frame["frame"]
        x, y = index % 4 * 8, index // 4 * 8
        np.testing.assert_array_equal(output.crop((r["x"], r["y"], r["x"] + 8, r["y"] + 8)),
                                      original.crop((x, y, x + 8, y + 8)))
        assert output.getpixel((r["x"] - 1, r["y"])) == (0, 0, 0, 0)


def test_invalid_frame_layout_is_not_silently_trimmed():
    with pytest.raises(PipelineError, match="not divisible"):
        make_layout((31, 24), columns=4, rows=3)
    with pytest.raises(PipelineError, match="divide"):
        make_layout((32, 24), frame_size=(7, 8))

