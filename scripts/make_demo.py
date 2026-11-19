"""Create a known-answer pixel grid to exercise the CLI without an image service."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "examples"
    destination.mkdir(exist_ok=True)
    sheet = Image.new("RGBA", (96, 64))
    for index, squash in enumerate((0, 1, 2, 1, 0, -1)):
        frame = Image.new("RGBA", (32, 32))
        draw = ImageDraw.Draw(frame)
        width, height = 20 + squash * 2, 18 - squash * 2
        x, y = (32 - width) // 2, 27 - height
        outline, dark, green, light = "#172d2a", "#27644b", "#57ad61", "#b8dd75"
        draw.rectangle((x + 4, y, x + width - 5, y + height - 1), fill=outline)
        draw.rectangle((x + 2, y + 2, x + width - 3, y + height - 1), fill=outline)
        draw.rectangle((x, y + 6, x + width - 1, y + height - 3), fill=outline)
        draw.rectangle((x + 3, y + 3, x + width - 4, y + height - 3), fill=green)
        draw.rectangle((x + 1, y + 7, x + width - 2, y + height - 4), fill=green)
        draw.rectangle((x + 3, y + height - 5, x + width - 4, y + height - 2), fill=dark)
        draw.rectangle((x + 5, y + 3, x + 8, y + 4), fill=light)
        draw.rectangle((x + 3, y + 5, x + 4, y + 6), fill=light)
        eye_y = y + max(7, height // 2)
        for eye_x in (x + width // 3, x + width * 2 // 3):
            draw.rectangle((eye_x, eye_y, eye_x + 1, eye_y + 2), fill=outline)
        draw.point((x + width // 2, eye_y + 3), fill=outline)
        sheet.paste(frame, (index % 3 * 32, index // 3 * 32))
    sheet.save(destination / "slime-original.png")
    sheet.resize((1152, 768), Image.Resampling.NEAREST).save(destination / "slime-enlarged.png")
    print(f"Wrote known-answer demo inputs to {destination}")


if __name__ == "__main__":
    main()

