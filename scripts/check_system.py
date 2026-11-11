#!/usr/bin/env python
"""System information checker for Jalora optimization."""
import platform
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import torch
    CUDA_AVAILABLE = torch.cuda.is_available()
    CUDA_DEVICE_COUNT = torch.cuda.device_count() if CUDA_AVAILABLE else 0
    if CUDA_AVAILABLE:
        CUDA_DEVICE_NAME = torch.cuda.get_device_name(0)
        CUDA_TOTAL_MEM = torch.cuda.get_device_properties(0).total_memory / (1024**2)
    else:
        CUDA_DEVICE_NAME = None
        CUDA_TOTAL_MEM = None
except ImportError:
    CUDA_AVAILABLE = False
    CUDA_DEVICE_COUNT = 0
    CUDA_DEVICE_NAME = None
    CUDA_TOTAL_MEM = None

try:
    import psutil
    MEMORY_TOTAL_GB = psutil.virtual_memory().total / (1024**3)
    MEMORY_AVAILABLE_GB = psutil.virtual_memory().available / (1024**3)
    CPU_COUNT = psutil.cpu_count()
except ImportError:
    MEMORY_TOTAL_GB = 0
    MEMORY_AVAILABLE_GB = 0
    CPU_COUNT = 0

TOTAL_DISK, USED_DISK, FREE_DISK = shutil.disk_usage('C:\\')

print("=" * 60)
print("JALORA SYSTEM INFORMATION")
print("=" * 60)
print(f"OS: {platform.system()} {platform.release()}")
print(f"Python: {platform.python_version()}")
print(f"CPU cores: {CPU_COUNT}")
print(f"RAM: {MEMORY_TOTAL_GB:.1f} GB total, {MEMORY_AVAILABLE_GB:.1f} GB available")
print(f"Disk: {TOTAL_DISK/(1024**3):.1f} GB total, {FREE_DISK/(1024**3):.1f} GB free")
print(f"CUDA: {CUDA_AVAILABLE} ({CUDA_DEVICE_COUNT} devices)")
if CUDA_DEVICE_NAME:
    print(f"GPU: {CUDA_DEVICE_NAME} ({CUDA_TOTAL_MEM:.1f} MB VRAM)")
print("=" * 60)