"""Test-first HQ enhancement: candidate profiles, preserve alpha and print resolution."""
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from pod_artwork_engine.api import create_app
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.quality_enhance import (
    PROFILE_VARIANTS, quality_candidate, technical_score, upscale_alpha_safe,
)


def sample(flat=True):
    image=Image.new("RGBA",(320,240),(0,0,0,0) if flat else (20,45,60,255))
    draw=ImageDraw.Draw(image)
    draw.ellipse((50,35,270,211), fill=(240,190,150,255), outline=(12,10,20,255),width=5)
    return image


def test_profiles_are_bounded_and_explain_choices():
    assert PROFILE_VARIANTS["fast"]==("standard",)
    assert PROFILE_VARIANTS["balanced"]==("standard","detail")
    assert PROFILE_VARIANTS["max_quality"]==("standard","detail","texture")


def test_quality_candidate_is_review_draft_and_has_measurable_metrics():
    original=sample()
    for profile, variants in PROFILE_VARIANTS.items():
        for variant in variants:
            output,report=quality_candidate(original,mode="sticker_clean",profile=profile,variant=variant)
            assert output.mode=="RGBA"
            assert output.width>=original.width
            assert report["profile"]==profile and report["variant"]==variant
            assert report["print_ready"] is False
            assert 0<=report["technical_score"]<=100
            assert "edge_gain" in report and "alpha_drift" in report
            assert output.getpixel((0,0))[3]==0
            assert output.getpixel((output.width//2,output.height//2))[3]>0


def test_invalid_profile_variant_rejected():
    with pytest.raises(ValueError):
        quality_candidate(sample(),profile="fast",variant="texture")
    with pytest.raises(ValueError):
        quality_candidate(sample(),profile="magic")


def test_hq_detail_can_use_anime_model_with_transparent_edges(tmp_path, monkeypatch):
    import pod_artwork_engine.quality_enhance as quality
    calls = []
    def fake_model(image, *, model, allow_alpha=False, wait_for_gpu_seconds=0):
        calls.append((model, allow_alpha, wait_for_gpu_seconds))
        return upscale_alpha_safe(image, (image.width * 2, image.height * 2)), {
            "engine": "real-esrgan-ncnn-vulkan", "model": model,
        }
    monkeypatch.setattr(quality, "enhance_with_realesrgan", fake_model)
    result, report = quality_candidate(sample(), mode="sticker_clean",
                                       profile="max_quality", variant="detail")
    assert calls == [("realesrgan-x4plus-anime", True, 90)]
    assert report["engine"] == "real-esrgan-ncnn-vulkan"
    assert result.getpixel((0, 0))[3] == 0


def test_alpha_safe_upscale_preserves_mask_and_avoids_opaque_halo():
    source=Image.new("RGBA",(100,100),(0,0,0,0))
    ImageDraw.Draw(source).ellipse((22,22,80,80),fill=(255,255,255,255))
    result=upscale_alpha_safe(source,(200,200),sharpen=1.15)
    assert result.mode=="RGBA" and result.size==(200,200)
    assert result.getpixel((0,0))[3]==0
    assert result.getpixel((100,100))[3]==255
    semitransparent=[p for p in result.getdata() if 30<p[3]<220]
    assert semitransparent
    assert min(p[0] for p in semitransparent)>200, "White contours must not develop dark halos"


def test_quality_api_reports_engine_and_score(tmp_path):
    client=TestClient(create_app(Settings(data_root=tmp_path)))
    buffer=BytesIO()
    sample().save(buffer,format="PNG")
    response=client.post("/enhance/run?mode=vector_clean&profile=max_quality&variant=detail",
        files={"file":("art.png",buffer.getvalue(),"image/png")})
    assert response.status_code==200,response.text[:250]
    assert response.headers["x-enhance-profile"]=="max_quality"
    assert response.headers["x-enhance-variant"]=="detail"
    assert response.headers["x-enhance-draft"]=="true"
    assert response.headers["x-enhance-operation"] == "hq-detail-alpha-safe"
    assert response.headers["x-enhance-tile-count"] == "0"
    assert 0<=float(response.headers["x-enhance-technical-score"])<=100
    with Image.open(BytesIO(response.content)) as output:
        assert output.width>=320


def test_fast_default_remains_backward_compatible(tmp_path):
    client=TestClient(create_app(Settings(data_root=tmp_path)))
    buffer=BytesIO()
    sample().save(buffer,format="PNG")
    response=client.post("/enhance/run?mode=vector_clean",
        files={"file":("art.png",buffer.getvalue(),"image/png")})
    assert response.status_code==200
    assert response.headers["x-enhance-profile"]=="fast"
    assert response.headers["x-enhance-variant"]=="standard"


def test_invalid_variant_is_422(tmp_path):
    client=TestClient(create_app(Settings(data_root=tmp_path)))
    buffer=BytesIO()
    sample().save(buffer,format="PNG")
    response=client.post("/enhance/run?profile=fast&variant=detail",files={"file":("art.png",buffer.getvalue(),"image/png")})
    assert response.status_code==422


def test_technical_score_does_not_assume_large_change_is_good():
    original=sample(flat=False)
    identical=technical_score(original,original)
    assert identical["alpha_drift"]==0
    corrupt=Image.new("RGBA",original.size,(255,0,0,0))
    poor=technical_score(original,corrupt)
    assert poor["technical_score"] < identical["technical_score"]

def test_tiled_max_quality_refines_full_canvas_without_upscaling_dimensions(monkeypatch):
    """Large print artwork should get true bounded inference, not only Pillow sharpening."""
    import pod_artwork_engine.quality_enhance as quality
    source = Image.new("RGBA", (85, 73), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((5, 4, 79, 68), fill=(210, 30, 100, 255))
    calls = []

    def fake_model(tile, *, model, allow_alpha=False, wait_for_gpu_seconds=0):
        calls.append((tile.size, model, allow_alpha, wait_for_gpu_seconds))
        return tile.resize((tile.width * 2, tile.height * 2), Image.Resampling.NEAREST), {}

    monkeypatch.setattr(quality, "enhance_with_realesrgan", fake_model)
    output, meta = quality.refine_large_with_realesrgan(
        source, model="realesrgan-x4plus-anime", tile_size=32, halo=4,
    )
    assert output is not None and output.size == source.size
    assert meta["tile_count"] == 9
    assert len(calls) == 9
    assert all(call[1:] == ("realesrgan-x4plus-anime", True, 90) for call in calls)
    assert output.getpixel((0, 0))[3] == 0
    assert output.getpixel((42, 36))[3] == 255
    assert source.getpixel((42, 36)) == (210, 30, 100, 255)


def test_tiled_max_quality_is_all_or_nothing_on_failed_gpu_tile(monkeypatch):
    import pod_artwork_engine.quality_enhance as quality
    calls = []

    def unavailable(tile, *, model, allow_alpha=False, wait_for_gpu_seconds=0):
        calls.append(tile.size)
        if len(calls) == 2:
            return None, {"reason": "GPU busy"}
        return tile.resize((tile.width * 2, tile.height * 2)), {}

    monkeypatch.setattr(quality, "enhance_with_realesrgan", unavailable)
    source = Image.new("RGBA", (72, 56), (120, 70, 25, 255))
    output, meta = quality.refine_large_with_realesrgan(source, model="realesrgan-x4plus",
                                                       tile_size=32, halo=4)
    assert output is None
    assert "GPU busy" in meta["reason"]
    assert len(calls) == 2


def test_max_quality_large_artwork_routes_to_real_tiled_inference(monkeypatch):
    import pod_artwork_engine.quality_enhance as quality
    source = sample(flat=False)
    calls = []

    def fake_tiles(image, *, model, **kwargs):
        calls.append(model)
        return image.copy(), {"tile_count": 4}

    monkeypatch.setattr(quality, "SINGLE_PASS_MAX_OUTPUT_PIXELS", 100)
    monkeypatch.setattr(quality, "refine_large_with_realesrgan", fake_tiles)
    output, report = quality.quality_candidate(source, mode="painterly_clean",
                                              profile="max_quality", variant="detail")
    assert calls == ["realesrgan-x4plus"]
    assert output.size == source.size
    assert report["engine"] == "real-esrgan-ncnn-vulkan"
    assert report["operation"] == "hq-tiled-ai-detail-refine"
    assert report["tile_count"] == 4
    assert report["print_ready"] is False


def test_manual_model_forces_real_ai_even_for_typography(monkeypatch):
    import pod_artwork_engine.quality_enhance as quality
    calls = []

    def fake_model(image, *, model, allow_alpha=False, wait_for_gpu_seconds=0):
        calls.append(model)
        return image.resize((image.width * 2, image.height * 2)), {"engine": "real-esrgan-ncnn-vulkan"}

    monkeypatch.setattr(quality, "available_realesrgan_model_names", lambda: ("realesrgan-x4plus", "realesrgan-x4plus-anime"))
    monkeypatch.setattr(quality, "enhance_with_realesrgan", fake_model)
    output, report = quality.quality_candidate(sample(), mode="typography_clean",
        profile="max_quality", variant="detail", model="realesrgan-x4plus")
    assert output.size == (640, 480)
    assert calls == ["realesrgan-x4plus"]
    assert report["model"] == "realesrgan-x4plus"


def test_manual_model_never_silently_falls_back(monkeypatch):
    import pod_artwork_engine.quality_enhance as quality
    monkeypatch.setattr(quality, "available_realesrgan_model_names", lambda: ("realesrgan-x4plus",))
    monkeypatch.setattr(quality, "enhance_with_realesrgan", lambda *args, **kwargs:
        (None, {"reason": "Vulkan GPU unavailable"}))
    with pytest.raises(ValueError, match="Vulkan GPU unavailable"):
        quality.quality_candidate(sample(), mode="typography_clean", profile="max_quality",
                                  variant="detail", model="realesrgan-x4plus")
    with pytest.raises(ValueError, match="not installed"):
        quality.quality_candidate(sample(), mode="sticker_clean", profile="max_quality",
                                  variant="detail", model="fake-model")


def test_strong_detail_sharpening_is_visible_and_alpha_safe():
    from PIL import ImageFilter
    from pod_artwork_engine.quality_enhance import apply_detail_strength
    source = Image.new("RGBA", (160, 140), (0, 0, 0, 0))
    ink = Image.new("RGBA", (100, 90), (90, 130, 180, 255))
    ImageDraw.Draw(ink).ellipse((15, 10, 80, 75), fill=(180, 210, 230, 255))
    source.paste(ink, (30, 25))
    blurred = source.filter(ImageFilter.GaussianBlur(.8))
    result = apply_detail_strength(blurred, "strong")
    assert result.getchannel("A").tobytes() == blurred.getchannel("A").tobytes()
    assert result.getpixel((0, 0))[3] == 0
    from PIL import ImageChops
    assert ImageChops.difference(result.convert("RGB"), blurred.convert("RGB")).getbbox() is not None


def test_selectable_model_api_header_and_manual_failure(tmp_path, monkeypatch):
    import pod_artwork_engine.quality_enhance as quality
    monkeypatch.setattr(quality, "available_realesrgan_model_names", lambda: ("realesrgan-x4plus",))
    monkeypatch.setattr(quality, "enhance_with_realesrgan", lambda image, **kw:
        (image.resize((image.width * 2, image.height * 2)), {"model": kw["model"]}))
    client = TestClient(create_app(Settings(data_root=tmp_path)))
    buffer = BytesIO()
    sample().save(buffer, format="PNG")
    payload = {"file": ("art.png", buffer.getvalue(), "image/png")}
    response = client.post("/enhance/run?mode=typography_clean&profile=max_quality&variant=detail"
        "&model=realesrgan-x4plus&detail_strength=strong", files=payload)
    assert response.status_code == 200, response.text[:250]
    assert response.headers["x-enhance-model"] == "realesrgan-x4plus"
    assert response.headers["x-enhance-detail-strength"] == "strong"
    bad = client.post("/enhance/run?profile=max_quality&variant=detail&model=unavailable", files=payload)
    assert bad.status_code == 422
