"""Generate placeholder app icons.

- tray.png   : 22×22 RGBA, white music note on transparent bg (macOS menu bar)
- icon.png   : 512×512 RGB, magenta/black checkerboard (app icon)
- icon.icns  : macOS bundle icon (built from icon.iconset via iconutil)
"""
import os
import struct
import zlib

# ── PNG helpers ────────────────────────────────────────────────────────────────

def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png_rgb(path: str, size: int, pixels: list):
    """Write RGB PNG (no alpha). pixels = flat list of (r,g,b) tuples."""
    raw = b""
    for y in range(size):
        raw += b"\x00"
        for x in range(size):
            r, g, b = pixels[y * size + x]
            raw += bytes([r, g, b])
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(_chunk(b"IEND", b""))


def write_png_rgba(path: str, size: int, pixels: list):
    """Write RGBA PNG. pixels = flat list of (r,g,b,a) tuples."""
    raw = b""
    for y in range(size):
        raw += b"\x00"
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            raw += bytes([r, g, b, a])
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # color type 6 = RGBA
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(_chunk(b"IEND", b""))


# ── Checkerboard (app icon) ────────────────────────────────────────────────────

MAGENTA = (255, 0, 255)
BLACK   = (0, 0, 0)
_GRID   = 8


def checkerboard_rgb(size: int) -> list:
    sq = max(1, size // _GRID)
    pixels = []
    for y in range(size):
        for x in range(size):
            cell = (x // sq + y // sq) % 2
            pixels.append(MAGENTA if cell == 0 else BLACK)
    return pixels


# ── Music-note tray icon (RGBA, white on transparent) ─────────────────────────
#
# Drawn on a 22×22 canvas using a simple bitmap:
#   • filled oval (note head)  at bottom-left
#   • vertical stem            right side of head
#   • horizontal flag          top of stem
#
# The bitmap is defined at 11×11 and upscaled ×2 to 22×22.

_NOTE_11 = [
    "           ",  # 0
    "      ##   ",  # 1
    "      ##   ",  # 2
    "      ##   ",  # 3
    "    ####   ",  # 4
    "   #####   ",  # 5
    "   #####   ",  # 6
    "   #####   ",  # 7
    "    ###    ",  # 8
    "           ",  # 9
    "           ",  # 10
]


def _upscale2(bitmap: list[str]) -> list[str]:
    result = []
    for row in bitmap:
        doubled_row = "".join(c * 2 for c in row)
        result.append(doubled_row)
        result.append(doubled_row)
    return result


def music_note_rgba(size: int) -> list:
    note = _upscale2(_NOTE_11)  # 22×22 now
    pixels = []
    for y in range(size):
        for x in range(size):
            # scale note bitmap to target size
            nx = x * 22 // size
            ny = y * 22 // size
            if ny < len(note) and nx < len(note[ny]) and note[ny][nx] == "#":
                pixels.append((255, 255, 255, 255))  # opaque white
            else:
                pixels.append((0, 0, 0, 0))          # transparent
    return pixels


# ── Generate files ─────────────────────────────────────────────────────────────

here = os.path.dirname(os.path.abspath(__file__))

# tray.png — white note on transparent background for macOS menu bar
write_png_rgba(os.path.join(here, "tray.png"), 22, music_note_rgba(22))
print("  tray.png  (22×22 RGBA)")

# iconset — checkerboard for app icon
iconset = os.path.join(here, "icon.iconset")
os.makedirs(iconset, exist_ok=True)

sizes = {
    "icon_16x16.png":        16,
    "icon_16x16@2x.png":     32,
    "icon_32x32.png":        32,
    "icon_32x32@2x.png":     64,
    "icon_128x128.png":     128,
    "icon_128x128@2x.png":  256,
    "icon_256x256.png":     256,
    "icon_256x256@2x.png":  512,
    "icon_512x512.png":     512,
    "icon_512x512@2x.png": 1024,
}
for name, px in sizes.items():
    write_png_rgb(os.path.join(iconset, name), px, checkerboard_rgb(px))
    print(f"  icon.iconset/{name}")

import shutil
shutil.copy(os.path.join(iconset, "icon_512x512.png"), os.path.join(here, "icon.png"))
print("  icon.png  (512×512 RGB)")

import sys, subprocess

# ── icon.icns (macOS only) ─────────────────────────────────────────────────────
if sys.platform == "darwin":
    result = subprocess.run(
        ["iconutil", "-c", "icns", iconset, "-o", os.path.join(here, "icon.icns")],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("  icon.icns")
    else:
        print("  icon.icns FAILED:", result.stderr.strip())
else:
    print("  (skip .icns — not macOS)")

# ── icon.ico (Windows — multi-size ICO from PNG data) ─────────────────────────

def _png_bytes(size: int) -> bytes:
    """Return raw PNG bytes for a checkerboard at the given size."""
    import io
    buf = io.BytesIO()
    pixels = checkerboard_rgb(size)
    raw = b""
    for y in range(size):
        raw += b"\x00"
        for x in range(size):
            r, g, b = pixels[y * size + x]
            raw += bytes([r, g, b])
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    buf.write(b"\x89PNG\r\n\x1a\n")
    buf.write(_chunk(b"IHDR", ihdr))
    buf.write(_chunk(b"IDAT", zlib.compress(raw, 9)))
    buf.write(_chunk(b"IEND", b""))
    return buf.getvalue()


ico_sizes = [16, 32, 48, 256]
images = [(s, _png_bytes(s)) for s in ico_sizes]
n = len(images)
# ICO header: 6 bytes; each dir entry: 16 bytes; images follow
data_offset = 6 + n * 16
ico_header = struct.pack("<HHH", 0, 1, n)  # reserved=0, type=1 (ICO), count
dir_entries = b""
img_data = b""
offset = data_offset
for size, png in images:
    entry_size = len(png)
    w = 0 if size == 256 else size   # ICO uses 0 to mean 256
    h = 0 if size == 256 else size
    dir_entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, entry_size, offset)
    img_data += png
    offset += entry_size

ico_path = os.path.join(here, "icon.ico")
with open(ico_path, "wb") as f:
    f.write(ico_header + dir_entries + img_data)
print("  icon.ico")

print("Done.")
