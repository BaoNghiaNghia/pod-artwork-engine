from __future__ import annotations

import psutil

from .settings import GIB, Settings


def recommended_job_concurrency(settings: Settings) -> int:
    """Conservative auto-concurrency for CPU-first reconstruction workloads."""
    if settings.max_concurrent_jobs > 0:
        return max(1, min(settings.max_concurrent_jobs, 8))

    logical = psutil.cpu_count(logical=True) or 8
    cpu_budget = max(1, min(settings.cpu_soft_threads, logical) // 8)
    ram_budget = max(1, int(settings.ram_soft_bytes // (6 * GIB)))
    return max(1, min(cpu_budget, ram_budget, 4))
