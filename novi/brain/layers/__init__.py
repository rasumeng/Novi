"""Brain layers — per-domain knowledge managers.

Each layer owns its store and its domain invariants. Layers never import each
other; the Brain coordinates cross-layer writes and emits domain events.
"""
