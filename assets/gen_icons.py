"""Generate app icons from assets/icon.png (1024×1024 RGBA master).

Requires: pillow  (pip install pillow)

Outputs:
  icon.iconset/   — all macOS sizes
  icon.icns       — macOS bundle icon (macOS only, via iconutil)
  icon.ico        — Windows multi-size icon
  tray.png        — 44×44 tray icon (black vinyl+magnifier on transparent)
"""
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
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

# ── tray.png — vinyl + magnifier, black on transparent (macOS template image) ──
# Rendered at 44×44 (@2x). Coordinates are in 22px logical units, scaled ×2.

def _make_tray(px: int = 44) -> "Image.Image":
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    sc = px / 22  # scale from 22px logical reference

    BK  = (0, 0, 0, 255)
    BKg = (0, 0, 0, 100)

    def _circ(cx, cy, r, fill=None, outline=None, lw=1):
        cx, cy, r = cx * sc, cy * sc, r * sc
        lw = max(1, round(lw * sc))
        d.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                  fill=fill, outline=outline, width=lw)

    # Vinyl record (center-left)
    _circ(9.5, 12.5, 7.5, outline=BK, lw=1.2)
    _circ(9.5, 12.5, 5.8, outline=BKg, lw=0.8)
    _circ(9.5, 12.5, 4.0, outline=BKg, lw=0.8)
    _circ(9.5, 12.5, 2.2, fill=BK)

    # Magnifying glass (upper-right, overlays vinyl)
    mx, my, mr = 16.5, 6.0, 3.4
    _circ(mx, my, mr, outline=BK, lw=1.3)
    x1 = (mx + mr * 0.707) * sc
    y1 = (my + mr * 0.707) * sc
    x2, y2 = 20.8 * sc, 10.5 * sc
    d.line([(x1, y1), (x2, y2)], fill=BK, width=max(1, round(1.8 * sc)))

    return img

_make_tray(44).save(here / "tray.png")
print("  tray.png (44×44 RGBA)")

print("Done.")
