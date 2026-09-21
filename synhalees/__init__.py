"""SynhalEES: The Multimodal Sinhala Cultural Benchmark.

Tri-modal (Text, Vision, Audio) benchmark for the Sinhala linguistic and
cultural sphere. Ships Pillar-First data loaders (:mod:`synhalees.data`),
model adapters (:mod:`synhalees.models`), scoring engines
(:mod:`synhalees.evaluators`), and the checkpointed runner
:class:`synhalees.runner.SynhalEESBenchmark` (imported lazily).
"""

from . import data
from .env import get_device, is_kaggle

__version__ = "0.1.0"
__all__ = ["SynhalEESBenchmark", "data", "get_device", "is_kaggle"]


def __getattr__(name):
    if name == "SynhalEESBenchmark":
        from .runner import SynhalEESBenchmark

        return SynhalEESBenchmark
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")