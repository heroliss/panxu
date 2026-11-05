"""Draw assets/panxu.ico: a cobalt rounded square with three white bars, longest first (盘序: disk space, in order).

Each size is drawn on its own (at 8x, then reduced) so the bars stay crisp at 16 px. Needs Pillow, only for
this tool: python -m pip install pillow; python tools/make_icon.py
"""
import io
import os
import struct

from PIL import Image, ImageDraw

COBALT = (43, 79, 216, 255)  # --cobalt in web/app.css
WHITE = (255, 255, 255, 255)
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'panxu.ico')


def draw(size: int) -> Image.Image:
    k = 8
    s = size * k
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.22), fill=COBALT)
    left = s * 0.22
    full = s * 0.56
    bar = s * 0.12
    gap = s * 0.085
    top = (s - (3 * bar + 2 * gap)) / 2
    for i, share in enumerate((1.0, 0.72, 0.44)):
        y = top + i * (bar + gap)
        d.rounded_rectangle((left, y, left + full * share, y + bar), radius=bar / 2, fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    images = []
    for size in SIZES:
        buf = io.BytesIO()
        draw(size).save(buf, 'PNG')
        images.append((size, buf.getvalue()))
    # ICO with PNG entries (Windows Vista and later), one entry per size.
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b'', b''
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    with open(OUT, 'wb') as fh:
        fh.write(header + entries + data)
    print('wrote', OUT, len(images), 'sizes')


if __name__ == '__main__':
    main()
