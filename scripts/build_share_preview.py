"""Render the versioned GIPS share artwork locally, without network or report data."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, SCALE = 1200, 630, 2
PAPER = "#f5f3ee"
INK = "#17202b"
MUTED = "#536071"
BLUE = "#2855e8"


def font(size, weight=600):
    face = ImageFont.truetype(str(ROOT / "design/fonts/Inter.ttf"), size * SCALE)
    face.set_variation_by_axes([32, weight])
    return face


def render(destination):
    image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), PAPER)
    draw = ImageDraw.Draw(image)

    def rect(bounds, fill):
        draw.rectangle(tuple(round(value * SCALE) for value in bounds), fill=fill)

    def text(position, value, size, weight=600, color=INK):
        point = tuple(value * SCALE for value in position)
        face = font(size, weight)
        bounds = draw.textbbox(point, value, font=face, anchor="lt")
        if min(bounds[:2]) < 0 or bounds[2] > WIDTH * SCALE - 38 * SCALE or bounds[3] > HEIGHT * SCALE - 24 * SCALE:
            raise ValueError(f"Share image text exceeds its safe area: {value}")
        draw.text(point, value, font=face, fill=color, anchor="lt")

    # The editorial identity stays separate from CleanApp's public report hub.
    rect((768, 0, WIDTH, HEIGHT), BLUE)
    text((60, 80), "GIPS", 174, 760)
    text((64, 303), "Grok Improvement", 53, 620)
    text((64, 367), "Proposals", 53, 620)
    rect((64, 475, 132, 479), BLUE)
    text((64, 509), "Independent project", 27, 550, MUTED)
    text((64, 566), "borisolver.github.io/gips", 24, 450, MUTED)

    text((808, 74), "FROM REPORT TO REPAIR", 18, 650, PAPER)
    for y in (185, 296, 407):
        draw.ellipse((808 * SCALE, (y - 6) * SCALE, 820 * SCALE, (y + 6) * SCALE), fill=PAPER)
    draw.line((814 * SCALE, 209 * SCALE, 814 * SCALE, 272 * SCALE), fill="#a9baff", width=2 * SCALE)
    draw.line((814 * SCALE, 320 * SCALE, 814 * SCALE, 383 * SCALE), fill="#a9baff", width=2 * SCALE)
    text((849, 165), "Evidence", 39, 600, PAPER)
    text((849, 276), "Proposals", 39, 600, PAPER)
    text((849, 387), "Testable", 38, 600, PAPER)
    text((849, 434), "outcomes", 38, 600, PAPER)

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(destination, format="PNG", optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "share/gips-v1.png")
    args = parser.parse_args()
    render(args.output)
    print(f"Rendered {WIDTH}x{HEIGHT}: {args.output}")
