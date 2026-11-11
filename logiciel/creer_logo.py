"""Dessine le logo de Frétille (fretille.png et fretille.ico) : un poisson dans une bulle."""
from pathlib import Path

from PIL import Image, ImageDraw

ICI = Path(__file__).resolve().parent


def logo(t=512):
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, t - 8, t - 8], fill=(19, 34, 42, 255), outline=(60, 198, 192, 255), width=t // 28)
    # vagues
    for k, y in enumerate((0.70, 0.79)):
        pts = [(x, t * y + t * 0.02 * ((x // (t // 8)) % 2 * 2 - 1)) for x in range(t // 6, t * 5 // 6 + 1, t // 16)]
        d.line(pts, fill=(60, 198, 192, 150 - 50 * k), width=t // 40, joint="curve")
    # corps du poisson
    cx, cy = t * 0.46, t * 0.44
    d.ellipse([cx - t * 0.22, cy - t * 0.12, cx + t * 0.18, cy + t * 0.12], fill=(240, 180, 76, 255))
    d.polygon([(cx + t * 0.14, cy), (cx + t * 0.33, cy - t * 0.13), (cx + t * 0.33, cy + t * 0.13)], fill=(240, 150, 60, 255))
    d.polygon([(cx - t * 0.04, cy - t * 0.11), (cx + t * 0.06, cy - t * 0.21), (cx + t * 0.10, cy - t * 0.09)],
              fill=(240, 150, 60, 255))
    d.ellipse([cx - t * 0.15, cy - t * 0.04, cx - t * 0.09, cy + t * 0.02], fill=(19, 34, 42, 255))
    # bulles
    for bx, by, r in ((0.24, 0.27, 0.035), (0.18, 0.18, 0.025), (0.27, 0.12, 0.018)):
        d.ellipse([t * (bx - r), t * (by - r), t * (bx + r), t * (by + r)], outline=(230, 241, 243, 220), width=t // 90 + 1)
    return img


if __name__ == "__main__":
    image = logo()
    image.resize((256, 256), Image.LANCZOS).save(ICI / "fretille.png")
    image.save(ICI / "fretille.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("logo écrit")
