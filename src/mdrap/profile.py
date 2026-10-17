"""Dynamic Hardware Profiler & Resource Adaptation Engine for MDRAP.

Automatically inspects host hardware (CPU architecture, core count, physical RAM)
and dynamically configures memory limits, ring buffer capacities, and WAL flush
cadences to guarantee stability from 2GB RAM budget machines ('potato PCs')
to 64+ core institutional commercial servers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import platform
import sys

__stability__ = "stable"


class ProfileMode(str, Enum):
    """Execution resource profile modes."""

    LITE = "LITE"  # Potato PC / edge: ≤ 4-6 GB RAM, 2 cores, tight memory budget
    STANDARD = "STANDARD"  # Workstation / dev machine: 8-32 GB RAM, 4-16 cores
    ULTRA = "ULTRA"  # Commercial server / HFT node: ≥ 48-64 GB RAM, 16+ cores


def get_total_ram_bytes() -> int:
    """Detect total physical RAM in bytes using standard library primitives only."""
    # 1. Windows via ctypes GlobalMemoryStatusEx
    if sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        except Exception:
            pass

    # 2. Linux via /proc/meminfo or sysconf
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        return int(parts[1]) * 1024  # kB to bytes
        except Exception:
            pass

    # 3. POSIX sysconf
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return int(pages * page_size)
    except (AttributeError, ValueError, OSError):
        pass

    # Fallback heuristic: 8 GB default
    return 8 * 1024 * 1024 * 1024


@dataclass(slots=True)
class HardwareProfile:
    """Runtime hardware adaptation parameters."""

    mode: ProfileMode
    cpu_cores: int
    total_ram_gb: float
    ring_buffer_slots: int
    ring_buffer_bytes: int
    wal_flush_interval_s: float
    wal_batch_size: int
    sqlite_page_size: int
    sqlite_cache_size_kb: int
    max_queue_size: int
    spin_iterations: int
    description: str

    @classmethod
    def detect(cls, override_mode: str | ProfileMode | None = None) -> HardwareProfile:
        """Detect host resources and return an optimized HardwareProfile."""
        cpu_cores = os.cpu_count() or 2
        ram_bytes = get_total_ram_bytes()
        ram_gb = round(ram_bytes / (1024**3), 2)

        # Allow environment or parameter override
        env_override = os.environ.get("MDRAP_PROFILE")
        selected = override_mode or env_override

        if selected:
            mode_str = str(selected).strip().upper()
            if mode_str in ("LITE", "POTATO", "MINIMAL", "LOW"):
                mode = ProfileMode.LITE
            elif mode_str in ("ULTRA", "SERVER", "MAX", "HIGH"):
                mode = ProfileMode.ULTRA
            else:
                mode = ProfileMode.STANDARD
        else:
            # Automatic heuristic classification
            if ram_gb < 6.0 or cpu_cores <= 2:
                mode = ProfileMode.LITE
            elif ram_gb >= 48.0 and cpu_cores >= 16:
                mode = ProfileMode.ULTRA
            else:
                mode = ProfileMode.STANDARD

        return cls.for_mode(mode, cpu_cores=cpu_cores, total_ram_gb=ram_gb)

    @classmethod
    def for_mode(
        cls,
        mode: ProfileMode,
        cpu_cores: int | None = None,
        total_ram_gb: float | None = None,
    ) -> HardwareProfile:
        """Construct a HardwareProfile for a specific target mode."""
        cores = cpu_cores or (os.cpu_count() or 2)
        ram = total_ram_gb or 8.0

        if mode == ProfileMode.LITE:
            return cls(
                mode=ProfileMode.LITE,
                cpu_cores=cores,
                total_ram_gb=ram,
                ring_buffer_slots=32768,  # 32K slots = 4MB shared memory
                ring_buffer_bytes=4 * 1024 * 1024,
                wal_flush_interval_s=0.100,  # 100ms batch sync
                wal_batch_size=500,
                sqlite_page_size=4096,  # Compact pages
                sqlite_cache_size_kb=8192,  # 8MB SQLite cache
                max_queue_size=2048,  # Bounded in-flight queue
                spin_iterations=0,  # Zero busy-spin; immediate yield/sleep
                description="Lite Mode: Optimized for low RAM / potato PCs with bounded memory footprint.",
            )
        elif mode == ProfileMode.ULTRA:
            return cls(
                mode=ProfileMode.ULTRA,
                cpu_cores=cores,
                total_ram_gb=ram,
                ring_buffer_slots=1048576,  # 1M slots = 128MB shared memory
                ring_buffer_bytes=128 * 1024 * 1024,
                wal_flush_interval_s=0.001,  # 1ms high-frequency sync
                wal_batch_size=10000,
                sqlite_page_size=65536,  # 64KB high-throughput pages
                sqlite_cache_size_kb=1048576,  # 1GB SQLite cache
                max_queue_size=131072,  # Large burst queue
                spin_iterations=100,  # Busy-spin on hot seqlocks
                description="Ultra Mode: Optimized for multi-core institutional servers and high-throughput HFT feeds.",
            )
        else:
            return cls(
                mode=ProfileMode.STANDARD,
                cpu_cores=cores,
                total_ram_gb=ram,
                ring_buffer_slots=131072,  # 128K slots = 16MB shared memory
                ring_buffer_bytes=16 * 1024 * 1024,
                wal_flush_interval_s=0.010,  # 10ms sync
                wal_batch_size=2000,
                sqlite_page_size=8192,
                sqlite_cache_size_kb=65536,  # 64MB cache
                max_queue_size=16384,
                spin_iterations=20,
                description="Standard Mode: Balanced profile for multi-core workstations and quantitative research.",
            )

    def summary(self) -> dict[str, str | int | float]:
        """Return a structured summary of the hardware profile."""
        return {
            "mode": self.mode.value,
            "cpu_cores": self.cpu_cores,
            "total_ram_gb": self.total_ram_gb,
            "ring_buffer_mb": round(self.ring_buffer_bytes / (1024**2), 1),
            "ring_buffer_slots": self.ring_buffer_slots,
            "wal_flush_interval_ms": int(self.wal_flush_interval_s * 1000),
            "sqlite_cache_mb": round(self.sqlite_cache_size_kb / 1024, 1),
            "max_queue_size": self.max_queue_size,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        }


# Global cached detection instance
_CURRENT_PROFILE: HardwareProfile | None = None


def current_profile() -> HardwareProfile:
    """Return the active HardwareProfile, detecting it if not yet initialized."""
    global _CURRENT_PROFILE
    if _CURRENT_PROFILE is None:
        _CURRENT_PROFILE = HardwareProfile.detect()
    return _CURRENT_PROFILE


def set_cpu_affinity(core_ids: list[int], pid: int | None = None) -> bool:
    """Pin process execution to specific CPU cores.

    Supports Linux (sched_setaffinity) and Windows (SetProcessAffinityMask).
    Returns True if affinity was successfully applied, False on unsupported platforms or failure.
    """
    if not core_ids:
        return False

    # 1. Linux sched_setaffinity
    if hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(pid or 0, set(core_ids))
            return True
        except Exception:
            return False

    # 2. Windows SetProcessAffinityMask
    if sys.platform == "win32":
        try:
            import ctypes

            mask = 0
            for c in core_ids:
                if 0 <= c < 64:
                    mask |= 1 << c
            h_proc = ctypes.windll.kernel32.GetCurrentProcess()
            ret = ctypes.windll.kernel32.SetProcessAffinityMask(
                h_proc, ctypes.c_size_t(mask)
            )
            return bool(ret != 0)
        except Exception:
            return False

    return False


def get_cpu_affinity(pid: int | None = None) -> list[int]:
    """Return currently assigned CPU cores for the process."""
    if hasattr(os, "sched_getaffinity"):
        try:
            return sorted(list(os.sched_getaffinity(pid or 0)))
        except Exception:
            pass

    if sys.platform == "win32":
        try:
            import ctypes

            h_proc = ctypes.windll.kernel32.GetCurrentProcess()
            proc_mask = ctypes.c_size_t()
            sys_mask = ctypes.c_size_t()
            if ctypes.windll.kernel32.GetProcessAffinityMask(
                h_proc, ctypes.byref(proc_mask), ctypes.byref(sys_mask)
            ):
                val = proc_mask.value
                return [i for i in range(64) if (val & (1 << i))]
        except Exception:
            pass

    return list(range(os.cpu_count() or 1))


def set_numa_node(node_id: int) -> bool:
    """Bind process memory allocation to a specific NUMA node on Linux."""
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            libnuma = ctypes.CDLL("libnuma.so.1")
            if hasattr(libnuma, "numa_run_on_node"):
                ret = libnuma.numa_run_on_node(int(node_id))
                return ret == 0
        except Exception:
            pass
    return False
