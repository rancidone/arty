"""Backend boundary, failure handling, and full-size regression checks."""

import numpy as np
import pytest
from PIL import Image

from art_pipeline import core


def test_1080p_grid_recovers_known_original():
    palette = np.array([[20, 30, 40, 0], [205, 75, 65, 127],
                        [255, 210, 100, 255], [75, 155, 110, 255]], dtype=np.uint8)
    original = Image.fromarray(palette[np.random.default_rng(3).integers(0, 4, (54, 96))])
    enlarged = original.resize((1920, 1080), Image.Resampling.NEAREST)
    result = core.detect_grid(enlarged)
    assert result.reliable
    np.testing.assert_array_equal(core.sample(enlarged, result.grid), original)


@pytest.mark.parametrize("response", [None, (None, None), (0, 8), (999, 8), (float("nan"), 8)])
def test_backend_failure_returns_actionable_report(monkeypatch, response):
    image = Image.fromarray(np.random.default_rng(2).integers(0, 256, (80, 96, 4), dtype=np.uint8))
    monkeypatch.setattr(core._backend, "detect_grid_scale", lambda image: response)
    result = core.detect_grid(image)
    assert result.grid is None
    assert not result.reliable
    assert "Perfect Pixel" in result.error


@pytest.mark.parametrize("error", [TypeError("cannot unpack"), IndexError("out of bounds"),
                                   ValueError("bad grid"), ZeroDivisionError()])
def test_backend_exceptions_do_not_escape(monkeypatch, error):
    def fail(image):
        raise error

    monkeypatch.setattr(core._backend, "detect_grid_scale", fail)
    result = core.detect_grid(Image.fromarray(np.random.default_rng(1).integers(0, 256, (80, 96, 4), dtype=np.uint8)))
    assert result.grid is None
    assert type(error).__name__ in result.error


@pytest.mark.parametrize("edges", [[0, 5, 5, 12], [0, float("nan"), 12], [0],
                                  [-8, -4, 4], [0, 200, 400]])
def test_malformed_refined_boundaries_are_rejected(monkeypatch, edges):
    monkeypatch.setattr(core._backend, "detect_grid_scale", lambda image: (8, 8))
    monkeypatch.setattr(core._backend, "refine_grids", lambda *args: (edges, [0, 10, 20]))
    result = core.detect_grid(Image.fromarray(np.random.default_rng(1).integers(0, 256, (80, 96, 4), dtype=np.uint8)))
    assert not result.reliable
    assert result.grid is None


def test_preview_geometry_matches_backend_and_hidden_rgb_is_ignored(monkeypatch):
    received = []

    def detect(image):
        print("Upstream progress")
        received.append(image.copy())
        return 3, 2

    monkeypatch.setattr(core._backend, "detect_grid_scale", detect)
    monkeypatch.setattr(core._backend, "refine_grids", lambda *args: ([0, 4, 12, 24], [0, 6, 18]))
    source = np.random.default_rng(1).integers(0, 256, (18, 24, 4), dtype=np.uint8)
    source[:6, :, 3] = 0
    first = core.detect_grid(Image.fromarray(source))
    source[:6, :, :3] = 244
    second = core.detect_grid(Image.fromarray(source))
    np.testing.assert_array_equal(received[0], received[1])
    assert first.grid == second.grid
    assert first.grid.x_edges == (0, 4, 12, 24)
    assert first.grid.to_dict()["geometry"] == "refined"

