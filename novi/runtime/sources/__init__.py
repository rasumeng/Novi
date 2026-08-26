"""Unified retrieval layer.

Public contracts live in ``base``; concrete source adapters wrap existing
stores (Memory, Knowledge, Project, Web) and the layered tier adapters
(Identity, Scenario). Memory/Knowledge/Project sources are Brain-aware:
when a Brain is wired they delegate to it instead of the store.
"""

from .base import MergedRetrievalResult, RetrievedItem, RetrievalResult, RetrievalSource
from .file import FileRetrievalSource
from .identity import IdentityRetrievalSource
from .knowledge import KnowledgeRetrievalSource
from .memory import MemoryRetrievalSource
from .project import ProjectRetrievalSource
from .scenario import ScenarioRetrievalSource
from .web import WebRetrievalSource

__all__ = [
    "RetrievalSource",
    "RetrievedItem",
    "RetrievalResult",
    "MergedRetrievalResult",
    "MemoryRetrievalSource",
    "KnowledgeRetrievalSource",
    "IdentityRetrievalSource",
    "ScenarioRetrievalSource",
    "WebRetrievalSource",
    "ProjectRetrievalSource",
    "FileRetrievalSource",
]
