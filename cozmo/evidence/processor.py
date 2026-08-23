"""EvidenceProcessor — post-collection evidence refinement pipeline.

Orchestrates: Source conversion+ranking → fact extraction → conflict detection
→ confidence assessment → context compression. Wraps/complements
``EvidenceCollector``: it consumes an ``EvidenceBundle`` and never mutates it,
never performs retrieval, and leaves ``bundle.merged_text`` untouched so both
contracts coexist during migration.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.evidence import EvidenceBundle

from .compressor import ContextCompressor
from .confidence import ConfidenceAssessor
from .conflicts import ConflictDetector
from .context import EvidenceConfig, EvidenceContext, RankingConfig, Source
from .extractor import FactExtractor
from .ranking import SourceRanking

log = logging.getLogger("cozmo.evidence.processor")

_WORD = re.compile(r"[a-z0-9]{3,}")

# Freshness string parsers (SearchResult.freshness). Returns datetime or None.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_RELATIVE = re.compile(r"(\d+)\s+(day|week|month|year)s?\s+ago")
_MINUTE = re.compile(r"(\d+)\s+minute")
_HOUR = re.compile(r"(\d+)\s+hour")

_SOURCE_TYPE_BY_DOMAIN = {
    "github.com": "code",
    "stackoverflow.com": "forum",
    "wikipedia.org": "reference",
    "en.wikipedia.org": "reference",
    "docs.python.org": "documentation",
    "readthedocs.io": "documentation",
    "developer.mozilla.org": "documentation",
    "mdn.dev": "documentation",
    "youtube.com": "video",
    "youtu.be": "video",
}

# Known strong textual domains → authority boost
_AUTHORITY_DOMAINS = {
    "wikipedia.org", "en.wikipedia.org", "github.com", "stackoverflow.com",
    "docs.python.org", "mdn.dev", "developer.mozilla.org", "readthedocs.io",
}

_RAW_TEXT_CAP = 12000


class EvidenceProcessor:
    """Consumes an EvidenceBundle, produces an immutable EvidenceContext."""

    def __init__(self, config: EvidenceConfig | None = None):
        self._cfg = config or EvidenceConfig()
        self._ranking = SourceRanking()
        self._extractor = FactExtractor(
            min_confidence=self._cfg.min_fact_confidence,
            max_facts=self._cfg.max_facts,
            extractor=self._cfg.extractor,
        )
        self._conflicts = ConflictDetector()
        self._confidence = ConfidenceAssessor()
        self._compressor = ContextCompressor(budget_chars=self._cfg.budget_chars)

    def process(self, bundle: EvidenceBundle) -> EvidenceContext:
        """Refine a collected bundle into structured, trusted evidence.

        Returns a low-trust fallback context (``fallback=True``, summary =
        ``bundle.merged_text``) when the bundle failed or extraction confidence
        was too low. The bundle itself is never modified.
        """
        if bundle.error or not bundle.results:
            return EvidenceContext(
                query=bundle.query,
                summary=bundle.merged_text or "",
                fallback=True,
            )

        sources = self._build_sources(bundle)
        raw_text = self._raw_text(bundle)
        facts = self._extract_with_sources(bundle)
        conflicts = self._conflicts.detect(facts)
        confidence = self._confidence.assess(facts, sources, conflicts)

        if not facts:
            log.info("evidence fallback for '%s': no facts above threshold",
                     bundle.query)
            return EvidenceContext(
                query=bundle.query,
                facts=(),
                sources=tuple(sources),
                conflicts=conflicts,
                confidence=confidence,
                summary=bundle.merged_text or "",
                fallback=True,
            )

        result = self._compressor.compress(raw_text, facts, bundle.query)
        return EvidenceContext(
            query=bundle.query,
            facts=facts,
            sources=tuple(sources),
            conflicts=conflicts,
            confidence=confidence,
            summary=result.summary,
        )

    # ── pipeline stages ─────────────────────────────────────────────────

    def _build_sources(self, bundle: EvidenceBundle) -> list[Source]:
        query_terms = self._terms(bundle.query)
        sources = []
        seen_urls: set[str] = set()
        for r in bundle.results:
            domain = _domain(r.url)
            # Same source collected twice is one source: keep the first
            # occurrence, never inflate corroboration with duplicates.
            if r.url:
                if r.url in seen_urls:
                    continue
                seen_urls.add(r.url)
            text = (r.title + " " + r.snippet).lower()
            relevance = self._overlap(text, query_terms)
            sources.append(
                Source(
                    url=r.url,
                    title=r.title,
                    authority=_authority(domain, r.url),
                    relevance=relevance,
                    freshness=_parse_freshness(r.freshness),
                    source_type=_source_type(domain),
                )
            )
        config = self._cfg.ranking or RankingConfig()
        return self._ranking.rank(sources, config)

    def _extract_with_sources(self, bundle: EvidenceBundle) -> tuple:
        """Extract facts per-source so each Fact carries its source URLs,
        then merge duplicates across sources."""
        from dataclasses import replace

        raw_facts: list = []
        for r in bundle.results[:5]:
            content = r.full_text if r.full_text else r.snippet
            if not content:
                continue
            facts, _ = self._extractor.extract(content[:3000], bundle.query)
            raw_facts.extend(replace(f, sources=(r.url,)) for f in facts)
        return self._extractor.merge_facts(raw_facts)

    def _raw_text(self, bundle: EvidenceBundle) -> str:
        parts = []
        total = 0
        for r in bundle.results:
            if len(parts) >= 5:
                break
            content = r.full_text if r.full_text else r.snippet
            if not content:
                continue
            chunk = content[:3000]
            parts.append(chunk)
            total += len(chunk)
            if total >= _RAW_TEXT_CAP:
                break
        return "\n\n".join(parts)

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {w for w in _WORD.findall(text.lower())}

    @staticmethod
    def _overlap(text: str, query_terms: set[str]) -> float:
        if not query_terms:
            return 0.5
        hits = sum(1 for t in query_terms if t in text)
        return min(1.0, hits / len(query_terms))


def _domain(url: str) -> str:
    """Extract netloc from URL, stripping www."""
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return netloc.removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def _source_type(domain: str) -> str:
    return _SOURCE_TYPE_BY_DOMAIN.get(domain, "web")


def _authority(domain: str, url: str) -> float:
    if domain in _AUTHORITY_DOMAINS:
        return 0.9
    if "github.com" in domain:
        return 0.8
    return 0.5


def _parse_freshness(freshness: str | None) -> datetime | None:
    """Parse a freshness hint into a UTC datetime.

    Determinism contract (evidence parity): identical inputs must produce
    structurally equal EvidenceContext objects. Wall-clock-relative strings
    ("2 days ago") are therefore quantized to whole UTC days — freshness
    ranking operates on day granularity anyway, and sub-second wall-clock
    noise must never leak into the immutable context.
    """
    if not freshness:
        return None

    def _day(dt: datetime) -> datetime:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        m = _ISO_DATE.match(freshness.strip())
        if m:
            return datetime.fromisoformat(m.group(0)).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    now = _day(datetime.now(timezone.utc))
    m = _RELATIVE.search(freshness.lower())
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = {"day": n, "week": 7 * n, "month": 30 * n, "year": 365 * n}.get(unit, n)
        return now - timedelta(days=days)
    m = _MINUTE.search(freshness.lower())
    if m:
        return now
    m = _HOUR.search(freshness.lower())
    if m:
        return now
    return None
