"""Phase 8B research intelligence — pure, deterministic helpers.

Everything here is graph-owned logic over ALREADY-retrieved evidence and the
ALREADY-bound model handle passed in per-run state. No retrieval execution,
no model resolution/selection, no persistence, no configuration. The
RetrievalCoordinator stays the single budget authority; these helpers never
search — they only transform questions, accumulated evidence, and citations.

Bounds (all enforced structurally, not by convention):
  - sub-questions per decomposition
  - retained evidence bundles / total grounding characters
  - citation-manifest entries
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

MAX_SUB_QUESTIONS = 3
MAX_DECOMPOSE_RETRIES = 1        # parse attempts after the first try
MAX_EVIDENCE_BUNDLES = 4         # retained bundles per run (bounded state)
MAX_MANIFEST_ENTRIES = 12        # citation manifest entries
MAX_SUB_QUESTION_CHARS = 200
DEFAULT_GROUNDING_BUDGET_CHARS = 12000

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_MULTI_SIGNALS = (" and ", " vs ", " versus ", "compare", "difference",
                  "both ", " respectively", "; ")

# ── 8B.1 query decomposition ──────────────────────────────────────────────


def should_decompose(question: str) -> bool:
    """Whether decomposition is plausibly useful for this question.

    Deliberately conservative: trivial/single-clause questions are searched
    as-is without spending a model call. A question must carry a multi-part
    signal AND enough substance to be worth splitting.
    """
    q = (question or "").strip()
    if len(q) < 24:
        return False
    lowered = q.lower()
    if any(sig in lowered for sig in _MULTI_SIGNALS):
        return True
    # Multiple question marks ("Who X? When Y?") also signal multi-part asks.
    return lowered.count("?") >= 2


def parse_decomposition(raw: str) -> list[str]:
    """Deterministic JSON-contract parser for decomposition output.

    Contract: ``{"sub_questions": ["...", ...]}``. Returns [] when the model
    produced anything else (caller falls back to the original question).
    Never raises.
    """
    if not raw:
        return []
    match = _JSON_OBJ_RE.search(raw)
    if not match:
        return []
    try:
        obj = json.loads(match.group())
    except (json.JSONDecodeError, ValueError):
        return []
    items = obj.get("sub_questions") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        text = item.strip()[:MAX_SUB_QUESTION_CHARS]
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= MAX_SUB_QUESTIONS:
            break
    return out


def build_decompose_prompt(question: str) -> str:
    """The bounded deterministic prompt for the decomposition node."""
    return (
        "Decompose the research question into at most "
        f"{MAX_SUB_QUESTIONS} independent sub-questions that together fully "
        "cover it. Reply with ONLY JSON:\n"
        '{"sub_questions": ["...", "..."]}\n'
        "If the question is already single-focused, return it unchanged as "
        "the only element.\n\n"
        f"Question: {question}"
    )


# ── 8B.2 gap → refined query ─────────────────────────────────────────────


MAX_REFINED_TERMS = 3


def refine_query(original: str, gaps: list[str], grounding: str) -> str:
    """Derive the next search query from uncovered gap terms.

    Deterministic and entity-preserving (Phase 8 remediation, audit A): the
    subject of the original question always LEADS the refined query, so a
    refinement never degrades "What was Tesla's 2024 revenue?" into
    "revenue 2024" and loses the entity. Composition:

        [<first anchor term>] + [<up to two uncovered gaps>] + [<remaining
        anchors>] — hard-capped at MAX_REFINED_TERMS tokens.

    Gap terms already present in the grounding are dropped (defensive —
    evaluate pre-filters). With no effective gaps the original is returned
    untouched, which keeps the coordinator's duplicate gate exactly as
    effective as before refinement existed.
    """
    original = (original or "").strip()
    gaps = [g for g in (gaps or []) if g]
    if not gaps or not original:
        return original

    low_grounding = (grounding or "").lower()
    effective = [g for g in gaps if g.lower() not in low_grounding]
    if not effective:
        return original

    anchor_terms = _key_terms(original)
    # Full miss — evidence covers NONE of the question's own terms, so no
    # deterministic transform beats the original question.
    if not any(a in low_grounding for a in anchor_terms):
        return original

    # Entity-first refinement: the question's own subject stays in the query,
    # the uncovered aspect names what is still missing, and further anchors
    # pad remaining slots — timeframes (years) FIRST, since a missing fact is
    # usually period-bound. Bounded: never more than MAX_REFINED_TERMS terms,
    # so this can never become unbounded expansion.
    selected: list[str] = []
    if anchor_terms:
        selected.append(anchor_terms[0].lower())
    for g in effective[:2]:
        gl = g.lower()
        if len(selected) >= MAX_REFINED_TERMS:
            break
        if gl not in selected:
            selected.append(gl)
    rest = anchor_terms[1:]
    years = [a for a in rest if a.isdigit() and len(a) == 4]
    for a in years + [a for a in rest if a not in years]:
        if len(selected) >= MAX_REFINED_TERMS:
            break
        al = a.lower()
        if al not in selected:
            selected.append(al)

    refined = " ".join(dict.fromkeys(selected)).strip()
    if len(refined) < 3 or refined == original.lower():
        return original
    return refined


_WORD_RE = re.compile(r"[A-Za-z0-9]{2,}")
_STOPWORDS = frozenset({
    "what", "is", "the", "are", "how", "to", "in", "of", "for", "a", "an",
    "and", "or", "on", "at", "by", "with", "from", "do", "does", "can",
    "will", "would", "should", "could", "did", "has", "have", "had",
    "was", "were", "be", "been", "being", "get", "got", "am", "its",
    "it's", "that", "this", "these", "those", "i", "my", "me",
    "you", "your", "we", "our", "they", "them", "their", "he", "she",
    "him", "her", "his", "tell", "give", "show", "find", "help",
    "when", "where", "why", "which", "who", "whom",
})


def _key_terms(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall((text or "").lower())
            if t not in _STOPWORDS]


# ── 8B.3/8B.4 evidence accumulation + dedup ───────────────────────────────


def _url_identity(url: str) -> str:
    """Stable source identity: scheme-stripped host + path, no query/hash."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        return f"{host}{parsed.path.rstrip('/')}".lower()
    except Exception:
        return url.lower()


def accumulate_bundle(bundles: list, bundle) -> tuple[list, int]:
    """Accumulate one retrieved bundle into bounded cross-attempt state.

    Deduplicates by URL identity against every retained bundle, drops empty /
    failed bundles, and caps both bundle count and total result count.
    Returns ``(new_bundles, added_sources)``; the input list is not mutated.
    """
    kept = list(bundles or [])
    if bundle is None or getattr(bundle, "error", None):
        return kept, 0

    results = list(getattr(bundle, "results", None) or [])
    existing_urls: set[str] = set()
    total_results = 0
    for b in kept:
        for r in getattr(b, "results", None) or []:
            total_results += 1
            ident = _url_identity(getattr(r, "url", ""))
            if ident:
                existing_urls.add(ident)

    fresh = []
    for r in results:
        ident = _url_identity(getattr(r, "url", ""))
        if ident and ident in existing_urls:
            continue
        if ident:
            existing_urls.add(ident)
        fresh.append(r)

    merged_text = getattr(bundle, "merged_text", "") or ""
    new_bundle = bundle
    if len(fresh) != len(results):
        # Rebuild a trimmed copy so deduped sources cannot re-enter grounding.
        import copy
        new_bundle = copy.copy(bundle)
        try:
            new_bundle.results = fresh
            new_bundle.source_count = len(
                [r for r in fresh
                 if getattr(r, "full_text", "") or getattr(r, "snippet", "")])
        except Exception:
            new_bundle = bundle

    if not fresh and not merged_text:
        return kept, 0

    kept.append(new_bundle)
    while len(kept) > MAX_EVIDENCE_BUNDLES:
        dropped = kept.pop(0)
        for r in getattr(dropped, "results", None) or []:
            ident = _url_identity(getattr(r, "url", ""))
            existing_urls.discard(ident)
    return kept, len(fresh)


# ── 8B.6 citation manifest ────────────────────────────────────────────────


@dataclass(frozen=True)
class ManifestEntry:
    source_id: str
    url: str
    title: str


@dataclass(frozen=True)
class CitationManifest:
    entries: tuple[ManifestEntry, ...] = ()

    def ids(self) -> tuple[str, ...]:
        return tuple(e.source_id for e in self.entries)

    def to_prompt(self) -> str:
        lines = ["Citable sources:"]
        for e in self.entries:
            title = e.title or e.url or e.source_id
            lines.append(f"[{e.source_id}] {title} — {e.url}")
        return "\n".join(lines)


def build_manifest(bundles: list) -> CitationManifest:
    """Build the citation manifest from ACTUAL retrieved results only.

    Deterministic: enumerates deduplicated sources across accumulated bundles
    in retrieval order. Never consults the LLM — invented citations are
    impossible because validation resolves against exactly this manifest.
    """
    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    n = 0
    for b in bundles or []:
        for r in getattr(b, "results", None) or []:
            url = (getattr(r, "url", "") or "").strip()
            ident = _url_identity(url)
            if not url or (ident and ident in seen):
                continue
            if ident:
                seen.add(ident)
            n += 1
            entries.append(ManifestEntry(
                source_id=f"S{n}", url=url,
                title=(getattr(r, "title", "") or "").strip(),
            ))
            if len(entries) >= MAX_MANIFEST_ENTRIES:
                return CitationManifest(entries=tuple(entries))
    return CitationManifest(entries=tuple(entries))


# ── 8B.7 context-window-aware grounding ──────────────────────────────────


def context_budget_for(model, default: int = DEFAULT_GROUNDING_BUDGET_CHARS) -> int:
    """Grounding character budget derived from the bound model's DESCRIPTIVE
    context metadata. Informational only — this can never influence selection
    (the model was already resolved upstream); an unknown capability yields
    the conservative default."""
    ctx_len = 0
    for holder in (model, getattr(model, "model_kwargs", None)):
        for attr in ("context_length", "context_window", "num_ctx"):
            try:
                val = getattr(holder, attr, None)
            except Exception:
                val = None
            if isinstance(val, int) and 0 < val <= 10_000_000:
                ctx_len = max(ctx_len, val)
    # ~4 chars/token heuristic, keep half the window for prompt+answer.
    budget = int(ctx_len * 4 * 0.5) if ctx_len else 0
    return min(budget, 60_000) or default


def truncate_grounding(bundles: list, budget_chars: int) -> tuple[str, bool]:
    """Merge accumulated bundles into grounding text within the budget.

    Newest-first (most relevant attempt wins), whole-source chunks are kept
    until the budget is exhausted, then truncated with an explicit marker.
    Returns ``(grounding_text, truncated)``.
    """
    budget = max(200, int(budget_chars))
    chunks: list[tuple[int, str]] = []
    n_sources = 0
    for b in reversed(list(bundles or [])):  # newest first
        for r in getattr(b, "results", None) or []:
            n_sources += 1
            content = (getattr(r, "full_text", "") or
                       getattr(r, "snippet", "") or "")
            content = content.strip()
            if not content:
                continue
            title = (getattr(r, "title", "") or "").strip()
            url = (getattr(r, "url", "") or "").strip()
            header = f"### {title}\n{url}\n" if title or url else ""
            chunks.append((n_sources, f"{header}{content}\n"))
    # Fallback: bundles without structured results still carry merged text.
    if not chunks:
        texts = [(i, (getattr(b, "merged_text", "") or ""))
                 for i, b in enumerate(reversed(list(bundles or [])), 1)]
        chunks = [(i, t) for i, t in texts if t.strip()]

    parts: list[str] = []
    used = 0
    truncated = False
    for idx, chunk in chunks:
        remaining = budget - used
        if remaining <= 200:
            truncated = True
            break
        if len(chunk) > remaining:
            parts.append(chunk[:remaining].rstrip() +
                         "\n…[source truncated to fit context]\n")
            used += remaining
            truncated = True
        else:
            parts.append(chunk)
            used += len(chunk)
    return "\n".join(parts), truncated


# ── synthesis support ─────────────────────────────────────────────────────


_CONFLICT_HEADER = "Notable disagreements between sources"


def build_synthesis_extras(manifest: CitationManifest,
                           conflicts: list[dict],
                           truncated: bool) -> str:
    """Additive synthesis-prompt block: citations + conflicts + truncation note."""
    blocks: list[str] = []
    if manifest.entries:
        blocks.append(manifest.to_prompt() +
                      "\nCite sources inline using their [S#] identifiers.")
    if conflicts:
        lines = [_CONFLICT_HEADER + " (present them honestly):"]
        for c in conflicts:
            stmts = " | ".join(c.get("statements") or ())
            srcs = ",".join(c.get("sources") or ())
            sev = c.get("severity", "")
            line = f"- ({sev}) {stmts}"
            if srcs:
                line += f" [{srcs}]"
            res = c.get("resolution")
            if res:
                line += f" — {res}"
            lines.append(line)
        blocks.append("\n".join(lines))
    if truncated:
        blocks.append("Note: evidence was truncated to fit the context window; "
                      "cite only what is present.")
    return "\n\n".join(blocks)


# ── 8B.8 validation ───────────────────────────────────────────────────────

# Explicit insufficiency/uncertainty phrase families (Phase 8 remediation,
# audit C). Deterministic and bounded — ordinary hedging ("may", "possibly",
# "it seems") is deliberately NOT here: cautious wording alone must never
# classify an answer as insufficient. Contractions are normalized away at
# match time so "don't"/"dont" behave identically.
_INSUFFICIENT_MARKERS = (
    # explicit lack of evidence
    "insufficient evidence", "not enough evidence", "evidence does not",
    "no reliable evidence", "no reliable information",
    "evidence is insufficient", "insufficient to answer", "lacks evidence",
    # inability to verify / confirm
    "could not be verified", "cannot be confirmed", "unable to verify",
    "unable to confirm", "could not verify", "cannot verify",
    "information could not be verified", "not possible to verify",
    # first-person uncertainty
    "i am not certain", "i'm not certain", "i am not sure",
    "i'm not sure", "we are not certain", "i don't know",
    "i do not know", "i don't have reliable information",
    "i do not have reliable information", "i couldn't verify",
    "i could not verify", "i was unable to verify", "i cannot determine",
    "i'm unsure", "i am unsure",
    # availability / coverage
    "no information available", "information is unavailable",
    "only partial information",
)

_NORMALIZED_MARKERS = tuple(m.replace("'", "") for m in _INSUFFICIENT_MARKERS)


def discloses_insufficiency(answer: str) -> bool:
    low = (answer or "").lower().replace("'", "")
    return any(m in low for m in _NORMALIZED_MARKERS)


_CITE_RE = re.compile(r"\[(S\d+)\]")


def cited_source_ids(answer: str) -> list[str]:
    """Source ids actually cited in the answer, in order, deduplicated."""
    seen: list[str] = []
    for m in _CITE_RE.findall(answer or ""):
        if m not in seen:
            seen.append(m)
    return seen


def validate_answer(answer: str, manifest: CitationManifest,
                    has_evidence: bool) -> dict:
    """Structural validation — deliberately forgiving on wording.

    Checks (all resolvable WITHOUT semantic judgement):
      - citations_used:   whether the answer cites manifest sources
      - invalid_citations: cited ids absent from the manifest
      - disclosed_insufficient: honest insufficiency disclosure
      - status: sufficient | insufficient | empty
    An answer over real evidence with zero citations is NOT auto-failed;
    it merely records citations_used=False.
    """
    answer = answer or ""
    if not answer.strip():
        return {"status": "empty", "citations_used": False,
                "invalid_citations": [], "disclosed_insufficient": False}
    valid = set(manifest.ids())
    cited = cited_source_ids(answer)
    invalid = [c for c in cited if c not in valid]
    disclosed = discloses_insufficiency(answer)
    if disclosed:
        status = "insufficient"
    elif has_evidence and not cited:
        # Evidence exists but nothing cited: acceptable, but flagged.
        status = "sufficient"
    else:
        status = "sufficient"
    return {
        "status": status,
        "citations_used": bool(cited),
        "invalid_citations": invalid[:8],
        "disclosed_insufficient": disclosed,
    }


def acknowledges_conflicts(answer: str, conflicts: list[dict],
                           min_tokens: int = 2) -> bool:
    """Whether the answer substantively references a surfaced conflict.

    Deterministic: counts significant shared tokens between the answer and
    the conflict statements. Wording differences allowed; pure silence on
    every conflict fails.
    """
    if not conflicts:
        return True  # nothing to acknowledge
    low = (answer or "").lower()
    for c in conflicts:
        tokens = [t for t in _key_terms(" ".join(c.get("statements") or []))
                  if len(t) >= 5]
        if sum(1 for t in tokens if t in low) >= min_tokens:
            return True
    return False


# ── conflict collection (reuses cozmo/evidence infrastructure) ────────────

_MAX_CONFLICTS = 8
_MAX_CONFLICT_FACTS = 40


def collect_conflicts(bundles: list, processor=None) -> list[dict]:
    """Detect contradictions across accumulated evidence.

    Reuses the EXISTING ConflictDetector via EvidenceProcessor for per-source
    fact extraction, then runs detection ONCE over the combined fact list
    (Phase 8 remediation, audit D): processing each bundle in isolation can
    never see a cross-source contradiction, which starved the conflict
    pipeline on realistic multi-source evidence. Facts carry their source
    attribution through extraction. Output is descriptive dicts; no automatic
    truth decision beyond what the deterministic confidence-based resolver
    already provides. Bounded output.
    """
    out: list[dict] = []
    if processor is None:
        from ..evidence.processor import EvidenceProcessor
        processor = EvidenceProcessor()
    from ..evidence.conflicts import ConflictDetector

    facts: list = []
    for b in bundles or []:
        try:
            ctx = processor.process(b)
        except Exception:
            continue
        facts.extend(getattr(ctx, "facts", ()) or ())
        if len(facts) >= _MAX_CONFLICT_FACTS:
            break

    for c in ConflictDetector().detect(facts[:_MAX_CONFLICT_FACTS]):
        entry = {
            "statements": list(c.statements)[:2],
            "sources": list(c.sources)[:4],
            "severity": c.severity,
            "resolution": c.resolution,
        }
        if entry not in out:
            out.append(entry)
        if len(out) >= _MAX_CONFLICTS:
            break
    return out
