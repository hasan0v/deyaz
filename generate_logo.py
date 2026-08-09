"""Generate Dikte's Windows application icon in PNG and multi-size ICO formats."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "dikte-logo.png"
ICO_PATH = ASSETS / "dikte.ico"
CANVAS = 1024


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (42, 42, size - 42, size - 42), radius=radius, fill=255
    )
    return mask


def make_logo():
    """Create a crisp microphone mark with transparent rounded corners."""
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    mask = rounded_mask(CANVAS, 224)

    # Subtle mineral-black vertical gradient.
    background = Image.new("RGBA", image.size)
    pixels = background.load()
    for y in range(CANVAS):
        blend = y / (CANVAS - 1)
        colour = (
            int(27 - 12 * blend),
            int(40 - 17 * blend),
            int(41 - 17 * blend),
            255,
        )
        for x in range(CANVAS):
            pixels[x, y] = colour
    image.alpha_composite(Image.composite(background, Image.new("RGBA", image.size), mask))

    # A restrained glow gives the mark depth without losing small-size clarity.
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((190, 190, 834, 834), fill=(242, 100, 64, 100))
    glow = glow.filter(ImageFilter.GaussianBlur(76))
    image.alpha_composite(glow)

    draw = ImageDraw.Draw(image)
    coral = (242, 100, 64, 255)
    warm = (255, 131, 91, 255)
    ink = (18, 23, 24, 255)
    cream = (246, 235, 226, 255)

    # Concentric signal rings are also the app's recording-state visual language.
    draw.ellipse((166, 166, 858, 858), outline=(65, 87, 84, 255), width=34)
    draw.ellipse((220, 220, 804, 804), outline=(242, 100, 64, 150), width=30)
    draw.ellipse((280, 280, 744, 744), fill=coral)

    # Tiny highlight on the coral disc.
    draw.arc((294, 294, 730, 730), 205, 328, fill=warm, width=28)

    # Bold microphone silhouette: intentionally simple for 16px taskbar rendering.
    draw.rounded_rectangle((445, 365, 579, 570), radius=67, fill=ink)
    draw.arc((390, 450, 634, 665), 0, 180, fill=ink, width=38)
    draw.rounded_rectangle((493, 635, 531, 715), radius=19, fill=ink)
    draw.rounded_rectangle((425, 695, 599, 733), radius=19, fill=ink)

    # One small light aperture keeps the mark identifiable on dark taskbars.
    draw.rounded_rectangle((477, 397, 547, 493), radius=35, fill=cream)
    return image


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    logo = make_logo()
    logo.save(PNG_PATH, "PNG", optimize=True)
    logo.resize((256, 256), Image.Resampling.LANCZOS).save(
        ICO_PATH,
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
               (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(PNG_PATH)
    print(ICO_PATH)


if __name__ == "__main__":
    main()
