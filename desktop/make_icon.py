"""Generate faultiva.ico from the waveform mark.

The mark is the project's own idea drawn literally: a signal that toggles twice
and then goes flat and red - a stuck-at fault.  Written with struct rather than
Pillow so the build needs no extra dependency.

Produces a real multi-size ICO (16/24/32/48/64/128/256) of 32-bit BGRA PNGs,
which is what Windows and Inno Setup expect.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "ui" / "faultiva.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)

BG = (0x0F, 0x12, 0x16, 0xFF)      # --paper dark
INK = (0xF2, 0xF5, 0xF8, 0xFF)     # --ink dark
ACC = (0x6B, 0x9B, 0xFF, 0xFF)     # --acc dark
BAD = (0xFF, 0x6B, 0x5E, 0xFF)     # --bad dark


def _blend(dst, src, alpha):
    """Composite src over dst with coverage alpha in 0..1."""
    return tuple(int(round(d + (s - d) * alpha)) for d, s in zip(dst, src))


def _draw(size: int) -> bytes:
    """Render the mark as raw RGBA rows."""
    px = [[BG for _ in range(size)] for _ in range(size)]
    s = size / 32.0                      # design on a 32x32 grid

    stroke = max(1.6, 2.4 * s)
    half = stroke / 2.0

    # waveform geometry on the 32-grid:
    #   low 22 -> high 11 -> low 22, then flat (stuck) to the right edge
    lo, hi = 22.0 * s, 11.0 * s
    x0, x1, x2, x3, x4 = 3.0 * s, 9.0 * s, 15.0 * s, 21.0 * s, 29.0 * s

    segments = [
        # (x_start, y_start, x_end, y_end, colour)
        (x0, lo, x1, lo, INK),       # low
        (x1, lo, x1, hi, INK),       # rising edge
        (x1, hi, x2, hi, INK),       # high
        (x2, hi, x2, lo, INK),       # falling edge
        (x2, lo, x3, lo, INK),       # low again
        (x3, lo, x4, lo, BAD),       # stuck: flat, red
    ]

    def cover(cx, cy, seg):
        """Coverage of pixel centre (cx, cy) by a thick segment."""
        ax, ay, bx, by, _ = seg
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        if length2 == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((cx - ax) * dx + (cy - ay) * dy) / length2))
        px_, py_ = ax + t * dx, ay + t * dy
        dist = ((cx - px_) ** 2 + (cy - py_) ** 2) ** 0.5
        # antialias across one pixel at the edge
        return max(0.0, min(1.0, (half + 0.5) - dist))

    for y in range(size):
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            for seg in segments:
                a = cover(cx, cy, seg)
                if a > 0:
                    px[y][x] = _blend(px[y][x], seg[4], a)

    # the break point: a filled dot where switching stops
    bx_, by_ = x3, lo
    r = max(1.4, 2.6 * s)
    for y in range(size):
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            d = ((cx - bx_) ** 2 + (cy - by_) ** 2) ** 0.5
            a = max(0.0, min(1.0, (r + 0.5) - d))
            if a > 0:
                px[y][x] = _blend(px[y][x], BAD, a)

    # accent underline, so the mark reads at small sizes
    uy = 27.0 * s
    for y in range(size):
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            if x0 <= cx <= x4:
                a = max(0.0, min(1.0, (max(0.9, 1.3 * s) + 0.5) - abs(cy - uy)))
                if a > 0:
                    px[y][x] = _blend(px[y][x], ACC, a * 0.9)

    rows = bytearray()
    for y in range(size):
        rows.append(0)                        # PNG filter: none
        for x in range(size):
            rows.extend(bytes(px[y][x]))
    return bytes(rows)


def _png(size: int, raw: bytes) -> bytes:
    """Wrap raw RGBA scanlines in a PNG container."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main() -> None:
    images = [(s, _png(s, _draw(s))) for s in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = bytearray(), bytearray()
    for size, blob in images:
        dim = 0 if size >= 256 else size
        entries.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                   len(blob), offset))
        blobs.extend(blob)
        offset += len(blob)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(header + bytes(entries) + bytes(blobs))
    print(f"wrote {OUT}")
    print(f"  sizes {', '.join(str(s) for s in SIZES)}")
    print(f"  bytes {OUT.stat().st_size:,}")


if __name__ == "__main__":
    main()
