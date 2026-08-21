"""LangGraph workflow graphs (Phase 7 Stage 3).

Graphs RECEIVE an already-constructed LangChain model/runnable from the Cozmo
runtime boundary. They never resolve, recommend, select, substitute, fall
back, or persist models — and never read or write configuration.

See ``tests/test_architecture.py`` Guard 5 (graph import boundary).
"""

from .coding_graph import CodingGraph
from .research_graph import ResearchGraph

__all__ = ["ResearchGraph", "CodingGraph"]