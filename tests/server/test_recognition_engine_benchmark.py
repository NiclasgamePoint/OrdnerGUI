"""Exercise the fixed, fully synthetic benchmark and the disabled model gate."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("recognition_benchmark", ROOT / "tools/recognition_benchmark.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_synthetic_benchmark_reports_errors_recall_and_group_metrics():
    result = benchmark.run_benchmark()
    assert result["dataset"]["kind"] == "synthetic-only"
    assert result["dataset"]["case_count"] >= 50
    assert result["current"]["overall"] == {"true_positive": 27, "false_positive": 0, "false_negative": 0, "precision": 1.0, "recall": 1.0}
    assert result["legacy_regex_baseline"]["overall"]["false_positive"] > 0
    assert result["legacy_regex_baseline"]["overall"]["false_negative"] > 0
    assert set(result["current"]["by"]) == {"field", "format", "group", "split", "customer"}
    assert {"phone", "email", "contact", "company", "address"} <= set(result["current"]["by"]["field"])
    assert result["optional_model_stage"]["enabled"] is False
    assert result["current"]["review_evidence_count"] > 0
    assert result["current"]["customer_macro"]["precision"] == 1.0


def test_customer_identities_and_groups_are_disjoint_between_splits():
    fixture = json.loads(benchmark.FIXTURE.read_text())
    split_names: dict[str, set[str]] = {}
    split_ids: dict[str, set[str]] = {}
    for case in fixture["cases"]:
        split_names.setdefault(case["split"], set()).add(case["customer_name"])
        split_ids.setdefault(case["split"], set()).add(case["customer_id"])
    assert not split_names["development"] & split_names["evaluation"]
    assert not split_ids["development"] & split_ids["evaluation"]


def test_benchmark_counts_false_positives_and_false_negatives_independently():
    from papagui_server.domain.customer_recognition import DocumentCandidate
    case = dict(id="invented-case", content="", expected=[dict(field_name="email", value="expected@example.org")], customer_name="Benchmark Kunde", customer_id="synthetic-1", group="fake", format="text", split="evaluation")
    def wrong(content, **kwargs):
        return [DocumentCandidate("email", "wrong@example.org", 0, "test", "", quality="strong")]
    result = benchmark.evaluate([case], wrong)
    assert result["overall"] == {"true_positive": 0, "false_positive": 1, "false_negative": 1, "precision": 0.0, "recall": 0.0}
    assert len(result["failures"]) == 1


def test_empty_benchmark_does_not_claim_precision_or_recall():
    result = benchmark.evaluate([])
    assert result["overall"]["precision"] is None and result["overall"]["recall"] is None


@pytest.mark.parametrize("change", [
    {"precision": 0.94}, {"strong_precision": 0.97}, {"recall": 0.8},
    {"runtime_seconds": 31}, {"peak_memory_mb": 2049},
])
def test_model_gate_requires_measurable_quality_and_resource_compliance(change):
    baseline = {"precision": 0.97, "recall": 0.8}
    trial = {"precision": 0.98, "strong_precision": 0.99, "recall": 0.85, "runtime_seconds": 20, "peak_memory_mb": 1024, **change}
    result = benchmark.model_stage_gate(baseline=baseline, trial=trial, independent_customer_holdout=True, measured_real_reference=True, runtime_limit_seconds=30, memory_limit_mb=2048)
    assert not result["eligible_for_human_review"] and not result["enabled"]


def test_synthetic_success_never_enables_model_stage():
    good = {"precision": 1.0, "strong_precision": 1.0, "recall": 0.95, "runtime_seconds": 1, "peak_memory_mb": 10}
    result = benchmark.model_stage_gate(baseline={"precision": 0.98, "recall": 0.8}, trial=good, independent_customer_holdout=False, measured_real_reference=False, runtime_limit_seconds=30, memory_limit_mb=2048)
    assert not result["eligible_for_human_review"] and not result["enabled"]
    reviewed = benchmark.model_stage_gate(baseline={"precision": 0.98, "recall": 0.8}, trial=good, independent_customer_holdout=True, measured_real_reference=True, runtime_limit_seconds=30, memory_limit_mb=2048)
    assert reviewed["eligible_for_human_review"] and not reviewed["enabled"]
