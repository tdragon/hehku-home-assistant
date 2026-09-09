"""Generate original placeholder brand icons for the integration."""

from pathlib import Path

from PIL import Image, ImageDraw


def create_icon(size: int, output: Path) -> None:
    """Draw a simple blue energy glyph with transparent rounded corners."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = size // 16
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size // 5,
        fill=(17, 94, 163, 255),
    )
    points = [
        (size * 0.56, size * 0.17),
        (size * 0.29, size * 0.56),
        (size * 0.48, size * 0.56),
        (size * 0.39, size * 0.84),
        (size * 0.72, size * 0.43),
        (size * 0.52, size * 0.43),
    ]
    draw.polygon(points, fill=(255, 255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)


if __name__ == "__main__":
    base = Path(__file__).parents[1] / "custom_components" / "hehku_energy" / "brand"
    create_icon(256, base / "icon.png")
    create_icon(512, base / "icon@2x.png")
