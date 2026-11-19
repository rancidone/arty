import json
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from art_pipeline.cli import main


@pytest.fixture
def source(tmp_path):
    original = Image.fromarray(np.random.default_rng(4).integers(0, 256, (24, 32, 4), dtype=np.uint8))
    path = tmp_path / "input.png"
    original.resize((192, 144), Image.Resampling.NEAREST).save(path)
    return path, original


def test_convert_full_workflow(source, tmp_path):
    path, original = source
    output = tmp_path / "export" / "sheet.png"
    preview = tmp_path / "preview.png"
    frames = tmp_path / "frames"
    assert main(["convert", str(path), "-o", str(output), "--columns", "4", "--rows", "3",
                 "--padding", "1", "--preview", str(preview), "--frames-dir", str(frames)]) == 0
    metadata = json.loads(output.with_suffix(".json").read_text())
    assert metadata["meta"]["image"] == "sheet.png"
    assert metadata["meta"]["recovery"]["sampling"]["scale_x"] == 6
    assert metadata["meta"]["size"] == {"w": 40, "h": 30}
    assert Image.open(preview).format == "PNG"
    assert len(list(frames.glob("*.png"))) == 12
    for index in range(12):
        x, y = index % 4 * 8, index // 4 * 8
        with Image.open(frames / f"frame_{index:04d}.png") as frame:
            np.testing.assert_array_equal(frame, original.crop((x, y, x + 8, y + 8)))


def test_analyze_json(source, capsys):
    assert main(["analyze", str(source[0]), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["detection"]["reliable"]
    assert report["detection"]["backendVersion"] == "0.1.4"
    assert report["sampling"]["geometry"] == "refined"
    assert len(report["sampling"]["x_edges"]) == 33
    assert report["sampling"]["width"] == 32


def test_blank_analyze_is_valid_json_and_does_not_invent_a_grid(tmp_path, capsys):
    path = tmp_path / "blank.png"
    Image.new("RGBA", (80, 60), "red").save(path)
    assert main(["analyze", str(path), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["sampling"] is None
    assert not report["detection"]["reliable"]
    assert report["detection"]["error"]


@pytest.mark.parametrize("options", [["--scale", "6"], ["--size", "32x24"]])
def test_explicit_controls_bypass_backend(source, tmp_path, monkeypatch, capsys, options):
    def should_not_run(*args, **kwargs):
        raise AssertionError("Explicit controls must not invoke detection")

    monkeypatch.setattr("art_pipeline.cli.detect_grid", should_not_run)
    output = tmp_path / "out.png"
    assert main(["convert", str(source[0]), "-o", str(output), *options]) == 0
    np.testing.assert_array_equal(Image.open(output), source[1])
    report = json.loads(output.with_suffix(".json").read_text())["meta"]["recovery"]
    assert report["detection"]["attempted"] is False
    assert report["detection"]["reliable"] is None
    assert "Warning:" not in capsys.readouterr().err


def test_force_does_not_invent_a_grid_on_backend_failure(tmp_path):
    path, output = tmp_path / "blank.png", tmp_path / "out.png"
    Image.new("RGBA", (80, 60)).save(path)
    assert main(["convert", str(path), "-o", str(output), "--force"]) == 2
    assert not output.exists()


def test_uncertain_auto_fails_before_writing(tmp_path, capsys):
    path = tmp_path / "blank.png"
    Image.new("RGBA", (80, 60), "red").save(path)
    out = tmp_path / "out.png"
    assert main(["convert", str(path), "-o", str(out)]) == 2
    assert "--scale or --size" in capsys.readouterr().err
    assert not out.exists()
    assert main(["convert", str(path), "-o", str(out), "--size", "16x12"]) == 0
    assert Image.open(out).size == (16, 12)


def test_crop_and_explicit_scale(source, tmp_path):
    path, original = source
    output = tmp_path / "crop.png"
    assert main(["convert", str(path), "-o", str(output), "--crop", "48,48,96,48",
                 "--scale", "6"]) == 0
    np.testing.assert_array_equal(Image.open(output), original.crop((8, 8, 24, 16)))


def test_existing_output_and_source_are_protected(source, tmp_path):
    path, _ = source
    contents = path.read_bytes()
    assert main(["convert", str(path), "-o", str(path), "--overwrite"]) == 2
    assert path.read_bytes() == contents
    out = tmp_path / "out.png"
    sidecar = out.with_suffix(".json")
    sidecar.write_text("leave me alone")
    assert main(["convert", str(path), "-o", str(out)]) == 2
    assert not out.exists()
    assert sidecar.read_text() == "leave me alone"
    assert main(["convert", str(path), "-o", str(out), "--overwrite"]) == 0


def test_preview_collision_is_rejected(source, tmp_path):
    out = tmp_path / "out.png"
    assert main(["convert", str(source[0]), "-o", str(out), "--preview", str(out)]) == 2
    assert not out.exists()


def test_jpeg_samples_actual_decoded_colors_without_palette_changes(tmp_path):
    path, output = tmp_path / "input.jpg", tmp_path / "out.png"
    rng = np.random.default_rng(8)
    image = Image.fromarray(rng.integers(0, 256, (16, 24, 3), dtype=np.uint8))
    image.resize((144, 96), Image.Resampling.NEAREST).save(path, quality=90)
    with Image.open(path) as decoded:
        expected = np.asarray(decoded.convert("RGBA"))[3::6, 3::6]
    assert main(["convert", str(path), "-o", str(output), "--scale", "6"]) == 0
    np.testing.assert_array_equal(Image.open(output), expected)


def test_paletted_png_preserves_transparency(tmp_path):
    path, output = tmp_path / "palette.png", tmp_path / "out.png"
    image = Image.new("P", (12, 8))
    image.putpalette([11, 22, 33, 44, 55, 66] + [0] * (768 - 6))
    image.paste(1, (4, 0, 12, 8))
    image.info["transparency"] = 0
    image.save(path)
    assert main(["convert", str(path), "-o", str(output), "--scale", "2"]) == 0
    with Image.open(path) as decoded:
        np.testing.assert_array_equal(Image.open(output), np.asarray(decoded.convert("RGBA"))[1::2, 1::2])


@pytest.mark.parametrize("options", [
    ["--columns", "5"], ["--columns", "0"], ["--padding", "-1"], ["--size", "0x8"],
    ["--scale", "0"], ["--size", "32x24", "--offset", "1,1"],
    ["--frame-size", "8x8", "--rows", "3"], ["--crop", "0,0,999,99"],
    ["--max-scale", "nan"], ["--min-confidence", "1.5"],
    ["--refine-intensity", "0.8"], ["--refine-intensity", "nan"], ["--offset", "1,1"],
])
def test_bad_options_do_not_write(source, tmp_path, options):
    output = tmp_path / "out.png"
    assert main(["convert", str(source[0]), "-o", str(output), *options]) == 2
    assert not output.exists()


def test_module_entrypoint():
    result = subprocess.run([sys.executable, "-m", "art_pipeline", "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "analyze" in result.stdout and "convert" in result.stdout

