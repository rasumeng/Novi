"""Phase 9 unified retrieval layer.

Public contracts live in ``base``; concrete source adapters wrap existing
stores. No adapter is consumed by the runtime yet (migration step 4+).
"""

from .base import RetrievedItem, RetrievalResult, RetrievalSource
from .file import FileRetrievalSource
from .knowledge import KnowledgeRetrievalSource
from .memory import MemoryRetrievalSource
from .project import ProjectRetrievalSource
from .web import WebRetrievalSource

__all__ = [
    "RetrievalSource",
    "RetrievedItem",
    "RetrievalResult",
    "MemoryRetrievalSource",
    "KnowledgeRetrievalSource",
    "WebRetrievalSource",
    "ProjectRetrievalSource",
    "FileRetrievalSource",
]
