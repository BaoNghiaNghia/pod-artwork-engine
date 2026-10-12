"""Read-only runtime process inspection for safe POD desktop update diagnostics."""
import json
import psutil

items = []
for process in psutil.process_iter(["pid", "name", "exe", "status"]):
    try:
        name = str(process.info.get("name") or "").lower()
        if name in {"pod-artwork-desktop.exe", "pod-artwork-engine.exe", "podartworktool.exe"}:
            items.append({
                "pid": process.pid,
                "name": name,
                "exe": process.info.get("exe"),
                "status": process.info.get("status"),
            })
    except (psutil.Error, OSError):
        continue
print(json.dumps(items, ensure_ascii=False, indent=2))
