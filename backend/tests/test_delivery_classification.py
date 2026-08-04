from app.services.analyzers.contracts import delivery_classification


def test_delivery_classification_separates_demo_placeholder_and_unvalidated_model() -> None:
    assert delivery_classification({"analysis_mode": "demo_fixture", "evidence_grade": False}) == (
        "reviewed_demo",
        "demo",
    )
    assert delivery_classification({"analysis_mode": "stub", "evidence_grade": False}) == (
        "reviewed_placeholder",
        "workflow",
    )
    assert delivery_classification({"analysis_mode": "remote_http", "evidence_grade": False}) == (
        "reviewed_non_evaluated",
        "review",
    )
    assert delivery_classification({"analysis_mode": "evaluated_model", "evidence_grade": True}) == (
        "final",
        "validation",
    )
