# JSON Schemas

This document defines the JSON schemas used by skill-creator.

---

## evals.json

Defines the evals for a skill. Located at `evals/evals.json` within the skill directory.

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's example prompt",
      "expected_output": "Description of expected result",
      "files": ["evals/files/sample1.pdf"],
      "expectations": [
        "The output includes X",
        "The skill used script Y"
      ]
    }
  ]
}
```

---

## grading.json

Output from the grader agent. Located at `<run-dir>/grading.json`.

```json
{
  "expectations": [
    {"text": "The output includes the name 'John Smith'", "passed": true, "evidence": "Found in transcript Step 3"},
    {"text": "The spreadsheet has a SUM formula in cell B10", "passed": false, "evidence": "No spreadsheet was created."}
  ],
  "summary": {"passed": 2, "failed": 1, "total": 3, "pass_rate": 0.67},
  "execution_metrics": {"tool_calls": {"Read": 5}, "total_tool_calls": 15, "total_steps": 6, "errors_encountered": 0, "output_chars": 12450, "transcript_chars": 3200},
  "timing": {"executor_duration_seconds": 165.0, "grader_duration_seconds": 26.0, "total_duration_seconds": 191.0},
  "claims": [{"claim": "The form has 12 fillable fields", "type": "factual", "verified": true, "evidence": "Counted 12 fields in field_info.json"}],
  "user_notes_summary": {"uncertainties": [], "needs_review": [], "workarounds": []},
  "eval_feedback": {"suggestions": [], "overall": "No suggestions, evals look solid"}
}
```

---

## timing.json

Wall clock timing for a run. Located at `<run-dir>/timing.json`.

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332,
  "total_duration_seconds": 23.3,
  "executor_start": "2026-01-15T10:30:00Z",
  "executor_end": "2026-01-15T10:32:45Z",
  "executor_duration_seconds": 165.0,
  "grader_start": "2026-01-15T10:32:46Z",
  "grader_end": "2026-01-15T10:33:12Z",
  "grader_duration_seconds": 26.0
}
```

---

## benchmark.json

Output from aggregate_benchmark.py. Located at `<workspace>/iteration-N/benchmark.json`.

```json
{
  "metadata": {"skill_name": "pdf", "skill_path": "/path/to/pdf", "executor_model": "claude-sonnet-4-20250514", "analyzer_model": "most-capable-model", "timestamp": "2026-01-15T10:30:00Z", "evals_run": [1, 2, 3], "runs_per_configuration": 3},
  "runs": [{"eval_id": 1, "eval_name": "Ocean", "configuration": "with_skill", "run_number": 1, "result": {"pass_rate": 0.85, "passed": 6, "failed": 1, "total": 7, "time_seconds": 42.5, "tokens": 3800, "tool_calls": 18, "errors": 0}, "expectations": [{"text": "...", "passed": true}], "notes": []}],
  "run_summary": {
    "with_skill": {"pass_rate": {"mean": 0.85, "stddev": 0.05}, "time_seconds": {"mean": 45.0, "stddev": 12.0}, "tokens": {"mean": 3800, "stddev": 400}},
    "without_skill": {"pass_rate": {"mean": 0.35, "stddev": 0.08}, "time_seconds": {"mean": 32.0, "stddev": 8.0}, "tokens": {"mean": 2100, "stddev": 300}},
    "delta": {"pass_rate": "+0.50", "time_seconds": "+13.0", "tokens": "+1700"}
  },
  "notes": ["Assertion 'Output is a PDF file' passes 100% in both configurations"]
}
```

---

## comparison.json

Output from blind comparator. Located at `<grading-dir>/comparison-N.json`.

```json
{
  "winner": "A",
  "reasoning": "Output A is complete with proper formatting. Output B is missing the date field.",
  "rubric": {"A": {"content": {"correctness": 5, "completeness": 5, "accuracy": 4}, "structure": {"organization": 4, "formatting": 5, "usability": 4}, "content_score": 4.7, "structure_score": 4.3, "overall_score": 9.0}},
  "output_quality": {"A": {"score": 9, "strengths": ["Complete solution"], "weaknesses": []}},
  "expectation_results": {"A": {"passed": 4, "total": 5, "pass_rate": 0.80}}
}
```

---

## analysis.json

Output from post-hoc analyzer. Located at `<grading-dir>/analysis.json`.

```json
{
  "comparison_summary": {"winner": "A", "winner_skill": "path/to/winner", "loser_skill": "path/to/loser"},
  "winner_strengths": ["Clear step-by-step instructions"],
  "loser_weaknesses": ["Vague instruction 'process the document appropriately'"],
  "instruction_following": {"winner": {"score": 9}, "loser": {"score": 6}},
  "improvement_suggestions": [{"priority": "high", "category": "instructions", "suggestion": "Replace vague instruction with explicit steps"}],
  "transcript_insights": {"winner_execution_pattern": "Read skill -> Followed 5-step process", "loser_execution_pattern": "Read skill -> Unclear on approach"}
}
```
