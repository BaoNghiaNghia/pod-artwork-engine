from pathlib import Path
from PIL import Image, ImageDraw

icons = Path("desktop/src-tauri/icons")
icons.mkdir(parents=True, exist_ok=True)
image = Image.new("RGBA", (256, 256), (23, 32, 51, 255))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((38, 38, 218, 218), radius=42, fill=(69, 103, 232, 255))
draw.text((91, 116), "POD", fill="white")
image.save(icons / "icon.png")
image.save(
    icons / "icon.ico",
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
