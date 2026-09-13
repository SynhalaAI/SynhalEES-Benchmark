"""SynhalEES: The Multimodal Sinhala Cultural Benchmark.

Tri-modal (Text, Vision, Audio) benchmark for the Sinhala linguistic and
cultural sphere. The public API (``SynhalEESBenchmark``) is implemented in a
follow-up milestone — this package ships environment helpers and Pillar-First data loaders.
"""

from . import data
from .env import get_device, is_kaggle

__version__ = "0.1.0"
__all__ = ["data", "get_device", "is_kaggle"]