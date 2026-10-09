from __future__ import annotations
import argparse
import json
from pathlib import Path
from PIL import Image
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.job_store import JobStore

args=argparse.ArgumentParser(description="Inspect local POD job without modifying artifacts")
args.add_argument("job_id_prefix", help="First characters of job ID")
prefix=args.parse_args().job_id_prefix
settings=Settings.from_env()
store=JobStore(settings.database_path)
jobs=[j for j in store.list_recent(limit=500) if j.job_id.startswith(prefix)]
print("MATCHING_JOBS",len(jobs))
for job in jobs:
    print("JOB",job.model_dump_json(indent=2))
    root=settings.jobs_dir / job.job_id
    for filename in sorted(root.rglob("*")):
        if filename.is_file():
            print("ASSET",str(filename.relative_to(root)),filename.stat().st_size)
            if filename.suffix.lower() in {".jpg",".jpeg",".webp",".png"}:
                with Image.open(filename) as source:
                    image=source.convert("RGBA")
                    alpha=image.getchannel("A")
                    print("IMAGE",image.size,source.mode,"alpha",alpha.getextrema(),"bbox",alpha.getbbox())
            elif filename.suffix.lower()==".json" and ("checkpoint" in str(filename) or filename.name.startswith("qc")):
                try:
                    content=json.loads(filename.read_text(encoding="utf8"))
                    print("CHECKPOINT",filename.name,json.dumps(content,ensure_ascii=False)[:2400])
                except Exception as exc:print("READ_ERROR",type(exc).__name__,str(exc))
