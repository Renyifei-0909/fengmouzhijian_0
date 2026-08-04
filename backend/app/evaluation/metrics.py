from __future__ import annotations

import math
from decimal import Decimal
from typing import Any


WILSON_Z_95 = 1.959963984540054


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def wilson_interval(successes: int, total: int, *, z: float = WILSON_Z_95) -> dict[str, float]:
    if total <= 0:
        raise ValueError("Wilson interval requires total > 0")
    estimate = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = estimate + z2 / (2.0 * total)
    delta = z * math.sqrt(estimate * (1.0 - estimate) / total + z2 / (4.0 * total * total))
    return {
        "level": 0.95,
        "lower": max(0.0, (centre - delta) / denominator),
        "upper": min(1.0, (centre + delta) / denominator),
    }


def score_single_label(
    truths: list[str],
    predictions: list[str],
    class_order: list[str],
    *,
    threshold: str,
    ci_policy: str,
) -> dict[str, Any]:
    if not truths or len(truths) != len(predictions):
        raise ValueError("truths and predictions must have the same non-zero length")
    if threshold != "0.85":
        raise ValueError("Evaluation v0 supports only the canonical threshold string '0.85'")
    index = {class_id: position for position, class_id in enumerate(class_order)}
    matrix = [[0 for _ in class_order] for _ in class_order]
    for truth, prediction in zip(truths, predictions, strict=True):
        matrix[index[truth]][index[prediction]] += 1

    total = len(truths)
    correct = sum(matrix[position][position] for position in range(len(class_order)))
    per_class: dict[str, dict[str, Any]] = {}
    metric_rows: list[tuple[int, float, float, float]] = []
    for position, class_id in enumerate(class_order):
        tp = matrix[position][position]
        fp = sum(matrix[row][position] for row in range(len(class_order)) if row != position)
        fn = sum(matrix[position][column] for column in range(len(class_order)) if column != position)
        tn = total - tp - fp - fn
        support = tp + fn
        predicted_positive = tp + fp
        precision = _safe_ratio(tp, predicted_positive)
        recall = _safe_ratio(tp, support)
        f1 = _f1(precision, recall)
        per_class[class_id] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "support": support,
            "predicted_positive": predicted_positive,
            "zero_precision_denominator": predicted_positive == 0,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
        metric_rows.append((support, precision, recall, f1))

    macro = {
        "precision": sum(row[1] for row in metric_rows) / len(metric_rows),
        "recall": sum(row[2] for row in metric_rows) / len(metric_rows),
        "f1": sum(row[3] for row in metric_rows) / len(metric_rows),
    }
    weighted = {
        "precision": sum(row[0] * row[1] for row in metric_rows) / total,
        "recall": sum(row[0] * row[2] for row in metric_rows) / total,
        "f1": sum(row[0] * row[3] for row in metric_rows) / total,
    }
    # For closed-set single-label classification, aggregate TP is the diagonal
    # and aggregate FP/FN are both N - diagonal, so all micro metrics equal accuracy.
    accuracy = correct / total
    micro = {"precision": accuracy, "recall": accuracy, "f1": accuracy}
    interval = wilson_interval(correct, total)
    # Official v0 point gate is exactly 85%; integer arithmetic prevents any
    # Decimal-context or binary-float rounding from changing the boundary.
    point_passed = 100 * correct >= 85 * total
    ci_passed = Decimal(str(interval["lower"])) >= Decimal("0.85")
    threshold_passed = point_passed and (ci_policy != "lower_bound" or ci_passed)
    return {
        "class_order": class_order,
        "confusion_matrix": matrix,
        "accuracy": {
            "correct": correct,
            "total": total,
            "value": accuracy,
            "wilson_95": interval,
        },
        "per_class": per_class,
        "balanced_accuracy": macro["recall"],
        "macro": macro,
        "micro": micro,
        "weighted": weighted,
        "threshold": {
            "metric": "accuracy",
            "operator": ">=",
            "value": "0.85",
            "ci_policy": ci_policy,
            "point_passed": point_passed,
            "ci_lower_passed": ci_passed,
            "passed": threshold_passed,
        },
    }


__all__ = ["score_single_label", "wilson_interval"]
