"""Auto Enhance MVP: regression/contract tests written before implementation."""
from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from pod_artwork_engine.api import create_app
from pod_artwork_engine.auto_enhance import MAX_UPLOAD_BYTES, analyze_artwork, enhance_artwork
from pod_artwork_engine.settings import Settings


def fixture(kind: str) -> Image.Image:
    im = Image.new("RGBA", (300, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    if kind == "flat":
        draw.rectangle((30, 55, 260, 93), fill="#dc4894")
        draw.rectangle((35, 127, 245, 165), fill="#dc4894")
        draw.ellipse((100, 175, 160, 220), fill="#dc4894")
    elif kind == "sticker":
        draw.ellipse((50, 24, 249, 224), fill="#ffe92e", outline="#101010", width=12)
        draw.ellipse((98, 95, 120, 125), fill="#101010")
        draw.ellipse((176, 95, 198, 125), fill="#101010")
        draw.arc((107, 115, 197, 186), 10, 170, fill="#101010", width=8)
    elif kind == "dark":
        draw.rectangle((0, 0, 299, 239), fill="#171717")
    return im


def png_bytes(im: Image.Image) -> bytes:
    buffer = BytesIO()
    im.save(buffer, format="PNG")
    return buffer.getvalue()




def test_maximum_upload_file_size_is_50_mib():
    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024


def test_enhance_upload_rejects_over_limit_before_decoding(monkeypatch, tmp_path):
    # Small patched threshold keeps the regression fast without allocating 50 MiB.
    import pod_artwork_engine.api as api
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 128)
    client = TestClient(create_app(Settings(data_root=tmp_path)))
    result = client.post("/enhance/analyze", files={"file": ("oversize.png", b"x" * 129, "image/png")})
    assert result.status_code == 413
    assert "50 MB" in result.json()["detail"]


def test_artwork_job_upload_rejects_over_limit(monkeypatch, tmp_path):
    import pod_artwork_engine.api as api
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 128)
    client = TestClient(create_app(Settings(data_root=tmp_path)))
    result = client.post("/jobs", files={"files": ("oversize.png", b"x" * 129, "image/png")})
    assert result.status_code == 413
    assert "50 MB" in result.json()["detail"]
    assert not any((tmp_path / "jobs").iterdir())


def test_image_chat_upload_rejects_over_limit(monkeypatch, tmp_path):
    import pod_artwork_engine.api as api
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 128)
    client = TestClient(create_app(Settings(data_root=tmp_path)))
    result = client.post("/image-chat/sessions",
                         files={"files": ("too-big.png", b"x" * 129, "image/png")})
    assert result.status_code == 413
    assert "50 MB" in result.json()["detail"]


def test_input_pixel_capacity_allows_standard_print_canvas():
    from pod_artwork_engine.auto_enhance import MAX_INPUT_PIXELS
    assert MAX_INPUT_PIXELS >= 4500 * 5400


def test_artwork_job_keeps_both_same_named_images(tmp_path):
    client = TestClient(create_app(Settings(data_root=tmp_path)))
    red = png_bytes(Image.new("RGBA", (64, 64), "#ff0000"))
    blue = png_bytes(Image.new("RGBA", (64, 64), "#0000ff"))
    response = client.post("/jobs?quality_mode=quick_2d", files=[
        ("files", ("same.png", red, "image/png")),
        ("files", ("same.png", blue, "image/png")),
    ])
    assert response.status_code == 200
    paths = response.json()["source_paths"]
    assert len(paths) == 2 and paths[0] != paths[1]
    with Image.open(paths[0]) as first, Image.open(paths[1]) as second:
        assert first.convert("RGB").getpixel((0, 0)) != second.convert("RGB").getpixel((0, 0))

def test_classifier_is_deterministic_and_explains_choice():
    result = analyze_artwork(fixture("flat"))
    assert result == analyze_artwork(fixture("flat"))
    assert result["category"] in {"typography", "logo_vector", "sticker_cartoon", "mixed"}
    assert result["mode"] in {
        "typography_clean", "vector_clean", "sticker_clean", "gentle_clean",
    }
    assert 0 <= result["confidence"] <= 1
    assert result["reasons"]
    assert len(result["scores"]) >= 3
    assert result["print_ready"] is False


def test_black_art_on_transparent_background_is_not_dark_rescue():
    image = Image.new("RGBA", (300, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((35, 25, 260, 212), fill="#111111")
    draw.line((35, 25, 260, 212), fill="#111111", width=10)
    route = analyze_artwork(image)
    assert route["category"] == "logo_vector"
    assert route["mode"] == "vector_clean"
    assert any("transparen" in reason.lower() for reason in route["reasons"])


def test_dark_flat_detects_dark_rescue_without_lightening_source():
    image = fixture("dark")
    report = analyze_artwork(image)
    assert report["category"] == "dark_low_contrast"
    result, chosen = enhance_artwork(image, mode="auto")
    assert chosen["mode"] == "dark_rescue"
    assert image.getpixel((80, 95))[:3] == (23, 23, 23)  # no mutation
    assert result.mode == "RGBA"


def test_sticker_override_preserves_transparency_and_distinct_colors():
    image = fixture("sticker")
    result, report = enhance_artwork(image, mode="sticker_clean")
    assert report["mode"] == "sticker_clean"
    assert result.width > image.width
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((300, 240))[3] > 0


def test_light_edges_do_not_acquire_black_fringe():
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((12, 12, 84, 84), fill=(255, 255, 255, 255))
    out, _ = enhance_artwork(image, mode="vector_clean")
    edge_pixels = [pixel for pixel in out.getdata() if 20 < pixel[3] < 235]
    assert edge_pixels, "Expected smooth antialiased alpha edge pixels"
    assert min(min(pixel[:3]) for pixel in edge_pixels) > 218, (
        "Transparent white artwork must not develop dark RGB halo"
    )


def test_photo_override_does_not_modify_subject_semantics():
    original = Image.new("RGBA", (200, 150), (135, 96, 75, 255))
    result, plan = enhance_artwork(original, mode="photo_cutout")
    assert plan["mode"] == "photo_cutout"
    assert result.size[0] >= 200
    assert result.getpixel((result.width // 2, result.height // 2))[0] in range(125, 146)


def test_api_analyze_run_and_safe_override(tmp_path):
    client = TestClient(create_app(Settings(data_root=tmp_path)))
    source = png_bytes(fixture("sticker"))
    classified = client.post("/enhance/analyze", files={"file": ("input.png", source, "image/png")})
    assert classified.status_code == 200
    report = classified.json()
    assert report["mode"] in report["supported_modes"]
    assert report["confidence"] <= 1
    output = client.post("/enhance/run?mode=sticker_clean",
                         files={"file": ("input.png", source, "image/png")})
    assert output.status_code == 200
    assert output.headers["content-type"].startswith("image/png")
    assert output.headers["x-enhance-mode"] == "sticker_clean"
    assert output.headers["x-enhance-draft"] == "true"
    with Image.open(BytesIO(output.content)) as result:
        assert result.width > 300
        assert result.mode == "RGBA"
    assert client.post("/enhance/run?mode=unknown",
                       files={"file": ("x.png", source, "image/png")}).status_code == 422


def test_api_rejects_invalid_image_and_preserves_data_root(tmp_path):
    client = TestClient(create_app(Settings(data_root=tmp_path)))
    bad = client.post("/enhance/analyze", files={"file": ("bad.png", b"not-an-image", "image/png")})
    assert bad.status_code == 422
    assert not (tmp_path / "enhancements").exists()
