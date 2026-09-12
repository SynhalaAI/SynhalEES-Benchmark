"""Kaggle / local environment detector.

Detects whether SynhalEES is running inside a Kaggle Notebook (free
T4/P100 GPUs) or on a local machine, and returns the best available
compute device.
"""

from __future__ import annotations

import os


def is_kaggle() -> bool:
    """Return True when running inside a Kaggle Notebook/Kernel."""
    return os.path.exists("/kaggle") or (
        os.environ.get("KAGGLE_KERNEL_RUN_TYPE") is not None
    )


def get_device(override: str | None = None) -> str:
    """Return the compute device to use: "cuda" when a GPU is available.

    Args:
        override: Optional forced value ("cuda", "cpu", "mps"). When given,
            it wins over auto-detection.
    """
    if override:
        return override
    if is_kaggle():
        # Kaggle notebooks expose CUDA GPUs (T4/P100).
        return "cuda"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


__all__ = ["get_device", "is_kaggle"]