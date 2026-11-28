"""Draws the Hidoceptium logo: an original cartoon flame fruit on a transparent background.

Usage: python3 tools/make_hido_logo.py   -> assets/hido/logo.png, logo_96.png, logo.ico
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 1024
OUT = Path(__file__).resolve().parent.parent / "assets" / "hido"
INK = (52, 16, 6, 255)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def curve(points, steps=20):
    """Smooth closed Catmull-Rom curve through `points`."""
    out, n = [], len(points)
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in range(2)))
    return out


def outlined(img, pts, fill, width=26):
    """Shape with a thick dark outline, cartoon style."""
    d = ImageDraw.Draw(img)
    d.polygon(pts, fill=INK)
    inner = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(inner).polygon(pts, fill=fill)
    mask = inner.split()[3].filter(ImageFilter.MinFilter(width | 1))
    img.paste(inner, (0, 0), mask)


def background():
    img = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(img)
    for y in range(S):
        d.line([(0, y), (S, y)], fill=lerp((18, 82, 130, 255), (5, 22, 40, 255), y / S))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([120, 120, 900, 900], fill=(255, 140, 30, 110))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(110)))
    waves = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    wd = ImageDraw.Draw(waves)
    for base, amp, col in ((880, 20, (36, 128, 190, 180)), (930, 15, (22, 98, 156, 230))):
        pts = [(x, base + amp * math.sin(x / S * 2 * math.pi * 1.7)) for x in range(0, S + 8, 8)]
        wd.polygon(pts + [(S, S), (0, S)], fill=col)
    img.alpha_composite(waves)
    return img


def flame_ring(cx, cy, r, spikes, height, phase, squash=1.0):
    """Points of a spiky fire ring around a circle; taller on top, short at the bottom."""
    pts = []
    n = spikes * 2
    for i in range(n):
        a = -math.pi / 2 + phase + i / n * 2 * math.pi
        up = max(0.0, -math.sin(a))  # 1 at the top, 0 below the middle
        reach = r + (height * (0.35 + 0.65 * up) if i % 2 == 0 else height * 0.12)
        # flames lean upwards
        pts.append((cx + math.cos(a) * reach, cy + math.sin(a) * reach * squash - (height * 0.5 * up if i % 2 == 0 else 0)))
    return curve(pts, 6)


def flames(img):
    cx, cy, r = 512, 600, 300
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(flame_ring(cx, cy, r, 11, 210, 0.0), fill=INK)
    d.polygon(flame_ring(cx, cy, r - 14, 11, 190, 0.0), fill=(225, 45, 15, 255))
    d.polygon(flame_ring(cx, cy, r - 20, 11, 140, 0.15), fill=(255, 128, 20, 255))
    d.polygon(flame_ring(cx, cy, r - 24, 11, 80, 0.3), fill=(255, 214, 70, 255))
    img.alpha_composite(layer)


def fruit(img):
    cx, cy, r = 512, 600, 300
    body = curve([(cx, cy - r), (cx + r * 0.78, cy - r * 0.66), (cx + r, cy), (cx + r * 0.74, cy + r * 0.72), (cx, cy + r * 0.95),
                  (cx - r * 0.74, cy + r * 0.72), (cx - r, cy), (cx - r * 0.78, cy - r * 0.66)], 16)
    shade = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for i in range(r, 0, -3):
        t = 1 - i / r
        sd.ellipse([cx - i - 40 * t, cy - i - 60 * t, cx + i - 40 * t, cy + i - 60 * t], fill=lerp((214, 88, 10, 255), (255, 190, 60, 255), min(1, t * 1.6)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(body, fill=255)
    outlined(img, body, (230, 110, 20, 255))
    img.paste(shade, (0, 0), mask.filter(ImageFilter.MinFilter(27)))
    d = ImageDraw.Draw(img)
    # swirl grooves
    for k in range(3):
        start = k * 2 * math.pi / 3 + 1.9
        pts = []
        for i in range(90):
            f = i / 89
            a = start + f * 2 * math.pi * 0.62
            rr = r * (0.55 + 0.33 * f)
            pts.append((cx + math.cos(a) * rr, cy + 40 + math.sin(a) * rr * 0.85))
        d.line(pts, fill=(160, 54, 6, 230), width=22, joint="curve")
    # leaf and stem
    stem = [(cx - 18, cy - r + 10), (cx - 30, cy - r - 80), (cx + 14, cy - r - 86), (cx + 22, cy - r + 10)]
    outlined(img, stem, (110, 70, 30, 255), 18)
    leaf = curve([(cx + 10, cy - r - 70), (cx + 120, cy - r - 170), (cx + 250, cy - r - 150), (cx + 170, cy - r - 60)], 12)
    outlined(img, leaf, (80, 200, 90, 255), 20)
    d.line([(cx + 30, cy - r - 82), (cx + 200, cy - r - 130)], fill=(40, 140, 60, 255), width=10)
    # big angry glowing eyes
    for side in (-1, 1):
        ex = cx + side * 120
        eye = [(ex - 95 * side, cy - 30), (ex + 85 * side, cy - 110), (ex + 95 * side, cy + 15), (ex + 10 * side, cy + 70), (ex - 85 * side, cy + 35)]
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(glow).polygon(curve(eye, 8), fill=(255, 250, 200, 200))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14)))
        outlined(img, curve(eye, 8), (255, 255, 246, 255), 24)
    # wide jagged grin
    top, bottom = cy + 120, cy + 250
    mouth = [(cx - 200, top - 10)]
    for i in range(1, 9):
        x = cx - 200 + i * 50
        mouth.append((x, top + (34 if i % 2 else 0)))
    mouth += [(cx + 160, bottom - 40), (cx + 60, bottom), (cx - 60, bottom), (cx - 160, bottom - 40)]
    outlined(img, mouth, (255, 150, 40, 255), 22)
    inner = [(cx - 150, top + 40), (cx + 150, top + 40), (cx + 110, bottom - 40), (cx, bottom - 22), (cx - 110, bottom - 40)]
    ImageDraw.Draw(img).polygon(inner, fill=(110, 18, 4, 255))
    # highlight
    hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse([cx - 230, cy - 230, cx - 130, cy - 160], fill=(255, 255, 255, 140))
    img.alpha_composite(hl.filter(ImageFilter.GaussianBlur(8)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))  # transparent: just the fruit, no tile
    flames(img)
    fruit(img)
    # fit the drawing to the square with a small margin
    box = img.getbbox()
    if box:
        crop = img.crop(box)
        side = int(max(crop.size) * 1.04)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.alpha_composite(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
        img = square.resize((S, S), Image.LANCZOS)
    big = img.resize((256, 256), Image.LANCZOS)
    big.save(OUT / "logo.png")
    img.resize((96, 96), Image.LANCZOS).save(OUT / "logo_96.png")
    big.save(OUT / "logo.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
