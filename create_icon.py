"""
create_icon.py — Generates icon.ico for PyInstaller build
Run once: python create_icon.py
Requires: pip install Pillow
"""
import pathlib, sys

try:
    from PIL import Image
except ImportError:
    print("Pillow not installed. Run: pip install Pillow")
    sys.exit(1)

# ── Try to convert the generated PNG first ───────────────────────────────────
png_src = pathlib.Path(
    r"C:\Users\Test\.gemini\antigravity\brain\37b255ef-080f-4ec5-b699-318ba7c37ffb\subtitle_icon_1780764005697.png"
)
out = pathlib.Path(__file__).parent / "icon.ico"

if png_src.exists():
    img = Image.open(png_src).convert("RGBA")
    img.save(str(out), format="ICO", sizes=[(16,16),(32,32),(48,48),(256,256)])
    print(f"icon.ico created from PNG  →  {out}")
else:
    # Fallback: draw icon programmatically
    from PIL import ImageDraw
    for sz in [256]:
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = sz // 64
        d.ellipse([4*m, 4*m, 60*m, 60*m], fill=(26, 26, 46, 255))
        d.rectangle([10*m, 24*m, 54*m, 31*m], fill=(255, 255, 255, 255))
        d.rectangle([14*m, 36*m, 50*m, 42*m], fill=(200, 200, 200, 220))
        d.ellipse([45*m, 8*m, 57*m, 20*m], fill=(233, 69, 96, 255))
    img.save(str(out), format="ICO", sizes=[(16,16),(32,32),(48,48),(256,256)])
    print(f"icon.ico created programmatically  →  {out}")
