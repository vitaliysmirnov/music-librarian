"""Generate app icons from assets/icon.png (1024×1024 RGBA master).

Requires: pillow  (already a dep of cairosvg; also in requirements-dev.txt)

Outputs:
  icon.iconset/   — all macOS sizes
  icon.icns       — macOS bundle icon (macOS only, via iconutil)
  icon.ico        — Windows multi-size icon
  tray.png        — 22×22 menu-bar icon (white note, transparent bg)
"""
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: pillow not installed.  pip install pillow")
    sys.exit(1)

here = Path(__file__).parent

# ── Load master ────────────────────────────────────────────────────────────────
src_path = here / "icon.png"
if not src_path.exists():
    print(f"ERROR: {src_path} not found")
    sys.exit(1)
src = Image.open(src_path).convert("RGBA")
print(f"  source: {src.size[0]}×{src.size[1]} RGBA")

# ── macOS iconset ──────────────────────────────────────────────────────────────
iconset = here / "icon.iconset"
iconset.mkdir(exist_ok=True)

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
    src.resize((px, px), Image.LANCZOS).save(iconset / name)
    print(f"  icon.iconset/{name}")

# ── icon.icns (macOS only) ─────────────────────────────────────────────────────
if sys.platform == "darwin":
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(here / "icon.icns")],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("  icon.icns")
    else:
        print("  icon.icns FAILED:", result.stderr.strip())
else:
    print("  (skip .icns — not macOS)")

# ── icon.ico (Windows, multi-size) ────────────────────────────────────────────
ico_sizes = [16, 32, 48, 256]
imgs = [src.resize((s, s), Image.LANCZOS) for s in ico_sizes]
ico_path = here / "icon.ico"
imgs[0].save(
    ico_path,
    format="ICO",
    sizes=[(s, s) for s in ico_sizes],
    append_images=imgs[1:],
)
print("  icon.ico")

# ── tray.png — 22×22 white music note on transparent background ────────────────
# Hand-drawn bitmap: reads clearly at menu-bar size without scaling artefacts.

def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

def _write_rgba_png(path: Path, size: int, pixels: list) -> None:
    raw = b""
    for y in range(size):
        raw += b"\x00"
        for x in range(size):
            raw += bytes(pixels[y * size + x])
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(_chunk(b"IEND", b""))

_NOTE_11 = [
    "           ",
    "      ##   ",
    "      ##   ",
    "      ##   ",
    "    ####   ",
    "   #####   ",
    "   #####   ",
    "   #####   ",
    "    ###    ",
    "           ",
    "           ",
]

def _upscale2(bitmap: list) -> list:
    out = []
    for row in bitmap:
        doubled = "".join(c * 2 for c in row)
        out.append(doubled)
        out.append(doubled)
    return out

def _music_note_rgba(size: int) -> list:
    note = _upscale2(_NOTE_11)
    pixels = []
    for y in range(size):
        for x in range(size):
            nx = x * 22 // size
            ny = y * 22 // size
            if ny < len(note) and nx < len(note[ny]) and note[ny][nx] == "#":
                pixels.append((255, 255, 255, 255))
            else:
                pixels.append((0, 0, 0, 0))
    return pixels

_write_rgba_png(here / "tray.png", 22, _music_note_rgba(22))
print("  tray.png (22×22 RGBA)")

print("Done.")
