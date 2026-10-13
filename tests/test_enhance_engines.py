"""Acceptance tests for real Real-ESRGAN NCNN and VTracer paths.

These are intentionally authored before the integration implementation.
No paid API, GPU or network dependency is needed in unit tests.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from pod_artwork_engine.api import create_app
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.enhance_engines import (
    REAL_ESRGAN_MODELS, enhance_with_realesrgan, generate_vector_svg,
    find_realesrgan_runtime, enhance_capabilities,
)


def png() -> bytes:
    im=Image.new("RGBA",(180,120),(0,0,0,0))
    ImageDraw.Draw(im).ellipse((20,20,160,100),fill=(255,174,0,255),outline=(10,10,10,255),width=7)
    out=io.BytesIO()
    im.save(out,format="PNG")
    return out.getvalue()


def test_vectors_create_actual_svg_and_preserve_transparent_negative_space():
    image=Image.open(io.BytesIO(png()))
    svg=generate_vector_svg(image)
    assert svg.lstrip().startswith("<svg") or "<svg" in svg[:1000]
    assert "<path" in svg
    assert "viewBox" in svg or "width" in svg
    assert len(svg.encode("utf-8")) < 2_000_000


def test_vector_api_returns_svg_with_real_svg_mimetype(tmp_path):
    client=TestClient(create_app(Settings(data_root=tmp_path)))
    response=client.post("/enhance/vector?mode=sticker_clean",
        files={"file":("logo.png",png(),"image/png")})
    assert response.status_code==200, response.text[:500]
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["x-enhance-engine"]=="vtracer"
    assert response.headers["x-enhance-draft"]=="true"
    assert "<svg" in response.text
    assert not (tmp_path/"enhancements").exists()


def test_svg_is_restricted_to_vector_modes(tmp_path):
    client=TestClient(create_app(Settings(data_root=tmp_path)))
    response=client.post("/enhance/vector?mode=photo_cutout",
        files={"file":("portrait.png",png(),"image/png")})
    assert response.status_code==422
    assert "vector" in response.json()["detail"].lower()


def test_real_esrgan_runner_uses_bounded_command_and_validates_output(tmp_path,monkeypatch):
    home=tmp_path/"realesrgan"
    models=home/"models"
    models.mkdir(parents=True)
    exe=home/"realesrgan-ncnn-vulkan.exe"
    exe.write_bytes(b"fake")
    (models/"realesrgan-x4plus.param").write_text("fake")
    (models/"realesrgan-x4plus.bin").write_bytes(b"fake")
    seen={}
    def fake_run(args,**kwargs):
        seen["args"]=args
        seen["kwargs"]=kwargs
        target=Path(args[args.index("-o")+1])
        with Image.open(args[args.index("-i")+1]) as img:
            img.resize((img.width*2,img.height*2)).save(target,"PNG")
        import subprocess
        return subprocess.CompletedProcess(args,0,stdout="",stderr="")
    monkeypatch.setattr("pod_artwork_engine.enhance_engines.subprocess.run",fake_run)
    img=Image.new("RGBA",(128,128),(75,120,200,255))
    enhanced,meta=enhance_with_realesrgan(img,home,model="realesrgan-x4plus")
    assert meta["engine"]=="real-esrgan-ncnn-vulkan"
    assert enhanced.size==(256,256)
    assert seen["args"][0]==str(exe)
    assert seen["kwargs"]["timeout"]<=180
    assert seen["kwargs"].get("shell") is False


def test_gpu_busy_falls_back_without_spawning_another_model_process(tmp_path, monkeypatch):
    import pod_artwork_engine.enhance_engines as engines
    home=tmp_path/"realesrgan"
    (home/"models").mkdir(parents=True)
    (home/"realesrgan-ncnn-vulkan.exe").write_bytes(b"fake")
    (home/"models"/"realesrgan-x4plus.param").write_text("fake")
    (home/"models"/"realesrgan-x4plus.bin").write_bytes(b"fake")
    def forbidden(*args,**kwargs):
        raise AssertionError("A second Vulkan job must not spawn")
    monkeypatch.setattr(engines.subprocess,"run",forbidden)
    assert engines._GPU_LOCK.acquire(blocking=False)
    try:
        result, meta = engines.enhance_with_realesrgan(Image.new("RGBA",(80,80),"orange"),home)
        assert result is None
        assert meta["engine"]=="fallback"
        assert "busy" in meta["reason"].lower()
    finally:
        engines._GPU_LOCK.release()


def test_esrgan_skips_transparent_artwork_and_does_not_claim_ai(tmp_path):
    image=Image.open(io.BytesIO(png()))
    result,meta=enhance_with_realesrgan(image,tmp_path)
    assert result is None
    assert meta["engine"]=="fallback"
    assert "unavailable" in meta["reason"] or "transparen" in meta["reason"]


def test_model_status_reports_missing_runtime_without_download(tmp_path,monkeypatch):
    monkeypatch.setenv("POD_REALESRGAN_DIR",str(tmp_path/"missing"))
    capabilities=enhance_capabilities()
    assert capabilities["vtracer"]["installed"] is True
    assert capabilities["realesrgan"]["installed"] is False
    assert capabilities["realesrgan"]["model_names"]==list(REAL_ESRGAN_MODELS)


def test_auto_routing_reports_actual_engine_on_fallback(tmp_path,monkeypatch):
    from pod_artwork_engine.auto_enhance import enhance_artwork
    monkeypatch.setenv("POD_REALESRGAN_DIR",str(tmp_path/"missing"))
    image=Image.new("RGBA",(200,140),(60,90,110,255))
    out,report=enhance_artwork(image,mode="painterly_clean")
    assert out.width>=200
    assert report["engine"] in {"pillow-fallback","real-esrgan-ncnn-vulkan"}
    assert report["engine"]=="pillow-fallback"
    assert report["print_ready"] is False


def test_capabilities_endpoint_and_ai_output_header(tmp_path):
    client=TestClient(create_app(Settings(data_root=tmp_path)))
    caps=client.get("/enhance/capabilities")
    assert caps.status_code==200
    assert "realesrgan" in caps.json() and "vtracer" in caps.json()
    response=client.post("/enhance/run?mode=sticker_clean",
        files={"file":("logo.png",png(),"image/png")})
    assert response.status_code==200
    assert response.headers["x-enhance-engine"] in {"pillow-fallback","real-esrgan-ncnn-vulkan"}
