# ruff: noqa: TID251 - benchmark CLI
"""Shared paths and helpers for the B-roll retrieval benchmark (no client data in the repo)."""

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

#: Every output (pool, queries, frames, embeddings, results) lives here, outside the repo (NDA).
OUT = Path(os.environ.get("BROLL_BENCH_OUT", "C:/Users/L14/.media_house_scratch/benchmarks/broll"))
HOME = Path(os.environ.get("BROLL_BENCH_HOME", "C:/Users/L14/.media_house_scratch/baseline"))
#: Owner's hand brief: its ``cover`` entries are the human labels.
BRIEF = HOME / "_qa" / "v8" / "run.json"
#: Optional extra labels (same shape as ``cover`` entries plus ``provenance``).
EXTRA = OUT / "labels_extra.json"
MODELS = OUT / "models"
FRAMES = OUT / "frames"
#: Frames per shot, evenly spread inside the shot (centres of K equal parts).
FRAMES_PER_SHOT = 5
#: The pipeline cuts the passage query at 200 characters (broll retrieval.query_text).
QUERY_CHARS = 200

MCLIP = (
    "sentence-transformers/clip-ViT-B-32-multilingual-v1",
    "58edf8cada9e398793dca955574a48cbb7f18be2",
)
SIGLIP2 = ("google/siglip2-base-patch16-224", "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2")
CLIP = ("openai/clip-vit-base-patch32", "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268")


def model_dir(repo_rev: tuple[str, str]) -> Path:
    """Local copy downloaded with curl (the Python TLS stack is blocked by the AV proxy here)."""
    return MODELS / f"{repo_rev[0].replace('/', '__')}@{repo_rev[1]}"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def memory_mb() -> tuple[float, float]:
    """(current, peak) working set of this process in MB; Windows only (psutil is not installed)."""
    if sys.platform != "win32":
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        return peak, peak
    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Counters),
        wintypes.DWORD,
    ]
    if not kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError(ctypes.get_last_error(), "K32GetProcessMemoryInfo failed")
    return counters.WorkingSetSize / 2**20, counters.PeakWorkingSetSize / 2**20
