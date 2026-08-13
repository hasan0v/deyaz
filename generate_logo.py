"""Generate DeYaz's deterministic neo-brutalist desktop logo assets."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "deyaz-logo.png"
ICO_PATH = ASSETS / "deyaz.ico"
ICNS_PATH = ASSETS / "deyaz.icns"
SVG_PATH = ASSETS / "deyaz-logo.svg"

SIZE = 1024
INK = "#202321"
CREAM = "#FFF9ED"
CORAL = "#FFB6BE"
MINT = "#A9ECB8"


def make_logo():
    """Draw at 2x resolution so small taskbar sizes keep a clean silhouette."""
    scale = 2
    image = Image.new("RGBA", (SIZE * scale, SIZE * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(coords, radius, fill, outline=None, width=1):
        draw.rounded_rectangle(
            tuple(value * scale for value in coords), radius=radius * scale,
            fill=fill, outline=outline, width=width * scale,
        )

    # Slightly offset mint backing gives the hand-built pastel system depth.
    box((91, 106, 933, 948), 190, INK)
    box((76, 73, 929, 926), 190, MINT, INK, 34)
    box((103, 98, 902, 897), 168, CORAL, INK, 38)

    # A geometric D remains legible at 16 px and needs no bundled font.
    box((278, 260, 758, 764), 238, CREAM)
    box((420, 380, 629, 644), 112, CORAL)
    box((278, 260, 414, 764), 62, CREAM)

    # Three tiny voice bars connect the monogram to speech without obscuring D.
    for x, top, bottom in ((482, 471, 551), (520, 438, 584), (558, 482, 540)):
        draw.rounded_rectangle(
            (x * scale, top * scale, (x + 24) * scale, bottom * scale),
            radius=12 * scale, fill=INK,
        )

    return image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def svg_source():
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <rect x="91" y="106" width="842" height="842" rx="190" fill="{INK}"/>
  <rect x="76" y="73" width="853" height="853" rx="190" fill="{MINT}" stroke="{INK}" stroke-width="34"/>
  <rect x="103" y="98" width="799" height="799" rx="168" fill="{CORAL}" stroke="{INK}" stroke-width="38"/>
  <rect x="278" y="260" width="480" height="504" rx="238" fill="{CREAM}"/>
  <rect x="420" y="380" width="209" height="264" rx="112" fill="{CORAL}"/>
  <rect x="278" y="260" width="136" height="504" rx="62" fill="{CREAM}"/>
  <rect x="482" y="471" width="24" height="80" rx="12" fill="{INK}"/>
  <rect x="520" y="438" width="24" height="146" rx="12" fill="{INK}"/>
  <rect x="558" y="482" width="24" height="58" rx="12" fill="{INK}"/>
</svg>'''


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    logo = make_logo()
    logo.save(PNG_PATH, "PNG", optimize=True)
    logo.save(
        ICO_PATH, format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
               (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    logo.save(ICNS_PATH, format="ICNS")
    SVG_PATH.write_text(svg_source(), encoding="utf-8")
    for path in (PNG_PATH, ICO_PATH, ICNS_PATH, SVG_PATH):
        print(path)


if __name__ == "__main__":
    main()
