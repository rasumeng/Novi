"""ModelRuntime — thin boundary between Novi's resolved model selection and LangChain.

Novi decides WHAT model to use. This package turns the ALREADY-RESOLVED
selection into a LangChain runnable/model through the existing provider layer.
It performs no selection of any kind.
"""

from .factory import ModelRuntime, ResolvedModel

__all__ = ["ModelRuntime", "ResolvedModel"]