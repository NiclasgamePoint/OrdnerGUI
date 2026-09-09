#!/usr/bin/env python3
"""Run a reproducible synthetic recognition benchmark without opening user data.

Format labels describe input structures: this is a semantic recognition benchmark,
not an OCR/parser evaluation or a measured guarantee for real customer documents.
The fixed synthetic fixture is the only dataset this command opens.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Iterable
import json
from pathlib import Path
import re
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/server/src", ROOT / "packages/contracts/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from papagui_server.domain.customer_recognition import (  # noqa: E402
    DocumentCandidate, RECOGNITION_VERSION, document_candidates, normalize_candidate_value,
)

FIXTURE = ROOT / "tests/fixtures/recognition_synthetic/cases.json"


def _canonical(field: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else str(value)
    return normalize_candidate_value(field, raw) or "invalid:" + raw.casefold()


def _counts(expected: set[tuple[str, str]], actual: set[tuple[str, str]]) -> dict[str, int]:
    return {"true_positive": len(expected & actual), "false_positive": len(actual - expected), "false_negative": len(expected - actual)}


def _metrics(counts: dict[str, int]) -> dict[str, Any]:
    tp, fp, fn = (counts.get(key, 0) for key in ("true_positive", "false_positive", "false_negative"))
    return {**counts, "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}


def _legacy(content: str, **kwargs: Any) -> Iterable[DocumentCandidate]:
    """Frozen former regex behavior, used only for the illustrative baseline."""
    if kwargs.get("blocks"):
        content = "\n".join(str(block.get("text", "")) for block in kwargs["blocks"])
    for field, pattern in (
        ("email", r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"),
        ("phone", r"(?<!\w)(?:\+\d{1,3}[\s/.-]*)?(?:\d[\s/().-]*){7,15}(?!\w)"),
    ):
        for value in re.findall(pattern, content):
            yield DocumentCandidate(field, value.strip(), 0.0, "legacy-baseline", "", quality="strong")


def evaluate(cases: list[dict[str, Any]], recognizer: Callable[..., Iterable[DocumentCandidate]] = document_candidates) -> dict[str, Any]:
    total: dict[str, int] = defaultdict(int)
    groups: dict[str, dict[str, dict[str, int]]] = {key: defaultdict(lambda: defaultdict(int)) for key in ("field", "format", "group", "split", "customer")}
    failures: list[dict[str, Any]] = []
    review_count = 0
    evidence_count = 0
    started = time.perf_counter()
    for case in cases:
        expected = {(item["field_name"], _canonical(item["field_name"], item["value"])) for item in case["expected"]}
        candidates = list(recognizer(case["content"], customer_name=case["customer_name"], own_identities=case.get("own_identities", ()), blocks=case.get("blocks", ())))
        evidence_count += len(candidates)
        review_count += sum(candidate.quality == "review" for candidate in candidates)
        actual = {(item.field_name, item.normalized_value or _canonical(item.field_name, item.value)) for item in candidates if item.quality == "strong"}
        counts = _counts(expected, actual)
        for key, value in counts.items():
            total[key] += value
            for dimension in ("format", "group", "split"):
                groups[dimension][case[dimension]][key] += value
            groups["customer"][case["customer_id"]][key] += value
        for field in {item[0] for item in expected | actual}:
            for key, value in _counts({item for item in expected if item[0] == field}, {item for item in actual if item[0] == field}).items():
                groups["field"][field][key] += value
        if counts["false_positive"] or counts["false_negative"]:
            failures.append({"case_id": case["id"], "unexpected": sorted(actual - expected), "missing": sorted(expected - actual)})
    metrics = {dimension: {name: _metrics(dict(counts)) for name, counts in sorted(values.items())} for dimension, values in groups.items()}
    macro = {}
    for metric in ("precision", "recall"):
        values = [item[metric] for item in metrics["customer"].values() if item[metric] is not None]
        macro[metric] = sum(values) / len(values) if values else None
    return {"overall": _metrics(dict(total)), "by": metrics, "customer_macro": macro,
            "review_evidence_count": review_count, "all_evidence_count": evidence_count,
            "elapsed_seconds": round(time.perf_counter() - started, 6), "failures": failures}


def model_stage_gate(*, baseline: dict[str, float], trial: dict[str, float], independent_customer_holdout: bool, measured_real_reference: bool, runtime_limit_seconds: float, memory_limit_mb: float) -> dict[str, Any]:
    """Fail closed until an independent measured trial demonstrates useful recall.

    Synthetic success alone cannot enable a model or an external processing path.
    This helper evaluates recorded metrics; it never calls a model or network.
    """
    checks = {
        "independent_customer_holdout": independent_customer_holdout,
        "measured_real_reference": measured_real_reference,
        "precision_target": trial.get("precision", 0) >= max(0.95, baseline.get("precision", 0)),
        "strong_precision_target": trial.get("strong_precision", 0) >= 0.98,
        "recall_improvement": trial.get("recall", 0) > baseline.get("recall", 0),
        "runtime_budget": 0 <= trial.get("runtime_seconds", float("inf")) <= runtime_limit_seconds,
        "memory_budget": 0 <= trial.get("peak_memory_mb", float("inf")) <= memory_limit_mb,
    }
    return {"eligible_for_human_review": all(checks.values()), "checks": checks, "enabled": False}


def run_benchmark() -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    customer_splits: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        customer_splits[case["customer_id"]].add(case["split"])
    if any(len(splits) != 1 for splits in customer_splits.values()):
        raise ValueError("Synthetic customer groups must not span development/evaluation splits")
    return {
        "schema_version": 1, "recognition_version": RECOGNITION_VERSION,
        "dataset": {"kind": "synthetic-only", "case_count": len(cases), "customer_group_count": len(customer_splits), "provenance": fixture["provenance"]},
        "measurement_scope": "Strong field/contact/address proposals, exact normalized value and party-bearing payload; review evidence is reported separately. No parser/OCR accuracy or production quality claim.",
        "legacy_regex_baseline": evaluate(cases, _legacy), "current": evaluate(cases),
        "optional_model_stage": {"enabled": False, "reason": "No independently annotated real reference or measured model benefit is available; no model or external service was invoked."},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON report destination")
    args = parser.parse_args()
    report = run_benchmark()
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
