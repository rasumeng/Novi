"""Evidence rendering — deterministic model-facing text for EvidenceContext.

The missing half of the live-path integration: ``EvidenceProcessor`` produces
structured facts/sources/conflicts, but its ``summary`` intentionally carries
no source identity (URLs live in the structured contract). The runtime's
existing grounding format preserves URLs and titles; any processed replacement
must preserve them too. This renderer closes that parity gap.

Pure function over the frozen evidence contracts: no I/O, no stores, no
model calls, deterministic ordering (input order preserved — facts arrive in
extraction order, sources in ranked order).
"""

from __future__ import annotations

from .context import EvidenceContext

_MAX_FACTS = 12
_MAX_SOURCES = 8
_MAX_CONFLICTS = 4


def render_evidence_context(ctx: EvidenceContext) -> str:
    """Render a non-fallback EvidenceContext into grounding text.

    Preserves every parity requirement the raw merged_text satisfied — source
    identity (titles + URLs), relevant content (facts + compressed passages),
    ordering — while adding what the flat text never had: confidence header,
    conflict surfacing with resolutions, and per-fact source attribution.

    Returns "" when the context is a fallback or carries no usable content;
    callers keep their existing raw-text behavior in that case.
    """
    if ctx is None or ctx.fallback:
        return ""
    facts = ctx.facts[:_MAX_FACTS]
    if not facts and not ctx.sources:
        return ""

    url_to_ref = {
        s.url: i for i, s in enumerate(ctx.sources[:_MAX_SOURCES], 1)
        if s.url
    }

    parts = [f"**Evidence Summary** (confidence: {ctx.confidence:.2f})"]
    if ctx.query:
        parts.append(f"Query: {ctx.query}")

    if facts:
        fact_lines = []
        for f in facts:
            refs = " ".join(
                f"[S{url_to_ref[url]}]" for url in f.sources if url in url_to_ref
            )
            fact_lines.append(f"- {f.statement}{(' ' + refs) if refs else ''}")
        parts.append("\nVerified facts:\n" + "\n".join(fact_lines))

    conflicts = [
        c for c in ctx.conflicts[:_MAX_CONFLICTS] if c.statements
    ]
    if conflicts:
        lines = []
        for c in conflicts:
            joined = " vs ".join(c.statements)
            resolution = f" — {c.resolution}" if c.resolution else ""
            lines.append(f"- [{c.severity}] {joined}{resolution}")
        parts.append("\nConflicting claims:\n" + "\n".join(lines))

    sources = [s for s in ctx.sources[:_MAX_SOURCES] if s.url]
    if sources:
        lines = []
        for i, s in enumerate(sources, 1):
            title = s.title or s.url
            lines.append(f"[S{i}] {title} — {s.url} ({s.source_type})")
        parts.append("\nSources:\n" + "\n".join(lines))

    return "\n".join(parts).strip()
