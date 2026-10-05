from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass

import psutil


@dataclass(frozen=True)
class GpuInfo:
    name: str
    adapter_ram_bytes: int | None = None
    driver_version: str | None = None
    memory_source: str | None = None


@dataclass(frozen=True)
class HardwareProfile:
    platform: str
    cpu_logical: int
    cpu_physical: int
    memory_total_bytes: int
    memory_available_bytes: int
    gpus: list[GpuInfo]
    recommended_engine_threads: int
    gpu_policy: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["gpus"] = [asdict(gpu) for gpu in self.gpus]
        return payload


def _run_hidden(command: list[str], timeout: int = 4) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _nvidia_gpus() -> list[GpuInfo]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        result = _run_hidden(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ]
        )
        if result.returncode != 0:
            return []
        gpus: list[GpuInfo] = []
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3:
                continue
            gpus.append(
                GpuInfo(
                    name=parts[0],
                    adapter_ram_bytes=int(float(parts[1]) * 1024 * 1024),
                    driver_version=parts[2],
                    memory_source="nvidia-smi",
                )
            )
        return gpus
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _windows_registry_gpus() -> list[GpuInfo]:
    if os.name != "nt":
        return []
    script = r"""
$items = @()
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Video' -ErrorAction SilentlyContinue | ForEach-Object {
  Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d{4}$' } | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
    $mem = $p.'HardwareInformation.qwMemorySize'
    if ($p.DriverDesc -and $mem) {
      $items += [PSCustomObject]@{
        Name = $p.DriverDesc
        AdapterRAM = [uint64]$mem
        DriverVersion = $p.DriverVersion
      }
    }
  }
}
$items | Sort-Object Name -Unique | ConvertTo-Json -Compress
"""
    try:
        result = _run_hidden(["powershell", "-NoProfile", "-Command", script])
        if result.returncode != 0 or not result.stdout.strip():
            return []
        raw = json.loads(result.stdout)
        records = raw if isinstance(raw, list) else [raw]
        return [
            GpuInfo(
                name=str(item.get("Name") or "Unknown GPU"),
                adapter_ram_bytes=int(item["AdapterRAM"]) if item.get("AdapterRAM") else None,
                driver_version=str(item["DriverVersion"]) if item.get("DriverVersion") else None,
                memory_source="windows-registry",
            )
            for item in records
        ]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError):
        return []


def _windows_cim_gpus() -> list[GpuInfo]:
    if os.name != "nt":
        return []
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress"
        ),
    ]
    try:
        result = _run_hidden(command)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        raw = json.loads(result.stdout)
        records = raw if isinstance(raw, list) else [raw]
        return [
            GpuInfo(
                name=str(item.get("Name") or "Unknown GPU"),
                adapter_ram_bytes=int(item["AdapterRAM"]) if item.get("AdapterRAM") else None,
                driver_version=str(item["DriverVersion"]) if item.get("DriverVersion") else None,
                memory_source="win32-videocontroller-approximate",
            )
            for item in records
        ]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError):
        return []


def _merge_gpus(primary: list[GpuInfo], fallback: list[GpuInfo]) -> list[GpuInfo]:
    merged = list(primary)
    names = {gpu.name.lower() for gpu in primary}
    for gpu in fallback:
        if gpu.name.lower() not in names:
            merged.append(gpu)
    return merged


def _windows_gpus() -> list[GpuInfo]:
    nvidia = _nvidia_gpus()
    registry = _windows_registry_gpus()
    if registry:
        return _merge_gpus(nvidia, registry)
    return _merge_gpus(nvidia, _windows_cim_gpus())


def detect_hardware() -> HardwareProfile:
    logical = psutil.cpu_count(logical=True) or 1
    physical = psutil.cpu_count(logical=False) or max(1, logical // 2)
    memory = psutil.virtual_memory()
    gpus = _windows_gpus()

    recommended_threads = min(40, max(4, logical - max(8, logical // 4)))
    gpu_names = " ".join(gpu.name.lower() for gpu in gpus)
    if "rx 470" in gpu_names or "radeon rx 470" in gpu_names:
        gpu_policy = "lightweight_optional"
    elif gpus:
        gpu_policy = "benchmark_required"
    else:
        gpu_policy = "cpu_only"

    return HardwareProfile(
        platform=platform.platform(),
        cpu_logical=logical,
        cpu_physical=physical,
        memory_total_bytes=memory.total,
        memory_available_bytes=memory.available,
        gpus=gpus,
        recommended_engine_threads=recommended_threads,
        gpu_policy=gpu_policy,
    )
