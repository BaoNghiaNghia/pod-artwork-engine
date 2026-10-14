"""Run two real pinned Vulkan inferences and prove manual model/strength changes pixels."""
from PIL import Image, ImageChops, ImageDraw, ImageStat
from pod_artwork_engine.quality_enhance import quality_candidate

sample = Image.new("RGBA", (176, 120), (0, 0, 0, 0))
paint = ImageDraw.Draw(sample)
paint.rounded_rectangle((16, 12, 160, 107), radius=13, fill=(60, 96, 143, 255))
paint.ellipse((30, 22, 112, 100), fill=(230, 160, 75, 255), outline=(15, 25, 48, 255), width=3)
paint.line((50, 65, 100, 35), fill=(255, 255, 255, 255), width=3)
paint.line((50, 35, 100, 65), fill=(255, 255, 255, 255), width=2)

natural, n = quality_candidate(sample, mode="typography_clean", profile="max_quality",
                                variant="detail", model="realesrgan-x4plus-anime", detail_strength="natural")
clear, c = quality_candidate(sample, mode="typography_clean", profile="max_quality",
                             variant="detail", model="realesrgan-x4plus-anime", detail_strength="strong")
assert n["engine"] == c["engine"] == "real-esrgan-ncnn-vulkan", (n, c)
assert natural.size == clear.size == (352, 240)
assert natural.getchannel("A").tobytes() == clear.getchannel("A").tobytes()
pixel_change = ImageStat.Stat(ImageChops.difference(natural.convert("RGB"), clear.convert("RGB"))).mean
assert max(pixel_change) > 0.05, pixel_change
print("MANUAL_REAL_GPU_PASS", n["model"], "size", clear.size, "strength_changed_rgb_mean", [round(x, 3) for x in pixel_change])
