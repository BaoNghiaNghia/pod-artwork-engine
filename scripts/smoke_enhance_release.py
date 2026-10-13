"""Post-build, isolated packaged Engine + real model smoke test.

Starts release binary on a temporary data root and isolated loopback port;
checks VTracer SVG, NCNN/Vulkan inference, header labeling, and cleans up.
No production files or running desktop processes are touched.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw
import psutil

PORT = 18769
BASE = f"http://127.0.0.1:{PORT}"
EXE = Path("build/release/pod-artwork-engine.exe").resolve()


def send(path: str, payload: bytes | None = None, content_type: str = "application/json"):
    request = urllib.request.Request(BASE + path, data=payload,
                                     headers={"Content-Type": content_type},
                                     method="POST" if payload else "GET")
    with urllib.request.urlopen(request, timeout=80) as response:
        return response.status, {key.lower(): val for key, val in response.headers.items()}, response.read()


def multipart_image(img: Image.Image):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    raw=buf.getvalue()
    marker=b"pod-smoke-boundary"
    body=(b"--"+marker+b"\r\n"+
          b'Content-Disposition: form-data; name="file"; filename="smoke.png"\r\n'+
          b"Content-Type: image/png\r\n\r\n"+raw+
          b"\r\n--"+marker+b"--\r\n")
    return body, "multipart/form-data; boundary="+marker.decode()


def main():
    if not EXE.is_file():
        raise RuntimeError(f"release binary missing: {EXE}")
    with tempfile.TemporaryDirectory(prefix="pod-bundled-smoke-") as directory:
        env=os.environ.copy()
        env["POD_ENGINE_PORT"]=str(PORT)
        env["POD_ARTWORK_DATA"]=directory
        env.pop("POD_REALESRGAN_DIR",None)
        kwargs=dict(cwd=str(EXE.parent), env=env, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
        if os.name=="nt":
            kwargs["creationflags"]=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)
        proc=subprocess.Popen([str(EXE),"serve"], **kwargs)
        try:
            for index in range(90):
                if proc.poll() is not None:
                    raise RuntimeError(f"Engine exited early with {proc.returncode}")
                try:
                    code,_,raw=send("/health")
                    if code==200: break
                except (OSError,urllib.error.HTTPError): pass
                time.sleep(.5)
            else:
                raise RuntimeError("Bundled Engine readiness timeout")
            print("health",raw.decode(),flush=True)
            _,_,caps=send("/enhance/capabilities")
            capabilities=json.loads(caps)
            print("caps",json.dumps(capabilities,sort_keys=True),flush=True)
            assert capabilities["vtracer"]["installed"],"Bundled PyInstaller does not ship VTracer"
            assert capabilities["realesrgan"]["installed"],"Bundled release lacks NCNN models"
            vector_img=Image.new("RGBA",(180,120),(0,0,0,0))
            ImageDraw.Draw(vector_img).rectangle((35,20,145,100),fill=(220,35,90,255))
            body,ctype=multipart_image(vector_img)
            code,headers,svg=send("/enhance/vector?mode=vector_clean",body,ctype)
            assert code==200 and b"<svg" in svg and b"<path" in svg
            print("SVG_BYTES",len(svg),"ENGINE",headers.get("x-enhance-engine"),flush=True)
            photo=Image.new("RGB",(176,120),(25,35,65))
            draw=ImageDraw.Draw(photo)
            draw.rectangle((25,23,155,98),fill=(240,160,80),outline=(250,250,255),width=3)
            body,ctype=multipart_image(photo)
            code,headers,img=send("/enhance/run?mode=painterly_clean",body,ctype)
            print("AI_HEADERS",headers,flush=True)
            assert code==200 and headers.get("x-enhance-engine")=="real-esrgan-ncnn-vulkan",headers
            with Image.open(io.BytesIO(img)) as output:
                assert output.size==(352,240),output.size
                print("AI_UPSCALE",output.size,"ENGINE",headers.get("x-enhance-engine"),flush=True)
            # Release must also support HQ profiles, transparent anime model,
            # real GPU processing, and enhanced score headers (no mocks).
            sticker = Image.new("RGBA", (176,120), (0,0,0,0))
            ImageDraw.Draw(sticker).ellipse((20,12,156,108),
                fill=(255,204,31,255),outline=(10,10,14,255),width=5)
            body,ctype=multipart_image(sticker)
            code,headers,png=send("/enhance/run?mode=sticker_clean&profile=max_quality&variant=detail",
                                   body,ctype)
            assert code==200 and headers.get("x-enhance-profile")=="max_quality",headers
            assert headers.get("x-enhance-engine")=="real-esrgan-ncnn-vulkan",headers
            with Image.open(io.BytesIO(png)) as output:
                assert output.size==(352,240),output.size
                assert output.getpixel((0,0))[3]==0
            print("HQ_TRANSPARENT_AI_PASS",headers.get("x-enhance-technical-score"),flush=True)
            code,headers,svg=send("/enhance/vector?mode=sticker_clean&profile=max_quality",body,ctype)
            assert code==200 and b"<path" in svg
            assert headers.get("x-enhance-vector-preset")=="sticker"
            print("HQ_VECTOR_PASS",len(svg),flush=True)
            print("BUNDLED_SMOKE_PASS",flush=True)
        finally:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=12, check=False)
            else:
                proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            # PyInstaller onefile may leave a separate bootloader child alive;
            # stop only processes running THIS isolated build/release binary.
            children = []
            for owned in psutil.process_iter(["exe"]):
                try:
                    executable = owned.info.get("exe")
                    if executable and Path(executable).resolve() == EXE:
                        children.append(owned)
                except (OSError, psutil.Error):
                    continue
            for owned in children:
                try: owned.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied): pass
            _, remaining = psutil.wait_procs(children, timeout=8)
            for owned in remaining:
                try: owned.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied): pass
            time.sleep(1.0)

if __name__=="__main__":
    main()
