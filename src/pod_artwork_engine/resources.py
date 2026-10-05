from __future__ import annotations

import os

import psutil

from .contracts import ResourceSnapshot
from .storage import StorageManager


def capture_resources(storage: StorageManager) -> ResourceSnapshot:
    process = psutil.Process(os.getpid())
    memory = psutil.virtual_memory()
    return ResourceSnapshot(
        cpu_percent=max(0.0, psutil.cpu_percent(interval=None)),
        process_rss_bytes=max(0, process.memory_info().rss),
        memory_available_bytes=max(0, memory.available),
        storage_used_bytes=storage.status().used_bytes,
    )
