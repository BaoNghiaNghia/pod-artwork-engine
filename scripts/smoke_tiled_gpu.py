"""Opt-in real-GPU smoke for tiled Max Quality. No external files are modified."""
from __future__ import annotations

from time import monotonic
from PIL import Image, ImageChops, ImageDraw
from pod_artwork_engine.quality_enhance import refine_large_with_realesrgan


def main() -> None:
    original = Image.new("RGBA", (392, 300), (0, 0, 0, 0))
    ink = ImageDraw.Draw(original)
    ink.rounded_rectangle((18, 12, 373, 289), radius=34,
                          fill=(236, 191, 43, 255), outline=(24, 20, 18, 255), width=6)
    ink.ellipse((100, 92, 145, 140), fill=(10, 11, 13, 255))
    ink.ellipse((249, 92, 292, 140), fill=(10, 11, 13, 255))
    ink.arc((124, 135, 269, 225), start=5, end=175, fill=(10, 11, 13, 255), width=8)

    start = monotonic()
    result, meta = refine_large_with_realesrgan(
        original, model="realesrgan-x4plus-anime",
        tile_size=196, halo=16, time_budget_seconds=180,
    )
    assert result is not None, f"Tiled inference failed: {meta}"
    assert result.size == original.size
    assert meta["tile_count"] == 4, meta
    assert not ImageChops.difference(original.getchannel("A"),
                                     result.getchannel("A")).getbbox(), "Source alpha changed"
    assert result.getpixel((0, 0))[3] == 0
    print("TILED_REAL_GPU_PASS", "tiles", meta["tile_count"],
          "canvas", result.size, "alpha_exact", True,
          "elapsed_s", round(monotonic() - start, 1), flush=True)


if __name__ == "__main__":
    main()
