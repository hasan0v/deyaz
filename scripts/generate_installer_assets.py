"""Generate Windows installer artwork from the canonical DeYaz logo."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def font(size: int, bold: bool = False):
    names = (
        "C:/Windows/Fonts/segoeuib.ttf" if bold else
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    for name in names:
        path = Path(name)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def contain_logo(size: int) -> Image.Image:
    logo = Image.open(ASSETS / "deyaz-logo.png").convert("RGBA")
    logo.thumbnail((size, size), Image.Resampling.LANCZOS)
    return logo


def build_wizard() -> None:
    width, height = 164, 314
    image = Image.new("RGB", (width, height), "#fffdf3")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((10, 12, 154, 302), 18, fill="#fff4ef", outline="#202422", width=2)
    draw.rounded_rectangle((20, 22, 144, 146), 20, fill="#ffb7c1")
    logo = contain_logo(88)
    image.paste(logo, (38, 40), logo)
    draw.text((22, 164), "DeYaz", font=font(25, True), fill="#202422")
    draw.text((22, 198), "Voice to text,", font=font(12), fill="#4d5350")
    draw.text((22, 216), "made simple.", font=font(12), fill="#4d5350")
    draw.rounded_rectangle((22, 252, 142, 278), 9, fill="#a9eab9", outline="#202422", width=1)
    draw.text((34, 259), "Ali Hasanov", font=font(11, True), fill="#202422")
    image.save(ASSETS / "installer-wizard.bmp")


def build_small() -> None:
    image = Image.new("RGB", (55, 55), "#fffdf3")
    logo = contain_logo(49)
    image.paste(logo, (3, 3), logo)
    image.save(ASSETS / "installer-small.bmp")


if __name__ == "__main__":
    build_wizard()
    build_small()
