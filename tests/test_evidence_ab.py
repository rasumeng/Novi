"""Evidence A/B evaluation tests (Phase 7 measurement tooling).

Covers: corpus loading, fake-client run mechanics, recall invariants,
verdict semantics (PASS / FAIL), cross-run comparison, save/load round-trip,
and the evidence-compare CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

from cozmo.evaluation import (
    EvidenceABReport,
    EvidenceDataset,
    compare_evidence_ab,
    run_evidence_ab,
)
from cozmo.evaluation.evidence_ab import MODES, REGRESSION_TOLERANCE

CORPUS_PATH = Path(__file__).parent / "evidence_corpus.json"


class FakeClient:
    """Deterministic model: answers from grounding, judges by term presence.

    An answer is correct when every expected term appears verbatim in the
    grounding. Judges ``1`` iff the answer is grounded in the given context.
    """

    def __init__(self, expected_by_query: dict):
        self._expected = expected_by_query
        self.calls: list[str] = []

    def invoke(self, messages):
        prompt = messages[0].content
        self.calls.append(prompt)
        if prompt.startswith("Answer"):
            grounding = re.search(r"<grounding>\n(.*?)\n</grounding>", prompt, re.S)
            query = re.search(r"Question: (.*)\nAnswer:", prompt, re.S)
            expected = self._expected.get(query.group(1).strip(), ())
            g = (grounding.group(1) if grounding else "").lower()
            if expected and all(t in g for t in expected):
                return " ".join(expected)
            return "I don't know"
        query = re.search(r"Question: (.*?)\nContext:", prompt, re.S)
        ctx = re.search(r"Context:\n(.*?)\nAnswer:", prompt, re.S)
        answer = re.search(r"Answer:\n(.*)$", prompt, re.S)
        if answer.group(1).strip() == "I don't know":
            return "0"
        expected = self._expected.get(query.group(1).strip(), ())
        return "1" if expected and all(t in (ctx.group(1) or "").lower() for t in expected) else "0"


def _expected_by_query(dataset: EvidenceDataset) -> dict:
    return {c.query: c.expected for c in dataset.cases}


def _run(dataset: EvidenceDataset):
    client = FakeClient(_expected_by_query(dataset))
    return run_evidence_ab(dataset, client, model_name="fake"), client


def _bern_case():
    """Partial-extraction drop: one passage retained, key entity (Bern) excluded."""
    from cozmo.evaluation.evidence_ab import EvidenceCase

    return EvidenceCase(
        id="C-98",
        query="What is the capital of Switzerland and how many official languages does it have?",
        expected=("bern", "four"),
        sources=(
            {
                "title": "Switzerland",
                "url": "https://en.wikipedia.org/wiki/Switzerland",
                "snippet": "",
                "full_text": (
                    "Switzerland has four official languages: German, French, "
                    "Italian, and Romansh. The parliament meets in Bern, a "
                    "city on the Aare river. Swiss neutrality is respected by "
                    "neighboring states."
                ),
            },
        ),
    )


# ── dataset loading ────────────────────────────────────────────────────


def test_corpus_loads():
    ds = EvidenceDataset.from_json(CORPUS_PATH)
    assert len(ds) == 9
    ids = [c.id for c in ds.cases]
    assert len(ids) == len(set(ids)), "corpus ids must be unique"
    assert ds.cases[0].id.startswith("EV-")
    assert all(c.query and c.sources for c in ds.cases)


# ── run mechanics ──────────────────────────────────────────────────────


def test_run_mechanics_and_recall():
    ds = EvidenceDataset.from_json(CORPUS_PATH)
    report, client = _run(ds)
    agg = report.aggregate()
    assert agg["cases"] == len(ds)
    assert agg["compression"] > 0
    assert agg["recall"]["N_none"] == 0.0
    assert all(c.confidence >= 0.0 for c in report.cases)
    assert client.calls, "fake client must receive prompts"


def test_b_never_beats_a_on_corpus():
    """B summary sentences are drawn from merged_text, so A recall >= B recall."""
    ds = EvidenceDataset.from_json(CORPUS_PATH)
    report, _ = _run(ds)
    a = report.aggregate()["recall"]["A_merged"]
    b = report.aggregate()["recall"]["B_context"]
    assert b <= a


def test_verdict_pass_when_b_retains():
    ds = EvidenceDataset.from_json(CORPUS_PATH)
    single = EvidenceDataset(name="single", cases=[ds.cases[0]])  # EV-01
    report, _ = _run(single)
    agg = report.aggregate()
    assert agg["recall"]["A_merged"] == 1.0
    assert agg["recall"]["B_context"] == 1.0
    assert report.verdict() == "PASS"


def test_verdict_fails_when_b_drops():
    ds = EvidenceDataset(name="drop", cases=[_bern_case()])
    report, _ = _run(ds)
    agg = report.aggregate()
    assert agg["recall"]["A_merged"] == 1.0
    assert agg["recall"]["B_context"] == 0.0
    assert report.verdict() == "FAIL"


# ── comparison and round-trip ──────────────────────────────────────────


def test_compare_identical_reports_pass(tmp_path):
    ds = EvidenceDataset.from_json(CORPUS_PATH)
    report, _ = _run(ds)
    path = tmp_path / "base.json"
    report.save(path)
    comp = compare_evidence_ab(path, path)
    assert comp.passed
    for mode in MODES:
        assert comp.deltas[mode]["recall"]["delta"] == 0.0
        assert comp.deltas[mode]["grounded"]["delta"] == 0.0
    loaded = EvidenceABReport.from_json(path)
    assert loaded.aggregate() == report.aggregate()


def test_compare_flags_b_recall_regression(tmp_path):
    ds = EvidenceDataset.from_json(CORPUS_PATH)
    good = EvidenceDataset(name="good", cases=[ds.cases[0]])
    bad = EvidenceDataset(name="bad", cases=[_bern_case()])
    base, _ = _run(good)
    cand, _ = _run(bad)
    comp = compare_evidence_ab(base, cand)
    assert not comp.passed
    assert (
        comp.deltas["B_context"]["recall"]["delta"] < -REGRESSION_TOLERANCE
    )


# ── CLI ────────────────────────────────────────────────────────────────


def test_cli_evidence_compare(tmp_path):
    from cozmo.evaluation import __main__ as m

    ds = EvidenceDataset.from_json(CORPUS_PATH)
    report, _ = _run(ds)
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    report.save(base)
    report.save(cand)
    rc = m.main(["evidence-compare", str(base), str(cand)])
    assert rc == 0
