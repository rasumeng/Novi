"""Evidence processing subsystem (Phase 7).

Transforms raw retrieved evidence into trusted, structured evidence with
quality signals. ``EvidenceProcessor`` wraps ``EvidenceCollector`` — it
consumes ``EvidenceBundle`` and never performs retrieval itself.
"""

from .compressor import CompressionResult, ContextCompressor
from .confidence import ConfidenceAssessor
from .conflicts import ConflictDetector
from .context import (
    EvidenceConfig,
    EvidenceContext,
    Fact,
    RankingConfig,
    Source,
    Conflict,
)
from .extractor import FactExtractor
from .processor import EvidenceProcessor
from .ranking import SourceRanking

__all__ = [
    "CompressionResult",
    "ConfidenceAssessor",
    "Conflict",
    "ConflictDetector",
    "ContextCompressor",
    "EvidenceConfig",
    "EvidenceContext",
    "EvidenceProcessor",
    "Fact",
    "FactExtractor",
    "RankingConfig",
    "Source",
    "SourceRanking",
]
