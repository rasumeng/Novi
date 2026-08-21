"""ModelRuntime — thin boundary between Cozmo's resolved model selection and LangChain.

Cozmo decides WHAT model to use. This package turns the ALREADY-RESOLVED
selection into a LangChain runnable/model through the existing provider layer.
It performs no selection of any kind.
"""

from .factory import ModelRuntime, ResolvedModel

__all__ = ["ModelRuntime", "ResolvedModel"]