"""Fit the README and site screenshots onto Product Hunt's 1270x760 gallery canvas.

    python3 docs/launch/make_gallery.py      # needs Pillow; writes docs/launch/gallery/

The sources are captures of the synthetic sample workday (see docs/assets/README.md).
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "gallery"
SOURCES = [
    ("1-graph.png", ROOT / "docs/assets/headline.png"),
    ("2-context-files.png", ROOT / "docs/assets/screens/contexts-all-files.png"),
    ("3-timeline.png", ROOT / "docs/assets/screens/contexts-all-timeline.png"),
    ("4-workflows.png", ROOT / "docs/assets/screens/workflows.png"),
    ("5-integrations.png", ROOT / "docs/assets/screens/integrations.png"),
]
SIZE = (1270, 760)

OUT.mkdir(exist_ok=True)
for name, source in SOURCES:
    image = Image.open(source).convert("RGB")
    image.thumbnail(SIZE, Image.LANCZOS)
    canvas = Image.new("RGB", SIZE, (0, 0, 0))
    canvas.paste(image, ((SIZE[0] - image.width) // 2, (SIZE[1] - image.height) // 2))
    canvas.save(OUT / name, optimize=True)
    print(OUT / name)
